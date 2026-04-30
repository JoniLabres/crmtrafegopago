import logging
from abc import ABC, abstractmethod
from datetime import date
import pandas as pd

logger = logging.getLogger(__name__)

STANDARD_SCHEMA = [
    "date", "channel", "campaign_utm", "campaign_name",
    "spend", "impressions", "clicks", "leads", "revenue",
    "roas", "cpl", "cpc", "ctr",
]


class BasePuller(ABC):
    channel: str = ""

    def __init__(self):
        self.auth()

    @abstractmethod
    def auth(self) -> None:
        """Inicializa credenciais e cliente da API."""

    @abstractmethod
    def fetch(self, date_from: date, date_to: date) -> list:
        """Puxa dados brutos da API para o período."""

    @abstractmethod
    def normalize(self, raw: list) -> pd.DataFrame:
        """Converte dados brutos para o schema padrão."""

    def run(self, date_from: date, date_to: date) -> pd.DataFrame:
        logger.info("[%s] Puxando dados de %s a %s", self.channel, date_from, date_to)
        raw = self.fetch(date_from, date_to)
        df = self.normalize(raw)
        df = self._ensure_schema(df)
        logger.info("[%s] %d linhas normalizadas", self.channel, len(df))
        return df

    def _ensure_schema(self, df: pd.DataFrame) -> pd.DataFrame:
        for col in STANDARD_SCHEMA:
            if col not in df.columns:
                df[col] = 0 if col not in ("date", "channel", "campaign_utm", "campaign_name") else ""
        return df[STANDARD_SCHEMA]

    @staticmethod
    def _safe_divide(numerator: float, denominator: float) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0
