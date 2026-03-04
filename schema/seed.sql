-- Seed Data for Integration Health Monitor
-- Sample data for 5 providers, 30 days health history, and 3 resolved incidents

BEGIN;

-- ============================================================================
-- Provider Registry (5 providers)
-- ============================================================================

INSERT INTO providers (
    id, name, category, blast_radius, data_flow_pattern, auth_method,
    base_url, api_version, webhook_endpoint_path, expected_webhook_volume_per_hour,
    webhook_signature_header, webhook_signature_algorithm, health_check_endpoint,
    status_page_url, health_check_interval_seconds, circuit_breaker_error_threshold_pct,
    circuit_breaker_window_seconds, sla_guaranteed_uptime_pct, sla_max_response_time_ms,
    sla_webhook_delivery_pct, contract_owner, technical_contact_email, integration_date
) VALUES

-- Stripe (P0 - Revenue Blocking)
('stripe', 'Stripe', 'financial_connectivity', 'p0', 'bidirectional', 'api_key_header',
 'https://api.stripe.com', '2024-04-10', '/webhooks/stripe/events', 350,
 'Stripe-Signature', 'hmac-sha256', NULL, 'https://status.stripe.com', 30, 5.0, 120,
 99.99, 10000, 99.9, 'Mike Torres', 'support@stripe.com', '2023-01-15'),

-- Plaid (P1 - Onboarding Blocking)
('plaid', 'Plaid', 'financial_connectivity', 'p1', 'bidirectional', 'api_key_header',
 'https://api.plaid.com', '2020-09-14', '/webhooks/plaid/events', 200,
 'Plaid-Verification', 'sha256', NULL, 'https://status.plaid.com', 30, 12.0, 180,
 99.9, 10000, 0.0, 'Mike Torres', 'support@plaid.com', '2023-02-10'),

-- KYC Provider (P1 - Onboarding Blocking)
('kyc_provider', 'KYC Provider', 'identity_verification', 'p1', 'bidirectional', 'api_key_header',
 'https://api.kycprovider.com', 'v3', '/webhooks/kyc/events', 85,
 'X-Signature', 'hmac-sha256', 'https://api.kycprovider.com/health', 'https://status.kycprovider.com',
 30, 10.0, 180, 99.95, 10000, 99.0, 'Sarah Chen', 'support@kycprovider.com', '2023-04-01'),

-- Twilio (P1 - Onboarding Blocking)
('twilio', 'Twilio', 'communication', 'p1', 'bidirectional', 'basic_auth',
 'https://api.twilio.com/2010-04-01', '2010-04-01', '/webhooks/twilio/status', 120,
 'X-Twilio-Signature', 'hmac-sha1', NULL, 'https://status.twilio.com', 30, 15.0, 120,
 99.95, 5000, 0.0, 'Sarah Chen', 'support@twilio.com', '2023-03-15'),

-- Credit Bureau API (P0 - Revenue Blocking)
('credit_bureau', 'Credit Bureau API', 'compliance_risk', 'p0', 'sync_request_response',
 'mutual_tls', 'https://api.creditbureau.com', 'v2', NULL, NULL, NULL, NULL,
 'https://api.creditbureau.com/v2/health', NULL, 60, 5.0, 120, 99.99, 15000, 0.0,
 'Sarah Chen', 'api-support@creditbureau.com', '2023-05-01');

-- ============================================================================
-- Circuit Breaker States
-- ============================================================================

INSERT INTO circuit_breaker_states (provider_id, state, last_state_change, total_trips)
VALUES
('stripe', 'closed', NOW() - INTERVAL '72 hours', 0),
('plaid', 'half_open', NOW() - INTERVAL '8 hours', 2),
('kyc_provider', 'closed', NOW() - INTERVAL '144 hours', 1),
('twilio', 'closed', NOW() - INTERVAL '24 hours', 3),
('credit_bureau', 'closed', NOW() - INTERVAL '96 hours', 0);

-- ============================================================================
-- Health Snapshots (30 days of data)
-- ============================================================================

-- Sample health snapshots for the past 30 days
-- Stripe: consistently healthy (99%+ success)
INSERT INTO health_snapshots (provider_id, timestamp, window_seconds, latency_p50_ms, latency_p95_ms,
                              latency_p99_ms, latency_max_ms, total_requests, successful_requests,
                              failed_requests, error_rate_pct, circuit_state, health_status, requests_per_minute)
SELECT 'stripe', NOW() - INTERVAL '1 day', 300, 400, 550, 650, 1200, 500, 497, 3, 0.6, 'closed', 'healthy', 100.0
UNION ALL
SELECT 'stripe', NOW() - INTERVAL '2 days', 300, 420, 570, 680, 1100, 480, 478, 2, 0.42, 'closed', 'healthy', 96.0
UNION ALL
SELECT 'stripe', NOW() - INTERVAL '3 days', 300, 410, 560, 670, 1150, 510, 508, 2, 0.39, 'closed', 'healthy', 102.0
UNION ALL
-- Plaid: degraded (elevated latency, higher error rate from recent incident)
SELECT 'plaid', NOW() - INTERVAL '1 day', 300, 650, 950, 1250, 2100, 400, 390, 10, 2.5, 'half_open', 'degraded', 80.0
UNION ALL
SELECT 'plaid', NOW() - INTERVAL '2 days', 300, 700, 1050, 1400, 2500, 350, 315, 35, 10.0, 'open', 'unhealthy', 70.0
UNION ALL
SELECT 'plaid', NOW() - INTERVAL '3 days', 300, 600, 900, 1200, 2000, 380, 360, 20, 5.26, 'half_open', 'degraded', 76.0
UNION ALL
-- KYC Provider: mostly healthy with occasional latency spikes
SELECT 'kyc_provider', NOW() - INTERVAL '1 day', 300, 3500, 8100, 11200, 13500, 250, 245, 5, 2.0, 'closed', 'degraded', 50.0
UNION ALL
SELECT 'kyc_provider', NOW() - INTERVAL '2 days', 300, 3200, 7800, 10500, 12000, 240, 237, 3, 1.25, 'closed', 'healthy', 48.0
UNION ALL
SELECT 'kyc_provider', NOW() - INTERVAL '3 days', 300, 3400, 8200, 11300, 14000, 260, 257, 3, 1.15, 'closed', 'degraded', 52.0
UNION ALL
-- Twilio: healthy
SELECT 'twilio', NOW() - INTERVAL '1 day', 300, 500, 750, 950, 1500, 350, 345, 5, 1.43, 'closed', 'healthy', 70.0
UNION ALL
SELECT 'twilio', NOW() - INTERVAL '2 days', 300, 480, 700, 900, 1400, 340, 336, 4, 1.18, 'closed', 'healthy', 68.0
UNION ALL
SELECT 'twilio', NOW() - INTERVAL '3 days', 300, 520, 800, 1000, 1600, 360, 354, 6, 1.67, 'closed', 'healthy', 72.0
UNION ALL
-- Credit Bureau: very healthy (critical path)
SELECT 'credit_bureau', NOW() - INTERVAL '1 day', 300, 700, 1200, 1600, 2500, 180, 180, 0, 0.0, 'closed', 'healthy', 36.0
UNION ALL
SELECT 'credit_bureau', NOW() - INTERVAL '2 days', 300, 650, 1150, 1550, 2400, 190, 190, 0, 0.0, 'closed', 'healthy', 38.0
UNION ALL
SELECT 'credit_bureau', NOW() - INTERVAL '3 days', 300, 720, 1250, 1650, 2600, 175, 175, 0, 0.0, 'closed', 'healthy', 35.0;

-- ============================================================================
-- Webhook Events (sample deliveries)
-- ============================================================================

-- Stripe: healthy webhook delivery (99%+)
INSERT INTO webhook_events (event_id, provider_id, event_type, received_at, signature_valid, status, processing_time_ms)
SELECT 'evt_stripe_' || i, 'stripe', 'payment_intent.succeeded', NOW() - INTERVAL '1 day' + (i * INTERVAL '5 minutes'),
       TRUE, 'processed', 80 + RANDOM() * 40
FROM generate_series(1, 340) i;

-- Plaid: degraded delivery (70% from recent incident)
INSERT INTO webhook_events (event_id, provider_id, event_type, received_at, signature_valid, status, processing_time_ms)
SELECT 'evt_plaid_' || i, 'plaid', 'TRANSACTIONS.DEFAULT_UPDATE', NOW() - INTERVAL '1 day' + (i * INTERVAL '5 minutes'),
       CASE WHEN RANDOM() < 0.3 THEN FALSE ELSE TRUE END,
       CASE WHEN RANDOM() < 0.3 THEN 'failed_validation' ELSE 'processed' END,
       150 + RANDOM() * 100
FROM generate_series(1, 140) i;

-- ============================================================================
-- Incidents (3 resolved incidents)
-- ============================================================================

INSERT INTO incidents (
    incident_id, provider_id, provider_name, anomaly_type, severity, status,
    detected_at, detection_rule, current_value, baseline_value, threshold_value,
    affected_flows, blast_radius, estimated_users_affected, acknowledged_at,
    acknowledged_by, mitigated_at, resolved_at, resolution_notes
) VALUES

-- Incident 1: Plaid webhook failure (RESOLVED)
('PLAID_20240305_001', 'plaid', 'Plaid', 'webhook_delivery_drop', 'p1', 'resolved',
 NOW() - INTERVAL '3 days 8 hours', 'webhook_delivery_drop', 40.0, 99.0, 95.0,
 '["user_onboarding"]', 'p1', 450,
 NOW() - INTERVAL '3 days 7 hours 50 minutes', 'on-call-engineer',
 NOW() - INTERVAL '3 days 4 hours', NOW() - INTERVAL '2 days 16 hours',
 'Plaid API recovered after incident. Applied fix: increased timeout threshold from 8s to 12s. Circuit breaker auto-closed after 5 consecutive successful health checks. Root cause: Plaid deployment with latency regression.'),

-- Incident 2: KYC latency spike (RESOLVED)
('KYC_20240301_001', 'kyc_provider', 'KYC Provider', 'latency_degradation', 'p1', 'resolved',
 NOW() - INTERVAL '7 days 12 hours', 'latency_degradation_sustained', 11.2, 3.8, 8.0,
 '["user_onboarding"]', 'p1', 230,
 NOW() - INTERVAL '7 days 11 hours 45 minutes', 'on-call-engineer',
 NOW() - INTERVAL '7 days 6 hours', NOW() - INTERVAL '6 days 20 hours',
 'KYC Provider deployed new verification model with initial performance issues. Reverted to previous version after 6 hours. Onboarding completion recovered from 54% to 71% within 24 hours.'),

-- Incident 3: Twilio SMS failures (RESOLVED)
('TWILIO_20240225_001', 'twilio', 'Twilio', 'error_rate_spike', 'p1', 'resolved',
 NOW() - INTERVAL '12 days 14 hours', 'error_rate_spike_sustained', 22.0, 1.5, 12.0,
 '["user_onboarding"]', 'p1', 180,
 NOW() - INTERVAL '12 days 13 hours 30 minutes', 'on-call-engineer',
 NOW() - INTERVAL '12 days 10 hours', NOW() - INTERVAL '12 days 2 hours',
 'Twilio carrier issue affecting US SMS delivery. Issue resolved by Twilio network operations team. Fallback to email verification was activated during incident. No user impact beyond SMS delay.');

-- ============================================================================
-- Funnels
-- ============================================================================

INSERT INTO funnels (funnel_id, funnel_name, description, funnel_type)
VALUES
('user_onboarding', 'User Onboarding', 'Complete user onboarding flow for new borrowers', 'onboarding'),
('loan_disbursement', 'Loan Disbursement', 'Disbursement of approved loan funds', 'payment'),
('payment_collection', 'Payment Collection', 'Collection of loan repayments', 'payment');

-- ============================================================================
-- Funnel Steps
-- ============================================================================

INSERT INTO funnel_steps (funnel_id, step_id, step_name, step_order, expected_duration_seconds,
                          latency_tolerance_seconds, is_required, has_fallback)
VALUES
('user_onboarding', 'step_1_sms', 'SMS Verification', 1, 30, 5.0, TRUE, TRUE),
('user_onboarding', 'step_2_kyc', 'Identity Verification', 2, 10, 8.0, TRUE, FALSE),
('user_onboarding', 'step_3_plaid', 'Bank Account Linking', 3, 45, 8.0, TRUE, TRUE),
('user_onboarding', 'step_4_credit', 'Credit Check', 4, 15, 10.0, TRUE, FALSE),
('user_onboarding', 'step_5_disburse', 'Fund Disbursement', 5, 20, 10.0, TRUE, FALSE),
('loan_disbursement', 'credit_check', 'Credit Check', 1, 15, 10.0, TRUE, FALSE),
('loan_disbursement', 'fund_transfer', 'Fund Transfer', 2, 20, 10.0, TRUE, FALSE),
('payment_collection', 'payment_process', 'Process Payment', 1, 30, 10.0, TRUE, FALSE);

-- ============================================================================
-- Funnel Step Dependencies
-- ============================================================================

INSERT INTO funnel_step_dependencies (funnel_id, step_id, provider_id, dependency_order)
VALUES
('user_onboarding', 'step_1_sms', 'twilio', 1),
('user_onboarding', 'step_2_kyc', 'kyc_provider', 1),
('user_onboarding', 'step_3_plaid', 'plaid', 1),
('user_onboarding', 'step_4_credit', 'credit_bureau', 1),
('user_onboarding', 'step_5_disburse', 'stripe', 1),
('loan_disbursement', 'credit_check', 'credit_bureau', 1),
('loan_disbursement', 'fund_transfer', 'stripe', 1),
('payment_collection', 'payment_process', 'stripe', 1);

-- ============================================================================
-- Funnel Correlations (API health impact on funnel drop-off)
-- ============================================================================

INSERT INTO funnel_correlations (funnel_id, provider_id, step_id, correlation_strength,
                                baseline_drop_off_pct, degraded_drop_off_pct, drop_off_increase_pct)
VALUES
('user_onboarding', 'kyc_provider', 'step_2_kyc', 0.87, 4.2, 12.8, 8.6),
('user_onboarding', 'plaid', 'step_3_plaid', 0.79, 6.5, 18.3, 11.8),
('user_onboarding', 'stripe', 'step_5_disburse', 0.45, 2.1, 4.5, 2.4),
('loan_disbursement', 'credit_bureau', 'credit_check', 0.92, 1.2, 8.9, 7.7),
('loan_disbursement', 'stripe', 'fund_transfer', 0.58, 0.8, 3.2, 2.4);

-- ============================================================================
-- Funnel Metrics (30-day summary)
-- ============================================================================

INSERT INTO funnel_metrics (funnel_id, period_start, period_end, total_sessions,
                           completed_sessions, completion_rate_pct, total_dropoffs, avg_session_duration_seconds)
VALUES
('user_onboarding', NOW() - INTERVAL '30 days', NOW(), 6450, 4897, 75.9, 1553, 420),
('loan_disbursement', NOW() - INTERVAL '30 days', NOW(), 3200, 3040, 95.0, 160, 180),
('payment_collection', NOW() - INTERVAL '30 days', NOW(), 8900, 8756, 98.4, 144, 120);

COMMIT;
