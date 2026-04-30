import sys
import os
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "tracking"))

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

from agent import CampaignAgent
from briefing import run_briefing
from campaign_creator import create_campaign
from optimizer import diagnose, optimize, forecast
from memory_loader import list_products

HELP = """
Comandos disponíveis:
  /produto [nome]   — muda o produto ativo
  /produtos         — lista os produtos disponíveis
  /briefing         — inicia módulo de briefing
  /criar            — cria nova campanha (chama briefing primeiro)
  /diagnostico      — diagnóstico do produto ativo (últimos 7 dias)
  /otimizar         — recomendações de otimização baseadas em dados
  /prever [budget]  — simula cenários com o budget informado
  /reset            — limpa o histórico da conversa
  /sair             — encerra o agente
"""


def _print_separator():
    print("\n" + "─" * 60 + "\n")


def main():
    print("\n" + "="*60)
    print("  AGENTE DE TRÁFEGO PAGO — Powered by Claude")
    print("="*60)
    print(HELP)

    try:
        agent = CampaignAgent()
    except ValueError as e:
        print(f"\n⚠ {e}")
        print("Configure ANTHROPIC_API_KEY no arquivo .env para usar o agente.")
        agent = None

    active_product = None
    last_briefing = None

    produtos = list_products()
    if produtos:
        print(f"Produtos disponíveis: {', '.join(produtos)}")
        print(f"Use /produto {produtos[0]} para começar.\n")

    while True:
        try:
            user_input = input("Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nEncerrando...")
            break

        if not user_input:
            continue

        # ── Comandos ──────────────────────────────────────────────────────────

        if user_input.startswith("/produto"):
            parts = user_input.split(maxsplit=1)
            if len(parts) < 2:
                print(f"Uso: /produto [nome]. Disponíveis: {', '.join(list_products())}")
                continue
            nome = parts[1].strip()
            try:
                if agent:
                    agent.set_product(nome)
                active_product = nome
                print(f"\n✓ Produto ativo: {nome}\n")
            except ValueError as e:
                print(f"⚠ {e}")

        elif user_input == "/produtos":
            print(f"\nProdutos: {', '.join(list_products())}\n")

        elif user_input == "/briefing":
            last_briefing = run_briefing()
            if active_product:
                last_briefing["produto"] = active_product

        elif user_input == "/criar":
            if not active_product:
                print("⚠ Defina um produto primeiro com /produto [nome]")
                continue
            if not last_briefing:
                print("Iniciando briefing...\n")
                last_briefing = run_briefing()
                last_briefing["produto"] = active_product
            _print_separator()
            print("Gerando plano de campanha...\n")
            result = create_campaign(last_briefing, active_product, agent=agent)
            print(result)
            _print_separator()

        elif user_input == "/diagnostico":
            if not active_product:
                print("⚠ Defina um produto primeiro com /produto [nome]")
                continue
            _print_separator()
            print(f"Diagnóstico de {active_product} (últimos 7 dias)...\n")
            result = diagnose(active_product, days=7, agent=agent)
            print(result)
            _print_separator()

        elif user_input == "/otimizar":
            if not active_product:
                print("⚠ Defina um produto primeiro com /produto [nome]")
                continue
            _print_separator()
            print(f"Gerando otimizações para {active_product}...\n")
            result = optimize(active_product, days=7, agent=agent)
            print(result)
            _print_separator()

        elif user_input.startswith("/prever"):
            if not active_product:
                print("⚠ Defina um produto primeiro com /produto [nome]")
                continue
            parts = user_input.split(maxsplit=1)
            try:
                budget = float(parts[1].replace("R$", "").replace(",", ".").strip()) if len(parts) > 1 else 5000.0
            except ValueError:
                budget = 5000.0
            _print_separator()
            print(f"Simulando cenários para {active_product} / R${budget:,.2f}...\n")
            result = forecast(active_product, budget=budget, agent=agent)
            print(result)
            _print_separator()

        elif user_input == "/reset":
            if agent:
                agent.reset()
            print("✓ Histórico da conversa limpo.\n")

        elif user_input in ("/sair", "/exit", "/quit"):
            print("\nAté logo!")
            break

        elif user_input == "/help":
            print(HELP)

        else:
            if not agent:
                print("⚠ Agente indisponível. Configure ANTHROPIC_API_KEY no .env")
                continue
            if not active_product:
                print("⚠ Defina um produto com /produto [nome] antes de conversar.")
                continue
            _print_separator()
            response = agent.chat(user_input)
            print(f"Agente: {response}")
            _print_separator()


if __name__ == "__main__":
    main()
