# Integration Architecture: Multi-Provider API Ecosystem

**Last Updated:** February 2025

---

## 1. The Integration Problem at Scale

Every company starts with a handful of API integrations. A payment processor, an email provider, maybe an analytics tool. At 3-4 integrations, you can manage them by memory. Engineers know which APIs are in play, how they're connected, and what breaks when something goes down.

At 8+ integrations, that mental model collapses. Nobody has the full picture anymore. Integrations get added by different people at different times. Some have retry logic, some don't. Some send webhooks, some require polling. Some have rate limits that nobody documented. The system still works most of the time, but when it fails, nobody can tell you why, and nobody can tell you what else is affected.

This document covers how we mapped, categorized, and monitored the integration ecosystems across four client engagements, and the architectural patterns that emerged as repeatable solutions.

---

## 2. Provider Categories

Every multi-API environment breaks down into the same functional layers, regardless of industry. The specific providers change, but the categories are consistent.

### 2.1 Category Taxonomy

| Category | Purpose | Examples Across Clients |
|---|---|---|
| **Identity & Verification** | Confirm users are who they claim to be | KYC providers, identity verification APIs, business verification services |
| **Financial Connectivity** | Connect to bank accounts, process payments, issue cards | Bank linking APIs, payment processors, card issuing platforms |
| **Communication** | Transactional messaging, 2FA, notifications | SMS APIs, email delivery services, push notification platforms |
| **Fulfillment & Operations** | Physical or digital delivery, inventory sync | 3PL warehouse APIs, shipping label generators, inventory sync services |
| **Compliance & Risk** | Fraud detection, regulatory checks, audit logging | Fraud scoring APIs, sanctions screening, compliance verification |
| **Data & Analytics** | Event tracking, customer data enrichment | Analytics platforms, CDP integrations, enrichment APIs |
| **Document & Workflow** | Signatures, document generation, form processing | E-signature APIs, document generation services, OCR providers |
| **Industry-Specific** | Vertical tools unique to the client's domain | Insurance carrier APIs, benefits eligibility feeds, EHR integrations |

### 2.2 Why Categories Matter

Categories determine failure impact patterns. When a Communication provider goes down, users can't receive 2FA codes — onboarding stops, but existing users are unaffected. When a Financial Connectivity provider degrades, active transactions fail, refunds stall, and revenue impact is immediate.

The integration registry tags every provider with its category so that incidents can be triaged by blast radius, not just by which provider name appears in the error log.

---

## 3. Dependency Chains

The most dangerous architectural pattern we found across clients was sequential dependency chains in critical user flows. A single user action (like completing onboarding) would trigger 4-6 API calls in sequence, where each step depended on the previous one succeeding.

### 3.1 Example: Lending Onboarding Flow

```
User submits application
    │
    ▼
┌─────────────────────┐
│ Step 1: Phone        │ ── Twilio SMS API (2FA verification)
│ Verification         │    Failure mode: User can't verify, flow stops
│ Avg latency: 1.2s   │    Fallback: Email verification (SendGrid)
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Step 2: Identity     │ ── Socure KYC API
│ Verification         │    Failure mode: Can't verify identity, flow stops
│ Avg latency: 3.8s   │    Fallback: Manual review queue
│ P95 latency: 11.2s  │    ← THIS WAS THE BOTTLENECK
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Step 3: Bank         │ ── Plaid Link + webhooks
│ Account Linking      │    Failure mode: Can't verify income/assets
│ Avg latency: 2.1s   │    Fallback: Manual bank statement upload
│ Webhook dependency   │    ← SILENT FAILURES HERE
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Step 4: Credit       │ ── Credit bureau API
│ Check                │    Failure mode: Can't underwrite
│ Avg latency: 0.9s   │    Fallback: None (hard dependency)
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Step 5: Disbursement │ ── Stripe Connect
│                      │    Failure mode: Approved but can't fund
│ Avg latency: 1.4s   │    Fallback: ACH next-day via backup processor
└─────────────────────┘
```

**Total chain latency (p50):** ~9.4 seconds
**Total chain latency (p95):** ~22+ seconds
**Single point of failure count:** 1 (credit bureau — no fallback)
**Silent failure risk:** 1 (Plaid webhooks — async, no delivery guarantee monitoring)

### 3.2 Example: E-Commerce Order Flow

```
Customer places order
    │
    ├──► Stripe (payment capture)
    │       └── Failure: Order fails, customer sees error
    │
    ├──► Inventory API (3PL warehouse)
    │       └── Failure: Payment captured but fulfillment doesn't start
    │           ← SILENT: No retry, no delivery confirmation
    │
    ├──► ShipStation (label generation)
    │       └── Failure: Order in limbo, manual label required
    │
    ├──► Klaviyo (order confirmation email)
    │       └── Failure: Customer doesn't get confirmation
    │           Low severity but generates support tickets
    │
    └──► ERP (accounting sync)
            └── Failure: Revenue recognized late
                Invisible until month-end close
```

### 3.3 Dependency Chain Risk Assessment

For each client, we mapped every user-facing flow to its API dependencies and scored the chain:

| Risk Factor | How We Score It |
|---|---|
| **Chain length** | Number of sequential API calls. Longer chains = higher compound failure probability |
| **Hard dependencies** | Steps with no fallback. One hard dependency in a 5-step chain means the chain's uptime is bounded by that provider's uptime |
| **Async gaps** | Steps that depend on webhooks rather than synchronous responses. These are where silent failures live |
| **Latency budget** | Total acceptable time for the user-facing flow. If p95 latency exceeds the budget, users drop off |
| **Blast radius** | Number of users or revenue dollars affected when this chain breaks |

---

## 4. Data Flow Patterns

### 4.1 Synchronous Request-Response

The simplest and most common pattern. Client application makes an API call, waits for the response, proceeds.

**Where we saw it:** Payment processing, identity verification, credit checks.

**Monitoring approach:** Straightforward. Track latency, status codes, and error rates per endpoint. The api_health_tracker module handles this.

**Risk:** Latency compounds in chains. If 5 synchronous calls each take 2 seconds, the user waits 10 seconds.

### 4.2 Webhook-Based Async

Provider sends data to the client's webhook endpoint after an event occurs. The client doesn't poll — they wait to be notified.

**Where we saw it:** Bank linking (Plaid), payment status updates (Stripe), fulfillment status (3PL), carrier updates (insurance).

**Monitoring approach:** Much harder. You need to track:
- Did the webhook actually get delivered? (Many providers don't retry, or retry only once)
- Did our endpoint acknowledge it? (A 500 response means the provider thinks they delivered, but we didn't process it)
- How long between the event and the webhook delivery? (Latency here can be minutes or hours)
- Are there gaps in the event sequence? (Missing webhook = missing data)

**Risk:** Silent failures. If a webhook doesn't arrive, nothing errors out. The data just doesn't show up. The webhook_monitor module exists specifically because this pattern was the root cause of the majority of customer-facing issues across all four clients.

### 4.3 Polling

Client periodically checks the provider's API for updates. Used when the provider doesn't support webhooks or when webhooks are unreliable.

**Where we saw it:** Legacy insurance carrier APIs, some ERP integrations, benefits eligibility status checks.

**Monitoring approach:** Track polling frequency, response payload changes, rate limit consumption.

**Risk:** Stale data between polling intervals. Rate limit exhaustion if polling too frequently.

### 4.4 File-Based Exchange (Batch)

Provider sends or expects flat files (CSV, EDI, SFTP uploads) on a schedule. Common in healthcare and insurance.

**Where we saw it:** Benefits enrollment feeds, claims processing files, carrier policy feeds.

**Monitoring approach:** Track file arrival times, file size anomalies (empty file = likely error), row count consistency, schema drift.

**Risk:** Failures are discovered hours or days later. A missing file at 2 AM isn't noticed until someone checks Monday morning.

---

## 5. Failure Blast Radius Mapping

Not all integration failures are equal. A failed analytics event is invisible to the user. A failed payment capture loses revenue immediately. We built blast radius maps for each client to prioritize monitoring and alerting.

### 5.1 Blast Radius Categories

| Severity | Definition | Example | Response Time Target |
|---|---|---|---|
| **P0 — Revenue Blocking** | Users cannot complete a transaction that generates revenue | Payment processor down, card issuing API failing | < 5 minutes to detect, < 15 minutes to mitigate |
| **P1 — Onboarding Blocking** | New users cannot complete signup/activation | KYC provider timeout, bank linking webhook failure | < 10 minutes to detect, < 30 minutes to mitigate |
| **P2 — Feature Degraded** | A feature works partially or with reduced quality | Fraud scoring slow (approving without full score), email delivery delayed | < 30 minutes to detect, < 2 hours to mitigate |
| **P3 — Back-Office Impact** | Internal operations affected, users unaware | ERP sync failed, analytics events missing, reporting delayed | < 4 hours to detect, next business day to resolve |

### 5.2 Example Blast Radius Map (Lending Client)

| Provider | Category | Severity If Down | Users Affected | Revenue Impact | Fallback? |
|---|---|---|---|---|---|
| Payment Processor | Financial | P0 | All active borrowers | Direct — disbursements stop | Secondary processor (ACH) |
| KYC Provider | Identity | P1 | New applicants only | Indirect — pipeline stops | Manual review queue |
| Bank Linking | Financial | P1 | New applicants | Indirect — can't verify income | Manual upload |
| SMS API | Communication | P1 | New applicants (2FA) | Indirect — can't verify phone | Email fallback |
| Credit Bureau | Compliance | P0 | New applicants | Direct — can't underwrite | None (hard dependency) |
| Email Service | Communication | P3 | All users | None — cosmetic only | Queue and retry |
| Analytics | Data | P3 | None (internal) | None | Events buffered locally |

---

## 6. Architecture Decisions

### 6.1 Why a Unified Registry

The first decision across every engagement was centralizing provider configurations into a single registry rather than having each integration managed independently in application code.

**Before:** Each integration had its own configuration scattered across environment variables, config files, and hardcoded values. Nobody could answer "how many integrations do we have?" without reading code.

**After:** The integration_registry.py module stores every provider's endpoints, authentication method, SLA definitions, health check URLs, webhook configurations, and dependency mappings in one place. This became the foundation for everything else — you can't monitor what you haven't cataloged.

### 6.2 Why Event-Sourced Health Tracking

Rather than polling each provider's status page (which are notoriously unreliable and often delayed), we track health from our own perspective: every API call and webhook delivery generates an event that the health tracker consumes.

This means our health scores reflect the actual experience our application is having with each provider, not what the provider's status page claims. We caught multiple incidents where a provider's status page showed "all systems operational" while our error rates were at 15%+.

### 6.3 Why Circuit Breakers Per Provider

When a provider starts failing, the worst thing you can do is keep hammering it with requests. It slows down your application (waiting for timeouts), it makes the provider's problem worse (more load), and it burns through rate limits.

The api_health_tracker implements per-provider circuit breakers:
- **Closed** (normal): Requests flow through, health metrics are tracked
- **Open** (provider failing): Requests are immediately routed to fallback or fail fast. No more waiting for timeouts
- **Half-open** (testing recovery): A small number of requests probe whether the provider has recovered

The thresholds for tripping the circuit breaker are configured per provider based on their historical reliability and the blast radius of their failure.

---

## 7. Lessons Learned

1. **Webhook monitoring is more important than API monitoring.** Synchronous API failures are loud — they throw errors, break flows, generate tickets. Webhook failures are silent. The majority of customer-facing issues across all four clients were caused by undetected webhook delivery failures.

2. **Provider status pages lie.** Not intentionally, but they're often delayed by 15-30 minutes and frequently miss partial degradation. Monitor from your own perspective, not theirs.

3. **Sequential chains are fragile.** Any chain of 5+ synchronous API calls will have reliability problems. The math is simple: five 99.9% providers in sequence give you 99.5% chain reliability. That's over 4 hours of downtime per year. Parallelize where possible, add fallbacks for hard dependencies.

4. **The integration you forgot about is the one that breaks you.** Every client had at least one integration that nobody remembered setting up. It was configured by a former employee or a previous consultant, it had no monitoring, and it was quietly critical to some business process.

5. **Start with the registry.** You can't monitor what you haven't cataloged. The first deliverable for every engagement was simply documenting what existed.
