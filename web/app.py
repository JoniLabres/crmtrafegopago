import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

# Ensure web/ is on sys.path before importing local modules
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv, set_key

import config_store
from config_store import get_db_url
import auth as _auth
import mailer as _mailer

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "tracking"))
sys.path.insert(0, str(ROOT / "data_pipeline"))
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "ai_agent"))
sys.path.insert(0, str(ROOT / "hubspot"))

load_dotenv(ROOT / ".env")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="IXCTraffic", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# ── Injeta current_user em todos os TemplateResponse automaticamente ─────────
_orig_template_response = templates.TemplateResponse

def _template_response_with_user(name, context, *args, **kwargs):
    req = context.get("request")
    if req and hasattr(req.state, "user"):
        context.setdefault("current_user", req.state.user)
    else:
        context.setdefault("current_user", None)
    return _orig_template_response(name, context, *args, **kwargs)

templates.TemplateResponse = _template_response_with_user

# ── Middleware de autenticação ────────────────────────────────────────────────
# Paths completamente públicos (sem autenticação)
_PUBLIC_PREFIXES = (
    "/login", "/logout", "/setup",
    "/static/",
    "/api/slack/events",   # webhook externo do Slack
    "/api/pipeline/cron",  # job agendado externo
)

def _auth_enabled() -> bool:
    """Auth só ativa quando DATABASE_URL está configurada."""
    return bool(os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL"))


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # Sem banco configurado → ferramenta funciona sem login
    if not _auth_enabled():
        request.state.user = {"id": 0, "email": "admin@local", "name": "Admin", "role": "admin"}
        return await call_next(request)

    # Permite paths públicos
    if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)

    # Verifica se setup inicial é necessário
    try:
        if _auth.needs_setup():
            if path.startswith("/api/"):
                return JSONResponse({"error": "Setup pendente"}, status_code=503)
            return RedirectResponse("/setup", status_code=302)
    except Exception:
        pass  # DB offline — deixa passar

    # Verifica sessão
    token = request.cookies.get("ixc_session")
    user = _auth.get_user_from_token(token)

    if user is None:
        if path.startswith("/api/"):
            return JSONResponse({"error": "Não autenticado"}, status_code=401)
        next_url = path if not path.startswith("/admin") else "/"
        return RedirectResponse(f"/login?next={next_url}", status_code=302)

    # Admin-only: /admin/*
    if path.startswith("/admin/") and user.get("role") != "admin":
        if request.method == "GET":
            return RedirectResponse("/", status_code=302)
        return JSONResponse({"error": "Acesso restrito a administradores"}, status_code=403)

    request.state.user = user
    return await call_next(request)


@app.exception_handler(Exception)
async def _unhandled_exception(request: Request, exc: Exception):
    import traceback
    tb = traceback.format_exc()
    logger.error("Unhandled exception %s %s: %s\n%s", request.method, request.url.path, exc, tb)
    return JSONResponse(
        {"error": str(exc), "type": type(exc).__name__, "path": request.url.path},
        status_code=500,
    )

ENV_PATH = ROOT / ".env"
THRESHOLDS_PATH = ROOT / "config" / "alert_thresholds.json"
TAXONOMY_PATH   = ROOT / "config" / "utm_taxonomy.json"
PRODUCTS_PATH   = ROOT / "config" / "products.json"
ACCOUNTS_PATH   = ROOT / "config" / "accounts.json"
LTV_PATH        = ROOT / "config" / "ltv_metrics.json"

_agent_instance = None
_active_product = None


_ENV_KEYS = [
    "META_CLIENT_ID","META_CLIENT_SECRET","META_ACCESS_TOKEN","META_AD_ACCOUNT_ID","META_PIXEL_ID",
    "GOOGLE_ADS_CLIENT_ID","GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_DEVELOPER_TOKEN","GOOGLE_ADS_CUSTOMER_ID","GOOGLE_ADS_REFRESH_TOKEN",
    "LINKEDIN_CLIENT_ID","LINKEDIN_CLIENT_SECRET",
    "LINKEDIN_ACCESS_TOKEN","LINKEDIN_AD_ACCOUNT_ID","LINKEDIN_PARTNER_ID",
    "TIKTOK_CLIENT_ID","TIKTOK_CLIENT_SECRET",
    "TIKTOK_ACCESS_TOKEN","TIKTOK_AD_ACCOUNT_ID","TIKTOK_PIXEL_ID",
    "GTM_CLIENT_ID","GTM_CLIENT_SECRET","GTM_OAUTH_REFRESH_TOKEN",
    "HUBSPOT_CLIENT_ID","HUBSPOT_CLIENT_SECRET","HUBSPOT_ACCESS_TOKEN","HUBSPOT_REFRESH_TOKEN",
    "HUBSPOT_PORTAL_ID","HUBSPOT_API_KEY",
    "ANTHROPIC_API_KEY","SLACK_WEBHOOK_URL","DATABASE_URL","APP_BASE_URL",
    "GA4_MEASUREMENT_ID","GA4_PROPERTY_ID","GA4_API_SECRET","GA4_CREDENTIALS_JSON",
    "GTM_ACCOUNT_ID","GTM_CONTAINER_ID","GTM_PUBLIC_ID","GTM_CREDENTIALS_JSON",
    "SLACK_BOT_TOKEN","SLACK_SIGNING_SECRET",
    "SMTP_HOST","SMTP_PORT","SMTP_USER","SMTP_PASSWORD","SMTP_FROM","APP_URL",
]


def _load_env_overrides() -> dict:
    """Load env vars saved via the form from ixc_config (Vercel + DB only)."""
    overrides = config_store.load("env_overrides", None, {})
    return overrides if isinstance(overrides, dict) else {}


def _apply_env_overrides() -> None:
    """Inject DB-persisted env vars into os.environ (called at request time)."""
    for k, v in _load_env_overrides().items():
        if v:
            os.environ[k] = v


def _get_env() -> dict:
    _apply_env_overrides()
    return {k: os.getenv(k, "") for k in _ENV_KEYS}


def _load_accounts() -> list:
    data = config_store.load("accounts", ACCOUNTS_PATH, {"produtos": []})
    return data.get("produtos", []) if isinstance(data, dict) else []


def _load_ltv() -> dict:
    _empty = {
        "global": {"ltv_medio": 0, "mrr": 0, "arr": 0, "total_clientes": 0,
                   "ativos": 0, "churned": 0, "churn_rate_pct": 0},
        "por_produto": {},
        "gerado_em": None,
    }
    data = config_store.load("ltv_metrics", LTV_PATH, _empty)
    return data if isinstance(data, dict) else _empty


def _load_taxonomy():
    with open(TAXONOMY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _load_thresholds():
    data = config_store.load("thresholds", THRESHOLDS_PATH, {"produtos": {}})
    return data.get("produtos", {}) if isinstance(data, dict) else {}


def _list_products() -> list[str]:
    """Merge product names from products.json and accounts.json so both sources are covered."""
    names: set[str] = set()
    for key, path in [("products", PRODUCTS_PATH), ("accounts", ACCOUNTS_PATH)]:
        try:
            data = config_store.load(key, path, {"produtos": []})
            if isinstance(data, dict):
                for p in data.get("produtos", []):
                    if isinstance(p, dict) and p.get("nome"):
                        names.add(str(p["nome"]))
        except Exception as exc:
            logger.warning("_list_products [%s]: %s", key, exc)
    return sorted(names)


def _get_last_sync() -> str:
    """Return ISO timestamp of last successful pipeline run, or empty string."""
    data = config_store.load("pipeline_last_sync", None, {})
    return data.get("synced_at", "") if isinstance(data, dict) else ""


def _save_last_sync(total: int, date_from, date_to) -> None:
    config_store.save("pipeline_last_sync", {
        "synced_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "rows": total,
        "date_from": str(date_from),
        "date_to": str(date_to),
    }, None)


def _get_dashboard_data(days: int = 30, produto: str = ""):
    try:
        import psycopg2
        conn = psycopg2.connect(get_db_url())

        # Build optional produto filter fragment
        p_filter = "AND produto = %s" if produto else ""

        with conn.cursor() as cur:
            params_base = (days, produto) if produto else (days,)

            cur.execute(f"""
                SELECT COALESCE(SUM(spend),0), COALESCE(SUM(leads),0),
                       COALESCE(SUM(revenue),0), COALESCE(COUNT(DISTINCT channel),0),
                       COALESCE(AVG(roas),0),
                       COALESCE(SUM(impressions),0), COALESCE(SUM(clicks),0)
                FROM campaigns_daily WHERE date >= CURRENT_DATE - %s {p_filter}
            """, params_base)
            spend, leads, revenue, channels, roas, impressions, clicks = cur.fetchone()

            # Funil CRM — colunas adicionadas via migration; fallback para 0 se não existirem
            mqls = sqls = sals = deals_closed = 0
            try:
                cur.execute(f"""
                    SELECT COALESCE(SUM(mqls),0), COALESCE(SUM(sqls),0),
                           COALESCE(SUM(sals),0), COALESCE(SUM(deals_closed),0)
                    FROM campaigns_daily WHERE date >= CURRENT_DATE - %s {p_filter}
                """, params_base)
                mqls, sqls, sals, deals_closed = cur.fetchone()
            except Exception:
                conn.rollback()

            cur.execute(f"""
                SELECT channel,
                       COALESCE(ROUND(SUM(spend)::numeric,2), 0) AS spend,
                       COALESCE(SUM(leads), 0) AS leads,
                       COALESCE(SUM(impressions), 0) AS impressions,
                       COALESCE(SUM(clicks), 0) AS clicks,
                       COALESCE(ROUND(
                           CASE WHEN SUM(spend)>0 THEN SUM(revenue)/SUM(spend) ELSE 0 END
                       ::numeric,2), 0) AS roas,
                       COALESCE(ROUND(
                           CASE WHEN SUM(leads)>0 THEN SUM(spend)/SUM(leads) ELSE 0 END
                       ::numeric,2), 0) AS cpl,
                       COALESCE(ROUND(
                           CASE WHEN SUM(clicks)>0 THEN SUM(spend)/SUM(clicks) ELSE 0 END
                       ::numeric,4), 0) AS cpc,
                       COALESCE(ROUND(
                           CASE WHEN SUM(impressions)>0 THEN SUM(clicks)::numeric/SUM(impressions) ELSE 0 END
                       ::numeric,4), 0) AS ctr
                FROM campaigns_daily WHERE date >= CURRENT_DATE - %s {p_filter}
                GROUP BY channel ORDER BY spend DESC
            """, params_base)
            cols = [d[0] for d in cur.description]
            by_channel = [dict(zip(cols, r)) for r in cur.fetchall()]

            # Funil por canal — opcional
            try:
                cur.execute(f"""
                    SELECT channel,
                           COALESCE(SUM(mqls),0), COALESCE(SUM(sqls),0),
                           COALESCE(SUM(sals),0), COALESCE(SUM(deals_closed),0)
                    FROM campaigns_daily WHERE date >= CURRENT_DATE - %s {p_filter}
                    GROUP BY channel
                """, params_base)
                funnel_by_ch = {r[0]: {"mqls": int(r[1]), "sqls": int(r[2]),
                                       "sals": int(r[3]), "deals_closed": int(r[4])}
                                for r in cur.fetchall()}
                for row in by_channel:
                    f = funnel_by_ch.get(row["channel"], {})
                    row["mqls"]         = f.get("mqls", 0)
                    row["sqls"]         = f.get("sqls", 0)
                    row["sals"]         = f.get("sals", 0)
                    row["deals_closed"] = f.get("deals_closed", 0)
            except Exception:
                conn.rollback()
                for row in by_channel:
                    row.setdefault("mqls", 0)
                    row.setdefault("sqls", 0)
                    row.setdefault("sals", 0)
                    row.setdefault("deals_closed", 0)

            cur.execute(f"""
                SELECT campaign_utm, MAX(campaign_name) AS campaign_name, channel, produto,
                       COALESCE(ROUND(SUM(spend)::numeric,2), 0) AS spend,
                       COALESCE(SUM(leads), 0) AS leads,
                       COALESCE(SUM(impressions), 0) AS impressions,
                       COALESCE(SUM(clicks), 0) AS clicks,
                       COALESCE(ROUND(SUM(revenue)::numeric,2), 0) AS revenue,
                       COALESCE(ROUND(
                           CASE WHEN SUM(spend)>0 THEN SUM(revenue)/SUM(spend) ELSE 0 END
                       ::numeric,2), 0) AS roas,
                       COALESCE(ROUND(
                           CASE WHEN SUM(leads)>0 THEN SUM(spend)/SUM(leads) ELSE 0 END
                       ::numeric,2), 0) AS cpl
                FROM campaigns_daily WHERE date >= CURRENT_DATE - %s {p_filter}
                GROUP BY campaign_utm, channel, produto
                ORDER BY spend DESC LIMIT 10
            """, params_base)
            cols = [d[0] for d in cur.description]
            top_campaigns = [dict(zip(cols, r)) for r in cur.fetchall()]

            # Funil por campanha — opcional
            try:
                cur.execute(f"""
                    SELECT campaign_utm,
                           COALESCE(SUM(mqls),0), COALESCE(SUM(sqls),0),
                           COALESCE(SUM(sals),0), COALESCE(SUM(deals_closed),0)
                    FROM campaigns_daily WHERE date >= CURRENT_DATE - %s {p_filter}
                    GROUP BY campaign_utm
                """, params_base)
                funnel_by_utm = {r[0]: (int(r[1]), int(r[2]), int(r[3]), int(r[4])) for r in cur.fetchall()}
                for row in top_campaigns:
                    f = funnel_by_utm.get(row["campaign_utm"], (0, 0, 0, 0))
                    row["mqls"], row["sqls"], row["sals"], row["deals_closed"] = f
            except Exception:
                conn.rollback()
                for row in top_campaigns:
                    row.setdefault("mqls", 0)
                    row.setdefault("sqls", 0)
                    row.setdefault("sals", 0)
                    row.setdefault("deals_closed", 0)

            # Alerts are not filtered by product (show all)
            cur.execute("""
                SELECT alert_type, severity, message, sent_at::text, campaign_utm
                FROM alerts_log WHERE sent_at >= NOW() - INTERVAL '48 hours'
                ORDER BY sent_at DESC LIMIT 8
            """)
            cols = [d[0] for d in cur.description]
            alerts = [dict(zip(cols, r)) for r in cur.fetchall()]

        conn.close()
        cpl = round(spend / leads, 2) if leads else 0
        mer = round(revenue / spend, 2) if spend > 0 else 0
        ctr_geral = round(clicks / impressions * 100, 2) if impressions else 0
        cpc_geral = round(spend / clicks, 4) if clicks else 0
        return {
            "kpis": {"spend": float(spend), "leads": int(leads), "revenue": float(revenue),
                     "channels": int(channels), "roas": float(roas), "cpl": cpl,
                     "mer": mer, "deals": int(deals_closed), "roas_meta": 4.0,
                     "impressions": int(impressions), "clicks": int(clicks),
                     "ctr": ctr_geral, "cpc": cpc_geral,
                     "mqls": int(mqls), "sqls": int(sqls), "sals": int(sals), "deals_closed": int(deals_closed)},
            "by_channel": by_channel, "top_campaigns": top_campaigns, "alerts": alerts,
        }
    except Exception as e:
        logger.warning("DB indisponível: %s", e)
        return {
            "kpis": {"spend":0,"leads":0,"revenue":0,"channels":0,"roas":0,"cpl":0,"mer":0,"deals":0,
                     "roas_meta":4.0,"impressions":0,"clicks":0,"ctr":0,"cpc":0,
                     "mqls":0,"sqls":0,"sals":0,"deals_closed":0},
            "by_channel": [], "top_campaigns": [], "alerts": [],
        }


def _build_gtm_container(ga4_id: str, meta_pixel: str, li_partner: str, tiktok_pixel: str) -> dict:
    """Generates a minimal but complete GTM container JSON for import."""
    tags, triggers, variables = [], [], []
    uid = 1

    # ── Built-in triggers ────────────────────────────────────────────────
    triggers.append({"accountId":"0","containerId":"0","triggerId":"2147479553",
                     "name":"All Pages","type":"PAGEVIEW","fingerprint":"1"})
    triggers.append({"accountId":"0","containerId":"0","triggerId":"2147479554",
                     "name":"DOM Ready","type":"DOM_READY","fingerprint":"2"})

    # ── Variables: UTM dataLayer ──────────────────────────────────────────
    for utm_var in ["utm_source","utm_medium","utm_campaign","utm_content","utm_term"]:
        variables.append({
            "accountId":"0","containerId":"0",
            "variableId": str(uid),
            "name": f"DLV - {utm_var}",
            "type": "v",
            "parameter": [
                {"type":"INTEGER","key":"dataLayerVersion","value":"2"},
                {"type":"BOOLEAN","key":"setDefaultValue","value":"false"},
                {"type":"TEMPLATE","key":"name","value": utm_var},
            ],
        })
        uid += 1

    # ── GA4 Configuration tag ─────────────────────────────────────────────
    if ga4_id:
        tags.append({
            "accountId":"0","containerId":"0","tagId": str(uid),
            "name": "GA4 - Configuration",
            "type": "googtag",
            "parameter": [{"type":"TEMPLATE","key":"id","value": ga4_id}],
            "firingTriggerId": ["2147479553"],
        })
        uid += 1
        tags.append({
            "accountId":"0","containerId":"0","tagId": str(uid),
            "name": "GA4 - generate_lead",
            "type": "gaawe",
            "parameter": [
                {"type":"TEMPLATE","key":"measurementId","value": ga4_id},
                {"type":"TEMPLATE","key":"eventName","value":"generate_lead"},
                {"type":"LIST","key":"eventParameters","list":[
                    {"type":"MAP","map":[
                        {"type":"TEMPLATE","key":"name","value":"utm_source"},
                        {"type":"TEMPLATE","key":"value","value":"{{DLV - utm_source}}"},
                    ]},
                    {"type":"MAP","map":[
                        {"type":"TEMPLATE","key":"name","value":"utm_campaign"},
                        {"type":"TEMPLATE","key":"value","value":"{{DLV - utm_campaign}}"},
                    ]},
                ]},
            ],
            "firingTriggerId": ["{{lead_trigger}}"],
        })
        uid += 1

    # ── Meta Pixel ────────────────────────────────────────────────────────
    if meta_pixel:
        tags.append({
            "accountId":"0","containerId":"0","tagId": str(uid),
            "name": "Meta Pixel - PageView",
            "type": "html",
            "parameter": [{"type":"TEMPLATE","key":"html","value":
                f"""<script>
!function(f,b,e,v,n,t,s){{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?n.callMethod.apply(n,arguments):n.queue.push(arguments)}};if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}}(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');
fbq('init','{meta_pixel}');fbq('track','PageView');
</script>"""}],
            "firingTriggerId": ["2147479553"],
        })
        uid += 1
        tags.append({
            "accountId":"0","containerId":"0","tagId": str(uid),
            "name": "Meta Pixel - Lead",
            "type": "html",
            "parameter": [{"type":"TEMPLATE","key":"html","value":
                "<script>fbq('track','Lead',{utm_campaign:'{{DLV - utm_campaign}}',utm_source:'{{DLV - utm_source}}'});</script>"}],
            "firingTriggerId": ["{{lead_trigger}}"],
        })
        uid += 1

    # ── LinkedIn Insight Tag ──────────────────────────────────────────────
    if li_partner:
        tags.append({
            "accountId":"0","containerId":"0","tagId": str(uid),
            "name": "LinkedIn - Insight Tag",
            "type": "html",
            "parameter": [{"type":"TEMPLATE","key":"html","value":
                f'<script>_linkedin_partner_id="{li_partner}";window._linkedin_data_partner_ids=window._linkedin_data_partner_ids||[];window._linkedin_data_partner_ids.push(_linkedin_partner_id);</script><script async src="https://snap.licdn.com/li.lms-analytics/insight.min.js"></script>'}],
            "firingTriggerId": ["2147479553"],
        })
        uid += 1

    # ── TikTok Pixel ─────────────────────────────────────────────────────
    if tiktok_pixel:
        tags.append({
            "accountId":"0","containerId":"0","tagId": str(uid),
            "name": "TikTok Pixel - PageView",
            "type": "html",
            "parameter": [{"type":"TEMPLATE","key":"html","value":
                f"""<script>!function(w,d,t){{w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];ttq.methods=["page","track","identify"];ttq.setAndDefer=function(t,e){{t[e]=function(){{t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}}};for(var i=0;i<ttq.methods.length;i++)ttq.setAndDefer(ttq,ttq.methods[i]);ttq.load=function(e){{var i="https://analytics.tiktok.com/i18n/pixel/events.js";ttq._i=ttq._i||{{}},ttq._i[e]=[],ttq._t=ttq._t||{{}},ttq._t[e]=+new Date;var o=document.createElement("script");o.type="text/javascript",o.async=!0,o.src=i+"?sdkid="+e+"&lib="+t;var a=document.getElementsByTagName("script")[0];a.parentNode.insertBefore(o,a)}};ttq.load('{tiktok_pixel}');ttq.page()}}(window,document,'ttq');</script>"""}],
            "firingTriggerId": ["2147479553"],
        })
        uid += 1

    return {
        "exportFormatVersion": 2,
        "exportTime": "2025-01-01 00:00:00",
        "containerVersion": {
            "path": "accounts/0/containers/0/versions/0",
            "accountId": "0",
            "containerId": "0",
            "containerVersionId": "0",
            "container": {"path":"accounts/0/containers/0","accountId":"0","containerId":"0",
                         "name":"IXCTraffic Export","publicId":"GTM-XXXXXX",
                         "usageContext":["WEB"]},
            "tag": tags,
            "trigger": triggers,
            "variable": variables,
        }
    }


def _get_alert_count():
    try:
        import psycopg2
        conn = psycopg2.connect(get_db_url())
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM alerts_log WHERE sent_at >= NOW() - INTERVAL '48 hours' AND severity = 'critico'")
            count = cur.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    try:
        _apply_env_overrides()
        params = request.query_params
        produto = params.get("produto", "").strip()
        date_from_str = params.get("date_from", "").strip()
        date_to_str   = params.get("date_to", "").strip()

        if date_from_str and date_to_str:
            try:
                from datetime import date as _date
                df = _date.fromisoformat(date_from_str)
                dt = _date.fromisoformat(date_to_str)
                days = (_date.today() - df).days + 1
                days = max(1, min(days, 730))
            except ValueError:
                days = 30
                date_from_str = date_to_str = ""
        else:
            try:
                days = int(params.get("days", 30))
                days = max(7, min(days, 365))
            except ValueError:
                days = 30
            date_from_str = date_to_str = ""

        data = _get_dashboard_data(days, produto)
        ltv_all = _load_ltv()

        if produto and isinstance(ltv_all, dict) and produto in ltv_all.get("por_produto", {}):
            p = ltv_all["por_produto"][produto]
            ltv_filtered = {
                "global": {
                    "ltv_medio":        p.get("ltv_medio", 0),
                    "mrr":              p.get("mrr", 0),
                    "arr":              p.get("arr", 0),
                    "total_clientes":   p.get("total_clientes", 0),
                    "ativos":           p.get("ativos", 0),
                    "churned":          p.get("churned", 0),
                    "churn_rate_pct":   p.get("churn_rate_pct", 0),
                },
                "por_produto": {produto: p},
                "gerado_em": ltv_all.get("gerado_em"),
            }
        else:
            ltv_filtered = ltv_all

        try:
            produtos = _list_products()
        except Exception:
            produtos = []

        return templates.TemplateResponse("dashboard.html", {
            "request": request, "page": "dashboard", "days": days,
            "produto_filtro": produto,
            "date_from": date_from_str,
            "date_to":   date_to_str,
            "produtos": produtos,
            "active_product": _active_product, "alert_count": _get_alert_count(),
            "ltv": ltv_filtered,
            "last_sync": _get_last_sync(),
            **data,
        })
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.error("Dashboard error: %s\n%s", exc, tb)
        return HTMLResponse(
            f"<pre style='font-family:monospace;padding:2rem;'>"
            f"<b>Dashboard error</b> — {type(exc).__name__}: {exc}\n\n{tb}</pre>",
            status_code=500,
        )

@app.get("/api/dashboard/chart-data")
async def dashboard_chart_data(days: int = 30, produto: str = ""):
    """Returns daily spend/leads trend and channel breakdown for charts."""
    _apply_env_overrides()
    try:
        import psycopg2
        conn = psycopg2.connect(get_db_url())
        p_filter = "AND produto = %s" if produto else ""
        params = (days, produto) if produto else (days,)

        with conn.cursor() as cur:
            # Daily totals (all channels combined)
            cur.execute(f"""
                SELECT date::text, COALESCE(SUM(spend),0), COALESCE(SUM(leads),0)
                FROM campaigns_daily
                WHERE date >= CURRENT_DATE - %s {p_filter}
                GROUP BY date ORDER BY date
            """, params)
            daily = [{"date": r[0], "spend": float(r[1]), "leads": int(r[2])}
                     for r in cur.fetchall()]

            # Spend by channel
            cur.execute(f"""
                SELECT channel, COALESCE(SUM(spend),0), COALESCE(SUM(leads),0)
                FROM campaigns_daily
                WHERE date >= CURRENT_DATE - %s {p_filter}
                GROUP BY channel ORDER BY SUM(spend) DESC
            """, params)
            channels = [{"channel": r[0], "spend": float(r[1]), "leads": int(r[2])}
                        for r in cur.fetchall()]

            # Funil summary
            mqls = sqls = sals = deals_closed = leads_total = 0
            try:
                cur.execute(f"""
                    SELECT COALESCE(SUM(leads),0), COALESCE(SUM(mqls),0),
                           COALESCE(SUM(sqls),0), COALESCE(SUM(sals),0),
                           COALESCE(SUM(deals_closed),0)
                    FROM campaigns_daily WHERE date >= CURRENT_DATE - %s {p_filter}
                """, params)
                leads_total, mqls, sqls, sals, deals_closed = cur.fetchone()
            except Exception:
                conn.rollback()

        conn.close()
        return {
            "daily": daily,
            "channels": channels,
            "funnel": [
                {"label": "Leads",    "value": int(leads_total)},
                {"label": "MQL",      "value": int(mqls)},
                {"label": "SQL",      "value": int(sqls)},
                {"label": "SAL",      "value": int(sals)},
                {"label": "Fechados", "value": int(deals_closed)},
            ],
        }
    except Exception as exc:
        logger.warning("chart-data error: %s", exc)
        return {"daily": [], "channels": [], "funnel": []}


@app.get("/conexoes", response_class=HTMLResponse)
async def conexoes(request: Request):
    return templates.TemplateResponse("conexoes.html", {
        "request": request, "page": "conexoes",
        "env": _get_env(), "accounts": _load_accounts(),
        "active_product": _active_product, "alert_count": _get_alert_count(),
        "is_vercel": config_store.IS_VERCEL,
        "has_db": bool(get_db_url()),
    })

@app.get("/campanhas", response_class=HTMLResponse)
async def campanhas(request: Request):
    try:
        _apply_env_overrides()
        # Date filter — default: last 7 days (same as dashboard default)
        from datetime import date as _date, timedelta as _td
        params = dict(request.query_params)
        try:
            days = int(params.get("days", 7))
        except Exception:
            days = 7
        date_to_str   = params.get("date_to",   str(_date.today()))
        date_from_str = params.get("date_from",  str(_date.today() - _td(days=days - 1)))

        all_campaigns = []
        try:
            import psycopg2
            conn = psycopg2.connect(get_db_url())
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT campaign_utm,
                           MAX(campaign_name) AS campaign_name,
                           channel, produto,
                           COALESCE(ROUND(SUM(spend)::numeric,2), 0)       AS spend,
                           COALESCE(SUM(leads), 0)                          AS leads,
                           COALESCE(SUM(clicks), 0)                         AS clicks,
                           COALESCE(SUM(impressions), 0)                    AS impressions,
                           COALESCE(ROUND(SUM(revenue)::numeric,2), 0)      AS revenue,
                           COALESCE(ROUND(
                               CASE WHEN SUM(spend)>0 THEN SUM(revenue)/SUM(spend) ELSE 0 END
                           ::numeric,2), 0) AS roas,
                           COALESCE(ROUND(
                               CASE WHEN SUM(leads)>0 THEN SUM(spend)/SUM(leads) ELSE 0 END
                           ::numeric,2), 0) AS cpl,
                           COALESCE(ROUND(
                               CASE WHEN SUM(clicks)>0 THEN SUM(spend)/SUM(clicks) ELSE 0 END
                           ::numeric,2), 0) AS cpc,
                           COALESCE(ROUND(
                               CASE WHEN SUM(impressions)>0 THEN SUM(clicks)::numeric/SUM(impressions) ELSE 0 END
                           ::numeric,4), 0) AS ctr
                    FROM campaigns_daily
                    WHERE date BETWEEN %s AND %s
                    GROUP BY campaign_utm, channel, produto
                    ORDER BY spend DESC
                """, (date_from_str, date_to_str))
                cols = [d[0] for d in cur.description]
                all_campaigns = [dict(zip(cols, r)) for r in cur.fetchall()]

                # Funil por campanha — colunas opcionais, fallback para 0
                try:
                    cur.execute("""
                        SELECT campaign_utm,
                               COALESCE(SUM(mqls),0), COALESCE(SUM(sqls),0),
                               COALESCE(SUM(sals),0), COALESCE(SUM(deals_closed),0)
                        FROM campaigns_daily
                        WHERE date BETWEEN %s AND %s
                        GROUP BY campaign_utm
                    """, (date_from_str, date_to_str))
                    funnel_map = {r[0]: (int(r[1]), int(r[2]), int(r[3]), int(r[4])) for r in cur.fetchall()}
                    for c in all_campaigns:
                        f = funnel_map.get(c["campaign_utm"], (0, 0, 0, 0))
                        c["mqls"], c["sqls"], c["sals"], c["deals_closed"] = f
                except Exception:
                    conn.rollback()
                    for c in all_campaigns:
                        c.setdefault("mqls", 0)
                        c.setdefault("sqls", 0)
                        c.setdefault("sals", 0)
                        c.setdefault("deals_closed", 0)

            conn.close()
        except Exception as e:
            logger.warning("campanhas DB query: %s", e)
        try:
            produtos = _list_products()
        except Exception:
            produtos = []
        return templates.TemplateResponse("campanhas.html", {
            "request": request, "page": "campanhas",
            "campaigns": all_campaigns, "produtos": produtos,
            "date_from": date_from_str, "date_to": date_to_str, "days": days,
            "active_product": _active_product, "alert_count": _get_alert_count(),
        })
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.error("Campanhas error: %s\n%s", exc, tb)
        return HTMLResponse(
            f"<pre style='font-family:monospace;padding:2rem;'>"
            f"<b>Campanhas error</b> — {type(exc).__name__}: {exc}\n\n{tb}</pre>",
            status_code=500,
        )

@app.get("/alertas", response_class=HTMLResponse)
async def alertas(request: Request):
    _apply_env_overrides()
    alerts, stats = [], {"total":0,"critico":0,"atencao":0,"ok":0}
    # Métricas reais por produto (mês atual) para mostrar progresso vs meta
    produto_metrics: dict = {}
    try:
        import psycopg2
        conn = psycopg2.connect(get_db_url())
        with conn.cursor() as cur:
            cur.execute("""
                SELECT alert_type, severity, message, sent_at::text, campaign_utm, produto
                FROM alerts_log WHERE sent_at >= NOW() - INTERVAL '48 hours'
                ORDER BY sent_at DESC
            """)
            cols = [d[0] for d in cur.description]
            alerts = [dict(zip(cols, r)) for r in cur.fetchall()]
            stats["total"] = len(alerts)
            for a in alerts:
                stats[a["severity"]] = stats.get(a["severity"],0) + 1
            # Métricas do mês atual por produto
            cur.execute("""
                SELECT produto,
                       COALESCE(ROUND(SUM(spend)::numeric,2),0)       AS spend,
                       COALESCE(SUM(leads),0)                         AS leads,
                       COALESCE(SUM(clicks),0)                        AS clicks,
                       COALESCE(COUNT(DISTINCT date),0)               AS dias,
                       COALESCE(ROUND(AVG(NULLIF(cpl,0))::numeric,2),0) AS cpl_medio
                FROM campaigns_daily
                WHERE date >= DATE_TRUNC('month', CURRENT_DATE)
                  AND produto IS NOT NULL
                GROUP BY produto
            """)
            for row in cur.fetchall():
                p = row[0]
                dias = row[4] or 1
                produto_metrics[p] = {
                    "spend":     float(row[1]),
                    "leads":     int(row[2]),
                    "clicks":    int(row[3]),
                    "cpl_medio": float(row[5]),
                    "leads_dia": round(int(row[2]) / dias, 1),
                }
        conn.close()
    except Exception as exc:
        logger.warning("alertas metrics error: %s", exc)

    # Garantir que todos os produtos listados apareçam nas metas
    thresholds = _load_thresholds()
    default_t = {"cpl_meta":0.0,"roas_meta":0.0,"mer_meta":0.0,
                 "leads_meta_diario":0,"budget_mensal":0.0,"frequencia_max_meta":4.0}
    for p in _list_products():
        if p not in thresholds:
            thresholds[p] = default_t.copy()

    return templates.TemplateResponse("alertas.html", {
        "request": request, "page": "alertas",
        "alerts": alerts, "stats": stats,
        "thresholds": thresholds,
        "produto_metrics": produto_metrics,
        "active_product": _active_product, "alert_count": stats.get("critico",0),
    })

@app.get("/agente", response_class=HTMLResponse)
async def agente(request: Request):
    try:
        produtos = _list_products()
    except Exception:
        produtos = []
    return templates.TemplateResponse("agente.html", {
        "request": request, "page": "agente",
        "produtos": produtos, "active_product": _active_product, "alert_count": _get_alert_count(),
    })

@app.get("/utm", response_class=HTMLResponse)
async def utm_page(request: Request):
    return templates.TemplateResponse("utm.html", {
        "request": request, "page": "utm",
        "taxonomy": _load_taxonomy(), "active_product": _active_product, "alert_count": _get_alert_count(),
    })


@app.get("/rastreamento", response_class=HTMLResponse)
async def rastreamento_page(request: Request):
    _apply_env_overrides()
    return templates.TemplateResponse("rastreamento.html", {
        "request": request, "page": "rastreamento",
        "env": _get_env(),
        "accounts": _load_accounts(),
        "active_product": _active_product,
        "alert_count": _get_alert_count(),
    })


@app.get("/api/rastreamento/gtm-export")
async def gtm_export(product: str = ""):
    _apply_env_overrides()
    accounts = _load_accounts()
    produto = next((p for p in accounts if p["nome"] == product), None)
    env = _get_env()

    r = produto.get("rastreamento", {}) if produto else {}
    contas = produto.get("contas", {}) if produto else {}

    gtm_id       = r.get("gtm_public_id") or env.get("GTM_PUBLIC_ID", "")
    ga4_id       = r.get("ga4_measurement_id") or env.get("GA4_MEASUREMENT_ID", "")
    meta_pixel   = contas.get("meta", {}).get("pixel_id") or env.get("META_PIXEL_ID", "")
    li_partner   = contas.get("linkedin", {}).get("partner_id") or env.get("LINKEDIN_PARTNER_ID", "")
    tiktok_pixel = contas.get("tiktok", {}).get("pixel_id") or env.get("TIKTOK_PIXEL_ID", "")

    container = _build_gtm_container(ga4_id, meta_pixel, li_partner, tiktok_pixel)
    return JSONResponse(container)


# ── API: Env ──────────────────────────────────────────────────────────────────

@app.post("/api/env/save")
async def env_save(request: Request):
    data = await request.json()
    saved = 0
    for key, value in data.items():
        if not value:
            continue
        os.environ[key] = value
        saved += 1
        if not config_store.IS_VERCEL:
            try:
                if not ENV_PATH.exists():
                    ENV_PATH.write_text("")
                set_key(str(ENV_PATH), key, value)
            except Exception:
                pass
    if not config_store.IS_VERCEL:
        load_dotenv(ENV_PATH, override=True)

    # Persist to DB so vars survive across serverless invocations
    db_url = get_db_url()
    if db_url:
        try:
            existing = _load_env_overrides()
            for key, value in data.items():
                if value:
                    existing[key] = value
            ok = config_store.save("env_overrides", existing, None)
            if ok:
                return {"message": f"{saved} variável(is) salva(s) no banco ✓"}
            else:
                return {
                    "message": f"{saved} variável(is) ativa(s) nesta sessão — falha ao gravar no banco. Verifique a conexão em Ações → Health Check.",
                    "warn": True,
                }
        except Exception as exc:
            logger.error("env_save DB error: %s", exc)
            return {
                "message": f"Erro ao salvar no banco: {exc}",
                "warn": True,
            }

    if config_store.IS_VERCEL:
        return {
            "message": "Banco não detectado (POSTGRES_URL / DATABASE_URL ausente). Credenciais ativas só nesta sessão.",
            "warn": True,
        }
    return {"message": f"{saved} variável(is) salva(s) no .env"}


@app.post("/api/env/delete")
async def env_delete(request: Request):
    """Remove chaves específicas dos overrides salvos no banco."""
    data = await request.json()
    keys_to_delete = data.get("keys", [])
    if not keys_to_delete:
        return JSONResponse({"error": "Nenhuma chave informada."}, status_code=400)
    db_url = get_db_url()
    if not db_url:
        for k in keys_to_delete:
            os.environ.pop(k, None)
        return {"message": f"{len(keys_to_delete)} chave(s) removida(s) da sessão."}
    try:
        existing = _load_env_overrides()
        removed = [k for k in keys_to_delete if k in existing]
        for k in keys_to_delete:
            existing.pop(k, None)
            os.environ.pop(k, None)
        config_store.save("env_overrides", existing, None)
        return {"message": f"{len(removed)} chave(s) removida(s) do banco: {', '.join(removed)}"}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── API: UTM ──────────────────────────────────────────────────────────────────

@app.post("/api/utm/build")
async def utm_build(request: Request):
    try:
        from utm_builder import build_utm
        d = await request.json()
        url = build_utm(
            d.get("base_url","https://seusite.com.br/lp"),
            source=d["source"], medium=d["medium"],
            campaign_parts={"produto":d["produto"],"funil":d["funil"],"objetivo":d["objetivo"],"ano_mes":d["ano_mes"]},
            content=d.get("content",""), term=d.get("term",""),
        )
        return {"url": url}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

@app.post("/api/utm/validate")
async def utm_validate(request: Request):
    from utm_validator import validate_utm
    d = await request.json()
    result = validate_utm(d.get("url",""))
    return {"status": result["status"], "params": result["params"], "errors": result["erros"]}


# ── API: Agent ────────────────────────────────────────────────────────────────

@app.post("/api/agent/product")
async def agent_set_product(request: Request):
    global _agent_instance, _active_product
    d = await request.json()
    product = d.get("product","")
    try:
        from agent import CampaignAgent
        if _agent_instance is None:
            _agent_instance = CampaignAgent()
        _agent_instance.set_product(product)
        _active_product = product
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/agent/chat")
async def agent_chat(request: Request):
    global _agent_instance, _active_product

    # Accept both JSON and multipart/form-data (for creative uploads)
    content_type = request.headers.get("content-type", "")
    creative_path: str = None
    creative_name: str = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        message = form.get("message", "")
        creative_file = form.get("creative")
        if creative_file and hasattr(creative_file, "filename"):
            import tempfile, shutil
            suffix = "." + creative_file.filename.rsplit(".", 1)[-1].lower()
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            shutil.copyfileobj(creative_file.file, tmp)
            tmp.close()
            creative_path = tmp.name
            creative_name = creative_file.filename
    else:
        d = await request.json()
        message = d.get("message", "")

    try:
        from agent import CampaignAgent
        from briefing import _parse_objetivo, _parse_canal, _parse_budget
        from campaign_creator import create_campaign
        from optimizer import diagnose, optimize, forecast

        if _agent_instance is None:
            _agent_instance = CampaignAgent()

        # Inject creative context into the message if a file was uploaded
        if creative_path and creative_name:
            message = (
                f"{message}\n\n[CRIATIVO ANEXADO: {creative_name} | caminho_temp: {creative_path}]"
                "\nUse as ferramentas meta_upload_image ou meta_upload_video com esse caminho quando for criar o anúncio."
            )

        if message.startswith("/diagnostico"):
            if not _active_product:
                return {"reply": "Selecione um produto primeiro."}
            reply = diagnose(_active_product, days=7, agent=_agent_instance)
        elif message.startswith("/otimizar"):
            if not _active_product:
                return {"reply": "Selecione um produto primeiro."}
            reply = optimize(_active_product, days=7, agent=_agent_instance)
        elif message.startswith("/prever"):
            if not _active_product:
                return {"reply": "Selecione um produto primeiro."}
            parts = message.split()
            try: budget = float(parts[1].replace("R$","").replace(",","."))
            except: budget = 5000.0
            reply = forecast(_active_product, budget=budget, agent=_agent_instance)
        elif message.startswith("/criar") or message.startswith("/briefing"):
            briefing = {"objetivo":"leads","canal":["meta","google"],"budget":"R$ 5.000","prazo":"30 dias"}
            if _active_product:
                briefing["produto"] = _active_product
            reply = create_campaign(briefing, _active_product or "produto-a", agent=_agent_instance)
        else:
            reply = _agent_instance.chat(message)

        return {"reply": reply}
    except Exception as e:
        logger.error("Agent error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        # Clean up temp file
        if creative_path:
            try:
                import os as _os
                _os.unlink(creative_path)
            except Exception:
                pass

@app.post("/api/agent/reset")
async def agent_reset():
    global _agent_instance
    if _agent_instance:
        _agent_instance.reset()
    return {"ok": True}


# ── API: Alerts ───────────────────────────────────────────────────────────────

@app.post("/api/alerts/check")
async def alerts_check():
    try:
        from alerts import AlertSystem
        system = AlertSystem()
        found = system.check_all()
        return {"count": len(found)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/alerts/thresholds")
async def alerts_thresholds(request: Request):
    data = await request.json()
    config_store.save("thresholds", {"produtos": data}, THRESHOLDS_PATH)
    return {"message": "Thresholds salvos"}


# ── API: Actions ──────────────────────────────────────────────────────────────

@app.post("/api/pipeline/run")
async def pipeline_run(request: Request):
    params = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    try:
        from load_database import run_pipeline, _load_accounts
        from datetime import date, timedelta

        # Load DB-persisted env vars before running pipeline
        _apply_env_overrides()

        # Quick diagnostic before full run
        accounts = _load_accounts()
        if not accounts:
            return JSONResponse({"message": "Nenhum produto em accounts.json. Configure em Conexões → Contas por Produto."}, status_code=400)

        # Accept either date_from/date_to strings or days integer
        if params.get("date_from") and params.get("date_to"):
            date_from = date.fromisoformat(params["date_from"])
            date_to   = date.fromisoformat(params["date_to"])
        else:
            days = int(params.get("days", 7))
            date_to   = date.today()
            date_from = date_to - timedelta(days=days)

        total = run_pipeline(date_from=date_from, date_to=date_to)
        _save_last_sync(total, date_from, date_to)
        return {"message": f"Pipeline concluído: {total} campanhas carregadas ({date_from} → {date_to}, {len(accounts)} produto(s))"}
    except Exception as e:
        logger.error("Pipeline error: %s", e, exc_info=True)
        return JSONResponse({"message": f"Erro: {e}"}, status_code=500)


@app.get("/api/pipeline/cron")
@app.post("/api/pipeline/cron")
async def pipeline_cron(request: Request):
    """Called by Vercel Cron. Verifies CRON_SECRET header then runs pipeline for last 2 days."""
    cron_secret = os.getenv("CRON_SECRET", "")
    if cron_secret:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {cron_secret}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        from load_database import run_pipeline, _load_accounts
        from datetime import date, timedelta
        _apply_env_overrides()
        date_to   = date.today()
        date_from = date_to - timedelta(days=2)
        total = run_pipeline(date_from=date_from, date_to=date_to)
        _save_last_sync(total, date_from, date_to)
        logger.info("Cron pipeline OK: %d rows", total)
        return {"ok": True, "rows": total, "date_from": str(date_from), "date_to": str(date_to)}
    except Exception as e:
        logger.error("Cron pipeline error: %s", e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/pipeline/sync")
async def pipeline_sync(request: Request):
    """Quick sync for last 2 days — called from dashboard Sync button."""
    try:
        from load_database import run_pipeline, _load_accounts
        from datetime import date, timedelta
        _apply_env_overrides()
        accounts = _load_accounts()
        if not accounts:
            return JSONResponse({"message": "Nenhum produto configurado."}, status_code=400)
        date_to   = date.today()
        date_from = date_to - timedelta(days=2)
        total = run_pipeline(date_from=date_from, date_to=date_to)
        _save_last_sync(total, date_from, date_to)
        return {"message": f"{total} registros sincronizados"}
    except Exception as e:
        logger.error("Sync error: %s", e, exc_info=True)
        return JSONResponse({"message": f"Erro: {e}"}, status_code=500)


@app.post("/api/hubspot/create-properties")
async def hs_create_props():
    try:
        from hubspot_client import HubSpotClient
        from create_properties import create_properties, CONTACT_PROPERTIES, DEAL_PROPERTIES
        client = HubSpotClient()
        contacts = create_properties(client, "contacts", CONTACT_PROPERTIES)
        deals    = create_properties(client, "deals",    DEAL_PROPERTIES)
        return {"message": "Propriedades criadas", "contacts": contacts, "deals": deals}
    except Exception as e:
        return JSONResponse({"message": f"Erro: {e}"}, status_code=500)

@app.post("/api/hubspot/import-products")
async def hs_import_products():
    try:
        sys.path.insert(0, str(ROOT / "hubspot"))
        from import_products import import_products
        result = import_products()
        # Persist the products list via config_store (works on Vercel)
        if result.get("produtos"):
            existing = config_store.load("products", PRODUCTS_PATH, {"produtos": []})
            existing_map = {p["nome"]: p for p in existing.get("produtos", [])}
            # import_products already wrote to PRODUCTS_PATH on disk; also push to DB
            if PRODUCTS_PATH.exists():
                with open(PRODUCTS_PATH, encoding="utf-8") as f:
                    products_data = json.load(f)
                config_store.save("products", products_data, None)  # DB only (file already written)
        return {
            "message": f"Importados {result['importados']} produto(s) do HubSpot. Total: {len(result['produtos'])}",
            "importados": result["importados"],
            "produtos": result["produtos"],
        }
    except Exception as e:
        logger.error("Import products error: %s", e)
        return JSONResponse({"message": f"Erro: {e}"}, status_code=500)

@app.post("/api/ltv/refresh")
async def ltv_refresh():
    try:
        sys.path.insert(0, str(ROOT / "hubspot"))
        from ltv_calculator import run as run_ltv
        metrics = run_ltv()
        config_store.save("ltv_metrics", metrics, LTV_PATH)
        g = metrics["global"]
        return {
            "message": (
                f"LTV calculado — Médio: R$ {g['ltv_medio']:,.2f} | "
                f"MRR: R$ {g['mrr']:,.2f} | "
                f"Clientes: {g['total_clientes']} ({g['ativos']} ativos)"
            ),
            "global": g,
            "por_produto": metrics["por_produto"],
        }
    except Exception as e:
        logger.error("LTV refresh error: %s", e)
        return JSONResponse({"message": f"Erro: {e}"}, status_code=500)

@app.get("/api/ltv/data")
async def ltv_data():
    return _load_ltv()


@app.post("/api/accounts/save")
async def accounts_save(request: Request):
    data = await request.json()
    nome = data.get("nome", "").strip()
    if not nome:
        return JSONResponse({"message": "Nome do produto é obrigatório"}, status_code=400)
    accounts = _load_accounts()
    existing = next((p for p in accounts if p["nome"] == nome), None)
    default_contas = {
        "meta": {"ad_account_id": "", "pixel_id": ""},
        "google": {"customer_id": ""},
        "linkedin": {"ad_account_id": "", "partner_id": ""},
        "tiktok": {"ad_account_id": "", "pixel_id": ""},
    }
    default_rastreamento = {
        "ga4_measurement_id": "", "ga4_property_id": "",
        "gtm_public_id": "", "gtm_container_id": "",
    }
    if existing:
        existing["contas"] = data.get("contas", existing.get("contas", default_contas))
        existing["rastreamento"] = data.get("rastreamento", existing.get("rastreamento", default_rastreamento))
    else:
        accounts.append({
            "nome": nome,
            "contas": data.get("contas", default_contas),
            "rastreamento": data.get("rastreamento", default_rastreamento),
        })
    ok = config_store.save("accounts", {"produtos": accounts}, ACCOUNTS_PATH)
    if not ok:
        suffix = " — adicione DATABASE_URL nas variáveis de ambiente do Vercel para persistir" if config_store.IS_VERCEL else ""
        return JSONResponse({"message": f"Erro ao salvar contas de '{nome}'{suffix}", "error": True}, status_code=500)
    return {"message": f"Contas de '{nome}' salvas"}


@app.get("/api/tracking/snippet/{nome}", response_class=HTMLResponse)
async def tracking_snippet(nome: str):
    """Returns the full HTML tracking snippet for a product's <head>."""
    accounts = _load_accounts()
    produto = next((p for p in accounts if p["nome"] == nome), None)
    if not produto:
        return HTMLResponse(f"<!-- Produto '{nome}' não encontrado -->", status_code=404)

    r = produto.get("rastreamento", {})
    contas = produto.get("contas", {})
    gtm_id  = r.get("gtm_public_id", "").strip()
    ga4_id  = r.get("ga4_measurement_id", "").strip()
    pixel   = contas.get("meta", {}).get("pixel_id", "").strip()
    partner = contas.get("linkedin", {}).get("partner_id", "").strip()
    tiktok  = contas.get("tiktok", {}).get("pixel_id", "").strip()

    parts = [f"<!-- IXCTraffic — Snippet de rastreamento: {nome} -->"]

    if gtm_id:
        parts.append(f"""<!-- Google Tag Manager -->
<script>(function(w,d,s,l,i){{w[l]=w[l]||[];w[l].push({{'gtm.start':
new Date().getTime(),event:'gtm.js'}});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
}})(window,document,'script','dataLayer','{gtm_id}');</script>
<!-- End Google Tag Manager -->""")

    if ga4_id and not gtm_id:
        parts.append(f"""<!-- Google Analytics 4 (direto, sem GTM) -->
<script async src="https://www.googletagmanager.com/gtag/js?id={ga4_id}"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', '{ga4_id}');
</script>""")

    if pixel:
        parts.append(f"""<!-- Meta Pixel -->
<script>
!function(f,b,e,v,n,t,s){{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)}};if(!f._fbq)f._fbq=n;
n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}}(window,
document,'script','https://connect.facebook.net/en_US/fbevents.js');
fbq('init', '{pixel}');
fbq('track', 'PageView');
</script>""")

    if partner:
        parts.append(f"""<!-- LinkedIn Insight Tag -->
<script type="text/javascript">
_linkedin_partner_id = "{partner}";
window._linkedin_data_partner_ids = window._linkedin_data_partner_ids || [];
window._linkedin_data_partner_ids.push(_linkedin_partner_id);
</script>
<script type="text/javascript">
(function(l){{if(!l){{window.lintrk=function(a,b){{window.lintrk.q.push([a,b])}};
window.lintrk.q=[]}}var s=document.getElementsByTagName("script")[0];
var b=document.createElement("script");b.type="text/javascript";b.async=true;
b.src="https://snap.licdn.com/li.lms-analytics/insight.min.js";
s.parentNode.insertBefore(b,s);}})(window.lintrk);
</script>""")

    if tiktok:
        parts.append(f"""<!-- TikTok Pixel -->
<script>
!function(w,d,t){{w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];
ttq.methods=["page","track","identify","instances","debug","on","off","once","ready","alias","group","enableCookie","disableCookie"];
ttq.setAndDefer=function(t,e){{t[e]=function(){{t.push([e].concat(Array.prototype.slice.call(arguments,0)))}};}};
for(var i=0;i<ttq.methods.length;i++)ttq.setAndDefer(ttq,ttq.methods[i]);
ttq.instance=function(t){{for(var e=ttq._i[t]||[],n=0;n<ttq.methods.length;n++)ttq.setAndDefer(e,ttq.methods[n]);return e}};
ttq.load=function(e,n){{var i="https://analytics.tiktok.com/i18n/pixel/events.js";
ttq._i=ttq._i||{{}},ttq._i[e]=[],ttq._i[e]._u=i,ttq._t=ttq._t||{{}},ttq._t[e]=+new Date,ttq._o=ttq._o||{{}},ttq._o[e]=n||{{}};
var o=document.createElement("script");o.type="text/javascript",o.async=!0,o.src=i+"?sdkid="+e+"&lib="+t;
var a=document.getElementsByTagName("script")[0];a.parentNode.insertBefore(o,a)}};
ttq.load('{tiktok}');ttq.page();}}(window,document,'ttq');
</script>""")

    if gtm_id:
        parts.append(f"""<!-- GTM noscript (cole após <body>) -->
<!-- <noscript><iframe src="https://www.googletagmanager.com/ns.html?id={gtm_id}"
height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript> -->""")

    if len(parts) == 1:
        parts.append(f"<!-- Nenhum ID de rastreamento configurado para {nome}. Configure em Conexões → Contas por Produto. -->")

    return HTMLResponse("\n\n".join(parts))


@app.get("/api/accounts/tracking/{nome}")
async def accounts_tracking(nome: str):
    accounts = _load_accounts()
    produto = next((p for p in accounts if p["nome"] == nome), None)
    if not produto:
        return JSONResponse({"error": f"Produto '{nome}' não encontrado"}, status_code=404)
    return {
        "nome": nome,
        "rastreamento": produto.get("rastreamento", {}),
        "contas": {
            "meta_pixel_id": produto.get("contas", {}).get("meta", {}).get("pixel_id", ""),
            "linkedin_partner_id": produto.get("contas", {}).get("linkedin", {}).get("partner_id", ""),
            "tiktok_pixel_id": produto.get("contas", {}).get("tiktok", {}).get("pixel_id", ""),
        },
    }


@app.get("/api/google/accounts")
async def google_list_accounts():
    """Lista sub-contas do MCC do Google Ads para mapear aos produtos."""
    _apply_env_overrides()
    try:
        from google_ads_pull import list_mcc_accounts
        developer_token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
        client_id       = os.getenv("GOOGLE_ADS_CLIENT_ID", "")
        client_secret   = os.getenv("GOOGLE_ADS_CLIENT_SECRET", "")
        refresh_token   = os.getenv("GOOGLE_ADS_REFRESH_TOKEN", "")
        mcc_id          = os.getenv("GOOGLE_ADS_CUSTOMER_ID", "")
        if not all([developer_token, refresh_token, mcc_id]):
            return JSONResponse({"error": "Credenciais do Google Ads não configuradas"}, status_code=400)
        accounts = list_mcc_accounts(developer_token, client_id, client_secret, refresh_token, mcc_id)
        return {"accounts": accounts, "mcc": mcc_id.replace("-", "")}
    except Exception as e:
        logger.error("google/accounts error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/accounts/remove")
async def accounts_remove(request: Request):
    data = await request.json()
    nome = data.get("nome", "").strip()
    accounts = [p for p in _load_accounts() if p["nome"] != nome]
    ok = config_store.save("accounts", {"produtos": accounts}, ACCOUNTS_PATH)
    if not ok and config_store.IS_VERCEL:
        return JSONResponse({"message": "Adicione DATABASE_URL para persistir no Vercel", "error": True}, status_code=500)
    return {"message": f"Produto '{nome}' removido"}

@app.get("/api/google/debug")
async def google_debug():
    """Debug endpoint: testa credenciais Google Ads + mostra dados do banco por produto/canal."""
    _apply_env_overrides()
    import requests as _req
    dev_token  = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
    client_id  = os.getenv("GOOGLE_ADS_CLIENT_ID", "")
    client_sec = os.getenv("GOOGLE_ADS_CLIENT_SECRET", "")
    ref_token  = os.getenv("GOOGLE_ADS_REFRESH_TOKEN", "")
    mcc_id     = os.getenv("GOOGLE_ADS_CUSTOMER_ID", "").replace("-", "")
    result = {
        "dev_token_set": bool(dev_token),
        "dev_token_prefix": dev_token[:6] + "..." if dev_token else "",
        "client_id_set": bool(client_id),
        "client_secret_set": bool(client_sec),
        "refresh_token_set": bool(ref_token),
        "refresh_token_prefix": ref_token[:10] + "..." if ref_token else "",
        "mcc_id": mcc_id,
    }
    if not all([dev_token, client_id, client_sec, ref_token]):
        result["error"] = "Credenciais incompletas"
        return result
    # Step 1: get access token
    try:
        tr = _req.post("https://oauth2.googleapis.com/token", data={
            "client_id": client_id, "client_secret": client_sec,
            "refresh_token": ref_token, "grant_type": "refresh_token",
        }, timeout=15)
        result["token_status"] = tr.status_code
        if tr.status_code != 200:
            result["token_error"] = tr.text[:300]
            return result
        access_token = tr.json().get("access_token", "")
        result["access_token_obtained"] = bool(access_token)
    except Exception as e:
        result["token_exception"] = str(e)
        return result
    # Step 2: listAccessibleCustomers
    try:
        r = _req.get(
            "https://googleads.googleapis.com/v20/customers:listAccessibleCustomers",
            headers={"Authorization": f"Bearer {access_token}", "developer-token": dev_token},
            timeout=15,
        )
        result["accessible_customers_status"] = r.status_code
        result["accessible_customers_body"] = r.json() if r.headers.get("content-type","").startswith("application/json") else r.text[:300]
    except Exception as e:
        result["accessible_customers_exception"] = str(e)
    # Step 3: MCC searchStream
    if mcc_id:
        try:
            r2 = _req.post(
                f"https://googleads.googleapis.com/v20/customers/{mcc_id}/googleAds:searchStream",
                json={"query": "SELECT customer_client.id, customer_client.descriptive_name FROM customer_client WHERE customer_client.manager = FALSE AND customer_client.status = 'ENABLED'"},
                headers={"Authorization": f"Bearer {access_token}", "developer-token": dev_token, "login-customer-id": mcc_id},
                timeout=15,
            )
            result["mcc_search_status"] = r2.status_code
            result["mcc_search_body"] = r2.json() if r2.headers.get("content-type","").startswith("application/json") else r2.text[:500]
        except Exception as e:
            result["mcc_search_exception"] = str(e)
    # DB: spend por produto/canal no período
    try:
        import psycopg2
        conn = psycopg2.connect(get_db_url())
        with conn.cursor() as cur:
            cur.execute("""
                SELECT produto, channel, date::text,
                       ROUND(SUM(spend)::numeric,2) AS spend,
                       SUM(leads) AS leads
                FROM campaigns_daily
                WHERE date BETWEEN '2026-05-01' AND '2026-05-11'
                GROUP BY produto, channel, date
                ORDER BY produto, channel, date
            """)
            rows = cur.fetchall()
        conn.close()
        result["db_may_1_11"] = [
            {"produto": r[0], "channel": r[1], "date": r[2],
             "spend": float(r[3]), "leads": int(r[4])}
            for r in rows
        ]
    except Exception as e:
        result["db_error"] = str(e)
    return result


@app.get("/api/pipeline/debug")
async def pipeline_debug():
    """Mostra o estado das credenciais e contas sem executar o pipeline."""
    import sys as _sys
    accounts = _load_accounts()
    meta_token = os.getenv("META_ACCESS_TOKEN", "")
    meta_account = os.getenv("META_AD_ACCOUNT_ID", "")
    db_url = get_db_url()
    return {
        "python": _sys.version,
        "is_vercel": config_store.IS_VERCEL,
        "database_url_set": bool(db_url),
        "meta_token_set": bool(meta_token),
        "meta_token_prefix": meta_token[:10] + "..." if meta_token else "",
        "meta_account_global_env": meta_account,
        "produtos": [
            {
                "nome": p["nome"],
                "meta_ad_account_id": p.get("contas", {}).get("meta", {}).get("ad_account_id", ""),
            }
            for p in accounts
        ],
    }


@app.get("/api/debug/compare")
async def debug_compare(date_from: str = None, date_to: str = None):
    """
    Compara spend/impressions/clicks do banco vs. APIs diretas (Meta + Google) por produto.
    Uso: /api/debug/compare?date_from=2026-05-01&date_to=2026-05-11
    """
    _apply_env_overrides()
    from datetime import date as _date, timedelta as _td
    import requests as _req

    if not date_from:
        date_from = str(_date.today() - _td(days=6))
    if not date_to:
        date_to = str(_date.today())

    result = {"period": f"{date_from} → {date_to}", "db": {}, "meta_api": {}, "google_api": {}, "diff": {}}

    # ── 1. Banco ──────────────────────────────────────────────────────────────
    try:
        import psycopg2
        conn = psycopg2.connect(get_db_url())
        with conn.cursor() as cur:
            cur.execute("""
                SELECT produto, channel,
                       ROUND(SUM(spend)::numeric,2)  AS spend,
                       SUM(impressions)               AS impressions,
                       SUM(clicks)                    AS clicks,
                       SUM(leads)                     AS leads,
                       COUNT(DISTINCT campaign_utm)   AS campanhas
                FROM campaigns_daily
                WHERE date BETWEEN %s AND %s
                GROUP BY produto, channel
                ORDER BY produto, channel
            """, (date_from, date_to))
            for row in cur.fetchall():
                key = f"{row[0]}/{row[1]}"
                result["db"][key] = {
                    "produto": row[0], "channel": row[1],
                    "spend": float(row[2]), "impressions": int(row[3]),
                    "clicks": int(row[4]), "leads": int(row[5]),
                    "campanhas": int(row[6]),
                }
        conn.close()
    except Exception as e:
        result["db_error"] = str(e)

    # ── 2. Meta Ads API direta ────────────────────────────────────────────────
    accounts = _load_accounts()
    meta_token = os.getenv("META_ACCESS_TOKEN", "")
    if meta_token:
        for prod in accounts:
            nome = prod.get("nome", "")
            acc_id = prod.get("contas", {}).get("meta", {}).get("ad_account_id", "")
            if not acc_id:
                continue
            try:
                r = _req.get(
                    f"https://graph.facebook.com/v21.0/{acc_id}/insights",
                    params={
                        "access_token": meta_token,
                        "level": "account",
                        "fields": "spend,impressions,clicks,actions",
                        "time_range": json.dumps({"since": date_from, "until": date_to}),
                    }, timeout=20,
                )
                if r.ok:
                    data = r.json().get("data", [{}])
                    d = data[0] if data else {}
                    actions = d.get("actions", [])
                    leads = sum(int(float(a.get("value", 0))) for a in actions
                                if a.get("action_type") in ("lead","omni_lead","complete_registration"))
                    result["meta_api"][nome] = {
                        "account_id": acc_id,
                        "spend": float(d.get("spend", 0)),
                        "impressions": int(d.get("impressions", 0)),
                        "clicks": int(d.get("clicks", 0)),
                        "leads": leads,
                    }
                else:
                    result["meta_api"][nome] = {"account_id": acc_id, "error": r.json().get("error", {}).get("message", r.text[:200])}
            except Exception as e:
                result["meta_api"][nome] = {"account_id": acc_id, "error": str(e)}

    # ── 3. Google Ads API direta ──────────────────────────────────────────────
    dev_token    = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
    client_id    = os.getenv("GOOGLE_ADS_CLIENT_ID", "")
    client_sec   = os.getenv("GOOGLE_ADS_CLIENT_SECRET", "")
    ref_token    = os.getenv("GOOGLE_ADS_REFRESH_TOKEN", "")
    mcc_id       = os.getenv("GOOGLE_ADS_CUSTOMER_ID", "").replace("-", "")

    if dev_token and ref_token:
        try:
            tr = _req.post("https://oauth2.googleapis.com/token", data={
                "client_id": client_id, "client_secret": client_sec,
                "refresh_token": ref_token, "grant_type": "refresh_token",
            }, timeout=15)
            access_token = tr.json().get("access_token", "") if tr.ok else ""
        except Exception:
            access_token = ""

        if access_token:
            for prod in accounts:
                nome = prod.get("nome", "")
                cid  = prod.get("contas", {}).get("google", {}).get("customer_id", "").replace("-", "")
                if not cid:
                    continue
                try:
                    query = f"""
                        SELECT metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions
                        FROM campaign
                        WHERE segments.date BETWEEN '{date_from}' AND '{date_to}'
                          AND campaign.status != 'REMOVED'
                          AND metrics.cost_micros > 0
                    """
                    headers = {
                        "Authorization": f"Bearer {access_token}",
                        "developer-token": dev_token,
                    }
                    if mcc_id and mcc_id != cid:
                        headers["login-customer-id"] = mcc_id
                    r = _req.post(
                        f"https://googleads.googleapis.com/v20/customers/{cid}/googleAds:searchStream",
                        json={"query": query}, headers=headers, timeout=20,
                    )
                    if r.ok:
                        spend = impressions = clicks = conversions = 0
                        for batch in r.json():
                            for row in batch.get("results", []):
                                m = row.get("metrics", {})
                                spend       += int(m.get("costMicros", 0))
                                impressions += int(m.get("impressions", 0))
                                clicks      += int(m.get("clicks", 0))
                                conversions += float(m.get("conversions", 0))
                        result["google_api"][nome] = {
                            "customer_id": cid,
                            "spend": round(spend / 1_000_000, 2),
                            "impressions": impressions,
                            "clicks": clicks,
                            "conversions": int(conversions),
                        }
                    else:
                        result["google_api"][nome] = {"customer_id": cid, "error": r.text[:300]}
                except Exception as e:
                    result["google_api"][nome] = {"customer_id": cid, "error": str(e)}

    # ── 4. Diff banco vs API ──────────────────────────────────────────────────
    for nome, api_data in result["meta_api"].items():
        db_key = f"{nome}/meta"
        db_data = result["db"].get(db_key, {})
        api_spend = api_data.get("spend", 0)
        db_spend  = db_data.get("spend", 0)
        if isinstance(api_spend, (int, float)) and isinstance(db_spend, (int, float)):
            diff = round(api_spend - db_spend, 2)
            pct  = round(diff / api_spend * 100, 1) if api_spend else 0
            result["diff"][f"{nome}/meta"] = {
                "api_spend": api_spend, "db_spend": db_spend,
                "diff": diff, "diff_pct": f"{pct}%",
                "status": "✅ ok" if abs(pct) < 1 else "⚠️ divergente",
            }
    for nome, api_data in result["google_api"].items():
        db_key = f"{nome}/google"
        db_data = result["db"].get(db_key, {})
        api_spend = api_data.get("spend", 0)
        db_spend  = db_data.get("spend", 0)
        if isinstance(api_spend, (int, float)) and isinstance(db_spend, (int, float)):
            diff = round(api_spend - db_spend, 2)
            pct  = round(diff / api_spend * 100, 1) if api_spend else 0
            result["diff"][f"{nome}/google"] = {
                "api_spend": api_spend, "db_spend": db_spend,
                "diff": diff, "diff_pct": f"{pct}%",
                "status": "✅ ok" if abs(pct) < 1 else "⚠️ divergente",
            }

    return result


@app.get("/api/debug/dashboard")
async def debug_dashboard(days: int = 30):
    """Retorna o dict kpis + colunas da tabela como JSON para diagnóstico."""
    data = _get_dashboard_data(days)
    kpis = data.get("kpis", {})
    by_ch = data.get("by_channel", [])
    top = data.get("top_campaigns", [])
    return {
        "kpis_keys": list(kpis.keys()),
        "kpis": kpis,
        "by_channel_sample": by_ch[:2],
        "top_campaigns_sample": [
            {k: v for k, v in c.items() if k in ("campaign_utm","spend","mqls","sqls","deals_closed")}
            for c in top[:3]
        ],
        "by_channel_has_mqls": all("mqls" in r for r in by_ch) if by_ch else "no rows",
    }


@app.get("/api/debug/db")
async def debug_db():
    """Estado do banco: linhas por canal/produto e últimas datas."""
    _apply_env_overrides()
    try:
        import psycopg2
        conn = psycopg2.connect(get_db_url())
        with conn.cursor() as cur:
            cur.execute("""
                SELECT channel, produto,
                       COUNT(*) AS rows,
                       MIN(date) AS date_min,
                       MAX(date) AS date_max,
                       ROUND(SUM(spend)::numeric,0) AS total_spend,
                       SUM(leads) AS total_leads
                FROM campaigns_daily
                GROUP BY channel, produto
                ORDER BY total_spend DESC
            """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.execute("SELECT COUNT(*) FROM campaigns_daily")
            total = cur.fetchone()[0]
        conn.close()
        return {
            "total_rows": total,
            "by_channel_produto": rows,
            "empty": total == 0,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/debug/pull")
async def debug_pull(produto: str = "ixc-provedor", channel: str = "meta",
                     date_from: str = None, date_to: str = None):
    """
    Roda o pull de um produto/canal específico passo a passo e retorna output verbose.
    Uso: /api/debug/pull?produto=ixc-provedor&channel=meta&date_from=2026-05-01&date_to=2026-05-10
    """
    _apply_env_overrides()
    import traceback
    import requests as _req
    from datetime import date as _date, timedelta as _td

    if not date_from:
        date_from = str(_date.today() - _td(days=9))
    if not date_to:
        date_to = str(_date.today() - _td(days=1))

    result: dict = {
        "produto": produto, "channel": channel,
        "period": f"{date_from} → {date_to}",
        "steps": [],
    }

    def step(name: str, data):
        result["steps"].append({"step": name, "data": data})

    # Step 1: load accounts
    accounts = _load_accounts()
    prod_cfg = next((p for p in accounts if p["nome"] == produto), None)
    step("accounts_loaded", {"count": len(accounts), "names": [p["nome"] for p in accounts]})

    if not prod_cfg:
        step("ERROR", f"Produto '{produto}' não encontrado em accounts.json")
        return result

    account_config = prod_cfg.get("contas", {}).get(channel, {})
    step("account_config", account_config)

    # Step 2: check token
    token = os.getenv("META_ACCESS_TOKEN", "") if channel == "meta" else ""
    step("token_set", bool(token))
    step("token_prefix", token[:12] + "..." if token else "")

    if channel == "meta":
        ad_account_id = account_config.get("ad_account_id", "")
        step("ad_account_id", ad_account_id)

        if not token or not ad_account_id:
            step("ERROR", "META_ACCESS_TOKEN ou ad_account_id ausente")
            return result

        # Step 3: raw API call
        try:
            url = f"https://graph.facebook.com/v21.0/{ad_account_id}/insights"
            params = {
                "access_token": token,
                "level": "campaign",
                "fields": "campaign_name,campaign_id,spend,impressions,clicks,actions,date_start",
                "time_range": f'{{"since":"{date_from}","until":"{date_to}"}}',
                "time_increment": 1,
                "limit": 10,
                "filtering": '[{"field":"spend","operator":"GREATER_THAN","value":"0"}]',
            }
            resp = _req.get(url, params=params, timeout=30)
            step("api_status", resp.status_code)
            if resp.ok:
                data = resp.json()
                rows = data.get("data", [])
                paging = data.get("paging", {})
                step("api_rows_returned", len(rows))
                step("api_paging", {"next": bool(paging.get("next")), "cursors": bool(paging.get("cursors"))})
                if rows:
                    step("api_first_row", rows[0])
                    step("api_total_spend_sample", sum(float(r.get("spend", 0)) for r in rows))
                else:
                    step("api_empty", "Nenhuma linha retornada — sem campanhas com gasto no período?")
            else:
                step("api_error", resp.text[:500])
        except Exception as e:
            step("api_exception", traceback.format_exc())

        # Step 4: try instantiating puller and running full pull
        try:
            sys.path.insert(0, str(ROOT / "data_pipeline"))
            from meta_ads_pull import MetaAdsPuller
            from datetime import date as _d2
            puller = MetaAdsPuller(produto=produto, account_config=account_config)
            step("puller_auth", "OK")
            df = puller.run(_d2.fromisoformat(date_from), _d2.fromisoformat(date_to))
            step("puller_rows", len(df))
            if not df.empty:
                step("puller_spend_total", float(df["spend"].sum()))
                step("puller_sample", df.head(3).to_dict(orient="records"))
            else:
                step("puller_empty", "DataFrame vazio após normalize()")
        except EnvironmentError as e:
            step("puller_env_error", str(e))
        except Exception as e:
            step("puller_exception", traceback.format_exc())

    return result


# ── Slack Bot ─────────────────────────────────────────────────────────────────

_slack_sessions: dict = {}  # user_id → CampaignAgent (in-memory)

SLACK_SYSTEM_SUFFIX = """

FORMATO PARA SLACK:
- Use *negrito* (um asterisco) para destaque
- Use listas com • ou -
- Mantenha respostas concisas — máx 400 palavras
- Não use Markdown com ## ou ###, use *Título* em vez disso
- Use emojis com moderação para tornar a leitura mais fácil
"""


def _slack_post(channel: str, text: str, thread_ts: str = None) -> bool:
    token = os.getenv("SLACK_BOT_TOKEN", "")
    if not token:
        return False
    import requests as _req
    payload = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    try:
        r = _req.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload, timeout=10,
        )
        if not r.ok or not r.json().get("ok"):
            logger.error("slack post error: %s", r.text[:200])
        return r.ok
    except Exception as e:
        logger.error("slack post exception: %s", e)
        return False


def _verify_slack_sig(body: bytes, timestamp: str, signature: str) -> bool:
    import hmac, hashlib, time
    signing_secret = os.getenv("SLACK_SIGNING_SECRET", "")
    if not signing_secret:
        return True  # sem secret configurado, permite (dev mode)
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
        base = f"v0:{timestamp}:{body.decode('utf-8')}"
        expected = "v0=" + hmac.new(
            signing_secret.encode(), base.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


def _get_slack_agent(user_id: str):
    if user_id not in _slack_sessions:
        from agent import CampaignAgent
        agent = CampaignAgent()
        agent._system_prompt = agent._system_prompt + SLACK_SYSTEM_SUFFIX
        _slack_sessions[user_id] = agent
    return _slack_sessions[user_id]


def _md_to_mrkdwn(text: str) -> str:
    """Converte markdown básico para Slack mrkdwn."""
    import re
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)   # **bold** → *bold*
    text = re.sub(r"#{1,3} (.+)", r"*\1*", text)       # ## Header → *Header*
    text = re.sub(r"`{3}[a-z]*\n?", "```", text)        # normaliza code blocks
    return text


async def _process_slack_message(channel: str, text: str, user_id: str, thread_ts: str):
    try:
        _apply_env_overrides()
        agent = _get_slack_agent(user_id)
        active = _active_product
        reply = agent.chat(text, product_name=active)
        _slack_post(channel, _md_to_mrkdwn(reply), thread_ts)
    except Exception as e:
        logger.error("Slack processing error: %s", e, exc_info=True)
        _slack_post(channel, f"❌ Erro ao processar: {e}", thread_ts)


@app.post("/api/slack/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()

    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    signature = request.headers.get("X-Slack-Signature", "")
    if not _verify_slack_sig(body, timestamp, signature):
        logger.warning("Slack: assinatura inválida")
        return JSONResponse({"error": "invalid signature"}, status_code=403)

    data = json.loads(body)
    logger.info("Slack event recebido: type=%s", data.get("type"))

    # Handshake de verificação do Slack
    if data.get("type") == "url_verification":
        return {"challenge": data["challenge"]}

    event = data.get("event", {})
    event_type = event.get("type", "")
    logger.info("Slack event: %s bot_id=%s subtype=%s", event_type,
                event.get("bot_id"), event.get("subtype"))

    if event_type in ("message", "app_mention"):
        # Ignora mensagens do próprio bot e retries já processados
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return {"ok": True}
        if request.headers.get("X-Slack-Retry-Num"):
            return {"ok": True}

        channel   = event.get("channel", "")
        text      = event.get("text", "").strip()
        user_id   = event.get("user", "unknown")
        thread_ts = event.get("thread_ts") or event.get("ts", "")

        if not text or not channel:
            return {"ok": True}

        import re
        text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()
        if not text:
            return {"ok": True}

        logger.info("Slack mensagem de %s no canal %s: %s", user_id, channel, text[:80])

        # Confirma recebimento ao usuário imediatamente
        _slack_post(channel, "⏳ Processando...", thread_ts)

        # Processa e responde em background
        background_tasks.add_task(_process_slack_message, channel, text, user_id, thread_ts)

    return {"ok": True}


@app.get("/api/slack/test")
async def slack_test(channel: str = None):
    """Envia uma mensagem de teste ao Slack para verificar se o Bot Token está funcionando."""
    _apply_env_overrides()
    token = os.getenv("SLACK_BOT_TOKEN", "")
    if not token:
        return JSONResponse({"error": "SLACK_BOT_TOKEN não configurado"}, status_code=400)
    if not channel:
        return JSONResponse({"error": "Passe ?channel=CXXXXXXX (ID do canal)"}, status_code=400)
    ok = _slack_post(channel, "✅ Midas IA conectado! Bot Token funcionando.")
    return {"ok": ok, "channel": channel, "token_prefix": token[:12] + "..."}


@app.get("/api/smtp/test")
async def smtp_test(request: Request):
    """Testa a conexão SMTP e envia um e-mail de diagnóstico para o admin."""
    _apply_env_overrides()
    import os as _os
    cfg = {
        "SMTP_HOST":     _os.getenv("SMTP_HOST", ""),
        "SMTP_PORT":     _os.getenv("SMTP_PORT", "587"),
        "SMTP_USER":     _os.getenv("SMTP_USER", ""),
        "SMTP_PASSWORD": "***" if _os.getenv("SMTP_PASSWORD") else "(vazio)",
        "APP_URL":       _os.getenv("APP_URL", ""),
    }
    if not _mailer.is_configured():
        return JSONResponse({"ok": False, "erro": "SMTP não configurado", "vars": cfg})
    admin_email = request.state.user.get("email", "")
    ok, err = _mailer.send_welcome(admin_email, "Teste SMTP", "senha-de-teste-123")
    return JSONResponse({
        "ok": ok,
        "erro": err or None,
        "destino": admin_email,
        "vars": cfg,
    })

@app.get("/api/db/test")
async def db_test():
    """Diagnoses the database connection and shows what URL is being used."""
    _apply_env_overrides()
    db_url = get_db_url()
    if not db_url:
        detected = {k: bool(os.getenv(k)) for k in ["DATABASE_URL","POSTGRES_URL","POSTGRES_URL_NON_POOLING","POSTGRES_PRISMA_URL"]}
        return JSONResponse({"ok": False, "error": "Nenhuma variável de banco encontrada", "env_vars": detected}, status_code=400)
    # Mask password in URL for display
    import re
    safe_url = re.sub(r":([^:@]+)@", ":***@", db_url)
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            ver = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM ixc_config") if True else None
        conn.close()
        overrides = _load_env_overrides()
        return {
            "ok": True,
            "url": safe_url,
            "pg_version": ver[:40],
            "ixc_config_keys": list(overrides.keys()),
        }
    except Exception as e:
        return JSONResponse({"ok": False, "url": safe_url, "error": str(e)}, status_code=500)


@app.post("/api/db/clear-campaigns")
async def db_clear_campaigns():
    """Apaga todos os dados de campaigns_daily para reprocessamento limpo."""
    db_url = get_db_url()
    if not db_url:
        return JSONResponse({"error": "Banco não configurado"}, status_code=400)
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM campaigns_daily")
            deleted = cur.rowcount
        conn.commit()
        conn.close()
        return {"message": f"{deleted} linhas removidas de campaigns_daily"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/db/setup")
async def db_setup():
    """Create all tables and run migrations. Safe to run multiple times."""
    db_url = get_db_url()
    if not db_url:
        return JSONResponse({"message": "Banco não configurado (DATABASE_URL / POSTGRES_URL ausente)", "error": True}, status_code=400)
    schema_path = ROOT / "dashboard" / "schema.sql"
    if not schema_path.exists():
        return JSONResponse({"message": "schema.sql não encontrado", "error": True}, status_code=500)
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        sql = schema_path.read_text(encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(sql)
            # Migration: fix UNIQUE constraint to include channel and produto
            cur.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'campaigns_daily_date_campaign_utm_key'
                    ) THEN
                        ALTER TABLE campaigns_daily
                            DROP CONSTRAINT campaigns_daily_date_campaign_utm_key;
                        ALTER TABLE campaigns_daily
                            ADD CONSTRAINT campaigns_daily_date_channel_utm_produto_key
                            UNIQUE (date, channel, campaign_utm, produto);
                    END IF;
                END $$;
            """)
            # Migration: adiciona colunas de funil se não existirem
            cur.execute("""
                ALTER TABLE campaigns_daily
                    ADD COLUMN IF NOT EXISTS mqls         INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS sqls         INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS sals         INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS deals_closed INTEGER DEFAULT 0;
            """)
        conn.commit()
        conn.close()
        # Cria tabelas de autenticação (idempotente)
        try:
            _auth.setup_tables()
        except Exception as ae:
            logger.warning("auth.setup_tables falhou: %s", ae)
        return {"message": "Banco configurado: tabelas e migrações aplicadas com sucesso"}
    except Exception as e:
        logger.error("db/setup error: %s", e)
        return JSONResponse({"message": f"Erro ao configurar banco: {e}", "error": True}, status_code=500)


@app.post("/api/health-check")
async def health_check():
    lines = []
    checks = {
        "META_ACCESS_TOKEN": "Meta Ads",
        "GOOGLE_ADS_DEVELOPER_TOKEN": "Google Ads",
        "LINKEDIN_ACCESS_TOKEN": "LinkedIn Ads",
        "TIKTOK_ACCESS_TOKEN": "TikTok Ads",
        "HUBSPOT_ACCESS_TOKEN": "HubSpot (OAuth)",
        "HUBSPOT_API_KEY": "HubSpot (Private App)",
        "ANTHROPIC_API_KEY": "Claude API",
        "SLACK_WEBHOOK_URL": "Slack",
        "DATABASE_URL": "PostgreSQL",
    }
    for key, name in checks.items():
        val = os.getenv(key)
        status = "OK" if val else "NÃO CONFIGURADO"
        lines.append(f"  {status:20s} {name}")
    return {"output": "\n".join(lines)}


# ── OAuth: Connect & Callback ─────────────────────────────────────────────────

_CHANNEL_NAMES = {
    "meta": "Meta Ads",
    "google": "Google Ads",
    "linkedin": "LinkedIn Ads",
    "tiktok": "TikTok Ads",
    "hubspot": "HubSpot CRM",
    "gtm": "Google Tag Manager",
}

_CHANNEL_CLIENT_ID_VARS = {
    "meta": "META_CLIENT_ID",
    "google": "GOOGLE_ADS_CLIENT_ID",
    "linkedin": "LINKEDIN_CLIENT_ID",
    "tiktok": "TIKTOK_CLIENT_ID",
    "hubspot": "HUBSPOT_CLIENT_ID",
    "gtm": "GTM_CLIENT_ID",
}


def _base_url(request: Request) -> str:
    configured = os.getenv("APP_BASE_URL", "").rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


@app.get("/auth/connect/{channel}")
async def auth_connect(channel: str, request: Request):
    _apply_env_overrides()
    from oauth_flows import meta_auth_url, google_auth_url, linkedin_auth_url, tiktok_auth_url, hubspot_auth_url, gtm_auth_url

    client_id_var = _CHANNEL_CLIENT_ID_VARS.get(channel)
    if not client_id_var or not os.getenv(client_id_var):
        return templates.TemplateResponse("oauth_accounts.html", {
            "request": request, "channel": channel,
            "channel_name": _CHANNEL_NAMES.get(channel, channel),
            "accounts": [],
            "error": f"Configure {client_id_var} no .env antes de conectar.",
            "active_product": _active_product, "alert_count": _get_alert_count(),
        }, status_code=400)

    base = _base_url(request)
    builders = {
        "meta": meta_auth_url,
        "google": google_auth_url,
        "linkedin": linkedin_auth_url,
        "tiktok": tiktok_auth_url,
        "hubspot": hubspot_auth_url,
        "gtm": gtm_auth_url,
    }
    url, _ = builders[channel](base)
    return RedirectResponse(url)


@app.get("/auth/callback/{channel}")
async def auth_callback(channel: str, request: Request):
    _apply_env_overrides()
    from oauth_flows import (
        verify_state,
        meta_exchange, meta_list_accounts,
        google_exchange, google_list_accounts,
        linkedin_exchange, linkedin_list_accounts,
        tiktok_exchange, tiktok_list_accounts,
        hubspot_exchange, hubspot_get_portal_info,
        gtm_exchange, gtm_list_containers,
    )

    params = dict(request.query_params)
    state = params.get("state", "")
    code = params.get("code", "")
    error = params.get("error", params.get("error_description", ""))
    channel_name = _CHANNEL_NAMES.get(channel, channel)

    if error:
        return templates.TemplateResponse("oauth_accounts.html", {
            "request": request, "channel": channel, "channel_name": channel_name,
            "accounts": [], "error": error,
            "active_product": _active_product, "alert_count": _get_alert_count(),
        })

    if not verify_state(state, channel):
        return templates.TemplateResponse("oauth_accounts.html", {
            "request": request, "channel": channel, "channel_name": channel_name,
            "accounts": [], "error": "State inválido. Tente conectar novamente.",
            "active_product": _active_product, "alert_count": _get_alert_count(),
        }, status_code=400)

    base = _base_url(request)
    try:
        if channel == "meta":
            token_data = await meta_exchange(code, base)
            access_token = token_data["access_token"]
            _save_env_token("META_ACCESS_TOKEN", access_token)
            accounts = await meta_list_accounts(access_token)
            accounts = [{"id": a["id"], "name": a.get("name", a["id"])} for a in accounts]

        elif channel == "google":
            token_data = await google_exchange(code, base)
            refresh_token = token_data.get("refresh_token", "")
            if refresh_token:
                _save_env_token("GOOGLE_ADS_REFRESH_TOKEN", refresh_token)
            # Try MCC listing first, fallback to listAccessibleCustomers
            accounts = []
            developer_token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
            client_id       = os.getenv("GOOGLE_ADS_CLIENT_ID", "")
            client_secret   = os.getenv("GOOGLE_ADS_CLIENT_SECRET", "")
            mcc_id          = os.getenv("GOOGLE_ADS_CUSTOMER_ID", "")
            rt = refresh_token or os.getenv("GOOGLE_ADS_REFRESH_TOKEN", "")
            if developer_token and rt and mcc_id:
                try:
                    from google_ads_pull import list_mcc_accounts
                    raw = list_mcc_accounts(developer_token, client_id, client_secret, rt, mcc_id)
                    accounts = [{"id": a["id"], "name": a.get("name", a["id"])} for a in raw]
                except Exception as gexc:
                    logger.warning("MCC listing fallback: %s", gexc)
            if not accounts and rt:
                try:
                    raw = await google_list_accounts(rt)
                    accounts = [{"id": a["id"], "name": a.get("name", a["id"])} for a in raw]
                except Exception:
                    pass

        elif channel == "linkedin":
            token_data = await linkedin_exchange(code, base)
            access_token = token_data.get("access_token", "")
            _save_env_token("LINKEDIN_ACCESS_TOKEN", access_token)
            accounts = await linkedin_list_accounts(access_token)

        elif channel == "tiktok":
            token_data = await tiktok_exchange(code, base)
            access_token = token_data.get("access_token", "")
            _save_env_token("TIKTOK_ACCESS_TOKEN", access_token)
            accounts = await tiktok_list_accounts(access_token)

        elif channel == "gtm":
            token_data    = await gtm_exchange(code, base)
            refresh_token = token_data.get("refresh_token", "")
            if refresh_token:
                _save_env_token("GTM_OAUTH_REFRESH_TOKEN", refresh_token)
            containers = await gtm_list_containers(refresh_token) if refresh_token else []
            # Show each container as selectable account; name includes public ID
            accounts = [
                {"id": c["id"], "name": f"{c['name']} ({c['public_id']})"}
                for c in containers
            ]

        elif channel == "hubspot":
            token_data = await hubspot_exchange(code, base)
            access_token = token_data.get("access_token", "")
            refresh_token = token_data.get("refresh_token", "")
            if access_token:
                _save_env_token("HUBSPOT_ACCESS_TOKEN", access_token)
            if refresh_token:
                _save_env_token("HUBSPOT_REFRESH_TOKEN", refresh_token)
            portal_info = await hubspot_get_portal_info(access_token) if access_token else {}
            hub_id = str(portal_info.get("hub_id", ""))
            hub_domain = portal_info.get("hub_domain", hub_id)
            if hub_id:
                _save_env_token("HUBSPOT_PORTAL_ID", hub_id)
            accounts = [{"id": hub_id, "name": hub_domain or hub_id}] if hub_id else []

        else:
            raise ValueError(f"Canal desconhecido: {channel}")

    except Exception as exc:
        logger.error("OAuth callback error [%s]: %s", channel, exc)
        return templates.TemplateResponse("oauth_accounts.html", {
            "request": request, "channel": channel, "channel_name": channel_name,
            "accounts": [], "error": str(exc),
            "active_product": _active_product, "alert_count": _get_alert_count(),
        })

    return templates.TemplateResponse("oauth_accounts.html", {
        "request": request, "channel": channel, "channel_name": channel_name,
        "accounts": accounts, "error": None,
        "active_product": _active_product, "alert_count": _get_alert_count(),
    })


@app.post("/auth/select")
async def auth_select(request: Request):
    form = await request.form()
    channel = form.get("channel", "")
    account_id = str(form.get("account_id", ""))
    account_name = str(form.get("account_name", account_id))

    id_vars = {
        "meta": "META_AD_ACCOUNT_ID",
        "google": "GOOGLE_ADS_CUSTOMER_ID",
        "linkedin": "LINKEDIN_AD_ACCOUNT_ID",
        "tiktok": "TIKTOK_AD_ACCOUNT_ID",
        "hubspot": "HUBSPOT_PORTAL_ID",
        "gtm": "GTM_CONTAINER_ID",
    }
    env_key = id_vars.get(channel)
    if env_key and account_id:
        _save_env_token(env_key, account_id)

    logger.info("OAuth account selected [%s]: %s (%s)", channel, account_id, account_name)
    return RedirectResponse("/conexoes?oauth=ok&channel=" + channel, status_code=303)


# ── API: GA4 ──────────────────────────────────────────────────────────────────

@app.get("/api/ga4/report")
async def ga4_report(request: Request):
    try:
        sys.path.insert(0, str(ROOT / "data_pipeline"))
        from ga4_pull import fetch_report
        params = request.query_params
        days = int(params.get("days", 30))
        produto = params.get("produto", "").strip() or None

        # Use per-product GA4 property if configured
        property_id = None
        if produto:
            accounts = _load_accounts()
            p = next((a for a in accounts if a["nome"] == produto), None)
            if p:
                property_id = p.get("rastreamento", {}).get("ga4_property_id") or None

        report = fetch_report(days=days, produto=produto, property_id=property_id)
        return report
    except EnvironmentError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        logger.error("GA4 report error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/ga4/event")
async def ga4_event(request: Request):
    try:
        sys.path.insert(0, str(ROOT / "data_pipeline"))
        from ga4_pull import send_event
        d = await request.json()
        ok = send_event(d.get("event_name", ""), d.get("params", {}), d.get("client_id", "server"))
        return {"ok": ok}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── API: GTM ──────────────────────────────────────────────────────────────────

@app.get("/api/gtm/debug-creds")
async def gtm_debug_creds():
    """Mostra quais credenciais GTM estão carregadas (valores mascarados)."""
    _apply_env_overrides()
    def mask(v: str) -> str:
        if not v:
            return "(vazio — 0 chars)"
        preview = (v[:6] + "..." + v[-4:]) if len(v) > 10 else v[:3] + "***"
        return f"{preview} ({len(v)} chars)"
    return {
        "GTM_CLIENT_ID":            mask(os.getenv("GTM_CLIENT_ID", "")),
        "GTM_CLIENT_SECRET":        mask(os.getenv("GTM_CLIENT_SECRET", "")),
        "GTM_OAUTH_REFRESH_TOKEN":  mask(os.getenv("GTM_OAUTH_REFRESH_TOKEN", "")),
        "GTM_ACCOUNT_ID":           os.getenv("GTM_ACCOUNT_ID", "(vazio)"),
        "GTM_PUBLIC_ID":            os.getenv("GTM_PUBLIC_ID", "(vazio)"),
        "has_db":                   bool(config_store.get_db_url()),
    }


@app.get("/api/gtm/verify")
async def gtm_verify():
    _apply_env_overrides()
    try:
        sys.path.insert(0, str(ROOT / "tracking"))
        from gtm_integration import verify_setup
        result = verify_setup()
        return result
    except EnvironmentError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        logger.error("GTM verify error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/gtm/snippet")
async def gtm_snippet():
    _apply_env_overrides()
    try:
        sys.path.insert(0, str(ROOT / "tracking"))
        from gtm_integration import get_snippet
        snippet = get_snippet()
        return {"snippet": snippet}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/rastreamento/deploy")
async def rastreamento_deploy(request: Request):
    """Deploys all tracking tags, triggers and variables to GTM via API, then publishes."""
    _apply_env_overrides()
    data = await request.json()
    main_url     = data.get("main_url", "").strip()
    thankyou_url = data.get("thankyou_url", "").strip()
    workspace_id = data.get("workspace_id", "1")
    publish      = data.get("publish", True)

    env = _get_env()
    ga4_id       = env.get("GA4_MEASUREMENT_ID", "")
    meta_pixel   = env.get("META_PIXEL_ID", "")
    li_partner   = env.get("LINKEDIN_PARTNER_ID", "")
    tiktok_pixel = env.get("TIKTOK_PIXEL_ID", "")
    hs_portal    = env.get("HUBSPOT_PORTAL_ID", "")

    if not thankyou_url:
        return JSONResponse({"error": "Informe a URL da página de obrigado.", "ok": False}, status_code=400)

    try:
        sys.path.insert(0, str(ROOT / "tracking"))
        from gtm_integration import deploy_all_tags, publish_workspace
        result = deploy_all_tags(
            ga4_id=ga4_id, meta_pixel=meta_pixel, li_partner=li_partner,
            tiktok_pixel=tiktok_pixel, hs_portal_id=hs_portal,
            workspace_id=workspace_id,
            main_url=main_url, thankyou_url=thankyou_url,
        )
        if publish:
            pub = publish_workspace(workspace_id=workspace_id, note="Deploy via IXCTraffic")
            result["published"] = True
            result["version_id"] = pub.get("version_id")
            result["log"].append(f"✓ Container GTM publicado — versão {pub.get('version_id')}")
        return result
    except EnvironmentError as e:
        return JSONResponse({"error": str(e), "ok": False}, status_code=400)
    except Exception as e:
        logger.error("GTM deploy error: %s", e)
        return JSONResponse({"error": str(e), "ok": False}, status_code=500)


@app.get("/api/rastreamento/mega-snippet")
async def rastreamento_mega_snippet(product: str = ""):
    _apply_env_overrides()
    env      = _get_env()
    accounts = _load_accounts()
    produto  = next((p for p in accounts if p["nome"] == product), None)
    r        = produto.get("rastreamento", {}) if produto else {}
    contas   = produto.get("contas", {})       if produto else {}
    from gtm_integration import generate_mega_snippet
    snippet = generate_mega_snippet(
        gtm_public_id = r.get("gtm_public_id") or env.get("GTM_PUBLIC_ID", ""),
        ga4_id        = r.get("ga4_measurement_id") or env.get("GA4_MEASUREMENT_ID", ""),
        meta_pixel    = contas.get("meta",{}).get("pixel_id") or env.get("META_PIXEL_ID", ""),
        li_partner    = contas.get("linkedin",{}).get("partner_id") or env.get("LINKEDIN_PARTNER_ID", ""),
        tiktok_pixel  = contas.get("tiktok",{}).get("pixel_id") or env.get("TIKTOK_PIXEL_ID", ""),
        hs_portal_id  = env.get("HUBSPOT_PORTAL_ID", ""),
    )
    return {"snippet": snippet}


@app.post("/api/gtm/publish")
async def gtm_publish(request: Request):
    try:
        sys.path.insert(0, str(ROOT / "tracking"))
        from gtm_integration import publish_workspace
        d = await request.json()
        note = d.get("note", "Publicado via IXCTraffic")
        result = publish_workspace(note=note)
        return result
    except EnvironmentError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        logger.error("GTM publish error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


def _save_env_token(key: str, value: str):
    os.environ[key] = value
    # Always persist to ixc_config so it survives across serverless requests
    try:
        existing = _load_env_overrides()
        existing[key] = value
        config_store.save("env_overrides", existing, None)
    except Exception as exc:
        logger.warning("_save_env_token DB persist failed [%s]: %s", key, exc)
    if not config_store.IS_VERCEL:
        try:
            if not ENV_PATH.exists():
                ENV_PATH.write_text("")
            set_key(str(ENV_PATH), key, value)
            load_dotenv(ENV_PATH, override=True)
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════════
# AUTENTICAÇÃO — Login / Logout / Setup
# ════════════════════════════════════════════════════════════════════════════

from fastapi import Form as _Form

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    next_url = request.query_params.get("next", "/")
    return templates.TemplateResponse("login.html", {
        "request": request, "next": next_url, "error": None, "email": None,
    })

@app.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    email:    str = _Form(...),
    password: str = _Form(...),
    next:     str = _Form("/"),
):
    token = _auth.login(email, password)
    if not token:
        return templates.TemplateResponse("login.html", {
            "request": request, "next": next,
            "error": "E-mail ou senha incorretos. Tente novamente.",
            "email": email,
        }, status_code=401)
    response = RedirectResponse(next if next.startswith("/") else "/", status_code=302)
    _is_https = (os.getenv("APP_BASE_URL", "").startswith("https://")
                 or request.url.scheme == "https")
    response.set_cookie(
        "ixc_session", token,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="lax",
        secure=_is_https,
    )
    return response

@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("ixc_session")
    if token:
        _auth.logout(token)
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("ixc_session")
    return response

@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if not _auth.needs_setup():
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("setup.html", {
        "request": request, "error": None, "name": None, "email": None,
    })

@app.post("/setup", response_class=HTMLResponse)
async def setup_post(
    request:  Request,
    name:     str = _Form(...),
    email:    str = _Form(...),
    password: str = _Form(...),
    confirm:  str = _Form(...),
):
    if not _auth.needs_setup():
        return RedirectResponse("/", status_code=302)
    err = None
    if len(password) < 8:
        err = "A senha deve ter no mínimo 8 caracteres."
    elif password != confirm:
        err = "As senhas não coincidem."
    if err:
        return templates.TemplateResponse("setup.html", {
            "request": request, "error": err, "name": name, "email": email,
        }, status_code=400)
    ok = _auth.create_admin(email, name, password)
    if not ok:
        return templates.TemplateResponse("setup.html", {
            "request": request,
            "error": "Não foi possível criar a conta. Verifique a conexão com o banco.",
            "name": name, "email": email,
        }, status_code=500)
    token = _auth.login(email, password)
    response = RedirectResponse("/", status_code=302)
    _is_https = (os.getenv("APP_BASE_URL", "").startswith("https://")
                 or request.url.scheme == "https")
    response.set_cookie("ixc_session", token, max_age=60*60*24*30,
                        httponly=True, samesite="lax", secure=_is_https)
    return response


# ════════════════════════════════════════════════════════════════════════════
# ADMIN — Gestão de Usuários
# ════════════════════════════════════════════════════════════════════════════

@app.get("/admin/usuarios", response_class=HTMLResponse)
async def admin_usuarios(request: Request):
    users = _auth.list_users()
    return templates.TemplateResponse("admin_usuarios.html", {
        "request": request, "page": "admin_usuarios",
        "active_product": _active_product, "alert_count": _get_alert_count(),
        "users": users,
    })

@app.post("/admin/usuarios/add")
async def admin_add_user(request: Request):
    data = await request.json()
    email    = data.get("email", "").strip()
    name     = data.get("name", "").strip()
    password = data.get("password", "")
    role     = data.get("role", "user")
    if not email or not name or not password:
        return JSONResponse({"ok": False, "error": "Preencha todos os campos."})
    if len(password) < 8:
        return JSONResponse({"ok": False, "error": "Senha mínima de 8 caracteres."})
    if role not in ("admin", "user"):
        role = "user"
    ok = _auth.add_user(email, name, password, role)
    if not ok:
        return JSONResponse({"ok": False, "error": "E-mail já cadastrado ou erro no banco."})

    # Envia e-mail de boas-vindas com as credenciais
    email_sent, email_err = _mailer.send_welcome(email, name, password)
    return JSONResponse({
        "ok": True,
        "email_sent": email_sent,
        "email_warn": None if email_sent else (
            "Usuário criado, mas o e-mail não foi enviado. "
            "Verifique as configurações SMTP em Conexões."
            if _mailer.is_configured() else
            "Usuário criado. Configure o SMTP em Conexões para enviar e-mails automáticos."
        ),
    })

@app.post("/admin/usuarios/toggle")
async def admin_toggle_user(request: Request):
    data = await request.json()
    uid  = int(data.get("user_id", 0))
    current = request.state.user
    if uid == current["id"]:
        return JSONResponse({"ok": False, "error": "Você não pode desativar a própria conta."})
    ok = _auth.toggle_active(uid)
    return JSONResponse({"ok": ok})

@app.post("/admin/usuarios/remove")
async def admin_remove_user(request: Request):
    data = await request.json()
    uid  = int(data.get("user_id", 0))
    current = request.state.user
    if uid == current["id"]:
        return JSONResponse({"ok": False, "error": "Você não pode remover a própria conta."})
    ok = _auth.remove_user(uid)
    return JSONResponse({"ok": ok})

@app.post("/admin/usuarios/reset-password")
async def admin_reset_password(request: Request):
    data = await request.json()
    uid  = int(data.get("user_id", 0))
    pw   = data.get("password", "")
    if len(pw) < 8:
        return JSONResponse({"ok": False, "error": "Senha mínima de 8 caracteres."})
    ok = _auth.change_password(uid, pw)
    return JSONResponse({"ok": ok})


# ════════════════════════════════════════════════════════════════════════════
# MINHA CONTA — Alterar própria senha
# ════════════════════════════════════════════════════════════════════════════

@app.get("/minha-conta/senha", response_class=HTMLResponse)
async def minha_conta_senha(request: Request):
    return templates.TemplateResponse("trocar_senha.html", {
        "request": request, "page": "minha_conta",
        "active_product": _active_product, "alert_count": _get_alert_count(),
        "success": False, "error": None,
    })

@app.post("/minha-conta/senha", response_class=HTMLResponse)
async def minha_conta_senha_post(
    request:          Request,
    current_password: str = _Form(...),
    new_password:     str = _Form(...),
    confirm_password: str = _Form(...),
):
    user = request.state.user
    error = None

    if len(new_password) < 8:
        error = "A nova senha deve ter no mínimo 8 caracteres."
    elif new_password != confirm_password:
        error = "A nova senha e a confirmação não coincidem."

    if not error:
        ok, msg = _auth.change_own_password(user["id"], current_password, new_password)
        if not ok:
            error = msg

    return templates.TemplateResponse("trocar_senha.html", {
        "request": request, "page": "minha_conta",
        "active_product": _active_product, "alert_count": _get_alert_count(),
        "success": error is None,
        "error": error,
    })
