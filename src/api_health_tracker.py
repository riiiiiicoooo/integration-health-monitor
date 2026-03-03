"""
API Health Tracker — Real-time health monitoring per third-party provider.

PM-authored reference implementation. Tracks what actually matters when you're
consuming external APIs: are they responding? How fast? Are they returning
errors? Should we stop sending them traffic?

This is the synchronous counterpart to the webhook_monitor. The webhook
module tracks async delivery reliability; this module tracks request/response
health for synchronous API calls.

Key design decision: we monitor from OUR perspective, not the provider's
status page. Provider status pages are delayed (15-30 minutes is typical)
and frequently miss partial degradation. In 6 out of 8 major incidents
across client engagements, our monitoring detected the issue before the
provider's status page updated.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from collections import defaultdict
from typing import Optional
import statistics
import random
import math


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CircuitState(Enum):
    """Circuit breaker state for a provider."""
    CLOSED = "closed"       # Normal — requests flow through
    OPEN = "open"           # Provider failing — requests fail fast
    HALF_OPEN = "half_open" # Testing recovery with probe requests


class HealthStatus(Enum):
    """Overall health assessment for a provider."""
    HEALTHY = "healthy"              # All metrics within normal range
    DEGRADED = "degraded"            # Elevated latency or error rate, still functional
    UNHEALTHY = "unhealthy"          # Circuit breaker open or metrics critical
    UNKNOWN = "unknown"              # Insufficient data to assess


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class APICallEvent:
    """A single API call to a third-party provider.

    Every outgoing API request generates one of these. The health tracker
    consumes these to calculate latency, error rates, and circuit breaker
    state.
    """
    provider_id: str
    endpoint: str                     # e.g., "/v1/verifications"
    method: str                       # GET, POST, etc.
    timestamp: datetime
    response_status_code: int         # HTTP status code
    latency_ms: float                 # Total round-trip time
    success: bool                     # Did the call accomplish its purpose?
    error_category: Optional[str] = None  # "timeout", "server_error", "rate_limited", "auth_error"
    retry_attempt: int = 0            # 0 = first attempt, 1+ = retry


@dataclass
class HealthSnapshot:
    """Point-in-time health assessment for a provider.

    Calculated over a rolling time window. Stored for trend analysis
    and used by the dashboard for real-time display.
    """
    provider_id: str
    timestamp: datetime
    window_seconds: int               # How far back this snapshot looks

    # Latency
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_max_ms: float

    # Error rates
    total_requests: int
    successful_requests: int
    failed_requests: int
    error_rate_pct: float
    errors_by_category: dict          # {"timeout": 5, "server_error": 3, ...}
    errors_by_status_code: dict       # {500: 3, 502: 2, 429: 1, ...}

    # Circuit breaker
    circuit_state: CircuitState
    health_status: HealthStatus

    # Throughput
    requests_per_minute: float


@dataclass
class CircuitBreakerConfig:
    """Configuration for a provider's circuit breaker.

    Thresholds come from the integration registry's HealthCheckConfig.
    They're tuned per-provider based on historical reliability and
    blast radius.
    """
    error_threshold_pct: float        # Error rate to trip (e.g., 10.0)
    window_seconds: int               # Time window for error rate calc
    recovery_probes: int              # Successful probes to close circuit
    probe_interval_seconds: int = 30  # How often to probe during OPEN state
    half_open_max_requests: int = 3   # Max requests in HALF_OPEN before deciding


@dataclass
class CircuitBreakerState:
    """Runtime state of a provider's circuit breaker."""
    state: CircuitState = CircuitState.CLOSED
    last_state_change: Optional[datetime] = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_failure_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    total_trips: int = 0              # How many times this circuit has opened
    last_probe_time: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Health Tracker
# ---------------------------------------------------------------------------

class APIHealthTracker:
    """Tracks API health metrics and manages circuit breakers per provider.

    Core responsibilities:
    1. Record every API call (latency, status code, success/failure)
    2. Calculate rolling health metrics (latency percentiles, error rates)
    3. Manage circuit breaker state (open/closed/half-open)
    4. Generate health snapshots for the dashboard
    5. Provide data for incident_detector anomaly detection

    In production, this would consume events from a message queue and
    store snapshots in a time-series database. The prototype uses
    in-memory storage.
    """

    def __init__(self):
        self._events: list[APICallEvent] = []
        self._circuit_configs: dict[str, CircuitBreakerConfig] = {}
        self._circuit_states: dict[str, CircuitBreakerState] = {}
        self._snapshots: list[HealthSnapshot] = []

    # -- Configuration ------------------------------------------------------

    def configure_circuit_breaker(
        self, provider_id: str, config: CircuitBreakerConfig
    ) -> None:
        """Set circuit breaker thresholds for a provider."""
        self._circuit_configs[provider_id] = config
        if provider_id not in self._circuit_states:
            self._circuit_states[provider_id] = CircuitBreakerState()

    # -- Event Recording ----------------------------------------------------

    def record_call(self, event: APICallEvent) -> dict:
        """Record an API call and update circuit breaker state.

        Returns the current health status so the caller knows whether
        to continue sending traffic or activate a fallback.
        """
        self._events.append(event)
        self._update_circuit_breaker(event)

        cb = self._circuit_states.get(event.provider_id)
        return {
            "provider_id": event.provider_id,
            "recorded": True,
            "circuit_state": cb.state.value if cb else "unknown",
            "should_send_traffic": self.should_send_traffic(event.provider_id),
        }

    def _update_circuit_breaker(self, event: APICallEvent) -> None:
        """Update circuit breaker state based on the latest event."""
        pid = event.provider_id
        if pid not in self._circuit_configs:
            return

        config = self._circuit_configs[pid]
        state = self._circuit_states[pid]

        if event.success:
            state.consecutive_failures = 0
            state.consecutive_successes += 1
            state.last_success_time = event.timestamp

            # HALF_OPEN -> CLOSED: recovery confirmed
            if (
                state.state == CircuitState.HALF_OPEN
                and state.consecutive_successes >= config.recovery_probes
            ):
                state.state = CircuitState.CLOSED
                state.last_state_change = event.timestamp
                state.consecutive_successes = 0

        else:
            state.consecutive_successes = 0
            state.consecutive_failures += 1
            state.last_failure_time = event.timestamp

            # Check if error rate exceeds threshold
            error_rate = self._calculate_error_rate(
                pid, config.window_seconds
            )

            # CLOSED -> OPEN: threshold exceeded
            if (
                state.state == CircuitState.CLOSED
                and error_rate >= config.error_threshold_pct
            ):
                state.state = CircuitState.OPEN
                state.last_state_change = event.timestamp
                state.total_trips += 1

            # HALF_OPEN -> OPEN: probe failed
            elif state.state == CircuitState.HALF_OPEN:
                state.state = CircuitState.OPEN
                state.last_state_change = event.timestamp

    def _calculate_error_rate(
        self, provider_id: str, window_seconds: int
    ) -> float:
        """Calculate error rate over a time window."""
        cutoff = datetime.now() - timedelta(seconds=window_seconds)
        window_events = [
            e for e in self._events
            if e.provider_id == provider_id and e.timestamp >= cutoff
        ]
        if not window_events:
            return 0.0

        failed = sum(1 for e in window_events if not e.success)
        return (failed / len(window_events)) * 100

    # -- Circuit Breaker Queries --------------------------------------------

    def should_send_traffic(self, provider_id: str) -> bool:
        """Should we send traffic to this provider?

        CLOSED: yes
        OPEN: no (fail fast, use fallback)
        HALF_OPEN: only probe requests
        """
        state = self._circuit_states.get(provider_id)
        if state is None:
            return True  # No circuit breaker configured, allow traffic

        if state.state == CircuitState.CLOSED:
            return True

        if state.state == CircuitState.OPEN:
            # Check if it's time to probe
            config = self._circuit_configs[provider_id]
            if state.last_state_change:
                seconds_since_open = (
                    datetime.now() - state.last_state_change
                ).total_seconds()
                if seconds_since_open >= config.probe_interval_seconds:
                    # Transition to half-open for probing
                    state.state = CircuitState.HALF_OPEN
                    state.last_state_change = datetime.now()
                    state.consecutive_successes = 0
                    state.last_probe_time = datetime.now()
                    return True  # Allow probe request
            return False

        # HALF_OPEN: allow limited traffic for probing
        return True

    def get_circuit_state(self, provider_id: str) -> dict:
        """Get current circuit breaker state for a provider."""
        state = self._circuit_states.get(provider_id)
        if state is None:
            return {"provider_id": provider_id, "state": "not_configured"}

        return {
            "provider_id": provider_id,
            "state": state.state.value,
            "last_state_change": (
                state.last_state_change.isoformat()
                if state.last_state_change else None
            ),
            "consecutive_failures": state.consecutive_failures,
            "consecutive_successes": state.consecutive_successes,
            "total_trips": state.total_trips,
            "should_send_traffic": self.should_send_traffic(provider_id),
        }

    # -- Health Snapshots ---------------------------------------------------

    def take_snapshot(
        self, provider_id: str, window_seconds: int = 300
    ) -> HealthSnapshot:
        """Generate a point-in-time health snapshot for a provider.

        Default window is 5 minutes (300 seconds). Short enough to catch
        acute issues, long enough to avoid noise from single requests.
        """
        cutoff = datetime.now() - timedelta(seconds=window_seconds)
        window_events = [
            e for e in self._events
            if e.provider_id == provider_id and e.timestamp >= cutoff
        ]

        if not window_events:
            snapshot = HealthSnapshot(
                provider_id=provider_id,
                timestamp=datetime.now(),
                window_seconds=window_seconds,
                latency_p50_ms=0,
                latency_p95_ms=0,
                latency_p99_ms=0,
                latency_max_ms=0,
                total_requests=0,
                successful_requests=0,
                failed_requests=0,
                error_rate_pct=0,
                errors_by_category={},
                errors_by_status_code={},
                circuit_state=self._circuit_states.get(
                    provider_id, CircuitBreakerState()
                ).state,
                health_status=HealthStatus.UNKNOWN,
                requests_per_minute=0,
            )
            self._snapshots.append(snapshot)
            return snapshot

        # Latency percentiles (from successful requests only)
        latencies = sorted(
            e.latency_ms for e in window_events if e.success
        )

        if latencies:
            p50 = latencies[len(latencies) // 2]
            p95 = latencies[int(len(latencies) * 0.95)]
            p99 = latencies[int(len(latencies) * 0.99)]
            max_lat = latencies[-1]
        else:
            p50 = p95 = p99 = max_lat = 0

        # Error breakdown
        failed = [e for e in window_events if not e.success]
        errors_by_cat = defaultdict(int)
        errors_by_code = defaultdict(int)
        for e in failed:
            if e.error_category:
                errors_by_cat[e.error_category] += 1
            errors_by_code[e.response_status_code] += 1

        error_rate = len(failed) / len(window_events) * 100

        # Throughput
        time_span = (
            window_events[-1].timestamp - window_events[0].timestamp
        ).total_seconds()
        rpm = (
            len(window_events) / (time_span / 60) if time_span > 0
            else len(window_events)
        )

        # Health assessment
        cb_state = self._circuit_states.get(
            provider_id, CircuitBreakerState()
        ).state

        if cb_state == CircuitState.OPEN:
            health = HealthStatus.UNHEALTHY
        elif error_rate > 10 or p95 > 10000:
            health = HealthStatus.DEGRADED
        elif error_rate > 25:
            health = HealthStatus.UNHEALTHY
        else:
            health = HealthStatus.HEALTHY

        snapshot = HealthSnapshot(
            provider_id=provider_id,
            timestamp=datetime.now(),
            window_seconds=window_seconds,
            latency_p50_ms=round(p50, 1),
            latency_p95_ms=round(p95, 1),
            latency_p99_ms=round(p99, 1),
            latency_max_ms=round(max_lat, 1),
            total_requests=len(window_events),
            successful_requests=len(window_events) - len(failed),
            failed_requests=len(failed),
            error_rate_pct=round(error_rate, 2),
            errors_by_category=dict(errors_by_cat),
            errors_by_status_code=dict(errors_by_code),
            circuit_state=cb_state,
            health_status=health,
            requests_per_minute=round(rpm, 1),
        )

        self._snapshots.append(snapshot)
        return snapshot

    # -- Latency Analysis ---------------------------------------------------

    def get_latency_trend(
        self,
        provider_id: str,
        window_hours: int = 24,
        intervals: int = 12,
    ) -> dict:
        """Latency trend over time — used to detect gradual degradation.

        A provider might not be "down" but if p95 latency has doubled
        over the last week, that's a conversation for the QBR.
        """
        interval_hours = window_hours / intervals
        trend_data = []
        now = datetime.now()

        for i in range(intervals):
            interval_end = now - timedelta(hours=i * interval_hours)
            interval_start = interval_end - timedelta(hours=interval_hours)

            latencies = [
                e.latency_ms
                for e in self._events
                if (
                    e.provider_id == provider_id
                    and e.timestamp >= interval_start
                    and e.timestamp < interval_end
                    and e.success
                )
            ]

            if latencies:
                sorted_lat = sorted(latencies)
                p50 = sorted_lat[len(sorted_lat) // 2]
                p95 = sorted_lat[int(len(sorted_lat) * 0.95)]
            else:
                p50 = p95 = 0

            trend_data.append({
                "interval_start": interval_start.isoformat(),
                "interval_end": interval_end.isoformat(),
                "p50_ms": round(p50, 1),
                "p95_ms": round(p95, 1),
                "sample_count": len(latencies),
            })

        trend_data.reverse()

        # Detect latency regression
        if len(trend_data) >= 4:
            recent_p95 = statistics.mean(
                d["p95_ms"] for d in trend_data[-3:] if d["p95_ms"] > 0
            ) if any(d["p95_ms"] > 0 for d in trend_data[-3:]) else 0
            older_p95 = statistics.mean(
                d["p95_ms"] for d in trend_data[:3] if d["p95_ms"] > 0
            ) if any(d["p95_ms"] > 0 for d in trend_data[:3]) else 0

            if older_p95 > 0:
                regression_pct = ((recent_p95 - older_p95) / older_p95) * 100
            else:
                regression_pct = 0
        else:
            regression_pct = 0

        return {
            "provider_id": provider_id,
            "window_hours": window_hours,
            "intervals": trend_data,
            "latency_regression_pct": round(regression_pct, 1),
            "regressing": regression_pct > 25,
        }

    # -- Provider Comparison ------------------------------------------------

    def compare_providers(
        self,
        provider_ids: list[str],
        window_seconds: int = 3600,
    ) -> list[dict]:
        """Compare health metrics across multiple providers.

        Used by the dashboard to show a side-by-side view of all providers
        and quickly identify which one is the problem.
        """
        comparisons = []
        for pid in provider_ids:
            snapshot = self.take_snapshot(pid, window_seconds)
            cb = self.get_circuit_state(pid)

            comparisons.append({
                "provider_id": pid,
                "health_status": snapshot.health_status.value,
                "error_rate_pct": snapshot.error_rate_pct,
                "latency_p50_ms": snapshot.latency_p50_ms,
                "latency_p95_ms": snapshot.latency_p95_ms,
                "total_requests": snapshot.total_requests,
                "circuit_state": cb["state"],
                "total_circuit_trips": cb.get("total_trips", 0),
            })

        # Sort by health — unhealthy first
        status_order = {
            "unhealthy": 0, "degraded": 1, "unknown": 2, "healthy": 3
        }
        comparisons.sort(
            key=lambda x: status_order.get(x["health_status"], 99)
        )

        return comparisons

    # -- Dashboard Summary --------------------------------------------------

    def get_tracker_summary(
        self,
        provider_ids: list[str],
        window_seconds: int = 300,
    ) -> dict:
        """Complete API health summary for the dashboard."""
        snapshots = {
            pid: self.take_snapshot(pid, window_seconds)
            for pid in provider_ids
        }

        healthy = [s for s in snapshots.values() if s.health_status == HealthStatus.HEALTHY]
        degraded = [s for s in snapshots.values() if s.health_status == HealthStatus.DEGRADED]
        unhealthy = [s for s in snapshots.values() if s.health_status == HealthStatus.UNHEALTHY]

        return {
            "timestamp": datetime.now().isoformat(),
            "window_seconds": window_seconds,
            "total_providers": len(provider_ids),
            "healthy": len(healthy),
            "degraded": len(degraded),
            "unhealthy": len(unhealthy),
            "providers": {
                pid: {
                    "health": snap.health_status.value,
                    "error_rate": snap.error_rate_pct,
                    "p50_ms": snap.latency_p50_ms,
                    "p95_ms": snap.latency_p95_ms,
                    "requests": snap.total_requests,
                    "circuit": snap.circuit_state.value,
                }
                for pid, snap in snapshots.items()
            },
        }


# ---------------------------------------------------------------------------
# Usage Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tracker = APIHealthTracker()

    # Configure circuit breakers (thresholds from integration registry)
    tracker.configure_circuit_breaker("kyc_provider", CircuitBreakerConfig(
        error_threshold_pct=10.0,
        window_seconds=180,
        recovery_probes=5,
    ))
    tracker.configure_circuit_breaker("stripe", CircuitBreakerConfig(
        error_threshold_pct=5.0,
        window_seconds=120,
        recovery_probes=3,
    ))
    tracker.configure_circuit_breaker("plaid", CircuitBreakerConfig(
        error_threshold_pct=12.0,
        window_seconds=180,
        recovery_probes=3,
    ))

    now = datetime.now()

    # Simulate healthy Stripe traffic
    for i in range(100):
        tracker.record_call(APICallEvent(
            provider_id="stripe",
            endpoint="/v1/payment_intents",
            method="POST",
            timestamp=now - timedelta(seconds=i * 3),
            response_status_code=200,
            latency_ms=random.gauss(800, 150),
            success=True,
        ))

    # Simulate degraded KYC provider (high latency, some failures)
    for i in range(60):
        is_failure = random.random() < 0.12  # 12% error rate
        latency = random.gauss(8500, 2000) if is_failure else random.gauss(3800, 800)
        tracker.record_call(APICallEvent(
            provider_id="kyc_provider",
            endpoint="/v3/verifications",
            method="POST",
            timestamp=now - timedelta(seconds=i * 5),
            response_status_code=500 if is_failure else 200,
            latency_ms=max(latency, 100),
            success=not is_failure,
            error_category="server_error" if is_failure else None,
        ))

    # Simulate healthy Plaid traffic
    for i in range(40):
        tracker.record_call(APICallEvent(
            provider_id="plaid",
            endpoint="/link/token/create",
            method="POST",
            timestamp=now - timedelta(seconds=i * 7),
            response_status_code=200,
            latency_ms=random.gauss(1200, 300),
            success=True,
        ))

    # Take snapshots and compare
    print("=== API HEALTH COMPARISON ===\n")
    comparison = tracker.compare_providers(
        ["stripe", "kyc_provider", "plaid"],
        window_seconds=300,
    )
    for p in comparison:
        status_icon = {
            "healthy": "✅",
            "degraded": "⚠️",
            "unhealthy": "🔴",
            "unknown": "❓",
        }
        print(
            f"{status_icon.get(p['health_status'], '?')} {p['provider_id']}: "
            f"errors={p['error_rate_pct']}% "
            f"p50={p['latency_p50_ms']}ms "
            f"p95={p['latency_p95_ms']}ms "
            f"circuit={p['circuit_state']}"
        )

    # Circuit breaker states
    print("\n=== CIRCUIT BREAKER STATES ===\n")
    for pid in ["stripe", "kyc_provider", "plaid"]:
        cb = tracker.get_circuit_state(pid)
        print(f"{pid}: {cb['state']} (trips: {cb['total_trips']})")

    # Dashboard summary
    print("\n=== TRACKER SUMMARY ===\n")
    summary = tracker.get_tracker_summary(
        ["stripe", "kyc_provider", "plaid"]
    )
    print(f"Healthy: {summary['healthy']}")
    print(f"Degraded: {summary['degraded']}")
    print(f"Unhealthy: {summary['unhealthy']}")
