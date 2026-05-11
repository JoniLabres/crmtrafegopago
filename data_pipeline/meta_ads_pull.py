import os
import logging
from datetime import date
from urllib.parse import urlparse, parse_qs
import requests
import pandas as pd
from dotenv import load_dotenv
from base_puller import BasePuller

load_dotenv()
logger = logging.getLogger(__name__)

META_API_VERSION = "v21.0"
META_BASE = f"https://graph.facebook.com/{META_API_VERSION}"

# Tipos de ação que contam como lead
LEAD_ACTION_TYPES = {
    "lead",
    "omni_lead",
    "complete_registration",
    "offsite_conversion.custom.lead",
    "onsite_conversion.messaging_conversation_started_7d",
    "contact_total",
}


class MetaAdsPuller(BasePuller):
    channel = "meta"

    def auth(self):
        self.access_token = os.getenv("META_ACCESS_TOKEN")
        self.ad_account_id = self._cfg("ad_account_id", "META_AD_ACCOUNT_ID")
        if not self.access_token or not self.ad_account_id:
            raise EnvironmentError("META_ACCESS_TOKEN e META_AD_ACCOUNT_ID são obrigatórios")

    def fetch(self, date_from: date, date_to: date) -> list:
        url = f"{META_BASE}/{self.ad_account_id}/insights"
        params = {
            "access_token": self.access_token,
            "level": "campaign",
            "fields": (
                "campaign_name,campaign_id,"
                "spend,impressions,clicks,actions,"
                "date_start"
            ),
            "time_range": f'{{"since":"{date_from}","until":"{date_to}"}}',
            "time_increment": 1,
            "limit": 500,
        }
        results = []
        while url:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 400:
                data = resp.json()
                logger.warning("[meta][%s] API error: %s", self.ad_account_id, data.get("error", {}).get("message", ""))
                return []
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("data", []))
            next_url = data.get("paging", {}).get("next")
            url = next_url if next_url else None
            params = {}
        return results

    def _fetch_campaign_utm(self, campaign_id: str) -> str:
        """Busca o utm_campaign real do primeiro ad da campanha via tracking specs."""
        try:
            url = f"{META_BASE}/{campaign_id}/ads"
            resp = requests.get(url, params={
                "access_token": self.access_token,
                "fields": "tracking_specs,adcreatives{url_tags}",
                "limit": 1,
            }, timeout=15)
            if not resp.ok:
                return ""
            ads = resp.json().get("data", [])
            if not ads:
                return ""
            # Try tracking_specs first
            for spec in ads[0].get("tracking_specs", []):
                if "u[utm_campaign]" in spec:
                    vals = spec.get("u[utm_campaign]", [])
                    if vals:
                        return vals[0]
            # Try adcreatives url_tags
            for creative in ads[0].get("adcreatives", {}).get("data", []):
                tags = creative.get("url_tags", "")
                parsed = parse_qs(tags)
                utm = parsed.get("utm_campaign", [""])[0]
                if utm:
                    return utm
        except Exception as exc:
            logger.debug("_fetch_campaign_utm [%s]: %s", campaign_id, exc)
        return ""

    def _extract_utm_from_name(self, name: str) -> str:
        """Fallback: extrai utm_campaign do nome da campanha se ele contiver parâmetros."""
        if "utm_campaign=" in name:
            try:
                part = name.split("utm_campaign=", 1)[1].split("&")[0].split(" ")[0]
                if part:
                    return part
            except Exception:
                pass
        return name.lower().replace(" ", "-")

    def _extract_leads(self, actions: list) -> int:
        total = 0
        for action in actions or []:
            atype = action.get("action_type", "")
            if atype in LEAD_ACTION_TYPES or atype.startswith("offsite_conversion.custom."):
                try:
                    total += int(float(action.get("value", 0)))
                except (ValueError, TypeError):
                    pass
        return total

    def normalize(self, raw: list) -> pd.DataFrame:
        rows = []
        for item in raw:
            spend = float(item.get("spend", 0) or 0)
            impressions = int(item.get("impressions", 0) or 0)
            clicks = int(item.get("clicks", 0) or 0)
            leads = self._extract_leads(item.get("actions", []))
            campaign_name = item.get("campaign_name", "")
            campaign_utm = self._extract_utm_from_name(campaign_name)
            rows.append({
                "date": item.get("date_start", ""),
                "channel": self.channel,
                "campaign_utm": campaign_utm,
                "campaign_name": campaign_name,
                "spend": spend,
                "impressions": impressions,
                "clicks": clicks,
                "leads": leads,
                "revenue": 0.0,
                "roas": 0.0,
                "cpl": self._safe_divide(spend, leads),
                "cpc": self._safe_divide(spend, clicks),
                "ctr": self._safe_divide(clicks, impressions),
            })
        return pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=["date","channel","campaign_utm","campaign_name",
                     "spend","impressions","clicks","leads",
                     "revenue","roas","cpl","cpc","ctr"]
        )


if __name__ == "__main__":
    from datetime import date, timedelta
    logging.basicConfig(level=logging.INFO)
    puller = MetaAdsPuller()
    df = puller.run(date.today() - timedelta(days=7), date.today())
    print(df.to_string())
