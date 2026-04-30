import logging
from hubspot_client import HubSpotClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WORKFLOWS_API = "/automation/v4/flows"


def _build_utm_capture_workflow() -> dict:
    """Workflow 1: Captura UTM nos campos do Contato via hidden fields do formulário."""
    utm_fields = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]
    copy_actions = [
        {
            "type": "SET_CONTACT_PROPERTY",
            "filters": [],
            "actionTypeVersion": 0,
            "fields": {
                "propertyName": field,
                "newValue": f"{{{{{field}}}}}",
            },
        }
        for field in utm_fields
    ]
    copy_actions.append({
        "type": "SET_CONTACT_PROPERTY",
        "filters": [],
        "actionTypeVersion": 0,
        "fields": {
            "propertyName": "canal_primeiro_toque",
            "newValue": "{{utm_source}}",
        },
    })

    return {
        "name": "Captura UTM no Contato",
        "description": "Copia parâmetros UTM dos hidden fields do formulário para as propriedades do Contato",
        "type": "CONTACT",
        "enabled": True,
        "enrollmentCriteria": {
            "type": "FORM_SUBMISSION",
            "filters": [],
        },
        "actions": copy_actions,
    }


def _build_utm_propagation_workflow() -> dict:
    """Workflow 2: Propaga UTM do Contato para o Negócio associado."""
    return {
        "name": "Propagar UTM para Negócio",
        "description": "Copia utm_campaign, canal_origem e produto_interesse do Contato para o Negócio ao criar",
        "type": "DEAL",
        "enabled": True,
        "enrollmentCriteria": {
            "type": "DEAL_CREATED",
            "filters": [],
        },
        "actions": [
            {
                "type": "COPY_TO_ASSOCIATED_OBJECT",
                "filters": [],
                "actionTypeVersion": 0,
                "fields": {
                    "fromObjectType": "CONTACT",
                    "toObjectType": "DEAL",
                    "propertyMappings": [
                        {"fromProperty": "utm_campaign", "toProperty": "utm_campaign_origem"},
                        {"fromProperty": "utm_source", "toProperty": "canal_origem"},
                        {"fromProperty": "produto_interesse", "toProperty": "produto_negocio"},
                    ],
                },
            }
        ],
    }


def _build_lead_scoring_workflow() -> dict:
    """Workflow 3: Lead Scoring baseado em canal e comportamento."""
    return {
        "name": "Lead Scoring por Canal",
        "description": "Incrementa score do contato baseado em canal, produto e comportamento",
        "type": "CONTACT",
        "enabled": True,
        "enrollmentCriteria": {
            "type": "CONTACT_CREATED_OR_UPDATED",
            "filters": [],
        },
        "actions": [
            {
                "type": "ADJUST_SCORE",
                "filters": [
                    {
                        "property": "utm_source",
                        "operator": "EQUAL",
                        "value": "google",
                    }
                ],
                "actionTypeVersion": 0,
                "fields": {"scoreProperty": "hubspotscore", "adjustment": 20},
            },
            {
                "type": "ADJUST_SCORE",
                "filters": [
                    {
                        "property": "produto_interesse",
                        "operator": "IS_NOT_EMPTY",
                        "value": "",
                    }
                ],
                "actionTypeVersion": 0,
                "fields": {"scoreProperty": "hubspotscore", "adjustment": 15},
            },
            {
                "type": "ADJUST_SCORE",
                "filters": [
                    {
                        "property": "utm_medium",
                        "operator": "EQUAL",
                        "value": "paid_search",
                    }
                ],
                "actionTypeVersion": 0,
                "fields": {"scoreProperty": "hubspotscore", "adjustment": 10},
            },
            {
                "type": "ADJUST_SCORE",
                "filters": [
                    {
                        "property": "hs_analytics_last_url",
                        "operator": "CONTAINS",
                        "value": "/preco",
                    }
                ],
                "actionTypeVersion": 0,
                "fields": {"scoreProperty": "hubspotscore", "adjustment": 5},
            },
        ],
    }


def create_workflow(client: HubSpotClient, workflow: dict) -> dict:
    name = workflow["name"]
    logger.info("Criando workflow: %s", name)
    result = client.post(WORKFLOWS_API, json=workflow)
    logger.info("Workflow criado com ID: %s", result.get("id", "N/A"))
    return result


def main():
    client = HubSpotClient()

    workflows = [
        _build_utm_capture_workflow(),
        _build_utm_propagation_workflow(),
        _build_lead_scoring_workflow(),
    ]

    print("\n=== Criando Workflows no HubSpot ===\n")
    results = []
    for wf in workflows:
        result = create_workflow(client, wf)
        results.append(result)
        print(f"OK: {wf['name']} — ID: {result.get('id', 'N/A')}")

    print(f"\nTotal de workflows criados: {len(results)}")


if __name__ == "__main__":
    main()
