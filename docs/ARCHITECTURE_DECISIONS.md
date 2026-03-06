# Architecture Decision Records

This document captures the key architectural decisions made during the design and implementation of the Integration Health Monitor. Each ADR explains the context, the decision, alternatives evaluated, and the resulting trade-offs.

---

## ADR-001: Three-State Circuit Breaker with Per-Provider Configuration

**Status:** Accepted
**Date:** 2024-03
**Context:** Third-party API providers fail in different ways and at different rates. A blanket failure-handling strategy would either be too aggressive (cutting off providers that are merely slow) or too lenient (continuing to send traffic to providers that are actively failing). The system needed a mechanism to automatically stop sending traffic to failing providers while still allowing recovery detection.
**Decision:** Implement a three-state circuit breaker (CLOSED, OPEN, HALF_OPEN) with per-provider thresholds sourced from the integration registry's `HealthCheckConfig`. Each provider has its own `error_threshold_pct`, `window_seconds`, `recovery_probes`, and `probe_interval_seconds`. The circuit breaker transitions from CLOSED to OPEN when the error rate within the configured window exceeds the threshold, enters HALF_OPEN after the probe interval elapses to test recovery with limited traffic, and returns to CLOSED only after a configurable number of consecutive successful probes.
**Alternatives Considered:**
- Static error-rate thresholds applied uniformly across all providers. Rejected because providers have vastly different baseline error rates and blast radii (e.g., Credit Bureau at 5% threshold vs. SendGrid at 25%).
- Binary on/off circuit breaker without a HALF_OPEN probing state. Rejected because it would require manual intervention to re-enable traffic after an outage, increasing mean time to recovery.
- External circuit breaker service (e.g., Envoy, Istio). Rejected as over-engineered for the prototype scope, though acknowledged as the production path for service-mesh deployments.
**Consequences:** Per-provider configuration adds operational complexity (each new provider requires threshold tuning), but it enables blast-radius-aware protection. P0 providers like Stripe get tight thresholds (5% error rate, 120-second window) while P3 providers like SendGrid get looser thresholds (25% error rate, 300-second window). The HALF_OPEN state enables automatic recovery without human intervention. Circuit breaker state is tracked in `circuit_breaker_state` and `circuit_breaker_history` tables for post-incident analysis.

---

## ADR-002: Rolling Baseline Anomaly Detection over Static Thresholds

**Status:** Accepted
**Date:** 2024-03
**Context:** Static alerting thresholds (e.g., "alert if error rate exceeds 5%") caused two problems across client engagements: (1) providers with normally high baselines triggered constant false alerts, and (2) providers with normally very low error rates could degrade significantly without tripping a fixed threshold. The incident detector needed to adapt to each provider's normal behavior.
**Decision:** Implement anomaly detection using rolling baselines computed over a configurable window (default 24 hours). Alert thresholds are expressed as multipliers of the baseline (e.g., "alert when current value exceeds 2x the baseline"). Anomalies must be sustained for a configurable number of minutes before creating an incident, filtered by a minimum sample size to avoid alerting on insufficient data. Detection rules are auto-generated from blast radius category: P0/P1 providers get tighter thresholds (2x multiplier, 3-5 minute sustained window) and P2/P3 providers get looser thresholds (3x multiplier, 10-15 minute sustained window).
**Alternatives Considered:**
- Static threshold alerting (e.g., absolute error rate or latency values). Rejected because it produces either too many false positives (for normally-noisy providers) or too many missed incidents (for normally-clean providers).
- Standard deviation-based anomaly detection (z-score). Considered but rejected for the initial implementation because it requires a larger sample size to produce reliable statistics and is harder for operators to reason about when tuning.
- Machine learning-based anomaly detection. Rejected as premature for the prototype. The rolling-baseline approach covers the primary use case (sustained degradation) with simpler, more debuggable logic.
**Consequences:** The system adapts to each provider's normal performance profile, significantly reducing alert fatigue. The sustained-minutes requirement filters out transient blips. The trade-off is that truly sudden failures (e.g., complete outage) still take `sustained_minutes` before triggering an incident. This is mitigated by the `COMPLETE_OUTAGE` anomaly type and the circuit breaker, which reacts immediately to individual failures. Baselines are stored in `provider_baselines` and anomaly readings in `detected_anomalies` for trend analysis.

---

## ADR-003: Expected-vs-Actual Volume Approach for Webhook Monitoring

**Status:** Accepted
**Date:** 2024-04
**Context:** Webhook failures are fundamentally different from synchronous API failures. When a synchronous API call fails, the caller gets an error response immediately. When a webhook delivery fails, the data simply never arrives -- there is no error signal on the receiving side. This was the root cause of the Plaid incident where webhook delivery dropped to 84% for two weeks without detection. The system needed a way to detect the absence of expected events.
**Decision:** Monitor webhook reliability by comparing expected volume against actual received volume. Each provider's `WebhookConfig` in the integration registry specifies `expected_volume_per_hour`. The webhook monitor continuously compares actual received events (excluding duplicates and signature-validation failures) against the expected baseline. Delivery gaps are detected by dividing lookback windows into intervals and flagging consecutive intervals where received volume falls below 70% of expected. A dead letter queue captures events that exhaust all retry attempts for manual reprocessing.
**Alternatives Considered:**
- Relying on provider-side delivery logs or status pages. Rejected because providers often do not expose per-customer delivery metrics, and status pages are delayed by 15-30 minutes.
- Polling provider APIs to cross-reference expected events. Considered but adds significant API call overhead and complexity. Viable as a complementary approach but not as the primary detection mechanism.
- End-to-end synthetic webhook testing (sending test events and verifying receipt). Considered for future implementation but requires provider-side support for test events, which not all providers offer.
**Consequences:** The expected-vs-actual approach detects silent webhook failures that are invisible to traditional monitoring. The trade-off is that the `expected_volume_per_hour` baseline must be maintained and adjusted as traffic patterns change (e.g., seasonal variation, growth). Delivery gaps detected in consecutive 15-minute intervals below 70% are classified as "warning" (50-70% delivery) or "critical" (below 50% delivery). The dead letter queue provides a recovery path for events that cannot be processed, with manual resolution tracking.

---

## ADR-004: Funnel-to-Provider Correlation for Drop-off Attribution

**Status:** Accepted
**Date:** 2024-04
**Context:** Product teams at client engagements consistently misattributed onboarding drop-offs to UX problems when the actual root cause was API degradation. The lending client's onboarding completion dropped from 78% to 61%, and the product team was redesigning the identity verification screen. Analysis proved that 60%+ of drop-offs at that step correlated with KYC API p95 latency exceeding 8 seconds. The system needed to automatically attribute drop-offs to either UX friction or API health issues.
**Decision:** Map each funnel step to its API dependencies via the integration registry's `flow_dependencies`. For each step event, automatically attribute the drop-off cause using a decision tree: (1) if the step timed out, attribute to `API_TIMEOUT`; (2) if the API returned a 4xx/5xx status, attribute to `API_ERROR`; (3) if the API latency exceeded the step's `latency_tolerance_seconds`, attribute to `API_LATENCY`; (4) otherwise, attribute to `UX_FRICTION`. Bottleneck detection identifies steps where the drop-off rate exceeds 1.5x the average across all steps, with recovery estimation assuming 70% of API-correlated drops are recoverable by fixing the integration.
**Consequences:** This correlation directly prevented a costly UX redesign at the lending client and redirected effort to the actual problem (KYC provider timeout tuning and fallback activation). The trade-off is that the attribution is heuristic-based -- a user who experienced both high API latency AND a confusing UI would be attributed to API latency even if the UX was the deciding factor. The 70% recovery estimate is conservative but has proven directionally accurate across engagements. Funnel correlations are stored in `funnel_correlations` with `correlation_strength` scores and `bottleneck_analysis` records for historical tracking.

---

## ADR-005: Weighted Composite Scoring for Provider Scorecards

**Status:** Accepted
**Date:** 2024-05
**Context:** Vendor management conversations at QBRs were subjective ("we feel like reliability has been an issue"). The system needed to produce a single, defensible score that could be used in contract negotiations, migration decisions, and executive reporting. The score needed to balance multiple dimensions of provider performance.
**Decision:** Implement a weighted composite score (0-100) with six components: uptime vs. SLA (30%), incident frequency (20%), p95 latency trend (15%), webhook delivery rate (15%), support responsiveness (10%), and developer experience (10%). Scores map to grades: Excellent (90-100), Good (75-89), Concerning (60-74), Unacceptable (below 60). Each grade triggers a recommended action from "maintain relationship" to "initiate migration planning." Migration evaluation is automatically triggered when any of three criteria are met: SLA breach in the last 6 months, 3+ incidents in 90 days, or composite score below 60.
**Alternatives Considered:**
- Simple pass/fail SLA compliance. Rejected because it loses nuance -- a provider at 99.94% against a 99.95% SLA is qualitatively different from one at 98.5%.
- Unweighted average of all metrics. Rejected because uptime and reliability should carry more weight than developer experience in critical-path evaluations.
- Provider-specific custom scoring models. Rejected for consistency -- the same methodology applied to all providers enables fair side-by-side comparison.
**Consequences:** The composite score gave the lending client concrete leverage in vendor negotiations. Proving 99.71% uptime against a 99.95% SLA guarantee, combined with 4 incidents in 90 days, resulted in negotiated credits and a dedicated support escalation path. The trade-off is that the weights are opinionated and may not perfectly reflect every organization's priorities. Support responsiveness and developer experience default to 75 (neutral) when external data sources are not integrated. QBR data packets are generated via `generate_qbr_packet()` and rendered as Markdown reports by `ScorecardReportGenerator` for distribution to VP Engineering, CFO, or procurement.

---

## ADR-006: Central Integration Registry as Single Source of Truth

**Status:** Accepted
**Date:** 2024-02
**Context:** Across four client engagements, provider configurations were scattered across environment variables, config files, hardcoded values, and tribal knowledge. Engineers could not answer basic questions like "how many integrations do we have?" or "which providers have no fallback?" without reading code. When incidents occurred, there was no quick way to determine blast radius, fallback options, or the responsible contact person.
**Decision:** Build a central `IntegrationRegistry` that catalogs every third-party API provider with complete metadata: identity (ID, name, category), blast radius classification (P0 through P3), data flow pattern (sync, webhook, polling, file batch, bidirectional), authentication method, SLA definitions, endpoint configurations, webhook configurations, health check parameters, fallback configurations, and ownership contacts. The registry supports flow dependency mapping -- linking user flows (e.g., "user_onboarding") to ordered lists of provider dependencies -- enabling compound reliability calculations and blast-radius impact assessment. Every other module in the system (health tracker, webhook monitor, incident detector, funnel analyzer, scorecard) sources its configuration from the registry.
**Alternatives Considered:**
- Distributed configuration via per-provider config files. Rejected because it makes cross-cutting queries impossible (e.g., "list all critical-path providers without fallbacks").
- External configuration management (e.g., Consul, etcd). Considered viable for production but adds infrastructure dependency. The database-backed registry provides the same queryability with simpler operations.
- Spreadsheet-based provider catalog. Rejected because it cannot be programmatically consumed by monitoring modules and drifts from reality quickly.
**Consequences:** The registry became the foundation for all monitoring capabilities. The `audit_report()` method enables quarterly reviews that flag providers needing attention: missing fallbacks, overdue contract reviews, and stale health checks. Flow dependency mapping enables compound reliability calculations (e.g., five 99.9% providers in sequence yields 99.5% chain reliability). The trade-off is that every provider onboarding requires a complete registry entry, which adds upfront effort but prevents the "nobody knows what integrations we have" problem from recurring. The PostgreSQL schema mirrors the registry data model with referential integrity, indexes for common query patterns, and views for dashboard consumption.
