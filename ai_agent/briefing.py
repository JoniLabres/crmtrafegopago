import logging
from memory_loader import list_products

logger = logging.getLogger(__name__)

QUESTIONS = [
    ("objetivo", "Qual é o objetivo principal da campanha?\n  1. Geração de leads\n  2. Vendas diretas\n  3. Tráfego para o site\n  4. Awareness de marca\n  Responda com o número ou descreva: "),
    ("produto", f"Qual produto será anunciado? Produtos disponíveis: {{produtos}}\n  → "),
    ("canal", "Quais canais serão usados?\n  1. Meta Ads\n  2. Google Ads\n  3. LinkedIn Ads\n  4. TikTok Ads\n  5. Todos os canais ativos\n  Responda com número(s) separados por vírgula: "),
    ("budget", "Qual é o budget total para esta campanha? (ex: R$ 5.000 por mês)\n  → R$ "),
    ("prazo", "Qual é o prazo da campanha? (ex: 30 dias, 3 meses)\n  → "),
]

OBJETIVO_MAP = {
    "1": "leads", "2": "vendas", "3": "trafego", "4": "awareness",
}

CANAL_MAP = {
    "1": "meta", "2": "google", "3": "linkedin", "4": "tiktok", "5": "todos",
}


def _parse_objetivo(raw: str) -> str:
    raw = raw.strip()
    return OBJETIVO_MAP.get(raw, raw.lower().replace(" ", "_"))


def _parse_canal(raw: str) -> list:
    parts = [p.strip() for p in raw.split(",")]
    return [CANAL_MAP.get(p, p) for p in parts]


def _parse_budget(raw: str) -> str:
    cleaned = raw.strip().replace("R$", "").replace(".", "").replace(",", ".").strip()
    try:
        return f"R$ {float(cleaned):,.2f}"
    except ValueError:
        return raw.strip()


def run_briefing() -> dict:
    print("\n" + "="*60)
    print("  BRIEFING INTELIGENTE — Tráfego Pago com IA")
    print("="*60)
    print("Responda as perguntas abaixo para gerar sua campanha.\n")

    produtos = list_products()
    briefing = {}

    for key, question in QUESTIONS:
        if key == "produto":
            question = question.format(produtos=", ".join(produtos))

        while True:
            raw = input(question).strip()
            if not raw:
                print("  ⚠ Resposta obrigatória. Tente novamente.")
                continue

            if key == "objetivo":
                briefing[key] = _parse_objetivo(raw)
            elif key == "canal":
                briefing[key] = _parse_canal(raw)
            elif key == "budget":
                briefing[key] = _parse_budget(raw)
            elif key == "produto":
                if raw not in produtos and raw not in [str(i+1) for i in range(len(produtos))]:
                    print(f"  ⚠ Produto inválido. Opções: {', '.join(produtos)}")
                    continue
                if raw.isdigit():
                    raw = produtos[int(raw)-1]
                briefing[key] = raw
            else:
                briefing[key] = raw
            break

    briefing["produto_ativo"] = briefing.get("produto", "")

    print("\n" + "="*60)
    print("  BRIEFING CONCLUÍDO")
    print("="*60)
    print(f"  Objetivo : {briefing.get('objetivo')}")
    print(f"  Produto  : {briefing.get('produto')}")
    print(f"  Canais   : {', '.join(briefing.get('canal', []))}")
    print(f"  Budget   : {briefing.get('budget')}")
    print(f"  Prazo    : {briefing.get('prazo')}")
    print("="*60 + "\n")

    return briefing
