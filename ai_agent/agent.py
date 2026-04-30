import os
import logging
import anthropic
from dotenv import load_dotenv
from memory_loader import get_product_context
from data_reader import get_current_metrics

load_dotenv()
logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-20250514"

IDENTITY = """Você é um especialista em tráfego pago com 10 anos de experiência em Meta Ads, \
Google Ads, LinkedIn Ads e TikTok Ads. Você combina domínio técnico de plataformas com \
visão estratégica de negócio e comunicação clara e direta.

REGRAS DE COMPORTAMENTO:
1. Sempre carregue o contexto do produto antes de qualquer resposta
2. Sempre faça briefing (mínimo 3 perguntas) antes de criar campanhas
3. Inclua UTMs completas em toda campanha gerada (formato: produto_funil_objetivo_ano-mes)
4. Apresente 3 cenários (conservador, base, agressivo) em estratégias e previsões
5. Baseie diagnósticos em dados reais do dashboard — nunca em feeling
6. Seja direto e objetivo — sem enrolação, sem jargão desnecessário
7. Quando identificar um problema, sempre proponha ações concretas e priorizadas"""


class CampaignAgent:
    def __init__(self, api_key: str = None):
        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY não configurada")
        self.client = anthropic.Anthropic(api_key=key)
        self.history: list[dict] = []
        self.active_product: str = None
        self._system_prompt: str = IDENTITY

    def set_product(self, product_name: str) -> None:
        product_ctx = get_product_context(product_name)
        metrics_ctx = get_current_metrics(product=product_name, days=30)
        self.active_product = product_name
        self._system_prompt = f"{IDENTITY}\n\n{product_ctx}\n\n{metrics_ctx}"
        self.history = []
        logger.info("Produto ativo: %s", product_name)

    def chat(self, message: str, product_name: str = None) -> str:
        if product_name and product_name != self.active_product:
            self.set_product(product_name)
        elif not self.active_product and not product_name:
            logger.warning("Nenhum produto ativo. Usando contexto genérico.")

        self.history.append({"role": "user", "content": message})

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=self._system_prompt,
            messages=self.history,
        )

        reply = response.content[0].text
        self.history.append({"role": "assistant", "content": reply})
        logger.info("Resposta gerada: %d tokens", response.usage.output_tokens)
        return reply

    def reset(self) -> None:
        self.history = []
        logger.info("Histórico da sessão resetado")
