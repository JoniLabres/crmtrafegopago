import os
import logging
import psycopg2
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def _get_conn():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise EnvironmentError("DATABASE_URL não configurada")
    return psycopg2.connect(url)


def _run_query(sql: str, params: dict) -> list:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def get_current_metrics(product: str = None, days: int = 30) -> str:
    date_from = date.today() - timedelta(days=days)

    filters = "WHERE date >= %(date_from)s"
    params: dict = {"date_from": date_from}
    if product:
        filters += " AND produto = %(produto)s"
        params["produto"] = product

    try:
        by_channel = _run_query(f"""
            SELECT channel,
                   ROUND(SUM(spend)::numeric, 2)       AS spend,
                   SUM(leads)                           AS leads,
                   ROUND(AVG(roas)::numeric, 2)         AS roas,
                   ROUND(AVG(cpl)::numeric, 2)          AS cpl,
                   ROUND(AVG(cpc)::numeric, 4)          AS cpc,
                   ROUND(AVG(ctr)::numeric, 4)          AS ctr
            FROM campaigns_daily {filters}
            GROUP BY channel
            ORDER BY spend DESC
        """, params)

        top_campaigns = _run_query(f"""
            SELECT campaign_utm, channel,
                   ROUND(SUM(spend)::numeric, 2)  AS spend,
                   SUM(leads)                     AS leads,
                   ROUND(AVG(roas)::numeric, 2)   AS roas,
                   ROUND(AVG(cpl)::numeric, 2)    AS cpl
            FROM campaigns_daily {filters}
            GROUP BY campaign_utm, channel
            ORDER BY roas DESC
            LIMIT 5
        """, params)

        active_alerts = _run_query("""
            SELECT alert_type, severity, message, sent_at
            FROM alerts_log
            WHERE sent_at >= NOW() - INTERVAL '48 hours'
            ORDER BY sent_at DESC
            LIMIT 10
        """, {})

    except Exception as e:
        logger.warning("Banco indisponível: %s", e)
        return _fallback_metrics(product, days)

    produto_label = f"Produto: {product}" if product else "Todos os produtos"
    lines = [
        f"=== MÉTRICAS ATUAIS ({produto_label} — últimos {days} dias) ===",
        "",
        "DESEMPENHO POR CANAL:",
    ]
    if by_channel:
        for r in by_channel:
            lines.append(
                f"  {r['channel'].upper()}: Spend R${r['spend']:,.2f} | "
                f"Leads {r['leads']} | ROAS {r['roas']}x | "
                f"CPL R${r['cpl']} | CPC R${r['cpc']} | CTR {float(r['ctr']):.1%}"
            )
    else:
        lines.append("  Sem dados no período")

    lines += ["", "TOP 5 CAMPANHAS (por ROAS):"]
    if top_campaigns:
        for r in top_campaigns:
            lines.append(
                f"  [{r['channel']}] {r['campaign_utm']}: "
                f"ROAS {r['roas']}x | CPL R${r['cpl']} | Leads {r['leads']}"
            )
    else:
        lines.append("  Sem dados no período")

    lines += ["", "ALERTAS ATIVOS (últimas 48h):"]
    if active_alerts:
        for a in active_alerts:
            lines.append(f"  [{a['severity'].upper()}] {a['message']}")
    else:
        lines.append("  Nenhum alerta ativo")

    return "\n".join(lines)


def _fallback_metrics(product: str, days: int) -> str:
    produto_label = product or "todos os produtos"
    return (
        f"=== MÉTRICAS ({produto_label} — últimos {days} dias) ===\n"
        "Banco de dados indisponível. Trabalhando sem dados em tempo real.\n"
        "Configure DATABASE_URL no .env para ativar métricas ao vivo."
    )
