-- ============================================================================
-- Integration Health Monitor — Supabase Migration (PostgreSQL 14+)
-- Multi-tenant with RLS, Realtime subscriptions, and audit trails
-- ============================================================================

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ============================================================================
-- 1. TENANCY & AUTH
-- ============================================================================

-- Clients (organizations)
CREATE TABLE clients (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    logo_url TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- User roles for multi-tenancy
CREATE TYPE user_role AS ENUM ('ops', 'provider', 'client', 'admin');

-- User profiles extended with role and client
ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS role user_role DEFAULT 'client';
ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS client_id UUID REFERENCES clients(id);
ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS is_provider_vendor BOOLEAN DEFAULT FALSE;
ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS provider_id VARCHAR(64);

-- ============================================================================
-- 2. PROVIDER REGISTRY
-- ============================================================================

CREATE TABLE providers (
    provider_id VARCHAR(64) PRIMARY KEY,
    client_id UUID NOT NULL REFERENCES clients(id),
    name VARCHAR(128) NOT NULL,
    category VARCHAR(64) NOT NULL,
    blast_radius VARCHAR(8) NOT NULL CHECK (blast_radius IN ('p0', 'p1', 'p2', 'p3')),
    data_flow_pattern VARCHAR(32) NOT NULL,
    auth_method VARCHAR(32) NOT NULL,
    base_url VARCHAR(512) NOT NULL,
    api_version VARCHAR(64),
    contract_owner VARCHAR(128),
    technical_contact VARCHAR(256),
    account_manager VARCHAR(256),
    integration_date TIMESTAMP WITH TIME ZONE NOT NULL,
    last_contract_review TIMESTAMP WITH TIME ZONE,
    notes TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_providers_client ON providers(client_id);
CREATE INDEX idx_providers_category ON providers(category);
CREATE INDEX idx_providers_blast_radius ON providers(blast_radius);
CREATE INDEX idx_providers_active ON providers(is_active);

-- Provider endpoints
CREATE TABLE provider_endpoints (
    id BIGSERIAL PRIMARY KEY,
    provider_id VARCHAR(64) NOT NULL REFERENCES providers(provider_id) ON DELETE CASCADE,
    path VARCHAR(512) NOT NULL,
    method VARCHAR(8) NOT NULL,
    description TEXT,
    is_critical BOOLEAN DEFAULT FALSE,
    expected_latency_ms INTEGER,
    timeout_ms INTEGER NOT NULL,
    rate_limit_per_min INTEGER,
    is_idempotent BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_endpoints_provider ON provider_endpoints(provider_id);

-- SLA definitions
CREATE TABLE provider_slas (
    provider_id VARCHAR(64) PRIMARY KEY REFERENCES providers(provider_id) ON DELETE CASCADE,
    guaranteed_uptime_pct NUMERIC(6,3) NOT NULL,
    max_response_time_ms INTEGER NOT NULL,
    webhook_delivery_pct NUMERIC(6,3) DEFAULT 0,
    support_response_minutes INTEGER,
    deprecation_notice_days INTEGER,
    credit_eligible BOOLEAN DEFAULT FALSE,
    contract_start_date DATE,
    contract_end_date DATE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Webhook configuration
CREATE TABLE webhook_configs (
    provider_id VARCHAR(64) PRIMARY KEY REFERENCES providers(provider_id) ON DELETE CASCADE,
    endpoint_path VARCHAR(256) NOT NULL,
    events_subscribed TEXT[] NOT NULL,
    signature_header VARCHAR(128) NOT NULL,
    signature_algorithm VARCHAR(32) NOT NULL,
    retry_policy TEXT,
    max_retry_attempts INTEGER DEFAULT 0,
    retry_window_hours INTEGER DEFAULT 0,
    expected_volume_per_hr INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Fallback configuration
CREATE TABLE provider_fallbacks (
    provider_id VARCHAR(64) PRIMARY KEY REFERENCES providers(provider_id) ON DELETE CASCADE,
    fallback_provider_id VARCHAR(64) REFERENCES providers(provider_id),
    fallback_type VARCHAR(32) NOT NULL,
    activation_method VARCHAR(32) NOT NULL,
    switchover_seconds INTEGER DEFAULT 0,
    data_compatibility VARCHAR(32),
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Circuit breaker configuration
CREATE TABLE circuit_breaker_configs (
    provider_id VARCHAR(64) PRIMARY KEY REFERENCES providers(provider_id) ON DELETE CASCADE,
    error_threshold_pct NUMERIC(5,2) NOT NULL,
    window_seconds INTEGER NOT NULL,
    recovery_probes INTEGER NOT NULL DEFAULT 3,
    probe_interval_seconds INTEGER DEFAULT 30,
    half_open_max_requests INTEGER DEFAULT 3,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Flow dependencies
CREATE TABLE flow_dependencies (
    id BIGSERIAL PRIMARY KEY,
    client_id UUID NOT NULL REFERENCES clients(id),
    flow_name VARCHAR(128) NOT NULL,
    provider_id VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    step_order INTEGER NOT NULL,
    is_required BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(client_id, flow_name, provider_id)
);

CREATE INDEX idx_flow_deps_client ON flow_dependencies(client_id);
CREATE INDEX idx_flow_deps_flow ON flow_dependencies(flow_name);

-- ============================================================================
-- 3. WEBHOOK EVENTS
-- ============================================================================

CREATE TABLE webhook_events (
    id BIGSERIAL PRIMARY KEY,
    event_id VARCHAR(256) NOT NULL,
    provider_id VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    client_id UUID NOT NULL REFERENCES clients(id),
    event_type VARCHAR(128) NOT NULL,
    received_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    provider_timestamp TIMESTAMP WITH TIME ZONE,
    payload_size_bytes INTEGER,
    signature_valid BOOLEAN NOT NULL,
    status VARCHAR(32) NOT NULL,
    processing_time_ms INTEGER,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_webhook_events_provider ON webhook_events(provider_id, received_at);
CREATE INDEX idx_webhook_events_client ON webhook_events(client_id, received_at);
CREATE INDEX idx_webhook_events_status ON webhook_events(status);
CREATE UNIQUE INDEX idx_webhook_events_dedup ON webhook_events(provider_id, event_id);

-- Dead letter queue
CREATE TABLE dead_letter_queue (
    id BIGSERIAL PRIMARY KEY,
    event_id VARCHAR(256) NOT NULL,
    provider_id VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    client_id UUID NOT NULL REFERENCES clients(id),
    event_type VARCHAR(128) NOT NULL,
    first_received_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL,
    total_attempts INTEGER NOT NULL,
    last_error TEXT NOT NULL,
    raw_payload JSONB NOT NULL,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP WITH TIME ZONE,
    resolution_notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_dlq_provider ON dead_letter_queue(provider_id);
CREATE INDEX idx_dlq_client ON dead_letter_queue(client_id);
CREATE INDEX idx_dlq_unresolved ON dead_letter_queue(resolved) WHERE resolved = FALSE;

-- ============================================================================
-- 4. API CALL EVENTS
-- ============================================================================

CREATE TABLE api_call_events (
    id BIGSERIAL PRIMARY KEY,
    provider_id VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    client_id UUID NOT NULL REFERENCES clients(id),
    endpoint VARCHAR(512) NOT NULL,
    method VARCHAR(8) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    response_status_code INTEGER NOT NULL,
    latency_ms NUMERIC(10,2) NOT NULL,
    success BOOLEAN NOT NULL,
    error_category VARCHAR(32),
    retry_attempt INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_api_calls_provider ON api_call_events(provider_id, timestamp);
CREATE INDEX idx_api_calls_client ON api_call_events(client_id, timestamp);
CREATE INDEX idx_api_calls_status ON api_call_events(success, timestamp);
CREATE INDEX idx_api_calls_errors ON api_call_events(error_category) WHERE error_category IS NOT NULL;

-- ============================================================================
-- 5. HEALTH SNAPSHOTS (Realtime enabled)
-- ============================================================================

CREATE TABLE health_snapshots (
    id BIGSERIAL PRIMARY KEY,
    provider_id VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    client_id UUID NOT NULL REFERENCES clients(id),
    snapshot_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    window_seconds INTEGER NOT NULL,
    latency_p50_ms NUMERIC(10,2),
    latency_p95_ms NUMERIC(10,2),
    latency_p99_ms NUMERIC(10,2),
    latency_max_ms NUMERIC(10,2),
    total_requests INTEGER NOT NULL DEFAULT 0,
    successful_requests INTEGER NOT NULL DEFAULT 0,
    failed_requests INTEGER NOT NULL DEFAULT 0,
    error_rate_pct NUMERIC(5,2) NOT NULL DEFAULT 0,
    errors_by_category JSONB,
    errors_by_status JSONB,
    circuit_state VARCHAR(16) NOT NULL CHECK (circuit_state IN ('closed', 'open', 'half_open')),
    health_status VARCHAR(16) NOT NULL CHECK (health_status IN ('healthy', 'degraded', 'unhealthy', 'unknown')),
    requests_per_minute NUMERIC(10,2) DEFAULT 0
);

ALTER TABLE health_snapshots REPLICA IDENTITY FULL;

CREATE INDEX idx_snapshots_provider ON health_snapshots(provider_id, snapshot_at);
CREATE INDEX idx_snapshots_client ON health_snapshots(client_id, snapshot_at);
CREATE INDEX idx_snapshots_status ON health_snapshots(health_status);

-- ============================================================================
-- 6. CIRCUIT BREAKER STATE
-- ============================================================================

CREATE TABLE circuit_breaker_state (
    provider_id VARCHAR(64) PRIMARY KEY REFERENCES providers(provider_id) ON DELETE CASCADE,
    client_id UUID NOT NULL REFERENCES clients(id),
    current_state VARCHAR(16) NOT NULL DEFAULT 'closed' CHECK (current_state IN ('closed', 'open', 'half_open')),
    last_state_change TIMESTAMP WITH TIME ZONE,
    consecutive_failures INTEGER DEFAULT 0,
    consecutive_successes INTEGER DEFAULT 0,
    last_failure_time TIMESTAMP WITH TIME ZONE,
    last_success_time TIMESTAMP WITH TIME ZONE,
    total_trips INTEGER DEFAULT 0,
    last_probe_time TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

ALTER TABLE circuit_breaker_state REPLICA IDENTITY FULL;

CREATE INDEX idx_cb_state_client ON circuit_breaker_state(client_id);

-- Circuit breaker history
CREATE TABLE circuit_breaker_history (
    id BIGSERIAL PRIMARY KEY,
    provider_id VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    client_id UUID NOT NULL REFERENCES clients(id),
    from_state VARCHAR(16) NOT NULL,
    to_state VARCHAR(16) NOT NULL,
    reason TEXT,
    transitioned_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cb_history_provider ON circuit_breaker_history(provider_id, transitioned_at);
CREATE INDEX idx_cb_history_client ON circuit_breaker_history(client_id, transitioned_at);

-- ============================================================================
-- 7. INCIDENTS
-- ============================================================================

CREATE TABLE incidents (
    incident_id VARCHAR(32) PRIMARY KEY,
    client_id UUID NOT NULL REFERENCES clients(id),
    provider_id VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    anomaly_type VARCHAR(64) NOT NULL,
    severity VARCHAR(8) NOT NULL CHECK (severity IN ('p0', 'p1', 'p2', 'p3')),
    status VARCHAR(32) NOT NULL CHECK (status IN ('detected', 'acknowledged', 'mitigating', 'resolved')),
    detected_at TIMESTAMP WITH TIME ZONE NOT NULL,
    detection_rule TEXT,
    current_value NUMERIC(12,2),
    baseline_value NUMERIC(12,2),
    threshold_value NUMERIC(12,2),
    affected_flows TEXT[],
    blast_radius VARCHAR(8),
    estimated_users INTEGER DEFAULT 0,
    acknowledged_at TIMESTAMP WITH TIME ZONE,
    acknowledged_by VARCHAR(128),
    mitigated_at TIMESTAMP WITH TIME ZONE,
    resolved_at TIMESTAMP WITH TIME ZONE,
    resolution_notes TEXT,
    alert_channels TEXT[] DEFAULT ARRAY[]::TEXT[],
    parent_incident_id VARCHAR(32) REFERENCES incidents(incident_id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

ALTER TABLE incidents REPLICA IDENTITY FULL;

CREATE INDEX idx_incidents_client ON incidents(client_id);
CREATE INDEX idx_incidents_provider ON incidents(provider_id);
CREATE INDEX idx_incidents_status ON incidents(status);
CREATE INDEX idx_incidents_severity ON incidents(severity);
CREATE INDEX idx_incidents_detected ON incidents(detected_at);
CREATE INDEX idx_incidents_active ON incidents(status) WHERE status != 'resolved';
CREATE INDEX idx_incidents_parent ON incidents(parent_incident_id) WHERE parent_incident_id IS NOT NULL;

-- Incident timeline
CREATE TABLE incident_timeline (
    id BIGSERIAL PRIMARY KEY,
    incident_id VARCHAR(32) NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    event_text TEXT NOT NULL,
    event_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_timeline_incident ON incident_timeline(incident_id, event_at);

-- ============================================================================
-- 8. ONBOARDING FUNNEL
-- ============================================================================

CREATE TABLE funnel_sessions (
    session_id VARCHAR(128) PRIMARY KEY,
    client_id UUID NOT NULL REFERENCES clients(id),
    user_id VARCHAR(128) NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    completed BOOLEAN DEFAULT FALSE,
    completed_at TIMESTAMP WITH TIME ZONE,
    drop_off_step VARCHAR(64),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_sessions_client ON funnel_sessions(client_id);
CREATE INDEX idx_sessions_started ON funnel_sessions(started_at);
CREATE INDEX idx_sessions_completed ON funnel_sessions(completed);
CREATE INDEX idx_sessions_dropoff ON funnel_sessions(drop_off_step) WHERE drop_off_step IS NOT NULL;

-- Funnel step events
CREATE TABLE funnel_step_events (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(128) NOT NULL REFERENCES funnel_sessions(session_id) ON DELETE CASCADE,
    client_id UUID NOT NULL REFERENCES clients(id),
    step_id VARCHAR(64) NOT NULL,
    step_order INTEGER NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    outcome VARCHAR(32) NOT NULL,
    api_latency_ms NUMERIC(10,2),
    api_provider_id VARCHAR(64),
    api_status_code INTEGER,
    error_message TEXT,
    drop_off_cause VARCHAR(32),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_step_events_session ON funnel_step_events(session_id);
CREATE INDEX idx_step_events_client ON funnel_step_events(client_id);
CREATE INDEX idx_step_events_step ON funnel_step_events(step_id, started_at);
CREATE INDEX idx_step_events_outcome ON funnel_step_events(outcome);
CREATE INDEX idx_step_events_cause ON funnel_step_events(drop_off_cause) WHERE drop_off_cause IS NOT NULL;

-- ============================================================================
-- 9. PROVIDER SCORECARD DATA
-- ============================================================================

CREATE TABLE uptime_records (
    id BIGSERIAL PRIMARY KEY,
    provider_id VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    client_id UUID NOT NULL REFERENCES clients(id),
    period_start TIMESTAMP WITH TIME ZONE NOT NULL,
    period_end TIMESTAMP WITH TIME ZONE NOT NULL,
    total_minutes NUMERIC(10,2) NOT NULL,
    available_min NUMERIC(10,2) NOT NULL,
    downtime_min NUMERIC(10,2) NOT NULL,
    incident_count INTEGER DEFAULT 0,
    uptime_pct NUMERIC(8,4) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_uptime_provider ON uptime_records(provider_id, period_start);
CREATE INDEX idx_uptime_client ON uptime_records(client_id, period_start);

-- Webhook reliability records
CREATE TABLE webhook_reliability (
    id BIGSERIAL PRIMARY KEY,
    provider_id VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    client_id UUID NOT NULL REFERENCES clients(id),
    period_start TIMESTAMP WITH TIME ZONE NOT NULL,
    period_end TIMESTAMP WITH TIME ZONE NOT NULL,
    expected_deliveries INTEGER NOT NULL,
    actual_deliveries INTEGER NOT NULL,
    delivery_rate_pct NUMERIC(6,3) NOT NULL,
    avg_delivery_latency_ms NUMERIC(10,2),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_webhook_rel_provider ON webhook_reliability(provider_id, period_start);
CREATE INDEX idx_webhook_rel_client ON webhook_reliability(client_id, period_start);

-- Latency records
CREATE TABLE latency_records (
    id BIGSERIAL PRIMARY KEY,
    provider_id VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    client_id UUID NOT NULL REFERENCES clients(id),
    period_start TIMESTAMP WITH TIME ZONE NOT NULL,
    period_end TIMESTAMP WITH TIME ZONE NOT NULL,
    p50_ms NUMERIC(10,2) NOT NULL,
    p95_ms NUMERIC(10,2) NOT NULL,
    p99_ms NUMERIC(10,2) NOT NULL,
    sample_count INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_latency_provider ON latency_records(provider_id, period_start);
CREATE INDEX idx_latency_client ON latency_records(client_id, period_start);

-- Cost records
CREATE TABLE cost_records (
    id BIGSERIAL PRIMARY KEY,
    provider_id VARCHAR(64) NOT NULL REFERENCES providers(provider_id),
    client_id UUID NOT NULL REFERENCES clients(id),
    period_start TIMESTAMP WITH TIME ZONE NOT NULL,
    period_end TIMESTAMP WITH TIME ZONE NOT NULL,
    total_cost NUMERIC(12,2) NOT NULL,
    total_calls INTEGER NOT NULL,
    successful INTEGER NOT NULL,
    failed INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_cost_provider ON cost_records(provider_id, period_start);
CREATE INDEX idx_cost_client ON cost_records(client_id, period_start);

-- ============================================================================
-- 10. ROW-LEVEL SECURITY (RLS)
-- ============================================================================

-- Enable RLS on all tables
ALTER TABLE clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE providers ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_endpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_slas ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_fallbacks ENABLE ROW LEVEL SECURITY;
ALTER TABLE circuit_breaker_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE flow_dependencies ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE dead_letter_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_call_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE health_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE circuit_breaker_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE circuit_breaker_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE incidents ENABLE ROW LEVEL SECURITY;
ALTER TABLE incident_timeline ENABLE ROW LEVEL SECURITY;
ALTER TABLE funnel_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE funnel_step_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE uptime_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_reliability ENABLE ROW LEVEL SECURITY;
ALTER TABLE latency_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE cost_records ENABLE ROW LEVEL SECURITY;

-- RLS Policies: Clients
-- Only admins and ops can see other clients; clients see only themselves
CREATE POLICY clients_admin_policy ON clients FOR SELECT
    USING (auth.uid() IS NULL OR
           (SELECT role FROM auth.users WHERE id = auth.uid()) IN ('admin', 'ops'));

CREATE POLICY clients_self_policy ON clients FOR SELECT
    USING (id = (SELECT client_id FROM auth.users WHERE id = auth.uid()));

-- RLS Policies: Providers (client_id based)
-- Ops can see all, clients see only their own, providers see their own scorecard
CREATE POLICY providers_ops_policy ON providers FOR SELECT
    USING ((SELECT role FROM auth.users WHERE id = auth.uid()) = 'ops');

CREATE POLICY providers_client_policy ON providers FOR SELECT
    USING (client_id = (SELECT client_id FROM auth.users WHERE id = auth.uid())
           AND (SELECT role FROM auth.users WHERE id = auth.uid()) = 'client');

CREATE POLICY providers_vendor_policy ON providers FOR SELECT
    USING (provider_id = (SELECT provider_id FROM auth.users WHERE id = auth.uid())
           AND (SELECT role FROM auth.users WHERE id = auth.uid()) = 'provider');

-- RLS Policies: Events (client_id based)
-- Ops can see all, clients see only their own
CREATE POLICY webhook_events_ops_policy ON webhook_events FOR SELECT
    USING ((SELECT role FROM auth.users WHERE id = auth.uid()) = 'ops');

CREATE POLICY webhook_events_client_policy ON webhook_events FOR SELECT
    USING (client_id = (SELECT client_id FROM auth.users WHERE id = auth.uid()));

CREATE POLICY api_call_events_ops_policy ON api_call_events FOR SELECT
    USING ((SELECT role FROM auth.users WHERE id = auth.uid()) = 'ops');

CREATE POLICY api_call_events_client_policy ON api_call_events FOR SELECT
    USING (client_id = (SELECT client_id FROM auth.users WHERE id = auth.uid()));

-- RLS Policies: Health Snapshots (for realtime)
CREATE POLICY health_snapshots_ops_policy ON health_snapshots FOR SELECT
    USING ((SELECT role FROM auth.users WHERE id = auth.uid()) = 'ops');

CREATE POLICY health_snapshots_client_policy ON health_snapshots FOR SELECT
    USING (client_id = (SELECT client_id FROM auth.users WHERE id = auth.uid()));

-- RLS Policies: Incidents
CREATE POLICY incidents_ops_policy ON incidents FOR SELECT
    USING ((SELECT role FROM auth.users WHERE id = auth.uid()) = 'ops');

CREATE POLICY incidents_client_policy ON incidents FOR SELECT
    USING (client_id = (SELECT client_id FROM auth.users WHERE id = auth.uid()));

-- RLS Policies: Uptime Records (scorecards)
CREATE POLICY uptime_records_ops_policy ON uptime_records FOR SELECT
    USING ((SELECT role FROM auth.users WHERE id = auth.uid()) = 'ops');

CREATE POLICY uptime_records_client_policy ON uptime_records FOR SELECT
    USING (client_id = (SELECT client_id FROM auth.users WHERE id = auth.uid()));

CREATE POLICY uptime_records_vendor_policy ON uptime_records FOR SELECT
    USING (provider_id = (SELECT provider_id FROM auth.users WHERE id = auth.uid())
           AND (SELECT role FROM auth.users WHERE id = auth.uid()) = 'provider');

-- ============================================================================
-- 11. REALTIME SUBSCRIPTIONS
-- ============================================================================

-- Enable realtime on health_snapshots and incidents for live dashboard updates
ALTER PUBLICATION supabase_realtime ADD TABLE health_snapshots;
ALTER PUBLICATION supabase_realtime ADD TABLE incidents;

-- ============================================================================
-- 12. UTILITY FUNCTIONS
-- ============================================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Triggers for updated_at
CREATE TRIGGER clients_update_timestamp BEFORE UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

CREATE TRIGGER providers_update_timestamp BEFORE UPDATE ON providers
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

CREATE TRIGGER incidents_update_timestamp BEFORE UPDATE ON incidents
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

CREATE TRIGGER circuit_breaker_state_update_timestamp BEFORE UPDATE ON circuit_breaker_state
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

-- ============================================================================
-- 13. VIEWS FOR COMMON QUERIES
-- ============================================================================

-- Current provider health per client
CREATE VIEW v_provider_health AS
SELECT
    p.client_id,
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
    WHERE provider_id = p.provider_id AND client_id = p.client_id
    ORDER BY snapshot_at DESC
    LIMIT 1
) hs ON TRUE
WHERE p.is_active = TRUE;

-- Active incidents with context
CREATE VIEW v_active_incidents AS
SELECT
    i.incident_id,
    i.client_id,
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
    i.detected_at DESC;

-- Funnel conversion rates (24h)
CREATE VIEW v_funnel_conversion_24h AS
SELECT
    client_id,
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
GROUP BY client_id, step_id, step_order
ORDER BY step_order;

-- SLA compliance (90 days)
CREATE VIEW v_sla_compliance_90d AS
SELECT
    p.client_id,
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
    AND u.client_id = p.client_id
    AND u.period_start >= NOW() - INTERVAL '90 days'
WHERE p.is_active = TRUE
GROUP BY p.client_id, p.provider_id, p.name, s.guaranteed_uptime_pct;
