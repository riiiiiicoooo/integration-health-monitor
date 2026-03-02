# Incident Response Playbook: Integration Failures

**Last Updated:** February 2025

---

## 1. Why Integration Incidents Are Different

When your own code breaks, you can read the logs, find the bug, and deploy a fix. When a third-party integration breaks, you're dependent on another company's engineering team to fix it, and you have no visibility into their timeline. Your job shifts from "fix the problem" to "detect it fast, route around it, and minimize customer impact while someone else fixes it."

This playbook covers how we structured incident response for integration failures across multiple client engagements where the teams were small (2-10 engineers), resources were limited, and the priority was always "stop the bleeding first, investigate later."

---

## 2. Detection

### 2.1 How Integration Failures Surface

Integration failures arrive through five channels, listed from fastest to slowest:

| Channel | Detection Speed | Example |
|---|---|---|
| **Automated monitoring** | Seconds to minutes | Circuit breaker trips, error rate threshold exceeded, webhook delivery rate drops |
| **Application errors** | Minutes | Unhandled exceptions, timeout errors in application logs |
| **Customer support tickets** | 30 min to hours | "I can't verify my identity," "My order confirmation never arrived" |
| **Internal team reports** | Hours to days | "The inventory numbers look off," "Enrollment file didn't come through" |
| **End-of-period discovery** | Days to weeks | Month-end reconciliation reveals missing transactions, audit finds gaps |

The entire point of the Integration Health Monitor is to shift detection from channels 3-5 (reactive, slow, expensive) to channel 1 (proactive, fast, automated).

### 2.2 Automated Detection Thresholds

The incident_detector module monitors three primary signals per provider:

**Error Rate Spike**
- Baseline: Rolling 24-hour average error rate per provider
- Threshold: Error rate exceeds 2x baseline for 3+ consecutive minutes
- Why 2x for 3 minutes: Avoids alerting on single retried requests or momentary blips. Three minutes of sustained elevated errors is almost always a real incident, not noise.

**Latency Degradation**
- Baseline: Rolling 24-hour p95 latency per provider
- Threshold: p95 latency exceeds 2x baseline for 5+ consecutive minutes
- Why 5 minutes: Latency spikes are more common than error spikes (provider under load but still responding). A longer window reduces false positives while still catching meaningful degradation before users start dropping off.

**Webhook Delivery Drop**
- Baseline: Expected webhook volume per hour (derived from historical patterns)
- Threshold: Delivered webhooks drop below 70% of expected volume for 15+ minutes
- Why 70% for 15 minutes: Webhook delivery is inherently bursty. A brief dip to 80% is normal. Sustained delivery below 70% means something is wrong — either the provider stopped sending, or our receiver is rejecting.

### 2.3 Detection Priorities by Blast Radius

Not all alerts are equal. The incident_detector routes alerts based on the provider's blast radius category (defined in INTEGRATION_ARCHITECTURE.md):

| Severity | Alert Channel | Response Expectation |
|---|---|---|
| P0 — Revenue Blocking | PagerDuty (page on-call), Slack #incidents | Acknowledge within 5 minutes |
| P1 — Onboarding Blocking | Slack #incidents, email to engineering lead | Acknowledge within 15 minutes |
| P2 — Feature Degraded | Slack #monitoring | Review within 2 hours |
| P3 — Back-Office Impact | Daily digest email | Review next business day |

---

## 3. Triage

### 3.1 First 5 Minutes: Is It Us or Them?

The most common waste of time in integration incidents is engineers debugging their own code when the problem is the third-party provider, or vice versa. The health dashboard answers this in under 2 minutes:

**Step 1: Check the provider health card on the dashboard.**
- Red = our monitoring has detected elevated errors or latency. Likely a provider issue.
- Yellow = degraded but still functional. Could be either side.
- Green = provider is healthy from our perspective. Likely our code or infrastructure.

**Step 2: Check the provider's status page.**
- If their status page shows an incident, confirmed provider issue. Skip to Section 4 (Mitigation).
- If their status page shows "all operational" but our monitoring shows red, it's still likely a provider issue — status pages lag by 15-30 minutes on average.

**Step 3: Check recent deployments.**
- Did we deploy anything in the last 2 hours that touches this integration? If yes, likely our code. Roll back first, investigate second.

**Step 4: Check other providers in the same category.**
- If multiple providers are degraded simultaneously, it's likely our infrastructure (network, DNS, load balancer), not the providers.

### 3.2 Triage Decision Tree

```
Integration alert fires
    │
    ├── Multiple providers affected simultaneously?
    │       ├── YES → Check our infrastructure (network, DNS, load balancer, egress)
    │       └── NO → Single provider issue, continue below
    │
    ├── Did we deploy in the last 2 hours?
    │       ├── YES, touching this integration → Roll back, verify fix
    │       └── NO → Likely provider-side issue
    │
    ├── Provider status page showing incident?
    │       ├── YES → Confirmed provider issue, go to Mitigation
    │       └── NO → Check our error logs for this provider's calls
    │
    ├── Our logs show timeout errors?
    │       ├── YES → Provider latency issue (even if their status page is green)
    │       └── NO → Check for auth errors, schema changes, rate limiting
    │
    └── Our logs show 401/403 errors?
            ├── YES → API key expired or rotated, check credentials
            └── NO → Escalate to engineering for deeper investigation
```

---

## 4. Mitigation

### 4.1 Mitigation Strategies by Failure Type

| Failure Type | Immediate Mitigation | Longer-Term Fix |
|---|---|---|
| **Provider fully down (5xx)** | Trip circuit breaker, route to fallback provider or graceful degradation | Evaluate secondary provider integration |
| **Provider slow (latency spike)** | Reduce timeout, trip circuit breaker if latency exceeds user tolerance | Implement request hedging, add caching layer |
| **Webhooks not delivering** | Switch to polling mode for this provider, backfill from API | Add webhook delivery monitoring with dead letter queue |
| **Rate limited (429)** | Implement request queuing with backoff, reduce non-critical calls | Negotiate higher limits, implement request batching |
| **Auth failure (401/403)** | Rotate API keys, check if provider rotated on their end | Implement key rotation automation, monitor expiration |
| **Schema change (unexpected response)** | Pin to previous API version if available, add defensive parsing | Implement response validation, monitor provider changelogs |

### 4.2 Circuit Breaker Activation

When the api_health_tracker trips a circuit breaker for a provider:

1. **All requests to that provider fail fast** (no waiting for timeouts)
2. **Fallback logic activates** (if configured for this provider)
3. **Alert fires** with the provider name, failure type, and affected user flows
4. **Half-open probe starts** — every 30 seconds, one request is sent to test if the provider has recovered
5. **Auto-recovery** — if 3 consecutive probes succeed, circuit closes and normal traffic resumes

### 4.3 Fallback Provider Patterns

Not every integration has a fallback, and not every fallback is equivalent. We documented three levels:

**Hot Fallback:** Secondary provider is always connected and ready. Traffic can be routed immediately. Used for critical-path integrations with high blast radius (payment processing, SMS delivery).

**Warm Fallback:** Secondary provider is integrated but not actively used. Requires configuration change or feature flag to activate. Adds 5-15 minutes to mitigation time. Used for important but not revenue-blocking integrations.

**Graceful Degradation:** No secondary provider. Instead, the feature degrades to a manual or delayed workflow. The user experience is worse but not broken. Used for integrations where a secondary provider isn't cost-justified (e.g., routing KYC failures to a manual review queue instead of integrating a second KYC vendor).

---

## 5. Communication

### 5.1 Internal Communication

During an active incident, the on-call engineer posts to #incidents with:

```
🔴 INTEGRATION INCIDENT
Provider: [Provider Name]
Category: [Identity / Financial / Communication / etc.]
Severity: [P0/P1/P2/P3]
Detected: [timestamp]
Impact: [which user flows are affected]
Status: [Investigating / Provider confirmed / Mitigating / Resolved]
Fallback: [Active / Not available / Not needed]
ETA: [Provider's stated ETA or "Unknown"]
```

Updates every 15 minutes for P0/P1, every hour for P2.

### 5.2 Provider Communication

When we confirm a provider-side issue, the account manager or technical contact sends:

**Initial Outreach (within 30 minutes of detection):**

```
Subject: [URGENT] Elevated error rates on [endpoint] — [Company Name] account

Hi [Provider Contact],

We're seeing elevated [error rates / latency / webhook delivery failures]
on [specific endpoint] starting at [timestamp UTC].

Our monitoring shows:
- Error rate: [X]% (baseline: [Y]%)
- P95 latency: [X]ms (baseline: [Y]ms)
- Affected volume: ~[N] requests in the last [time period]

Your status page currently shows [operational / incident reported].

Can you confirm if there's a known issue?
What's your current ETA for resolution?

This is impacting [user flow] for our [customer type].

Best,
[Name]
```

**Follow-Up (if no response in 2 hours):**
Escalate to the provider's account manager or support escalation path. Reference the SLA terms and the specific uptime guarantee from the contract. The provider_scorecard module tracks SLA compliance data that supports this escalation.

### 5.3 Customer Communication

For P0/P1 incidents affecting end users, the product or support team communicates:

- **In-app:** Banner or toast notification acknowledging the issue if the affected flow is visible to the user
- **Support team briefing:** Script for support agents handling related tickets
- **Post-resolution:** If more than 100 users were affected, send a brief status update via email acknowledging the disruption

We never blame the third-party provider by name in customer-facing communications. "We're experiencing a temporary issue with [feature]" is sufficient.

---

## 6. Post-Incident Review

### 6.1 When to Conduct a Review

- All P0 incidents
- P1 incidents lasting more than 1 hour
- Any incident that affected more than 50 users
- Any incident that exposed a monitoring gap (detected by support tickets or manual discovery, not automated monitoring)

### 6.2 Review Template

```
INTEGRATION INCIDENT REVIEW

Incident: [Title]
Date: [Date]
Duration: [Start → Resolution]
Severity: [P0/P1/P2/P3]
Provider: [Name]
Affected Flows: [List]

TIMELINE
[timestamp] — First anomaly detected by monitoring
[timestamp] — Alert fired to [channel]
[timestamp] — Engineer acknowledged
[timestamp] — Root cause identified: [provider issue / our code / infrastructure]
[timestamp] — Mitigation applied: [circuit breaker / fallback / rollback]
[timestamp] — Provider acknowledged issue
[timestamp] — Provider resolved issue
[timestamp] — Full service restored
[timestamp] — Backfill/cleanup completed

DETECTION
- How was this detected? [Automated monitoring / Support ticket / Internal report]
- How long between first failure and detection? [Duration]
- Could we have detected faster? [Yes/No, how]

IMPACT
- Users affected: [Count]
- Revenue impact: [Estimate]
- Support tickets generated: [Count]
- Engineering hours spent: [Count]

ROOT CAUSE
[Description of what actually broke and why]

MITIGATION EFFECTIVENESS
- Did the fallback work? [Yes/No/Partially]
- Was the circuit breaker configured correctly? [Yes/No]
- What would have happened without monitoring? [Estimated detection time]

ACTION ITEMS
1. [Action] — Owner: [Name] — Due: [Date]
2. [Action] — Owner: [Name] — Due: [Date]
```

### 6.3 Common Action Items

These are the action items that came up most frequently across client engagements:

| Finding | Action |
|---|---|
| No fallback for a P0 dependency | Integrate secondary provider, implement hot or warm failover |
| Webhook failures not monitored | Add webhook delivery tracking to the webhook_monitor module |
| Circuit breaker thresholds too aggressive | Tune thresholds based on provider's normal variance |
| Circuit breaker thresholds too lenient | Reduce error rate threshold or time window |
| Provider SLA breach not documented | Add data point to provider_scorecard for QBR |
| Alert fatigue (too many P3 alerts) | Move P3 providers to daily digest, out of real-time channel |
| Detected by support tickets, not monitoring | Add missing monitoring coverage for the affected endpoint |
| No runbook for this provider's failure | Create provider-specific mitigation steps |

---

## 7. Incident Metrics We Track

| Metric | Definition | Target |
|---|---|---|
| **MTTD (Mean Time to Detect)** | Time between first failure event and alert firing | < 5 min for P0, < 15 min for P1 |
| **MTTA (Mean Time to Acknowledge)** | Time between alert and human response | < 5 min for P0, < 15 min for P1 |
| **MTTM (Mean Time to Mitigate)** | Time between acknowledgment and customer impact reduced | < 15 min for P0, < 30 min for P1 |
| **MTTR (Mean Time to Resolve)** | Time between first failure and full resolution | Depends on provider |
| **Detection source ratio** | % of incidents detected by monitoring vs. support tickets | Target: > 90% automated |
| **False positive rate** | % of alerts that were not real incidents | Target: < 10% |
| **Incidents per provider per quarter** | Trend tracking per provider | Inform vendor evaluation |

---

## 8. Lessons Learned

1. **The first mitigation is always "stop making it worse."** Trip the circuit breaker. Stop sending traffic. Then figure out what happened. Every client's instinct was to keep retrying, which just made latency worse and burned rate limits.

2. **Write runbooks before you need them.** At 2 AM during a P0 incident is not the time to figure out the provider's escalation path or your fallback activation steps. We pre-wrote provider-specific runbooks for every P0/P1 integration.

3. **Provider status pages are a trailing indicator.** In 6 out of 8 major incidents across clients, our monitoring detected the issue 15-45 minutes before the provider's status page updated. Never wait for the provider to confirm — trust your own data.

4. **Customer support is your fastest escalation path with providers.** When the standard support channel is slow, filing a ticket through the provider's enterprise support or contacting your account manager directly gets faster results than waiting in the general queue.

5. **Track incidents per provider over time.** A provider with one major incident is having a bad day. A provider with 4 incidents in 90 days has a reliability problem. The provider_scorecard module turns this data into leverage for QBRs and contract negotiations.
