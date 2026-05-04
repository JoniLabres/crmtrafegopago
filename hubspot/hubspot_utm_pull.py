"""
Pulls UTM data from HubSpot contacts and maps them to the project's UTM taxonomy.

Reads utm_source, utm_medium, utm_campaign, utm_content, utm_term from HubSpot
contact properties and validates/normalises them against the local taxonomy.
Also reads HubSpot marketing email UTMs when available.
"""
import os
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hubspot_client import HubSpotClient

sys.path.insert(0, str(Path(__file__).parent.parent / "tracking"))
from utm_validator import validate_utm

load_dotenv_path = Path(__file__).parent.parent / ".env"

logger = logging.getLogger(__name__)

UTM_FIELDS = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]

CONTACT_PROPS = [
    "email", "createdate", "hs_analytics_first_url",
    "hs_analytics_source", "hs_analytics_source_data_1", "hs_analytics_source_data_2",
] + UTM_FIELDS

OUTPUT_PATH = Path(__file__).parent.parent / "outputs" / "hubspot_utms.json"


class HubSpotUTMPuller:
    def __init__(self):
        self.client = HubSpotClient()

    def _paginate_search(self, path: str, payload: dict) -> list:
        results = []
        after = None
        while True:
            if after:
                payload["after"] = after
            data = self.client.post(path, json=payload)
            results.extend(data.get("results", []))
            paging = data.get("paging", {})
            after = paging.get("next", {}).get("after")
            if not after:
                break
        return results

    def fetch_contacts_with_utms(self, date_from: date, date_to: date) -> list[dict]:
        logger.info("Buscando contatos com UTMs de %s a %s", date_from, date_to)
        payload = {
            "properties": CONTACT_PROPS,
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "createdate",
                            "operator": "BETWEEN",
                            "value": str(date_from),
                            "highValue": str(date_to),
                        },
                        {
                            "propertyName": "utm_campaign",
                            "operator": "HAS_PROPERTY",
                        },
                    ]
                }
            ],
            "limit": 100,
        }
        raw = self._paginate_search("/crm/v3/objects/contacts/search", payload)
        contacts = []
        for r in raw:
            props = r.get("properties", {})
            contact = {"id": r["id"]}
            for field in CONTACT_PROPS:
                contact[field] = props.get(field) or ""
            contacts.append(contact)
        logger.info("Contatos com UTMs encontrados: %d", len(contacts))
        return contacts

    def fetch_email_campaign_utms(self) -> list[dict]:
        """Pulls UTMs from HubSpot marketing email campaigns."""
        logger.info("Buscando UTMs de campanhas de email HubSpot")
        try:
            data = self.client.get("/marketing/v3/emails", params={"limit": 100, "state": "PUBLISHED"})
            emails = data.get("results", [])
        except Exception as exc:
            logger.warning("Erro ao buscar emails HubSpot: %s", exc)
            return []

        campaign_utms = []
        for email in emails:
            stats = email.get("statistics", {})
            settings = email.get("settings", {})
            tracking = email.get("primaryEmail", {}).get("content", {}).get("widgets", {})

            utm_campaign = (
                email.get("campaignName", "")
                or email.get("name", "")
            )
            entry = {
                "id": email.get("id"),
                "name": email.get("name"),
                "utm_source": "hubspot",
                "utm_medium": "email",
                "utm_campaign": utm_campaign,
                "sends": stats.get("sent", 0),
                "opens": stats.get("open", 0),
                "clicks": stats.get("click", 0),
                "conversions": stats.get("contactsConverted", 0),
            }
            campaign_utms.append(entry)

        logger.info("Campanhas de email HubSpot: %d", len(campaign_utms))
        return campaign_utms

    def validate_and_group(self, contacts: list[dict]) -> dict:
        """Groups contacts by utm_campaign and validates UTMs."""
        by_campaign: dict[str, dict] = {}

        for c in contacts:
            campaign = c.get("utm_campaign", "").strip()
            if not campaign:
                continue

            if campaign not in by_campaign:
                by_campaign[campaign] = {
                    "utm_source": c.get("utm_source", ""),
                    "utm_medium": c.get("utm_medium", ""),
                    "utm_campaign": campaign,
                    "utm_content": c.get("utm_content", ""),
                    "utm_term": c.get("utm_term", ""),
                    "leads": 0,
                    "validation": None,
                }

            by_campaign[campaign]["leads"] += 1

        for campaign, data in by_campaign.items():
            dummy_url = (
                f"https://x.com"
                f"?utm_source={data['utm_source']}"
                f"&utm_medium={data['utm_medium']}"
                f"&utm_campaign={campaign}"
                f"&utm_content={data['utm_content'] or 'n'}"
                f"&utm_term={data['utm_term'] or 'n'}"
            )
            result = validate_utm(dummy_url)
            data["validation"] = {
                "status": result["status"],
                "erros": result["erros"],
            }

        return by_campaign

    def run(self, days: int = 30) -> dict:
        date_to = date.today()
        date_from = date_to - timedelta(days=days)

        contacts = self.fetch_contacts_with_utms(date_from, date_to)
        by_campaign = self.validate_and_group(contacts)
        email_utms = self.fetch_email_campaign_utms()

        output = {
            "period": {"from": str(date_from), "to": str(date_to)},
            "total_contacts_with_utms": len(contacts),
            "campaigns": list(by_campaign.values()),
            "email_campaigns": email_utms,
        }

        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False, default=str)
        logger.info("Resultado salvo em %s", OUTPUT_PATH)

        return output


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from dotenv import load_dotenv
    load_dotenv(load_dotenv_path)

    puller = HubSpotUTMPuller()
    result = puller.run(days=30)
    print(f"\nCampanhas encontradas: {len(result['campaigns'])}")
    for c in result["campaigns"]:
        status = c["validation"]["status"]
        icon = "OK" if status == "ok" else "ERRO"
        print(f"  [{icon}] {c['utm_campaign']} — {c['leads']} leads")
