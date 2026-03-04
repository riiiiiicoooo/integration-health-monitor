-- Migration 001: Initial Tables
-- Description: Create base tables for integration registry, health checks, and webhooks
-- Applied: 2024-03-05

BEGIN;

-- Providers table: Central registry of all third-party API integrations
CREATE TABLE IF NOT EXISTS providers (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    category VARCHAR(50) NOT NULL,
    blast_radius VARCHAR(20) NOT NULL,
    data_flow_pattern VARCHAR(50) NOT NULL,
    auth_method VARCHAR(50) NOT NULL,
    base_url VARCHAR(500) NOT NULL,
    api_version VARCHAR(50),
    webhook_endpoint_path VARCHAR(500),
    expected_webhook_volume_per_hour INT,
    webhook_signature_header VARCHAR(100),
    webhook_signature_algorithm VARCHAR(50),
    health_check_endpoint VARCHAR(500),
    status_page_url VARCHAR(500),
    health_check_interval_seconds INT,
    circuit_breaker_error_threshold_pct FLOAT,
    circuit_breaker_window_seconds INT,
    sla_guaranteed_uptime_pct FLOAT,
    sla_max_response_time_ms INT,
    sla_webhook_delivery_pct FLOAT,
    fallback_provider_id VARCHAR(50),
    contract_owner VARCHAR(255),
    technical_contact_email VARCHAR(255),
    account_manager_email VARCHAR(255),
    integration_date TIMESTAMP NOT NULL,
    last_contract_review TIMESTAMP,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_providers_category ON providers(category);
CREATE INDEX idx_providers_blast_radius ON providers(blast_radius);
CREATE INDEX idx_providers_data_flow ON providers(data_flow_pattern);

-- API Health Checks: Records of every API call made to third-party providers
CREATE TABLE IF NOT EXISTS api_health_checks (
    id BIGSERIAL PRIMARY KEY,
    provider_id VARCHAR(50) NOT NULL REFERENCES providers(id),
    endpoint VARCHAR(500) NOT NULL,
    method VARCHAR(10) NOT NULL,
    response_status_code INT NOT NULL,
    latency_ms FLOAT NOT NULL,
    success BOOLEAN NOT NULL,
    error_category VARCHAR(50),
    retry_attempt INT DEFAULT 0,
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(provider_id) REFERENCES providers(id)
);

-- Partition api_health_checks by month for better performance
CREATE TABLE api_health_checks_2024_03 PARTITION OF api_health_checks
    FOR VALUES FROM ('2024-03-01') TO ('2024-04-01');

CREATE TABLE api_health_checks_2024_02 PARTITION OF api_health_checks
    FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');

CREATE INDEX idx_api_health_checks_provider_timestamp ON api_health_checks(provider_id, timestamp);
CREATE INDEX idx_api_health_checks_success ON api_health_checks(success);
CREATE INDEX idx_api_health_checks_status_code ON api_health_checks(response_status_code);

-- Health Snapshots: Point-in-time aggregated health metrics
CREATE TABLE IF NOT EXISTS health_snapshots (
    id BIGSERIAL PRIMARY KEY,
    provider_id VARCHAR(50) NOT NULL REFERENCES providers(id),
    timestamp TIMESTAMP NOT NULL,
    window_seconds INT NOT NULL,
    latency_p50_ms FLOAT,
    latency_p95_ms FLOAT,
    latency_p99_ms FLOAT,
    latency_max_ms FLOAT,
    total_requests INT,
    successful_requests INT,
    failed_requests INT,
    error_rate_pct FLOAT,
    circuit_state VARCHAR(20),
    health_status VARCHAR(20),
    requests_per_minute FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(provider_id) REFERENCES providers(id)
);

CREATE INDEX idx_health_snapshots_provider_timestamp ON health_snapshots(provider_id, timestamp);
CREATE INDEX idx_health_snapshots_health_status ON health_snapshots(health_status);

-- Webhook Events: Records of webhook deliveries from providers
CREATE TABLE IF NOT EXISTS webhook_events (
    id BIGSERIAL PRIMARY KEY,
    event_id VARCHAR(255) NOT NULL,
    provider_id VARCHAR(50) NOT NULL REFERENCES providers(id),
    event_type VARCHAR(100) NOT NULL,
    received_at TIMESTAMP NOT NULL,
    provider_timestamp TIMESTAMP,
    payload_size_bytes INT,
    signature_valid BOOLEAN,
    status VARCHAR(50) NOT NULL,
    processing_time_ms INT,
    error_message TEXT,
    retry_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(provider_id) REFERENCES providers(id),
    UNIQUE(event_id, provider_id)
);

-- Partition webhook_events by month
CREATE TABLE webhook_events_2024_03 PARTITION OF webhook_events
    FOR VALUES FROM ('2024-03-01') TO ('2024-04-01');

CREATE TABLE webhook_events_2024_02 PARTITION OF webhook_events
    FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');

CREATE INDEX idx_webhook_events_provider_received ON webhook_events(provider_id, received_at);
CREATE INDEX idx_webhook_events_event_id ON webhook_events(event_id);
CREATE INDEX idx_webhook_events_status ON webhook_events(status);

-- Dead Letter Queue: Webhook events that failed all retries
CREATE TABLE IF NOT EXISTS webhook_dead_letter (
    id BIGSERIAL PRIMARY KEY,
    event_id VARCHAR(255) NOT NULL,
    provider_id VARCHAR(50) NOT NULL REFERENCES providers(id),
    event_type VARCHAR(100) NOT NULL,
    first_received_at TIMESTAMP NOT NULL,
    last_attempt_at TIMESTAMP NOT NULL,
    total_attempts INT NOT NULL,
    last_error TEXT,
    raw_payload JSONB,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP,
    resolution_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(provider_id) REFERENCES providers(id)
);

CREATE INDEX idx_webhook_dlq_provider ON webhook_dead_letter(provider_id);
CREATE INDEX idx_webhook_dlq_resolved ON webhook_dead_letter(resolved);
CREATE INDEX idx_webhook_dlq_first_received ON webhook_dead_letter(first_received_at);

-- Circuit Breaker State: Tracks circuit breaker state for each provider
CREATE TABLE IF NOT EXISTS circuit_breaker_states (
    id BIGSERIAL PRIMARY KEY,
    provider_id VARCHAR(50) NOT NULL REFERENCES providers(id),
    state VARCHAR(20) NOT NULL,
    last_state_change TIMESTAMP,
    consecutive_failures INT DEFAULT 0,
    consecutive_successes INT DEFAULT 0,
    last_failure_time TIMESTAMP,
    last_success_time TIMESTAMP,
    total_trips INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(provider_id) REFERENCES providers(id),
    UNIQUE(provider_id)
);

CREATE INDEX idx_circuit_breaker_state ON circuit_breaker_states(state);

-- Provider Uptime Records: Historical uptime tracking for SLA compliance
CREATE TABLE IF NOT EXISTS provider_uptime_records (
    id BIGSERIAL PRIMARY KEY,
    provider_id VARCHAR(50) NOT NULL REFERENCES providers(id),
    period_start TIMESTAMP NOT NULL,
    period_end TIMESTAMP NOT NULL,
    total_minutes FLOAT NOT NULL,
    available_minutes FLOAT NOT NULL,
    downtime_minutes FLOAT NOT NULL,
    incident_count INT DEFAULT 0,
    uptime_pct FLOAT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(provider_id) REFERENCES providers(id)
);

CREATE INDEX idx_provider_uptime_provider_period ON provider_uptime_records(provider_id, period_start);

COMMIT;
