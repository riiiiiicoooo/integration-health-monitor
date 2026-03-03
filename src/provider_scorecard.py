"""
Provider Scorecard — SLA compliance tracking and vendor evaluation.

PM-authored reference implementation. Built because "we feel like reliability
has been an issue" doesn't work in vendor QBRs. This module generates the
hard data that changes vendor conversations from subjective complaints to
data-driven negotiations.

The lending client used this to prove their card issuing provider had 99.71%
uptime against a 99.95% SLA guarantee, with 4 incidents in 90 days. That
data gave them leverage to negotiate credits, a dedicated support escalation
path, and justified the cost of integrating a secondary provider.

Key insight: providers measure uptime from their side. We measure from ours.
The numbers are always different, and ours are always worse. Having our own
data is the single most effective tool for vendor management.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional
from collections import defaultdict
import statistics


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ScoreGrade(Enum):
    """Overall provider grade based on composite score."""
    EXCELLENT = "excellent"        # 90-100
    GOOD = "good"                  # 75-89
    CONCERNING = "concerning"      # 60-74
    UNACCEPTABLE = "unacceptable"  # Below 60


class SLAStatus(Enum):
    """Whether a provider is meeting their SLA."""
    COMPLIANT = "compliant"
    AT_RISK = "at_risk"            # Within 0.1% of SLA boundary
    BREACHED = "breached"


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class UptimeRecord:
    """A record of provider availability for a time period."""
    provider_id: str
    period_start: datetime
    period_end: datetime
    total_minutes: float
    available_minutes: float
    downtime_minutes: float
    incident_count: int
    uptime_pct: float

    @property
    def downtime_hours(self) -> float:
        return self.downtime_minutes / 60


@dataclass
class IncidentRecord:
    """Simplified incident record for scorecard calculations."""
    provider_id: str
    incident_id: str
    occurred_at: datetime
    duration_minutes: float
    severity: str
    root_cause: str
    appeared_on_status_page: bool      # Did the provider acknowledge it?
    mttr_minutes: float                # Provider's time to resolve


@dataclass
class LatencyRecord:
    """Latency snapshot for a time period."""
    provider_id: str
    period_start: datetime
    period_end: datetime
    p50_ms: float
    p95_ms: float
    p99_ms: float
    sample_count: int


@dataclass
class WebhookReliabilityRecord:
    """Webhook delivery reliability for a time period."""
    provider_id: str
    period_start: datetime
    period_end: datetime
    expected_deliveries: int
    actual_deliveries: int
    delivery_rate_pct: float
    avg_delivery_latency_ms: float


@dataclass
class CostRecord:
    """Cost data for a billing period."""
    provider_id: str
    period_start: datetime
    period_end: datetime
    total_cost: float
    total_api_calls: int
    successful_calls: int
    failed_calls: int

    @property
    def cost_per_call(self) -> float:
        return self.total_cost / self.total_api_calls if self.total_api_calls > 0 else 0

    @property
    def cost_per_successful_call(self) -> float:
        return self.total_cost / self.successful_calls if self.successful_calls > 0 else 0

    @property
    def waste_pct(self) -> float:
        """Percentage of spend on failed calls."""
        return self.failed_calls / self.total_api_calls * 100 if self.total_api_calls > 0 else 0


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------

@dataclass
class ProviderSLAConfig:
    """SLA terms from the provider's contract."""
    guaranteed_uptime_pct: float
    max_latency_ms: int
    webhook_delivery_pct: float       # 0 if no webhook SLA
    support_response_minutes: int
    credit_eligible: bool


class ProviderScorecard:
    """Generates provider health scores and QBR data packets.

    Core responsibilities:
    1. Track actual uptime vs. SLA guarantee
    2. Calculate composite provider health scores (0-100)
    3. Generate QBR data packets with all relevant metrics
    4. Compare providers side-by-side
    5. Identify when to trigger migration evaluation

    Scoring methodology is documented in PROVIDER_EVALUATION.md Section 5.2.
    """

    def __init__(self):
        self._sla_configs: dict[str, ProviderSLAConfig] = {}
        self._uptime_records: list[UptimeRecord] = []
        self._incidents: list[IncidentRecord] = []
        self._latency_records: list[LatencyRecord] = []
        self._webhook_records: list[WebhookReliabilityRecord] = []
        self._cost_records: list[CostRecord] = []

    # -- Configuration ------------------------------------------------------

    def configure_sla(
        self, provider_id: str, sla: ProviderSLAConfig
    ) -> None:
        self._sla_configs[provider_id] = sla

    # -- Data Ingestion -----------------------------------------------------

    def add_uptime_record(self, record: UptimeRecord) -> None:
        self._uptime_records.append(record)

    def add_incident(self, record: IncidentRecord) -> None:
        self._incidents.append(record)

    def add_latency_record(self, record: LatencyRecord) -> None:
        self._latency_records.append(record)

    def add_webhook_record(self, record: WebhookReliabilityRecord) -> None:
        self._webhook_records.append(record)

    def add_cost_record(self, record: CostRecord) -> None:
        self._cost_records.append(record)

    # -- SLA Compliance -----------------------------------------------------

    def get_sla_compliance(
        self, provider_id: str, days: int = 90
    ) -> dict:
        """Calculate SLA compliance for a provider over a period."""
        sla = self._sla_configs.get(provider_id)
        if not sla:
            return {"provider_id": provider_id, "error": "No SLA configured"}

        cutoff = datetime.now() - timedelta(days=days)

        # Uptime
        uptime_records = [
            r for r in self._uptime_records
            if r.provider_id == provider_id and r.period_start >= cutoff
        ]

        if uptime_records:
            total_minutes = sum(r.total_minutes for r in uptime_records)
            available_minutes = sum(r.available_minutes for r in uptime_records)
            actual_uptime = (
                available_minutes / total_minutes * 100
                if total_minutes > 0 else 100
            )
            total_downtime = sum(r.downtime_minutes for r in uptime_records)
        else:
            actual_uptime = 100.0
            total_downtime = 0.0

        uptime_gap = actual_uptime - sla.guaranteed_uptime_pct

        if actual_uptime >= sla.guaranteed_uptime_pct:
            uptime_status = SLAStatus.COMPLIANT
        elif actual_uptime >= sla.guaranteed_uptime_pct - 0.1:
            uptime_status = SLAStatus.AT_RISK
        else:
            uptime_status = SLAStatus.BREACHED

        # Incidents
        incidents = [
            r for r in self._incidents
            if r.provider_id == provider_id and r.occurred_at >= cutoff
        ]

        # Webhook delivery
        webhook_records = [
            r for r in self._webhook_records
            if r.provider_id == provider_id and r.period_start >= cutoff
        ]
        avg_webhook_delivery = (
            statistics.mean(r.delivery_rate_pct for r in webhook_records)
            if webhook_records else 100.0
        )

        webhook_status = SLAStatus.COMPLIANT
        if sla.webhook_delivery_pct > 0:
            if avg_webhook_delivery < sla.webhook_delivery_pct:
                webhook_status = SLAStatus.BREACHED
            elif avg_webhook_delivery < sla.webhook_delivery_pct + 0.5:
                webhook_status = SLAStatus.AT_RISK

        return {
            "provider_id": provider_id,
            "period_days": days,
            "uptime": {
                "guaranteed_pct": sla.guaranteed_uptime_pct,
                "actual_pct": round(actual_uptime, 4),
                "gap_pct": round(uptime_gap, 4),
                "total_downtime_minutes": round(total_downtime, 1),
                "total_downtime_hours": round(total_downtime / 60, 2),
                "status": uptime_status.value,
                "credit_eligible": sla.credit_eligible and uptime_status == SLAStatus.BREACHED,
            },
            "incidents": {
                "count": len(incidents),
                "total_duration_minutes": round(
                    sum(i.duration_minutes for i in incidents), 1
                ),
                "avg_mttr_minutes": round(
                    statistics.mean(i.mttr_minutes for i in incidents), 1
                ) if incidents else 0,
                "unacknowledged_by_provider": len(
                    [i for i in incidents if not i.appeared_on_status_page]
                ),
            },
            "webhook_delivery": {
                "guaranteed_pct": sla.webhook_delivery_pct,
                "actual_pct": round(avg_webhook_delivery, 2),
                "status": webhook_status.value,
            },
        }

    # -- Composite Score ----------------------------------------------------

    def calculate_score(
        self, provider_id: str, days: int = 90
    ) -> dict:
        """Calculate composite provider health score (0-100).

        Scoring weights from PROVIDER_EVALUATION.md Section 5.2:
        - Uptime vs. SLA: 30%
        - Incident frequency: 20%
        - p95 latency trend: 15%
        - Webhook delivery rate: 15%
        - Support responsiveness: 10%
        - Developer experience: 10% (qualitative, defaults to 75)
        """
        sla = self._sla_configs.get(provider_id)
        if not sla:
            return {"provider_id": provider_id, "error": "No SLA configured"}

        compliance = self.get_sla_compliance(provider_id, days)
        cutoff = datetime.now() - timedelta(days=days)

        # 1. Uptime score (30%)
        actual_uptime = compliance["uptime"]["actual_pct"]
        guaranteed = sla.guaranteed_uptime_pct
        if actual_uptime >= guaranteed:
            uptime_score = 100
        else:
            gap = guaranteed - actual_uptime
            uptime_score = max(0, 100 - (gap * 200))  # Steep penalty

        # 2. Incident frequency (20%)
        incident_count = compliance["incidents"]["count"]
        if incident_count == 0:
            incident_score = 100
        else:
            incident_score = max(0, 100 - (incident_count * 15))

        # 3. p95 latency trend (15%)
        latency_records = sorted(
            [r for r in self._latency_records
             if r.provider_id == provider_id and r.period_start >= cutoff],
            key=lambda r: r.period_start
        )
        if len(latency_records) >= 2:
            recent_p95 = statistics.mean(
                r.p95_ms for r in latency_records[-3:]
            ) if len(latency_records) >= 3 else latency_records[-1].p95_ms
            older_p95 = statistics.mean(
                r.p95_ms for r in latency_records[:3]
            ) if len(latency_records) >= 3 else latency_records[0].p95_ms

            if older_p95 > 0:
                change_pct = ((recent_p95 - older_p95) / older_p95) * 100
            else:
                change_pct = 0

            if change_pct <= 0:
                latency_score = 100  # Improving or stable
            elif change_pct < 25:
                latency_score = 85
            elif change_pct < 50:
                latency_score = 60
            else:
                latency_score = max(0, 100 - change_pct)
        else:
            latency_score = 75  # Insufficient data

        # 4. Webhook delivery rate (15%)
        webhook_delivery = compliance["webhook_delivery"]["actual_pct"]
        if webhook_delivery >= 99.9:
            webhook_score = 100
        elif webhook_delivery >= 99.0:
            webhook_score = 85
        elif webhook_delivery >= 95.0:
            webhook_score = 60
        else:
            webhook_score = max(0, webhook_delivery)

        # 5. Support responsiveness (10%) — would come from ticket tracking
        support_score = 75  # Default; would integrate with support system

        # 6. Developer experience (10%) — qualitative
        dx_score = 75  # Default; would be periodically assessed by engineers

        # Weighted composite
        composite = (
            uptime_score * 0.30
            + incident_score * 0.20
            + latency_score * 0.15
            + webhook_score * 0.15
            + support_score * 0.10
            + dx_score * 0.10
        )

        # Grade
        if composite >= 90:
            grade = ScoreGrade.EXCELLENT
        elif composite >= 75:
            grade = ScoreGrade.GOOD
        elif composite >= 60:
            grade = ScoreGrade.CONCERNING
        else:
            grade = ScoreGrade.UNACCEPTABLE

        return {
            "provider_id": provider_id,
            "period_days": days,
            "composite_score": round(composite, 1),
            "grade": grade.value,
            "components": {
                "uptime": {"score": round(uptime_score, 1), "weight": "30%"},
                "incidents": {"score": round(incident_score, 1), "weight": "20%"},
                "latency_trend": {"score": round(latency_score, 1), "weight": "15%"},
                "webhook_delivery": {"score": round(webhook_score, 1), "weight": "15%"},
                "support": {"score": round(support_score, 1), "weight": "10%"},
                "developer_experience": {"score": round(dx_score, 1), "weight": "10%"},
            },
            "action": self._recommend_action(grade),
        }

    def _recommend_action(self, grade: ScoreGrade) -> str:
        actions = {
            ScoreGrade.EXCELLENT: "Maintain relationship. Consider expanding usage.",
            ScoreGrade.GOOD: "Monitor trends. Address specific issues in next QBR.",
            ScoreGrade.CONCERNING: "Escalate to provider's account team. Begin evaluating alternatives.",
            ScoreGrade.UNACCEPTABLE: "Initiate migration planning. Integrate fallback provider.",
        }
        return actions[grade]

    # -- QBR Data Packet ----------------------------------------------------

    def generate_qbr_packet(
        self, provider_id: str, days: int = 90
    ) -> dict:
        """Generate the complete data packet for a quarterly business review.

        This is the deliverable that changed vendor conversations from
        "we feel like things have been slow" to "here's the data."
        """
        compliance = self.get_sla_compliance(provider_id, days)
        score = self.calculate_score(provider_id, days)
        cutoff = datetime.now() - timedelta(days=days)

        # Cost data
        cost_records = [
            r for r in self._cost_records
            if r.provider_id == provider_id and r.period_start >= cutoff
        ]
        total_cost = sum(r.total_cost for r in cost_records)
        total_calls = sum(r.total_api_calls for r in cost_records)
        total_failed = sum(r.failed_calls for r in cost_records)

        # Latency history
        latency_records = sorted(
            [r for r in self._latency_records
             if r.provider_id == provider_id and r.period_start >= cutoff],
            key=lambda r: r.period_start
        )

        # Incident details
        incidents = [
            r for r in self._incidents
            if r.provider_id == provider_id and r.occurred_at >= cutoff
        ]

        return {
            "provider_id": provider_id,
            "period_days": days,
            "generated_at": datetime.now().isoformat(),
            "executive_summary": {
                "composite_score": score["composite_score"],
                "grade": score["grade"],
                "recommendation": score["action"],
                "key_concerns": self._identify_key_concerns(
                    compliance, score, incidents
                ),
            },
            "sla_compliance": compliance,
            "health_score": score,
            "cost_analysis": {
                "total_cost": round(total_cost, 2),
                "total_api_calls": total_calls,
                "cost_per_call": round(
                    total_cost / total_calls, 4
                ) if total_calls > 0 else 0,
                "failed_calls": total_failed,
                "cost_of_failures": round(
                    total_cost * (total_failed / total_calls), 2
                ) if total_calls > 0 else 0,
                "waste_pct": round(
                    total_failed / total_calls * 100, 2
                ) if total_calls > 0 else 0,
            },
            "latency_history": [
                {
                    "period": r.period_start.strftime("%Y-%m-%d"),
                    "p50_ms": r.p50_ms,
                    "p95_ms": r.p95_ms,
                    "p99_ms": r.p99_ms,
                    "samples": r.sample_count,
                }
                for r in latency_records
            ],
            "incident_details": [
                {
                    "incident_id": i.incident_id,
                    "date": i.occurred_at.strftime("%Y-%m-%d"),
                    "duration_minutes": round(i.duration_minutes, 1),
                    "severity": i.severity,
                    "root_cause": i.root_cause,
                    "on_status_page": i.appeared_on_status_page,
                    "mttr_minutes": round(i.mttr_minutes, 1),
                }
                for i in incidents
            ],
            "volume_trend": {
                "total_calls": total_calls,
                "monthly_average": round(
                    total_calls / max(days / 30, 1), 0
                ),
            },
        }

    def _identify_key_concerns(
        self, compliance: dict, score: dict, incidents: list
    ) -> list[str]:
        """Identify the top concerns to highlight in the QBR."""
        concerns = []

        if compliance["uptime"]["status"] == "breached":
            concerns.append(
                f"SLA breach: actual uptime {compliance['uptime']['actual_pct']:.2f}% "
                f"vs. guaranteed {compliance['uptime']['guaranteed_pct']}%"
            )

        if compliance["incidents"]["count"] >= 3:
            concerns.append(
                f"{compliance['incidents']['count']} incidents in the review period "
                f"indicates a systemic reliability issue"
            )

        if compliance["incidents"]["unacknowledged_by_provider"] > 0:
            concerns.append(
                f"{compliance['incidents']['unacknowledged_by_provider']} incidents "
                f"were not reflected on the provider's status page"
            )

        if compliance["webhook_delivery"]["status"] == "breached":
            concerns.append(
                f"Webhook delivery below SLA: "
                f"{compliance['webhook_delivery']['actual_pct']:.1f}% "
                f"vs. guaranteed {compliance['webhook_delivery']['guaranteed_pct']}%"
            )

        components = score.get("components", {})
        latency = components.get("latency_trend", {})
        if latency.get("score", 100) < 70:
            concerns.append("Latency trend is worsening over the review period")

        return concerns

    # -- Provider Comparison ------------------------------------------------

    def compare_providers(
        self, provider_ids: list[str], days: int = 90
    ) -> list[dict]:
        """Side-by-side provider comparison for evaluation."""
        comparisons = []
        for pid in provider_ids:
            score = self.calculate_score(pid, days)
            compliance = self.get_sla_compliance(pid, days)

            comparisons.append({
                "provider_id": pid,
                "composite_score": score.get("composite_score", 0),
                "grade": score.get("grade", "unknown"),
                "uptime_pct": compliance.get("uptime", {}).get("actual_pct", 0),
                "uptime_status": compliance.get("uptime", {}).get("status", "unknown"),
                "incident_count": compliance.get("incidents", {}).get("count", 0),
                "webhook_delivery_pct": compliance.get("webhook_delivery", {}).get("actual_pct", 0),
            })

        comparisons.sort(key=lambda x: x["composite_score"], reverse=True)
        return comparisons

    # -- Migration Triggers -------------------------------------------------

    def check_migration_triggers(
        self, provider_id: str
    ) -> dict:
        """Check if migration criteria are met (see PROVIDER_EVALUATION.md Section 5.3)."""
        compliance_6mo = self.get_sla_compliance(provider_id, days=180)
        compliance_90d = self.get_sla_compliance(provider_id, days=90)
        score = self.calculate_score(provider_id, days=90)

        triggers = {
            "sla_breaches_6mo": compliance_6mo.get("uptime", {}).get("status") == "breached",
            "incident_count_90d": compliance_90d.get("incidents", {}).get("count", 0),
            "high_incident_rate": compliance_90d.get("incidents", {}).get("count", 0) >= 3,
            "score_below_60": score.get("composite_score", 100) < 60,
            "latency_regressing": score.get("components", {}).get(
                "latency_trend", {}
            ).get("score", 100) < 60,
        }

        migration_recommended = (
            triggers["sla_breaches_6mo"]
            or triggers["high_incident_rate"]
            or triggers["score_below_60"]
        )

        return {
            "provider_id": provider_id,
            "migration_recommended": migration_recommended,
            "triggers": triggers,
            "recommendation": (
                "Migration evaluation recommended based on sustained performance issues."
                if migration_recommended
                else "No migration triggers met. Continue monitoring."
            ),
        }


# ---------------------------------------------------------------------------
# Usage Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    scorecard = ProviderScorecard()

    now = datetime.now()

    # Configure SLAs
    scorecard.configure_sla("kyc_provider", ProviderSLAConfig(
        guaranteed_uptime_pct=99.95,
        max_latency_ms=10000,
        webhook_delivery_pct=99.0,
        support_response_minutes=30,
        credit_eligible=True,
    ))
    scorecard.configure_sla("stripe", ProviderSLAConfig(
        guaranteed_uptime_pct=99.99,
        max_latency_ms=10000,
        webhook_delivery_pct=99.9,
        support_response_minutes=30,
        credit_eligible=True,
    ))

    # Add uptime records (KYC provider with issues)
    for week in range(12):
        week_start = now - timedelta(weeks=12 - week)
        minutes_in_week = 7 * 24 * 60

        # KYC has some downtime
        downtime = 45 if week in [3, 7, 9, 11] else 0
        scorecard.add_uptime_record(UptimeRecord(
            provider_id="kyc_provider",
            period_start=week_start,
            period_end=week_start + timedelta(weeks=1),
            total_minutes=minutes_in_week,
            available_minutes=minutes_in_week - downtime,
            downtime_minutes=downtime,
            incident_count=1 if downtime > 0 else 0,
            uptime_pct=(minutes_in_week - downtime) / minutes_in_week * 100,
        ))

        # Stripe is rock solid
        scorecard.add_uptime_record(UptimeRecord(
            provider_id="stripe",
            period_start=week_start,
            period_end=week_start + timedelta(weeks=1),
            total_minutes=minutes_in_week,
            available_minutes=minutes_in_week,
            downtime_minutes=0,
            incident_count=0,
            uptime_pct=100.0,
        ))

    # Add KYC incidents
    for idx, week in enumerate([3, 7, 9, 11]):
        scorecard.add_incident(IncidentRecord(
            provider_id="kyc_provider",
            incident_id=f"INC-KYC-{idx + 1}",
            occurred_at=now - timedelta(weeks=12 - week),
            duration_minutes=45,
            severity="p1",
            root_cause="Capacity issue during peak hours",
            appeared_on_status_page=(idx % 2 == 0),
            mttr_minutes=45 + (idx * 15),
        ))

    # Add latency records
    for week in range(12):
        week_start = now - timedelta(weeks=12 - week)
        scorecard.add_latency_record(LatencyRecord(
            provider_id="kyc_provider",
            period_start=week_start,
            period_end=week_start + timedelta(weeks=1),
            p50_ms=3800 + (week * 100),  # Gradually worsening
            p95_ms=8500 + (week * 200),
            p99_ms=11000 + (week * 300),
            sample_count=5000,
        ))

    # Add webhook records
    for week in range(12):
        week_start = now - timedelta(weeks=12 - week)
        scorecard.add_webhook_record(WebhookReliabilityRecord(
            provider_id="kyc_provider",
            period_start=week_start,
            period_end=week_start + timedelta(weeks=1),
            expected_deliveries=14280,
            actual_deliveries=int(14280 * (0.985 - week * 0.002)),
            delivery_rate_pct=98.5 - (week * 0.2),
            avg_delivery_latency_ms=1200,
        ))

    # Add cost records
    for month in range(3):
        month_start = now - timedelta(days=90 - month * 30)
        scorecard.add_cost_record(CostRecord(
            provider_id="kyc_provider",
            period_start=month_start,
            period_end=month_start + timedelta(days=30),
            total_cost=4500 + (month * 200),
            total_api_calls=22000 + (month * 1500),
            successful_calls=21500 + (month * 1400),
            failed_calls=500 + (month * 100),
        ))

    # Generate scorecard
    print("=== KYC PROVIDER SCORECARD ===\n")
    score = scorecard.calculate_score("kyc_provider")
    print(f"Composite Score: {score['composite_score']}/100")
    print(f"Grade: {score['grade'].upper()}")
    print(f"Action: {score['action']}")
    print(f"\nComponents:")
    for name, data in score["components"].items():
        bar = "█" * int(data["score"] / 10)
        print(f"  {name:<25} {data['score']:>5.1f} ({data['weight']}) {bar}")

    # SLA compliance
    print(f"\n=== SLA COMPLIANCE (90 days) ===\n")
    compliance = scorecard.get_sla_compliance("kyc_provider")
    uptime = compliance["uptime"]
    print(f"Uptime: {uptime['actual_pct']:.2f}% (guaranteed: {uptime['guaranteed_pct']}%)")
    print(f"Status: {uptime['status'].upper()}")
    print(f"Downtime: {uptime['total_downtime_hours']:.1f} hours")
    print(f"Credit eligible: {uptime['credit_eligible']}")
    print(f"Incidents: {compliance['incidents']['count']}")
    print(f"Avg MTTR: {compliance['incidents']['avg_mttr_minutes']} min")
    print(f"Unacknowledged by provider: {compliance['incidents']['unacknowledged_by_provider']}")

    # Migration triggers
    print(f"\n=== MIGRATION TRIGGERS ===\n")
    triggers = scorecard.check_migration_triggers("kyc_provider")
    print(f"Migration recommended: {'YES' if triggers['migration_recommended'] else 'No'}")
    for trigger, value in triggers["triggers"].items():
        flag = "🔴" if value else "✅"
        print(f"  {flag} {trigger}: {value}")

    # QBR packet
    print(f"\n=== QBR KEY CONCERNS ===\n")
    qbr = scorecard.generate_qbr_packet("kyc_provider")
    for concern in qbr["executive_summary"]["key_concerns"]:
        print(f"  ⚠️  {concern}")

    # Compare providers
    print(f"\n=== PROVIDER COMPARISON ===\n")
    comparison = scorecard.compare_providers(["kyc_provider", "stripe"])
    for p in comparison:
        print(
            f"  {p['provider_id']:<15} "
            f"Score: {p['composite_score']:>5.1f} "
            f"Grade: {p['grade']:<12} "
            f"Uptime: {p['uptime_pct']:.2f}% "
            f"Incidents: {p['incident_count']}"
        )
