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

TIKTOK_API = "https://business-api.tiktok.com/open_api/v1.3"


class TikTokAdsPuller(BasePuller):
    channel = "tiktok"

    def auth(self):
        self.access_token = os.getenv("TIKTOK_ACCESS_TOKEN")
        self.ad_account_id = self._cfg("ad_account_id", "TIKTOK_AD_ACCOUNT_ID")
        if not self.access_token or not self.ad_account_id:
            raise EnvironmentError("TIKTOK_ACCESS_TOKEN e TIKTOK_AD_ACCOUNT_ID são obrigatórios")
        self.headers = {
            "Access-Token": self.access_token,
            "Content-Type": "application/json",
        }

    def fetch(self, date_from: date, date_to: date) -> list:
        params = {
            "advertiser_id": self.ad_account_id,
            "report_type": "BASIC",
            "dimensions": '["campaign_id","stat_time_day"]',
            "metrics": '["spend","impressions","clicks","conversion"]',
            "data_level": "AUCTION_CAMPAIGN",
            "start_date": str(date_from),
            "end_date": str(date_to),
            "page_size": 1000,
        }
        resp = requests.get(
            f"{TIKTOK_API}/report/integrated/get/",
            headers=self.headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            logger.error("TikTok API erro: %s", body.get("message"))
            return []
        return body.get("data", {}).get("list", [])

    def _fetch_campaign_name(self, campaign_id: str) -> str:
        resp = requests.get(
            f"{TIKTOK_API}/campaign/get/",
            headers=self.headers,
            params={"advertiser_id": self.ad_account_id, "campaign_ids": f'["{campaign_id}"]'},
            timeout=10,
        )
        try:
            data = resp.json().get("data", {}).get("list", [])
            if data:
                return data[0].get("campaign_name", campaign_id)
        except Exception:
            pass
        return campaign_id

    def _extract_utm(self, campaign_name: str, campaign_id: str) -> str:
        try:
            if "utm_campaign=" in campaign_name:
                parsed = parse_qs(campaign_name.split("?", 1)[1] if "?" in campaign_name else campaign_name)
                utm = parsed.get("utm_campaign", [""])[0]
                if utm:
                    return utm
        except Exception:
            pass
        return campaign_name.lower().replace(" ", "-") if campaign_name else f"tiktok-{campaign_id}"

    def normalize(self, raw: list) -> pd.DataFrame:
        rows = []
        for item in raw:
            dims = item.get("dimensions", {})
            mets = item.get("metrics", {})
            campaign_id = dims.get("campaign_id", "")
            spend = float(mets.get("spend", 0))
            impressions = int(mets.get("impressions", 0))
            clicks = int(mets.get("clicks", 0))
            conversions = int(mets.get("conversion", 0))
            rows.append({
                "date": dims.get("stat_time_day", "")[:10],
                "channel": self.channel,
                "campaign_utm": self._extract_utm("", campaign_id),
                "campaign_name": f"tiktok-{campaign_id}",
                "spend": spend,
                "impressions": impressions,
                "clicks": clicks,
                "leads": conversions,
                "revenue": 0.0,
                "roas": 0.0,
                "cpl": self._safe_divide(spend, conversions),
                "cpc": self._safe_divide(spend, clicks),
                "ctr": self._safe_divide(clicks, impressions),
            })
        return pd.DataFrame(rows) if rows else pd.DataFrame()


if __name__ == "__main__":
    from datetime import timedelta
    logging.basicConfig(level=logging.INFO)
    puller = TikTokAdsPuller()
    df = puller.run(date.today() - timedelta(days=7), date.today())
    print(df.to_string())
