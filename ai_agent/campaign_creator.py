import sys
import logging
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tracking"))
from utm_builder import build_utm
from utm_validator import validate_utm

logger = logging.getLogger(__name__)

BASE_URL = "https://seusite.com.br/lp"

CANAL_MEDIUM = {
    "meta": "paid_social",
    "google": "paid_search",
    "linkedin": "paid_social",
    "tiktok": "video",
    "programatica": "display",
}


def _build_utms(briefing: dict, product_name: str) -> dict:
    ano_mes = date.today().strftime("%Y-%m")
    objetivo = briefing.get("objetivo", "leads")
    canais = briefing.get("canal", ["meta"])
    if "todos" in canais:
        canais = list(CANAL_MEDIUM.keys())

    utms = {}
    for canal in canais:
        medium = CANAL_MEDIUM.get(canal, "paid_social")
        for funil in ["topo", "fundo"]:
            try:
                url = build_utm(
                    BASE_URL,
                    source=canal,
                    medium=medium,
                    campaign_parts={
                        "produto": product_name,
                        "funil": funil,
                        "objetivo": objetivo if objetivo in ["leads", "vendas", "trafego", "awareness"] else "leads",
                        "ano_mes": ano_mes,
                    },
                    content=f"criativo_{funil}_v1",
                    term="publico-principal",
                )
                validation = validate_utm(url)
                if validation["status"] == "ok":
                    utms[f"{canal}_{funil}"] = url
                else:
                    logger.warning("UTM inválida para %s/%s: %s", canal, funil, validation["erros"])
            except Exception as e:
                logger.warning("Erro ao gerar UTM para %s/%s: %s", canal, funil, e)
    return utms


def create_campaign(briefing: dict, product_name: str, agent=None) -> str:
    utms = _build_utms(briefing, product_name)

    utm_block = "\n".join(
        f"    [{key}]: {url}"
        for key, url in utms.items()
    ) or "    (nenhuma UTM gerada — verifique os parâmetros)"

    prompt = f"""
Com base no briefing abaixo, crie um plano completo de campanha de tráfego pago.

BRIEFING:
- Objetivo: {briefing.get('objetivo')}
- Produto: {product_name}
- Canais: {', '.join(briefing.get('canal', []))}
- Budget: {briefing.get('budget')}
- Prazo: {briefing.get('prazo')}

UTMs já geradas e validadas:
{utm_block}

O plano deve incluir:
1. Estrutura de campanha por canal (objetivos, públicos, formatos)
2. Distribuição de budget por canal (conservador / base / agressivo)
3. Segmentação detalhada para cada canal
4. Criativos sugeridos (headlines, copies, CTAs) — mínimo 3 variações
5. Cronograma de veiculação (fases de aprendizado, escala, manutenção)
6. KPIs de monitoramento e metas por fase
7. As UTMs acima devem ser incluídas no plano, associadas às campanhas corretas

Seja específico com números, públicos e estrutura — não genérico.
""".strip()

    if agent:
        return agent.chat(prompt)

    return f"[Plano de campanha para {product_name}]\n\nUTMs geradas:\n{utm_block}\n\n(Configure ANTHROPIC_API_KEY para gerar o plano completo com IA)"
