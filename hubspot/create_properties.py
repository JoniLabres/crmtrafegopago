import logging
from hubspot_client import HubSpotClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CONTACT_PROPERTIES = [
    {
        "name": "utm_source",
        "label": "UTM Source",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Canal de origem do lead (meta, google, linkedin, tiktok, programatica)",
    },
    {
        "name": "utm_medium",
        "label": "UTM Medium",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Tipo de mídia (paid_social, paid_search, display, video, native)",
    },
    {
        "name": "utm_campaign",
        "label": "UTM Campaign",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Campanha — chave de atribuição (produto_funil_objetivo_ano-mes)",
    },
    {
        "name": "utm_content",
        "label": "UTM Content",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Variação de criativo (tipo_angulo_variacao)",
    },
    {
        "name": "utm_term",
        "label": "UTM Term",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Palavra-chave ou nome do público",
    },
    {
        "name": "produto_interesse",
        "label": "Produto de Interesse",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "contactinformation",
        "description": "Produto de interesse do lead",
        "options": [
            {"label": "Produto A", "value": "produto-a", "displayOrder": 1},
            {"label": "Produto B", "value": "produto-b", "displayOrder": 2},
            {"label": "Produto C", "value": "produto-c", "displayOrder": 3},
        ],
    },
    {
        "name": "canal_primeiro_toque",
        "label": "Canal Primeiro Toque",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Primeiro canal de contato do lead",
    },
]

DEAL_PROPERTIES = [
    {
        "name": "utm_campaign_origem",
        "label": "UTM Campaign Origem",
        "type": "string",
        "fieldType": "text",
        "groupName": "dealinformation",
        "description": "Copiado do Contato via Workflow — chave de atribuição de receita",
    },
    {
        "name": "canal_origem",
        "label": "Canal Origem",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "dealinformation",
        "description": "Canal que originou o negócio",
        "options": [
            {"label": "Meta", "value": "meta", "displayOrder": 1},
            {"label": "Google", "value": "google", "displayOrder": 2},
            {"label": "LinkedIn", "value": "linkedin", "displayOrder": 3},
            {"label": "TikTok", "value": "tiktok", "displayOrder": 4},
            {"label": "Programática", "value": "programatica", "displayOrder": 5},
        ],
    },
    {
        "name": "produto_negocio",
        "label": "Produto do Negócio",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "dealinformation",
        "description": "Produto associado ao negócio",
        "options": [
            {"label": "Produto A", "value": "produto-a", "displayOrder": 1},
            {"label": "Produto B", "value": "produto-b", "displayOrder": 2},
            {"label": "Produto C", "value": "produto-c", "displayOrder": 3},
        ],
    },
    {
        "name": "roas_calculado",
        "label": "ROAS Calculado",
        "type": "number",
        "fieldType": "number",
        "groupName": "dealinformation",
        "description": "Receita / Custo da campanha de origem",
    },
    {
        "name": "cac_canal",
        "label": "CAC por Canal",
        "type": "number",
        "fieldType": "number",
        "groupName": "dealinformation",
        "description": "Custo de aquisição por canal (spend / deals_count)",
    },
]


def _property_exists(client: HubSpotClient, object_type: str, name: str) -> bool:
    try:
        client.get(f"/crm/v3/properties/{object_type}/{name}")
        return True
    except Exception:
        return False


def create_properties(client: HubSpotClient, object_type: str, properties: list) -> dict:
    created = 0
    skipped = 0

    for prop in properties:
        if _property_exists(client, object_type, prop["name"]):
            logger.info("[%s] Propriedade já existe: %s", object_type, prop["name"])
            skipped += 1
        else:
            client.post(f"/crm/v3/properties/{object_type}", json=prop)
            logger.info("[%s] Propriedade criada: %s", object_type, prop["name"])
            created += 1

    return {"criadas": created, "ja_existiam": skipped}


def main():
    client = HubSpotClient()

    print("\n=== Criando propriedades de Contato ===")
    result_contact = create_properties(client, "contacts", CONTACT_PROPERTIES)

    print("\n=== Criando propriedades de Negócio ===")
    result_deal = create_properties(client, "deals", DEAL_PROPERTIES)

    print("\n=== Resumo ===")
    print(f"Contato  — Criadas: {result_contact['criadas']} | Já existiam: {result_contact['ja_existiam']}")
    print(f"Negócio  — Criadas: {result_deal['criadas']} | Já existiam: {result_deal['ja_existiam']}")
    total = result_contact["criadas"] + result_deal["criadas"]
    print(f"Total de novas propriedades: {total}")


if __name__ == "__main__":
    main()
