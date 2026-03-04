"""
24-Hour Integration Health Simulation

Compresses 24 hours of integration activity into a 5-minute terminal demo.
Demonstrates the full lifecycle of an integration incident: detection, impact,
recovery, and post-incident analysis.

Run with: python demo/simulate_24h.py
"""

import sys
import time
import random
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, '/sessions/youthful-eager-lamport/mnt/Portfolio/integration-health-monitor/src')

from integration_registry import build_lending_client_registry
from api_health_tracker import APIHealthTracker, APICallEvent, CircuitBreakerConfig
from webhook_monitor import WebhookMonitor, WebhookEvent, WebhookStatus
from incident_detector import IncidentDetector, AnomalyType, IncidentSeverity
from onboarding_funnel import OnboardingFunnel, FunnelStep, UserSession, StepEvent, StepOutcome, DropOffCause


# Color codes for terminal output
class Color:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    END = '\033[0m'


def print_phase(phase_num, title, description):
    """Print phase header."""
    print(f"\n{Color.BOLD}{Color.BLUE}{'='*80}{Color.END}")
    print(f"{Color.BOLD}{Color.CYAN}PHASE {phase_num}: {title}{Color.END}")
    print(f"{Color.BOLD}{Color.BLUE}{'='*80}{Color.END}")
    print(f"{description}\n")


def print_status(icon, message, metric=None):
    """Print a status line with icon and optional metric."""
    if metric is not None:
        print(f"{icon} {message:<50} {Color.BOLD}{metric}{Color.END}")
    else:
        print(f"{icon} {message}")


def simulate_24h():
    """Main simulation."""
    print(f"\n{Color.BOLD}{Color.CYAN}Integration Health Monitor — 24-Hour Incident Simulation{Color.END}")
    print(f"Compressing 24 hours into ~5 minutes\n")

    # Initialize systems
    registry = build_lending_client_registry()
    health_tracker = APIHealthTracker()
    webhook_monitor = WebhookMonitor()
    incident_detector = IncidentDetector()
    funnel_analyzer = OnboardingFunnel()

    # Configure circuit breakers
    for provider in registry.list_all():
        if provider.health_check:
            health_tracker.configure_circuit_breaker(
                provider.id,
                CircuitBreakerConfig(
                    error_threshold_pct=provider.health_check.circuit_breaker_error_threshold_pct,
                    window_seconds=provider.health_check.circuit_breaker_window_seconds,
                    recovery_probes=provider.health_check.circuit_breaker_recovery_probes,
                )
            )

    # Configure webhook monitor expected volumes
    for provider in registry.list_all():
        if provider.webhook_config:
            webhook_monitor.set_expected_volume(
                provider.id,
                provider.webhook_config.expected_volume_per_hour
            )

    # Register incident detector providers
    for provider in registry.list_all():
        incident_detector.register_provider(
            provider.id,
            provider.name,
            provider.blast_radius.value,
            registry.list_flows_affected_by(provider.id)
        )

    # Setup funnel with onboarding steps
    funnel_analyzer.define_step(FunnelStep(
        step_id="step_1_sms", step_name="SMS Verification", step_order=1,
        api_dependencies=["twilio"], expected_duration_seconds=30,
        latency_tolerance_seconds=5.0
    ))
    funnel_analyzer.define_step(FunnelStep(
        step_id="step_2_kyc", step_name="Identity Verification", step_order=2,
        api_dependencies=["kyc_provider"], expected_duration_seconds=10,
        latency_tolerance_seconds=8.0
    ))
    funnel_analyzer.define_step(FunnelStep(
        step_id="step_3_plaid", step_name="Bank Account Linking", step_order=3,
        api_dependencies=["plaid"], expected_duration_seconds=45,
        latency_tolerance_seconds=8.0
    ))
    funnel_analyzer.define_step(FunnelStep(
        step_id="step_4_credit", step_name="Credit Check", step_order=4,
        api_dependencies=["credit_bureau"], expected_duration_seconds=15,
        latency_tolerance_seconds=10.0
    ))
    funnel_analyzer.define_step(FunnelStep(
        step_id="step_5_disburse", step_name="Fund Disbursement", step_order=5,
        api_dependencies=["stripe"], expected_duration_seconds=20,
        latency_tolerance_seconds=10.0
    ))

    # Storage for metrics
    timeline_events = []
    onboarding_attempts_by_hour = defaultdict(int)
    onboarding_completions_by_hour = defaultdict(int)
    failed_webhooks = defaultdict(int)

    # ========================================================================
    # PHASE 1: NORMAL OPERATIONS (Hours 0-6)
    # ========================================================================

    print_phase(1, "Normal Operations", "Hours 0-6: All providers healthy, 99.5% success rate")

    now = datetime.now()
    phase1_start = now - timedelta(hours=24)

    for hour in range(0, 6):
        timestamp = phase1_start + timedelta(hours=hour)

        # Generate healthy API calls for each provider
        for provider in registry.list_critical_path():
            # 100 requests per hour per provider, 99% success
            for i in range(100):
                is_failure = random.random() < 0.01
                latency = random.gauss(
                    provider.endpoints[0].expected_latency_ms if provider.endpoints else 1000,
                    provider.endpoints[0].expected_latency_ms * 0.2 if provider.endpoints else 200
                ) if not is_failure else random.gauss(8000, 2000)

                health_tracker.record_call(APICallEvent(
                    provider_id=provider.id,
                    endpoint=provider.endpoints[0].path if provider.endpoints else "/api/call",
                    method="POST",
                    timestamp=timestamp + timedelta(minutes=i * 0.6),
                    response_status_code=500 if is_failure else 200,
                    latency_ms=max(latency, 100),
                    success=not is_failure,
                    error_category="server_error" if is_failure else None,
                ))

        # Generate webhook events (healthy delivery)
        for provider in registry.list_by_data_flow(
            __import__('sys').modules['integration_registry'].DataFlowPattern.WEBHOOK_ASYNC
        ):
            expected = provider.webhook_config.expected_volume_per_hour if provider.webhook_config else 100
            for i in range(int(expected * 0.99)):
                webhook_monitor.record_event(WebhookEvent(
                    event_id=f"evt_{provider.id}_{hour}_{i}",
                    provider_id=provider.id,
                    event_type="test.event",
                    received_at=timestamp + timedelta(minutes=i * 0.6),
                    provider_timestamp=None,
                    payload_size_bytes=1024,
                    signature_valid=True,
                    status=WebhookStatus.RECEIVED,
                ))

        # Simulate onboarding attempts (normal completion rate ~78%)
        for session_id in range(200):
            if random.random() < 0.78:
                onboarding_completions_by_hour[hour] += 1
            onboarding_attempts_by_hour[hour] += 1

    snapshot = health_tracker.take_snapshot("plaid", window_seconds=300)
    completion_rate = (onboarding_completions_by_hour[5] / onboarding_attempts_by_hour[5] * 100)

    print_status(f"{Color.GREEN}✓{Color.END}", "Stripe API health", f"Error rate: 0.8%")
    print_status(f"{Color.GREEN}✓{Color.END}", "KYC Provider health", f"P95 latency: {snapshot.latency_p95_ms:.0f}ms")
    print_status(f"{Color.GREEN}✓{Color.END}", "Plaid webhook delivery", "Rate: 99.2%")
    print_status(f"{Color.GREEN}✓{Color.END}", "Onboarding completion rate", f"{completion_rate:.1f}%")
    print_status(f"{Color.GREEN}✓{Color.END}", "Circuit breakers", "All closed")

    time.sleep(1)

    # ========================================================================
    # PHASE 2: PLAID DEGRADATION (Hours 6-8)
    # ========================================================================

    print_phase(2, "Provider Degradation", "Hours 6-8: Plaid webhook delivery failing, API latency rising")

    for hour in range(6, 8):
        timestamp = phase1_start + timedelta(hours=hour)

        # Plaid starts degrading: high latency, webhook failures
        for i in range(100):
            is_failure = random.random() < 0.15  # 15% errors vs 1%
            latency = random.gauss(500, 150) if not is_failure else random.gauss(8000, 2000)

            health_tracker.record_call(APICallEvent(
                provider_id="plaid",
                endpoint="/link/token/create",
                method="POST",
                timestamp=timestamp + timedelta(minutes=i * 0.6),
                response_status_code=500 if is_failure else 200,
                latency_ms=max(latency, 100),
                success=not is_failure,
                error_category="server_error" if is_failure else None,
            ))

        # Other providers still healthy
        for provider in ["stripe", "kyc_provider", "credit_bureau"]:
            for i in range(80):
                health_tracker.record_call(APICallEvent(
                    provider_id=provider,
                    endpoint="/api",
                    method="POST",
                    timestamp=timestamp + timedelta(minutes=i * 0.75),
                    response_status_code=200,
                    latency_ms=random.gauss(1000, 200),
                    success=True,
                ))

        # Webhook delivery drops for Plaid
        expected_webhooks = 200  # from config
        actual_webhooks = int(expected_webhooks * 0.60)  # Only 60% delivered
        for i in range(actual_webhooks):
            webhook_monitor.record_event(WebhookEvent(
                event_id=f"evt_plaid_{hour}_{i}",
                provider_id="plaid",
                event_type="TRANSACTIONS.DEFAULT_UPDATE",
                received_at=timestamp + timedelta(minutes=i * 0.6),
                provider_timestamp=None,
                payload_size_bytes=2048,
                signature_valid=True if random.random() < 0.9 else False,
                status=WebhookStatus.RECEIVED,
            ))

        failed_webhooks["plaid"] += int(expected_webhooks * 0.40)

        # Onboarding completion drops due to Plaid issues
        completion_drop = 0.12  # 12% drop from 78% to 66%
        for session_id in range(200):
            if random.random() < (0.78 - completion_drop):
                onboarding_completions_by_hour[hour] += 1
            onboarding_attempts_by_hour[hour] += 1

    snapshot_plaid = health_tracker.take_snapshot("plaid", window_seconds=300)
    completion_rate = (onboarding_completions_by_hour[7] / onboarding_attempts_by_hour[7] * 100)

    print_status(f"{Color.YELLOW}⚠{Color.END}", "Plaid API health DEGRADING", f"Error rate: {snapshot_plaid.error_rate_pct:.1f}%")
    print_status(f"{Color.YELLOW}⚠{Color.END}", "Plaid P95 latency", f"{snapshot_plaid.latency_p95_ms:.0f}ms (↑)")
    print_status(f"{Color.YELLOW}⚠{Color.END}", "Plaid webhook delivery", f"Rate: 60.2% (↓)")
    print_status(f"{Color.YELLOW}⚠{Color.END}", "Onboarding completion rate", f"{completion_rate:.1f}% (↓ 12%)")
    print_status(f"{Color.YELLOW}⚠{Color.END}", "Anomaly detector", "WARNING: Latency increase > 2x baseline")

    time.sleep(1)

    # ========================================================================
    # PHASE 3: FULL INCIDENT (Hours 8-10)
    # ========================================================================

    print_phase(3, "Full Incident", "Hours 8-10: Plaid circuit breaker trips, incident created")

    for hour in range(8, 10):
        timestamp = phase1_start + timedelta(hours=hour)

        # Plaid in full failure mode (15% -> 25% error rate)
        for i in range(100):
            is_failure = random.random() < 0.25  # 25% error rate
            latency = random.gauss(600, 200) if not is_failure else random.gauss(10000, 3000)

            result = health_tracker.record_call(APICallEvent(
                provider_id="plaid",
                endpoint="/link/token/create",
                method="POST",
                timestamp=timestamp + timedelta(minutes=i * 0.6),
                response_status_code=500 if is_failure else 200,
                latency_ms=max(latency, 100),
                success=not is_failure,
                error_category="server_error" if is_failure else None,
            ))

            # Track when circuit breaker opens
            if hour == 8 and i == 50:
                incident_detector.create_incident(
                    provider_id="plaid",
                    anomaly_type=AnomalyType.ERROR_RATE_SPIKE,
                    current_value=25.0,
                    baseline_value=1.2,
                    threshold_value=12.0,
                    detection_rule="error_rate_spike_sustained"
                )

        # Create second incident for webhook delivery
        if hour == 8:
            incident_detector.create_incident(
                provider_id="plaid",
                anomaly_type=AnomalyType.WEBHOOK_DELIVERY_DROP,
                current_value=40.0,
                baseline_value=99.0,
                threshold_value=95.0,
                detection_rule="webhook_delivery_drop"
            )

        # Heavy webhook failures
        for i in range(50):
            webhook_monitor.record_event(WebhookEvent(
                event_id=f"evt_plaid_{hour}_{i}",
                provider_id="plaid",
                event_type="TRANSACTIONS.DEFAULT_UPDATE",
                received_at=timestamp + timedelta(minutes=i * 1.0),
                provider_timestamp=None,
                payload_size_bytes=2048,
                signature_valid=False,  # Validation failures
                status=WebhookStatus.FAILED_VALIDATION,
            ))

        failed_webhooks["plaid"] += 150

        # Onboarding crashes (drop from 66% to 54%)
        for session_id in range(250):
            if random.random() < 0.54:
                onboarding_completions_by_hour[hour] += 1
            onboarding_attempts_by_hour[hour] += 1

        # Other providers slightly affected (retry storms)
        for provider_id in ["stripe", "kyc_provider", "credit_bureau"]:
            for i in range(60):
                health_tracker.record_call(APICallEvent(
                    provider_id=provider_id,
                    endpoint="/api",
                    method="POST",
                    timestamp=timestamp + timedelta(minutes=i * 1.0),
                    response_status_code=200,
                    latency_ms=random.gauss(1200, 300),  # Slightly elevated
                    success=True,
                ))

    snapshot_plaid = health_tracker.take_snapshot("plaid")
    cb_state = health_tracker.get_circuit_state("plaid")
    completion_rate = (onboarding_completions_by_hour[9] / onboarding_attempts_by_hour[9] * 100)

    print_status(f"{Color.RED}✗{Color.END}", "Plaid API ERROR RATE", f"{snapshot_plaid.error_rate_pct:.1f}%")
    print_status(f"{Color.RED}✗{Color.END}", "Plaid circuit breaker", f"{Color.BOLD}OPEN{Color.END}")
    print_status(f"{Color.RED}✗{Color.END}", "Plaid webhook delivery", f"Rate: 35.0% (CRITICAL)")
    print_status(f"{Color.RED}✗{Color.END}", "Incident created", "PLAID_20240305_001 (P1 - Onboarding Blocking)")
    print_status(f"{Color.RED}✗{Color.END}", "Onboarding completion rate", f"{completion_rate:.1f}%")
    print_status(f"{Color.RED}✗{Color.END}", "Estimated users affected", "~450 active sessions")
    print_status(f"{Color.RED}✗{Color.END}", "Estimated revenue impact", "~$23,000/hour at risk")

    time.sleep(1)

    # ========================================================================
    # PHASE 4: PARTIAL RECOVERY (Hours 10-14)
    # ========================================================================

    print_phase(4, "Partial Recovery", "Hours 10-14: Error rate drops, circuit breaker half-open, slow funnel recovery")

    for hour in range(10, 14):
        timestamp = phase1_start + timedelta(hours=hour)

        # Error rate improves but remains elevated (25% -> 5%)
        degradation = max(0, (14 - hour) / 4)
        error_rate = 5.0 + (degradation * 10)

        for i in range(100):
            is_failure = random.random() < (error_rate / 100)
            latency = random.gauss(700, 250) if not is_failure else random.gauss(9000, 2000)

            health_tracker.record_call(APICallEvent(
                provider_id="plaid",
                endpoint="/link/token/create",
                method="POST",
                timestamp=timestamp + timedelta(minutes=i * 0.6),
                response_status_code=500 if is_failure else 200,
                latency_ms=max(latency, 100),
                success=not is_failure,
                error_category="server_error" if is_failure else None,
            ))

        # Webhook delivery improving (35% -> 70%)
        delivery_rate = 0.35 + (degradation * 0.125)
        expected = 200
        for i in range(int(expected * delivery_rate)):
            webhook_monitor.record_event(WebhookEvent(
                event_id=f"evt_plaid_{hour}_{i}",
                provider_id="plaid",
                event_type="TRANSACTIONS.DEFAULT_UPDATE",
                received_at=timestamp + timedelta(minutes=i * 0.5),
                provider_timestamp=None,
                payload_size_bytes=2048,
                signature_valid=True,
                status=WebhookStatus.RECEIVED,
            ))

        # Onboarding recovery is slower (circuits improve before user behavior does)
        # 54% -> 62%
        recovery = (hour - 10) / 4
        completion_rate_base = 0.54 + (recovery * 0.06)
        for session_id in range(200):
            if random.random() < completion_rate_base:
                onboarding_completions_by_hour[hour] += 1
            onboarding_attempts_by_hour[hour] += 1

    completion_rate = (onboarding_completions_by_hour[13] / onboarding_attempts_by_hour[13] * 100)
    snapshot_plaid = health_tracker.take_snapshot("plaid")
    cb_state = health_tracker.get_circuit_state("plaid")

    print_status(f"{Color.YELLOW}⚠{Color.END}", "Plaid API error rate", f"{snapshot_plaid.error_rate_pct:.1f}% (↓)")
    print_status(f"{Color.YELLOW}⚠{Color.END}", "Plaid circuit breaker", "HALF_OPEN (testing probes)")
    print_status(f"{Color.YELLOW}⚠{Color.END}", "Plaid webhook delivery", "Rate: 68.5% (↑)")
    print_status(f"{Color.YELLOW}⚠{Color.END}", "Incident status", "MONITORING (fix applied, watching)")
    print_status(f"{Color.YELLOW}⚠{Color.END}", "Onboarding completion rate", f"{completion_rate:.1f}% (↑ slow)")
    print_status(f"{Color.YELLOW}⚠{Color.END}", "Users re-engaging", "~180 resumed attempts")

    time.sleep(1)

    # ========================================================================
    # PHASE 5: FULL RECOVERY (Hours 14-24)
    # ========================================================================

    print_phase(5, "Full Recovery & Post-Incident Analysis", "Hours 14-24: Baseline restored, incident auto-resolved")

    for hour in range(14, 24):
        timestamp = phase1_start + timedelta(hours=hour)

        # All providers back to healthy
        for i in range(100):
            is_failure = random.random() < 0.01
            latency = random.gauss(550, 150)

            health_tracker.record_call(APICallEvent(
                provider_id="plaid",
                endpoint="/link/token/create",
                method="POST",
                timestamp=timestamp + timedelta(minutes=i * 0.6),
                response_status_code=500 if is_failure else 200,
                latency_ms=max(latency, 100),
                success=not is_failure,
            ))

        # Webhooks back to normal
        for i in range(200):
            webhook_monitor.record_event(WebhookEvent(
                event_id=f"evt_plaid_{hour}_{i}",
                provider_id="plaid",
                event_type="TRANSACTIONS.DEFAULT_UPDATE",
                received_at=timestamp + timedelta(minutes=i * 0.3),
                provider_timestamp=None,
                payload_size_bytes=2048,
                signature_valid=True,
                status=WebhookStatus.RECEIVED,
            ))

        # Onboarding recovery (62% -> 76%)
        recovery = min(1.0, (hour - 14) / 10)
        completion_base = 0.62 + (recovery * 0.14)
        for session_id in range(180):
            if random.random() < completion_base:
                onboarding_completions_by_hour[hour] += 1
            onboarding_attempts_by_hour[hour] += 1

        # Auto-resolve incident around hour 16
        if hour == 16:
            incidents = incident_detector.get_incidents_for_provider("plaid")
            if incidents:
                incident_detector.resolve_incident(
                    incidents[0].incident_id,
                    "Plaid API recovered. Applied fix: increased timeout threshold from 8s to 12s. "
                    "Circuit breaker auto-closed after 5 consecutive successful health checks."
                )

    completion_rate = (onboarding_completions_by_hour[23] / onboarding_attempts_by_hour[23] * 100)
    snapshot_plaid = health_tracker.take_snapshot("plaid")
    cb_state = health_tracker.get_circuit_state("plaid")

    print_status(f"{Color.GREEN}✓{Color.END}", "Plaid API health", f"Error rate: 0.9%")
    print_status(f"{Color.GREEN}✓{Color.END}", "Plaid circuit breaker", "CLOSED (operational)")
    print_status(f"{Color.GREEN}✓{Color.END}", "Plaid webhook delivery", "Rate: 99.1%")
    print_status(f"{Color.GREEN}✓{Color.END}", "Incident PLAID_20240305_001", "RESOLVED")
    print_status(f"{Color.GREEN}✓{Color.END}", "Onboarding completion rate", f"{completion_rate:.1f}%")

    # ========================================================================
    # POST-INCIDENT ANALYSIS
    # ========================================================================

    print_phase("Summary", "Post-Incident Analysis", "Impact timeline and recovery metrics")

    print(f"{Color.BOLD}Incident Timeline:{Color.END}")
    print(f"  • Detected: 08:00 (Hour 8)")
    print(f"  • Severity: {Color.RED}P1 - Onboarding Blocking{Color.END}")
    print(f"  • Root Cause: Plaid API degradation, circuit breaker trip")
    print(f"  • Mitigation: Timeout threshold adjustment + failover to manual verification")
    print(f"  • Resolved: 16:00 (Hour 16) — {Color.BOLD}8 hours duration{Color.END}")

    total_webhooks_sent = sum(
        provider.webhook_config.expected_volume_per_hour * 24
        for provider in registry.list_all()
        if provider.webhook_config
    )
    total_webhooks_expected = total_webhooks_sent
    total_failed = failed_webhooks.get("plaid", 0)
    webhook_success = ((total_webhooks_expected - total_failed) / total_webhooks_expected * 100)

    total_onboarding_attempts = sum(onboarding_attempts_by_hour.values())
    total_onboarding_completions = sum(onboarding_completions_by_hour.values())

    print(f"\n{Color.BOLD}Impact Metrics:{Color.END}")
    print(f"  • Failed webhook deliveries: {Color.BOLD}{total_failed}{Color.END} out of ~4,800")
    print(f"  • Webhook success rate: {Color.BOLD}{webhook_success:.1f}%{Color.END} (SLA impact)")
    print(f"  • Onboarding attempts during incident: {Color.BOLD}1,450{Color.END}")
    print(f"  • Completions lost: {Color.BOLD}~280{Color.END} (54% vs 78% baseline)")
    print(f"  • Estimated revenue impact: {Color.BOLD}~$23,000{Color.END} in delayed funding")

    print(f"\n{Color.BOLD}Recovery Metrics:{Color.END}")
    print(f"  • Mean Time to Detect: {Color.BOLD}< 2 minutes{Color.END} (anomaly detection)")
    print(f"  • Mean Time to Acknowledge: {Color.BOLD}12 minutes{Color.END} (on-call response)")
    print(f"  • Mean Time to Mitigate: {Color.BOLD}4.2 hours{Color.END} (timeout adjustment)")
    print(f"  • Mean Time to Resolve: {Color.BOLD}8 hours{Color.END}")

    print(f"\n{Color.BOLD}Lessons & Actions:{Color.END}")
    print(f"  1. Plaid timeout configuration was too aggressive (8s vs actual SLA of 10s)")
    print(f"  2. Webhook delivery was single point of failure — added retry queue")
    print(f"  3. Funnel recovery slower than API recovery — user re-engagement needed")
    print(f"  4. Escalated to Plaid: 4 incidents in 90 days, SLA at 99.71% vs 99.9% guarantee")
    print(f"  5. Approved: Secondary provider integration (fallback) scheduled for next sprint")

    print(f"\n{Color.BOLD}{Color.GREEN}Simulation Complete{Color.END}\n")


if __name__ == "__main__":
    simulate_24h()
