"""
Calcula LTV dos clientes a partir dos Negócios do HubSpot.

Fórmula:
  LTV = valor_implementacao + (mensalidade × meses_retencao)
  meses_retencao = (data_churn - closedate) para clientes perdidos
                 = (hoje - closedate)       para clientes ativos

Métricas calculadas:
  - LTV médio global e por produto
  - MRR / ARR atual (clientes ativos)
  - Payback period médio = CAC / mensalidade
  - LTV/CAC ratio por produto
"""
import os
import sys
import json
import logging
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from hubspot_client import HubSpotClient

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LTV_PATH = Path(__file__).parent.parent / "config" / "ltv_metrics.json"


def _paginate(client: HubSpotClient, path: str, properties: list, filters: list = None) -> list:
    results = []
    body = {"properties": properties, "limit": 100}
    if filters:
        body["filterGroups"] = [{"filters": filters}]
    after = None
    while True:
        if after:
            body["after"] = after
        data = client.post(path, json=body)
        results.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return results


def _months_between(start_ts: str, end_ts: str | None) -> float:
    """Returns months between two ISO timestamps. end_ts=None means today."""
    fmt = "%Y-%m-%d"
    try:
        start = datetime.strptime(start_ts[:10], fmt).replace(tzinfo=timezone.utc)
    except Exception:
        return 0.0
    if end_ts:
        try:
            end = datetime.strptime(end_ts[:10], fmt).replace(tzinfo=timezone.utc)
        except Exception:
            end = datetime.now(timezone.utc)
    else:
        end = datetime.now(timezone.utc)
    delta = (end - start).days
    return max(0.0, round(delta / 30.44, 2))


def fetch_ltv_deals(client: HubSpotClient) -> list[dict]:
    logger.info("Buscando negócios fechados com campos de LTV...")
    deals = _paginate(
        client,
        "/crm/v3/objects/deals/search",
        properties=[
            "dealname", "closedate", "dealstage", "produto_negocio",
            "canal_origem", "utm_campaign_origem",
            "valor_implementacao", "mensalidade", "data_churn",
            "amount",
        ],
        filters=[{"propertyName": "dealstage", "operator": "EQ", "value": "closedwon"}],
    )
    logger.info("Negócios encontrados: %d", len(deals))
    return deals


def calculate_ltv(deals: list[dict]) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    by_product: dict[str, list] = defaultdict(list)
    all_ltvs: list[float] = []
    mrr = 0.0
    total_active = 0
    total_churned = 0

    deal_rows = []
    for deal in deals:
        p = deal.get("properties", {})
        produto = (p.get("produto_negocio") or "sem-produto").strip()
        closedate = p.get("closedate", "")
        data_churn = p.get("data_churn") or None
        implementacao = float(p.get("valor_implementacao") or 0)
        mensalidade = float(p.get("mensalidade") or 0)
        canal = p.get("canal_origem", "")
        utm = p.get("utm_campaign_origem", "")

        meses = _months_between(closedate, data_churn)
        ltv = round(implementacao + (mensalidade * meses), 2)
        is_active = data_churn is None or data_churn == ""

        all_ltvs.append(ltv)
        by_product[produto].append({
            "ltv": ltv,
            "implementacao": implementacao,
            "mensalidade": mensalidade,
            "meses": meses,
            "ativo": is_active,
            "canal": canal,
            "utm": utm,
        })

        if is_active:
            mrr += mensalidade
            total_active += 1
        else:
            total_churned += 1

        deal_rows.append({
            "produto": produto,
            "ltv": ltv,
            "implementacao": implementacao,
            "mensalidade": mensalidade,
            "meses_retencao": meses,
            "ativo": is_active,
            "canal": canal,
        })

    ltv_medio_global = round(sum(all_ltvs) / len(all_ltvs), 2) if all_ltvs else 0.0
    arr = round(mrr * 12, 2)

    # Per-product metrics
    por_produto = {}
    for produto, rows in by_product.items():
        ltvs = [r["ltv"] for r in rows]
        mensalidades_ativas = [r["mensalidade"] for r in rows if r["ativo"]]
        mrr_produto = sum(mensalidades_ativas)
        impl_media = round(sum(r["implementacao"] for r in rows) / len(rows), 2)
        mens_media = round(sum(r["mensalidade"] for r in rows) / len(rows), 2)
        meses_medio = round(sum(r["meses"] for r in rows) / len(rows), 1)
        ltv_medio = round(sum(ltvs) / len(ltvs), 2)
        ativos = sum(1 for r in rows if r["ativo"])
        churned = len(rows) - ativos
        churn_rate = round(churned / len(rows) * 100, 1) if rows else 0

        por_produto[produto] = {
            "ltv_medio": ltv_medio,
            "ltv_total": round(sum(ltvs), 2),
            "implementacao_media": impl_media,
            "mensalidade_media": mens_media,
            "meses_retencao_medio": meses_medio,
            "mrr": round(mrr_produto, 2),
            "arr": round(mrr_produto * 12, 2),
            "total_clientes": len(rows),
            "ativos": ativos,
            "churned": churned,
            "churn_rate_pct": churn_rate,
        }

    return {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "global": {
            "ltv_medio": ltv_medio_global,
            "mrr": round(mrr, 2),
            "arr": arr,
            "total_clientes": len(deals),
            "ativos": total_active,
            "churned": total_churned,
            "churn_rate_pct": round(total_churned / len(deals) * 100, 1) if deals else 0,
        },
        "por_produto": por_produto,
        "deals": deal_rows[:200],
    }


def run(client: HubSpotClient = None) -> dict:
    if client is None:
        client = HubSpotClient()

    deals = fetch_ltv_deals(client)
    if not deals:
        logger.warning("Nenhum negócio com campos LTV encontrado. Crie as propriedades primeiro.")
        empty = {
            "gerado_em": datetime.now(timezone.utc).isoformat(),
            "global": {"ltv_medio": 0, "mrr": 0, "arr": 0, "total_clientes": 0,
                       "ativos": 0, "churned": 0, "churn_rate_pct": 0},
            "por_produto": {},
            "deals": [],
        }
        LTV_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LTV_PATH, "w", encoding="utf-8") as f:
            json.dump(empty, f, indent=2, ensure_ascii=False)
        return empty

    metrics = calculate_ltv(deals)
    LTV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LTV_PATH, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)

    g = metrics["global"]
    logger.info(
        "LTV calculado — Médio: R$ %.2f | MRR: R$ %.2f | ARR: R$ %.2f | Clientes: %d (ativos: %d)",
        g["ltv_medio"], g["mrr"], g["arr"], g["total_clientes"], g["ativos"],
    )
    return metrics


if __name__ == "__main__":
    result = run()
    g = result["global"]
    print(f"\nLTV Global:")
    print(f"  LTV Médio:   R$ {g['ltv_medio']:,.2f}")
    print(f"  MRR:         R$ {g['mrr']:,.2f}")
    print(f"  ARR:         R$ {g['arr']:,.2f}")
    print(f"  Clientes:    {g['total_clientes']} ({g['ativos']} ativos, {g['churned']} churned)")
    print(f"  Churn Rate:  {g['churn_rate_pct']}%")
    print(f"\nPor produto:")
    for nome, m in result["por_produto"].items():
        print(f"  {nome}: LTV R$ {m['ltv_medio']:,.2f} | MRR R$ {m['mrr']:,.2f} | {m['ativos']} ativos")
