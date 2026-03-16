# Integration Health Monitor — Service Level Objectives (SLOs)

**Last Updated:** March 2026
**Compliance Scope:** Incident detection accuracy, webhook delivery reliability, provider availability assurance

---

## Error Budget Policy

Integration Health Monitor operates on a **detection accuracy budget** (not availability budget). Unlike traditional systems, IHM's primary function is to detect when third-party integrations fail. An outage of IHM itself can go unnoticed for hours if monitoring is blind; therefore, we prioritize detection accuracy and coverage over API response time.

**SLO Categories:**
- **Detection accuracy SLOs:** False positive rate, false negative rate, MTTI (Mean Time To Incident) — no error budget
- **Operational SLOs:** API availability, dashboard latency — monthly error budgets with burn rate alerts

---

## SLO 1: Incident Detection Accuracy (False Negative Rate)

**Service:** `incident-detector` (anomaly detection + circuit breaker)
**Definition:** Percentage of actual provider degradations (confirmed by manual investigation or customer report) that are detected automatically within the MTTI SLO window (< 2 min).

**Target:** 98.0% (2 out of 100 real incidents missed is acceptable)

**Error Budget:** None (accuracy is non-negotiable, but 2% miss rate is acceptable tolerance)

**Measurement:**
- Query: `(INCIDENTS_DETECTED_WITHIN_2MIN) / TOTAL_REAL_INCIDENTS`
- Sampling: 100% of incidents; monthly audit against manual investigation logs
- Ground truth: Customer reports, provider status pages, payment processor logs
- Source: `detected_anomalies`, `incident_reports`

**Why This Target:**
- **Operational reality:** At the baseline (50 active integrations), 3-5 real degradations occur per month. Missing 1-2 of those (2% miss rate) means 1-2 hours per month of blind time, which is defensible
- **Customer impact:** A missed incident = customers losing money (failed transactions, revenue loss, ops cost) without realizing it's provider-side. At 2% miss rate, customers rarely hit the undetected window
- **Detection limits:** Some provider failures (gradual, localized) are indistinguishable from normal variance; 98% captures all binary (outage/recovery) incidents plus most degradations
- **Practical tradeoff:** Aiming for 99% would require so much alerting infrastructure that false positives would exceed true incidents; 98% is the sweet spot

**Burn Rate Triggers (actually, escalation rules):**
- **3+ real incidents missed in 30 days:** Alert on-call; review detection rules for blind spots
- **Pattern of missed incidents:** (e.g., all missed incidents are for provider X) — update detection thresholds for that provider

**Mitigations:**
- Dual detection: Circuit breaker (reacts to immediate failures) + rolling baseline anomaly detection (detects sustained degradation)
- Multi-signal detection: Error rate + latency + webhook delivery rate; requires all to breach threshold
- Manual override: Customers can create incidents manually (for cases we miss)

---

## SLO 2: Mean Time To Incident Detection (MTTI)

**Service:** `incident-detector`
**Definition:** Time elapsed between when a provider actually degrades (first error/latency spike) and when IHM creates an incident record. Target: < 2 minutes for P0 providers (Stripe, Plaid), < 5 minutes for P1/P2 providers.

**Target:**
- P0 providers (critical path): 98% of incidents detected within 2 min
- P1 providers (high impact): 95% of incidents detected within 5 min
- P2 providers (medium impact): 90% of incidents detected within 10 min

**Error Budget:** None (MTTI is core to product value)

**Measurement:**
- Query: `(INCIDENTS_DETECTED_WITHIN_TARGET_MTTI) / TOTAL_INCIDENTS`
- Sampling: 100% of detected incidents
- Source: `provider_events` (first error timestamp) vs. `incidents` (detection timestamp)

**Why These Targets:**
- **Business impact:** A 2-minute MTTI for Stripe means product team learns about payment failures 2 min after they start; enough time to page on-call before customer tweets about it
- **Provider failure patterns:** Most provider outages go from "normal" → "complete outage" in 1-3 minutes; 2 min MTTI catches this
- **Detection latency breakdown:**
  - First error happens at T+0
  - Takes ~30 seconds for anomaly detector to accumulate enough data points
  - Takes ~20 seconds to evaluate rules and determine if anomaly is sustained
  - Takes ~10 seconds to create incident, call webhooks, update dashboards
  - Total: ~60 seconds baseline; 2 min target allows 60 second buffer for network latency/queuing

**Burn Rate Triggers:**
- **MTTI > 3 min for P0:** Action: Increase sampling rate (check provider health more frequently)
- **MTTI > 5 min for P1:** Action: Investigate query performance (anomaly detection queries running slow)
- **MTTI trending upward:** Action: May indicate database bloat or computational overhead

**Mitigations:**
- High-frequency sampling: Check provider health every 30 seconds (P0 providers) vs. every 2 min (P2 providers)
- Pre-computed baselines: Calculate rolling baselines offline, cache in Redis; detection queries just compare against cache
- Real-time streaming: Use Kafka for event ingestion instead of batch; enables sub-second latency for high-frequency providers

---

## SLO 3: Webhook Delivery Monitoring Completeness

**Service:** `webhook-monitor`
**Definition:** Percentage of monitored webhooks where we have a continuous expected-vs-actual volume baseline for detecting delivery gaps. Target: 100% of monitored webhook providers (Plaid, Stripe, SendGrid, etc.) have active monitoring.

**Target:** 100.0% (zero blind spots)

**Error Budget:** None (zero webhook providers should be unmonitored)

**Measurement:**
- Query: `(WEBHOOK_PROVIDERS_WITH_ACTIVE_MONITORING) / TOTAL_WEBHOOK_PROVIDERS`
- Sampling: Manual audit every week; any new provider should have monitoring baseline within 48 hours of first webhook received
- Source: `webhook_configs`, `webhook_monitoring_status`

**Why This Target:**
- **Historical incident:** Plaid webhook delivery dropped to 84% for two weeks without detection (root cause: Plaid upgraded their backend, broke our webhook validation); zero monitoring meant zero visibility into the problem
- **Silent failures:** Webhook failures are fundamentally different from API failures; API calls fail loudly (error response); webhooks fail silently (just never arrive)
- **Detection method:** Expected volume baseline (e.g., "Plaid should deliver ~500 webhooks/hour") is the only way to detect silent failures
- **100% target:** Every webhook provider we integrate with should be monitored; no exceptions

**Burn Rate Triggers:**
- **1+ webhook provider without active monitoring:** Manual escalation; create monitoring baseline within 24 hours
- **Monitoring baseline missing for >1 week:** Likely forgotten during integration; escalate to platform team

**Mitigations:**
- Mandatory checklist: Every new webhook integration requires 1 week of baseline collection before going live
- Automated alerts: Flag providers that haven't sent webhooks in 1 hour (expected to send frequently)
- Dead letter queue: Capture webhooks that fail validation; manual review to catch unknown patterns

---

## SLO 4: Provider Scorecard Accuracy (Weighted Composite Score)

**Service:** `scorecard-generator`
**Definition:** Percentage of provider scorecards where the weighted composite score (0-100) accurately reflects provider reliability as validated by manual vendor management reviews.

**Target:** 95.0% (scorecard ranking matches manual QBR assessment 95% of the time)

**Error Budget:** Monthly allowance of ~20 scorecard misclassifications (out of 50 active providers)

**Measurement:**
- Query: Manual audit of scorecards vs. vendor management notes; monthly comparison
- Validation: If scorecard says "Excellent (90-100)" but QBR notes say "provider had 3 incidents", scorecard accuracy failed
- Source: `provider_scorecards` vs. `qbr_notes`

**Why This Target:**
- **Business use:** Scorecards drive contract negotiations and migration decisions; if 20% of scores are wrong, business decisions are wrong
- **Weighting confidence:** Current weighting (30% uptime, 20% incident frequency, etc.) is based on customer feedback; 95% accuracy means scoring model is correct for 95% of providers
- **95% is reasonable:** Some providers have edge cases (e.g., provider improved during month, but scorecard lags); 95% is defensible

**Burn Rate Triggers:**
- **Scorecard grade mismatch > 5 providers in month:** Review weighting; might need to adjust multipliers
- **Same provider misclassified 2 months in a row:** Escalate for manual investigation

**Mitigations:**
- Weekly score review: Spot-check 5-10 providers each week; verify calculation
- Tuning: Monthly review of weights based on QBR feedback; adjust if weighting is drifting

---

## SLO 5: API Availability (Dashboard, Scorecard Generation)

**Service:** `api.integrationhealthmonitor.com`
**Definition:** Percentage of 1-minute windows where ≥99% of API requests (incident list, scorecard retrieval, funnel analysis) receive successful response within 30 seconds.

**Target:** 99.0% (7.2 hours downtime/month)

**Error Budget:** 7.2 hours/month

**Measurement:**
- Query: `(MINUTES_WITH_99PCT_SUCCESS) / TOTAL_MINUTES`
- Sampling: 1-minute windows; reported as rolling 24h average
- Source: API request latency metrics (Prometheus)

**Why This Target:**
- **Operational use:** Product teams query IHM dashboard during incident response; API should be available when needed
- **99% target:** Allows for 1-2 brief outages per month; acceptable for non-critical dashboard service (not patient-facing, not payment-critical)
- **Comparison:** More lenient than Clinical AI (99.5%) because IHM is advisory (doesn't block transactions); more strict than typical SaaS (99.5-99.9%)

**Burn Rate Triggers:**
- **Error rate > 2% (burn rate > 10x):** Page on-call; database or API server likely down
- **Error rate > 1%:** High burn; investigate
- **Latency p95 > 60s:** Watch alert; potential cascading query failure

**Mitigations:**
- Read replicas: All dashboard queries go to read replicas, not primary
- Query caching: Scorecards cached for 1 hour; incidents cached for 5 minutes
- Circuit breaker: If query takes >10s, return cached result or generic response

---

## SLO 6: Incident Reporting Latency (Alerts to Customers)

**Service:** `incident-reporter` (Slack, email, webhook)
**Definition:** Time from incident creation to customer notification. Target: < 2 min for P0 incidents, < 5 min for P1 incidents.

**Target:**
- P0 incidents: 95% reported within 2 min
- P1 incidents: 90% reported within 5 min
- P2 incidents: 85% reported within 15 min

**Error Budget:** None (reporting latency is non-negotiable for customer trust)

**Measurement:**
- Query: `(INCIDENTS_REPORTED_WITHIN_TARGET) / TOTAL_INCIDENTS`
- Source: `incident` (created_at) vs. `incident_notifications` (sent_at)

**Why These Targets:**
- **Customer expectation:** When Stripe goes down, customers expect to know within 2 min (before they see payment failures)
- **Practical latency breakdown:**
  - Incident detection: ~1-2 min (from SLO 2)
  - Notification batching: ~20 seconds (group related incidents)
  - Slack/email delivery: ~10-20 seconds
  - Total: ~2-3 min baseline for P0; 5 min target has buffer

**Burn Rate Triggers:**
- **P0 reporting latency > 3 min:** Action: Check Slack/email delivery (may be queued)
- **Multiple P0 incidents with >2 min reporting:** Action: Investigate notification service (may be rate-limited)

**Mitigations:**
- Direct push to Slack: Bypass email batching for P0 incidents
- Webhook delivery: Customers register webhook URLs for instant notification
- Status page: Publish incidents publicly immediately (don't wait for email delivery)

---

## Error Budget Consumption Practices

1. **Weekly review:** Review all detection metrics (SLO 1, 2, 3); non-negotiable meetings to discuss miss patterns
2. **Monthly executive review:** Report to VP Engineering on MTTI trends and false positive rates
3. **Continuous monitoring:** Detection accuracy dashboards should be visible to entire product team
4. **Incident learning:** Every missed incident triggers 1-hour post-mortem to identify blind spot

