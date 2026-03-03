-- ============================================================================
-- Integration Health Monitor — PostgreSQL Schema
--
-- PM-authored schema design. Maps directly to the Python data models in the
-- src/ modules. In production, this is what backs the in-memory stores used
-- in the prototypes.
--
-- Design decisions:
--   - Partitioned by provider_id where volume is high (events, health snapshots)
--   - Timestamped everything for time-series queries and trend analysis
--   - Kept provider configuration in the DB (not just env vars) so the
--     registry is queryable and auditable
--   - Separate tables for webhook events vs. API call events because they
--     have different schemas and different query patterns
-- ============================================================================


-- ============================================================================
-- 1. PROVIDER REGISTRY
-- Central catalog of all third-party API providers.
-- Source of truth for what integrations exist and how they're configured.
-- ============================================================================

CREATE TABLE providers (
    provider_id         VARCHAR(64) PRIMARY KEY,
    name                VARCHAR(128) NOT NULL,
    category            VARCHAR(64) NOT NULL,       -- identity_verification, financial_connectivity, etc.
    blast_radius        VARCHAR(8) NOT NULL,         -- p0, p1, p2, p3
    data_flow_pattern   VARCHAR(32) NOT NULL,        -- sync, webhook, polling, file_batch, bidirectional
    auth_method         VARCHAR(32) NOT NULL,
    base_url            VARCHAR(512) NOT NULL,
    api_version         VARCHAR(64),
    contract_owner      VARCHAR(128),
    technical_contact   VARCHAR(256),
    account_manager     VARCHAR(256),
    integration_date    TIMESTAMP NOT NULL,
    last_contract_review TIMESTAMP,
    notes               TEXT,
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_providers_category ON providers(category);
CREATE INDEX idx_providers_blast_radius ON providers(blast_radius);
CREATE INDEX idx_providers_active ON providers(is_active);


-- Provider endpoints (each provider has 1+ API endpoints we consume)
CREATE TABLE provider_endpoints (
    id                  SERIAL PRIMARY KEY,
    provider_id         VARCHAR(64) REFERENCES providers(provider_id),
    path                VARCHAR(512) NOT NULL,
    method              VARCHAR(8) NOT NULL,
    description         TEXT,
    is_critical         BOOLEAN DEFAULT FALSE,
    expected_latency_ms INTEGER,
    timeout_ms          INTEGER NOT NULL,
    rate_limit_per_min  INTEGER,
    is_idempotent       BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_endpoints_provider ON provider_endpoints(provider_id);


-- SLA definitions (contractual guarantees per provider)
CREATE TABLE provider_slas (
    provider_id                 VARCHAR(64) PRIMARY KEY REFERENCES providers(provider_id),
    guaranteed_uptime_pct       NUMERIC(6,3) NOT NULL,      -- e.g., 99.950
    max_response_time_ms        INTEGER NOT NULL,
    webhook_delivery_pct        NUMERIC(6,3) DEFAULT 0,
    support_response_minutes    INTEGER,
    deprecation_notice_days     INTEGER,
    credit_eligible             BOOLEAN DEFAULT FALSE,
    contract_start_date         DATE,
    contract_end_date           DATE,
    updated_at                  TIMESTAMP DEFAULT NOW()
);


-- Webhook configuration per provider
CREATE TABLE webhook_configs (
    provider_id             VARCHAR(64) PRIMARY KEY REFERENCES providers(provider_id),
    endpoint_path           VARCHAR(256) NOT NULL,
    events_subscribed       TEXT[] NOT NULL,         -- Array of event types
    signature_header        VARCHAR(128) NOT NULL,
    signature_algorithm     VARCHAR(32) NOT NULL,
    retry_policy            TEXT,
    max_retry_attempts      INTEGER DEFAULT 0,
    retry_window_hours      INTEGER DEFAULT 0,
    expected_volume_per_hr  INTEGER DEFAULT 0,
    created_at              TIMESTAMP DEFAULT NOW()
);


-- Fallback configuration
CREATE TABLE provider_fallbacks (
    provider_id             VARCHAR(64) PRIMARY KEY REFERENCES providers(provider_id),
    fallback_provider_id    VARCHAR(64) REFERENCES providers(provider_id),
    fallback_type           VARCHAR(32) NOT NULL,    -- hot, warm, graceful_degradation
    activation_method       VARCHAR(32) NOT NULL,    -- automatic, feature_flag, manual
    switchover_seconds      INTEGER DEFAULT 0,
    data_compatibility      VARCHAR(32),             -- full, partial, format_conversion_required
    notes                   TEXT,
    created_at              TIMESTAMP DEFAULT NOW()
);


-- Circuit breaker configuration
CREATE TABLE circuit_breaker_configs (
    provider_id                 VARCHAR(64) PRIMARY KEY REFERENCES providers(provider_id),
    error_threshold_pct         NUMERIC(5,2) NOT NULL,
    window_seconds              INTEGER NOT NULL,
    recovery_probes             INTEGER NOT NULL DEFAULT 3,
    probe_interval_seconds      INTEGER DEFAULT 30,
    half_open_max_requests      INTEGER DEFAULT 3,
    created_at                  TIMESTAMP DEFAULT NOW()
);


-- Flow dependency mappings (which providers does each user flow depend on?)
CREATE TABLE flow_dependencies (
    id              SERIAL PRIMARY KEY,
    flow_name       VARCHAR(128) NOT NULL,
    provider_id     VARCHAR(64) REFERENCES providers(provider_id),
    step_order      INTEGER NOT NULL,               -- Position in the dependency chain
    is_required     BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(flow_name, provider_id)
);

CREATE INDEX idx_flow_deps_flow ON flow_dependencies(flow_name);
CREATE INDEX idx_flow_deps_provider ON flow_dependencies(provider_id);


-- ============================================================================
-- 2. WEBHOOK EVENTS
-- Every webhook received from every provider. High volume table.
-- ============================================================================

CREATE TABLE webhook_events (
    id                  BIGSERIAL PRIMARY KEY,
    event_id            VARCHAR(256) NOT NULL,       -- Provider's event ID
    provider_id         VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    event_type          VARCHAR(128) NOT NULL,
    received_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    provider_timestamp  TIMESTAMP,                   -- When provider says it happened
    payload_size_bytes  INTEGER,
    signature_valid     BOOLEAN NOT NULL,
    status              VARCHAR(32) NOT NULL,        -- received, processed, failed_validation, etc.
    processing_time_ms  INTEGER,
    error_message       TEXT,
    retry_count         INTEGER DEFAULT 0,
    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_webhook_events_provider ON webhook_events(provider_id, received_at);
CREATE INDEX idx_webhook_events_status ON webhook_events(status);
CREATE INDEX idx_webhook_events_received ON webhook_events(received_at);
CREATE UNIQUE INDEX idx_webhook_events_dedup ON webhook_events(provider_id, event_id);


-- Dead letter queue for events that couldn't be processed
CREATE TABLE dead_letter_queue (
    id                  BIGSERIAL PRIMARY KEY,
    event_id            VARCHAR(256) NOT NULL,
    provider_id         VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    event_type          VARCHAR(128) NOT NULL,
    first_received_at   TIMESTAMP NOT NULL,
    last_attempt_at     TIMESTAMP NOT NULL,
    total_attempts      INTEGER NOT NULL,
    last_error          TEXT NOT NULL,
    raw_payload         JSONB NOT NULL,
    resolved            BOOLEAN DEFAULT FALSE,
    resolved_at         TIMESTAMP,
    resolution_notes    TEXT,
    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_dlq_provider ON dead_letter_queue(provider_id);
CREATE INDEX idx_dlq_unresolved ON dead_letter_queue(resolved) WHERE resolved = FALSE;


-- ============================================================================
-- 3. API CALL EVENTS
-- Every outgoing API call to every provider. High volume table.
-- ============================================================================

CREATE TABLE api_call_events (
    id                      BIGSERIAL PRIMARY KEY,
    provider_id             VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    endpoint                VARCHAR(512) NOT NULL,
    method                  VARCHAR(8) NOT NULL,
    timestamp               TIMESTAMP NOT NULL DEFAULT NOW(),
    response_status_code    INTEGER NOT NULL,
    latency_ms              NUMERIC(10,2) NOT NULL,
    success                 BOOLEAN NOT NULL,
    error_category          VARCHAR(32),             -- timeout, server_error, rate_limited, auth_error
    retry_attempt           INTEGER DEFAULT 0,
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_api_calls_provider ON api_call_events(provider_id, timestamp);
CREATE INDEX idx_api_calls_status ON api_call_events(success, timestamp);
CREATE INDEX idx_api_calls_errors ON api_call_events(error_category) WHERE error_category IS NOT NULL;


-- ============================================================================
-- 4. HEALTH SNAPSHOTS
-- Point-in-time health assessments generated by api_health_tracker.
-- Used for trend analysis and dashboard display.
-- ============================================================================

CREATE TABLE health_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    provider_id         VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    snapshot_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    window_seconds      INTEGER NOT NULL,

    -- Latency
    latency_p50_ms      NUMERIC(10,2),
    latency_p95_ms      NUMERIC(10,2),
    latency_p99_ms      NUMERIC(10,2),
    latency_max_ms      NUMERIC(10,2),

    -- Error rates
    total_requests      INTEGER NOT NULL DEFAULT 0,
    successful_requests INTEGER NOT NULL DEFAULT 0,
    failed_requests     INTEGER NOT NULL DEFAULT 0,
    error_rate_pct      NUMERIC(5,2) NOT NULL DEFAULT 0,
    errors_by_category  JSONB,
    errors_by_status    JSONB,

    -- State
    circuit_state       VARCHAR(16) NOT NULL,        -- closed, open, half_open
    health_status       VARCHAR(16) NOT NULL,        -- healthy, degraded, unhealthy, unknown
    requests_per_minute NUMERIC(10,2) DEFAULT 0
);

CREATE INDEX idx_snapshots_provider ON health_snapshots(provider_id, snapshot_at);
CREATE INDEX idx_snapshots_status ON health_snapshots(health_status);


-- ============================================================================
-- 5. CIRCUIT BREAKER STATE
-- Current and historical circuit breaker state per provider.
-- ============================================================================

CREATE TABLE circuit_breaker_state (
    provider_id             VARCHAR(64) PRIMARY KEY REFERENCES providers(provider_id),
    current_state           VARCHAR(16) NOT NULL DEFAULT 'closed',
    last_state_change       TIMESTAMP,
    consecutive_failures    INTEGER DEFAULT 0,
    consecutive_successes   INTEGER DEFAULT 0,
    last_failure_time       TIMESTAMP,
    last_success_time       TIMESTAMP,
    total_trips             INTEGER DEFAULT 0,
    last_probe_time         TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT NOW()
);

-- History of state transitions for post-incident review
CREATE TABLE circuit_breaker_history (
    id              BIGSERIAL PRIMARY KEY,
    provider_id     VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    from_state      VARCHAR(16) NOT NULL,
    to_state        VARCHAR(16) NOT NULL,
    reason          TEXT,
    transitioned_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cb_history_provider ON circuit_breaker_history(provider_id, transitioned_at);


-- ============================================================================
-- 6. INCIDENTS
-- Integration incidents detected by incident_detector.
-- ============================================================================

CREATE TABLE incidents (
    incident_id         VARCHAR(32) PRIMARY KEY,
    provider_id         VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    anomaly_type        VARCHAR(64) NOT NULL,
    severity            VARCHAR(8) NOT NULL,         -- p0, p1, p2, p3
    status              VARCHAR(32) NOT NULL,        -- detected, acknowledged, mitigating, resolved

    -- Detection
    detected_at         TIMESTAMP NOT NULL,
    detection_rule      TEXT,
    current_value       NUMERIC(12,2),
    baseline_value      NUMERIC(12,2),
    threshold_value     NUMERIC(12,2),

    -- Impact
    affected_flows      TEXT[],
    blast_radius        VARCHAR(8),
    estimated_users     INTEGER DEFAULT 0,

    -- Response
    acknowledged_at     TIMESTAMP,
    acknowledged_by     VARCHAR(128),
    mitigated_at        TIMESTAMP,
    resolved_at         TIMESTAMP,
    resolution_notes    TEXT,
    alert_channels      TEXT[],

    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_incidents_provider ON incidents(provider_id);
CREATE INDEX idx_incidents_status ON incidents(status);
CREATE INDEX idx_incidents_severity ON incidents(severity);
CREATE INDEX idx_incidents_detected ON incidents(detected_at);
CREATE INDEX idx_incidents_active ON incidents(status) WHERE status != 'resolved';


-- Incident timeline events
CREATE TABLE incident_timeline (
    id              BIGSERIAL PRIMARY KEY,
    incident_id     VARCHAR(32) NOT NULL REFERENCES incidents(incident_id),
    event_text      TEXT NOT NULL,
    event_at        TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_timeline_incident ON incident_timeline(incident_id, event_at);


-- ============================================================================
-- 7. ONBOARDING FUNNEL
-- User session and step tracking for funnel analysis.
-- ============================================================================

CREATE TABLE funnel_sessions (
    session_id      VARCHAR(128) PRIMARY KEY,
    user_id         VARCHAR(128) NOT NULL,
    started_at      TIMESTAMP NOT NULL,
    completed        BOOLEAN DEFAULT FALSE,
    completed_at    TIMESTAMP,
    drop_off_step   VARCHAR(64),
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_sessions_started ON funnel_sessions(started_at);
CREATE INDEX idx_sessions_completed ON funnel_sessions(completed);
CREATE INDEX idx_sessions_dropoff ON funnel_sessions(drop_off_step) WHERE drop_off_step IS NOT NULL;


CREATE TABLE funnel_step_events (
    id                  BIGSERIAL PRIMARY KEY,
    session_id          VARCHAR(128) NOT NULL REFERENCES funnel_sessions(session_id),
    step_id             VARCHAR(64) NOT NULL,
    step_order          INTEGER NOT NULL,
    started_at          TIMESTAMP NOT NULL,
    completed_at        TIMESTAMP,
    outcome             VARCHAR(32) NOT NULL,        -- completed, dropped_off, error, timeout, skipped
    api_latency_ms      NUMERIC(10,2),
    api_provider_id     VARCHAR(64),
    api_status_code     INTEGER,
    error_message       TEXT,
    drop_off_cause      VARCHAR(32),                 -- ux_friction, api_latency, api_error, api_timeout
    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_step_events_session ON funnel_step_events(session_id);
CREATE INDEX idx_step_events_step ON funnel_step_events(step_id, started_at);
CREATE INDEX idx_step_events_outcome ON funnel_step_events(outcome);
CREATE INDEX idx_step_events_cause ON funnel_step_events(drop_off_cause) WHERE drop_off_cause IS NOT NULL;


-- ============================================================================
-- 8. PROVIDER SCORECARD DATA
-- Aggregated metrics for SLA compliance and vendor evaluation.
-- ============================================================================

-- Weekly uptime records
CREATE TABLE uptime_records (
    id              BIGSERIAL PRIMARY KEY,
    provider_id     VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    period_start    TIMESTAMP NOT NULL,
    period_end      TIMESTAMP NOT NULL,
    total_minutes   NUMERIC(10,2) NOT NULL,
    available_min   NUMERIC(10,2) NOT NULL,
    downtime_min    NUMERIC(10,2) NOT NULL,
    incident_count  INTEGER DEFAULT 0,
    uptime_pct      NUMERIC(8,4) NOT NULL,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_uptime_provider ON uptime_records(provider_id, period_start);


-- Webhook delivery reliability records
CREATE TABLE webhook_reliability (
    id                      BIGSERIAL PRIMARY KEY,
    provider_id             VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    period_start            TIMESTAMP NOT NULL,
    period_end              TIMESTAMP NOT NULL,
    expected_deliveries     INTEGER NOT NULL,
    actual_deliveries       INTEGER NOT NULL,
    delivery_rate_pct       NUMERIC(6,3) NOT NULL,
    avg_delivery_latency_ms NUMERIC(10,2),
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_webhook_rel_provider ON webhook_reliability(provider_id, period_start);


-- Latency trend records (weekly snapshots for QBR)
CREATE TABLE latency_records (
    id              BIGSERIAL PRIMARY KEY,
    provider_id     VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    period_start    TIMESTAMP NOT NULL,
    period_end      TIMESTAMP NOT NULL,
    p50_ms          NUMERIC(10,2) NOT NULL,
    p95_ms          NUMERIC(10,2) NOT NULL,
    p99_ms          NUMERIC(10,2) NOT NULL,
    sample_count    INTEGER NOT NULL,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_latency_provider ON latency_records(provider_id, period_start);


-- Cost tracking per billing period
CREATE TABLE cost_records (
    id              BIGSERIAL PRIMARY KEY,
    provider_id     VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    period_start    TIMESTAMP NOT NULL,
    period_end      TIMESTAMP NOT NULL,
    total_cost      NUMERIC(12,2) NOT NULL,
    total_calls     INTEGER NOT NULL,
    successful      INTEGER NOT NULL,
    failed          INTEGER NOT NULL,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_cost_provider ON cost_records(provider_id, period_start);


-- ============================================================================
-- 9. USEFUL VIEWS
-- Pre-built queries for common dashboard and reporting needs.
-- ============================================================================

-- Current health status for all providers
CREATE VIEW v_provider_health AS
SELECT
    p.provider_id,
    p.name,
    p.category,
    p.blast_radius,
    cb.current_state AS circuit_state,
    hs.health_status,
    hs.error_rate_pct,
    hs.latency_p95_ms,
    hs.total_requests,
    hs.snapshot_at AS last_snapshot
FROM providers p
LEFT JOIN circuit_breaker_state cb ON cb.provider_id = p.provider_id
LEFT JOIN LATERAL (
    SELECT *
    FROM health_snapshots
    WHERE provider_id = p.provider_id
    ORDER BY snapshot_at DESC
    LIMIT 1
) hs ON TRUE
WHERE p.is_active = TRUE;


-- Critical providers without fallback (single points of failure)
CREATE VIEW v_single_points_of_failure AS
SELECT
    p.provider_id,
    p.name,
    p.category,
    p.blast_radius
FROM providers p
LEFT JOIN provider_fallbacks f ON f.provider_id = p.provider_id
WHERE p.blast_radius IN ('p0', 'p1')
  AND p.is_active = TRUE
  AND f.provider_id IS NULL;


-- Active incidents with provider context
CREATE VIEW v_active_incidents AS
SELECT
    i.incident_id,
    i.provider_id,
    p.name AS provider_name,
    p.blast_radius,
    i.anomaly_type,
    i.severity,
    i.status,
    i.detected_at,
    i.acknowledged_at,
    i.affected_flows,
    EXTRACT(EPOCH FROM (NOW() - i.detected_at)) / 60 AS duration_minutes
FROM incidents i
JOIN providers p ON p.provider_id = i.provider_id
WHERE i.status != 'resolved'
ORDER BY
    CASE i.severity
        WHEN 'p0' THEN 1
        WHEN 'p1' THEN 2
        WHEN 'p2' THEN 3
        WHEN 'p3' THEN 4
    END,
    i.detected_at;


-- Funnel conversion rates (last 24 hours)
CREATE VIEW v_funnel_conversion_24h AS
SELECT
    step_id,
    step_order,
    COUNT(*) AS reached,
    COUNT(*) FILTER (WHERE outcome = 'completed') AS completed,
    COUNT(*) FILTER (WHERE outcome IN ('dropped_off', 'error', 'timeout')) AS dropped,
    ROUND(
        COUNT(*) FILTER (WHERE outcome = 'completed')::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 1
    ) AS step_conversion_pct,
    ROUND(
        AVG(api_latency_ms) FILTER (WHERE outcome = 'completed'), 0
    ) AS avg_latency_completed_ms,
    ROUND(
        AVG(api_latency_ms) FILTER (WHERE outcome IN ('dropped_off', 'timeout')), 0
    ) AS avg_latency_dropped_ms
FROM funnel_step_events
WHERE started_at >= NOW() - INTERVAL '24 hours'
GROUP BY step_id, step_order
ORDER BY step_order;


-- Provider SLA compliance summary (last 90 days)
CREATE VIEW v_sla_compliance_90d AS
SELECT
    p.provider_id,
    p.name,
    s.guaranteed_uptime_pct,
    ROUND(
        SUM(u.available_min) / NULLIF(SUM(u.total_minutes), 0) * 100, 4
    ) AS actual_uptime_pct,
    ROUND(SUM(u.downtime_min), 1) AS total_downtime_minutes,
    SUM(u.incident_count) AS total_incidents,
    CASE
        WHEN SUM(u.available_min) / NULLIF(SUM(u.total_minutes), 0) * 100
             >= s.guaranteed_uptime_pct THEN 'compliant'
        WHEN SUM(u.available_min) / NULLIF(SUM(u.total_minutes), 0) * 100
             >= s.guaranteed_uptime_pct - 0.1 THEN 'at_risk'
        ELSE 'breached'
    END AS sla_status
FROM providers p
JOIN provider_slas s ON s.provider_id = p.provider_id
LEFT JOIN uptime_records u ON u.provider_id = p.provider_id
    AND u.period_start >= NOW() - INTERVAL '90 days'
WHERE p.is_active = TRUE
GROUP BY p.provider_id, p.name, s.guaranteed_uptime_pct;
