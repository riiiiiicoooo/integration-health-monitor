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

## Source Code: Reference Implementation

> **Note:** This code is a PM-authored reference implementation demonstrating the core technical concepts behind the Integration Health Monitor. It is not production code. These prototypes were built to validate feasibility, communicate architecture to engineering, and demonstrate technical fluency during product development.

### Core Modules

| File | Purpose |
|---|---|
| `integration_registry.py` | Central registry of all third-party API providers, their endpoints, authentication configurations, SLA definitions, and dependency mappings |
| `webhook_monitor.py` | Webhook delivery tracking with retry logic, failure pattern detection, dead letter queue management, and delivery rate calculation by provider |
| `api_health_tracker.py` | Real-time API health monitoring per provider — latency percentiles (p50/p95/p99), error rates by HTTP status code, circuit breaker state management, and uptime window tracking |
| `onboarding_funnel.py` | Maps each customer onboarding step to its API dependencies, correlates drop-off rates with integration health, and identifies bottlenecks caused by provider degradation |
| `incident_detector.py` | Anomaly detection for API degradation using statistical thresholds, automatic alert routing, and incident timeline construction from correlated events |
| `provider_scorecard.py` | SLA compliance tracking, actual vs. guaranteed uptime calculation, cost-per-call analysis, and reliability scoring for vendor QBR preparation |

### API & Data Layer

| File | Purpose |
|---|---|
| `webhook_receiver.py` | FastAPI webhook ingestion endpoint — receives, validates, and routes incoming webhooks from third-party providers with signature verification and event normalization |
| `schema.sql` | Database schema defining tables, relationships, and indexes for integration events, webhook logs, health snapshots, incidents, and provider SLA records |

### Dashboard

| File | Purpose |
|---|---|
| `dashboard.jsx` | React-based integration health dashboard — real-time provider status cards, webhook delivery rates, latency charts, onboarding funnel with API dependency overlay, and incident timeline (all synthetic data) |

### Documentation

| File | Purpose |
|---|---|
| `INTEGRATION_ARCHITECTURE.md` | How a multi-API ecosystem is structured — provider categories, data flows, dependency chains, and failure blast radius mapping |
| `INCIDENT_RESPONSE.md` | Playbook for integration failures — detection, triage, escalation paths, provider communication templates, and post-incident review framework |
| `PROVIDER_EVALUATION.md` | Framework for evaluating and selecting API providers — scoring criteria, proof-of-concept methodology, migration risk assessment, and contract negotiation leverage points |

---

## How These Were Used

As PM, I wrote these prototypes to:

1. **Diagnose a revenue problem.** The lending startup's onboarding completion had dropped from 78% to 61% over two months. Their team assumed it was a UX issue and was redesigning flows. The funnel analysis module proved that 60%+ of drop-offs correlated with their identity verification API's latency spikes exceeding 8 seconds during peak hours. The fix was a timeout adjustment and a fallback provider, not a redesign. Onboarding recovered to 74% within two weeks.

2. **Expose silent failures.** The e-commerce client's 3PL warehouse API had no retry logic and was silently dropping inventory sync webhooks. The webhook monitor caught 47 failed deliveries in the first month that had been causing overselling and manual reconciliation. Their ops team got back 10+ hours per week.

3. **Build the case for a fallback provider.** The incident detector surfaced that the lending client's card issuing provider had 4 degradation events in 90 days, each lasting 2-8 hours. The provider scorecard calculated actual uptime at 99.71% against a 99.95% SLA guarantee. This data gave the client leverage in their QBR and justified the cost of integrating a secondary issuer.

4. **Untangle a legacy integration mess.** The insurance brokerage had 8+ tools configured by different consultants over 3 years with no documentation. The integration registry became the single source of truth for what was connected to what, which webhooks were active, and who owned each integration. Two integrations turned out to be completely broken with nobody noticing.

5. **Communicate architecture to engineering.** The prototypes served as the working spec for production monitoring systems across engagements. Engineering teams used the data models, health scoring logic, and alerting thresholds directly when building production versions with proper database backing and alerting infrastructure.

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

### Reference Implementation
- **Language:** Python 3.11+
- **API Layer:** FastAPI for webhook ingestion and health API endpoints
- **Frontend:** React with Recharts for data visualization (synthetic data only)
- **Database:** PostgreSQL schema for event storage, health snapshots, and incident records
- **Key Libraries:** `dataclasses`, `statistics`, `datetime`, `enum`, `pydantic`, `uvicorn`
- **Production equivalent:** Datadog for metrics/alerting, PagerDuty for incident routing
- **Integration patterns:** Webhook ingestion, REST API polling, circuit breaker, exponential backoff with jitter

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

### Notifications & Reports
- **React Email Templates** (TSX):
  - `emails/degradation_alert.tsx`: Incident detection alert with metrics, impact summary, severity-based action items, links to dashboard
  - `emails/scorecard_report.tsx`: Monthly QBR report with uptime vs. SLA, incident counts, MTTR, cost analysis, renewal recommendations
- **Channels**: Slack (severity-routed), Resend (email), PagerDuty (escalation)

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
