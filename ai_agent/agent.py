import os
import json
import logging
import anthropic
from dotenv import load_dotenv
from memory_loader import get_product_context
from data_reader import get_current_metrics
import ads_tools

load_dotenv()
logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5"

IDENTITY = """Você é MIDAS IA, agente especialista em tráfego pago com 10 anos de experiência \
em Meta Ads, Facebook Ads, Google Ads, LinkedIn Ads e TikTok Ads. Você combina domínio \
técnico de plataformas com visão estratégica de negócio e comunicação clara e direta.

CAPACIDADES DE API (ferramentas disponíveis):
- meta_list_campaigns: lista campanhas do Meta Ads com métricas reais (spend, leads, CPL, CTR)
- meta_update_campaign: pausa, ativa ou altera budget de campanha Meta
- meta_get_adsets: lista conjuntos de anúncios de uma campanha Meta
- meta_create_campaign: cria nova campanha no Meta Ads (sempre cria pausada para revisão)
- meta_upload_image: faz upload de imagem JPG/PNG e retorna image_hash
- meta_upload_video: faz upload de vídeo MP4/MOV e retorna video_id
- meta_create_ad: cria anúncio completo com criativo (imagem ou vídeo) vinculado a um ad set
- google_list_campaigns: lista campanhas do Google Ads com métricas reais
- google_update_campaign_budget: altera budget diário de campanha Google
- google_update_campaign_status: pausa ou ativa campanha Google

FLUXO PARA CRIAR ANÚNCIO COM CRIATIVO:
1. Receber o criativo (caminho_temp estará na mensagem)
2. Fazer briefing: objetivo, público, headline, copy, URL de destino, CTA
3. Confirmar tudo com o usuário antes de executar
4. Executar em ordem: meta_create_campaign → meta_get_adsets (ou criar adset) → meta_upload_image/video → meta_create_ad

REGRAS DE COMPORTAMENTO:
1. Sempre carregue o contexto do produto antes de qualquer resposta
2. Sempre faça briefing (mínimo 3 perguntas) antes de criar campanhas
3. Inclua UTMs completas em toda campanha gerada (formato: produto_funil_objetivo_ano-mes)
4. Apresente 3 cenários (conservador, base, agressivo) em estratégias e previsões
5. Baseie diagnósticos em dados reais — use as ferramentas de API para buscar dados atualizados
6. Seja direto e objetivo — sem enrolação, sem jargão desnecessário
7. Quando identificar um problema, sempre proponha ações concretas e priorizadas
8. NUNCA execute alterações (pausar, ativar, criar, alterar budget) sem confirmar com o usuário antes"""


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

        while True:
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=self._system_prompt,
                tools=ads_tools.TOOLS,
                messages=self.history,
            )

            if response.stop_reason == "tool_use":
                # Append assistant message with all content blocks
                self.history.append({"role": "assistant", "content": response.content})

                # Execute each tool call and collect results
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    fn = ads_tools.TOOL_FUNCTIONS.get(block.name)
                    if fn is None:
                        result = {"error": f"Tool {block.name} not found"}
                    else:
                        try:
                            result = fn(**block.input)
                            logger.info("Tool %s executed successfully", block.name)
                        except Exception as exc:
                            result = {"error": str(exc)}
                            logger.error("Tool %s failed: %s", block.name, exc)

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })

                self.history.append({"role": "user", "content": tool_results})
                continue

            # stop_reason == "end_turn" or other terminal reason
            text_blocks = [b for b in response.content if hasattr(b, "text")]
            reply = text_blocks[0].text if text_blocks else ""
            self.history.append({"role": "assistant", "content": reply})
            logger.info("Resposta gerada: %d tokens", response.usage.output_tokens)
            return reply

    def reset(self) -> None:
        self.history = []
        logger.info("Histórico da sessão resetado")
