import os
import logging
from datetime import date
from urllib.parse import parse_qs, urlparse
import pandas as pd
from dotenv import load_dotenv
from base_puller import BasePuller

load_dotenv()
logger = logging.getLogger(__name__)


class GoogleAdsPuller(BasePuller):
    channel = "google"

    def auth(self):
        self.developer_token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN")
        self.customer_id = os.getenv("GOOGLE_ADS_CUSTOMER_ID", "").replace("-", "")
        self.client_id = os.getenv("GOOGLE_ADS_CLIENT_ID")
        self.client_secret = os.getenv("GOOGLE_ADS_CLIENT_SECRET")
        self.refresh_token = os.getenv("GOOGLE_ADS_REFRESH_TOKEN")
        if not all([self.developer_token, self.customer_id, self.refresh_token]):
            raise EnvironmentError(
                "GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_CUSTOMER_ID e GOOGLE_ADS_REFRESH_TOKEN são obrigatórios"
            )
        self._client = None
        try:
            from google.ads.googleads.client import GoogleAdsClient
            config = {
                "developer_token": self.developer_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "login_customer_id": self.customer_id,
                "use_proto_plus": True,
            }
            self._client = GoogleAdsClient.load_from_dict(config)
        except ImportError:
            logger.warning("google-ads SDK não instalado. Usando modo stub.")

    def fetch(self, date_from: date, date_to: date) -> list:
        if not self._client:
            return []
        ga_service = self._client.get_service("GoogleAdsService")
        query = f"""
            SELECT
                campaign.name,
                campaign.tracking_url_template,
                metrics.cost_micros,
                metrics.impressions,
                metrics.clicks,
                metrics.conversions,
                segments.date
            FROM campaign
            WHERE segments.date BETWEEN '{date_from}' AND '{date_to}'
              AND campaign.status = 'ENABLED'
        """
        stream = ga_service.search_stream(customer_id=self.customer_id, query=query)
        rows = []
        for batch in stream:
            for row in batch.results:
                rows.append({
                    "campaign_name": row.campaign.name,
                    "tracking_url": row.campaign.tracking_url_template,
                    "cost_micros": row.metrics.cost_micros,
                    "impressions": row.metrics.impressions,
                    "clicks": row.metrics.clicks,
                    "conversions": row.metrics.conversions,
                    "date": row.segments.date,
                })
        return rows

    def _extract_utm_campaign(self, tracking_url: str, campaign_name: str) -> str:
        try:
            parsed = parse_qs(urlparse(tracking_url).query)
            utm = parsed.get("utm_campaign", [""])[0]
            if utm:
                return utm
        except Exception:
            pass
        return campaign_name.lower().replace(" ", "-")

    def normalize(self, raw: list) -> pd.DataFrame:
        rows = []
        for item in raw:
            spend = item.get("cost_micros", 0) / 1_000_000
            impressions = int(item.get("impressions", 0))
            clicks = int(item.get("clicks", 0))
            conversions = int(item.get("conversions", 0))
            rows.append({
                "date": item.get("date", ""),
                "channel": self.channel,
                "campaign_utm": self._extract_utm_campaign(
                    item.get("tracking_url", ""), item.get("campaign_name", "")
                ),
                "campaign_name": item.get("campaign_name", ""),
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
