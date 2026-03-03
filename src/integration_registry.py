"""
Integration Registry — Central catalog of all third-party API providers.

PM-authored reference implementation. Built to solve the "nobody knows what
integrations we have" problem that appeared across every client engagement.
Before this existed, provider configurations were scattered across env vars,
config files, and hardcoded values. Engineers couldn't answer "how many
integrations do we have?" without reading code.

This became the foundation for all monitoring — you can't monitor what you
haven't cataloged.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ProviderCategory(Enum):
    """Functional category for blast-radius mapping and triage routing."""
    IDENTITY_VERIFICATION = "identity_verification"
    FINANCIAL_CONNECTIVITY = "financial_connectivity"
    COMMUNICATION = "communication"
    FULFILLMENT_OPERATIONS = "fulfillment_operations"
    COMPLIANCE_RISK = "compliance_risk"
    DATA_ANALYTICS = "data_analytics"
    DOCUMENT_WORKFLOW = "document_workflow"
    INDUSTRY_SPECIFIC = "industry_specific"


class BlastRadius(Enum):
    """Impact severity when this provider goes down.

    Defined in INTEGRATION_ARCHITECTURE.md Section 5.1.
    P0 = revenue stops. P3 = nobody notices until Monday.
    """
    P0_REVENUE_BLOCKING = "p0"
    P1_ONBOARDING_BLOCKING = "p1"
    P2_FEATURE_DEGRADED = "p2"
    P3_BACK_OFFICE = "p3"


class AuthMethod(Enum):
    """How we authenticate with this provider."""
    API_KEY_HEADER = "api_key_header"       # X-Api-Key or Authorization: Bearer
    API_KEY_QUERY = "api_key_query"         # ?api_key=xxx (legacy, some carriers)
    OAUTH2 = "oauth2"                       # Token refresh flow
    HMAC_SIGNATURE = "hmac_signature"       # Request signing (webhooks)
    MUTUAL_TLS = "mutual_tls"              # Certificate-based (healthcare/finance)
    BASIC_AUTH = "basic_auth"              # Username:password (legacy ERPs)


class DataFlowPattern(Enum):
    """How data moves between us and this provider.

    Determines which monitoring approach applies.
    See INTEGRATION_ARCHITECTURE.md Section 4.
    """
    SYNC_REQUEST_RESPONSE = "sync"         # We call, they respond immediately
    WEBHOOK_ASYNC = "webhook"              # They push events to our endpoint
    POLLING = "polling"                    # We periodically check for updates
    FILE_BATCH = "file_batch"             # Scheduled file exchange (SFTP/S3)
    BIDIRECTIONAL = "bidirectional"        # Both sync calls and webhook events


class CircuitState(Enum):
    """Circuit breaker state for this provider."""
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Provider failing, requests fail fast
    HALF_OPEN = "half_open" # Testing recovery with probe requests


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class SLADefinition:
    """What the provider contractually guarantees.

    These numbers come from the provider's contract or terms of service.
    The provider_scorecard module compares actual performance against these.
    """
    guaranteed_uptime_pct: float              # e.g., 99.95
    max_response_time_ms: int                 # Contractual latency ceiling
    webhook_delivery_guarantee_pct: float     # e.g., 99.9 (0.0 if no guarantee)
    support_response_time_minutes: int        # For P0 issues
    deprecation_notice_days: int              # Advance notice for breaking changes
    credit_eligible: bool                     # Can we claim credits for SLA breach?

    def annual_downtime_budget_minutes(self) -> float:
        """How many minutes of downtime the SLA allows per year.

        99.9%  = 525.6 minutes  (~8.76 hours)
        99.95% = 262.8 minutes  (~4.38 hours)
        99.99% = 52.56 minutes  (~0.88 hours)
        """
        return (1 - self.guaranteed_uptime_pct / 100) * 525_960


@dataclass
class EndpointConfig:
    """Single API endpoint we consume from this provider."""
    path: str                                  # e.g., "/v1/verifications"
    method: str                                # GET, POST, etc.
    description: str                           # What this endpoint does
    critical: bool                             # Is this on the critical user path?
    expected_latency_ms: int                   # What we expect under normal conditions
    timeout_ms: int                            # When we stop waiting
    rate_limit_per_minute: Optional[int]        # Provider's published rate limit
    idempotent: bool = False                   # Safe to retry without side effects?


@dataclass
class WebhookConfig:
    """Configuration for receiving webhooks from this provider."""
    endpoint_path: str                         # Our receiver URL path
    events_subscribed: list[str]               # Which events we listen for
    signature_header: str                      # Header containing HMAC signature
    signature_algorithm: str                   # e.g., "sha256"
    retry_policy: str                          # Provider's retry behavior
    max_retry_attempts: int                    # How many times they'll retry
    retry_window_hours: int                    # How long they'll keep retrying
    expected_volume_per_hour: int              # Baseline for anomaly detection


@dataclass
class FallbackConfig:
    """What to do when this provider fails."""
    fallback_provider_id: Optional[str]        # ID of the backup provider
    fallback_type: str                         # "hot", "warm", or "graceful_degradation"
    activation_method: str                     # How to switch: "automatic", "feature_flag", "manual"
    estimated_switchover_seconds: int          # How long failover takes
    data_compatibility: str                    # "full", "partial", "format_conversion_required"
    notes: str                                 # Operational notes for on-call


@dataclass
class HealthCheckConfig:
    """How we monitor this provider's availability."""
    health_endpoint: Optional[str]             # Provider's health check URL if available
    status_page_url: Optional[str]             # Provider's public status page
    check_interval_seconds: int                # How often we probe
    healthy_response_codes: list[int]          # Which HTTP codes mean "healthy"
    circuit_breaker_error_threshold_pct: float # Error rate to trip circuit breaker
    circuit_breaker_window_seconds: int        # Time window for error rate calc
    circuit_breaker_recovery_probes: int       # Successful probes to close circuit


# ---------------------------------------------------------------------------
# Provider Registration
# ---------------------------------------------------------------------------

@dataclass
class Provider:
    """Complete registration for a single third-party API provider.

    This is the atomic unit of the registry. Every provider we integrate
    with gets one of these, containing everything needed to monitor,
    triage, and manage the integration.
    """
    # Identity
    id: str                                    # Unique key, e.g., "plaid"
    name: str                                  # Display name, e.g., "Plaid"
    category: ProviderCategory
    blast_radius: BlastRadius
    data_flow: DataFlowPattern
    auth_method: AuthMethod

    # Configuration
    base_url: str                              # e.g., "https://api.plaid.com"
    api_version: str                           # e.g., "2023-10-12"
    endpoints: list[EndpointConfig]
    webhook_config: Optional[WebhookConfig]
    health_check: HealthCheckConfig
    sla: SLADefinition
    fallback: Optional[FallbackConfig]

    # Metadata
    contract_owner: str                        # Internal person who owns this relationship
    technical_contact_email: str               # Provider's technical support email
    account_manager_email: Optional[str]       # Provider's account manager
    integration_date: datetime                 # When we first integrated
    last_contract_review: Optional[datetime]   # Last QBR or contract renegotiation
    notes: str = ""                            # Free-text operational notes

    # Runtime state (updated by api_health_tracker)
    circuit_state: CircuitState = CircuitState.CLOSED
    last_health_check: Optional[datetime] = None
    current_error_rate_pct: float = 0.0
    current_p95_latency_ms: float = 0.0

    def is_critical_path(self) -> bool:
        """Is this provider on a revenue or onboarding critical path?"""
        return self.blast_radius in (
            BlastRadius.P0_REVENUE_BLOCKING,
            BlastRadius.P1_ONBOARDING_BLOCKING
        )

    def has_fallback(self) -> bool:
        return self.fallback is not None

    def is_healthy(self) -> bool:
        return self.circuit_state == CircuitState.CLOSED

    def days_since_contract_review(self) -> Optional[int]:
        if self.last_contract_review is None:
            return None
        return (datetime.now() - self.last_contract_review).days


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class IntegrationRegistry:
    """Central registry of all third-party API providers.

    Single source of truth for what integrations exist, how they're
    configured, and what their current state is. Used by every other
    module in the system.

    In production, this would be backed by a database. For the prototype,
    it's an in-memory store that we populate with provider configs.
    """

    def __init__(self):
        self._providers: dict[str, Provider] = {}
        self._dependency_map: dict[str, list[str]] = {}  # flow_name -> [provider_ids]

    # -- Registration -------------------------------------------------------

    def register(self, provider: Provider) -> None:
        """Add a provider to the registry."""
        if provider.id in self._providers:
            raise ValueError(
                f"Provider '{provider.id}' already registered. "
                f"Use update() to modify existing providers."
            )
        self._providers[provider.id] = provider

    def update(self, provider: Provider) -> None:
        """Update an existing provider's configuration."""
        if provider.id not in self._providers:
            raise KeyError(f"Provider '{provider.id}' not found in registry.")
        self._providers[provider.id] = provider

    def remove(self, provider_id: str) -> Provider:
        """Remove a provider from the registry. Returns the removed provider."""
        if provider_id not in self._providers:
            raise KeyError(f"Provider '{provider_id}' not found in registry.")
        return self._providers.pop(provider_id)

    # -- Lookup -------------------------------------------------------------

    def get(self, provider_id: str) -> Provider:
        """Get a provider by ID."""
        if provider_id not in self._providers:
            raise KeyError(f"Provider '{provider_id}' not found in registry.")
        return self._providers[provider_id]

    def list_all(self) -> list[Provider]:
        """List all registered providers."""
        return list(self._providers.values())

    def list_by_category(self, category: ProviderCategory) -> list[Provider]:
        """List providers in a specific functional category."""
        return [p for p in self._providers.values() if p.category == category]

    def list_by_blast_radius(self, blast_radius: BlastRadius) -> list[Provider]:
        """List providers with a specific blast radius.

        Used during incidents to quickly identify what's affected and how severe.
        """
        return [p for p in self._providers.values() if p.blast_radius == blast_radius]

    def list_critical_path(self) -> list[Provider]:
        """List all P0 and P1 providers — the ones that break user flows."""
        return [p for p in self._providers.values() if p.is_critical_path()]

    def list_without_fallback(self) -> list[Provider]:
        """List critical-path providers with no fallback configured.

        These are the single points of failure. Every item on this list
        is a risk that should be reviewed quarterly.
        """
        return [
            p for p in self._providers.values()
            if p.is_critical_path() and not p.has_fallback()
        ]

    def list_unhealthy(self) -> list[Provider]:
        """List providers with open or half-open circuit breakers."""
        return [
            p for p in self._providers.values()
            if p.circuit_state != CircuitState.CLOSED
        ]

    def list_by_data_flow(self, pattern: DataFlowPattern) -> list[Provider]:
        """List providers using a specific data flow pattern.

        Useful for targeted monitoring — e.g., list all webhook-based
        providers to check delivery rates.
        """
        return [p for p in self._providers.values() if p.data_flow == pattern]

    # -- Dependency Mapping -------------------------------------------------

    def register_flow_dependencies(
        self, flow_name: str, provider_ids: list[str]
    ) -> None:
        """Map a user flow to its provider dependencies (in order).

        Example:
            registry.register_flow_dependencies(
                "user_onboarding",
                ["twilio", "socure", "plaid", "credit_bureau", "stripe"]
            )

        The onboarding_funnel module uses this mapping to correlate
        drop-off with provider health.
        """
        for pid in provider_ids:
            if pid not in self._providers:
                raise KeyError(
                    f"Provider '{pid}' not found. Register it before "
                    f"adding it to a flow dependency."
                )
        self._dependency_map[flow_name] = provider_ids

    def get_flow_dependencies(self, flow_name: str) -> list[Provider]:
        """Get the ordered list of providers for a user flow."""
        if flow_name not in self._dependency_map:
            raise KeyError(f"Flow '{flow_name}' not found in dependency map.")
        return [self._providers[pid] for pid in self._dependency_map[flow_name]]

    def get_flow_health(self, flow_name: str) -> dict:
        """Get health summary for a user flow's dependency chain.

        Returns the chain's weakest link and overall health status.
        Used by the dashboard to show flow-level health at a glance.
        """
        providers = self.get_flow_dependencies(flow_name)
        unhealthy = [p for p in providers if not p.is_healthy()]
        no_fallback = [p for p in providers if not p.has_fallback()]

        chain_healthy = len(unhealthy) == 0

        # Compound reliability: multiply individual uptimes
        # Five 99.9% providers in sequence = 99.5% chain reliability
        chain_reliability = 1.0
        for p in providers:
            chain_reliability *= p.sla.guaranteed_uptime_pct / 100

        return {
            "flow_name": flow_name,
            "provider_count": len(providers),
            "chain_healthy": chain_healthy,
            "unhealthy_providers": [p.id for p in unhealthy],
            "single_points_of_failure": [p.id for p in no_fallback],
            "theoretical_chain_uptime_pct": round(chain_reliability * 100, 4),
            "total_expected_latency_ms": sum(
                min(e.expected_latency_ms for e in p.endpoints) if p.endpoints else 0
                for p in providers
            ),
        }

    def list_flows_affected_by(self, provider_id: str) -> list[str]:
        """Which user flows are affected when this provider goes down?

        Used by incident_detector to assess blast radius when an alert fires.
        """
        return [
            flow_name
            for flow_name, pids in self._dependency_map.items()
            if provider_id in pids
        ]

    # -- Audit & Reporting --------------------------------------------------

    def audit_report(self) -> dict:
        """Generate a registry audit for quarterly review.

        Flags providers that need attention: missing fallbacks, overdue
        contract reviews, and stale health checks.
        """
        all_providers = self.list_all()
        overdue_review_days = 120  # Flag if no QBR in 4+ months

        return {
            "total_providers": len(all_providers),
            "by_category": {
                cat.value: len(self.list_by_category(cat))
                for cat in ProviderCategory
            },
            "by_blast_radius": {
                br.value: len(self.list_by_blast_radius(br))
                for br in BlastRadius
            },
            "critical_without_fallback": [
                {"id": p.id, "name": p.name, "blast_radius": p.blast_radius.value}
                for p in self.list_without_fallback()
            ],
            "overdue_contract_review": [
                {
                    "id": p.id,
                    "name": p.name,
                    "days_since_review": p.days_since_contract_review(),
                }
                for p in all_providers
                if p.days_since_contract_review() is not None
                and p.days_since_contract_review() > overdue_review_days
            ],
            "no_contract_review_recorded": [
                {"id": p.id, "name": p.name}
                for p in all_providers
                if p.last_contract_review is None
            ],
            "currently_unhealthy": [
                {"id": p.id, "name": p.name, "state": p.circuit_state.value}
                for p in self.list_unhealthy()
            ],
            "webhook_providers": [
                p.id for p in self.list_by_data_flow(DataFlowPattern.WEBHOOK_ASYNC)
            ] + [
                p.id for p in self.list_by_data_flow(DataFlowPattern.BIDIRECTIONAL)
            ],
            "total_flows_mapped": len(self._dependency_map),
        }


# ---------------------------------------------------------------------------
# Example: Lending Client Registry
# ---------------------------------------------------------------------------

def build_lending_client_registry() -> IntegrationRegistry:
    """Builds the registry for the digital lending startup engagement.

    This is the actual provider configuration (with synthetic credentials)
    that was used for the lending client. Demonstrates how the registry
    captures the full integration landscape in one place.
    """
    registry = IntegrationRegistry()

    # -- Twilio (SMS / 2FA) -------------------------------------------------
    registry.register(Provider(
        id="twilio",
        name="Twilio",
        category=ProviderCategory.COMMUNICATION,
        blast_radius=BlastRadius.P1_ONBOARDING_BLOCKING,
        data_flow=DataFlowPattern.BIDIRECTIONAL,
        auth_method=AuthMethod.BASIC_AUTH,
        base_url="https://api.twilio.com/2010-04-01",
        api_version="2010-04-01",
        endpoints=[
            EndpointConfig(
                path="/Accounts/{sid}/Messages.json",
                method="POST",
                description="Send SMS for 2FA verification",
                critical=True,
                expected_latency_ms=800,
                timeout_ms=5000,
                rate_limit_per_minute=None,  # Account-level, not per-endpoint
                idempotent=False,
            ),
        ],
        webhook_config=WebhookConfig(
            endpoint_path="/webhooks/twilio/status",
            events_subscribed=["message.delivered", "message.failed", "message.undelivered"],
            signature_header="X-Twilio-Signature",
            signature_algorithm="hmac-sha1",
            retry_policy="Twilio retries failed webhook deliveries with exponential backoff",
            max_retry_attempts=3,
            retry_window_hours=24,
            expected_volume_per_hour=120,
        ),
        health_check=HealthCheckConfig(
            health_endpoint=None,
            status_page_url="https://status.twilio.com",
            check_interval_seconds=30,
            healthy_response_codes=[200, 201],
            circuit_breaker_error_threshold_pct=15.0,
            circuit_breaker_window_seconds=120,
            circuit_breaker_recovery_probes=3,
        ),
        sla=SLADefinition(
            guaranteed_uptime_pct=99.95,
            max_response_time_ms=5000,
            webhook_delivery_guarantee_pct=0.0,  # No explicit webhook SLA
            support_response_time_minutes=60,
            deprecation_notice_days=365,
            credit_eligible=True,
        ),
        fallback=FallbackConfig(
            fallback_provider_id="sendgrid",
            fallback_type="warm",
            activation_method="feature_flag",
            estimated_switchover_seconds=5,
            data_compatibility="partial",
            notes="Fall back to email verification via SendGrid. Worse UX but functional.",
        ),
        contract_owner="Sarah Chen",
        technical_contact_email="support@twilio.com",
        account_manager_email="enterprise@twilio.com",
        integration_date=datetime(2023, 3, 15),
        last_contract_review=datetime(2024, 9, 1),
    ))

    # -- Identity Verification Provider -------------------------------------
    registry.register(Provider(
        id="kyc_provider",
        name="KYC Provider",
        category=ProviderCategory.IDENTITY_VERIFICATION,
        blast_radius=BlastRadius.P1_ONBOARDING_BLOCKING,
        data_flow=DataFlowPattern.BIDIRECTIONAL,
        auth_method=AuthMethod.API_KEY_HEADER,
        base_url="https://api.kycprovider.com",
        api_version="v3",
        endpoints=[
            EndpointConfig(
                path="/v3/verifications",
                method="POST",
                description="Submit identity verification request",
                critical=True,
                expected_latency_ms=3800,
                timeout_ms=12000,
                rate_limit_per_minute=200,
                idempotent=True,
            ),
            EndpointConfig(
                path="/v3/verifications/{id}",
                method="GET",
                description="Check verification status",
                critical=True,
                expected_latency_ms=200,
                timeout_ms=5000,
                rate_limit_per_minute=500,
                idempotent=True,
            ),
        ],
        webhook_config=WebhookConfig(
            endpoint_path="/webhooks/kyc/events",
            events_subscribed=["verification.completed", "verification.failed", "verification.review"],
            signature_header="X-Signature",
            signature_algorithm="hmac-sha256",
            retry_policy="3 retries with exponential backoff over 24 hours",
            max_retry_attempts=3,
            retry_window_hours=24,
            expected_volume_per_hour=85,
        ),
        health_check=HealthCheckConfig(
            health_endpoint="https://api.kycprovider.com/health",
            status_page_url="https://status.kycprovider.com",
            check_interval_seconds=30,
            healthy_response_codes=[200],
            circuit_breaker_error_threshold_pct=10.0,  # Lower threshold — this is the bottleneck
            circuit_breaker_window_seconds=180,
            circuit_breaker_recovery_probes=5,
        ),
        sla=SLADefinition(
            guaranteed_uptime_pct=99.95,
            max_response_time_ms=10000,
            webhook_delivery_guarantee_pct=99.0,
            support_response_time_minutes=30,
            deprecation_notice_days=180,
            credit_eligible=True,
        ),
        fallback=FallbackConfig(
            fallback_provider_id=None,
            fallback_type="graceful_degradation",
            activation_method="automatic",
            estimated_switchover_seconds=0,
            data_compatibility="full",
            notes="Route to manual review queue when KYC provider is down. "
                  "Manual review adds 24-48 hour delay but doesn't lose the applicant.",
        ),
        contract_owner="Sarah Chen",
        technical_contact_email="support@kycprovider.com",
        account_manager_email="enterprise@kycprovider.com",
        integration_date=datetime(2023, 4, 1),
        last_contract_review=datetime(2024, 11, 15),
        notes="P95 latency is the #1 onboarding bottleneck. 11.2s at peak. "
              "Negotiated dedicated capacity allocation in Nov 2024 QBR.",
    ))

    # -- Plaid (Bank Account Linking) ---------------------------------------
    registry.register(Provider(
        id="plaid",
        name="Plaid",
        category=ProviderCategory.FINANCIAL_CONNECTIVITY,
        blast_radius=BlastRadius.P1_ONBOARDING_BLOCKING,
        data_flow=DataFlowPattern.BIDIRECTIONAL,
        auth_method=AuthMethod.API_KEY_HEADER,
        base_url="https://api.plaid.com",
        api_version="2020-09-14",
        endpoints=[
            EndpointConfig(
                path="/link/token/create",
                method="POST",
                description="Create Link token for bank account connection UI",
                critical=True,
                expected_latency_ms=1200,
                timeout_ms=8000,
                rate_limit_per_minute=100,
                idempotent=False,
            ),
            EndpointConfig(
                path="/item/public_token/exchange",
                method="POST",
                description="Exchange public token for access token after Link",
                critical=True,
                expected_latency_ms=600,
                timeout_ms=5000,
                rate_limit_per_minute=100,
                idempotent=True,
            ),
            EndpointConfig(
                path="/accounts/balance/get",
                method="POST",
                description="Retrieve account balances for underwriting",
                critical=True,
                expected_latency_ms=2100,
                timeout_ms=10000,
                rate_limit_per_minute=50,
                idempotent=True,
            ),
        ],
        webhook_config=WebhookConfig(
            endpoint_path="/webhooks/plaid/events",
            events_subscribed=[
                "ITEM.WEBHOOK_UPDATE_ACKNOWLEDGED",
                "TRANSACTIONS.INITIAL_UPDATE",
                "TRANSACTIONS.DEFAULT_UPDATE",
                "ITEM.ERROR",
                "ITEM.PENDING_EXPIRATION",
            ],
            signature_header="Plaid-Verification",
            signature_algorithm="sha256",
            retry_policy="Plaid retries webhooks up to 3 times over 24 hours",
            max_retry_attempts=3,
            retry_window_hours=24,
            expected_volume_per_hour=200,
        ),
        health_check=HealthCheckConfig(
            health_endpoint=None,
            status_page_url="https://status.plaid.com",
            check_interval_seconds=30,
            healthy_response_codes=[200],
            circuit_breaker_error_threshold_pct=12.0,
            circuit_breaker_window_seconds=180,
            circuit_breaker_recovery_probes=3,
        ),
        sla=SLADefinition(
            guaranteed_uptime_pct=99.9,
            max_response_time_ms=10000,
            webhook_delivery_guarantee_pct=0.0,  # No explicit webhook SLA
            support_response_time_minutes=60,
            deprecation_notice_days=365,
            credit_eligible=False,
        ),
        fallback=FallbackConfig(
            fallback_provider_id=None,
            fallback_type="graceful_degradation",
            activation_method="automatic",
            estimated_switchover_seconds=0,
            data_compatibility="full",
            notes="Fall back to manual bank statement upload. User uploads PDF, "
                  "OCR extracts account/balance data. Adds 24-48 hours to process.",
        ),
        contract_owner="Mike Torres",
        technical_contact_email="support@plaid.com",
        account_manager_email=None,
        integration_date=datetime(2023, 2, 10),
        last_contract_review=datetime(2024, 8, 20),
        notes="Webhook delivery dropped to 84% for 2 weeks in Q3 2024 before "
              "anyone noticed. This was the incident that triggered building "
              "the webhook_monitor module.",
    ))

    # -- Credit Bureau API --------------------------------------------------
    registry.register(Provider(
        id="credit_bureau",
        name="Credit Bureau API",
        category=ProviderCategory.COMPLIANCE_RISK,
        blast_radius=BlastRadius.P0_REVENUE_BLOCKING,
        data_flow=DataFlowPattern.SYNC_REQUEST_RESPONSE,
        auth_method=AuthMethod.MUTUAL_TLS,
        base_url="https://api.creditbureau.com",
        api_version="v2",
        endpoints=[
            EndpointConfig(
                path="/v2/credit-reports",
                method="POST",
                description="Pull credit report for underwriting decision",
                critical=True,
                expected_latency_ms=900,
                timeout_ms=15000,
                rate_limit_per_minute=60,
                idempotent=True,
            ),
        ],
        webhook_config=None,  # Synchronous only — no webhooks
        health_check=HealthCheckConfig(
            health_endpoint="https://api.creditbureau.com/v2/health",
            status_page_url=None,
            check_interval_seconds=60,
            healthy_response_codes=[200],
            circuit_breaker_error_threshold_pct=5.0,  # Very low — this is P0 with no fallback
            circuit_breaker_window_seconds=120,
            circuit_breaker_recovery_probes=5,
        ),
        sla=SLADefinition(
            guaranteed_uptime_pct=99.99,
            max_response_time_ms=15000,
            webhook_delivery_guarantee_pct=0.0,
            support_response_time_minutes=15,
            deprecation_notice_days=365,
            credit_eligible=True,
        ),
        fallback=None,  # HARD DEPENDENCY — no fallback exists
        contract_owner="Sarah Chen",
        technical_contact_email="api-support@creditbureau.com",
        account_manager_email="enterprise@creditbureau.com",
        integration_date=datetime(2023, 5, 1),
        last_contract_review=datetime(2024, 12, 1),
        notes="SINGLE POINT OF FAILURE. No fallback provider. Cannot underwrite "
              "without a credit pull. If this goes down, the entire lending "
              "pipeline stops. Monthly uptime review with their team.",
    ))

    # -- Stripe (Payment Disbursement) --------------------------------------
    registry.register(Provider(
        id="stripe",
        name="Stripe",
        category=ProviderCategory.FINANCIAL_CONNECTIVITY,
        blast_radius=BlastRadius.P0_REVENUE_BLOCKING,
        data_flow=DataFlowPattern.BIDIRECTIONAL,
        auth_method=AuthMethod.API_KEY_HEADER,
        base_url="https://api.stripe.com",
        api_version="2024-04-10",
        endpoints=[
            EndpointConfig(
                path="/v1/payment_intents",
                method="POST",
                description="Create payment intent for loan disbursement",
                critical=True,
                expected_latency_ms=800,
                timeout_ms=10000,
                rate_limit_per_minute=100,
                idempotent=True,
            ),
            EndpointConfig(
                path="/v1/transfers",
                method="POST",
                description="Transfer funds to borrower's connected account",
                critical=True,
                expected_latency_ms=1400,
                timeout_ms=15000,
                rate_limit_per_minute=100,
                idempotent=True,
            ),
            EndpointConfig(
                path="/v1/refunds",
                method="POST",
                description="Process refund for overpayment or cancellation",
                critical=False,
                expected_latency_ms=600,
                timeout_ms=10000,
                rate_limit_per_minute=100,
                idempotent=True,
            ),
        ],
        webhook_config=WebhookConfig(
            endpoint_path="/webhooks/stripe/events",
            events_subscribed=[
                "payment_intent.succeeded",
                "payment_intent.payment_failed",
                "transfer.created",
                "transfer.failed",
                "charge.dispute.created",
                "account.updated",
            ],
            signature_header="Stripe-Signature",
            signature_algorithm="hmac-sha256",
            retry_policy="Stripe retries up to 3 days with exponential backoff",
            max_retry_attempts=15,
            retry_window_hours=72,
            expected_volume_per_hour=350,
        ),
        health_check=HealthCheckConfig(
            health_endpoint=None,
            status_page_url="https://status.stripe.com",
            check_interval_seconds=30,
            healthy_response_codes=[200],
            circuit_breaker_error_threshold_pct=5.0,
            circuit_breaker_window_seconds=120,
            circuit_breaker_recovery_probes=3,
        ),
        sla=SLADefinition(
            guaranteed_uptime_pct=99.99,
            max_response_time_ms=10000,
            webhook_delivery_guarantee_pct=99.9,
            support_response_time_minutes=30,
            deprecation_notice_days=365,
            credit_eligible=True,
        ),
        fallback=FallbackConfig(
            fallback_provider_id="ach_backup",
            fallback_type="warm",
            activation_method="feature_flag",
            estimated_switchover_seconds=30,
            data_compatibility="partial",
            notes="Fall back to direct ACH via backup processor. Next-day settlement "
                  "instead of instant. Acceptable for non-urgent disbursements.",
        ),
        contract_owner="Mike Torres",
        technical_contact_email="support@stripe.com",
        account_manager_email="enterprise@stripe.com",
        integration_date=datetime(2023, 1, 15),
        last_contract_review=datetime(2024, 10, 1),
    ))

    # -- Email Service (P3 — non-critical) ----------------------------------
    registry.register(Provider(
        id="sendgrid",
        name="SendGrid",
        category=ProviderCategory.COMMUNICATION,
        blast_radius=BlastRadius.P3_BACK_OFFICE,
        data_flow=DataFlowPattern.BIDIRECTIONAL,
        auth_method=AuthMethod.API_KEY_HEADER,
        base_url="https://api.sendgrid.com",
        api_version="v3",
        endpoints=[
            EndpointConfig(
                path="/v3/mail/send",
                method="POST",
                description="Send transactional email",
                critical=False,
                expected_latency_ms=300,
                timeout_ms=5000,
                rate_limit_per_minute=600,
                idempotent=False,
            ),
        ],
        webhook_config=WebhookConfig(
            endpoint_path="/webhooks/sendgrid/events",
            events_subscribed=["delivered", "bounced", "dropped", "deferred"],
            signature_header="X-Twilio-Email-Event-Webhook-Signature",
            signature_algorithm="ecdsa",
            retry_policy="SendGrid does not retry webhook delivery",
            max_retry_attempts=0,
            retry_window_hours=0,
            expected_volume_per_hour=500,
        ),
        health_check=HealthCheckConfig(
            health_endpoint=None,
            status_page_url="https://status.sendgrid.com",
            check_interval_seconds=120,
            healthy_response_codes=[200, 202],
            circuit_breaker_error_threshold_pct=25.0,  # Higher threshold — non-critical
            circuit_breaker_window_seconds=300,
            circuit_breaker_recovery_probes=2,
        ),
        sla=SLADefinition(
            guaranteed_uptime_pct=99.95,
            max_response_time_ms=5000,
            webhook_delivery_guarantee_pct=0.0,
            support_response_time_minutes=120,
            deprecation_notice_days=180,
            credit_eligible=False,
        ),
        fallback=None,  # Non-critical. Emails queue and retry later.
        contract_owner="Sarah Chen",
        technical_contact_email="support@sendgrid.com",
        account_manager_email=None,
        integration_date=datetime(2023, 1, 20),
        last_contract_review=None,
        notes="Also serves as warm fallback for Twilio (email verification "
              "instead of SMS). SendGrid itself has no fallback — if email "
              "is down, notifications queue locally and retry.",
    ))

    # -- Register User Flow Dependencies ------------------------------------

    registry.register_flow_dependencies(
        "user_onboarding",
        ["twilio", "kyc_provider", "plaid", "credit_bureau", "stripe"]
    )

    registry.register_flow_dependencies(
        "loan_disbursement",
        ["credit_bureau", "stripe"]
    )

    registry.register_flow_dependencies(
        "payment_collection",
        ["stripe"]
    )

    return registry


# ---------------------------------------------------------------------------
# Usage Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    registry = build_lending_client_registry()

    print("=== REGISTRY AUDIT ===\n")
    audit = registry.audit_report()
    print(f"Total providers: {audit['total_providers']}")
    print(f"Total flows mapped: {audit['total_flows_mapped']}")

    print(f"\nBy category:")
    for cat, count in audit["by_category"].items():
        if count > 0:
            print(f"  {cat}: {count}")

    print(f"\nBy blast radius:")
    for br, count in audit["by_blast_radius"].items():
        if count > 0:
            print(f"  {br}: {count}")

    print(f"\nCritical providers WITHOUT fallback:")
    for p in audit["critical_without_fallback"]:
        print(f"  ⚠️  {p['name']} ({p['blast_radius']})")

    print(f"\n=== ONBOARDING FLOW HEALTH ===\n")
    flow = registry.get_flow_health("user_onboarding")
    print(f"Providers in chain: {flow['provider_count']}")
    print(f"Chain healthy: {flow['chain_healthy']}")
    print(f"Theoretical chain uptime: {flow['theoretical_chain_uptime_pct']}%")
    print(f"Single points of failure: {flow['single_points_of_failure']}")
    print(f"Total expected latency: {flow['total_expected_latency_ms']}ms")
