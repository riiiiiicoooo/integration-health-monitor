# Integration Health Monitor — Incident Runbooks

**Last Updated:** March 2026
**Severity Levels:** P0 (provider downtime not detected), P1 (SLO breach), P2 (degraded performance)

---

## Incident Runbook 1: Undetected Provider Downtime (MTTI SLO Miss)

**Likelihood:** Medium (1-2x per month — occurs when detection gap coincides with provider failure)
**Severity:** P0 (business risk — customer doesn't know provider is down, loses money silently)
**Detection Symptoms:** Customer report ("Stripe was down for 30 min and we had no idea"), or manual incident review shows detection was delayed >5 min

### Detection

**Automated triggers:**
- MTTI alert: `time(incident_created) - time(provider_actually_failed) > 120 seconds` for P0 provider
- Customer notification: "I didn't get an alert but Stripe was actually down"
- Provider status page vs. IHM detection: Provider status page shows outage started at 10:05 AM, but IHM incident created at 10:07 AM (2 min delay acceptable, but edge case)

**Manual triggers:**
- During incident postmortem: "When did we detect the failure?" vs. "When did Stripe actually go down?" (clock skew investigation)

### Diagnosis (First 30 minutes)

1. **Confirm detection was missed:**
   ```sql
   SELECT * FROM provider_events
   WHERE provider_id = 'STRIPE'
   AND status IN ('ERROR', 'TIMEOUT')
   AND created_at > NOW() - INTERVAL '24 hours'
   ORDER BY created_at DESC LIMIT 20;

   SELECT * FROM incidents
   WHERE provider_id = 'STRIPE'
   AND created_at > NOW() - INTERVAL '24 hours'
   ORDER BY created_at DESC LIMIT 5;
   ```
   Compare first error timestamp vs. incident creation timestamp. If gap > target MTTI, detection was delayed.

2. **Identify why detection was delayed:**
   - **Sampling lag:** Were we checking Stripe at the moment it failed? (Check health check schedule)
   - **Anomaly detection lag:** Did anomaly detector need multiple data points before deciding? (Check baseline vs. actual)
   - **Database lag:** Did incident creation queue back up? (Check database write latency during incident window)
   - **Rule threshold too high:** Did error rate threshold require too many errors before triggering? (Check rule configuration)

3. **Scope the customer impact:**
   - Which customers experienced undetected downtime?
   - How long was it undetected? (Gap between actual failure and our incident creation)
   - What transactions failed? (Check logs for failed Stripe API calls during gap)

4. **Check provider status page:**
   - Did provider announce the failure? (Check provider status page / Twitter)
   - When did they announce it vs. when did we detect it?
   - Example: Stripe announced downtime at 10:05 AM via status page; we detected at 10:07 AM (2 min lag, acceptable)

### Remediation (First 2 hours)

**Immediate:**
1. **Document the incident:**
   - Create postmortem: Why was detection delayed?
   - Was threshold too conservative? (E.g., required 5 consecutive errors before alerting)
   - Was sampling interval too infrequent? (E.g., checking every 5 min instead of every 2 min)

2. **Adjust thresholds if needed:**
   - If detection rule required "3 consecutive errors": Lower to "2 consecutive errors"
   - If sampling interval was 5 min: Lower to 2 min
   - If error threshold was 10% over 2 min: Lower to 5% over 1 min

3. **Customer notification:**
   - If undetected window was >5 min: Notify customers that Stripe was down and we didn't detect it
   - Provide transaction impact analysis: "5 payments failed during this window"

**Within 24 hours:**
1. **Detailed RCA:**
   - Review health check logs: When was Stripe last checked? Success or error?
   - Review anomaly detector: What signal triggered the incident eventually?
   - Review baseline: Was baseline stale or outdated?

2. **Preventive action:**
   - Lower detection threshold for this provider
   - Increase sampling frequency for P0 providers (every 30 sec instead of every 2 min)
   - Add redundant detection: If health check misses outage, webhook detection should catch it (wait for webhook from Stripe, if timeout occurs, escalate)

### Communication Template

**Internal (Slack #incident-ops):**
```
🚨 P0: Undetected Provider Downtime — [PROVIDER]

Timeline:
- [TIME 1]: Provider failed (first error in logs)
- [TIME 2]: We detected it (incident created)
- Gap: [N] minutes

Detection gap: Caused by [REASON: high threshold / infrequent sampling / baseline lag]

Impact:
- Customers without alerts: [N]
- Failed transactions: [N]
- Revenue at risk: $[ESTIMATE]

Actions:
- [x] Detection threshold adjusted
- [ ] Sampling frequency increased
- [ ] Customer notification sent
- [ ] Redundant detection rule added

RCA: [DUE IN 24H]
On-call: [NAME]
```

---

## Incident Runbook 2: Webhook Delivery Gap Not Detected (Silent Webhook Failure)

**Likelihood:** Low (goal is 100% webhook monitoring; historically 1-2x per year)
**Severity:** P0 (webhook data loss without detection)
**Detection Symptoms:** Customer reports "We didn't get any Stripe webhooks for 2 hours and IHM didn't alert"

### Detection

**Automated triggers:**
- Expected-vs-actual webhook monitor finds gap: Expected 500 webhooks/hour from Plaid; received 0 for 1-hour window
- Webhook delivery anomaly: Sudden drop in webhook volume (from 500/hour to 0) not detected by monitor

**Manual triggers:**
- Customer: "Our reconciliation job failed because no webhooks came in"
- Compliance: "Show me the audit trail for webhook delivery"

### Diagnosis (First 30 minutes)

1. **Confirm webhook gap:**
   ```sql
   SELECT
     DATE_TRUNC('hour', received_at) as hour,
     COUNT(*) as webhook_count
   FROM webhooks
   WHERE provider_id = 'PLAID'
   AND received_at > NOW() - INTERVAL '7 days'
   GROUP BY DATE_TRUNC('hour', received_at)
   ORDER BY hour DESC;
   ```
   Look for hours with 0 webhooks when we should expect 500+.

2. **Check if provider sent webhooks (to our endpoint vs. somewhere else):**
   - Did Plaid's webhook delivery logs show successful sends to our endpoint?
   - Example: Check Plaid dashboard → Webhook Activity → Delivery Status
   - If Plaid says "delivered successfully" but we didn't receive: Network/DNS issue on our side
   - If Plaid says "delivery failed": Provider-side issue (they had no webhooks to send, or their delivery system broke)

3. **Determine root cause:**
   - **Our webhook endpoint down:** IHM webhook receiver was down; check API health logs
   - **Provider webhook stopped:** Plaid had no events to send (not a failure, just no activity)
   - **DNS/network issue:** Plaid tried to send but couldn't reach our endpoint (timeout / DNS error)
   - **Our webhook monitor broken:** We received webhooks fine, but monitor didn't detect them (detection rule bug)

4. **Check monitoring status:**
   ```sql
   SELECT * FROM webhook_monitoring_status
   WHERE provider_id = 'PLAID'
   AND status = 'ACTIVE'
   LIMIT 1;
   ```
   Verify the monitor was enabled. (Risk: Monitor was disabled during maintenance and not re-enabled.)

### Remediation (First 2 hours)

**Immediate:**
1. **Restore webhook monitoring:**
   - If monitor is disabled: Re-enable and verify baseline is correct
   - If baseline is stale: Recalculate based on recent data

2. **Reprocess lost webhooks:**
   - If provider has event replay API: Request Plaid to resend webhooks from gap period
   - Example: `curl -X POST https://api.plaid.com/webhook/replay -d '{"start_time": "2026-03-16T10:00Z", "end_time": "2026-03-16T12:00Z"}'`
   - Or: Check provider's dead letter queue or webhook archive

3. **Determine customer impact:**
   - Customers relying on webhooks during gap period: How many? Which transactions failed?

**Within 24 hours:**
1. **RCA:**
   - Why wasn't webhook gap detected?
   - Was monitoring baseline correctly set for this provider?
   - Was detection threshold too lenient?

2. **Fix:**
   - Tighten detection threshold: Require only 50% delivery gap (not 75%) to alert
   - Add secondary detection: If zero webhooks for >30 min, alert immediately (don't wait for baseline comparison)
   - Implement webhook signature validation: Verify every webhook is signed by provider; unsigned webhooks are flagged (catches impersonation attacks)

3. **Redundant alerting:**
   - Set up paging if provider.webhook_delivery_rate < 80% (in addition to absolute 0% detection)

### Communication Template

**Internal (Slack #incident-ops):**
```
🚨 P0: Webhook Delivery Gap Not Detected — [PROVIDER]

Timeline:
- [TIME 1]: Webhook delivery stopped
- [TIME 2]: We detected it (or didn't detect, customer reported)
- Gap duration: [N] hours

Detection status: [Monitoring was disabled / Baseline was stale / Detection threshold too high]

Impact:
- Webhooks lost: ~[N]
- Customers affected: [LIST]
- Transactions not synced: [ESTIMATE]

Actions:
- [x] Monitoring re-enabled and verified
- [ ] Lost webhooks replayed (if provider supports it)
- [ ] Detection threshold tightened
- [ ] Redundant monitoring rule added

Customer notification: [REQUIRED if >1000 webhooks lost]
RCA: [DUE IN 24H]
```

---

## Incident Runbook 3: False Positive Incident Spam (Alert Fatigue)

**Likelihood:** Medium (1-3x per month — occurs when detection rules are too sensitive)
**Severity:** P2 (not customer-facing, but internal alert fatigue destroys credibility)
**Detection Symptoms:** Team complains "We're getting alerts every 5 minutes for the same provider"; or MTTI metric shows lots of very short incidents (< 1 min duration)

### Detection

**Automated triggers:**
- Alert: `INCIDENT_FREQUENCY_BY_PROVIDER` shows provider X has 50+ incidents in 1 day (should be 1-2)
- Dashboard: Incident list shows many consecutive incidents for same provider with recovery intervals <2 min

**Manual triggers:**
- Team says "IHM is spammy; I'm muting alerts"
- Executive sees 500+ incidents in monthly report for 50 providers (average 10 per provider is normal, 50+ is spam)

### Diagnosis (First 30 minutes)

1. **Identify spam pattern:**
   ```sql
   SELECT provider_id, DATE(created_at) as incident_date, COUNT(*) as incident_count
   FROM incidents
   WHERE created_at > NOW() - INTERVAL '24 hours'
   GROUP BY provider_id, DATE(created_at)
   ORDER BY incident_count DESC LIMIT 10;
   ```
   Identify providers with >10 incidents in 24 hours.

2. **Analyze incident lifecycle:**
   ```sql
   SELECT id, created_at, resolved_at, (resolved_at - created_at) as duration
   FROM incidents
   WHERE provider_id = 'SPAMMY_PROVIDER'
   AND created_at > NOW() - INTERVAL '24 hours'
   ORDER BY created_at DESC LIMIT 20;
   ```
   Look for incidents that resolve very quickly (<1 min), then recreate. This is the spam pattern.

3. **Identify root cause:**
   - **Oscillating provider:** Provider goes up/down/up/down rapidly (network flakiness)
   - **Detection threshold too low:** Rule triggers on small variance (e.g., error rate 5.1% when threshold is 5%)
   - **Baseline too stale:** Baseline was calculated during outage; now any small variance triggers
   - **Time-of-day pattern:** Provider has legitimate traffic spike at certain hours (peak load) that looks like error

4. **Validate baseline:**
   ```sql
   SELECT * FROM provider_baselines
   WHERE provider_id = 'SPAMMY_PROVIDER'
   ORDER BY created_at DESC LIMIT 1;
   ```
   Check: Is baseline_error_rate way off from recent data? (E.g., baseline says 0.5% but recent data shows 2%)

### Remediation (First 2 hours)

**Immediate:**
1. **Suppress spam:**
   - Lower rule sensitivity: If error rate threshold is 5%, raise to 10%
   - Increase sustained window: Require error rate to be high for >5 min, not >1 min
   - Add dampening: Suppress duplicate incidents for same provider if <30 min apart

2. **Recalculate baseline:**
   - If baseline is stale (>7 days old): Recalculate from last 24 hours of clean data
   - ```sql
     UPDATE provider_baselines
     SET baseline_error_rate = (SELECT AVG(error_rate) FROM provider_events WHERE provider_id = 'X' AND created_at > NOW() - INTERVAL '24 hours')
     WHERE provider_id = 'SPAMMY_PROVIDER';
     ```

3. **Analyze provider behavior:**
   - Is provider legitimately flaky? (Many brief outages) → Communicate this to customers
   - Is provider having normal traffic variance? → Adjust thresholds for this provider (make looser)

**Within 24 hours:**
1. **Review all detection rules:**
   - For each provider with >5 incidents/day: Review rule and determine if threshold is appropriate
   - Consider adding provider-specific thresholds (different rules for SendGrid vs. Plaid, since they have different failure modes)

2. **Implement adaptive thresholding:**
   - Instead of static threshold, calculate threshold as `baseline * 2.0` (alert when 2x normal, not when >fixed %`)
   - Allows natural variation while catching true degradations

### Communication Template

**Internal (Slack #incident-ops):**
```
⚠️ P2: Alert Fatigue — [PROVIDER] Generating 50+ Incidents/Day

Root cause:
- [Oscillating provider / Detection threshold too low / Stale baseline / Time-of-day pattern]

Actions taken:
- [x] Spam suppressed (threshold raised / dampening enabled)
- [x] Baseline recalculated
- [ ] Provider-specific rules reviewed

Team note: These are mostly false positives. Suppression should reduce alert noise.

Follow-up: Review if [PROVIDER] is actually reliable for customer workflows.
```

