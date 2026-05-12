"""
Pipeline principal: coleta dados de todos os canais por produto e carrega no PostgreSQL.

Fluxo:
  accounts.json → para cada produto → instancia puller com account_config específico
               → normaliza → join com HubSpot deals por utm_campaign
               → upsert em campaigns_daily
"""
import json
import os
import sys
import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
from base_puller import BasePuller
from meta_ads_pull import MetaAdsPuller
from google_ads_pull import GoogleAdsPuller
from linkedin_ads_pull import LinkedInAdsPuller
from tiktok_ads_pull import TikTokAdsPuller
from hubspot_pull import HubSpotPuller
from join_utm import join_campaign_data

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ACCOUNTS_PATH = ROOT / "config" / "accounts.json"

UPSERT_SQL = """
INSERT INTO campaigns_daily
    (date, channel, campaign_utm, campaign_name, produto,
     spend, impressions, clicks, leads, revenue, roas, cpl, cpc, ctr,
     mqls, sqls, deals_closed, updated_at)
VALUES %s
ON CONFLICT (date, channel, campaign_utm, produto) DO UPDATE SET
    campaign_name = EXCLUDED.campaign_name,
    spend         = EXCLUDED.spend,
    impressions   = EXCLUDED.impressions,
    clicks        = EXCLUDED.clicks,
    leads         = EXCLUDED.leads,
    revenue       = EXCLUDED.revenue,
    roas          = EXCLUDED.roas,
    cpl           = EXCLUDED.cpl,
    cpc           = EXCLUDED.cpc,
    ctr           = EXCLUDED.ctr,
    mqls          = EXCLUDED.mqls,
    sqls          = EXCLUDED.sqls,
    deals_closed  = EXCLUDED.deals_closed,
    updated_at    = NOW();
"""

CHANNEL_PULLERS = [
    (MetaAdsPuller,    "meta"),
    (GoogleAdsPuller,  "google"),
    (LinkedInAdsPuller,"linkedin"),
    (TikTokAdsPuller,  "tiktok"),
]


def _load_accounts() -> list:
    try:
        import config_store
        data = config_store.load("accounts", ACCOUNTS_PATH, {"produtos": []})
    except ImportError:
        with open(ACCOUNTS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    return data.get("produtos", []) if isinstance(data, dict) else []


def _get_db_url() -> str:
    for key in ("DATABASE_URL", "POSTGRES_URL_NON_POOLING", "POSTGRES_URL", "POSTGRES_PRISMA_URL"):
        val = os.getenv(key, "")
        if val:
            return val
    return ""


def _get_connection():
    database_url = _get_db_url()
    if not database_url:
        raise EnvironmentError("Banco não configurado — defina DATABASE_URL ou POSTGRES_URL")
    return psycopg2.connect(database_url)


def _pull_produto(nome: str, contas: dict, date_from: date, date_to: date) -> pd.DataFrame:
    """Puxa dados de todos os canais configurados para um produto específico."""
    frames = []
    for PullerClass, channel_key in CHANNEL_PULLERS:
        account_config = contas.get(channel_key, {})
        try:
            puller = PullerClass(produto=nome, account_config=account_config)
            df = puller.run(date_from, date_to)
            if not df.empty:
                frames.append(df)
        except EnvironmentError as e:
            logger.warning("[%s][%s] Credenciais ausentes, pulando: %s", channel_key, nome, e)
        except Exception as e:
            logger.error("[%s][%s] Erro ao puxar: %s", channel_key, nome, e)

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _pull_hubspot_deals(date_from: date, date_to: date) -> pd.DataFrame:
    try:
        puller = HubSpotPuller()
        return puller.fetch_deals(date_from, date_to)
    except Exception as e:
        logger.warning("HubSpot deals indisponível: %s", e)
        return pd.DataFrame()


def _pull_hubspot_funnel(date_from: date, date_to: date) -> pd.DataFrame:
    try:
        puller = HubSpotPuller()
        return puller.fetch_funnel_metrics(date_from, date_to)
    except Exception as e:
        logger.warning("HubSpot funnel indisponível: %s", e)
        return pd.DataFrame()


def run_pipeline(date_from: date = None, date_to: date = None) -> int:
    date_to = date_to or date.today()
    date_from = date_from or date_to - timedelta(days=1)

    logger.info("Pipeline iniciado: %s → %s", date_from, date_to)

    accounts = _load_accounts()
    if not accounts:
        logger.warning("Nenhum produto em accounts.json — configure em Conexões.")
        return 0

    # HubSpot: deals e funnel (MQL/SQL) compartilhados entre produtos
    deals_df  = _pull_hubspot_deals(date_from, date_to)
    funnel_df = _pull_hubspot_funnel(date_from, date_to)

    all_rows = []
    for produto_cfg in accounts:
        nome = produto_cfg.get("nome", "")
        contas = produto_cfg.get("contas", {})
        logger.info("Processando produto: %s", nome)

        ads_df = _pull_produto(nome, contas, date_from, date_to)
        if ads_df.empty:
            logger.info("[%s] Sem dados de Ads no período.", nome)
            continue

        consolidated = join_campaign_data(ads_df, deals_df, funnel_df)
        consolidated["produto"] = nome  # garante tag do produto

        for _, r in consolidated.iterrows():
            # Usa a data real da linha (dia da campanha), não date_from
            row_date = r.get("date", "") or str(date_from)
            try:
                row_date = str(row_date)[:10]  # garantir formato YYYY-MM-DD
            except Exception:
                row_date = str(date_from)
            all_rows.append((
                row_date,
                str(r.get("channel", "")),
                str(r.get("campaign_utm", "")),
                str(r.get("campaign_name", "")),
                nome,
                float(r.get("spend", 0)),
                int(r.get("impressions", 0)),
                int(r.get("clicks", 0)),
                int(r.get("leads", 0)),
                float(r.get("revenue", 0)),
                float(r.get("roas", 0)),
                float(r.get("cpl", 0)),
                float(r.get("cpc", 0)),
                float(r.get("ctr", 0)),
                int(r.get("mqls", 0)),
                int(r.get("sqls", 0)),
                int(r.get("deals_closed", 0)),
            ))

    if not all_rows:
        logger.warning("Pipeline sem linhas para upsert.")
        return 0

    # Deduplicar por (date, channel, campaign_utm, produto) somando métricas
    cols = ["date","channel","campaign_utm","campaign_name","produto",
            "spend","impressions","clicks","leads","revenue","roas","cpl","cpc","ctr",
            "mqls","sqls","deals_closed"]
    dedup_df = pd.DataFrame(all_rows, columns=cols)
    key = ["date","channel","campaign_utm","produto"]
    agg = {
        "campaign_name": "first",
        "spend": "sum", "impressions": "sum", "clicks": "sum",
        "leads": "sum", "revenue": "sum",
        "mqls": "sum", "sqls": "sum", "deals_closed": "sum",
    }
    dedup_df = dedup_df.groupby(key, as_index=False).agg(agg)
    dedup_df["roas"] = dedup_df.apply(
        lambda r: round(r["revenue"]/r["spend"],4) if r["spend"]>0 else 0.0, axis=1)
    dedup_df["cpl"]  = dedup_df.apply(
        lambda r: round(r["spend"]/r["leads"],2)   if r["leads"]>0  else 0.0, axis=1)
    dedup_df["cpc"]  = dedup_df.apply(
        lambda r: round(r["spend"]/r["clicks"],4)  if r["clicks"]>0 else 0.0, axis=1)
    dedup_df["ctr"]  = dedup_df.apply(
        lambda r: round(r["clicks"]/r["impressions"],4) if r["impressions"]>0 else 0.0, axis=1)
    # Remove updated_at do UPSERT_SQL pois não está em cols
    all_rows = [tuple(row) for row in dedup_df[cols].itertuples(index=False)]
    logger.info("Rows após deduplicação: %d", len(all_rows))

    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            execute_values(
                cur,
                UPSERT_SQL.replace(", updated_at", "").replace(", NOW()", ""),
                all_rows,
            )
            conn.commit()
        logger.info("Upsert concluído: %d linhas em %d produto(s)", len(all_rows), len(accounts))
        return len(all_rows)
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    total = run_pipeline(date_to=date.today(), date_from=date.today() - timedelta(days=days))
    print(f"\nPipeline concluído. {total} campanhas carregadas.")
