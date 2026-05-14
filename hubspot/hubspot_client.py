import os
import time
import logging
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

HUBSPOT_BASE_URL = "https://api.hubapi.com"
MAX_RETRIES = 3


def _refresh_access_token() -> str | None:
    """Use the stored refresh token to get a new access token. Returns new token or None."""
    refresh_token = os.getenv("HUBSPOT_REFRESH_TOKEN", "")
    client_id = os.getenv("HUBSPOT_CLIENT_ID", "")
    client_secret = os.getenv("HUBSPOT_CLIENT_SECRET", "")
    if not (refresh_token and client_id and client_secret):
        return None
    try:
        r = requests.post(
            "https://api.hubapi.com/oauth/v1/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        r.raise_for_status()
        new_token = r.json().get("access_token", "")
        if new_token:
            os.environ["HUBSPOT_ACCESS_TOKEN"] = new_token
            # Persist via config_store when running in web context
            try:
                import sys, pathlib
                sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "web"))
                from config_store import save as cs_save, load as cs_load
                overrides = cs_load("env_overrides", None, {})
                overrides["HUBSPOT_ACCESS_TOKEN"] = new_token
                cs_save("env_overrides", overrides, None)
            except Exception:
                pass
            logger.info("HubSpot access token refreshed successfully")
        return new_token or None
    except Exception as exc:
        logger.warning("Failed to refresh HubSpot access token: %s", exc)
        return None


class HubSpotClient:
    def __init__(self, api_key: str = None):
        # Priority: explicit arg → OAuth access token → Private App Token
        self.api_key = (
            api_key
            or os.getenv("HUBSPOT_ACCESS_TOKEN")
            or os.getenv("HUBSPOT_API_KEY")
        )
        if not self.api_key:
            raise ValueError(
                "HubSpot não configurado. Configure HUBSPOT_ACCESS_TOKEN (OAuth) "
                "ou HUBSPOT_API_KEY (Private App Token)."
            )
        self._using_oauth = bool(not api_key and os.getenv("HUBSPOT_ACCESS_TOKEN"))
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    def _update_token(self, new_token: str) -> None:
        self.api_key = new_token
        self.session.headers["Authorization"] = f"Bearer {new_token}"

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{HUBSPOT_BASE_URL}{path}"
        refreshed = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info("%s %s (tentativa %d)", method.upper(), path, attempt)
                response = self.session.request(method, url, **kwargs)

                if response.status_code == 429:
                    wait = int(response.headers.get("Retry-After", 10))
                    logger.warning("Rate limit atingido. Aguardando %ds...", wait)
                    time.sleep(wait)
                    continue

                if response.status_code == 401 and self._using_oauth and not refreshed:
                    # Try refreshing the OAuth token once
                    new_token = _refresh_access_token()
                    if new_token:
                        self._update_token(new_token)
                        refreshed = True
                        continue
                    raise PermissionError(
                        f"Token OAuth inválido e não foi possível renovar: {response.text}"
                    )

                if response.status_code in (401, 403):
                    raise PermissionError(
                        f"Credencial inválida ou sem permissão: {response.status_code} {response.text}"
                    )

                response.raise_for_status()
                return response.json() if response.content else {}

            except (requests.ConnectionError, requests.Timeout) as e:
                wait = 2 ** attempt
                logger.warning("Erro de conexão: %s. Aguardando %ds...", e, wait)
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(wait)

        raise RuntimeError(f"Falha após {MAX_RETRIES} tentativas: {method.upper()} {path}")

    def get(self, path: str, **kwargs) -> dict:
        return self._request("get", path, **kwargs)

    def post(self, path: str, **kwargs) -> dict:
        return self._request("post", path, **kwargs)

    def patch(self, path: str, **kwargs) -> dict:
        return self._request("patch", path, **kwargs)

    def delete(self, path: str, **kwargs) -> dict:
        return self._request("delete", path, **kwargs)
