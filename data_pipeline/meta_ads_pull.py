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

META_API_VERSION = "v19.0"
META_BASE = f"https://graph.facebook.com/{META_API_VERSION}"


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
            "fields": "campaign_name,spend,impressions,clicks,actions,date_start",
            "time_range": f'{{"since":"{date_from}","until":"{date_to}"}}',
            "time_increment": 1,
            "limit": 500,
        }
        results = []
        while url:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("data", []))
            url = data.get("paging", {}).get("next")
            params = {}
        return results

    def _extract_utm_campaign(self, row: dict) -> str:
        name = row.get("campaign_name", "")
        if "utm_campaign=" in name:
            try:
                parsed = parse_qs(f"?{name.split('?', 1)[1]}")
                return parsed.get("utm_campaign", [""])[0]
            except Exception:
                pass
        return name.lower().replace(" ", "-")

    def _extract_leads(self, actions: list) -> int:
        for action in actions or []:
            if action.get("action_type") == "lead":
                return int(action.get("value", 0))
        return 0

    def normalize(self, raw: list) -> pd.DataFrame:
        rows = []
        for item in raw:
            spend = float(item.get("spend", 0))
            impressions = int(item.get("impressions", 0))
            clicks = int(item.get("clicks", 0))
            leads = self._extract_leads(item.get("actions", []))
            rows.append({
                "date": item.get("date_start", ""),
                "channel": self.channel,
                "campaign_utm": self._extract_utm_campaign(item),
                "campaign_name": item.get("campaign_name", ""),
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
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=list(rows[0].keys()) if rows else [])


if __name__ == "__main__":
    from datetime import date, timedelta
    logging.basicConfig(level=logging.INFO)
    puller = MetaAdsPuller()
    df = puller.run(date.today() - timedelta(days=7), date.today())
    print(df.to_string())
