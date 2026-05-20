import os
import json
import logging
import psycopg2
import requests
from datetime import date, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

THRESHOLDS_PATH = Path(__file__).parent.parent / "config" / "alert_thresholds.json"

SEVERITY_EMOJI = {
    "critico": "🔴",
    "atencao": "🟡",
    "ok": "🟢",
}

_DEFAULT_THRESHOLDS = {"produtos": {}}


class AlertSystem:
    def __init__(self, db_conn=None, slack_webhook: str = None):
        self.slack_webhook = slack_webhook or os.getenv("SLACK_WEBHOOK_URL")
        self.db_conn = db_conn
        self.thresholds = self._load_thresholds()

    def _load_thresholds(self) -> dict:
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent / "data_pipeline"))
            import config_store
            data = config_store.load("thresholds", THRESHOLDS_PATH, _DEFAULT_THRESHOLDS)
        except (ImportError, Exception):
            try:
                with open(THRESHOLDS_PATH, encoding="utf-8") as f:
                    data = json.load(f)
            except FileNotFoundError:
                data = _DEFAULT_THRESHOLDS
        return data.get("produtos", {}) if isinstance(data, dict) else {}

    def _get_conn(self):
        if self.db_conn:
            return self.db_conn
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise EnvironmentError("DATABASE_URL não configurada")
        return psycopg2.connect(database_url)

    # ── Checks ────────────────────────────────────────────────────────────────

    def check_cpl_alto(self, produto: str, data: list = None) -> list:
        """CPL > 130% da meta por 3+ dias consecutivos."""
        alerts = []
        meta = self.thresholds.get(produto, {}).get("cpl_meta", 0.0)
        if not meta:
            return alerts

        rows = data if data is not None else self._query_cpl_alto(produto, meta)
        for row in rows:
            cpl = row.get("cpl_medio", 0)
            msg = (
                f"CPL alto em *{row['campaign_utm']}* ({row['channel']}): "
                f"R${cpl:.2f} vs meta R${meta:.2f} "
                f"por {row.get('dias_acima', 3)}+ dias"
            )
            alerts.append({
                "alert_type": "cpl_alto",
                "channel": row["channel"],
                "campaign_utm": row["campaign_utm"],
                "produto": produto,
                "severity": "atencao",
                "message": msg,
                "details": row,
            })
        return alerts

    def check_roas_baixo(self, produto: str, data: list = None) -> list:
        """ROAS < meta por 7+ dias."""
        alerts = []
        meta = self.thresholds.get(produto, {}).get("roas_meta", 0.0)
        if not meta:
            return alerts

        rows = data if data is not None else self._query_roas_baixo(produto, meta)
        for row in rows:
            roas = row.get("roas_medio", 0)
            msg = (
                f"ROAS baixo em *{row['campaign_utm']}* ({row['channel']}): "
                f"{roas:.2f}x vs meta {meta:.1f}x "
                f"por {row.get('dias_abaixo', 7)}+ dias"
            )
            alerts.append({
                "alert_type": "roas_baixo",
                "channel": row["channel"],
                "campaign_utm": row["campaign_utm"],
                "produto": produto,
                "severity": "atencao",
                "message": msg,
                "details": row,
            })
        return alerts

    def check_queda_leads(self, produto: str, data: list = None) -> list:
        """Queda > 50% de leads em 24h vs média dos 7 dias anteriores."""
        alerts = []
        rows = data if data is not None else self._query_queda_leads(produto)
        for row in rows:
            queda = row.get("queda_pct", 0)
            severity = "critico" if queda >= 70 else "atencao"
            msg = (
                f"Queda de leads em *{row['campaign_utm']}* ({row['channel']}): "
                f"{queda:.0f}% abaixo da média "
                f"({row.get('leads_hoje', 0)} hoje vs {row.get('leads_media_7d', 0):.1f} média)"
            )
            alerts.append({
                "alert_type": "queda_leads",
                "channel": row["channel"],
                "campaign_utm": row["campaign_utm"],
                "produto": produto,
                "severity": severity,
                "message": msg,
                "details": row,
            })
        return alerts

    def check_budget_pace(self, produto: str, data: list = None) -> list:
        """Pace > 110% do budget proporcional do mês (nível de produto, não por campanha)."""
        alerts = []
        budget = self.thresholds.get(produto, {}).get("budget_mensal", 0.0)
        if not budget:
            return alerts

        rows = data if data is not None else self._query_budget_pace(produto, budget)
        for row in rows:
            pace = row.get("pace_pct", 0)
            msg = (
                f"Budget acelerado em *{produto}*: "
                f"pace {pace:.0f}% do mês "
                f"(gasto R${row.get('gasto_acumulado', 0):.2f} vs "
                f"proporcional R${row.get('budget_proporcional', 0):.2f} / "
                f"budget R${budget:.2f}/mês)"
            )
            alerts.append({
                "alert_type": "budget_pace",
                "channel": "all",
                "campaign_utm": "all",
                "produto": produto,
                "severity": "atencao",
                "message": msg,
                "details": row,
            })
        return alerts

    def check_mer_baixo(self, produto: str, data: list = None) -> list:
        """MER blended (receita total / gasto total) abaixo da meta por 7+ dias."""
        alerts = []
        meta = self.thresholds.get(produto, {}).get("mer_meta", 1.5)

        rows = data if data is not None else self._query_mer_baixo(produto, meta)
        for row in rows:
            mer = row.get("mer", 0)
            severity = "critico" if mer < meta * 0.7 else "atencao"
            msg = (
                f"MER baixo para *{produto}*: "
                f"{mer:.2f}x vs meta {meta:.1f}x "
                f"(receita R${row.get('receita_total', 0):.2f} / "
                f"gasto R${row.get('gasto_total', 0):.2f}) "
                f"nos últimos 7 dias"
            )
            alerts.append({
                "alert_type": "mer_baixo",
                "channel": "all",
                "campaign_utm": "all",
                "produto": produto,
                "severity": severity,
                "message": msg,
                "details": row,
            })
        return alerts

    # ── Queries ───────────────────────────────────────────────────────────────

    def _query_cpl_alto(self, produto: str, meta: float) -> list:
        sql = """
        WITH daily AS (
            SELECT date, campaign_utm, channel, cpl,
                CASE WHEN cpl > %(meta)s * 1.30 THEN 1 ELSE 0 END AS above
            FROM campaigns_daily
            WHERE produto = %(produto)s AND date >= CURRENT_DATE - 14
        ),
        grouped AS (
            SELECT campaign_utm, channel, date, cpl,
                SUM(above) OVER (PARTITION BY campaign_utm ORDER BY date ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS consec
            FROM daily
        )
        SELECT campaign_utm, channel, %(produto)s AS produto, ROUND(AVG(cpl)::numeric,2) AS cpl_medio, MAX(consec) AS dias_acima
        FROM grouped WHERE consec >= 3
        GROUP BY campaign_utm, channel
        """
        return self._run_query(sql, {"produto": produto, "meta": meta})

    def _query_roas_baixo(self, produto: str, meta: float) -> list:
        sql = """
        WITH daily AS (
            SELECT date, campaign_utm, channel, roas,
                CASE WHEN roas < %(meta)s AND spend > 0 THEN 1 ELSE 0 END AS below
            FROM campaigns_daily
            WHERE produto = %(produto)s AND date >= CURRENT_DATE - 14
        ),
        grouped AS (
            SELECT campaign_utm, channel, date, roas,
                SUM(below) OVER (PARTITION BY campaign_utm ORDER BY date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS consec
            FROM daily
        )
        SELECT campaign_utm, channel, %(produto)s AS produto, ROUND(AVG(roas)::numeric,4) AS roas_medio, MAX(consec) AS dias_abaixo
        FROM grouped WHERE consec >= 7
        GROUP BY campaign_utm, channel
        """
        return self._run_query(sql, {"produto": produto, "meta": meta})

    def _query_queda_leads(self, produto: str) -> list:
        sql = """
        WITH hoje AS (
            SELECT campaign_utm, channel, %(produto)s AS produto, SUM(leads) AS leads_hoje
            FROM campaigns_daily
            WHERE date = CURRENT_DATE - 1 AND produto = %(produto)s
            GROUP BY campaign_utm, channel
        ),
        media AS (
            SELECT campaign_utm, ROUND(AVG(leads)::numeric,2) AS leads_media_7d
            FROM campaigns_daily
            WHERE date BETWEEN CURRENT_DATE - 8 AND CURRENT_DATE - 2 AND produto = %(produto)s
            GROUP BY campaign_utm
        )
        SELECT h.campaign_utm, h.channel, h.produto, h.leads_hoje, m.leads_media_7d,
            ROUND((1 - h.leads_hoje::numeric / NULLIF(m.leads_media_7d,0)) * 100, 1) AS queda_pct
        FROM hoje h JOIN media m ON h.campaign_utm = m.campaign_utm
        WHERE m.leads_media_7d > 0 AND h.leads_hoje < m.leads_media_7d * 0.5
        """
        return self._run_query(sql, {"produto": produto})

    def _query_budget_pace(self, produto: str, budget: float) -> list:
        # Compares TOTAL product spend (not per campaign) against the monthly budget
        sql = """
        WITH mensal AS (
            SELECT
                %(produto)s AS produto,
                SUM(spend) AS gasto,
                DATE_PART('day', CURRENT_DATE)::numeric AS dias_p,
                DATE_PART('day', DATE_TRUNC('month', CURRENT_DATE)
                    + INTERVAL '1 month' - INTERVAL '1 day')::numeric AS dias_m
            FROM campaigns_daily
            WHERE DATE_TRUNC('month', date) = DATE_TRUNC('month', CURRENT_DATE)
              AND produto = %(produto)s
        )
        SELECT produto,
            ROUND(gasto::numeric, 2) AS gasto_acumulado,
            ROUND((%(budget)s::numeric * dias_p / NULLIF(dias_m,0))::numeric, 2) AS budget_proporcional,
            ROUND((gasto / NULLIF(%(budget)s::numeric * dias_p / NULLIF(dias_m,0), 0) * 100)::numeric, 1) AS pace_pct
        FROM mensal
        WHERE gasto > %(budget)s::numeric * dias_p / NULLIF(dias_m,0) * 1.10
        """
        return self._run_query(sql, {"produto": produto, "budget": budget})

    def _query_mer_baixo(self, produto: str, meta: float) -> list:
        sql = """
        SELECT
            %(produto)s AS produto,
            ROUND(SUM(revenue)::numeric, 2) AS receita_total,
            ROUND(SUM(spend)::numeric, 2) AS gasto_total,
            ROUND(
                CASE WHEN SUM(spend) > 0 THEN SUM(revenue) / SUM(spend) ELSE 0 END
            ::numeric, 4) AS mer
        FROM campaigns_daily
        WHERE produto = %(produto)s
          AND date >= CURRENT_DATE - 7
          AND spend > 0
        HAVING SUM(spend) > 0
           AND (SUM(revenue) / NULLIF(SUM(spend), 0)) < %(meta)s
        """
        return self._run_query(sql, {"produto": produto, "meta": meta})

    def _run_query(self, sql: str, params: dict) -> list:
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            if not self.db_conn:
                conn.close()

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def check_all(self) -> list:
        all_alerts = []
        for produto, t in self.thresholds.items():
            # Skip products with no thresholds configured (all zeros)
            configured = any(v for v in t.values() if isinstance(v, (int, float)) and v > 0)
            if not configured:
                logger.info("check_all: produto '%s' sem metas definidas, pulando", produto)
                continue
            for check_fn in [
                self.check_cpl_alto,
                self.check_roas_baixo,
                self.check_queda_leads,
                self.check_budget_pace,
                self.check_mer_baixo,
            ]:
                try:
                    alerts = check_fn(produto)
                    for alert in alerts:
                        self.send_slack("#alertas-midia", alert["message"], alert["severity"])
                        self.log_alert(alert["alert_type"], alert)
                    all_alerts.extend(alerts)
                except Exception as e:
                    logger.error("Erro em %s/%s: %s", check_fn.__name__, produto, e)
        logger.info("check_all: %d alertas disparados", len(all_alerts))
        return all_alerts

    def send_slack(self, channel: str, message: str, severity: str = "atencao") -> bool:
        if not self.slack_webhook:
            logger.warning("SLACK_WEBHOOK_URL não configurado")
            return False
        emoji = SEVERITY_EMOJI.get(severity, "🟡")
        payload = {"text": f"{emoji} *[{severity.upper()}]* {message}", "channel": channel}
        try:
            resp = requests.post(self.slack_webhook, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info("Slack enviado: [%s] %s", severity, message[:80])
            return True
        except Exception as e:
            logger.error("Falha ao enviar Slack: %s", e)
            return False

    def log_alert(self, alert_type: str, details: dict) -> None:
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                import json as _json
                cur.execute(
                    """INSERT INTO alerts_log
                       (alert_type, channel, campaign_utm, produto, severity, message, details)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        alert_type,
                        details.get("channel", ""),
                        details.get("campaign_utm", ""),
                        details.get("produto", ""),
                        details.get("severity", "atencao"),
                        details.get("message", ""),
                        _json.dumps(details.get("details", {})),
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error("Erro ao gravar alerta no banco: %s", e)
        finally:
            if not self.db_conn:
                conn.close()
