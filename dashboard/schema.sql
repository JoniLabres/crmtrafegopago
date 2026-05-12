-- Schema consolidado do dashboard de tráfego pago

CREATE TABLE IF NOT EXISTS campaigns_daily (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL,
    channel         VARCHAR(50) NOT NULL,
    campaign_utm    VARCHAR(255) NOT NULL,
    campaign_name   VARCHAR(255),
    produto         VARCHAR(100),
    spend           NUMERIC(12, 2) DEFAULT 0,
    impressions     BIGINT DEFAULT 0,
    clicks          BIGINT DEFAULT 0,
    leads           INTEGER DEFAULT 0,
    revenue         NUMERIC(12, 2) DEFAULT 0,
    roas            NUMERIC(8, 4) DEFAULT 0,
    cpl             NUMERIC(10, 2) DEFAULT 0,
    cpc             NUMERIC(10, 4) DEFAULT 0,
    ctr             NUMERIC(8, 4) DEFAULT 0,
    mqls            INTEGER DEFAULT 0,
    sqls            INTEGER DEFAULT 0,
    sals            INTEGER DEFAULT 0,
    deals_closed    INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE (date, channel, campaign_utm, produto)
);

CREATE INDEX IF NOT EXISTS idx_campaigns_date         ON campaigns_daily (date);
CREATE INDEX IF NOT EXISTS idx_campaigns_channel      ON campaigns_daily (channel);
CREATE INDEX IF NOT EXISTS idx_campaigns_utm          ON campaigns_daily (campaign_utm);
CREATE INDEX IF NOT EXISTS idx_campaigns_produto      ON campaigns_daily (produto);

-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS alerts_log (
    id              SERIAL PRIMARY KEY,
    alert_type      VARCHAR(100) NOT NULL,
    channel         VARCHAR(50),
    campaign_utm    VARCHAR(255),
    produto         VARCHAR(100),
    severity        VARCHAR(20) NOT NULL CHECK (severity IN ('critico', 'atencao', 'ok')),
    message         TEXT NOT NULL,
    details         JSONB,
    sent_at         TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_type     ON alerts_log (alert_type);
CREATE INDEX IF NOT EXISTS idx_alerts_sent_at  ON alerts_log (sent_at);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts_log (severity);

-- ─────────────────────────────────────────────────────────────────────────────
-- Config store: replaces local JSON files on Vercel (read-only filesystem)
-- Keys: accounts, products, thresholds, ltv_metrics

CREATE TABLE IF NOT EXISTS ixc_config (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
