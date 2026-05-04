import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv, set_key

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

_agent_instance = None
_active_product = None


def _get_env() -> dict:
    keys = [
        "META_ACCESS_TOKEN","META_AD_ACCOUNT_ID","META_PIXEL_ID",
        "GOOGLE_ADS_DEVELOPER_TOKEN","GOOGLE_ADS_CUSTOMER_ID","GOOGLE_ADS_REFRESH_TOKEN",
        "GOOGLE_ADS_CLIENT_ID","GOOGLE_ADS_CLIENT_SECRET",
        "LINKEDIN_ACCESS_TOKEN","LINKEDIN_AD_ACCOUNT_ID","LINKEDIN_PARTNER_ID",
        "TIKTOK_ACCESS_TOKEN","TIKTOK_AD_ACCOUNT_ID","TIKTOK_PIXEL_ID",
        "HUBSPOT_API_KEY","ANTHROPIC_API_KEY","SLACK_WEBHOOK_URL","DATABASE_URL",
    ]
    return {k: os.getenv(k, "") for k in keys}


def _load_accounts() -> list:
    if not ACCOUNTS_PATH.exists():
        return []
    with open(ACCOUNTS_PATH, encoding="utf-8") as f:
        return json.load(f).get("produtos", [])


def _load_taxonomy():
    with open(TAXONOMY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _load_thresholds():
    with open(THRESHOLDS_PATH, encoding="utf-8") as f:
        return json.load(f)["produtos"]


def _list_products():
    with open(PRODUCTS_PATH, encoding="utf-8") as f:
        return [p["nome"] for p in json.load(f)["produtos"]]


def _get_dashboard_data(days: int = 30):
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(SUM(spend),0), COALESCE(SUM(leads),0),
                       COALESCE(SUM(revenue),0), COALESCE(COUNT(DISTINCT channel),0),
                       COALESCE(AVG(roas),0)
                FROM campaigns_daily WHERE date >= CURRENT_DATE - %s
            """, (days,))
            spend, leads, revenue, channels, roas = cur.fetchone()

            cur.execute("""
                SELECT channel, ROUND(SUM(spend)::numeric,2) AS spend, SUM(leads) AS leads,
                       ROUND(AVG(roas)::numeric,2) AS roas,
                       ROUND(AVG(NULLIF(cpl,0))::numeric,2) AS cpl
                FROM campaigns_daily WHERE date >= CURRENT_DATE - %s
                GROUP BY channel ORDER BY spend DESC
            """, (days,))
            cols = [d[0] for d in cur.description]
            by_channel = [dict(zip(cols, r)) for r in cur.fetchall()]

            cur.execute("""
                SELECT campaign_utm, channel, produto,
                       ROUND(SUM(spend)::numeric,2) AS spend, SUM(leads) AS leads,
                       ROUND(SUM(revenue)::numeric,2) AS revenue,
                       ROUND(AVG(roas)::numeric,2) AS roas,
                       ROUND(AVG(NULLIF(cpl,0))::numeric,2) AS cpl
                FROM campaigns_daily WHERE date >= CURRENT_DATE - %s
                GROUP BY campaign_utm, channel, produto
                ORDER BY roas DESC LIMIT 10
            """, (days,))
            cols = [d[0] for d in cur.description]
            top_campaigns = [dict(zip(cols, r)) for r in cur.fetchall()]

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
    data = _get_dashboard_data(30)
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "page": "dashboard", "days": 30,
        "active_product": _active_product, "alert_count": _get_alert_count(),
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
    env_path = str(ENV_PATH)
    if not ENV_PATH.exists():
        ENV_PATH.write_text("")
    for key, value in data.items():
        if value:
            set_key(env_path, key, value)
            os.environ[key] = value
    load_dotenv(ENV_PATH, override=True)
    return {"message": f"{len(data)} variável(is) salva(s) no .env"}


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
    thresholds = {"produtos": data}
    with open(THRESHOLDS_PATH, "w", encoding="utf-8") as f:
        json.dump(thresholds, f, indent=2, ensure_ascii=False)
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
        return {
            "message": f"Importados {result['importados']} produto(s) do HubSpot. Total: {len(result['produtos'])}",
            "importados": result["importados"],
            "produtos": result["produtos"],
        }
    except Exception as e:
        logger.error("Import products error: %s", e)
        return JSONResponse({"message": f"Erro: {e}"}, status_code=500)


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
    ACCOUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ACCOUNTS_PATH, "w", encoding="utf-8") as f:
        json.dump({"produtos": accounts}, f, indent=2, ensure_ascii=False)
    return {"message": f"Contas de '{nome}' salvas"}


@app.post("/api/accounts/remove")
async def accounts_remove(request: Request):
    data = await request.json()
    nome = data.get("nome", "").strip()
    accounts = _load_accounts()
    accounts = [p for p in accounts if p["nome"] != nome]
    with open(ACCOUNTS_PATH, "w", encoding="utf-8") as f:
        json.dump({"produtos": accounts}, f, indent=2, ensure_ascii=False)
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
