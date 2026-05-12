import os
import sys
import logging
from datetime import date
import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hubspot"))
from hubspot_client import HubSpotClient

load_dotenv()
logger = logging.getLogger(__name__)

CONTACT_PROPERTIES = [
    "email", "firstname", "lastname", "createdate",
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "produto_interesse", "canal_primeiro_toque",
]

DEAL_PROPERTIES = [
    "dealname", "amount", "closedate", "dealstage", "createdate",
    "utm_campaign_origem", "canal_origem", "produto_negocio",
    "roas_calculado", "cac_canal",
]


class HubSpotPuller:
    def __init__(self):
        self.client = HubSpotClient()

    def _paginate(self, path: str, params: dict) -> list:
        results = []
        after = None
        while True:
            if after:
                params["after"] = after
            data = self.client.get(path, params=params)
            results.extend(data.get("results", []))
            paging = data.get("paging", {})
            after = paging.get("next", {}).get("after")
            if not after:
                break
        return results

    def fetch_contacts(self, date_from: date, date_to: date) -> pd.DataFrame:
        logger.info("Puxando contatos de %s a %s", date_from, date_to)
        params = {
            "properties": ",".join(CONTACT_PROPERTIES),
            "filterGroups": f'[{{"filters":[{{"propertyName":"createdate","operator":"BETWEEN","value":"{date_from}","highValue":"{date_to}"}}]}}]',
            "limit": 100,
        }
        raw = self._paginate("/crm/v3/objects/contacts/search", params)
        rows = [
            {prop: r.get("properties", {}).get(prop, "") for prop in CONTACT_PROPERTIES}
            | {"id": r["id"]}
            for r in raw
        ]
        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=CONTACT_PROPERTIES + ["id"])
        logger.info("Contatos puxados: %d", len(df))
        return df

    def _search(self, object_type: str, filters: list, properties: list) -> list:
        """POST paginado na search API do HubSpot."""
        results = []
        after = None
        while True:
            body = {
                "properties": properties,
                "filterGroups": [{"filters": filters}],
                "limit": 100,
            }
            if after:
                body["after"] = after
            data = self.client.post(f"/crm/v3/objects/{object_type}/search", json=body)
            results.extend(data.get("results", []))
            after = data.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
        return results

    def fetch_funnel_metrics(self, date_from: date, date_to: date) -> pd.DataFrame:
        """Retorna MQL e SQL por utm_campaign no período (via lifecyclestage)."""
        utm_data: dict = {}

        for lifecycle, key in [
            ("marketingqualifiedlead", "mqls"),
            ("salesqualifiedlead",     "sqls"),
            ("salesacceptedlead",      "sals"),
        ]:
            try:
                contacts = self._search("contacts", [
                    {"propertyName": "lifecyclestage", "operator": "EQ",      "value": lifecycle},
                    {"propertyName": "createdate",     "operator": "BETWEEN",
                     "value": str(date_from), "highValue": str(date_to)},
                ], ["utm_campaign", "lifecyclestage"])
                for c in contacts:
                    utm = (c.get("properties", {}).get("utm_campaign") or "").strip()
                    if utm:
                        utm_data.setdefault(utm, {"mqls": 0, "sqls": 0, "sals": 0})
                        utm_data[utm][key] += 1
            except Exception as e:
                logger.warning("fetch_funnel_metrics [%s]: %s", lifecycle, e)

        if not utm_data:
            return pd.DataFrame(columns=["campaign_utm", "mqls", "sqls", "sals"])
        return pd.DataFrame([{"campaign_utm": k, **v} for k, v in utm_data.items()])

    def fetch_deals(self, date_from: date, date_to: date) -> pd.DataFrame:
        logger.info("Puxando negócios fechados de %s a %s", date_from, date_to)
        params = {
            "properties": ",".join(DEAL_PROPERTIES),
            "filterGroups": f'[{{"filters":[{{"propertyName":"closedate","operator":"BETWEEN","value":"{date_from}","highValue":"{date_to}"}},{{"propertyName":"dealstage","operator":"EQ","value":"closedwon"}}]}}]',
            "limit": 100,
        }
        raw = self._paginate("/crm/v3/objects/deals/search", params)
        rows = [
            {prop: r.get("properties", {}).get(prop, "") for prop in DEAL_PROPERTIES}
            | {"id": r["id"]}
            for r in raw
        ]
        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=DEAL_PROPERTIES + ["id"])
        if not df.empty and "amount" in df.columns:
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
        logger.info("Negócios puxados: %d", len(df))
        return df


if __name__ == "__main__":
    from datetime import timedelta
    logging.basicConfig(level=logging.INFO)
    puller = HubSpotPuller()
    contacts = puller.fetch_contacts(date.today() - timedelta(days=30), date.today())
    deals = puller.fetch_deals(date.today() - timedelta(days=30), date.today())
    print(f"Contatos: {len(contacts)} | Negócios: {len(deals)}")
