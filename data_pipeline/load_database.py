import os
import sys
import logging
from datetime import date, timedelta
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
from base_puller import BasePuller
from meta_ads_pull import MetaAdsPuller
from google_ads_pull import GoogleAdsPuller
from linkedin_ads_pull import LinkedInAdsPuller
from hubspot_pull import HubSpotPuller
from join_utm import join_campaign_data

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

UPSERT_SQL = """
INSERT INTO campaigns_daily
    (date, channel, campaign_utm, campaign_name, produto,
     spend, impressions, clicks, leads, revenue, roas, cpl, cpc, ctr, updated_at)
VALUES %s
ON CONFLICT (date, campaign_utm) DO UPDATE SET
    channel       = EXCLUDED.channel,
    campaign_name = EXCLUDED.campaign_name,
    produto       = EXCLUDED.produto,
    spend         = EXCLUDED.spend,
    impressions   = EXCLUDED.impressions,
    clicks        = EXCLUDED.clicks,
    leads         = EXCLUDED.leads,
    revenue       = EXCLUDED.revenue,
    roas          = EXCLUDED.roas,
    cpl           = EXCLUDED.cpl,
    cpc           = EXCLUDED.cpc,
    ctr           = EXCLUDED.ctr,
    updated_at    = NOW();
"""


def _get_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise EnvironmentError("DATABASE_URL não configurada")
    return psycopg2.connect(database_url)


def _pull_all_channels(date_from: date, date_to: date) -> pd.DataFrame:
    pullers = []
    for PullerClass in [MetaAdsPuller, GoogleAdsPuller, LinkedInAdsPuller]:
        try:
            pullers.append(PullerClass())
        except EnvironmentError as e:
            logger.warning("Pulando %s: %s", PullerClass.channel, e)

    frames = []
    for puller in pullers:
        try:
            df = puller.run(date_from, date_to)
            if not df.empty:
                frames.append(df)
        except Exception as e:
            logger.error("[%s] Erro ao puxar dados: %s", puller.channel, e)

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _pull_hubspot_deals(date_from: date, date_to: date) -> pd.DataFrame:
    try:
        puller = HubSpotPuller()
        return puller.fetch_deals(date_from, date_to)
    except Exception as e:
        logger.warning("HubSpot indisponível: %s", e)
        return pd.DataFrame()


def _to_db_rows(consolidated: pd.DataFrame, run_date: date) -> list:
    rows = []
    for _, r in consolidated.iterrows():
        rows.append((
            run_date,
            r.get("channel", ""),
            r.get("campaign_utm", ""),
            r.get("campaign_name", ""),
            r.get("produto", ""),
            float(r.get("spend", 0)),
            int(r.get("impressions", 0)),
            int(r.get("clicks", 0)),
            int(r.get("leads", 0)),
            float(r.get("revenue", 0)),
            float(r.get("roas", 0)),
            float(r.get("cpl", 0)),
            float(r.get("cpc", 0)),
            float(r.get("ctr", 0)),
            "NOW()",
        ))
    return rows


def run_pipeline(date_from: date = None, date_to: date = None) -> int:
    date_to = date_to or date.today()
    date_from = date_from or date_to - timedelta(days=1)

    logger.info("Iniciando pipeline: %s → %s", date_from, date_to)

    ads_df = _pull_all_channels(date_from, date_to)
    deals_df = _pull_hubspot_deals(date_from, date_to)

    if ads_df.empty:
        logger.warning("Nenhum dado de Ads disponível. Pipeline encerrado.")
        return 0

    consolidated = join_campaign_data(ads_df, deals_df)

    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            rows = []
            for _, r in consolidated.iterrows():
                rows.append((
                    date_from,
                    r.get("channel", ""),
                    r.get("campaign_utm", ""),
                    r.get("campaign_name", ""),
                    r.get("produto", ""),
                    float(r.get("spend", 0)),
                    int(r.get("impressions", 0)),
                    int(r.get("clicks", 0)),
                    int(r.get("leads", 0)),
                    float(r.get("revenue", 0)),
                    float(r.get("roas", 0)),
                    float(r.get("cpl", 0)),
                    float(r.get("cpc", 0)),
                    float(r.get("ctr", 0)),
                ))
            execute_values(cur, UPSERT_SQL.replace(", updated_at", "").replace(", NOW()", ""), rows)
            conn.commit()
            logger.info("Upsert concluído: %d linhas", len(rows))
            return len(rows)
    finally:
        conn.close()


if __name__ == "__main__":
    total = run_pipeline()
    print(f"\nPipeline concluído. {total} campanhas carregadas no banco.")
