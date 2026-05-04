import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

# Ensure web/ is on sys.path before importing local modules
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv, set_key

import config_store

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

ENV_PATH = ROOT / ".env"
THRESHOLDS_PATH = ROOT / "config" / "alert_thresholds.json"
TAXONOMY_PATH   = ROOT / "config" / "utm_taxonomy.json"
PRODUCTS_PATH   = ROOT / "config" / "products.json"
ACCOUNTS_PATH   = ROOT / "config" / "accounts.json"
LTV_PATH        = ROOT / "config" / "ltv_metrics.json"

_agent_instance = None
_active_product = None


def _get_env() -> dict:
    keys = [
        "META_CLIENT_ID","META_CLIENT_SECRET","META_ACCESS_TOKEN","META_AD_ACCOUNT_ID","META_PIXEL_ID",
        "GOOGLE_ADS_CLIENT_ID","GOOGLE_ADS_CLIENT_SECRET",
        "GOOGLE_ADS_DEVELOPER_TOKEN","GOOGLE_ADS_CUSTOMER_ID","GOOGLE_ADS_REFRESH_TOKEN",
        "LINKEDIN_CLIENT_ID","LINKEDIN_CLIENT_SECRET",
        "LINKEDIN_ACCESS_TOKEN","LINKEDIN_AD_ACCOUNT_ID","LINKEDIN_PARTNER_ID",
        "TIKTOK_CLIENT_ID","TIKTOK_CLIENT_SECRET",
        "TIKTOK_ACCESS_TOKEN","TIKTOK_AD_ACCOUNT_ID","TIKTOK_PIXEL_ID",
        "HUBSPOT_API_KEY","ANTHROPIC_API_KEY","SLACK_WEBHOOK_URL","DATABASE_URL","APP_BASE_URL",
        "GA4_MEASUREMENT_ID","GA4_PROPERTY_ID","GA4_API_SECRET","GA4_CREDENTIALS_JSON",
        "GTM_ACCOUNT_ID","GTM_CONTAINER_ID","GTM_PUBLIC_ID","GTM_CREDENTIALS_JSON",
    ]
    return {k: os.getenv(k, "") for k in keys}


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
    data = config_store.load("products", PRODUCTS_PATH, {"produtos": []})
    if isinstance(data, dict):
        return [p["nome"] for p in data.get("produtos", [])]
    return []


def _get_dashboard_data(days: int = 30, produto: str = ""):
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))

        # Build optional produto filter fragment
        p_filter = "AND produto = %s" if produto else ""

        with conn.cursor() as cur:
            params_base = (days, produto) if produto else (days,)

            cur.execute(f"""
                SELECT COALESCE(SUM(spend),0), COALESCE(SUM(leads),0),
                       COALESCE(SUM(revenue),0), COALESCE(COUNT(DISTINCT channel),0),
                       COALESCE(AVG(roas),0)
                FROM campaigns_daily WHERE date >= CURRENT_DATE - %s {p_filter}
            """, params_base)
            spend, leads, revenue, channels, roas = cur.fetchone()

            cur.execute(f"""
                SELECT channel, ROUND(SUM(spend)::numeric,2) AS spend, SUM(leads) AS leads,
                       ROUND(AVG(roas)::numeric,2) AS roas,
                       ROUND(AVG(NULLIF(cpl,0))::numeric,2) AS cpl
                FROM campaigns_daily WHERE date >= CURRENT_DATE - %s {p_filter}
                GROUP BY channel ORDER BY spend DESC
            """, params_base)
            cols = [d[0] for d in cur.description]
            by_channel = [dict(zip(cols, r)) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT campaign_utm, channel, produto,
                       ROUND(SUM(spend)::numeric,2) AS spend, SUM(leads) AS leads,
                       ROUND(SUM(revenue)::numeric,2) AS revenue,
                       ROUND(AVG(roas)::numeric,2) AS roas,
                       ROUND(AVG(NULLIF(cpl,0))::numeric,2) AS cpl
                FROM campaigns_daily WHERE date >= CURRENT_DATE - %s {p_filter}
                GROUP BY campaign_utm, channel, produto
                ORDER BY roas DESC LIMIT 10
            """, params_base)
            cols = [d[0] for d in cur.description]
            top_campaigns = [dict(zip(cols, r)) for r in cur.fetchall()]

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
        return {
            "kpis": {"spend": float(spend), "leads": int(leads), "revenue": float(revenue),
                     "channels": int(channels), "roas": float(roas), "cpl": cpl,
                     "deals": 0, "roas_meta": 4.0},
            "by_channel": by_channel, "top_campaigns": top_campaigns, "alerts": alerts,
        }
    except Exception as e:
        logger.warning("DB indisponível: %s", e)
        return {
            "kpis": {"spend":0,"leads":0,"revenue":0,"channels":0,"roas":0,"cpl":0,"deals":0,"roas_meta":4.0},
            "by_channel": [], "top_campaigns": [], "alerts": [],
        }


def _get_alert_count():
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
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
    params = request.query_params
    try:
        days = int(params.get("days", 30))
        days = max(7, min(days, 365))
    except ValueError:
        days = 30
    produto = params.get("produto", "").strip()

    data = _get_dashboard_data(days, produto)
    ltv_all = _load_ltv()

    # Filter LTV metrics to selected product
    if produto and produto in ltv_all.get("por_produto", {}):
        p = ltv_all["por_produto"][produto]
        ltv_filtered = {
            "global": {
                "ltv_medio": p["ltv_medio"],
                "mrr": p["mrr"],
                "arr": p["arr"],
                "total_clientes": p["total_clientes"],
                "ativos": p["ativos"],
                "churned": p["churned"],
                "churn_rate_pct": p["churn_rate_pct"],
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
        "produtos": produtos,
        "active_product": _active_product, "alert_count": _get_alert_count(),
        "ltv": ltv_filtered,
        **data,
    })

@app.get("/conexoes", response_class=HTMLResponse)
async def conexoes(request: Request):
    return templates.TemplateResponse("conexoes.html", {
        "request": request, "page": "conexoes",
        "env": _get_env(), "accounts": _load_accounts(),
        "active_product": _active_product, "alert_count": _get_alert_count(),
    })

@app.get("/campanhas", response_class=HTMLResponse)
async def campanhas(request: Request):
    data = _get_dashboard_data(30)
    all_campaigns = data["top_campaigns"]
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        with conn.cursor() as cur:
            cur.execute("""
                SELECT campaign_utm, channel, produto,
                       ROUND(SUM(spend)::numeric,2) AS spend, SUM(leads) AS leads,
                       ROUND(SUM(revenue)::numeric,2) AS revenue,
                       ROUND(AVG(roas)::numeric,2) AS roas,
                       ROUND(AVG(NULLIF(cpl,0))::numeric,2) AS cpl,
                       ROUND(AVG(NULLIF(cpc,0))::numeric,4) AS cpc,
                       ROUND(AVG(NULLIF(ctr,0))::numeric,4) AS ctr
                FROM campaigns_daily GROUP BY campaign_utm, channel, produto ORDER BY spend DESC
            """)
            cols = [d[0] for d in cur.description]
            all_campaigns = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
    except Exception:
        pass
    return templates.TemplateResponse("campanhas.html", {
        "request": request, "page": "campanhas",
        "campaigns": all_campaigns, "produtos": _list_products(),
        "active_product": _active_product, "alert_count": _get_alert_count(),
    })

@app.get("/alertas", response_class=HTMLResponse)
async def alertas(request: Request):
    alerts, stats = [], {"total":0,"critico":0,"atencao":0,"ok":0}
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
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
        conn.close()
    except Exception:
        pass
    return templates.TemplateResponse("alertas.html", {
        "request": request, "page": "alertas",
        "alerts": alerts, "stats": stats, "thresholds": _load_thresholds(),
        "active_product": _active_product, "alert_count": stats.get("critico",0),
    })

@app.get("/agente", response_class=HTMLResponse)
async def agente(request: Request):
    return templates.TemplateResponse("agente.html", {
        "request": request, "page": "agente",
        "produtos": _list_products(), "active_product": _active_product, "alert_count": _get_alert_count(),
    })

@app.get("/utm", response_class=HTMLResponse)
async def utm_page(request: Request):
    return templates.TemplateResponse("utm.html", {
        "request": request, "page": "utm",
        "taxonomy": _load_taxonomy(), "active_product": _active_product, "alert_count": _get_alert_count(),
    })


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
    suffix = " (adicione também no painel do Vercel para persistir)" if config_store.IS_VERCEL else " no .env"
    return {"message": f"{saved} variável(is) salva(s){suffix}"}


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
    d = await request.json()
    message = d.get("message","")
    try:
        from agent import CampaignAgent
        from briefing import _parse_objetivo, _parse_canal, _parse_budget
        from campaign_creator import create_campaign
        from optimizer import diagnose, optimize, forecast

        if _agent_instance is None:
            _agent_instance = CampaignAgent()

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
async def pipeline_run():
    try:
        from load_database import run_pipeline
        total = run_pipeline()
        return {"message": f"Pipeline concluído: {total} campanhas carregadas"}
    except Exception as e:
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
    if existing:
        existing["contas"] = data.get("contas", existing.get("contas", {}))
    else:
        accounts.append({"nome": nome, "contas": data.get("contas", {
            "meta": {"ad_account_id": ""},
            "google": {"customer_id": ""},
            "linkedin": {"ad_account_id": ""},
            "tiktok": {"ad_account_id": ""},
        })})
    config_store.save("accounts", {"produtos": accounts}, ACCOUNTS_PATH)
    return {"message": f"Contas de '{nome}' salvas"}


@app.post("/api/accounts/remove")
async def accounts_remove(request: Request):
    data = await request.json()
    nome = data.get("nome", "").strip()
    accounts = [p for p in _load_accounts() if p["nome"] != nome]
    config_store.save("accounts", {"produtos": accounts}, ACCOUNTS_PATH)
    return {"message": f"Produto '{nome}' removido"}

@app.post("/api/health-check")
async def health_check():
    lines = []
    checks = {
        "META_ACCESS_TOKEN": "Meta Ads",
        "GOOGLE_ADS_DEVELOPER_TOKEN": "Google Ads",
        "LINKEDIN_ACCESS_TOKEN": "LinkedIn Ads",
        "TIKTOK_ACCESS_TOKEN": "TikTok Ads",
        "HUBSPOT_API_KEY": "HubSpot",
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
}

_CHANNEL_CLIENT_ID_VARS = {
    "meta": "META_CLIENT_ID",
    "google": "GOOGLE_ADS_CLIENT_ID",
    "linkedin": "LINKEDIN_CLIENT_ID",
    "tiktok": "TIKTOK_CLIENT_ID",
}


def _base_url(request: Request) -> str:
    configured = os.getenv("APP_BASE_URL", "").rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


@app.get("/auth/connect/{channel}")
async def auth_connect(channel: str, request: Request):
    from oauth_flows import meta_auth_url, google_auth_url, linkedin_auth_url, tiktok_auth_url

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
    }
    url, _ = builders[channel](base)
    return RedirectResponse(url)


@app.get("/auth/callback/{channel}")
async def auth_callback(channel: str, request: Request):
    from oauth_flows import (
        verify_state,
        meta_exchange, meta_list_accounts,
        google_exchange, google_list_accounts,
        linkedin_exchange, linkedin_list_accounts,
        tiktok_exchange, tiktok_list_accounts,
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
            accounts = await google_list_accounts(refresh_token) if refresh_token else []

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
        report = fetch_report(days=days, produto=produto)
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

@app.get("/api/gtm/verify")
async def gtm_verify():
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
    try:
        sys.path.insert(0, str(ROOT / "tracking"))
        from gtm_integration import get_snippet
        snippet = get_snippet()
        return {"snippet": snippet}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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
    if not config_store.IS_VERCEL:
        try:
            if not ENV_PATH.exists():
                ENV_PATH.write_text("")
            set_key(str(ENV_PATH), key, value)
            load_dotenv(ENV_PATH, override=True)
        except Exception:
            pass
