import os
import logging
from datetime import date
from urllib.parse import parse_qs, urlparse
import requests
import pandas as pd
from dotenv import load_dotenv
from base_puller import BasePuller

load_dotenv()
logger = logging.getLogger(__name__)

LINKEDIN_API = "https://api.linkedin.com/v2"


class LinkedInAdsPuller(BasePuller):
    channel = "linkedin"

    def auth(self):
        self.access_token = os.getenv("LINKEDIN_ACCESS_TOKEN")
        self.ad_account_id = os.getenv("LINKEDIN_AD_ACCOUNT_ID")
        if not self.access_token or not self.ad_account_id:
            raise EnvironmentError("LINKEDIN_ACCESS_TOKEN e LINKEDIN_AD_ACCOUNT_ID são obrigatórios")
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def fetch(self, date_from: date, date_to: date) -> list:
        params = {
            "q": "analytics",
            "pivot": "CAMPAIGN",
            "dateRange.start.day": date_from.day,
            "dateRange.start.month": date_from.month,
            "dateRange.start.year": date_from.year,
            "dateRange.end.day": date_to.day,
            "dateRange.end.month": date_to.month,
            "dateRange.end.year": date_to.year,
            "timeGranularity": "DAILY",
            "accounts": f"urn:li:sponsoredAccount:{self.ad_account_id}",
            "fields": "dateRange,costInLocalCurrency,impressions,clicks,leadGenerationMailContactInfoShares,pivotValues",
        }
        resp = requests.get(
            f"{LINKEDIN_API}/adAnalyticsV2", headers=self.headers, params=params, timeout=30
        )
        resp.raise_for_status()
        return resp.json().get("elements", [])

    def _extract_utm_campaign(self, tracking_url: str, campaign_id: str) -> str:
        try:
            parsed = parse_qs(urlparse(tracking_url).query)
            utm = parsed.get("utm_campaign", [""])[0]
            if utm:
                return utm
        except Exception:
            pass
        return f"linkedin-campaign-{campaign_id}"

    def normalize(self, raw: list) -> pd.DataFrame:
        rows = []
        for item in raw:
            dr = item.get("dateRange", {}).get("start", {})
            date_str = f"{dr.get('year', '')}-{str(dr.get('month', '')).zfill(2)}-{str(dr.get('day', '')).zfill(2)}"
            spend = float(item.get("costInLocalCurrency", 0))
            impressions = int(item.get("impressions", 0))
            clicks = int(item.get("clicks", 0))
            leads = int(item.get("leadGenerationMailContactInfoShares", 0))
            campaign_id = str(item.get("pivotValues", [""])[0]).split(":")[-1]
            rows.append({
                "date": date_str,
                "channel": self.channel,
                "campaign_utm": self._extract_utm_campaign("", campaign_id),
                "campaign_name": f"linkedin-{campaign_id}",
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
        return pd.DataFrame(rows) if rows else pd.DataFrame()
