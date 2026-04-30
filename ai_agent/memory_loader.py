import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PRODUCTS_PATH = Path(__file__).parent.parent / "config" / "products.json"


def get_product_context(product_name: str) -> str:
    with open(PRODUCTS_PATH, encoding="utf-8") as f:
        data = json.load(f)

    produto = next(
        (p for p in data["produtos"] if p["nome"] == product_name), None
    )
    if not produto:
        raise ValueError(f"Produto '{product_name}' não encontrado em products.json")

    icp = produto.get("icp", {})
    benchmarks = produto.get("benchmarks", {})
    historico = produto.get("historico_criativos", [])
    sazonalidade = produto.get("sazonalidade", {})
    objecoes = produto.get("objecoes", [])
    concorrentes = produto.get("concorrentes", [])

    top_criativos = [c for c in historico if c.get("performance") == "alto"]
    criativo_txt = "\n".join(
        f"  - [{c['tipo']}] Ângulo: {c['angulo']} — {c['descricao']}"
        for c in top_criativos
    ) or "  - Nenhum criativo registrado ainda"

    bench_txt = "\n".join(
        f"  - {canal.upper()}: CPL R${m.get('cpl',0):.0f} | CPC R${m.get('cpc',0):.2f} | CTR {m.get('ctr',0):.1%} | ROAS {m.get('roas',0):.1f}x"
        for canal, m in benchmarks.items() if m.get("cpl", 0) > 0
    ) or "  - Benchmarks ainda não registrados"

    objecoes_txt = "\n".join(
        f"  - \"{o['objecao']}\" → {o['resposta']}"
        for o in objecoes
    ) or "  - Nenhuma objeção registrada"

    concorrentes_txt = "\n".join(
        f"  - {c['nome']}: {c['diferencial_vs_nos']}"
        for c in concorrentes
    ) or "  - Nenhum concorrente registrado"

    context = f"""
=== CONTEXTO DO PRODUTO: {produto['nome'].upper()} ===

POSICIONAMENTO:
{produto.get('posicionamento', 'Não definido')}

ICP (Perfil de Cliente Ideal):
  - Cargo/Perfil: {icp.get('cargo', 'N/A')}
  - Setor: {icp.get('setor', 'N/A')}
  - Tamanho de empresa: {icp.get('tamanho_empresa', 'N/A')}
  - Dores principais: {', '.join(icp.get('dores_principais', []))}
  - Gatilhos de compra: {', '.join(icp.get('gatilhos_compra', []))}

NÚMEROS:
  - Ticket médio: R${produto.get('ticket_medio', 0):,.0f}
  - Meta de ROAS: {produto.get('roas_meta', 0):.1f}x
  - Meta de CPL: R${produto.get('cpl_meta', 0):.0f}
  - Canais ativos: {', '.join(produto.get('canais_ativos', []))}

BENCHMARKS HISTÓRICOS:
{bench_txt}

CRIATIVOS TOP PERFORMANCE:
{criativo_txt}

SAZONALIDADE:
  - Meses de alta: {', '.join(sazonalidade.get('meses_alta', []))}
  - Meses de baixa: {', '.join(sazonalidade.get('meses_baixa', []))}
  - Observações: {sazonalidade.get('observacoes', 'N/A')}

CONCORRENTES:
{concorrentes_txt}

OBJEÇÕES MAIS COMUNS:
{objecoes_txt}
""".strip()

    logger.info("Contexto carregado para produto: %s", product_name)
    return context


def list_products() -> list:
    with open(PRODUCTS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return [p["nome"] for p in data["produtos"]]
