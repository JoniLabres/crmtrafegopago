import os
import time
import logging
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

HUBSPOT_BASE_URL = "https://api.hubapi.com"
MAX_RETRIES = 3


class HubSpotClient:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("HUBSPOT_API_KEY")
        if not self.api_key:
            raise ValueError("HUBSPOT_API_KEY não configurada")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{HUBSPOT_BASE_URL}{path}"
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info("%s %s (tentativa %d)", method.upper(), path, attempt)
                response = self.session.request(method, url, **kwargs)

                if response.status_code == 429:
                    wait = int(response.headers.get("Retry-After", 10))
                    logger.warning("Rate limit atingido. Aguardando %ds...", wait)
                    time.sleep(wait)
                    continue

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
