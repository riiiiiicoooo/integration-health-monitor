# Integration Health Monitor

**A PM-built platform for monitoring third-party API integrations, webhook reliability, and their impact on customer experience across multi-provider environments.**

---

## The Problem

A pattern across multiple consulting engagements: companies scaling through third-party API integrations with no centralized visibility into whether those integrations were actually working.

One client, a digital lending startup, had grown from 3 to 12+ API integrations in under 18 months. Plaid for bank linking, an identity verification provider, a credit bureau API, Stripe for disbursement, Twilio for SMS/2FA, and several more. Another client, a mid-market e-commerce brand, had a similar sprawl across Shopify, payment processors, fulfillment APIs, and warehouse integrations. A regional insurance brokerage had 8+ SaaS tools configured by different consultants over 3 years.

The symptoms were always the same:

- **Webhooks were failing silently.** The lending client's bank linking provider webhook delivery dropped to 84% over two weeks before anyone noticed. Customer bank linking was quietly breaking. Support tickets went up. Nobody connected the dots.
- **No error rate monitoring by provider.** Engineering would hear "onboarding is broken" but couldn't tell whether the issue was the KYC API timing out, the payment token creation failing, or their own middleware. Every incident started with 30 minutes of "which integration is it?"
- **Onboarding drop-off was invisible.** Product knew that 22% of users dropped off during the identity verification step, but didn't know that 60% of those drop-offs correlated with API latency exceeding 8 seconds. The fix wasn't UX. It was a timeout configuration and a fallback provider.
- **Integration sprawl with zero documentation.** The insurance client had integrations configured by 3 different consultants over 3 years. Nobody had a complete picture of which connections were active, which had silently broken, or what failed when a carrier API went down during quoting season.
- **No SLA accountability.** Providers guaranteed 99.9% uptime, but nobody was tracking actual delivery. When a card issuing provider had intermittent 503s for 6 hours on a Saturday, the lending client had no data to bring to the QBR.

Every client needed the same thing: a single system that could answer "are all our integrations healthy, and if not, what's the customer impact?"

---

## What I Built

An integration health monitoring platform that gives product, engineering, and ops teams a unified view of every third-party dependency — from raw API health down to customer-facing impact. The core modules were built for the lending startup engagement and then adapted across subsequent clients.

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Integration Health Monitor                   │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │  Integration  │  │   Webhook    │  │    API Health         │  │
│  │   Registry    │  │   Monitor    │  │    Tracker            │  │
│  │              │  │              │  │                       │  │
│  │ Provider     │  │ Delivery     │  │ Latency (p50/p95/p99) │  │
│  │  configs     │  │  tracking    │  │ Error rates by code   │  │
│  │ Endpoint     │  │ Retry logic  │  │ Circuit breaker       │  │
│  │  catalog     │  │ Dead letter  │  │  state                │  │
│  │ SLA          │  │  queue       │  │ Uptime windows        │  │
│  │  definitions │  │ Failure      │  │ Provider comparison   │  │
│  │ Auth configs │  │  patterns    │  │                       │  │
│  └──────┬───────┘  └──────┬───────┘  └───────────┬───────────┘  │
│         │                 │                      │              │
│  ┌──────┴──────────┐  ┌───┴──────────────┐  ┌────┴───────────┐  │
│  │ Webhook Receiver │  │   PostgreSQL     │  │   Dashboard    │  │
│  │ (FastAPI)       │  │   (schema.sql)   │  │   (React)      │  │
│  │                 │  │                  │  │                │  │
│  │ Signature       │  │ Integration      │  │ Provider       │  │
│  │  verification   │  │  events          │  │  status cards  │  │
│  │ Event           │  │ Webhook logs     │  │ Latency charts │  │
│  │  normalization  │  │ Health snapshots │  │ Funnel overlay │  │
│  │ Provider        │  │ Incidents        │  │ Incident       │  │
│  │  routing        │  │ SLA records      │  │  timeline      │  │
│  └──────┬──────────┘  └───┬──────────────┘  └────┬───────────┘  │
│         │                 │                      │              │
│  ┌──────┴─────────────────┴──────────────────────┴───────────┐  │
│  │                    Event Stream / Log                      │  │
│  └──────┬─────────────────┬──────────────────────┬───────────┘  │
│         │                 │                      │              │
│  ┌──────┴───────┐  ┌──────┴──────┐  ┌────────────┴──────────┐  │
│  │  Onboarding  │  │  Incident   │  │    Provider           │  │
│  │   Funnel     │  │  Detector   │  │    Scorecard          │  │
│  │              │  │             │  │                       │  │
│  │ Step → API   │  │ Anomaly     │  │ SLA compliance        │  │
│  │  mapping     │  │  detection  │  │ Uptime vs. guarantee  │  │
│  │ Drop-off     │  │ Alert       │  │ Cost per call         │  │
│  │  correlation │  │  routing    │  │ Reliability ranking   │  │
│  │ Bottleneck   │  │ Incident    │  │ QBR data export       │  │
│  │  detection   │  │  timeline   │  │                       │  │
│  └──────────────┘  └─────────────┘  └───────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## PM Perspective

Hardest decision: Whether to build a generic monitoring framework or client-specific implementations. The generic approach was more scalable but each client's integration landscape was different — the lending startup had 4 critical APIs (Plaid, Equifax, DocuSign, Stripe), the e-commerce platform had 12 (payment, shipping, inventory, marketing). Chose a hybrid: standardized health scoring and alerting engine with pluggable provider-specific adapters. The adapter pattern added a week per client but meant 80% of the codebase was reusable across engagements.

Surprise: The most impactful finding across clients wasn't API downtime — it was webhook delivery degradation. At the lending startup, Plaid's webhook delivery had quietly dropped to 84% over two weeks. Nobody noticed because the loan applications still worked — they just had stale income data. The borrower would get approved based on old income verification, then the compliance team would catch it in post-close review. By the time we surfaced this, they'd processed 47 applications with potentially stale data. That single finding justified the entire engagement.

Do differently: Would build the customer impact mapping earlier. We started with pure infrastructure monitoring (API response time, error rates, webhook delivery) but the "so what?" question kept coming up. Adding the connection between "Plaid webhooks are failing" → "12 loan applications are missing income verification" → "$340K in at-risk pipeline" made the alerts actionable. Should have been Phase 1, not Phase 2.

---

## Business Context

**Market:** SaaS companies with 5+ third-party integrations represent ~45,000 businesses in the US. Integration failures cost mid-market SaaS companies $180K-$500K/year in engineering time, lost revenue, and customer churn (Merge.dev State of Integrations Report).

**Unit Economics:**

| Metric | Before | After |
|--------|--------|-------|
| Annual integration-related costs | $320K/year | $85K/year |
| Integration engineering time | High (untracked) | Automated monitoring |
| Annual savings | — | $235K |
| Platform cost (build) | — | $130,000 |
| Platform cost (monthly) | — | $700 |
| Payback period | — | 7 months |
| 3-year ROI | — | 5x |

**Pricing:** If productized, $1,000-4,000/month based on integration count and webhook volume, targeting $5-12M ARR at 500 customers.

---

## Results (Across Engagements)

| Metric | Before | After | Client |
|---|---|---|---|
| Mean time to identify failing integration | 30+ minutes | < 2 minutes | All |
| Onboarding completion rate | 61% | 74% | Lending startup |
| Silent webhook failures detected | 0 (not monitored) | 47 caught in first month | E-commerce brand |
| Inventory reconciliation manual effort | 10+ hrs/week | < 1 hr/week | E-commerce brand |
| Provider SLA disputes resolved with data | 0 | 3 in first quarter | Lending startup |
| Broken integrations discovered | Unknown | 2 found immediately | Insurance brokerage |
| Engineering time on integration triage | ~15 hrs/week | ~3 hrs/week | Lending startup |

---

## Engagement & Budget

### Team & Timeline

| Role | Allocation | Duration |
|------|-----------|----------|
| Lead PM (Jacob) | 15 hrs/week | 12 weeks |
| Lead Developer (US) | 35 hrs/week | 12 weeks |
| Offshore Developer(s) | 1 × 35 hrs/week | 12 weeks |
| QA Engineer | 15 hrs/week | 12 weeks |

**Timeline:** 12 weeks total across 3 phases
- **Phase 1: Discovery & Design** (2 weeks) — Integration inventory audit, failure mode mapping, SLA requirements, alerting threshold design
- **Phase 2: Core Build** (7 weeks) — Health check engine, webhook delivery tracker, anomaly detection pipeline, Grafana dashboards, alert routing
- **Phase 3: Integration & Launch** (3 weeks) — Provider-specific connectors (Stripe, Salesforce, Plaid), incident response runbooks, ops team onboarding, monitoring calibration

### Budget Summary

| Category | Cost | Notes |
|----------|------|-------|
| PM & Strategy | $33,300 | Discovery, specs, stakeholder management |
| Development (Lead + Offshore) | $89,460 | Core platform build |
| AI/LLM Token Budget | $2,160/month | Claude Haiku for anomaly classification and incident summarization ~2M tokens/month |
| Infrastructure | $4,560/month | Supabase Pro, Redis, n8n, Trigger.dev, Grafana, AWS compute, React Email/Resend, misc |
| **Total Engagement** | **$130,000** | Fixed-price, phases billed at milestones |
| **Ongoing Run Rate** | **$700/month** | Infrastructure + AI tokens + 2hrs support |

---

## Client Context

This platform was built across multiple consulting engagements where the core problem was the same: companies scaling their product through third-party APIs without the internal platform engineering resources to monitor what was actually happening.

### Where This Was Deployed

**Digital Lending Startup (~60 employees, Series A)**
Online lending product where the application flow touched Plaid (bank linking), Socure (identity verification), a credit bureau API, Stripe (payment disbursement), and Twilio (SMS verification) sequentially. Two-person engineering team was entirely focused on the core product. Nobody was watching whether Plaid webhooks were actually delivering. Onboarding completion had dropped 17 points over two months and everyone assumed it was a UX problem.

**Regional Insurance Brokerage (~150 employees, no dedicated engineering team)**
Modernizing from paper-based processes, had adopted 8+ SaaS tools (Applied Epic for policy management, EZLynx for quoting, Vertafore for carrier connectivity, DocuSign for signatures, a payment processor, plus several carrier APIs). Each tool had its own webhook/API setup configured by different consultants over 3 years. No one had a complete picture of which integrations were active, which had silently broken, or what the customer impact was when a carrier API went down during quoting season.

**Mid-Market E-Commerce Brand (~$40M revenue, 90 employees)**
Shopify Plus storefront consuming Stripe for payments, ShipStation for fulfillment, Klaviyo for email, Loop for returns, Gorgias for support, plus a 3PL warehouse API and an ERP integration. The 3PL API had no retry logic and would silently drop inventory sync webhooks, causing overselling. The ops team was spending 10+ hours per week manually reconciling inventory because they had no visibility into which API calls were succeeding or failing.

**Healthcare Benefits Administrator (~200 employees, outsourced dev team)**
Platform connecting employers with benefits providers, consuming eligibility verification APIs, pharmacy benefit manager APIs, claims processing endpoints, and multiple carrier enrollment feeds. Regulatory requirements meant they needed to prove that enrollment data was being transmitted and acknowledged. Their offshore development team had built each integration independently with no shared monitoring or logging pattern.

### Common Thread

None of these companies had (or could justify) a dedicated platform engineering team to build internal monitoring tooling. They were all growing through API integrations faster than their operational maturity could keep up. The pattern was always the same: things worked fine at 3-4 integrations, started breaking silently at 8+, and became a customer-facing problem before anyone realized the root cause was integration health, not product quality.

---

## Tech Stack

### Core Stack
- **Language:** Python 3.11+
- **API Layer:** FastAPI for webhook ingestion and health API endpoints
- **Frontend:** React with Recharts for data visualization (synthetic data only)
- **Database:** PostgreSQL schema for event storage, health snapshots, and incident records
- **Key Libraries:** `dataclasses`, `statistics`, `datetime`, `enum`, `pydantic`, `uvicorn`
- **Integration patterns:** Webhook ingestion, REST API polling, circuit breaker, exponential backoff with jitter

### Alerting & Incident Management
- **PagerDuty:** Incident routing, escalation policies, on-call rotation management, and incident lifecycle tracking. Integrates with anomaly detection and circuit breaker state to route critical alerts through escalation channels based on provider blast radius.

### Analytics & Historical Query
- **ClickHouse:** Columnar time-series analytics database for high-performance historical queries on integration health metrics. Enables fast aggregation of latency percentiles, error rates, webhook delivery trends, and SLA compliance calculations across weeks/months of data (10x faster than PostgreSQL for analytics on large datasets).

---

## Modern Stack (Production Ready)

Built with cloud-native, API-first services for scalability and ease of operations:

### Data Layer
- **Supabase PostgreSQL**: Multi-tenant database with RLS policies, realtime subscriptions, and audit trails
- **Migrations**: `/supabase/migrations/001_initial_schema.sql` - Fully normalized schema with multi-tenant support (clients, users with roles, providers, events, incidents, funnel data, scorecards)

### Automation & Workflows
- **n8n**: Low-code workflow automation for health checks and incident escalation
  - `n8n/health_check_loop.json`: Cron-based health checks every 5 minutes → log to Supabase → compare against baseline → detect anomalies → trip circuit breaker if threshold exceeded
  - `n8n/incident_correlation.json`: Supabase webhook trigger for new incidents → correlate related signals → route by severity → notify Slack + send emails
- **Trigger.dev**: Serverless job scheduling for batch operations
  - `trigger-jobs/health_check_batch.ts`: Daily 2 AM - comprehensive health snapshot generation
  - `trigger-jobs/scorecard_generation.ts`: Monthly 1st of month - SLA compliance, cost analysis, QBR data

### Monitoring & Observability
- **Grafana Dashboards** (JSON exports):
  - `grafana/dashboards/provider_health.json`: Real-time latency percentiles, error rates by provider, circuit breaker timeline, webhook delivery rates, incident trends
  - `grafana/dashboards/funnel_correlation.json`: Onboarding funnel completion with API dependency overlay, drop-off correlation analysis by cause, top failing providers
- **Realtime Updates**: Supabase subscriptions for live health status and incident alerts
- **ClickHouse Integration**: Historical analytics queries for SLA trend analysis, cost-per-call calculations, provider ranking reports, and multi-month incident pattern analysis

### Incident Response & Escalation
- **PagerDuty Integration**: Incident severity routing (P0/P1 to on-call engineering, P2 to team Slack, P3 to digest), escalation policies for sustained outages, incident timeline auto-population from detected anomalies, MTTI/MTTR calculations for postmortems
- **Automated Escalation**: Circuit breaker OPEN state and anomaly severity automatically trigger PagerDuty incidents with provider context, blast radius classification, and suggested remediation steps

### Notifications & Reports
- **React Email Templates** (TSX):
  - `emails/degradation_alert.tsx`: Incident detection alert with metrics, impact summary, severity-based action items, links to dashboard, PagerDuty incident link
  - `emails/scorecard_report.tsx`: Monthly QBR report with uptime vs. SLA, incident counts, MTTR, cost analysis (powered by ClickHouse historical aggregations), renewal recommendations
- **Channels**: Slack (severity-routed), Resend (email), PagerDuty (escalation with on-call routing)

### Deployment & Configuration
- **Vercel**: Serverless hosting for API webhooks and cron jobs
  - `vercel.json`: Build configuration, cron triggers, function memory/timeout tuning
- **Replit**: Local development environment
  - `.replit` + `replit.nix`: Postgres, Node 18, Python 3.11, n8n, Grafana
- **Environment**: `.env.example` - All configuration keys for local and production

### Infrastructure
- **Upstash Redis**: Circuit breaker state cache (fast recovery probe lookups)
- **PostgreSQL 14+**: Full-text search on webhook payloads, JSONB columns for flexible event storage

### API & Cache
- **Caching**: Redis for circuit breaker state with 1-hour TTL
- **Idempotency**: Health checks log with unique request IDs, workflows are re-triggerable
- **Scalability**: Stateless functions, database-backed state, async job processing

---

## What's NOT Included

- Authentication or API key management (client-specific security configurations)
- Datadog/PagerDuty integration code (production monitoring layer built by engineering based on these prototypes)
- Deployment or infrastructure configuration
- Actual client data, provider credentials, or environment-specific configurations

---

## About This Project

This was built for multiple client engagements where third-party API reliability was creating silent failures — a lending startup, an e-commerce platform, an insurance brokerage, and a healthcare data company.

**Role & Leadership:**
- Identified the cross-client pattern of integration failures going undetected and productized the monitoring approach
- Led discovery at each client to map critical integration paths, SLAs, and failure modes
- Designed the health scoring framework, anomaly detection, and alerting architecture
- Established per-client results measurement tracking MTTI, webhook reliability, and recovery rates
