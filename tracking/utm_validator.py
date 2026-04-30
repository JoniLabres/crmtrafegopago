import json
import re
import csv
import logging
from pathlib import Path
from urllib.parse import urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TAXONOMY_PATH = Path(__file__).parent.parent / "config" / "utm_taxonomy.json"


def _load_taxonomy() -> dict:
    with open(TAXONOMY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _has_accents_or_spaces(value: str) -> bool:
    return bool(re.search(r"[À-ÿ\s]", value))


def _has_uppercase(value: str) -> bool:
    return value != value.lower()


def validate_utm(url: str) -> dict:
    """
    Valida todos os parâmetros UTM de uma URL.
    Retorna dict com: status (ok|error), erros encontrados, params extraídos.
    """
    result = {"status": "ok", "erros": [], "params": {}}
    taxonomy = _load_taxonomy()

    try:
        parsed = urlparse(url)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    except Exception as e:
        return {"status": "error", "erros": [f"URL inválida: {e}"], "params": {}}

    result["params"] = params

    required = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]
    for field in required:
        if field not in params:
            result["erros"].append(f"Parâmetro ausente: {field}")

    if "utm_source" in params:
        val = params["utm_source"]
        if val not in taxonomy["utm_source"]:
            result["erros"].append(
                f"utm_source inválido: '{val}'. Permitidos: {taxonomy['utm_source']}"
            )
        if _has_uppercase(val):
            result["erros"].append(f"utm_source com maiúscula: '{val}'")

    if "utm_medium" in params:
        val = params["utm_medium"]
        if val not in taxonomy["utm_medium"]:
            result["erros"].append(
                f"utm_medium inválido: '{val}'. Permitidos: {taxonomy['utm_medium']}"
            )

    if "utm_campaign" in params:
        val = params["utm_campaign"]
        parts = val.split("_")
        if len(parts) != 4:
            result["erros"].append(
                f"utm_campaign deve ter formato produto_funil_objetivo_ano-mes, recebido: '{val}'"
            )
        else:
            _, funil, objetivo, ano_mes = parts
            if funil not in taxonomy["utm_campaign"]["funil"]:
                result["erros"].append(
                    f"funil inválido em utm_campaign: '{funil}'. Permitidos: {taxonomy['utm_campaign']['funil']}"
                )
            if objetivo not in taxonomy["utm_campaign"]["objetivo"]:
                result["erros"].append(
                    f"objetivo inválido em utm_campaign: '{objetivo}'. Permitidos: {taxonomy['utm_campaign']['objetivo']}"
                )
            if not re.match(r"^\d{4}-\d{2}$", ano_mes):
                result["erros"].append(
                    f"ano-mes inválido em utm_campaign: '{ano_mes}'. Use YYYY-MM"
                )
        if _has_uppercase(val):
            result["erros"].append(f"utm_campaign com maiúscula: '{val}'")
        if _has_accents_or_spaces(val):
            result["erros"].append(f"utm_campaign com acento ou espaço: '{val}'")

    for field in ["utm_content", "utm_term"]:
        if field in params:
            val = params[field]
            if _has_uppercase(val):
                result["erros"].append(f"{field} com maiúscula: '{val}'")
            if _has_accents_or_spaces(val):
                result["erros"].append(f"{field} com acento ou espaço: '{val}'")

    if result["erros"]:
        result["status"] = "error"

    return result


def validate_batch(csv_path: str) -> list:
    """
    Valida uma planilha CSV de URLs com UTMs.
    Espera coluna 'url'. Retorna lista de resultados por linha.
    """
    results = []
    path = Path(csv_path)

    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {csv_path}")

    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "url" not in (reader.fieldnames or []):
            raise ValueError("CSV deve ter coluna 'url'")

        for i, row in enumerate(reader, 1):
            url = row.get("url", "").strip()
            result = validate_utm(url)
            result["linha"] = i
            result["url"] = url
            results.append(result)
            status = "OK" if result["status"] == "ok" else f"ERRO: {result['erros']}"
            logger.info("Linha %d: %s", i, status)

    total = len(results)
    ok = sum(1 for r in results if r["status"] == "ok")
    logger.info("Resultado: %d/%d URLs válidas", ok, total)
    return results


if __name__ == "__main__":
    exemplos = [
        "https://site.com?utm_source=meta&utm_medium=paid_social&utm_campaign=produto-a_topo_leads_2025-05&utm_content=video_dor_v1&utm_term=lookalike-clientes",
        "https://site.com?utm_source=Meta&utm_medium=paid_social&utm_campaign=produto-a_topo_leads_2025-05&utm_content=video_dor_v1&utm_term=lookalike-clientes",
        "https://site.com?utm_source=meta&utm_medium=paid_social&utm_campaign=produto-a_topo_2025-05&utm_content=video_dor_v1&utm_term=lookalike-clientes",
    ]

    print("\n=== Validação de UTMs ===\n")
    for url in exemplos:
        result = validate_utm(url)
        print(f"URL: {url[:80]}...")
        print(f"Status: {result['status']}")
        if result["erros"]:
            for erro in result["erros"]:
                print(f"  - {erro}")
        print()
