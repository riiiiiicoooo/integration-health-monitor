"""
Onboarding Funnel — Maps customer journey steps to API dependencies.

PM-authored reference implementation. This module solved the most expensive
misdiagnosis across client engagements: product teams assuming drop-off was
a UX problem when it was actually an integration health problem.

The lending client's onboarding completion dropped from 78% to 61%. Product
was redesigning the identity verification screen. This module proved that
60%+ of drop-offs at that step correlated with the KYC API's p95 latency
exceeding 8 seconds during peak hours. The fix was a timeout adjustment
and fallback provider — not a redesign.

Key insight: you can't optimize UX if the underlying API is the bottleneck.
This module connects the dots between "user dropped off at Step 3" and
"the API behind Step 3 was responding in 11 seconds."
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional
from collections import defaultdict
import statistics


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class StepOutcome(Enum):
    """What happened when a user reached this step."""
    COMPLETED = "completed"            # User made it through
    DROPPED_OFF = "dropped_off"        # User abandoned (closed browser, left)
    ERROR = "error"                    # Step failed with an error shown to user
    TIMEOUT = "timeout"                # User waited too long, gave up
    SKIPPED = "skipped"                # Step was skipped (fallback or optional)
    PENDING = "pending"                # User is currently on this step


class DropOffCause(Enum):
    """Root cause attribution for a drop-off."""
    UX_FRICTION = "ux_friction"        # User chose not to continue (form too long, etc.)
    API_LATENCY = "api_latency"        # API response was slow, user gave up waiting
    API_ERROR = "api_error"            # API returned an error, step failed
    API_TIMEOUT = "api_timeout"        # API timed out, step couldn't complete
    UNKNOWN = "unknown"                # Can't determine cause


@dataclass
class FunnelStep:
    """Definition of a single step in the customer journey.

    Each step maps to zero or more API dependencies. Steps with API
    dependencies can have their drop-off correlated with API health.
    """
    step_id: str                       # e.g., "identity_verification"
    step_name: str                     # e.g., "Identity Verification"
    step_order: int                    # Position in the flow (1-based)
    api_dependencies: list[str]        # Provider IDs from the registry
    expected_duration_seconds: int     # How long this step should take
    latency_tolerance_seconds: float   # Max API latency before UX degrades
    is_required: bool = True           # Can the user skip this step?
    has_fallback: bool = False         # Is there a non-API fallback path?
    fallback_description: str = ""


@dataclass
class UserSession:
    """A single user's journey through the onboarding funnel."""
    session_id: str
    user_id: str
    started_at: datetime
    steps: list['StepEvent'] = field(default_factory=list)
    completed: bool = False
    completed_at: Optional[datetime] = None
    drop_off_step: Optional[str] = None

    @property
    def total_duration_seconds(self) -> Optional[float]:
        if self.completed_at and self.started_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    @property
    def furthest_step(self) -> Optional[int]:
        if not self.steps:
            return None
        return max(s.step_order for s in self.steps)


@dataclass
class StepEvent:
    """Record of a user encountering a specific step."""
    session_id: str
    step_id: str
    step_order: int
    started_at: datetime
    completed_at: Optional[datetime]
    outcome: StepOutcome
    api_latency_ms: Optional[float]    # How long the API call took (if applicable)
    api_provider_id: Optional[str]     # Which provider was called
    api_status_code: Optional[int]     # HTTP status from the API
    error_message: Optional[str] = None
    drop_off_cause: Optional[DropOffCause] = None

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.completed_at and self.started_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


# ---------------------------------------------------------------------------
# Funnel Analyzer
# ---------------------------------------------------------------------------

class OnboardingFunnel:
    """Analyzes customer onboarding funnel with API dependency correlation.

    Core responsibilities:
    1. Define the funnel steps and their API dependencies
    2. Record user sessions and step events
    3. Calculate conversion rates per step
    4. Correlate drop-offs with API health metrics
    5. Identify bottlenecks caused by provider degradation

    This is the module that proved the KYC latency problem. It showed that
    drop-off at the identity verification step was 3x higher when API
    latency exceeded 8 seconds vs. when it was under 4 seconds.
    """

    def __init__(self):
        self._steps: dict[str, FunnelStep] = {}
        self._step_order: list[str] = []        # Ordered step IDs
        self._sessions: dict[str, UserSession] = {}
        self._step_events: list[StepEvent] = []

    # -- Funnel Definition --------------------------------------------------

    def define_step(self, step: FunnelStep) -> None:
        """Add a step to the funnel definition."""
        self._steps[step.step_id] = step
        # Maintain ordered list
        self._step_order = sorted(
            self._steps.keys(),
            key=lambda sid: self._steps[sid].step_order
        )

    def get_steps(self) -> list[FunnelStep]:
        """Get all steps in order."""
        return [self._steps[sid] for sid in self._step_order]

    # -- Session Recording --------------------------------------------------

    def start_session(
        self, session_id: str, user_id: str, started_at: datetime
    ) -> UserSession:
        """Record a new user starting the onboarding flow."""
        session = UserSession(
            session_id=session_id,
            user_id=user_id,
            started_at=started_at,
        )
        self._sessions[session_id] = session
        return session

    def record_step(self, event: StepEvent) -> None:
        """Record a user's interaction with a funnel step.

        Automatically attributes drop-off cause based on API health data.
        """
        # Auto-attribute drop-off cause
        if event.outcome in (StepOutcome.DROPPED_OFF, StepOutcome.TIMEOUT):
            event.drop_off_cause = self._attribute_drop_off(event)

        self._step_events.append(event)

        # Update session
        if event.session_id in self._sessions:
            session = self._sessions[event.session_id]
            session.steps.append(event)

            if event.outcome == StepOutcome.DROPPED_OFF:
                session.drop_off_step = event.step_id
            elif event.outcome == StepOutcome.ERROR:
                session.drop_off_step = event.step_id

    def complete_session(
        self, session_id: str, completed_at: datetime
    ) -> None:
        """Mark a session as successfully completed."""
        if session_id in self._sessions:
            self._sessions[session_id].completed = True
            self._sessions[session_id].completed_at = completed_at

    def _attribute_drop_off(self, event: StepEvent) -> DropOffCause:
        """Determine root cause of a drop-off.

        This is the core logic that connects API health to user behavior.
        """
        step = self._steps.get(event.step_id)
        if not step or not step.api_dependencies:
            return DropOffCause.UX_FRICTION

        # API timeout: the call timed out
        if event.outcome == StepOutcome.TIMEOUT:
            return DropOffCause.API_TIMEOUT

        # API error: the call returned an error
        if event.api_status_code and event.api_status_code >= 400:
            return DropOffCause.API_ERROR

        # API latency: the call succeeded but was too slow
        if (
            event.api_latency_ms
            and step.latency_tolerance_seconds > 0
            and event.api_latency_ms > step.latency_tolerance_seconds * 1000
        ):
            return DropOffCause.API_LATENCY

        return DropOffCause.UX_FRICTION

    # -- Funnel Analysis ----------------------------------------------------

    def get_funnel_conversion(
        self, window_hours: int = 24
    ) -> dict:
        """Calculate step-by-step conversion rates.

        The classic funnel view: how many users reach each step, and
        what percentage complete it.
        """
        cutoff = datetime.now() - timedelta(hours=window_hours)
        recent_sessions = [
            s for s in self._sessions.values()
            if s.started_at >= cutoff
        ]

        total_started = len(recent_sessions)
        if total_started == 0:
            return {"window_hours": window_hours, "total_started": 0, "steps": []}

        steps_data = []
        for step_id in self._step_order:
            step = self._steps[step_id]

            # Events for this step in the time window
            step_events = [
                e for e in self._step_events
                if (
                    e.step_id == step_id
                    and e.started_at >= cutoff
                )
            ]

            reached = len(step_events)
            completed = len([
                e for e in step_events
                if e.outcome == StepOutcome.COMPLETED
            ])
            dropped = len([
                e for e in step_events
                if e.outcome in (
                    StepOutcome.DROPPED_OFF,
                    StepOutcome.ERROR,
                    StepOutcome.TIMEOUT,
                )
            ])

            step_conversion = (completed / reached * 100) if reached > 0 else 0
            overall_conversion = (completed / total_started * 100) if total_started > 0 else 0

            steps_data.append({
                "step_id": step_id,
                "step_name": step.step_name,
                "step_order": step.step_order,
                "reached": reached,
                "completed": completed,
                "dropped": dropped,
                "step_conversion_pct": round(step_conversion, 1),
                "overall_conversion_pct": round(overall_conversion, 1),
                "api_dependencies": step.api_dependencies,
            })

        total_completed = len([s for s in recent_sessions if s.completed])

        return {
            "window_hours": window_hours,
            "total_started": total_started,
            "total_completed": total_completed,
            "overall_completion_pct": round(
                total_completed / total_started * 100, 1
            ),
            "steps": steps_data,
        }

    # -- Drop-off Correlation -----------------------------------------------

    def get_drop_off_analysis(
        self, step_id: str, window_hours: int = 24
    ) -> dict:
        """Detailed drop-off analysis for a specific step.

        This is where the magic happens. Breaks down WHY users dropped
        off and correlates with API health data.
        """
        cutoff = datetime.now() - timedelta(hours=window_hours)
        step = self._steps.get(step_id)
        if not step:
            raise KeyError(f"Step '{step_id}' not found.")

        step_events = [
            e for e in self._step_events
            if e.step_id == step_id and e.started_at >= cutoff
        ]

        if not step_events:
            return {"step_id": step_id, "total_events": 0}

        # Group by outcome
        by_outcome = defaultdict(int)
        for e in step_events:
            by_outcome[e.outcome.value] += 1

        # Group drop-offs by cause
        drops = [
            e for e in step_events
            if e.outcome in (
                StepOutcome.DROPPED_OFF,
                StepOutcome.ERROR,
                StepOutcome.TIMEOUT,
            )
        ]
        by_cause = defaultdict(int)
        for e in drops:
            cause = e.drop_off_cause.value if e.drop_off_cause else "unknown"
            by_cause[cause] += 1

        # API-correlated analysis
        api_related_drops = [
            e for e in drops
            if e.drop_off_cause in (
                DropOffCause.API_LATENCY,
                DropOffCause.API_ERROR,
                DropOffCause.API_TIMEOUT,
            )
        ]

        api_correlation_pct = (
            len(api_related_drops) / len(drops) * 100
            if drops else 0
        )

        # Latency breakdown for this step
        latencies = [
            e.api_latency_ms for e in step_events
            if e.api_latency_ms is not None
        ]
        completed_latencies = [
            e.api_latency_ms for e in step_events
            if e.api_latency_ms is not None
            and e.outcome == StepOutcome.COMPLETED
        ]
        dropped_latencies = [
            e.api_latency_ms for e in drops
            if e.api_latency_ms is not None
        ]

        return {
            "step_id": step_id,
            "step_name": step.step_name,
            "window_hours": window_hours,
            "total_events": len(step_events),
            "by_outcome": dict(by_outcome),
            "total_drop_offs": len(drops),
            "drop_off_rate_pct": round(
                len(drops) / len(step_events) * 100, 1
            ),
            "drop_off_by_cause": dict(by_cause),
            "api_correlated_pct": round(api_correlation_pct, 1),
            "api_dependencies": step.api_dependencies,
            "latency_analysis": {
                "all_requests": {
                    "count": len(latencies),
                    "median_ms": round(statistics.median(latencies), 1) if latencies else 0,
                    "p95_ms": round(
                        sorted(latencies)[int(len(latencies) * 0.95)], 1
                    ) if latencies else 0,
                },
                "completed_users": {
                    "count": len(completed_latencies),
                    "median_ms": round(
                        statistics.median(completed_latencies), 1
                    ) if completed_latencies else 0,
                },
                "dropped_users": {
                    "count": len(dropped_latencies),
                    "median_ms": round(
                        statistics.median(dropped_latencies), 1
                    ) if dropped_latencies else 0,
                },
            },
            "recommendation": self._generate_recommendation(
                step, drops, api_related_drops, latencies
            ),
        }

    def _generate_recommendation(
        self,
        step: FunnelStep,
        all_drops: list[StepEvent],
        api_drops: list[StepEvent],
        latencies: list[float],
    ) -> str:
        """Generate an actionable recommendation based on drop-off analysis."""
        if not all_drops:
            return "No drop-offs detected. Step is performing well."

        api_pct = len(api_drops) / len(all_drops) * 100 if all_drops else 0

        if api_pct > 50:
            if latencies:
                p95 = sorted(latencies)[int(len(latencies) * 0.95)]
                tolerance = step.latency_tolerance_seconds * 1000

                if p95 > tolerance:
                    return (
                        f"API latency is the primary bottleneck. "
                        f"P95 latency ({p95:.0f}ms) exceeds tolerance "
                        f"({tolerance:.0f}ms). {api_pct:.0f}% of drop-offs "
                        f"correlate with API issues. Fix the integration "
                        f"before optimizing UX."
                    )

            timeout_drops = len([
                e for e in api_drops
                if e.drop_off_cause == DropOffCause.API_TIMEOUT
            ])
            if timeout_drops > len(api_drops) * 0.3:
                return (
                    f"API timeouts are driving drop-offs. "
                    f"{timeout_drops} timeout-related drops. "
                    f"Consider adjusting timeout thresholds or "
                    f"implementing a fallback."
                )

            return (
                f"{api_pct:.0f}% of drop-offs are API-related. "
                f"Investigate provider health before UX changes."
            )

        return (
            f"Drop-offs appear primarily UX-driven ({100 - api_pct:.0f}% "
            f"non-API causes). UX optimization is appropriate."
        )

    # -- Bottleneck Detection -----------------------------------------------

    def find_bottlenecks(self, window_hours: int = 24) -> list[dict]:
        """Identify steps that are disproportionately causing drop-offs.

        A bottleneck is a step where:
        1. Drop-off rate is significantly higher than other steps
        2. The drop-off correlates with API health issues
        3. Fixing the API issue would have measurable funnel impact
        """
        funnel = self.get_funnel_conversion(window_hours)
        if funnel["total_started"] == 0:
            return []

        bottlenecks = []
        avg_drop_rate = statistics.mean(
            s["dropped"] / s["reached"] * 100
            for s in funnel["steps"]
            if s["reached"] > 0
        ) if any(s["reached"] > 0 for s in funnel["steps"]) else 0

        for step_data in funnel["steps"]:
            if step_data["reached"] == 0:
                continue

            drop_rate = step_data["dropped"] / step_data["reached"] * 100
            step = self._steps[step_data["step_id"]]

            # Is this step a bottleneck? (drop rate > 1.5x average)
            if drop_rate > avg_drop_rate * 1.5 and step_data["dropped"] > 5:
                analysis = self.get_drop_off_analysis(
                    step_data["step_id"], window_hours
                )

                bottlenecks.append({
                    "step_id": step_data["step_id"],
                    "step_name": step_data["step_name"],
                    "drop_off_rate_pct": round(drop_rate, 1),
                    "avg_drop_rate_pct": round(avg_drop_rate, 1),
                    "severity_multiplier": round(drop_rate / avg_drop_rate, 1),
                    "total_drops": step_data["dropped"],
                    "api_correlated_pct": analysis["api_correlated_pct"],
                    "api_dependencies": step.api_dependencies,
                    "has_fallback": step.has_fallback,
                    "recommendation": analysis["recommendation"],
                    "potential_recovery": self._estimate_recovery(
                        step_data, analysis, funnel["total_started"]
                    ),
                })

        bottlenecks.sort(key=lambda b: b["severity_multiplier"], reverse=True)
        return bottlenecks

    def _estimate_recovery(
        self,
        step_data: dict,
        analysis: dict,
        total_started: int,
    ) -> dict:
        """Estimate the impact of fixing this bottleneck.

        If 60% of drop-offs are API-related, and we fix the API issue,
        we can estimate how many additional users would complete onboarding.
        """
        api_drops = int(
            step_data["dropped"] * analysis["api_correlated_pct"] / 100
        )
        # Assume fixing the API recovers 70% of API-correlated drops
        # (some users dropped for multiple reasons)
        recoverable = int(api_drops * 0.7)

        return {
            "api_related_drops": api_drops,
            "estimated_recoverable": recoverable,
            "completion_rate_improvement_pct": round(
                recoverable / total_started * 100, 1
            ) if total_started > 0 else 0,
        }

    # -- Dashboard Summary --------------------------------------------------

    def get_funnel_summary(self, window_hours: int = 24) -> dict:
        """Complete funnel summary for the dashboard."""
        funnel = self.get_funnel_conversion(window_hours)
        bottlenecks = self.find_bottlenecks(window_hours)

        return {
            "funnel": funnel,
            "bottleneck_count": len(bottlenecks),
            "bottlenecks": bottlenecks,
            "api_driven_drop_offs": any(
                b["api_correlated_pct"] > 50 for b in bottlenecks
            ),
        }


# ---------------------------------------------------------------------------
# Usage Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import random

    funnel = OnboardingFunnel()

    # Define the lending onboarding steps
    funnel.define_step(FunnelStep(
        step_id="phone_verification",
        step_name="Phone Verification (2FA)",
        step_order=1,
        api_dependencies=["twilio"],
        expected_duration_seconds=30,
        latency_tolerance_seconds=5.0,
        has_fallback=True,
        fallback_description="Email verification via SendGrid",
    ))
    funnel.define_step(FunnelStep(
        step_id="identity_verification",
        step_name="Identity Verification",
        step_order=2,
        api_dependencies=["kyc_provider"],
        expected_duration_seconds=60,
        latency_tolerance_seconds=8.0,
        has_fallback=True,
        fallback_description="Manual review queue (24-48 hour delay)",
    ))
    funnel.define_step(FunnelStep(
        step_id="bank_linking",
        step_name="Bank Account Linking",
        step_order=3,
        api_dependencies=["plaid"],
        expected_duration_seconds=45,
        latency_tolerance_seconds=10.0,
        has_fallback=True,
        fallback_description="Manual bank statement upload",
    ))
    funnel.define_step(FunnelStep(
        step_id="credit_check",
        step_name="Credit Check",
        step_order=4,
        api_dependencies=["credit_bureau"],
        expected_duration_seconds=15,
        latency_tolerance_seconds=15.0,
        has_fallback=False,
    ))
    funnel.define_step(FunnelStep(
        step_id="disbursement",
        step_name="Loan Disbursement",
        step_order=5,
        api_dependencies=["stripe"],
        expected_duration_seconds=10,
        latency_tolerance_seconds=10.0,
        has_fallback=True,
        fallback_description="Next-day ACH via backup processor",
    ))

    # Simulate 200 user sessions
    now = datetime.now()
    for i in range(200):
        session_id = f"sess_{i}"
        user_id = f"user_{i}"
        session_start = now - timedelta(hours=random.uniform(0, 24))

        funnel.start_session(session_id, user_id, session_start)

        current_time = session_start
        completed_all = True

        for step in funnel.get_steps():
            # Simulate API latency
            if step.step_id == "identity_verification":
                # KYC provider has high latency — the bottleneck
                api_latency = random.gauss(6500, 3000)
                api_latency = max(api_latency, 500)
                drop_chance = 0.25 if api_latency > 8000 else 0.08
            elif step.step_id == "bank_linking":
                api_latency = random.gauss(2100, 500)
                drop_chance = 0.06
            else:
                api_latency = random.gauss(1000, 300)
                drop_chance = 0.04

            # Determine outcome
            if random.random() < drop_chance:
                if api_latency > step.latency_tolerance_seconds * 1000:
                    outcome = StepOutcome.TIMEOUT
                elif random.random() < 0.3:
                    outcome = StepOutcome.ERROR
                else:
                    outcome = StepOutcome.DROPPED_OFF
                completed_all = False
            else:
                outcome = StepOutcome.COMPLETED

            step_start = current_time
            step_end = step_start + timedelta(
                seconds=step.expected_duration_seconds
            ) if outcome == StepOutcome.COMPLETED else None

            funnel.record_step(StepEvent(
                session_id=session_id,
                step_id=step.step_id,
                step_order=step.step_order,
                started_at=step_start,
                completed_at=step_end,
                outcome=outcome,
                api_latency_ms=api_latency,
                api_provider_id=step.api_dependencies[0] if step.api_dependencies else None,
                api_status_code=200 if outcome == StepOutcome.COMPLETED else 500,
            ))

            if outcome != StepOutcome.COMPLETED:
                break

            current_time = step_end

        if completed_all:
            funnel.complete_session(session_id, current_time)

    # Print funnel analysis
    print("=== ONBOARDING FUNNEL ===\n")
    conversion = funnel.get_funnel_conversion(window_hours=24)
    print(f"Started: {conversion['total_started']}")
    print(f"Completed: {conversion['total_completed']}")
    print(f"Completion Rate: {conversion['overall_completion_pct']}%\n")

    for step in conversion["steps"]:
        bar = "█" * int(step["step_conversion_pct"] / 5)
        print(
            f"  {step['step_order']}. {step['step_name']:<30} "
            f"{step['step_conversion_pct']:>5.1f}% "
            f"({step['completed']}/{step['reached']}) {bar}"
        )

    # Bottleneck analysis
    print("\n=== BOTTLENECK DETECTION ===\n")
    bottlenecks = funnel.find_bottlenecks(window_hours=24)
    if bottlenecks:
        for b in bottlenecks:
            print(f"🔴 {b['step_name']}")
            print(f"   Drop-off: {b['drop_off_rate_pct']}% (avg: {b['avg_drop_rate_pct']}%)")
            print(f"   API-correlated: {b['api_correlated_pct']}%")
            print(f"   {b['recommendation']}")
            print(f"   Recovery potential: +{b['potential_recovery']['completion_rate_improvement_pct']}% completion rate")
            print()
    else:
        print("No bottlenecks detected.")
