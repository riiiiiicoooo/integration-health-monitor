# Provider Evaluation Framework

**Last Updated:** February 2025

---

## 1. Why This Framework Exists

Choosing a third-party API provider is one of the highest-leverage decisions a product team makes. A good choice disappears into the background — it just works. A bad choice creates a permanent tax: engineering time spent on workarounds, customer experience degraded by latency or failures, vendor management overhead that never goes away.

Most companies evaluate providers by reading docs, running a quick proof of concept, and picking the one that "feels right." This works at 3 integrations. At 8+, the cumulative impact of poorly evaluated providers becomes the dominant source of operational pain.

This framework was developed across four client engagements to standardize how we evaluate, select, and manage API providers. It covers initial selection, proof-of-concept methodology, contract negotiation leverage, and ongoing performance management.

---

## 2. Evaluation Criteria

### 2.1 Scoring Matrix

Every provider is scored across six dimensions. Each dimension is weighted based on the client's priorities and the integration's blast radius.

| Dimension | Weight (Critical Path) | Weight (Non-Critical) | What We Measure |
|---|---|---|---|
| **Reliability** | 30% | 20% | Historical uptime, incident frequency, MTTR, status page transparency |
| **Performance** | 25% | 15% | Latency (p50/p95/p99), throughput limits, geographic distribution |
| **Developer Experience** | 15% | 25% | Documentation quality, SDK availability, sandbox environment, error messages |
| **Operational Maturity** | 15% | 10% | Webhook support, retry policies, rate limit transparency, API versioning |
| **Commercial Terms** | 10% | 20% | Pricing model, volume discounts, SLA guarantees, contract flexibility |
| **Strategic Fit** | 5% | 10% | Roadmap alignment, market position, acquisition risk, geographic coverage |

### 2.2 Reliability Deep Dive

Reliability is weighted highest for critical-path integrations because a provider that's fast and cheap but goes down twice a month is more expensive than a slower, pricier provider with 99.99% uptime.

**What we actually measure:**

| Metric | How We Get It | Red Flag |
|---|---|---|
| Published uptime SLA | Provider's terms of service | No SLA published, or SLA below 99.9% |
| Actual uptime (our measurement) | provider_scorecard module data | Actual uptime more than 0.1% below published SLA |
| Incident count (last 90 days) | Provider's status page history + our incident data | 3+ incidents in 90 days |
| Mean time to resolve | Status page incident durations | MTTR > 2 hours average |
| Status page transparency | Manual review | Incidents that we detected but never appeared on status page |
| Webhook delivery rate | webhook_monitor data | Delivery rate below 99% |
| Degraded performance events | api_health_tracker data | Latency spikes > 2x baseline more than once per week |

### 2.3 Performance Deep Dive

Performance matters differently depending on where the provider sits in the user flow. A payment processor with 200ms latency is fine. An identity verification API with 8-second p95 latency kills onboarding conversion.

**What we actually measure:**

| Metric | Method | Why It Matters |
|---|---|---|
| p50 latency | Load test during POC | Typical user experience |
| p95 latency | Load test during POC | Worst-case for most users; this is what kills conversion |
| p99 latency | Load test during POC | Tail latency; affects 1 in 100 users but generates disproportionate support tickets |
| Latency under load | Ramp test to 2x expected volume | Does performance degrade gracefully or cliff? |
| Geographic latency | Test from client's user geographies | A provider fast from US-East may be slow from Europe |
| Rate limits | Published docs + load test confirmation | Will we hit limits at current volume? At 3x volume? |

### 2.4 Developer Experience Deep Dive

Developer experience directly correlates with implementation speed and ongoing maintenance cost. A provider with poor docs and cryptic error messages will cost 2-3x in engineering time over the integration's lifetime.

| Signal | Evaluation Method |
|---|---|
| Documentation completeness | Can an engineer build the integration from docs alone, without contacting support? |
| Sandbox/test environment | Does a fully functional sandbox exist? Can it simulate edge cases (failures, timeouts, webhooks)? |
| SDK quality | Does the SDK handle auth, retries, and error parsing? Or is it a thin wrapper over HTTP? |
| Error messages | Are error responses specific and actionable, or generic (e.g., "Bad Request" with no detail)? |
| API versioning | Is there a versioning strategy? How much notice before breaking changes? |
| Changelog transparency | Can we see what changed in each API version? Are breaking changes clearly flagged? |

### 2.5 Operational Maturity Deep Dive

Operational maturity determines how much ongoing maintenance the integration requires. A provider with good webhook support, transparent rate limiting, and stable APIs is low-maintenance. A provider without these creates recurring engineering work.

| Signal | What Good Looks Like | What Bad Looks Like |
|---|---|---|
| Webhook support | Configurable events, signature verification, retry with backoff, delivery logs | No webhooks (polling only), or webhooks with no retry and no delivery confirmation |
| Rate limiting | Published limits, 429 responses with Retry-After header, burst allowance | Undocumented limits, hard cutoffs with no warning, no Retry-After header |
| API versioning | Semantic versioning, 6+ month deprecation notice, parallel version support | Breaking changes without notice, no versioning, "latest" only |
| Idempotency | Idempotency key support on write operations | No idempotency — retries can create duplicates |
| Pagination | Cursor-based pagination on list endpoints | Offset-based only (skips items under concurrent writes), or no pagination |

---

## 3. Proof of Concept Methodology

### 3.1 POC Structure

Every provider evaluation includes a structured POC before commitment. The POC has a 2-week time box and a specific pass/fail criteria defined before the POC begins.

**Week 1: Integration Build**
- Implement the core use case using the provider's API
- Test all required endpoints (not just the "happy path" the demo showed)
- Verify webhook delivery, signature validation, and retry behavior
- Test error handling: What happens when you send bad data? When auth fails? When you hit rate limits?

**Week 2: Load and Reliability Testing**
- Latency test: Measure p50/p95/p99 under expected load
- Ramp test: Increase load to 2x expected volume, observe degradation
- Failure simulation: What happens when the provider is slow? When it returns 500s? When webhooks don't arrive?
- Edge case testing: Concurrent requests, large payloads, special characters, timezone handling

### 3.2 POC Pass/Fail Criteria

Defined before the POC starts. Example for a KYC provider:

| Criteria | Pass | Fail |
|---|---|---|
| p95 latency | < 5 seconds | > 8 seconds |
| Verification accuracy | > 95% match rate on test data | < 90% match rate |
| Webhook delivery | > 99% delivery rate | < 95% delivery rate |
| Error message quality | Actionable error codes with descriptions | Generic errors requiring support tickets |
| Sandbox completeness | Can simulate all outcomes (pass, fail, manual review, timeout) | Can only simulate happy path |
| Documentation | Integration completed without contacting support | Required 3+ support tickets to complete basic integration |

### 3.3 POC Anti-Patterns

Patterns we learned to avoid:

| Anti-Pattern | Why It's Dangerous |
|---|---|
| Evaluating only the happy path | The unhappy path is where you'll spend 80% of your engineering time |
| Letting the provider run the POC | They'll show you the best-case scenario. You need to test worst-case |
| Skipping load testing | Everything works at 10 requests per second. Real problems emerge at 100+ |
| Comparing on price alone | A provider that costs 20% less but requires 3x the engineering time is more expensive |
| No defined pass/fail criteria | Without criteria, decisions default to "it seems fine" and politics |

---

## 4. Contract Negotiation Leverage

### 4.1 Data-Driven Negotiation

The provider_scorecard module exists specifically to generate the data needed for contract negotiations and QBRs. Having actual performance data changes the dynamic from "we feel like reliability has been an issue" to "your actual uptime was 99.71% against a 99.95% SLA, with 4 incidents in 90 days."

**Leverage points from monitoring data:**

| Data Point | Leverage |
|---|---|
| Actual uptime vs. SLA guarantee | If actual uptime is below SLA, request credits and remediation plan |
| Incident frequency and MTTR | Pattern of repeated incidents justifies requesting engineering escalation path |
| Latency trend (worsening over time) | Performance degradation justifies requesting capacity allocation or infrastructure commitment |
| Webhook delivery rate | Below 99% delivery rate justifies requesting retry improvements or delivery guarantees |
| Our volume as % of their platform | Higher volume = more leverage. Request dedicated support, custom rate limits, or priority incident response |

### 4.2 Key Contract Terms to Negotiate

| Term | What to Push For | Why |
|---|---|---|
| Uptime SLA | 99.95%+ with financial credits for breaches | Without financial penalties, SLAs are aspirational, not contractual |
| Credit calculation | Credits based on our measured downtime, not theirs | Provider's measurement always shows higher uptime than yours |
| Rate limits | Published limits with 6-month notice before reduction | Avoids surprise throttling as you scale |
| Breaking changes | 12-month deprecation notice on API versions | Prevents forced migration on their timeline |
| Support tier | Named technical contact, 1-hour response for P0 | General support queues are useless during incidents |
| Data portability | Full data export within 30 days of contract termination | Prevents vendor lock-in |
| Webhook SLA | 99.9% delivery rate with retry for at least 72 hours | Protects against silent webhook failures |

### 4.3 Multi-Provider Strategy

For critical-path integrations (P0 blast radius), we always recommend evaluating a secondary provider during the initial selection process, even if you don't integrate them immediately. Knowing who your fallback would be and roughly what integration would require gives you:

1. **Negotiation leverage:** "We've evaluated [Competitor] and they meet our requirements" is the strongest negotiation position
2. **Faster mitigation:** If you need to switch, you've already done the evaluation
3. **Risk quantification:** You know the cost of switching, which helps evaluate whether the current provider's issues justify the migration

---

## 5. Ongoing Performance Management

### 5.1 Quarterly Business Reviews

Every P0/P1 provider gets a quarterly review using data from the provider_scorecard module:

**QBR Data Packet:**
- Uptime: Actual vs. SLA (monthly and quarterly)
- Incidents: Count, duration, MTTR, root causes
- Latency: p50/p95/p99 trends over the quarter
- Webhook delivery: Delivery rate, average delivery latency, failure patterns
- Volume: Our request volume and growth trajectory
- Cost: Total cost, cost per call, cost per successful call
- Open issues: Unresolved support tickets, feature requests, integration bugs

### 5.2 Provider Health Scoring

The provider_scorecard generates a composite score (0-100) for each provider based on:

| Component | Weight | Scoring |
|---|---|---|
| Uptime vs. SLA | 30% | 100 if actual >= guaranteed; linear decrease below |
| Incident frequency | 20% | 100 if 0 incidents in 90 days; -15 per incident |
| p95 latency trend | 15% | 100 if stable or improving; decrease if worsening |
| Webhook delivery rate | 15% | 100 if >= 99.9%; linear decrease below |
| Support responsiveness | 10% | Based on average response time to support tickets |
| Developer experience | 10% | Qualitative assessment from engineering team |

**Score interpretation:**

| Score | Assessment | Action |
|---|---|---|
| 90-100 | Excellent | Maintain relationship, consider expanding usage |
| 75-89 | Good | Monitor trends, address specific issues in QBR |
| 60-74 | Concerning | Escalate issues to provider's account team, begin evaluating alternatives |
| Below 60 | Unacceptable | Initiate migration planning, activate or integrate fallback provider |

### 5.3 When to Switch Providers

Switching providers is expensive (engineering time, migration risk, potential downtime). We developed specific triggers for when switching is justified despite the cost:

| Trigger | Threshold |
|---|---|
| SLA breaches | 3+ breaches in a rolling 6-month period |
| Unresolved incidents | Provider acknowledges a systemic issue but fails to resolve within 60 days |
| Latency regression | p95 latency has increased > 50% over 6 months with no improvement commitment |
| Breaking changes | 2+ unannounced breaking changes in 12 months |
| Support degradation | Average support response time has exceeded SLA for 3+ consecutive months |
| Business risk | Provider acquired, leadership change, or financial instability signals |

---

## 6. Evaluation Checklist

Quick reference for starting a new provider evaluation:

```
PRE-EVALUATION
□ Define the use case and required API capabilities
□ Identify blast radius category (P0/P1/P2/P3)
□ Set pass/fail criteria for POC
□ Identify 2-3 candidate providers
□ Review each provider's status page history (last 6 months)

DURING POC (2 weeks)
□ Implement core use case with each candidate
□ Test all required endpoints, including error cases
□ Verify webhook delivery and retry behavior
□ Run latency test (p50/p95/p99 under expected load)
□ Run ramp test (2x expected load)
□ Test failure modes (timeouts, 500s, rate limits)
□ Evaluate documentation and SDK quality
□ Contact support with a technical question (measure response time)

POST-POC
□ Score each candidate against evaluation matrix
□ Document migration path if switching from current provider
□ Negotiate contract terms using evaluation data
□ Define monitoring thresholds in integration_registry
□ Configure webhook_monitor and api_health_tracker
□ Add provider to provider_scorecard for ongoing tracking
□ Create incident response runbook for this provider
□ Schedule first QBR (90 days after go-live)
```
