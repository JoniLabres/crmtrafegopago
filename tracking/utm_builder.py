import json
import unicodedata
import logging
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TAXONOMY_PATH = Path(__file__).parent.parent / "config" / "utm_taxonomy.json"


def _load_taxonomy() -> dict:
    with open(TAXONOMY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFD", value)
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return value.lower().replace(" ", "-")


def _validate_field(field_name: str, value: str, allowed: list) -> None:
    if value not in allowed:
        raise ValueError(
            f"Valor inválido para {field_name}: '{value}'. "
            f"Valores permitidos: {allowed}"
        )


def build_campaign_slug(produto: str, funil: str, objetivo: str, ano_mes: str) -> str:
    taxonomy = _load_taxonomy()
    funis = taxonomy["utm_campaign"]["funil"]
    objetivos = taxonomy["utm_campaign"]["objetivo"]

    _validate_field("funil", funil, funis)
    _validate_field("objetivo", objetivo, objetivos)

    produto_slug = _normalize(produto)
    if len(ano_mes) != 7 or ano_mes[4] != "-":
        raise ValueError(
            f"Formato de ano_mes inválido: '{ano_mes}'. Use YYYY-MM (ex: 2025-05)"
        )

    return f"{produto_slug}_{funil}_{objetivo}_{ano_mes}"


def build_utm(
    base_url: str,
    source: str,
    medium: str,
    campaign_parts: dict,
    content: str = "",
    term: str = "",
) -> str:
    """
    Gera URL completa com parâmetros UTM validados.

    campaign_parts: dict com chaves produto, funil, objetivo, ano_mes
    """
    taxonomy = _load_taxonomy()

    _validate_field("utm_source", source, taxonomy["utm_source"])
    _validate_field("utm_medium", medium, taxonomy["utm_medium"])

    required = ["produto", "funil", "objetivo", "ano_mes"]
    missing = [k for k in required if k not in campaign_parts]
    if missing:
        raise ValueError(f"campaign_parts faltando campos: {missing}")

    campaign = build_campaign_slug(
        campaign_parts["produto"],
        campaign_parts["funil"],
        campaign_parts["objetivo"],
        campaign_parts["ano_mes"],
    )

    params = {
        "utm_source": source,
        "utm_medium": medium,
        "utm_campaign": campaign,
    }
    if content:
        params["utm_content"] = _normalize(content)
    if term:
        params["utm_term"] = _normalize(term)

    parsed = urlparse(base_url)
    query = urlencode(params)
    final_url = urlunparse(parsed._replace(query=query))

    logger.info("UTM gerada: %s", final_url)
    return final_url


if __name__ == "__main__":
    base = "https://seusite.com.br/lp"
    exemplos = [
        {
            "source": "meta",
            "medium": "paid_social",
            "campaign_parts": {"produto": "produto-a", "funil": "topo", "objetivo": "leads", "ano_mes": "2025-05"},
            "content": "video_dor_v1",
            "term": "lookalike-clientes",
        },
        {
            "source": "google",
            "medium": "paid_search",
            "campaign_parts": {"produto": "produto-a", "funil": "fundo", "objetivo": "vendas", "ano_mes": "2025-05"},
            "content": "texto_preco_v1",
            "term": "software-gestao",
        },
        {
            "source": "linkedin",
            "medium": "paid_social",
            "campaign_parts": {"produto": "produto-b", "funil": "meio", "objetivo": "leads", "ano_mes": "2025-05"},
            "content": "imagem_beneficio_v2",
            "term": "gerentes-ti",
        },
        {
            "source": "tiktok",
            "medium": "video",
            "campaign_parts": {"produto": "produto-a", "funil": "topo", "objetivo": "awareness", "ano_mes": "2025-05"},
            "content": "video_humor_v1",
            "term": "18-34",
        },
        {
            "source": "facebook",
            "medium": "paid_social",
            "campaign_parts": {"produto": "produto-a", "funil": "topo", "objetivo": "leads", "ano_mes": "2025-05"},
            "content": "video_testemunho_v1",
            "term": "lookalike-compradores",
        },
        {
            "source": "programatica",
            "medium": "display",
            "campaign_parts": {"produto": "produto-b", "funil": "meio", "objetivo": "trafego", "ano_mes": "2025-05"},
            "content": "banner_oferta_v1",
            "term": "retargeting-visitantes",
        },
    ]

    print("\n=== Exemplos de UTMs geradas ===\n")
    for i, ex in enumerate(exemplos, 1):
        url = build_utm(base, **ex)
        print(f"{i}. {url}\n")
