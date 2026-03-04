"""
Pydantic models for the Integration Health Monitor API.

These models define the request/response schemas for all endpoints,
with validation, serialization, and documentation.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Integration Health Models
# ---------------------------------------------------------------------------

class LatencyMetrics(BaseModel):
    """Latency percentiles for an integration."""
    p50_ms: float = Field(..., description="50th percentile latency in milliseconds")
    p95_ms: float = Field(..., description="95th percentile latency")
    p99_ms: float = Field(..., description="99th percentile latency")
    max_ms: float = Field(..., description="Maximum observed latency")


class ErrorMetrics(BaseModel):
    """Error rate breakdown for an integration."""
    error_rate_pct: float = Field(..., description="Overall error rate percentage")
    total_requests: int = Field(..., description="Total requests in the window")
    failed_requests: int = Field(..., description="Number of failed requests")
    errors_by_category: Dict[str, int] = Field(default_factory=dict)
    errors_by_status_code: Dict[int, int] = Field(default_factory=dict)


class CircuitBreakerStatus(BaseModel):
    """Circuit breaker state for a provider."""
    state: str = Field(..., description="closed, open, or half_open")
    last_state_change: Optional[datetime] = None
    total_trips: int = Field(default=0)
    should_send_traffic: bool = Field(...)


class IntegrationHealth(BaseModel):
    """Detailed health metrics for a single integration."""
    provider_id: str
    provider_name: str
    health_status: str = Field(..., description="healthy, degraded, unhealthy, or unknown")
    latency: LatencyMetrics
    errors: ErrorMetrics
    circuit_breaker: CircuitBreakerStatus
    requests_per_minute: float
    timestamp: datetime


class IntegrationStatus(BaseModel):
    """Summary status for an integration."""
    provider_id: str
    provider_name: str
    blast_radius: str
    health_status: str
    circuit_state: str
    error_rate_pct: float
    latency_p95_ms: float
    uptime_pct: Optional[float] = None


class IntegrationListResponse(BaseModel):
    """List of integrations with current health status."""
    timestamp: datetime
    total_integrations: int
    healthy: int
    degraded: int
    unhealthy: int
    integrations: List[IntegrationStatus]


class HealthHistoryPoint(BaseModel):
    """A single point in time-series health data."""
    timestamp: datetime
    error_rate_pct: float
    latency_p50_ms: float
    latency_p95_ms: float
    requests: int


class HealthHistory(BaseModel):
    """Time-series health data for an integration."""
    provider_id: str
    window_hours: int
    data_points: List[HealthHistoryPoint]


# ---------------------------------------------------------------------------
# Webhook Models
# ---------------------------------------------------------------------------

class WebhookDeliveryRate(BaseModel):
    """Webhook delivery metrics for a provider."""
    provider_id: str
    window_hours: int
    expected_events: int
    total_received: int
    valid_received: int
    failed: int
    duplicates: int
    delivery_rate_pct: float
    is_healthy: bool
    is_critical: bool


class DeadLetterEntry(BaseModel):
    """A webhook event that failed to process."""
    event_id: str
    provider_id: str
    event_type: str
    first_received_at: datetime
    last_attempt_at: datetime
    total_attempts: int
    last_error: str
    resolved: bool = False


class WebhookDeadLetterResponse(BaseModel):
    """Dead letter queue status."""
    total_unresolved: int
    total_resolved: int
    by_provider: Dict[str, int]
    oldest_unresolved: Optional[datetime] = None
    oldest_age_hours: float
    entries: List[DeadLetterEntry]


# ---------------------------------------------------------------------------
# Incident Models
# ---------------------------------------------------------------------------

class IncidentTimelineEvent(BaseModel):
    """A single event in an incident's timeline."""
    timestamp: datetime
    event: str


class IncidentSummary(BaseModel):
    """Summary of an incident."""
    incident_id: str
    provider_id: str
    provider_name: str
    anomaly_type: str
    severity: str = Field(..., description="p0, p1, p2, or p3")
    status: str = Field(..., description="detected, acknowledged, mitigating, monitoring, or resolved")
    detected_at: datetime
    current_value: float
    baseline_value: float
    affected_flows: List[str]
    blast_radius: str


class IncidentDetail(BaseModel):
    """Full incident details including timeline."""
    incident_id: str
    provider_id: str
    provider_name: str
    anomaly_type: str
    severity: str
    status: str
    detected_at: datetime
    acknowledged_at: Optional[datetime] = None
    mitigated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    current_value: float
    baseline_value: float
    threshold_value: float
    affected_flows: List[str]
    blast_radius: str
    estimated_users_affected: int
    resolution_notes: str = ""
    timeline: List[IncidentTimelineEvent]


class IncidentListResponse(BaseModel):
    """List of incidents."""
    timestamp: datetime
    total_incidents: int
    by_severity: Dict[str, int]
    incidents: List[IncidentSummary]


class AcknowledgeIncidentRequest(BaseModel):
    """Request to acknowledge an incident."""
    acknowledged_by: str = Field(..., description="Email or name of person acknowledging")


class IncidentCorrelation(BaseModel):
    """Correlated signals for an incident."""
    incident_id: str
    provider_id: str
    primary_signal: str
    correlated_signals: List[Dict[str, Any]]
    affected_providers: List[str]
    affected_funnels: List[str]


# ---------------------------------------------------------------------------
# Funnel Models
# ---------------------------------------------------------------------------

class FunnelStepHealth(BaseModel):
    """Health metrics for a single funnel step."""
    step_id: str
    step_name: str
    step_order: int
    completion_rate_pct: float
    drop_off_rate_pct: float
    avg_duration_seconds: float
    api_dependencies: List[str]


class FunnelHealthResponse(BaseModel):
    """Health of an onboarding funnel."""
    funnel_name: str
    timestamp: datetime
    overall_completion_rate_pct: float
    overall_drop_off_rate_pct: float
    steps: List[FunnelStepHealth]
    bottleneck_step: Optional[FunnelStepHealth] = None
    api_correlation_analysis: Dict[str, Any]


# ---------------------------------------------------------------------------
# Scorecard Models
# ---------------------------------------------------------------------------

class SLAComplianceRecord(BaseModel):
    """SLA compliance for a period."""
    period_start: datetime
    period_end: datetime
    uptime_pct: float
    sla_target_pct: float
    compliant: bool
    downtime_minutes: float


class ProviderScorecard(BaseModel):
    """Vendor scorecard for contract renewal discussions."""
    provider_id: str
    provider_name: str
    composite_score: float = Field(..., ge=0, le=100)
    grade: str = Field(..., description="excellent, good, concerning, or unacceptable")

    # SLA Compliance
    uptime_pct: float
    sla_target_pct: float
    sla_status: str = Field(..., description="compliant, at_risk, or breached")

    # Reliability
    incident_count_30d: int
    mean_time_to_resolve_minutes: float

    # Cost
    cost_per_call_usd: float
    total_cost_30d: float

    # Latency
    p95_latency_ms: float
    latency_trend: str = Field(..., description="stable, improving, or degrading")

    # Recommendation
    renewal_recommendation: str = Field(
        ..., description="renew, renegotiate, or replace"
    )


class ScorecardListResponse(BaseModel):
    """List of provider scorecards."""
    timestamp: datetime
    providers: List[ProviderScorecard]


# ---------------------------------------------------------------------------
# Webhook Receiver Models
# ---------------------------------------------------------------------------

class WebhookPayload(BaseModel):
    """Generic webhook payload (provider-specific parsing happens in handlers)."""
    provider_id: str
    event_type: str
    event_id: str
    timestamp: Optional[datetime] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class WebhookReceiptResponse(BaseModel):
    """Response to a webhook receipt."""
    status: str = Field(default="received")
    event_id: str
    provider_id: str
    message: str = ""


# ---------------------------------------------------------------------------
# Dashboard Summary Models
# ---------------------------------------------------------------------------

class DashboardSummary(BaseModel):
    """High-level dashboard summary."""
    timestamp: datetime
    integrations_healthy: int
    integrations_degraded: int
    integrations_unhealthy: int
    active_incidents: int
    critical_incidents: int
    webhook_delivery_healthy: int
    webhook_delivery_unhealthy: int
    top_bottleneck_funnels: List[Dict[str, Any]]
    top_unreliable_providers: List[Dict[str, Any]]
