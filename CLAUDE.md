# CLAUDE.md — Estrutura de Tráfego Pago com IA

## Contexto do Projeto

Este projeto implementa uma estrutura completa de tráfego pago com dashboard unificado e agente de IA. São 5 camadas interdependentes que devem ser implementadas em ordem.

### Princípio central
O `utm_campaign` é a chave que une gasto de mídia (APIs dos canais) com receita (HubSpot). Sem ele gravado corretamente, ROAS é incalculável.

---

## Arquitetura das 5 Camadas

```
[Canais de Ads] → [GTM + UTM Engine + GA4] → [HubSpot CRM] → [Dashboard] → [Agente de IA]
     Meta              utm_source                Contato         Looker         Claude API
     Google            utm_medium                Negócio         Metabase       Memória
     LinkedIn          utm_campaign              Workflows       Alertas        6 módulos
     TikTok            utm_content               Lead Score
     Programática      utm_term
```

---

## Stack de Ferramentas

| Ferramenta | Função | Auth necessária |
|---|---|---|
| Google Tag Manager | Hub de tags e eventos | GTM_ACCOUNT_ID, GTM_CONTAINER_ID |
| Google Analytics 4 | Analytics e conversões | GA4_MEASUREMENT_ID, GA4_API_SECRET |
| Meta Ads | Canal social | META_ACCESS_TOKEN, META_AD_ACCOUNT_ID |
| Google Ads | Canal search | GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_CUSTOMER_ID |
| LinkedIn Ads | Canal B2B | LINKEDIN_ACCESS_TOKEN, LINKEDIN_AD_ACCOUNT_ID |
| TikTok Ads | Canal topo | TIKTOK_ACCESS_TOKEN, TIKTOK_AD_ACCOUNT_ID |
| HubSpot | CRM + Pipeline | HUBSPOT_API_KEY |
| Looker Studio / Metabase | Dashboard | METABASE_URL, METABASE_TOKEN |
| Claude API | Agente de IA | ANTHROPIC_API_KEY |
| Slack | Alertas | SLACK_WEBHOOK_URL |
| PostgreSQL | Banco de dados consolidado | DATABASE_URL |

---

## Convenções de Código

- Linguagem principal: **Python 3.11+**
- Gerenciador de dependências: **pip** com `requirements.txt`
- Variáveis de ambiente: sempre via `.env` (nunca hardcoded)
- Logs: sempre usar `logging` com nível INFO por padrão
- Erros de API: retry com exponential backoff (máx 3 tentativas)
- Encoding: UTF-8 em todos os arquivos
- Datas: sempre ISO 8601 (YYYY-MM-DD)

---

## Taxonomia de UTM (IMUTÁVEL)

```
utm_source   → meta | google | linkedin | tiktok | programatica
utm_medium   → paid_social | paid_search | display | video | native
utm_campaign → [produto]_[funil]_[objetivo]_[ano-mes]
utm_content  → [tipo]_[angulo]_[variacao]
utm_term     → keyword-exata | nome-do-publico
```

**Regras:**
- Tudo minúsculo
- Sem acento, sem espaço
- Separador: hífen dentro do campo, underscore entre campos
- Exemplos válidos:
  - `produto-a_topo_leads_2025-05`
  - `video_dor_v1`
  - `lookalike-clientes`

---

## Estrutura de Diretórios do Projeto

```
trafego-pago/
├── CLAUDE.md                  ← este arquivo
├── tasks.md                   ← tarefas por fase
├── .env.example               ← template de variáveis
├── requirements.txt           ← dependências Python
├── config/
│   ├── utm_taxonomy.json      ← taxonomia oficial de UTMs
│   └── products.json          ← memória de produtos para o agente
├── tracking/
│   ├── gtm_config.json        ← configuração exportável para GTM
│   ├── utm_builder.py         ← gerador e validador de UTMs
│   └── utm_validator.py       ← testa se UTMs seguem a taxonomia
├── hubspot/
│   ├── create_properties.py   ← cria propriedades customizadas
│   ├── create_workflows.py    ← cria workflows de UTM e lead score
│   └── hubspot_client.py      ← cliente reutilizável da API
├── data_pipeline/
│   ├── meta_ads_pull.py       ← puxa dados da API do Meta Ads
│   ├── google_ads_pull.py     ← puxa dados da API do Google Ads
│   ├── linkedin_ads_pull.py   ← puxa dados da API do LinkedIn Ads
│   ├── tiktok_ads_pull.py     ← puxa dados da API do TikTok Ads
│   ├── ga4_pull.py            ← puxa dados do GA4
│   ├── hubspot_pull.py        ← puxa leads e negócios do HubSpot
│   ├── join_utm.py            ← join de gasto de Ads com CRM por UTM
│   └── load_database.py       ← carrega dados no PostgreSQL
├── dashboard/
│   ├── schema.sql             ← schema do banco consolidado
│   ├── queries.sql            ← queries principais do dashboard
│   └── alerts.py              ← sistema de alertas automáticos
└── ai_agent/
    ├── agent.py               ← agente principal com Claude API
    ├── memory_loader.py       ← carrega memória de negócio por produto
    ├── data_reader.py         ← lê métricas do dashboard em tempo real
    ├── briefing.py            ← módulo de briefing inteligente
    ├── campaign_creator.py    ← módulo de criação de campanhas
    └── optimizer.py           ← módulo de otimização e diagnóstico
```

---

## Propriedades Customizadas do HubSpot

### Objeto Contato
| Campo | Tipo | Descrição |
|---|---|---|
| utm_source | single_line_text | Canal de origem |
| utm_medium | single_line_text | Tipo de mídia |
| utm_campaign | single_line_text | Campanha — chave de atribuição |
| utm_content | single_line_text | Variação de criativo |
| utm_term | single_line_text | Palavra-chave ou público |
| produto_interesse | enumeration | Produto de interesse do lead |
| canal_primeiro_toque | single_line_text | Primeiro canal de contato |

### Objeto Negócio
| Campo | Tipo | Descrição |
|---|---|---|
| utm_campaign_origem | single_line_text | Copiado do Contato via Workflow |
| canal_origem | enumeration | Canal que originou o negócio |
| produto_negocio | enumeration | Produto do negócio |
| roas_calculado | number | Receita / Custo da campanha |
| cac_canal | number | Custo de aquisição por canal |

---

## Sistema de Alertas

| Alerta | Condição | Canal |
|---|---|---|
| CPL alto | CPL > 130% da meta por 3+ dias | Slack #alertas-midia |
| ROAS baixo | ROAS < 3x por 7+ dias | Slack #alertas-midia |
| Queda de leads | Redução > 50% em 24h | Slack #alertas-critico |
| Frequência alta | Frequência Meta > 4 por 5+ dias | Slack #alertas-midia |
| Budget no limite | Pace > 110% do planejado | Slack #alertas-midia |

---

## Agente de IA — Comportamento Esperado

O agente SEMPRE deve:
1. Carregar a memória do produto ativo antes de qualquer resposta
2. Fazer briefing (mínimo 3 perguntas) antes de criar campanhas
3. Incluir UTMs completas em toda campanha gerada
4. Apresentar 3 cenários (conservador, base, agressivo) em estratégias
5. Basear diagnósticos em dados reais do dashboard, não em feeling
6. Registrar o resultado de cada campanha na memória do produto

### 6 módulos do agente
1. **Briefing inteligente** — coleta contexto antes de agir
2. **Criação de campanhas** — estrutura, segmentação, budget, UTMs
3. **Geração de criativos** — headlines, copies, CTAs por canal
4. **Diagnóstico** — identifica problemas e causas com dados reais
5. **Otimização** — recomenda ajustes de lances, públicos, budget
6. **Previsão** — simula cenários de ROAS, CAC e receita

---

## Variáveis de Ambiente Necessárias

Copie `.env.example` para `.env` e preencha antes de executar qualquer script:

```bash
cp .env.example .env
```

Nunca commitar o `.env`. Ele está no `.gitignore`.

---

## Como Executar

```bash
# 1. Instalar dependências
pip install -r requirements.txt

# 2. Configurar variáveis
cp .env.example .env
# editar .env com as credenciais

# 3. Executar por fase (ver tasks.md)
python tracking/utm_builder.py
python hubspot/create_properties.py
python data_pipeline/meta_ads_pull.py
python ai_agent/agent.py
```

---

## Regras para o Claude Code

- **Nunca hardcodar** credenciais, tokens ou IDs
- **Sempre criar** `.env.example` com as variáveis necessárias (sem valores)
- **Sempre adicionar** tratamento de erro e retry em chamadas de API
- **Sempre validar** UTMs contra a taxonomia antes de gravar no banco
- **Sempre logar** o resultado de cada operação
- **Sempre criar** o arquivo de saída em `/outputs/` quando gerar relatórios
- Ao finalizar cada fase, **rodar os testes** antes de passar para a próxima
- Se uma API retornar erro 401/403, **parar e pedir** as credenciais corretas
