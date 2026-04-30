# tasks.md — Tarefas por Fase

> Execute as fases em ordem. Só avance quando o critério de aceite da fase anterior estiver validado.
> Ao iniciar cada fase, diga: **"Executar Fase X"** e o Claude Code assumirá o contexto do CLAUDE.md.

---

## FASE 0 — Setup do Projeto

**Critério de aceite:** estrutura de diretórios criada, `.env.example` gerado, dependências instaladas.

```
Crie a estrutura completa de diretórios do projeto conforme definida no CLAUDE.md.
Depois crie os seguintes arquivos base:

1. requirements.txt com todas as dependências necessárias:
   - requests, python-dotenv, pandas, psycopg2-binary
   - google-ads, facebook-business, linkedin-api
   - anthropic, slack-sdk, schedule
   - pytest, black, isort

2. .env.example com TODAS as variáveis de ambiente listadas no CLAUDE.md,
   sem valores — apenas os nomes das variáveis com comentários explicativos.

3. .gitignore incluindo: .env, __pycache__, *.pyc, outputs/, *.log

4. config/utm_taxonomy.json com a taxonomia oficial de UTMs definida no CLAUDE.md,
   estruturada como JSON com listas de valores válidos para cada parâmetro.

5. config/products.json com template de memória de produto para o agente de IA.
   Inclua campos: nome, posicionamento, icp, ticket_medio, roas_meta, cpl_meta,
   canais_ativos, historico_criativos, sazonalidade, concorrentes, objecoes.

Ao finalizar, liste todos os arquivos criados e confirme que a estrutura bate
com o CLAUDE.md.
```

---

## FASE 1 — UTM Engine e Validação

**Critério de aceite:** gerador de UTMs funcionando, validador rejeitando UTMs fora do padrão, testes passando.

```
Implemente o UTM Engine completo com dois scripts:

1. tracking/utm_builder.py
   - Função build_utm(source, medium, campaign_parts, content, term)
   - campaign_parts = dict com: produto, funil, objetivo, ano_mes
   - Valida cada campo contra a taxonomia em config/utm_taxonomy.json
   - Retorna a URL completa com parâmetros
   - Lança ValueError com mensagem clara se algum valor for inválido
   - Exemplo de uso no __main__: gerar 5 UTMs de exemplo para cada canal

2. tracking/utm_validator.py
   - Função validate_utm(url) → retorna dict com status e erros encontrados
   - Verifica: todos os 5 parâmetros presentes, valores dentro da taxonomia,
     formato do utm_campaign (produto_funil_objetivo_ano-mes), sem maiúsculas,
     sem acentos, sem espaços
   - Função validate_batch(csv_path) → valida planilha inteira de UTMs

3. tests/test_utm.py
   - Testes para build_utm: caso feliz, campo inválido, campo faltando
   - Testes para validate_utm: UTM válida, UTM com source errado,
     UTM com campaign mal formatado
   - Execute: pytest tests/test_utm.py -v

Ao finalizar, mostre o resultado do pytest e 3 exemplos de UTMs geradas.
```

---

## FASE 2 — Configuração do GTM

**Critério de aceite:** arquivo JSON exportável para GTM gerado com todas as tags, triggers e variáveis necessárias.

```
Gere o arquivo tracking/gtm_config.json com a configuração completa do GTM
pronta para importar no container.

Deve incluir:

VARIÁVEIS (Variables):
- URL Query: utm_source, utm_medium, utm_campaign, utm_content, utm_term
- Data Layer: form_id, page_path, lead_value
- Cookie: _fbp (Facebook), _ga (Google Analytics)

TRIGGERS (Triggers):
- Pageview: All Pages
- Form Submit: todos os formulários (trigger tipo "Form Submission")
- Custom Event: lead_generated
- Custom Event: purchase_completed

TAGS (Tags):
- GA4 Configuration (com Measurement ID como variável)
- GA4 Event: lead_generated (com todos os parâmetros UTM)
- GA4 Event: purchase_completed
- Meta Pixel: PageView
- Meta Pixel: Lead (com Event ID para deduplicação CAPI)
- LinkedIn Insight Tag
- TikTok Pixel: PageView
- TikTok Pixel: Lead

Para cada tag, inclua os campos necessários referenciando as variáveis criadas.
Use {{Variable Name}} para referenciar variáveis dentro das tags.

Também gere tracking/gtm_setup_guide.md com o passo a passo para importar
o JSON no GTM e validar cada tag no Preview Mode.
```

---

## FASE 3 — HubSpot: Propriedades e Workflows

**Critério de aceite:** script executa sem erros com credenciais válidas, propriedades criadas no HubSpot, workflow de propagação de UTM funcionando.

```
Implemente a integração completa com o HubSpot:

1. hubspot/hubspot_client.py
   - Classe HubSpotClient com autenticação via HUBSPOT_API_KEY do .env
   - Métodos base: get, post, patch com retry (3x, exponential backoff)
   - Logging de todas as requisições
   - Tratamento de rate limit (429) com wait automático

2. hubspot/create_properties.py
   - Cria todas as propriedades customizadas definidas no CLAUDE.md
   - Para Contato: utm_source, utm_medium, utm_campaign, utm_content,
     utm_term, produto_interesse, canal_primeiro_toque
   - Para Negócio: utm_campaign_origem, canal_origem, produto_negocio,
     roas_calculado, cac_canal
   - Verifica se a propriedade já existe antes de criar (idempotente)
   - Imprime resumo: quantas criadas, quantas já existiam

3. hubspot/create_workflows.py
   Cria 3 workflows via API:

   Workflow 1 — "Captura UTM no Contato":
   - Trigger: formulário submetido
   - Ações: copiar cookie/hidden field de cada UTM para a propriedade correspondente

   Workflow 2 — "Propagar UTM para Negócio":
   - Trigger: negócio criado
   - Ações: copiar utm_campaign, canal_origem e produto_interesse
     do Contato associado para o Negócio

   Workflow 3 — "Lead Scoring por Canal":
   - Trigger: contato criado ou atualizado
   - Ações: aumentar score baseado em regras:
     +20 se utm_source = google, +15 se produto_interesse preenchido,
     +10 se utm_medium = paid_search, +5 se page_path contém /preco

4. tests/test_hubspot.py
   - Testes com mock da API (não chamar API real nos testes)
   - Testar: criação de propriedade, idempotência, retry em 429

Execute create_properties.py no final e mostre o resumo de propriedades criadas.
```

---

## FASE 4 — Pipeline de Dados

**Critério de aceite:** todos os pullers executando, dados chegando no PostgreSQL, join de UTMs funcionando.

```
Implemente o pipeline completo de coleta e consolidação de dados:

1. data_pipeline/base_puller.py
   - Classe base BasePuller com: auth(), fetch(date_from, date_to), normalize()
   - Método normalize() converte para schema padrão:
     {date, channel, campaign_utm, campaign_name, spend, impressions,
      clicks, leads, revenue, roas, cpl, cpc, ctr}

2. data_pipeline/meta_ads_pull.py
   - Herda BasePuller
   - Usa facebook-business SDK com META_ACCESS_TOKEN e META_AD_ACCOUNT_ID
   - Puxa por campanha: spend, impressions, clicks, leads (evento Lead)
   - Extrai utm_campaign do campo campaign_name ou URL parameter
   - Normaliza para schema padrão

3. data_pipeline/google_ads_pull.py
   - Herda BasePuller
   - Usa google-ads SDK com GOOGLE_ADS_DEVELOPER_TOKEN
   - Puxa: cost, impressions, clicks, conversions por campanha
   - Extrai utm_campaign do tracking template da campanha

4. data_pipeline/linkedin_ads_pull.py
   - Herda BasePuller
   - Usa LINKEDIN_ACCESS_TOKEN
   - Puxa: spend, impressions, clicks, leads por campanha
   - Extrai utm_campaign do tracking URL

5. data_pipeline/hubspot_pull.py
   - Puxa todos os contatos criados no período com campos UTM
   - Puxa todos os negócios fechados no período com utm_campaign_origem e amount
   - Retorna dois DataFrames: contacts_df e deals_df

6. data_pipeline/join_utm.py
   - Função join_campaign_data(ads_df, deals_df)
   - Join por utm_campaign entre gastos de Ads e negócios do HubSpot
   - Calcula: ROAS = revenue / spend, CAC = spend / deals_count,
     CPL = spend / leads
   - Retorna DataFrame consolidado com uma linha por campanha

7. dashboard/schema.sql
   - Tabela campaigns_daily: todos os campos do schema padrão + produto extraído do utm_campaign
   - Tabela alerts_log: registro de todos os alertas disparados
   - Índices em: date, channel, campaign_utm, produto

8. data_pipeline/load_database.py
   - Carrega DataFrame consolidado no PostgreSQL (upsert por date + campaign_utm)
   - Extrai produto do utm_campaign (primeiro segmento antes do _)
   - Roda todo o pipeline: puxa de todos os canais → join → carrega no banco

Gere também um script data_pipeline/run_pipeline.sh que executa tudo em sequência
e pode ser agendado via cron diariamente às 06:00.
```

---

## FASE 5 — Sistema de Alertas

**Critério de aceite:** alertas disparando para o Slack com as condições definidas no CLAUDE.md, testados com dados simulados.

```
Implemente o sistema de alertas automáticos:

1. dashboard/queries.sql
   Adicione as queries de alertas:
   - q_cpl_acima_meta: CPL > 130% da meta por 3+ dias consecutivos
   - q_roas_abaixo_meta: ROAS < 3x por 7+ dias
   - q_queda_leads: redução > 50% de leads em 24h vs média dos 7 dias anteriores
   - q_frequencia_alta: (placeholder — virá do Meta Ads API diretamente)
   - q_budget_pace: gasto até hoje vs proporcional do mês

2. dashboard/alerts.py
   - Classe AlertSystem com SLACK_WEBHOOK_URL do .env
   - Método check_all() — roda todas as queries e dispara alertas encontrados
   - Método send_slack(channel, message, severity) — formata mensagem com
     emoji por severidade (🔴 crítico, 🟡 atenção, 🟢 ok)
   - Método log_alert(alert_type, details) — grava em alerts_log no banco
   - Configuração de metas por produto em config/alert_thresholds.json:
     {produto: {cpl_meta, roas_meta, leads_meta_diario}}

3. dashboard/scheduler.py
   - Usa schedule library para rodar check_all() a cada hora
   - Roda o pipeline completo (load_database.py) todo dia às 06:00
   - Log de cada execução em logs/scheduler.log

4. tests/test_alerts.py
   - Testa cada condição de alerta com dados simulados
   - Verifica que o alerta correto é gerado para cada condição
   - Usa mock do Slack para não enviar mensagens reais nos testes

Execute tests/test_alerts.py e mostre os resultados.
Gere também config/alert_thresholds.json com valores de exemplo para 3 produtos.
```

---

## FASE 6 — Agente de IA

**Critério de aceite:** agente respondendo com contexto de negócio, gerando campanhas com UTMs corretas, diagnósticos baseados em dados reais.

```
Implemente o agente de IA completo:

1. ai_agent/memory_loader.py
   - Carrega config/products.json para o produto ativo
   - Função get_product_context(product_name) → string formatada para o system prompt
   - Inclui: posicionamento, ICP, histórico de criativos top, benchmarks por canal,
     sazonalidade, objeções mais comuns

2. ai_agent/data_reader.py
   - Função get_current_metrics(product=None, days=30) → string com métricas atuais
   - Puxa do PostgreSQL: ROAS, CPL, CPC, CTR por canal dos últimos N dias
   - Inclui alertas ativos do alerts_log
   - Formata como texto estruturado para o contexto do agente

3. ai_agent/agent.py
   - Classe CampaignAgent com ANTHROPIC_API_KEY do .env
   - System prompt dinâmico montado com:
     a) Identidade: especialista em tráfego pago com 10 anos de experiência
     b) Contexto do produto: saído do memory_loader
     c) Dados atuais: saídos do data_reader
     d) Regras de comportamento: sempre briefing, sempre UTMs, sempre 3 cenários
   - Método chat(message, product_name) → resposta do agente
   - Histórico de conversa mantido na sessão
   - Modelo: claude-sonnet-4-20250514

4. ai_agent/briefing.py
   - Função run_briefing() → interativo no terminal
   - Faz 5 perguntas essenciais: objetivo, produto, canal, budget, prazo
   - Valida respostas e pede clarificação se necessário
   - Retorna dict com o briefing completo para passar ao agente

5. ai_agent/campaign_creator.py
   - Função create_campaign(briefing_dict, product_name) → string com plano completo
   - Inclui: objetivo, segmentação, budget por canal, UTMs geradas via utm_builder,
     criativos sugeridos, cronograma
   - Valida UTMs geradas antes de retornar

6. ai_agent/optimizer.py
   - Função diagnose(product_name, days=7) → relatório de diagnóstico
   - Lê métricas do banco, identifica campanhas abaixo da meta,
     compara com benchmarks históricos, sugere ações priorizadas

7. ai_agent/cli.py
   - Interface de linha de comando interativa
   - Comandos disponíveis:
     /produto [nome] — muda o produto ativo
     /briefing — inicia módulo de briefing
     /criar — cria nova campanha (chama briefing primeiro)
     /diagnostico — roda diagnóstico do produto ativo
     /otimizar — sugere otimizações baseadas em dados atuais
     /sair — encerra o agente

Ao finalizar, execute: python ai_agent/cli.py
Teste com: /produto produto-a → /diagnostico → /criar
Mostre a resposta do agente para cada comando.
```

---

## FASE 7 — Documentação Final e Testes de Integração

**Critério de aceite:** todos os testes passando, README.md completo, ciclo end-to-end funcionando.

```
Finalize o projeto com documentação e testes de integração:

1. tests/test_integration.py
   Teste do ciclo completo end-to-end (com dados mockados):
   - Gerar UTM válida → validar → salvar no banco
   - Criar contato no HubSpot com UTM → criar negócio → verificar propagação
   - Rodar pipeline de dados → verificar dados no banco → verificar join
   - Disparar condição de alerta → verificar mensagem no Slack (mock)
   - Criar briefing → gerar campanha → verificar UTMs corretas na saída

2. README.md completo com:
   - Visão geral da arquitetura (pode referenciar o CLAUDE.md)
   - Pré-requisitos (Python 3.11+, PostgreSQL, credenciais de API)
   - Setup rápido em 5 passos
   - Como executar cada fase
   - Como usar o agente de IA (comandos disponíveis)
   - Como configurar os alertas
   - Troubleshooting: erros comuns e soluções

3. scripts/health_check.py
   - Verifica se todas as variáveis de ambiente estão configuradas
   - Testa conexão com cada serviço: HubSpot, PostgreSQL, Slack, Claude API
   - Testa conexão com cada canal de Ads (Meta, Google, LinkedIn, TikTok)
   - Imprime relatório de status: ✅ ok / ❌ falhou / ⚠️ não configurado

Execute na ordem:
1. python scripts/health_check.py  → confirmar que tudo está conectado
2. pytest tests/ -v --tb=short      → todos os testes devem passar
3. python data_pipeline/load_database.py  → pipeline completo
4. python ai_agent/cli.py           → agente funcionando

Mostre o resultado de cada execução.
```

---

## Como Usar Este Arquivo

### Iniciar uma fase
Cole no Claude Code:
```
Leia o CLAUDE.md e o tasks.md, depois execute a Fase X conforme descrito.
```

### Retomar após pausa
Cole no Claude Code:
```
Leia o CLAUDE.md. A Fase X foi concluída. Continue com a Fase Y.
```

### Pedir ajuda em um script específico
Cole no Claude Code:
```
Leia o CLAUDE.md. Estou com erro no script [nome do arquivo]:
[cole o erro aqui]
Corrija mantendo as convenções do projeto.
```

### Adicionar um novo produto ao agente
Cole no Claude Code:
```
Leia o CLAUDE.md e config/products.json.
Adicione um novo produto chamado [nome] com as seguintes informações:
[descreva o produto, ICP, ticket médio, canais, etc.]
```

---

## Checklist de Validação por Fase

- [ ] **Fase 0** — Estrutura de diretórios criada, `.env.example` completo
- [ ] **Fase 1** — `pytest tests/test_utm.py` passando 100%
- [ ] **Fase 2** — `gtm_config.json` gerado com todas as tags e variáveis
- [ ] **Fase 3** — Propriedades criadas no HubSpot, workflows ativos
- [ ] **Fase 4** — Dados chegando no PostgreSQL, join funcionando
- [ ] **Fase 5** — Alertas disparando para Slack, testes passando
- [ ] **Fase 6** — Agente respondendo com contexto, UTMs corretas nas campanhas
- [ ] **Fase 7** — Todos os testes passando, health check verde em tudo
