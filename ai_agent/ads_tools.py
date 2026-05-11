"""
Ferramentas de acesso direto às APIs de Meta Ads e Google Ads para o Midas IA.
Cada função é mapeada como uma tool no Anthropic tool_use.
"""
import os
import json
import logging
import requests
from datetime import date, timedelta

logger = logging.getLogger(__name__)

META_API_VERSION = "v21.0"
META_BASE = f"https://graph.facebook.com/{META_API_VERSION}"
GOOGLE_ADS_BASE = "https://googleads.googleapis.com/v20"
TOKEN_URL = "https://oauth2.googleapis.com/token"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _meta_token() -> str:
    return os.getenv("META_ACCESS_TOKEN", "")

def _google_access_token() -> str:
    resp = requests.post(TOKEN_URL, data={
        "client_id":     os.getenv("GOOGLE_ADS_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_ADS_CLIENT_SECRET"),
        "refresh_token": os.getenv("GOOGLE_ADS_REFRESH_TOKEN"),
        "grant_type":    "refresh_token",
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()["access_token"]

def _google_headers(login_customer_id: str = None) -> dict:
    h = {
        "Authorization":   f"Bearer {_google_access_token()}",
        "developer-token": os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
    }
    mcc = os.getenv("GOOGLE_ADS_CUSTOMER_ID", "").replace("-", "")
    if login_customer_id:
        h["login-customer-id"] = login_customer_id.replace("-", "")
    elif mcc:
        h["login-customer-id"] = mcc
    return h

def _accounts_config() -> dict:
    """Retorna mapeamento produto → contas."""
    try:
        import sys, pathlib
        root = pathlib.Path(__file__).parent.parent
        sys.path.insert(0, str(root / "web"))
        import config_store
        data = config_store.load("accounts", root / "config" / "accounts.json", {"produtos": []})
    except Exception:
        import json as _json, pathlib
        path = pathlib.Path(__file__).parent.parent / "config" / "accounts.json"
        data = _json.loads(path.read_text()) if path.exists() else {"produtos": []}
    return {p["nome"]: p.get("contas", {}) for p in data.get("produtos", [])}

def _meta_account(product: str) -> str:
    cfg = _accounts_config()
    return cfg.get(product, {}).get("meta", {}).get("ad_account_id", "")

def _google_customer(product: str) -> str:
    cfg = _accounts_config()
    return cfg.get(product, {}).get("google", {}).get("customer_id", "")


# ── META ADS TOOLS ────────────────────────────────────────────────────────────

def meta_list_campaigns(product: str, date_from: str = None, date_to: str = None) -> dict:
    """Lista campanhas do Meta Ads para um produto com métricas de performance."""
    account_id = _meta_account(product)
    if not account_id:
        return {"error": f"Conta Meta não configurada para '{product}'"}
    if not date_from:
        date_from = str(date.today() - timedelta(days=7))
    if not date_to:
        date_to = str(date.today())

    # Campos das campanhas
    r = requests.get(f"{META_BASE}/{account_id}/campaigns", params={
        "access_token": _meta_token(),
        "fields": "id,name,status,objective,daily_budget,lifetime_budget,created_time",
        "limit": 100,
    }, timeout=20)
    if not r.ok:
        return {"error": r.json().get("error", {}).get("message", r.text[:200])}
    campaigns = r.json().get("data", [])

    # Insights do período
    insights_r = requests.get(f"{META_BASE}/{account_id}/insights", params={
        "access_token": _meta_token(),
        "level": "campaign",
        "fields": "campaign_id,campaign_name,spend,impressions,clicks,actions,cpm,cpc,ctr",
        "time_range": json.dumps({"since": date_from, "until": date_to}),
        "limit": 200,
    }, timeout=20)
    insights_by_id = {}
    if insights_r.ok:
        for row in insights_r.json().get("data", []):
            insights_by_id[row["campaign_id"]] = row

    result = []
    for c in campaigns:
        ins = insights_by_id.get(c["id"], {})
        actions = ins.get("actions", [])
        leads = sum(int(float(a.get("value", 0))) for a in actions
                    if a.get("action_type") in ("lead","omni_lead","complete_registration"))
        budget_daily = c.get("daily_budget", "")
        result.append({
            "id": c["id"],
            "name": c["name"],
            "status": c["status"],
            "objective": c.get("objective", ""),
            "daily_budget_brl": f"R$ {float(budget_daily)/100:.2f}" if budget_daily else "—",
            "spend": f"R$ {float(ins.get('spend', 0)):.2f}",
            "impressions": ins.get("impressions", "0"),
            "clicks": ins.get("clicks", "0"),
            "ctr": f"{float(ins.get('ctr', 0)):.2f}%",
            "cpc": f"R$ {float(ins.get('cpc', 0)):.2f}",
            "leads": leads,
            "cpl": f"R$ {float(ins.get('spend',0))/leads:.2f}" if leads > 0 else "—",
        })

    return {"product": product, "account": account_id,
            "period": f"{date_from} → {date_to}", "campaigns": result}


def meta_update_campaign(product: str, campaign_id: str,
                         status: str = None, daily_budget_brl: float = None) -> dict:
    """Pausa, ativa ou altera budget diário de uma campanha Meta Ads."""
    payload = {"access_token": _meta_token()}
    changes = []
    if status:
        s = status.upper()
        if s not in ("ACTIVE", "PAUSED"):
            return {"error": "status deve ser ACTIVE ou PAUSED"}
        payload["status"] = s
        changes.append(f"status → {s}")
    if daily_budget_brl is not None:
        payload["daily_budget"] = int(daily_budget_brl * 100)  # centavos
        changes.append(f"budget diário → R$ {daily_budget_brl:.2f}")
    if not changes:
        return {"error": "Informe status ou daily_budget_brl para alterar"}

    r = requests.post(f"{META_BASE}/{campaign_id}", data=payload, timeout=15)
    if not r.ok:
        return {"error": r.json().get("error", {}).get("message", r.text[:200])}
    return {"success": True, "campaign_id": campaign_id, "changes": changes,
            "message": f"Campanha {campaign_id} atualizada: {', '.join(changes)}"}


def meta_get_adsets(product: str, campaign_id: str) -> dict:
    """Lista conjuntos de anúncios de uma campanha Meta Ads."""
    r = requests.get(f"{META_BASE}/{campaign_id}/adsets", params={
        "access_token": _meta_token(),
        "fields": "id,name,status,daily_budget,optimization_goal,targeting,bid_amount,billing_event",
        "limit": 50,
    }, timeout=15)
    if not r.ok:
        return {"error": r.json().get("error", {}).get("message", r.text[:200])}
    return {"campaign_id": campaign_id, "adsets": r.json().get("data", [])}


def meta_create_campaign(product: str, name: str, objective: str,
                         daily_budget_brl: float, special_ad_categories: list = None) -> dict:
    """Cria uma nova campanha no Meta Ads. objective: OUTCOME_LEADS, OUTCOME_TRAFFIC, OUTCOME_AWARENESS, OUTCOME_SALES."""
    account_id = _meta_account(product)
    if not account_id:
        return {"error": f"Conta Meta não configurada para '{product}'"}
    payload = {
        "access_token": _meta_token(),
        "name": name,
        "objective": objective,
        "status": "PAUSED",  # sempre cria pausada para revisão
        "daily_budget": int(daily_budget_brl * 100),
        "special_ad_categories": special_ad_categories or [],
    }
    r = requests.post(f"{META_BASE}/{account_id}/campaigns", data=payload, timeout=15)
    if not r.ok:
        return {"error": r.json().get("error", {}).get("message", r.text[:300])}
    campaign_id = r.json().get("id")
    return {"success": True, "campaign_id": campaign_id, "name": name,
            "status": "PAUSED", "message": f"Campanha '{name}' criada (pausada para revisão). ID: {campaign_id}"}


# ── GOOGLE ADS TOOLS ──────────────────────────────────────────────────────────

def google_list_campaigns(product: str, date_from: str = None, date_to: str = None) -> dict:
    """Lista campanhas do Google Ads para um produto com métricas de performance."""
    customer_id = _google_customer(product)
    if not customer_id:
        return {"error": f"Conta Google Ads não configurada para '{product}'"}
    if not date_from:
        date_from = str(date.today() - timedelta(days=7))
    if not date_to:
        date_to = str(date.today())

    query = f"""
        SELECT campaign.id, campaign.name, campaign.status,
               campaign.advertising_channel_type,
               campaign_budget.amount_micros,
               metrics.cost_micros, metrics.impressions,
               metrics.clicks, metrics.conversions, metrics.ctr,
               metrics.average_cpc
        FROM campaign
        WHERE segments.date BETWEEN '{date_from}' AND '{date_to}'
          AND campaign.status != 'REMOVED'
          AND metrics.cost_micros > 0
    """
    mcc = os.getenv("GOOGLE_ADS_CUSTOMER_ID", "").replace("-", "")
    r = requests.post(
        f"{GOOGLE_ADS_BASE}/customers/{customer_id}/googleAds:searchStream",
        json={"query": query},
        headers=_google_headers(mcc),
        timeout=30,
    )
    if not r.ok:
        return {"error": f"Google Ads API {r.status_code}: {r.text[:300]}"}

    campaigns = []
    for batch in r.json():
        for row in batch.get("results", []):
            c = row.get("campaign", {})
            m = row.get("metrics", {})
            b = row.get("campaignBudget", {})
            spend = int(m.get("costMicros", 0)) / 1_000_000
            conversions = float(m.get("conversions", 0))
            clicks = int(m.get("clicks", 0))
            campaigns.append({
                "id": c.get("id", ""),
                "name": c.get("name", ""),
                "status": c.get("status", ""),
                "type": c.get("advertisingChannelType", ""),
                "daily_budget_brl": f"R$ {int(b.get('amountMicros',0))/1_000_000:.2f}",
                "spend": f"R$ {spend:.2f}",
                "impressions": m.get("impressions", 0),
                "clicks": clicks,
                "ctr": f"{float(m.get('ctr',0))*100:.2f}%",
                "cpc": f"R$ {int(m.get('averageCpc',0))/1_000_000:.2f}",
                "conversions": conversions,
                "cpl": f"R$ {spend/conversions:.2f}" if conversions > 0 else "—",
            })

    return {"product": product, "customer_id": customer_id,
            "period": f"{date_from} → {date_to}", "campaigns": campaigns}


def google_update_campaign_budget(product: str, campaign_id: str,
                                   new_daily_budget_brl: float) -> dict:
    """Altera o budget diário de uma campanha Google Ads."""
    customer_id = _google_customer(product)
    if not customer_id:
        return {"error": f"Conta Google Ads não configurada para '{product}'"}

    # Primeiro busca o budget_id da campanha
    query = f"SELECT campaign.id, campaign.campaign_budget FROM campaign WHERE campaign.id = {campaign_id}"
    mcc = os.getenv("GOOGLE_ADS_CUSTOMER_ID", "").replace("-", "")
    r = requests.post(
        f"{GOOGLE_ADS_BASE}/customers/{customer_id}/googleAds:searchStream",
        json={"query": query},
        headers=_google_headers(mcc),
        timeout=15,
    )
    if not r.ok:
        return {"error": f"Erro ao buscar campanha: {r.text[:200]}"}

    budget_resource = None
    for batch in r.json():
        for row in batch.get("results", []):
            budget_resource = row.get("campaign", {}).get("campaignBudget", "")

    if not budget_resource:
        return {"error": "Budget resource não encontrado para a campanha"}

    budget_id = budget_resource.split("/")[-1]
    mutate_r = requests.post(
        f"{GOOGLE_ADS_BASE}/customers/{customer_id}/campaignBudgets:mutate",
        json={"operations": [{"update": {
            "resourceName": budget_resource,
            "amountMicros": str(int(new_daily_budget_brl * 1_000_000)),
        }, "updateMask": "amountMicros"}]},
        headers=_google_headers(mcc),
        timeout=15,
    )
    if not mutate_r.ok:
        return {"error": f"Erro ao atualizar budget: {mutate_r.text[:300]}"}
    return {"success": True, "campaign_id": campaign_id,
            "new_daily_budget": f"R$ {new_daily_budget_brl:.2f}",
            "message": f"Budget da campanha {campaign_id} atualizado para R$ {new_daily_budget_brl:.2f}/dia"}


def google_update_campaign_status(product: str, campaign_id: str, status: str) -> dict:
    """Pausa ou ativa uma campanha Google Ads. status: ENABLED ou PAUSED."""
    customer_id = _google_customer(product)
    if not customer_id:
        return {"error": f"Conta Google Ads não configurada para '{product}'"}
    s = status.upper()
    if s not in ("ENABLED", "PAUSED"):
        return {"error": "status deve ser ENABLED ou PAUSED"}
    mcc = os.getenv("GOOGLE_ADS_CUSTOMER_ID", "").replace("-", "")
    r = requests.post(
        f"{GOOGLE_ADS_BASE}/customers/{customer_id}/campaigns:mutate",
        json={"operations": [{"update": {
            "resourceName": f"customers/{customer_id}/campaigns/{campaign_id}",
            "status": s,
        }, "updateMask": "status"}]},
        headers=_google_headers(mcc),
        timeout=15,
    )
    if not r.ok:
        return {"error": f"Erro ao atualizar status: {r.text[:300]}"}
    return {"success": True, "campaign_id": campaign_id, "status": s,
            "message": f"Campanha {campaign_id} → {s}"}


# ── META ADS CREATIVE TOOLS ──────────────────────────────────────────────────

def meta_upload_image(product: str, file_path: str) -> dict:
    """Faz upload de uma imagem para o Meta Ads e retorna o image_hash necessário para criar anúncios."""
    account_id = _meta_account(product)
    if not account_id:
        return {"error": f"Conta Meta não configurada para '{product}'"}
    import pathlib
    p = pathlib.Path(file_path)
    if not p.exists():
        return {"error": f"Arquivo não encontrado: {file_path}"}
    with open(file_path, "rb") as f:
        r = requests.post(
            f"{META_BASE}/{account_id}/adimages",
            data={"access_token": _meta_token()},
            files={"filename": (p.name, f, "image/jpeg")},
            timeout=60,
        )
    if not r.ok:
        return {"error": r.json().get("error", {}).get("message", r.text[:300])}
    images = r.json().get("images", {})
    image_hash = list(images.values())[0].get("hash", "") if images else ""
    return {"success": True, "image_hash": image_hash, "file": p.name,
            "message": f"Imagem '{p.name}' enviada. Hash: {image_hash}"}


def meta_upload_video(product: str, file_path: str) -> dict:
    """Faz upload de um vídeo para o Meta Ads e retorna o video_id necessário para criar anúncios."""
    account_id = _meta_account(product)
    if not account_id:
        return {"error": f"Conta Meta não configurada para '{product}'"}
    import pathlib
    p = pathlib.Path(file_path)
    if not p.exists():
        return {"error": f"Arquivo não encontrado: {file_path}"}
    with open(file_path, "rb") as f:
        r = requests.post(
            f"{META_BASE}/{account_id}/advideos",
            data={"access_token": _meta_token(), "title": p.stem},
            files={"source": (p.name, f, "video/mp4")},
            timeout=120,
        )
    if not r.ok:
        return {"error": r.json().get("error", {}).get("message", r.text[:300])}
    video_id = r.json().get("id", "")
    return {"success": True, "video_id": video_id, "file": p.name,
            "message": f"Vídeo '{p.name}' enviado. ID: {video_id}"}


def meta_create_ad(product: str, adset_id: str, headline: str, body: str,
                   link_url: str, cta: str = "LEARN_MORE",
                   image_hash: str = None, video_id: str = None) -> dict:
    """Cria um anúncio completo no Meta Ads com criativo (imagem ou vídeo). Sempre cria com status PAUSED."""
    account_id = _meta_account(product)
    if not account_id:
        return {"error": f"Conta Meta não configurada para '{product}'"}
    if not image_hash and not video_id:
        return {"error": "Informe image_hash (imagem) ou video_id (vídeo)"}

    # Monta o creative
    if image_hash:
        story = {"link_data": {
            "link": link_url,
            "message": body,
            "name": headline,
            "call_to_action": {"type": cta},
            "image_hash": image_hash,
        }}
    else:
        story = {"video_data": {
            "video_id": video_id,
            "title": headline,
            "message": body,
            "call_to_action": {"type": cta, "value": {"link": link_url}},
        }}

    cr = requests.post(f"{META_BASE}/{account_id}/adcreatives", data={
        "access_token": _meta_token(),
        "name": f"criativo_{headline[:40]}",
        "object_story_spec": json.dumps(story),
    }, timeout=20)
    if not cr.ok:
        return {"error": f"Erro ao criar creative: {cr.json().get('error',{}).get('message', cr.text[:200])}"}
    creative_id = cr.json().get("id", "")

    # Cria o anúncio
    ad_r = requests.post(f"{META_BASE}/{account_id}/ads", data={
        "access_token": _meta_token(),
        "name": headline[:80],
        "adset_id": adset_id,
        "creative": json.dumps({"creative_id": creative_id}),
        "status": "PAUSED",
    }, timeout=20)
    if not ad_r.ok:
        return {"error": f"Erro ao criar anúncio: {ad_r.json().get('error',{}).get('message', ad_r.text[:200])}"}
    ad_id = ad_r.json().get("id", "")
    return {"success": True, "ad_id": ad_id, "creative_id": creative_id,
            "status": "PAUSED",
            "message": f"Anúncio '{headline[:60]}' criado (pausado para revisão). ID: {ad_id}"}


# ── DB QUERY TOOL ─────────────────────────────────────────────────────────────

def db_query_campaigns(date_from: str = None, date_to: str = None,
                       produto: str = None, channel: str = None) -> dict:
    """Consulta o banco de dados local com métricas agregadas de campanhas.
    Use para responder perguntas sobre spend, leads, ROAS, CPL, CTR no período."""
    from datetime import date as _date, timedelta as _td
    if not date_from:
        date_from = str(_date.today() - _td(days=7))
    if not date_to:
        date_to = str(_date.today())
    try:
        import sys, pathlib, psycopg2
        root = pathlib.Path(__file__).parent.parent
        sys.path.insert(0, str(root / "web"))
        from config_store import get_db_url
        conn = psycopg2.connect(get_db_url())

        where = ["date BETWEEN %s AND %s"]
        params: list = [date_from, date_to]
        if produto:
            where.append("produto = %s")
            params.append(produto)
        if channel:
            where.append("channel = %s")
            params.append(channel)
        where_sql = " AND ".join(where)

        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT produto, channel,
                       ROUND(SUM(spend)::numeric,2)      AS spend,
                       SUM(impressions)                   AS impressions,
                       SUM(clicks)                        AS clicks,
                       SUM(leads)                         AS leads,
                       ROUND(SUM(revenue)::numeric,2)     AS revenue,
                       CASE WHEN SUM(spend)>0
                            THEN ROUND((SUM(revenue)/SUM(spend))::numeric,2) ELSE 0 END AS roas,
                       CASE WHEN SUM(leads)>0
                            THEN ROUND((SUM(spend)/SUM(leads))::numeric,2) ELSE 0 END AS cpl,
                       CASE WHEN SUM(clicks)>0
                            THEN ROUND((SUM(spend)/SUM(clicks))::numeric,2) ELSE 0 END AS cpc,
                       CASE WHEN SUM(impressions)>0
                            THEN ROUND((SUM(clicks)::numeric/SUM(impressions)*100)::numeric,2) ELSE 0 END AS ctr_pct
                FROM campaigns_daily
                WHERE {where_sql}
                GROUP BY produto, channel
                ORDER BY spend DESC
            """, params)
            cols = [d[0] for d in cur.description]
            by_channel = [dict(zip(cols, r)) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT MAX(campaign_name) AS campaign_name, campaign_utm,
                       produto, channel,
                       ROUND(SUM(spend)::numeric,2)      AS spend,
                       SUM(leads)                         AS leads,
                       CASE WHEN SUM(leads)>0
                            THEN ROUND((SUM(spend)/SUM(leads))::numeric,2) ELSE 0 END AS cpl,
                       CASE WHEN SUM(spend)>0
                            THEN ROUND((SUM(revenue)/SUM(spend))::numeric,2) ELSE 0 END AS roas
                FROM campaigns_daily
                WHERE {where_sql}
                GROUP BY campaign_utm, produto, channel
                ORDER BY spend DESC
                LIMIT 15
            """, params)
            cols2 = [d[0] for d in cur.description]
            top_campaigns = [dict(zip(cols2, r)) for r in cur.fetchall()]

        conn.close()
        totals = {
            "spend":       sum(float(r["spend"]) for r in by_channel),
            "leads":       sum(int(r["leads"]) for r in by_channel),
            "impressions": sum(int(r["impressions"]) for r in by_channel),
            "clicks":      sum(int(r["clicks"]) for r in by_channel),
        }
        totals["cpl"]  = round(totals["spend"] / totals["leads"], 2) if totals["leads"] else 0
        totals["revenue"] = sum(float(r["revenue"]) for r in by_channel)
        totals["roas"] = round(totals["revenue"] / totals["spend"], 2) if totals["spend"] else 0
        return {
            "period": f"{date_from} → {date_to}",
            "totals": totals,
            "by_product_channel": by_channel,
            "top_campaigns": top_campaigns,
        }
    except Exception as e:
        return {"error": str(e)}


# ── TOOL DEFINITIONS (Anthropic format) ──────────────────────────────────────

TOOLS = [
    {
        "name": "meta_list_campaigns",
        "description": "Lista todas as campanhas do Meta Ads de um produto com métricas de performance (spend, leads, CPL, CTR). Use para analisar o estado atual das campanhas.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product":   {"type": "string", "description": "Nome do produto (ex: ixcsoft, ixc-xcale)"},
                "date_from": {"type": "string", "description": "Data início YYYY-MM-DD (padrão: 7 dias atrás)"},
                "date_to":   {"type": "string", "description": "Data fim YYYY-MM-DD (padrão: hoje)"},
            },
            "required": ["product"],
        },
    },
    {
        "name": "meta_update_campaign",
        "description": "Pausa, ativa ou altera o budget diário de uma campanha Meta Ads. SEMPRE confirme com o usuário antes de executar alterações.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product":          {"type": "string", "description": "Nome do produto"},
                "campaign_id":      {"type": "string", "description": "ID da campanha Meta"},
                "status":           {"type": "string", "description": "ACTIVE ou PAUSED"},
                "daily_budget_brl": {"type": "number", "description": "Novo budget diário em R$"},
            },
            "required": ["product", "campaign_id"],
        },
    },
    {
        "name": "meta_get_adsets",
        "description": "Lista os conjuntos de anúncios (ad sets) de uma campanha Meta Ads com segmentação e configurações.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product":     {"type": "string", "description": "Nome do produto"},
                "campaign_id": {"type": "string", "description": "ID da campanha Meta"},
            },
            "required": ["product", "campaign_id"],
        },
    },
    {
        "name": "meta_create_campaign",
        "description": "Cria uma nova campanha no Meta Ads (sempre cria com status PAUSED para revisão). Use após briefing completo com o usuário.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product":               {"type": "string", "description": "Nome do produto"},
                "name":                  {"type": "string", "description": "Nome da campanha"},
                "objective":             {"type": "string", "description": "OUTCOME_LEADS, OUTCOME_TRAFFIC, OUTCOME_AWARENESS ou OUTCOME_SALES"},
                "daily_budget_brl":      {"type": "number", "description": "Budget diário em R$"},
                "special_ad_categories": {"type": "array",  "items": {"type": "string"}, "description": "Lista vazia [] para a maioria dos casos"},
            },
            "required": ["product", "name", "objective", "daily_budget_brl"],
        },
    },
    {
        "name": "meta_upload_image",
        "description": "Faz upload de uma imagem (JPG/PNG) para o Meta Ads e retorna o image_hash. Use o caminho_temp do arquivo anexado pelo usuário.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product":   {"type": "string", "description": "Nome do produto"},
                "file_path": {"type": "string", "description": "Caminho local do arquivo de imagem (caminho_temp recebido na mensagem)"},
            },
            "required": ["product", "file_path"],
        },
    },
    {
        "name": "meta_upload_video",
        "description": "Faz upload de um vídeo (MP4/MOV) para o Meta Ads e retorna o video_id. Use o caminho_temp do arquivo anexado pelo usuário.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product":   {"type": "string", "description": "Nome do produto"},
                "file_path": {"type": "string", "description": "Caminho local do arquivo de vídeo (caminho_temp recebido na mensagem)"},
            },
            "required": ["product", "file_path"],
        },
    },
    {
        "name": "meta_create_ad",
        "description": "Cria um anúncio completo no Meta Ads com criativo (imagem ou vídeo). Sempre cria pausado. Use após upload do criativo e com adset_id definido.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product":    {"type": "string", "description": "Nome do produto"},
                "adset_id":   {"type": "string", "description": "ID do conjunto de anúncios"},
                "headline":   {"type": "string", "description": "Título do anúncio (máx 80 chars)"},
                "body":       {"type": "string", "description": "Texto do anúncio"},
                "link_url":   {"type": "string", "description": "URL de destino"},
                "cta":        {"type": "string", "description": "Call to action: LEARN_MORE, SIGN_UP, GET_QUOTE, CONTACT_US, DOWNLOAD"},
                "image_hash": {"type": "string", "description": "Hash da imagem (retornado por meta_upload_image)"},
                "video_id":   {"type": "string", "description": "ID do vídeo (retornado por meta_upload_video)"},
            },
            "required": ["product", "adset_id", "headline", "body", "link_url"],
        },
    },
    {
        "name": "google_list_campaigns",
        "description": "Lista campanhas do Google Ads de um produto com métricas de performance (spend, conversões, CPL, CTR, CPC).",
        "input_schema": {
            "type": "object",
            "properties": {
                "product":   {"type": "string", "description": "Nome do produto"},
                "date_from": {"type": "string", "description": "Data início YYYY-MM-DD"},
                "date_to":   {"type": "string", "description": "Data fim YYYY-MM-DD"},
            },
            "required": ["product"],
        },
    },
    {
        "name": "google_update_campaign_budget",
        "description": "Altera o budget diário de uma campanha Google Ads. SEMPRE confirme com o usuário antes de executar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product":              {"type": "string", "description": "Nome do produto"},
                "campaign_id":          {"type": "string", "description": "ID numérico da campanha Google"},
                "new_daily_budget_brl": {"type": "number", "description": "Novo budget diário em R$"},
            },
            "required": ["product", "campaign_id", "new_daily_budget_brl"],
        },
    },
    {
        "name": "db_query_campaigns",
        "description": "Consulta o banco de dados local com métricas agregadas de campanhas (spend, leads, ROAS, CPL, CTR, impressões, cliques). Use para responder perguntas sobre performance no período, comparar canais, identificar melhores campanhas, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Data início YYYY-MM-DD (padrão: 7 dias atrás)"},
                "date_to":   {"type": "string", "description": "Data fim YYYY-MM-DD (padrão: hoje)"},
                "produto":   {"type": "string", "description": "Filtrar por produto específico (ex: ixcsoft). Omitir para todos."},
                "channel":   {"type": "string", "description": "Filtrar por canal: meta, google, linkedin, tiktok. Omitir para todos."},
            },
            "required": [],
        },
    },
    {
        "name": "google_update_campaign_status",
        "description": "Pausa (PAUSED) ou ativa (ENABLED) uma campanha Google Ads. SEMPRE confirme com o usuário antes de executar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product":     {"type": "string", "description": "Nome do produto"},
                "campaign_id": {"type": "string", "description": "ID numérico da campanha Google"},
                "status":      {"type": "string", "description": "ENABLED ou PAUSED"},
            },
            "required": ["product", "campaign_id", "status"],
        },
    },
]

TOOL_FUNCTIONS = {
    "meta_list_campaigns":           meta_list_campaigns,
    "meta_update_campaign":          meta_update_campaign,
    "meta_get_adsets":               meta_get_adsets,
    "meta_create_campaign":          meta_create_campaign,
    "meta_upload_image":             meta_upload_image,
    "meta_upload_video":             meta_upload_video,
    "meta_create_ad":                meta_create_ad,
    "google_list_campaigns":         google_list_campaigns,
    "google_update_campaign_budget": google_update_campaign_budget,
    "google_update_campaign_status": google_update_campaign_status,
    "db_query_campaigns":            db_query_campaigns,
}
