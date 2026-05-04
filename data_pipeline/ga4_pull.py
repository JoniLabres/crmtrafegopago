"""
Google Analytics 4 — data puller using the GA4 Data API (analyticsdata v1beta).

Required env vars:
  GA4_PROPERTY_ID     — numeric property ID (without "properties/" prefix)
  GA4_CREDENTIALS_JSON — path to service account JSON, OR
  GA4_API_SECRET      — API secret for Measurement Protocol (server-side events)

Install: pip install google-analytics-data
"""
import os
import json
import logging
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

OUTPUT_PATH = Path(__file__).parent.parent / "outputs" / "ga4_report.json"

DIMENSIONS = [
    "sessionSource",
    "sessionMedium",
    "sessionCampaignName",
    "sessionManualAdContent",
    "sessionManualTerm",
    "date",
]

METRICS = [
    "sessions",
    "totalUsers",
    "newUsers",
    "bounceRate",
    "conversions",
    "totalRevenue",
    "averageSessionDuration",
]


def _get_client():
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.oauth2 import service_account

    creds_path = os.getenv("GA4_CREDENTIALS_JSON", "")
    if creds_path and Path(creds_path).exists():
        creds = service_account.Credentials.from_service_account_file(
            creds_path,
            scopes=["https://www.googleapis.com/auth/analytics.readonly"],
        )
        return BetaAnalyticsDataClient(credentials=creds)

    # Falls back to Application Default Credentials
    return BetaAnalyticsDataClient()


def fetch_report(days: int = 30, produto: str = None) -> dict:
    """
    Pulls session + conversion data from GA4, grouped by UTM parameters.
    Returns a dict ready to be saved or merged into campaigns_daily.
    """
    from google.analytics.data_v1beta.types import (
        RunReportRequest,
        DateRange,
        Dimension,
        Metric,
    )

    property_id = os.getenv("GA4_PROPERTY_ID", "")
    if not property_id:
        raise EnvironmentError("GA4_PROPERTY_ID não configurado")

    client = _get_client()
    date_to = date.today()
    date_from = date_to - timedelta(days=days)

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name=d) for d in DIMENSIONS],
        metrics=[Metric(name=m) for m in METRICS],
        date_ranges=[DateRange(
            start_date=str(date_from),
            end_date=str(date_to),
        )],
        limit=10000,
    )

    logger.info("Consultando GA4 (property %s, %d dias)", property_id, days)
    response = client.run_report(request)

    rows = []
    for row in response.rows:
        dims = {DIMENSIONS[i]: row.dimension_values[i].value for i in range(len(DIMENSIONS))}
        mets = {METRICS[i]: row.metric_values[i].value for i in range(len(METRICS))}

        if produto and dims.get("sessionCampaignName", "").split("_")[0] != produto:
            continue

        rows.append({
            "date": dims.get("date"),
            "channel": dims.get("sessionSource", ""),
            "utm_source": dims.get("sessionSource", ""),
            "utm_medium": dims.get("sessionMedium", ""),
            "utm_campaign": dims.get("sessionCampaignName", ""),
            "utm_content": dims.get("sessionManualAdContent", ""),
            "utm_term": dims.get("sessionManualTerm", ""),
            "sessions": int(mets.get("sessions", 0)),
            "users": int(mets.get("totalUsers", 0)),
            "new_users": int(mets.get("newUsers", 0)),
            "bounce_rate": float(mets.get("bounceRate", 0)),
            "conversions": int(mets.get("conversions", 0)),
            "revenue": float(mets.get("totalRevenue", 0)),
            "avg_session_duration_s": float(mets.get("averageSessionDuration", 0)),
        })

    logger.info("Linhas retornadas pelo GA4: %d", len(rows))

    output = {
        "property_id": property_id,
        "period": {"from": str(date_from), "to": str(date_to)},
        "rows": rows,
        "totals": {
            "sessions": sum(r["sessions"] for r in rows),
            "conversions": sum(r["conversions"] for r in rows),
            "revenue": sum(r["revenue"] for r in rows),
        },
    }
    return output


def save_report(report: dict) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    logger.info("Relatório GA4 salvo em %s", OUTPUT_PATH)


def send_event(event_name: str, params: dict, client_id: str = "server") -> bool:
    """
    Sends a server-side event to GA4 via Measurement Protocol.
    Requires GA4_MEASUREMENT_ID and GA4_API_SECRET env vars.
    """
    import httpx

    measurement_id = os.getenv("GA4_MEASUREMENT_ID", "")
    api_secret = os.getenv("GA4_API_SECRET", "")
    if not measurement_id or not api_secret:
        logger.warning("GA4_MEASUREMENT_ID ou GA4_API_SECRET não configurados")
        return False

    url = (
        "https://www.google-analytics.com/mp/collect"
        f"?measurement_id={measurement_id}&api_secret={api_secret}"
    )
    payload = {
        "client_id": client_id,
        "events": [{"name": event_name, "params": params}],
    }

    try:
        r = httpx.post(url, json=payload, timeout=10)
        r.raise_for_status()
        logger.info("Evento GA4 enviado: %s", event_name)
        return True
    except Exception as exc:
        logger.error("Erro ao enviar evento GA4: %s", exc)
        return False


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")

    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    try:
        report = fetch_report(days=days)
        save_report(report)
        print(f"\nSessões: {report['totals']['sessions']}")
        print(f"Conversões: {report['totals']['conversions']}")
        print(f"Receita: R$ {report['totals']['revenue']:.2f}")
    except EnvironmentError as e:
        print(f"Erro de configuração: {e}")
        sys.exit(1)
