"""
FastAPI Application for Integration Health Monitor

Wires together all backend modules (integration_registry, api_health_tracker,
webhook_monitor, incident_detector, onboarding_funnel, provider_scorecard)
and exposes them through REST endpoints and WebSockets.

This is the integration layer between backend logic and frontend dashboard.
"""

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
from typing import Optional, List
import json
import logging

# Import backend modules
import sys
sys.path.insert(0, '/sessions/youthful-eager-lamport/mnt/Portfolio/integration-health-monitor/src')

from integration_registry import build_lending_client_registry, CircuitState
from api_health_tracker import APIHealthTracker, APICallEvent, CircuitBreakerConfig
from webhook_monitor import WebhookMonitor, WebhookEvent, WebhookStatus
from incident_detector import IncidentDetector, DetectionRule, AnomalyType, Incident
from onboarding_funnel import OnboardingFunnel, FunnelStep, StepEvent, StepOutcome
from provider_scorecard import ProviderScorecard, ProviderSLAConfig

from .models import (
    IntegrationListResponse, IntegrationStatus, IntegrationHealth, LatencyMetrics,
    ErrorMetrics, CircuitBreakerStatus, HealthHistory, HealthHistoryPoint,
    WebhookDeliveryRate, WebhookDeadLetterResponse, DeadLetterEntry,
    IncidentListResponse, IncidentSummary, IncidentDetail, IncidentCorrelation,
    AcknowledgeIncidentRequest, FunnelHealthResponse, FunnelStepHealth,
    ScorecardListResponse, ProviderScorecard as ScorecardModel,
    WebhookPayload, WebhookReceiptResponse, DashboardSummary
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Integration Health Monitor API",
    description="Monitor third-party API integrations, webhook reliability, and customer impact",
    version="1.0.0"
)

# Add CORS middleware for dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize backend systems
registry = build_lending_client_registry()
health_tracker = APIHealthTracker()
webhook_monitor = WebhookMonitor()
incident_detector = IncidentDetector()
funnel_analyzer = OnboardingFunnel()
scorecard_system = ProviderScorecard()

# Configure health tracker circuit breakers from registry
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

# Configure incident detector rules
for provider in registry.list_all():
    incident_detector.register_provider(
        provider.id,
        provider.name,
        provider.blast_radius.value,
        registry.list_flows_affected_by(provider.id)
    )


# ---------------------------------------------------------------------------
# Health Endpoints
# ---------------------------------------------------------------------------

@app.get("/integrations", response_model=IntegrationListResponse, tags=["Health"])
async def list_integrations():
    """List all monitored integrations with current health status."""
    providers = registry.list_all()

    statuses = []
    for provider in providers:
        snapshot = health_tracker.take_snapshot(provider.id, window_seconds=300)
        cb = health_tracker.get_circuit_state(provider.id)

        statuses.append(IntegrationStatus(
            provider_id=provider.id,
            provider_name=provider.name,
            blast_radius=provider.blast_radius.value,
            health_status=snapshot.health_status.value,
            circuit_state=cb["state"],
            error_rate_pct=snapshot.error_rate_pct,
            latency_p95_ms=snapshot.latency_p95_ms,
            uptime_pct=None
        ))

    healthy = len([s for s in statuses if s.health_status == "healthy"])
    degraded = len([s for s in statuses if s.health_status == "degraded"])
    unhealthy = len([s for s in statuses if s.health_status == "unhealthy"])

    return IntegrationListResponse(
        timestamp=datetime.now(),
        total_integrations=len(providers),
        healthy=healthy,
        degraded=degraded,
        unhealthy=unhealthy,
        integrations=statuses
    )


@app.get("/integrations/{provider_id}/health", response_model=IntegrationHealth, tags=["Health"])
async def get_integration_health(provider_id: str, window_seconds: int = Query(300, ge=60)):
    """Get detailed health metrics for an integration."""
    try:
        provider = registry.get(provider_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    snapshot = health_tracker.take_snapshot(provider_id, window_seconds=window_seconds)
    cb = health_tracker.get_circuit_state(provider_id)

    return IntegrationHealth(
        provider_id=provider.id,
        provider_name=provider.name,
        health_status=snapshot.health_status.value,
        latency=LatencyMetrics(
            p50_ms=snapshot.latency_p50_ms,
            p95_ms=snapshot.latency_p95_ms,
            p99_ms=snapshot.latency_p99_ms,
            max_ms=snapshot.latency_max_ms
        ),
        errors=ErrorMetrics(
            error_rate_pct=snapshot.error_rate_pct,
            total_requests=snapshot.total_requests,
            failed_requests=snapshot.failed_requests,
            errors_by_category=snapshot.errors_by_category,
            errors_by_status_code=snapshot.errors_by_status_code
        ),
        circuit_breaker=CircuitBreakerStatus(
            state=cb["state"],
            last_state_change=cb.get("last_state_change"),
            total_trips=cb.get("total_trips", 0),
            should_send_traffic=cb["should_send_traffic"]
        ),
        requests_per_minute=snapshot.requests_per_minute,
        timestamp=snapshot.timestamp
    )


@app.get("/integrations/{provider_id}/history", response_model=HealthHistory, tags=["Health"])
async def get_integration_history(
    provider_id: str,
    window_hours: int = Query(24, ge=1, le=720),
    intervals: int = Query(12, ge=4, le=100)
):
    """Get time-series health data for charting."""
    try:
        provider = registry.get(provider_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    trend = health_tracker.get_latency_trend(provider_id, window_hours, intervals)

    data_points = []
    for interval in trend["intervals"]:
        data_points.append(HealthHistoryPoint(
            timestamp=datetime.fromisoformat(interval["interval_end"]),
            error_rate_pct=0,  # Would need additional tracking
            latency_p50_ms=interval.get("p50_ms", 0),
            latency_p95_ms=interval.get("p95_ms", 0),
            requests=interval.get("sample_count", 0)
        ))

    return HealthHistory(
        provider_id=provider_id,
        window_hours=window_hours,
        data_points=data_points
    )


# ---------------------------------------------------------------------------
# Webhook Endpoints
# ---------------------------------------------------------------------------

@app.post("/webhooks/{provider_id}", response_model=WebhookReceiptResponse, tags=["Webhooks"])
async def receive_webhook(provider_id: str, payload: WebhookPayload):
    """Receive and validate webhook from a provider."""
    try:
        provider = registry.get(provider_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    if not provider.webhook_config:
        raise HTTPException(status_code=400, detail=f"Provider '{provider_id}' does not accept webhooks")

    # Record the webhook event
    event = WebhookEvent(
        event_id=payload.event_id,
        provider_id=provider_id,
        event_type=payload.event_type,
        received_at=datetime.now(),
        provider_timestamp=payload.timestamp,
        payload_size_bytes=len(json.dumps(payload.payload).encode()),
        signature_valid=True,  # Signature verification would happen here
        status=WebhookStatus.RECEIVED
    )

    recorded = webhook_monitor.record_event(event)

    return WebhookReceiptResponse(
        status="received",
        event_id=recorded.event_id,
        provider_id=provider_id,
        message="Webhook received and queued for processing"
    )


@app.get("/webhooks/dead-letter", response_model=WebhookDeadLetterResponse, tags=["Webhooks"])
async def get_dead_letter_queue(provider_id: Optional[str] = None):
    """Get failed webhook deliveries for investigation."""
    entries = webhook_monitor.get_dlq_entries(provider_id=provider_id)
    dlq_summary = webhook_monitor.get_dlq_summary()

    return WebhookDeadLetterResponse(
        total_unresolved=dlq_summary["total_unresolved"],
        total_resolved=dlq_summary["total_resolved"],
        by_provider=dlq_summary["by_provider"],
        oldest_unresolved=dlq_summary.get("oldest_unresolved"),
        oldest_age_hours=dlq_summary.get("oldest_age_hours", 0),
        entries=[
            DeadLetterEntry(
                event_id=e.event_id,
                provider_id=e.provider_id,
                event_type=e.event_type,
                first_received_at=e.first_received_at,
                last_attempt_at=e.last_attempt_at,
                total_attempts=e.total_attempts,
                last_error=e.last_error,
                resolved=e.resolved
            )
            for e in entries
        ]
    )


# ---------------------------------------------------------------------------
# Incident Endpoints
# ---------------------------------------------------------------------------

@app.get("/incidents", response_model=IncidentListResponse, tags=["Incidents"])
async def list_incidents(
    severity: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500)
):
    """List detected incidents with severity and status."""
    incidents = incident_detector.get_all_incidents()

    if severity:
        incidents = [i for i in incidents if i.severity.value == severity]
    if status:
        incidents = [i for i in incidents if i.status.value == status]

    incidents = incidents[:limit]

    by_severity = {}
    for inc in incident_detector.get_all_incidents():
        key = inc.severity.value
        by_severity[key] = by_severity.get(key, 0) + 1

    return IncidentListResponse(
        timestamp=datetime.now(),
        total_incidents=len(incidents),
        by_severity=by_severity,
        incidents=[
            IncidentSummary(
                incident_id=i.incident_id,
                provider_id=i.provider_id,
                provider_name=i.provider_name,
                anomaly_type=i.anomaly_type.value,
                severity=i.severity.value,
                status=i.status.value,
                detected_at=i.detected_at,
                current_value=i.current_value,
                baseline_value=i.baseline_value,
                affected_flows=i.affected_flows,
                blast_radius=i.blast_radius
            )
            for i in incidents
        ]
    )


@app.get("/incidents/{incident_id}", response_model=IncidentDetail, tags=["Incidents"])
async def get_incident_detail(incident_id: str):
    """Get full incident details including timeline."""
    try:
        incident = incident_detector.get_incident(incident_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")

    from .models import IncidentTimelineEvent
    timeline = [
        IncidentTimelineEvent(timestamp=datetime.fromisoformat(e["timestamp"]), event=e["event"])
        for e in incident.timeline
    ]

    return IncidentDetail(
        incident_id=incident.incident_id,
        provider_id=incident.provider_id,
        provider_name=incident.provider_name,
        anomaly_type=incident.anomaly_type.value,
        severity=incident.severity.value,
        status=incident.status.value,
        detected_at=incident.detected_at,
        acknowledged_at=incident.acknowledged_at,
        mitigated_at=incident.mitigated_at,
        resolved_at=incident.resolved_at,
        current_value=incident.current_value,
        baseline_value=incident.baseline_value,
        threshold_value=incident.threshold_value,
        affected_flows=incident.affected_flows,
        blast_radius=incident.blast_radius,
        estimated_users_affected=incident.estimated_users_affected,
        resolution_notes=incident.resolution_notes,
        timeline=timeline
    )


@app.post("/incidents/{incident_id}/acknowledge", tags=["Incidents"])
async def acknowledge_incident(incident_id: str, request: AcknowledgeIncidentRequest):
    """Mark an incident as being investigated."""
    try:
        incident = incident_detector.get_incident(incident_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")

    incident_detector.acknowledge_incident(incident_id, request.acknowledged_by)

    return {"status": "acknowledged", "incident_id": incident_id}


@app.get("/incidents/{incident_id}/correlation", response_model=IncidentCorrelation, tags=["Incidents"])
async def get_incident_correlation(incident_id: str):
    """Show correlated alerts and affected integrations."""
    try:
        incident = incident_detector.get_incident(incident_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")

    # Find correlated incidents (same time window, same blast radius)
    all_incidents = incident_detector.get_all_incidents()
    time_window = timedelta(minutes=30)
    correlated = [
        i for i in all_incidents
        if i.incident_id != incident_id
        and abs((i.detected_at - incident.detected_at).total_seconds()) < time_window.total_seconds()
    ]

    return IncidentCorrelation(
        incident_id=incident_id,
        provider_id=incident.provider_id,
        primary_signal=incident.anomaly_type.value,
        correlated_signals=[
            {
                "incident_id": i.incident_id,
                "provider_id": i.provider_id,
                "anomaly_type": i.anomaly_type.value,
                "severity": i.severity.value
            }
            for i in correlated
        ],
        affected_providers=[incident.provider_id],
        affected_funnels=incident.affected_flows
    )


# ---------------------------------------------------------------------------
# Funnel Endpoints
# ---------------------------------------------------------------------------

@app.get("/funnels/{funnel_name}/health", response_model=FunnelHealthResponse, tags=["Funnels"])
async def get_funnel_health(funnel_name: str):
    """Get onboarding funnel health with per-step API correlation."""
    try:
        flow_health = registry.get_flow_health(funnel_name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Funnel '{funnel_name}' not found")

    providers = registry.get_flow_dependencies(funnel_name)

    # Get health for each provider in the funnel
    step_health = []
    for i, provider in enumerate(providers, 1):
        snapshot = health_tracker.take_snapshot(provider.id)
        step_health.append(FunnelStepHealth(
            step_id=provider.id,
            step_name=provider.name,
            step_order=i,
            completion_rate_pct=95.0,  # Would need actual funnel tracking
            drop_off_rate_pct=5.0 + (snapshot.error_rate_pct * 0.1),
            avg_duration_seconds=snapshot.latency_p95_ms / 1000,
            api_dependencies=[provider.id]
        ))

    # Identify bottleneck (highest drop-off or slowest)
    bottleneck = max(step_health, key=lambda x: x.drop_off_rate_pct + (x.avg_duration_seconds / 10))

    return FunnelHealthResponse(
        funnel_name=funnel_name,
        timestamp=datetime.now(),
        overall_completion_rate_pct=flow_health.get("chain_healthy", True) and 94.0 or 87.0,
        overall_drop_off_rate_pct=flow_health.get("chain_healthy", True) and 6.0 or 13.0,
        steps=step_health,
        bottleneck_step=bottleneck,
        api_correlation_analysis={
            "single_points_of_failure": flow_health.get("single_points_of_failure", []),
            "theoretical_chain_uptime_pct": flow_health.get("theoretical_chain_uptime_pct", 99.0),
            "total_expected_latency_ms": flow_health.get("total_expected_latency_ms", 0)
        }
    )


# ---------------------------------------------------------------------------
# Scorecard Endpoints
# ---------------------------------------------------------------------------

@app.get("/providers/{provider_name}/scorecard", response_model=ScorecardModel, tags=["Scorecard"])
async def get_provider_scorecard(provider_name: str):
    """Get SLA compliance, reliability score, and cost analysis."""
    try:
        provider = registry.get(provider_name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found")

    snapshot = health_tracker.take_snapshot(provider_name)

    # Calculate composite score (0-100)
    uptime_pct = 99.5  # Would track actual uptime
    incident_count = len(incident_detector.get_incidents_for_provider(provider_name))
    latency_score = max(0, 100 - (snapshot.latency_p95_ms / 100))
    reliability_score = max(0, 100 - (incident_count * 10))

    composite_score = (
        (uptime_pct / 100 * 35) +
        (latency_score * 0.3) +
        (reliability_score * 0.35)
    )

    def get_grade(score):
        if score >= 90:
            return "excellent"
        elif score >= 75:
            return "good"
        elif score >= 60:
            return "concerning"
        else:
            return "unacceptable"

    def get_recommendation(score, uptime_pct):
        if score >= 85 and uptime_pct >= provider.sla.guaranteed_uptime_pct:
            return "renew"
        elif score < 75 and incident_count > 3:
            return "replace"
        else:
            return "renegotiate"

    return ScorecardModel(
        provider_id=provider.id,
        provider_name=provider.name,
        composite_score=round(composite_score, 1),
        grade=get_grade(composite_score),
        uptime_pct=uptime_pct,
        sla_target_pct=provider.sla.guaranteed_uptime_pct,
        sla_status="compliant" if uptime_pct >= provider.sla.guaranteed_uptime_pct else "breached",
        incident_count_30d=incident_count,
        mean_time_to_resolve_minutes=45.0 if incident_count > 0 else 0,
        cost_per_call_usd=0.002,
        total_cost_30d=500.0,
        p95_latency_ms=snapshot.latency_p95_ms,
        latency_trend="stable",
        renewal_recommendation=get_recommendation(composite_score, uptime_pct)
    )


@app.get("/providers/scorecards", response_model=ScorecardListResponse, tags=["Scorecard"])
async def list_provider_scorecards():
    """Get scorecards for all providers."""
    providers = registry.list_all()
    scorecards = []

    for provider in providers:
        snapshot = health_tracker.take_snapshot(provider.id)
        incident_count = len(incident_detector.get_incidents_for_provider(provider.id))

        uptime_pct = 99.5
        latency_score = max(0, 100 - (snapshot.latency_p95_ms / 100))
        reliability_score = max(0, 100 - (incident_count * 10))

        composite_score = (
            (uptime_pct / 100 * 35) +
            (latency_score * 0.3) +
            (reliability_score * 0.35)
        )

        def get_grade(score):
            if score >= 90:
                return "excellent"
            elif score >= 75:
                return "good"
            elif score >= 60:
                return "concerning"
            else:
                return "unacceptable"

        def get_recommendation(score, uptime_pct):
            if score >= 85 and uptime_pct >= provider.sla.guaranteed_uptime_pct:
                return "renew"
            elif score < 75 and incident_count > 3:
                return "replace"
            else:
                return "renegotiate"

        scorecards.append(ScorecardModel(
            provider_id=provider.id,
            provider_name=provider.name,
            composite_score=round(composite_score, 1),
            grade=get_grade(composite_score),
            uptime_pct=uptime_pct,
            sla_target_pct=provider.sla.guaranteed_uptime_pct,
            sla_status="compliant" if uptime_pct >= provider.sla.guaranteed_uptime_pct else "breached",
            incident_count_30d=incident_count,
            mean_time_to_resolve_minutes=45.0 if incident_count > 0 else 0,
            cost_per_call_usd=0.002,
            total_cost_30d=500.0,
            p95_latency_ms=snapshot.latency_p95_ms,
            latency_trend="stable",
            renewal_recommendation=get_recommendation(composite_score, uptime_pct)
        ))

    # Sort by composite score (best first)
    scorecards.sort(key=lambda x: x.composite_score, reverse=True)

    return ScorecardListResponse(
        timestamp=datetime.now(),
        providers=scorecards
    )


# ---------------------------------------------------------------------------
# Dashboard Endpoints
# ---------------------------------------------------------------------------

@app.get("/dashboard/summary", response_model=DashboardSummary, tags=["Dashboard"])
async def get_dashboard_summary():
    """High-level dashboard summary."""
    providers = registry.list_all()
    incidents = incident_detector.get_all_incidents()

    # Integration health summary
    healthy = 0
    degraded = 0
    unhealthy = 0

    for provider in providers:
        snapshot = health_tracker.take_snapshot(provider.id)
        if snapshot.health_status.value == "healthy":
            healthy += 1
        elif snapshot.health_status.value == "degraded":
            degraded += 1
        else:
            unhealthy += 1

    # Webhook health summary
    webhook_rates = webhook_monitor.get_delivery_rates_all_providers()
    webhook_healthy = len([r for r in webhook_rates if r["is_healthy"]])
    webhook_unhealthy = len([r for r in webhook_rates if not r["is_healthy"]])

    # Incident summary
    critical_incidents = [i for i in incidents if i.severity.value in ["p0", "p1"]]

    # Top bottleneck funnels
    top_funnels = []
    for flow_name in ["user_onboarding", "loan_disbursement", "payment_collection"]:
        try:
            flow_health = registry.get_flow_health(flow_name)
            if not flow_health.get("chain_healthy"):
                top_funnels.append({
                    "name": flow_name,
                    "completion_rate": 85.0,
                    "bottleneck": flow_health.get("single_points_of_failure", ["unknown"])[0]
                })
        except:
            pass

    # Top unreliable providers
    top_unreliable = []
    for provider in providers:
        incident_count = len(incident_detector.get_incidents_for_provider(provider.id))
        if incident_count > 0:
            snapshot = health_tracker.take_snapshot(provider.id)
            top_unreliable.append({
                "name": provider.name,
                "incidents_30d": incident_count,
                "error_rate": snapshot.error_rate_pct,
                "health_status": snapshot.health_status.value
            })

    top_unreliable.sort(key=lambda x: x["incidents_30d"], reverse=True)

    return DashboardSummary(
        timestamp=datetime.now(),
        integrations_healthy=healthy,
        integrations_degraded=degraded,
        integrations_unhealthy=unhealthy,
        active_incidents=len([i for i in incidents if i.status.value != "resolved"]),
        critical_incidents=len(critical_incidents),
        webhook_delivery_healthy=webhook_healthy,
        webhook_delivery_unhealthy=webhook_unhealthy,
        top_bottleneck_funnels=top_funnels[:3],
        top_unreliable_providers=top_unreliable[:5]
    )


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["System"])
async def health_check():
    """System health check."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(),
        "integrations_monitored": len(registry.list_all()),
        "incidents_tracked": len(incident_detector.get_all_incidents())
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
