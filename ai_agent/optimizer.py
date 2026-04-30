import logging
from data_reader import get_current_metrics

logger = logging.getLogger(__name__)


def diagnose(product_name: str, days: int = 7, agent=None) -> str:
    metrics = get_current_metrics(product=product_name, days=days)

    prompt = f"""
Faça um diagnóstico completo de desempenho com base nos dados abaixo.

{metrics}

O diagnóstico deve:
1. Identificar os 3 principais problemas de desempenho (com dados específicos)
2. Para cada problema: causa provável + impacto no negócio
3. Listar ações corretivas priorizadas (P1/P2/P3)
4. Destacar o que está funcionando bem e deve ser escalado
5. Apresentar previsão de melhoria se as ações forem implementadas

Seja direto, use os números dos dados fornecidos e priorize o que tem maior impacto.
""".strip()

    if agent:
        return agent.chat(prompt)

    return f"[Diagnóstico de {product_name} — últimos {days} dias]\n\n{metrics}\n\n(Configure ANTHROPIC_API_KEY para diagnóstico com IA)"


def optimize(product_name: str, days: int = 7, agent=None) -> str:
    metrics = get_current_metrics(product=product_name, days=days)

    prompt = f"""
Com base nos dados de desempenho abaixo, gere recomendações de otimização priorizadas.

{metrics}

Para cada recomendação, inclua:
- Canal e campanha específica
- Ação concreta (ex: aumentar lance em 15%, pausar público X, testar criativo Y)
- Impacto esperado em CPL ou ROAS
- Nível de esforço (baixo/médio/alto)
- Urgência (imediato/esta semana/este mês)

Organize por impacto: comece pelas mudanças com maior retorno e menor esforço.
""".strip()

    if agent:
        return agent.chat(prompt)

    return f"[Otimizações para {product_name}]\n\n{metrics}\n\n(Configure ANTHROPIC_API_KEY para otimizações com IA)"


def forecast(product_name: str, budget: float, days: int = 30, agent=None) -> str:
    metrics = get_current_metrics(product=product_name, days=30)

    prompt = f"""
Com base no histórico de desempenho abaixo, simule 3 cenários para os próximos {days} dias
com budget de R${budget:,.2f}.

{metrics}

Para cada cenário (conservador, base, agressivo), apresente:
- Premissas (CPL, ROAS, CTR esperados)
- Leads estimados
- Receita estimada
- ROAS projetado
- CAC estimado
- Distribuição de budget por canal

Finalize com a recomendação de qual cenário adotar e por quê.
""".strip()

    if agent:
        return agent.chat(prompt)

    return f"[Previsão para {product_name} — {days} dias / R${budget:,.2f}]\n\n(Configure ANTHROPIC_API_KEY para previsão com IA)"
