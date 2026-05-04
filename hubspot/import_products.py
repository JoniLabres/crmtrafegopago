"""
Importa produtos do HubSpot e popula config/products.json.

Estratégia:
  1. Busca todos os Negócios fechados agrupados por produto_negocio
  2. Para cada produto, calcula benchmarks reais (CPL, ROAS, ticket médio)
  3. Busca Contatos associados para extrair ICP e objeções comuns
  4. Grava em config/products.json mantendo campos manuais existentes
"""
import os
import sys
import json
import logging
import unicodedata
from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from hubspot_client import HubSpotClient

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PRODUCTS_PATH = Path(__file__).parent.parent / "config" / "products.json"
THRESHOLDS_PATH = Path(__file__).parent.parent / "config" / "alert_thresholds.json"


def _slug(name: str) -> str:
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return name.lower().replace(" ", "-").replace("_", "-")


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


def fetch_products_from_deals(client: HubSpotClient) -> dict:
    """Agrupa negócios fechados por produto_negocio e calcula métricas."""
    logger.info("Buscando negócios fechados no HubSpot...")
    deals = _paginate(client, "/crm/v3/objects/deals/search", [
        "dealname", "amount", "closedate", "dealstage",
        "produto_negocio", "canal_origem", "utm_campaign_origem",
    ], filters=[
        {"propertyName": "dealstage", "operator": "EQ", "value": "closedwon"}
    ])
    logger.info("Negócios encontrados: %d", len(deals))

    by_product = defaultdict(lambda: {
        "deals": [], "amounts": [], "canais": defaultdict(int),
        "campaigns": set(), "canal_origem_list": [],
    })

    for deal in deals:
        p = deal.get("properties", {})
        produto = p.get("produto_negocio", "").strip()
        if not produto:
            continue
        slug = _slug(produto)
        amount = float(p.get("amount") or 0)
        by_product[slug]["deals"].append(deal)
        by_product[slug]["amounts"].append(amount)
        by_product[slug]["nome_original"] = produto
        canal = p.get("canal_origem", "")
        if canal:
            by_product[slug]["canais"][canal] += 1
        utm = p.get("utm_campaign_origem", "")
        if utm:
            by_product[slug]["campaigns"].add(utm)

    return dict(by_product)


def fetch_contacts_for_icp(client: HubSpotClient, produto_slug: str) -> dict:
    """Busca contatos com produto_interesse para extrair ICP."""
    logger.info("Buscando contatos para ICP: %s", produto_slug)
    try:
        contacts = _paginate(client, "/crm/v3/objects/contacts/search", [
            "jobtitle", "industry", "company", "utm_source",
            "produto_interesse", "canal_primeiro_toque",
        ], filters=[
            {"propertyName": "produto_interesse", "operator": "EQ", "value": produto_slug}
        ])
    except Exception as e:
        logger.warning("Não foi possível buscar contatos: %s", e)
        return {}

    cargos = defaultdict(int)
    setores = defaultdict(int)
    canais  = defaultdict(int)

    for c in contacts:
        p = c.get("properties", {})
        if p.get("jobtitle"): cargos[p["jobtitle"]] += 1
        if p.get("industry"): setores[p["industry"]] += 1
        if p.get("utm_source"): canais[p["utm_source"]] += 1

    top = lambda d, n=3: [k for k, _ in sorted(d.items(), key=lambda x: -x[1])[:n]]
    return {
        "cargos_comuns": top(cargos),
        "setores_comuns": top(setores),
        "canais_comuns": top(canais),
        "total_contatos": len(contacts),
    }


def fetch_ad_accounts(client: HubSpotClient) -> list:
    """Tenta listar contas de anúncios conectadas ao HubSpot."""
    try:
        data = client.get("/marketing/v3/ad-accounts")
        return data.get("results", [])
    except Exception:
        return []


def build_product_entry(slug: str, data: dict, icp: dict, existing: dict = None) -> dict:
    amounts   = data.get("amounts", [])
    ticket    = round(sum(amounts) / len(amounts), 2) if amounts else 0
    canais    = list(data.get("canais", {}).keys()) or ["meta", "google"]
    campaigns = list(data.get("campaigns", set()))

    entry = existing.copy() if existing else {}

    entry.update({
        "nome": slug,
        "posicionamento": entry.get("posicionamento", f"Produto {data.get('nome_original', slug)} — preencha o posicionamento"),
        "ticket_medio": ticket,
        "canais_ativos": list(set(entry.get("canais_ativos", []) + canais)),
        "roas_meta": entry.get("roas_meta", 4.0),
        "cpl_meta": entry.get("cpl_meta", 100.0),
    })

    if not entry.get("icp"):
        entry["icp"] = {
            "cargo": ", ".join(icp.get("cargos_comuns", ["A preencher"])),
            "setor": ", ".join(icp.get("setores_comuns", ["A preencher"])),
            "tamanho_empresa": "A preencher",
            "dores_principais": ["A preencher"],
            "gatilhos_compra": ["A preencher"],
        }

    if not entry.get("historico_criativos"):
        entry["historico_criativos"] = []

    if not entry.get("sazonalidade"):
        entry["sazonalidade"] = {"meses_alta": [], "meses_baixa": [], "observacoes": ""}

    if not entry.get("concorrentes"):
        entry["concorrentes"] = []

    if not entry.get("objecoes"):
        entry["objecoes"] = [{"objecao": "A preencher", "resposta": "A preencher"}]

    existing_benchmarks = entry.get("benchmarks", {})
    if not existing_benchmarks:
        entry["benchmarks"] = {
            canal: {"cpl": 0, "cpc": 0, "ctr": 0, "roas": 0}
            for canal in canais
        }

    entry["_hubspot_deals"] = len(data.get("deals", []))
    entry["_hubspot_contacts"] = icp.get("total_contatos", 0)
    entry["_campaigns_detectadas"] = campaigns[:5]

    return entry


def import_products(client: HubSpotClient = None) -> dict:
    if client is None:
        client = HubSpotClient()

    existing_data = {"produtos": []}
    if PRODUCTS_PATH.exists():
        with open(PRODUCTS_PATH, encoding="utf-8") as f:
            existing_data = json.load(f)

    existing_map = {p["nome"]: p for p in existing_data.get("produtos", [])}

    by_product = fetch_products_from_deals(client)

    if not by_product:
        logger.warning("Nenhum produto encontrado nos negócios do HubSpot.")
        logger.info("Dica: crie a propriedade 'produto_negocio' nos Negócios e preencha os dados.")
        return {"importados": 0, "produtos": []}

    produtos = []
    for slug, data in by_product.items():
        icp = fetch_contacts_for_icp(client, slug)
        entry = build_product_entry(slug, data, icp, existing=existing_map.get(slug))
        produtos.append(entry)
        logger.info("Produto importado: %s (%d negócios)", slug, data.get("_deals_count", len(data["deals"])))

    for slug, existing in existing_map.items():
        if slug not in by_product:
            produtos.append(existing)
            logger.info("Produto mantido (sem negócios novos): %s", slug)

    produtos.sort(key=lambda p: p["nome"])
    result = {"produtos": produtos}

    with open(PRODUCTS_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info("products.json atualizado: %d produtos", len(produtos))

    _update_thresholds(produtos)
    return {"importados": len(by_product), "produtos": [p["nome"] for p in produtos]}


def _update_thresholds(produtos: list) -> None:
    existing = {}
    if THRESHOLDS_PATH.exists():
        with open(THRESHOLDS_PATH, encoding="utf-8") as f:
            existing = json.load(f).get("produtos", {})

    thresholds = {}
    for p in produtos:
        slug = p["nome"]
        thresholds[slug] = existing.get(slug, {
            "cpl_meta": p.get("cpl_meta", 100.0),
            "roas_meta": p.get("roas_meta", 4.0),
            "leads_meta_diario": 5,
            "budget_mensal": 3000.0,
            "frequencia_max_meta": 4.0,
        })

    with open(THRESHOLDS_PATH, "w", encoding="utf-8") as f:
        json.dump({"produtos": thresholds}, f, indent=2, ensure_ascii=False)
    logger.info("alert_thresholds.json atualizado: %d produtos", len(thresholds))


if __name__ == "__main__":
    result = import_products()
    print(f"\nImportação concluída:")
    print(f"  Importados do HubSpot: {result['importados']}")
    print(f"  Total em products.json: {len(result['produtos'])}")
    print(f"  Produtos: {', '.join(result['produtos'])}")
