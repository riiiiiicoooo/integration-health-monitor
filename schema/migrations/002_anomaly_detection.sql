-- Migration 002: Anomaly Detection Tables
-- Description: Add tables for baseline tracking and anomaly detection
-- Applied: 2024-03-05

BEGIN;

-- Baselines: Rolling baseline metrics for each provider/metric pair
CREATE TABLE IF NOT EXISTS provider_baselines (
    id BIGSERIAL PRIMARY KEY,
    provider_id VARCHAR(50) NOT NULL REFERENCES providers(id),
    metric_name VARCHAR(100) NOT NULL,
    baseline_value FLOAT NOT NULL,
    baseline_window_hours INT NOT NULL,
    last_updated TIMESTAMP NOT NULL,
    sample_size INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(provider_id) REFERENCES providers(id),
    UNIQUE(provider_id, metric_name)
);

CREATE INDEX idx_provider_baselines_provider_metric ON provider_baselines(provider_id, metric_name);

-- Anomalies: Detected anomalies for each provider
CREATE TABLE IF NOT EXISTS detected_anomalies (
    id BIGSERIAL PRIMARY KEY,
    provider_id VARCHAR(50) NOT NULL REFERENCES providers(id),
    timestamp TIMESTAMP NOT NULL,
    metric_name VARCHAR(100) NOT NULL,
    current_value FLOAT NOT NULL,
    baseline_value FLOAT NOT NULL,
    threshold_value FLOAT NOT NULL,
    anomaly_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    sustained_minutes INT,
    is_anomalous BOOLEAN NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(provider_id) REFERENCES providers(id)
);

CREATE INDEX idx_anomalies_provider_timestamp ON detected_anomalies(provider_id, timestamp);
CREATE INDEX idx_anomalies_anomaly_type ON detected_anomalies(anomaly_type);
CREATE INDEX idx_anomalies_severity ON detected_anomalies(severity);

-- Detection Thresholds: Configuration for anomaly detection rules
CREATE TABLE IF NOT EXISTS detection_thresholds (
    id BIGSERIAL PRIMARY KEY,
    provider_id VARCHAR(50) NOT NULL REFERENCES providers(id),
    anomaly_type VARCHAR(50) NOT NULL,
    baseline_window_hours INT NOT NULL,
    threshold_multiplier FLOAT NOT NULL,
    sustained_minutes INT NOT NULL,
    min_sample_size INT DEFAULT 10,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(provider_id) REFERENCES providers(id),
    UNIQUE(provider_id, anomaly_type)
);

CREATE INDEX idx_detection_thresholds_provider ON detection_thresholds(provider_id);

-- Incidents: Confirmed incidents requiring human attention
CREATE TABLE IF NOT EXISTS incidents (
    id BIGSERIAL PRIMARY KEY,
    incident_id VARCHAR(100) NOT NULL UNIQUE,
    provider_id VARCHAR(50) NOT NULL REFERENCES providers(id),
    provider_name VARCHAR(255) NOT NULL,
    anomaly_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    status VARCHAR(50) NOT NULL,
    detected_at TIMESTAMP NOT NULL,
    detection_rule VARCHAR(255),
    current_value FLOAT,
    baseline_value FLOAT,
    threshold_value FLOAT,
    affected_flows TEXT,  -- JSON array as TEXT
    blast_radius VARCHAR(20),
    estimated_users_affected INT DEFAULT 0,
    acknowledged_at TIMESTAMP,
    acknowledged_by VARCHAR(255),
    mitigated_at TIMESTAMP,
    resolved_at TIMESTAMP,
    resolution_notes TEXT,
    alert_channels TEXT,  -- JSON array as TEXT
    timeline JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(provider_id) REFERENCES providers(id)
);

CREATE INDEX idx_incidents_provider_detected ON incidents(provider_id, detected_at);
CREATE INDEX idx_incidents_status ON incidents(status);
CREATE INDEX idx_incidents_severity ON incidents(severity);
CREATE INDEX idx_incidents_timestamp ON incidents(detected_at);

-- Incident Timeline: Event log for each incident
CREATE TABLE IF NOT EXISTS incident_timeline (
    id BIGSERIAL PRIMARY KEY,
    incident_id VARCHAR(100) NOT NULL REFERENCES incidents(incident_id),
    timestamp TIMESTAMP NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    event_description TEXT,
    created_by VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_incident_timeline_incident_timestamp ON incident_timeline(incident_id, timestamp);

-- Incident Correlations: Links between related incidents
CREATE TABLE IF NOT EXISTS incident_correlations (
    id BIGSERIAL PRIMARY KEY,
    primary_incident_id VARCHAR(100) NOT NULL REFERENCES incidents(incident_id),
    correlated_incident_id VARCHAR(100) NOT NULL REFERENCES incidents(incident_id),
    correlation_strength FLOAT,  -- 0-1, indicates how related they are
    correlation_reason VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(primary_incident_id) REFERENCES incidents(incident_id),
    FOREIGN KEY(correlated_incident_id) REFERENCES incidents(incident_id)
);

CREATE INDEX idx_incident_correlations_primary ON incident_correlations(primary_incident_id);
CREATE INDEX idx_incident_correlations_correlated ON incident_correlations(correlated_incident_id);

COMMIT;
