-- Migration 003: Funnel Correlation Tables
-- Description: Add tables for onboarding funnel analysis and API correlation
-- Applied: 2024-03-05

BEGIN;

-- Funnels: Definition of customer journeys (onboarding, loan disbursement, etc.)
CREATE TABLE IF NOT EXISTS funnels (
    id BIGSERIAL PRIMARY KEY,
    funnel_id VARCHAR(100) NOT NULL UNIQUE,
    funnel_name VARCHAR(255) NOT NULL,
    description TEXT,
    funnel_type VARCHAR(100),  -- 'onboarding', 'conversion', 'payment', etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Funnel Steps: Individual steps within a funnel
CREATE TABLE IF NOT EXISTS funnel_steps (
    id BIGSERIAL PRIMARY KEY,
    funnel_id VARCHAR(100) NOT NULL REFERENCES funnels(funnel_id),
    step_id VARCHAR(100) NOT NULL,
    step_name VARCHAR(255) NOT NULL,
    step_order INT NOT NULL,
    expected_duration_seconds INT,
    latency_tolerance_seconds FLOAT,
    is_required BOOLEAN DEFAULT TRUE,
    has_fallback BOOLEAN DEFAULT FALSE,
    fallback_description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(funnel_id) REFERENCES funnels(funnel_id),
    UNIQUE(funnel_id, step_id)
);

CREATE INDEX idx_funnel_steps_funnel_order ON funnel_steps(funnel_id, step_order);

-- Funnel Step Dependencies: Maps each step to its API dependencies
CREATE TABLE IF NOT EXISTS funnel_step_dependencies (
    id BIGSERIAL PRIMARY KEY,
    step_id VARCHAR(100) NOT NULL,
    funnel_id VARCHAR(100) NOT NULL,
    provider_id VARCHAR(50) NOT NULL,
    dependency_order INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(funnel_id) REFERENCES funnels(funnel_id),
    FOREIGN KEY(provider_id) REFERENCES providers(id),
    UNIQUE(funnel_id, step_id, provider_id)
);

CREATE INDEX idx_funnel_dependencies_funnel_provider ON funnel_step_dependencies(funnel_id, provider_id);

-- User Sessions: Track individual user journeys through funnels
CREATE TABLE IF NOT EXISTS user_sessions (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL UNIQUE,
    funnel_id VARCHAR(100) NOT NULL REFERENCES funnels(funnel_id),
    user_id VARCHAR(255) NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    completed BOOLEAN DEFAULT FALSE,
    drop_off_step VARCHAR(100),
    total_duration_seconds FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(funnel_id) REFERENCES funnels(funnel_id)
);

CREATE INDEX idx_user_sessions_funnel_started ON user_sessions(funnel_id, started_at);
CREATE INDEX idx_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX idx_user_sessions_completed ON user_sessions(completed);
CREATE INDEX idx_user_sessions_drop_off ON user_sessions(drop_off_step);

-- Step Events: Record of user encountering each funnel step
CREATE TABLE IF NOT EXISTS step_events (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL REFERENCES user_sessions(session_id),
    step_id VARCHAR(100) NOT NULL,
    step_order INT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    outcome VARCHAR(50) NOT NULL,  -- completed, dropped_off, error, timeout, skipped, pending
    api_latency_ms FLOAT,
    api_provider_id VARCHAR(50),
    api_status_code INT,
    error_message TEXT,
    drop_off_cause VARCHAR(50),  -- ux_friction, api_latency, api_error, api_timeout, unknown
    duration_seconds FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(session_id) REFERENCES user_sessions(session_id),
    FOREIGN KEY(api_provider_id) REFERENCES providers(id)
);

CREATE INDEX idx_step_events_session ON step_events(session_id);
CREATE INDEX idx_step_events_step_outcome ON step_events(step_id, outcome);
CREATE INDEX idx_step_events_api_provider ON step_events(api_provider_id);
CREATE INDEX idx_step_events_drop_off ON step_events(drop_off_cause);

-- Funnel Correlations: Links between funnel issues and provider health
CREATE TABLE IF NOT EXISTS funnel_correlations (
    id BIGSERIAL PRIMARY KEY,
    funnel_id VARCHAR(100) NOT NULL REFERENCES funnels(funnel_id),
    provider_id VARCHAR(50) NOT NULL REFERENCES providers(id),
    step_id VARCHAR(100),
    correlation_strength FLOAT,  -- 0-1, how strongly drop-off correlates with provider health
    baseline_drop_off_pct FLOAT,
    degraded_drop_off_pct FLOAT,
    drop_off_increase_pct FLOAT,
    last_analysis TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(funnel_id) REFERENCES funnels(funnel_id),
    FOREIGN KEY(provider_id) REFERENCES providers(id),
    UNIQUE(funnel_id, provider_id)
);

CREATE INDEX idx_funnel_correlations_funnel_provider ON funnel_correlations(funnel_id, provider_id);

-- Funnel Metrics: Aggregate metrics for funnel performance over time
CREATE TABLE IF NOT EXISTS funnel_metrics (
    id BIGSERIAL PRIMARY KEY,
    funnel_id VARCHAR(100) NOT NULL REFERENCES funnels(funnel_id),
    period_start TIMESTAMP NOT NULL,
    period_end TIMESTAMP NOT NULL,
    total_sessions INT NOT NULL,
    completed_sessions INT NOT NULL,
    completion_rate_pct FLOAT NOT NULL,
    total_dropoffs INT NOT NULL,
    avg_session_duration_seconds FLOAT,
    avg_latency_ms FLOAT,
    by_step_completion JSONB,  -- Step-by-step completion rates
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(funnel_id) REFERENCES funnels(funnel_id)
);

CREATE INDEX idx_funnel_metrics_funnel_period ON funnel_metrics(funnel_id, period_start);

-- Bottleneck Analysis: Identifies which steps are causing drop-offs
CREATE TABLE IF NOT EXISTS bottleneck_analysis (
    id BIGSERIAL PRIMARY KEY,
    funnel_id VARCHAR(100) NOT NULL REFERENCES funnels(funnel_id),
    analysis_timestamp TIMESTAMP NOT NULL,
    bottleneck_step_id VARCHAR(100),
    drop_off_rate_pct FLOAT,
    root_cause VARCHAR(100),  -- api_latency, api_error, ux_friction, etc.
    affected_users_30d INT,
    estimated_revenue_impact FLOAT,
    recommendations TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(funnel_id) REFERENCES funnels(funnel_id)
);

CREATE INDEX idx_bottleneck_analysis_funnel ON bottleneck_analysis(funnel_id, analysis_timestamp);

COMMIT;
