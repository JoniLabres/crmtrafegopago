-- Queries de alertas do dashboard de tráfego pago
-- Todas as queries retornam linhas somente quando a condição de alerta é verdadeira.

-- ─────────────────────────────────────────────────────────────────────────────
-- q_cpl_acima_meta
-- CPL > 130% da meta por 3+ dias consecutivos
-- Parâmetro: :produto (ex: 'produto-a'), :cpl_meta (ex: 100.0)
-- ─────────────────────────────────────────────────────────────────────────────
-- q_cpl_acima_meta
WITH daily AS (
    SELECT
        date,
        campaign_utm,
        channel,
        produto,
        cpl,
        :cpl_meta AS cpl_meta,
        CASE WHEN cpl > :cpl_meta * 1.30 THEN 1 ELSE 0 END AS above_threshold
    FROM campaigns_daily
    WHERE produto = :produto
      AND date >= CURRENT_DATE - INTERVAL '14 days'
),
grouped AS (
    SELECT
        campaign_utm,
        channel,
        produto,
        date,
        cpl,
        cpl_meta,
        above_threshold,
        SUM(above_threshold) OVER (
            PARTITION BY campaign_utm
            ORDER BY date
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        ) AS consecutive_days_above
    FROM daily
)
SELECT
    campaign_utm,
    channel,
    produto,
    ROUND(AVG(cpl)::numeric, 2)      AS cpl_medio,
    ROUND(MAX(cpl_meta)::numeric, 2) AS cpl_meta,
    MAX(consecutive_days_above)      AS dias_acima
FROM grouped
WHERE consecutive_days_above >= 3
GROUP BY campaign_utm, channel, produto
ORDER BY cpl_medio DESC;


-- ─────────────────────────────────────────────────────────────────────────────
-- q_roas_abaixo_meta
-- ROAS < 3x por 7+ dias
-- Parâmetro: :produto, :roas_meta
-- ─────────────────────────────────────────────────────────────────────────────
WITH daily AS (
    SELECT
        date,
        campaign_utm,
        channel,
        produto,
        roas,
        :roas_meta AS roas_meta,
        CASE WHEN roas < :roas_meta AND spend > 0 THEN 1 ELSE 0 END AS below_threshold
    FROM campaigns_daily
    WHERE produto = :produto
      AND date >= CURRENT_DATE - INTERVAL '14 days'
),
grouped AS (
    SELECT
        campaign_utm,
        channel,
        produto,
        date,
        roas,
        roas_meta,
        SUM(below_threshold) OVER (
            PARTITION BY campaign_utm
            ORDER BY date
            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        ) AS consecutive_days_below
    FROM daily
)
SELECT
    campaign_utm,
    channel,
    produto,
    ROUND(AVG(roas)::numeric, 4)       AS roas_medio,
    ROUND(MAX(roas_meta)::numeric, 2)  AS roas_meta,
    MAX(consecutive_days_below)        AS dias_abaixo
FROM grouped
WHERE consecutive_days_below >= 7
GROUP BY campaign_utm, channel, produto
ORDER BY roas_medio ASC;


-- ─────────────────────────────────────────────────────────────────────────────
-- q_queda_leads
-- Redução > 50% de leads em 24h vs média dos 7 dias anteriores
-- Parâmetro: :produto
-- ─────────────────────────────────────────────────────────────────────────────
WITH hoje AS (
    SELECT
        campaign_utm,
        channel,
        produto,
        SUM(leads) AS leads_hoje
    FROM campaigns_daily
    WHERE date = CURRENT_DATE - INTERVAL '1 day'
      AND produto = :produto
    GROUP BY campaign_utm, channel, produto
),
media_7d AS (
    SELECT
        campaign_utm,
        ROUND(AVG(leads)::numeric, 2) AS leads_media_7d
    FROM campaigns_daily
    WHERE date BETWEEN CURRENT_DATE - INTERVAL '8 days' AND CURRENT_DATE - INTERVAL '2 days'
      AND produto = :produto
    GROUP BY campaign_utm
)
SELECT
    h.campaign_utm,
    h.channel,
    h.produto,
    h.leads_hoje,
    m.leads_media_7d,
    ROUND((1 - h.leads_hoje::numeric / NULLIF(m.leads_media_7d, 0)) * 100, 1) AS queda_pct
FROM hoje h
JOIN media_7d m ON h.campaign_utm = m.campaign_utm
WHERE m.leads_media_7d > 0
  AND h.leads_hoje < m.leads_media_7d * 0.5
ORDER BY queda_pct DESC;


-- ─────────────────────────────────────────────────────────────────────────────
-- q_budget_pace
-- Gasto acumulado no mês vs proporcional do orçamento mensal planejado
-- Parâmetro: :produto, :budget_mensal
-- Alerta quando pace > 110% do planejado
-- ─────────────────────────────────────────────────────────────────────────────
WITH mensal AS (
    SELECT
        campaign_utm,
        channel,
        produto,
        SUM(spend) AS gasto_acumulado,
        DATE_PART('day', CURRENT_DATE)                             AS dias_passados,
        DATE_PART('day', DATE_TRUNC('month', CURRENT_DATE)
            + INTERVAL '1 month' - INTERVAL '1 day')              AS dias_no_mes
    FROM campaigns_daily
    WHERE DATE_TRUNC('month', date) = DATE_TRUNC('month', CURRENT_DATE)
      AND produto = :produto
    GROUP BY campaign_utm, channel, produto
)
SELECT
    campaign_utm,
    channel,
    produto,
    ROUND(gasto_acumulado::numeric, 2)                                   AS gasto_acumulado,
    ROUND((:budget_mensal * dias_passados / dias_no_mes)::numeric, 2)   AS budget_proporcional,
    ROUND((gasto_acumulado / NULLIF(:budget_mensal * dias_passados / dias_no_mes, 0) * 100)::numeric, 1) AS pace_pct
FROM mensal
WHERE gasto_acumulado > :budget_mensal * dias_passados / dias_no_mes * 1.10
ORDER BY pace_pct DESC;
