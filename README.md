# CRM Tráfego Pago com IA

Sistema completo de tráfego pago com dashboard unificado e agente de IA para gestão, diagnóstico e criação de campanhas.

## Arquitetura

```
[Canais de Ads] → [GTM + UTM Engine + GA4] → [HubSpot CRM] → [Dashboard] → [Agente IA]
     Meta              utm_source                Contato         PostgreSQL    Claude API
     Google            utm_medium                Negócio         Alertas       6 módulos
     LinkedIn          utm_campaign              Workflows       Slack
     TikTok            utm_content               Lead Score
```

## Pré-requisitos

- Python 3.11+
- PostgreSQL 14+
- Credenciais das APIs (ver `.env.example`)

## Setup em 5 passos

**1. Clone e instale dependências**
```bash
git clone https://github.com/JoniLabres/crmtrafegopago.git
cd crmtrafegopago
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**2. Configure as variáveis de ambiente**
```bash
cp .env.example .env
# Edite .env com suas credenciais de API
```

**3. Crie o banco de dados**
```bash
psql -U postgres -c "CREATE DATABASE trafegopago;"
psql -U postgres -d trafegopago -f dashboard/schema.sql
```

**4. Execute o health check**
```bash
python scripts/health_check.py
```

**5. Rode o pipeline inicial**
```bash
cd data_pipeline
python load_database.py
```

---

## Como executar cada fase

### Fase 1 — UTM Engine
```bash
# Gerar UTMs de exemplo
python tracking/utm_builder.py

# Validar uma URL
python tracking/utm_validator.py

# Rodar testes
.venv/bin/python -m pytest tests/test_utm.py -v
```

### Fase 2 — GTM
Importe `tracking/gtm_config.json` no GTM:
**Admin → Importar Container → Mesclar → substitua os 4 IDs de constantes**

Consulte o guia completo em `tracking/gtm_setup_guide.md`.

### Fase 3 — HubSpot
```bash
cd hubspot
python create_properties.py   # Cria 12 propriedades customizadas
python create_workflows.py    # Cria 3 workflows de UTM e lead scoring
```

### Fase 4 — Pipeline de Dados
```bash
# Execução manual
cd data_pipeline
python load_database.py

# Agendar via cron (diário às 06:00)
crontab -e
# Adicione: 0 6 * * * /caminho/trafego-pago/data_pipeline/run_pipeline.sh
```

### Fase 5 — Alertas
```bash
# Verificar alertas manualmente
cd dashboard
python -c "from alerts import AlertSystem; AlertSystem().check_all()"

# Iniciar scheduler (alertas a cada hora + pipeline às 06:00)
python dashboard/scheduler.py
```

### Fase 6 — Agente de IA
```bash
cd ai_agent
python cli.py
```

---

## Como usar o agente de IA

```
/produto produto-a     → ativa o produto e carrega contexto + métricas
/briefing              → inicia coleta de briefing (5 perguntas)
/criar                 → gera plano de campanha com UTMs validadas
/diagnostico           → diagnóstico dos últimos 7 dias com dados reais
/otimizar              → recomendações priorizadas por impacto
/prever 5000           → simula 3 cenários com budget de R$5.000
/reset                 → limpa histórico da conversa
/sair                  → encerra
```

Qualquer outra mensagem inicia uma conversa livre com o agente, que sempre usa o contexto do produto ativo e as métricas em tempo real.

---

## Taxonomia de UTM (imutável)

```
utm_source   → meta | google | linkedin | tiktok | programatica
utm_medium   → paid_social | paid_search | display | video | native
utm_campaign → [produto]_[funil]_[objetivo]_[ano-mes]
utm_content  → [tipo]_[angulo]_[variacao]
utm_term     → keyword-exata | nome-do-publico
```

**Regras:** tudo minúsculo, sem acento, sem espaço, hífen dentro do campo, underscore entre campos.

---

## Como configurar os alertas

Edite `config/alert_thresholds.json` com as metas de cada produto:

```json
{
  "produtos": {
    "meu-produto": {
      "cpl_meta": 120.0,
      "roas_meta": 3.5,
      "leads_meta_diario": 8,
      "budget_mensal": 6000.0
    }
  }
}
```

Alertas disponíveis:
| Alerta | Condição | Canal Slack |
|---|---|---|
| CPL alto | CPL > 130% da meta por 3+ dias | #alertas-midia |
| ROAS baixo | ROAS < meta por 7+ dias | #alertas-midia |
| Queda de leads | Redução > 50% em 24h | #alertas-critico |
| Budget pace | Pace > 110% do proporcional | #alertas-midia |

---

## Adicionar um novo produto ao agente

Edite `config/products.json` e adicione um novo objeto no array `produtos` seguindo a estrutura do template. Depois use `/produto nome-do-produto` no CLI.

---

## Troubleshooting

| Erro | Causa | Solução |
|---|---|---|
| `HUBSPOT_API_KEY não configurada` | Variável ausente no `.env` | Copie `.env.example` e preencha |
| `ValueError: utm_source inválido` | Source fora da taxonomia | Use: meta, google, linkedin, tiktok, programatica |
| `PermissionError: 401` | Token de API expirado | Gere novo token na plataforma |
| `DATABASE_URL não configurada` | Banco não configurado | Preencha `DATABASE_URL` no `.env` |
| `ModuleNotFoundError` | Dependência não instalada | `pip install -r requirements.txt` |
| Pipeline sem dados | Credenciais de Ads ausentes | Configure as variáveis de cada canal no `.env` |

---

## Estrutura do projeto

```
trafego-pago/
├── config/              → taxonomia UTM, produtos, thresholds de alerta
├── tracking/            → UTM builder, validator, configuração GTM
├── hubspot/             → cliente, propriedades e workflows
├── data_pipeline/       → pullers por canal, join UTM, carga no banco
├── dashboard/           → schema SQL, queries de alerta, scheduler
├── ai_agent/            → agente Claude, 6 módulos, CLI interativo
├── tests/               → testes unitários e de integração
├── scripts/             → health check
└── outputs/             → relatórios gerados (gitignored)
```

---

## Testes

```bash
# Todos os testes
.venv/bin/python -m pytest tests/ -v --tb=short

# Por módulo
.venv/bin/python -m pytest tests/test_utm.py -v
.venv/bin/python -m pytest tests/test_hubspot.py -v
.venv/bin/python -m pytest tests/test_alerts.py -v
.venv/bin/python -m pytest tests/test_integration.py -v
```
