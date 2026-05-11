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

GOOGLE_ADS_API_VERSION = "v20"
GOOGLE_ADS_BASE = f"https://googleads.googleapis.com/{GOOGLE_ADS_API_VERSION}"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _get_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    resp = requests.post(TOKEN_URL, data={
        "client_id":     client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type":    "refresh_token",
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()["access_token"]


def list_accessible_customers(developer_token: str, client_id: str,
                              client_secret: str, refresh_token: str) -> list:
    """Lista todas as contas acessíveis pelo token via listAccessibleCustomers."""
    access_token = _get_access_token(client_id, client_secret, refresh_token)
    resp = requests.get(
        f"{GOOGLE_ADS_BASE}/customers:listAccessibleCustomers",
        headers={
            "Authorization":   f"Bearer {access_token}",
            "developer-token": developer_token,
        },
        timeout=30,
    )
    if not resp.ok:
        logger.error("listAccessibleCustomers error %s: %s", resp.status_code, resp.text[:300])
        resp.raise_for_status()
    names = resp.json().get("resourceNames", [])
    return [n.split("/")[-1] for n in names]


def list_mcc_accounts(developer_token: str, client_id: str, client_secret: str,
                      refresh_token: str, mcc_id: str) -> list:
    """Lista sub-contas ativas sob o MCC via REST.
    Tenta searchStream no MCC; se 404, cai em listAccessibleCustomers."""
    access_token = _get_access_token(client_id, client_secret, refresh_token)
    mcc_clean = mcc_id.replace("-", "")
    url = f"{GOOGLE_ADS_BASE}/customers/{mcc_clean}/googleAds:searchStream"
    query = """
        SELECT customer_client.id, customer_client.descriptive_name,
               customer_client.currency_code, customer_client.status
        FROM customer_client
        WHERE customer_client.manager = FALSE
          AND customer_client.status = 'ENABLED'
    """
    headers = {
        "Authorization":      f"Bearer {access_token}",
        "developer-token":    developer_token,
        "login-customer-id":  mcc_clean,
    }
    resp = requests.post(url, json={"query": query}, headers=headers, timeout=30)
    if resp.status_code == 404:
        logger.warning("MCC %s retornou 404, usando listAccessibleCustomers", mcc_clean)
        ids = list_accessible_customers(developer_token, client_id, client_secret, refresh_token)
        return [{"id": cid, "name": cid, "currency": ""} for cid in ids]
    if not resp.ok:
        logger.error("list_mcc_accounts error %s: %s", resp.status_code, resp.text[:300])
        resp.raise_for_status()
    accounts = []
    for batch in resp.json():
        for result in batch.get("results", []):
            c = result.get("customerClient", {})
            accounts.append({
                "id":       str(c.get("id", "")),
                "name":     c.get("descriptiveName", ""),
                "currency": c.get("currencyCode", ""),
            })
    return accounts


class GoogleAdsPuller(BasePuller):
    channel = "google"

    def auth(self):
        self.developer_token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN")
        self.customer_id     = self._cfg("customer_id", "GOOGLE_ADS_CUSTOMER_ID").replace("-", "")
        self.mcc_id          = os.getenv("GOOGLE_ADS_CUSTOMER_ID", "").replace("-", "")
        self.client_id       = os.getenv("GOOGLE_ADS_CLIENT_ID")
        self.client_secret   = os.getenv("GOOGLE_ADS_CLIENT_SECRET")
        self.refresh_token   = os.getenv("GOOGLE_ADS_REFRESH_TOKEN")
        if not all([self.developer_token, self.refresh_token, self.customer_id]):
            raise EnvironmentError(
                "GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_REFRESH_TOKEN e "
                "GOOGLE_ADS_CUSTOMER_ID são obrigatórios"
            )

    def fetch(self, date_from: date, date_to: date) -> list:
        access_token = _get_access_token(self.client_id, self.client_secret, self.refresh_token)
        url = f"{GOOGLE_ADS_BASE}/customers/{self.customer_id}/googleAds:searchStream"
        query = f"""
            SELECT
                campaign.name,
                campaign.tracking_url_template,
                campaign.final_url_suffix,
                metrics.cost_micros,
                metrics.impressions,
                metrics.clicks,
                metrics.conversions,
                segments.date
            FROM campaign
            WHERE segments.date BETWEEN '{date_from}' AND '{date_to}'
              AND campaign.status = 'ENABLED'
              AND metrics.cost_micros > 0
        """
        headers = {
            "Authorization":   f"Bearer {access_token}",
            "developer-token": self.developer_token,
        }
        if self.mcc_id and self.mcc_id != self.customer_id:
            headers["login-customer-id"] = self.mcc_id

        resp = requests.post(url, json={"query": query}, headers=headers, timeout=30)
        if resp.status_code == 400:
            logger.warning("[google][%s] API error: %s", self.customer_id, resp.text[:200])
            return []
        resp.raise_for_status()
        rows = []
        for batch in resp.json():
            for result in batch.get("results", []):
                campaign = result.get("campaign", {})
                metrics  = result.get("metrics", {})
                segments = result.get("segments", {})
                rows.append({
                    "campaign_name": campaign.get("name", ""),
                    "tracking_url":  campaign.get("trackingUrlTemplate", ""),
                    "final_url_suffix": campaign.get("finalUrlSuffix", ""),
                    "cost_micros":   int(metrics.get("costMicros", 0)),
                    "impressions":   int(metrics.get("impressions", 0)),
                    "clicks":        int(metrics.get("clicks", 0)),
                    "conversions":   float(metrics.get("conversions", 0)),
                    "date":          segments.get("date", ""),
                })
        return rows

    def _extract_utm_campaign(self, tracking_url: str, final_url_suffix: str, campaign_name: str) -> str:
        for src in [tracking_url, final_url_suffix]:
            if not src:
                continue
            try:
                parsed = parse_qs(urlparse(src).query)
                utm = parsed.get("utm_campaign", [""])[0]
                if utm:
                    return utm
            except Exception:
                pass
        return campaign_name.lower().replace(" ", "-")

    def normalize(self, raw: list) -> pd.DataFrame:
        rows = []
        for item in raw:
            spend       = item.get("cost_micros", 0) / 1_000_000
            impressions = int(item.get("impressions", 0))
            clicks      = int(item.get("clicks", 0))
            conversions = int(item.get("conversions", 0))
            rows.append({
                "date":          item.get("date", ""),
                "channel":       self.channel,
                "campaign_utm":  self._extract_utm_campaign(
                    item.get("tracking_url", ""),
                    item.get("final_url_suffix", ""),
                    item.get("campaign_name", ""),
                ),
                "campaign_name": item.get("campaign_name", ""),
                "spend":         spend,
                "impressions":   impressions,
                "clicks":        clicks,
                "leads":         conversions,
                "revenue":       0.0,
                "roas":          0.0,
                "cpl":           self._safe_divide(spend, conversions),
                "cpc":           self._safe_divide(spend, clicks),
                "ctr":           self._safe_divide(clicks, impressions),
            })
        return pd.DataFrame(rows) if rows else pd.DataFrame()
