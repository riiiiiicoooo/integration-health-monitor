# Integration Health Monitor -- Improvements & Roadmap

**Date:** 2026-03-05
**Scope:** Architecture review, technology recommendations, and prioritized roadmap

---

## Product Overview

The Integration Health Monitor is a platform for tracking the health, reliability, and business impact of third-party API integrations. It was built to solve a recurring problem across client engagements: teams had no centralized visibility into which integrations existed, how they were performing, or whether silent failures (especially webhook delivery gaps) were causing customer-facing issues.

The system provides five core capabilities:

1. **Integration Registry** (`src/integration_registry.py`) -- A central catalog of all third-party API providers with metadata including blast radius classification (P0-P3), SLA definitions, authentication methods, data flow patterns, fallback configurations, and user flow dependency mapping.

2. **API Health Tracking** (`src/api_health_tracker.py`) -- Real-time monitoring of synchronous API calls with latency percentile tracking (p50/p95/p99), error rate calculation, and circuit breaker state management (closed/open/half-open).

3. **Webhook Monitoring** (`src/webhook_monitor.py`) -- Delivery reliability tracking for asynchronous webhook events, including expected-vs-actual volume gap detection, dead letter queue management, and delivery trend analysis.

4. **Incident Detection** (`src/incident_detector.py`) -- Anomaly detection engine that compares current metrics against rolling baselines, creates incidents when sustained anomalies are detected, and routes alerts based on blast radius severity.

5. **Onboarding Funnel Correlation** (`src/onboarding_funnel.py`) -- Maps customer journey steps to API dependencies and correlates drop-off rates with provider health metrics, distinguishing UX-driven drop-offs from API-driven ones.

6. **Provider Scorecard** (`src/provider_scorecard.py`, `src/scorecard_report.py`) -- SLA compliance tracking, composite health scoring (0-100), QBR data packet generation, and vendor migration trigger evaluation.

The reference implementation uses a digital lending startup as its demonstration domain, with providers including Stripe, Plaid, Twilio, a KYC verification provider, a credit bureau API, and SendGrid.

---

## Current Architecture

### Tech Stack

| Layer | Technology | Version | Notes |
|-------|-----------|---------|-------|
| **Backend API** | FastAPI | 0.115.0 | REST API with Pydantic models |
| **Language** | Python | 3.x | Dataclass-heavy domain models |
| **Database** | PostgreSQL | 15-alpine | Full relational schema with views |
| **ORM/DB Driver** | SQLAlchemy + psycopg2 | 2.0.36 / 2.9.9 | Listed in requirements but not wired in app.py |
| **Dashboard** | React (JSX) | - | Single-file component with Recharts |
| **Charts** | Recharts | - | Line, Area, Bar charts |
| **Background Jobs** | Trigger.dev | v3 | Health check batch + scorecard generation |
| **Workflow Automation** | n8n | - | Health check loop with circuit breaker logic |
| **Email Templates** | React Email | - | Degradation alerts + scorecard reports |
| **Metrics Export** | prometheus-client | 0.19.0 | Listed in requirements, not integrated |
| **HTTP Client** | httpx + aiohttp | 0.27.0 / 3.9.1 | Listed in requirements |
| **Containerization** | Docker Compose | 3.8 | PostgreSQL, API, Dashboard, Redis, pgAdmin |
| **Cache** | Redis | 7-alpine | Provisioned but not integrated |
| **Cloud DB** | Supabase | - | Migration files present |
| **Visualization** | Grafana | - | Dashboard JSON configs for provider health and funnel correlation |
| **Deployment** | Vercel | - | vercel.json present |

### Key Components

```
src/
  integration_registry.py   -- Provider catalog + dependency mapping (941 lines)
  api_health_tracker.py     -- Sync API monitoring + circuit breakers (660 lines)
  webhook_monitor.py        -- Async webhook delivery tracking (720 lines)
  incident_detector.py      -- Anomaly detection + alert routing (655 lines)
  onboarding_funnel.py      -- Funnel analysis + bottleneck detection (705 lines)
  provider_scorecard.py     -- SLA compliance + vendor scoring (760 lines)
  scorecard_report.py       -- Markdown report generation (350 lines)

api/
  app.py                    -- FastAPI application (668 lines)
  models.py                 -- Pydantic request/response schemas (315 lines)

dashboard/
  dashboard.jsx             -- React dashboard (1084 lines, single file)

schema/
  schema.sql                -- Full PostgreSQL schema (561 lines)
  migrations/               -- 3 migration files
  seed.sql                  -- Seed data

trigger-jobs/
  health_check_batch.ts     -- Daily health checks via Trigger.dev
  scorecard_generation.ts   -- Monthly scorecard generation

n8n/
  health_check_loop.json    -- 5-minute health check workflow
  incident_correlation.json -- Incident correlation workflow

emails/
  degradation_alert.tsx     -- React Email incident alert template
  scorecard_report.tsx      -- React Email scorecard report template

grafana/
  dashboards/               -- Provider health + funnel correlation dashboards
```

### How It Works

1. The **Integration Registry** serves as the foundation -- every provider is cataloged with its endpoints, SLA terms, fallback configuration, and blast radius classification.

2. The **API Health Tracker** records every outgoing API call and calculates rolling latency percentiles and error rates. It manages per-provider circuit breakers that trip when error thresholds are exceeded.

3. The **Webhook Monitor** tracks incoming webhook events against expected delivery volumes, detecting silent failures (delivery gaps) and managing a dead letter queue for events that could not be processed.

4. The **Incident Detector** consumes health metrics from both the tracker and webhook monitor, comparing current values against rolling baselines. When sustained anomalies are detected, it creates incidents with severity based on the provider's blast radius and routes alerts to appropriate channels.

5. The **Onboarding Funnel** module maps each customer journey step to its API dependencies and automatically attributes drop-offs to root causes (API latency, API error, API timeout, or UX friction).

6. The **FastAPI application** (`api/app.py`) wires all modules together and exposes REST endpoints for the dashboard, while the React dashboard provides a single-pane-of-glass view with tabs for overview, webhooks, funnel analysis, and provider scorecards.

### Architecture Gaps Identified

- All source modules use **in-memory data structures** (Python lists and dicts). The PostgreSQL schema exists but is not wired to the application code.
- The API layer has a **hardcoded sys.path** insertion pointing to a Replit session path.
- The `api/app.py` references methods that do not exist on the backend classes (e.g., `incident_detector.register_provider()`, `incident_detector.get_all_incidents()`, `incident_detector.get_incidents_for_provider()`).
- Redis is provisioned in Docker Compose but **never used** in application code.
- The prometheus-client dependency is listed but **not integrated**.
- The dashboard uses **entirely synthetic/hardcoded data** rather than fetching from the API.
- There are **no tests** anywhere in the repository despite pytest being in requirements.
- SQLAlchemy is listed as a dependency but no ORM models or database session management exists.
- The `api/app.py` uses `allow_origins=["*"]` CORS configuration with no restrictions.

---

## Recommended Improvements

### 1. Wire Database Layer to Application Code

**Problem:** All six core modules (`integration_registry`, `api_health_tracker`, `webhook_monitor`, `incident_detector`, `onboarding_funnel`, `provider_scorecard`) use in-memory Python lists and dicts. The PostgreSQL schema in `schema/schema.sql` is comprehensive but completely disconnected from the application.

**Recommendation:** Introduce a repository pattern using SQLAlchemy 2.0 async sessions.

**Files affected:**
- Create `src/database.py` -- Async engine and session factory
- Create `src/repositories/` -- One repository per domain (e.g., `provider_repo.py`, `health_repo.py`)
- Modify `api/app.py` -- Replace in-memory module initialization with database-backed repositories
- Modify each `src/*.py` module -- Accept repository injection instead of managing internal state

**Specific approach:**
```python
# src/database.py
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

engine = create_async_engine("postgresql+asyncpg://...", pool_size=20, max_overflow=10)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

Replace `psycopg2-binary` with `asyncpg` (version 0.29+) for true async PostgreSQL access, which aligns with FastAPI's async-first design.

### 2. Fix API Layer Inconsistencies

**Problem:** `api/app.py` calls methods that do not exist on the backend classes:
- Line 89: `incident_detector.register_provider()` -- The actual method is `configure_provider()`
- Line 282: `incident_detector.get_all_incidents()` -- No such method exists; only `get_active_incidents()`
- Line 465: `incident_detector.get_incidents_for_provider()` -- No such method; only `get_incidents_by_provider()`
- Line 20: Hardcoded `sys.path.insert(0, '/sessions/youthful-eager-lamport/...')` is a Replit artifact

**Recommendation:** Fix all method name mismatches, remove the hardcoded path, and use proper Python package imports via `__init__.py` files or relative imports.

### 3. Add Comprehensive Test Suite

**Problem:** Zero tests exist despite `pytest`, `pytest-asyncio`, and `pytest-cov` being in `requirements.txt`.

**Recommendation:** Add tests at three levels:

- **Unit tests** for each `src/` module (circuit breaker state transitions, anomaly detection logic, funnel attribution, scorecard scoring)
- **Integration tests** for the FastAPI endpoints using `httpx.AsyncClient`
- **Property-based tests** using `hypothesis` for edge cases in statistical calculations (percentile computation, delivery rate calculation)

Priority test targets:
- `api_health_tracker.py` -- Circuit breaker state machine transitions (lines 186-232)
- `incident_detector.py` -- Sustained anomaly detection and incident deduplication (lines 256-313)
- `onboarding_funnel.py` -- Drop-off cause attribution logic (lines 197-222)
- `webhook_monitor.py` -- Delivery gap detection algorithm (lines 276-378)

### 4. Replace Synthetic Dashboard Data with API Integration

**Problem:** `dashboard/dashboard.jsx` contains 143 lines of hardcoded synthetic data (lines 40-202) and never calls the backend API.

**Recommendation:**
- Add `useEffect` hooks with `fetch()` calls to the FastAPI endpoints
- Implement SWR or React Query for data fetching with automatic revalidation
- Add WebSocket support for real-time updates (the API already imports `WebSocket` from FastAPI but never uses it)
- Move the single-file dashboard into a proper component structure

### 5. Integrate Redis for Caching and Circuit Breaker State

**Problem:** Redis is provisioned in `docker-compose.yml` (line 85-97) but never used. Circuit breaker state is held in memory, which means it resets on API restart.

**Recommendation:** Use Redis for:
- **Circuit breaker state persistence** -- Critical for surviving process restarts
- **Rate limiting** -- Protect against thundering herd during provider recovery
- **Health snapshot caching** -- Avoid recalculating snapshots on every dashboard poll
- **Pub/sub for real-time updates** -- Push circuit breaker state changes to connected WebSocket clients

Use `redis-py` (version 5.0+) with async support via `aioredis` integration.

### 6. Integrate Prometheus Metrics Export

**Problem:** `prometheus-client==0.19.0` is in `requirements.txt` but never used.

**Recommendation:** Instrument the FastAPI application with Prometheus metrics:

```python
from prometheus_client import Counter, Histogram, Gauge
from prometheus_fastapi_instrumentator import Instrumentator

api_call_latency = Histogram('api_call_latency_ms', 'API call latency', ['provider_id', 'endpoint'])
circuit_breaker_state = Gauge('circuit_breaker_state', 'Circuit breaker state', ['provider_id'])
webhook_delivery_rate = Gauge('webhook_delivery_rate_pct', 'Webhook delivery rate', ['provider_id'])
incident_count = Gauge('active_incidents', 'Active incident count', ['severity'])
```

This enables Grafana dashboards (configs already exist in `grafana/dashboards/`) to consume live data instead of requiring custom API polling.

### 7. Add Structured Logging

**Problem:** The application uses basic `logging.basicConfig(level=logging.INFO)` with no structured output. `python-json-logger==2.0.7` is in requirements but unused.

**Recommendation:** Configure structured JSON logging with correlation IDs:

```python
from pythonjsonlogger import jsonlogger

handler = logging.StreamHandler()
handler.setFormatter(jsonlogger.JsonFormatter(
    '%(asctime)s %(levelname)s %(name)s %(message)s',
    rename_fields={'asctime': 'timestamp', 'levelname': 'level'}
))
```

Add request correlation IDs via FastAPI middleware so that all log entries for a single request/incident can be traced.

### 8. Implement Proper Configuration Management

**Problem:** Configuration is scattered: hardcoded values in source files, `.env.example` exists but no `pydantic-settings` usage despite being in requirements, and database URLs are hardcoded in `docker-compose.yml`.

**Recommendation:** Use `pydantic-settings` (already a dependency) to create a typed settings class:

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    redis_url: str = "redis://localhost:6379"
    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:3000"]

    class Config:
        env_file = ".env"
```

This replaces `allow_origins=["*"]` with configurable, environment-specific CORS settings.

### 9. Add Database Migration Tooling

**Problem:** Schema migrations exist as raw SQL files in `schema/migrations/` but there is no migration runner or version tracking.

**Recommendation:** Adopt Alembic (the standard migration tool for SQLAlchemy) to manage schema evolution:
- Version: Alembic 1.13+
- Auto-generate migrations from SQLAlchemy ORM models
- Track migration state in the database
- Support rollback for failed deployments

### 10. Harden Webhook Signature Verification

**Problem:** In `api/app.py` line 229, webhook signature verification is hardcoded to `signature_valid=True`. The `verify_hmac_signature()` method in `webhook_monitor.py` (lines 562-589) exists but is never called from the API layer.

**Recommendation:** Wire the signature verification into the webhook receiver endpoint and implement provider-specific verification adapters (Stripe uses timestamp-prefixed signatures, Twilio includes the URL in the HMAC, Plaid uses JWKs).

---

## New Technologies & Trends

### 1. OpenTelemetry for Unified Observability

**What:** OpenTelemetry (OTel) is the CNCF standard for distributed tracing, metrics, and logs. The Python SDK (`opentelemetry-api` 1.25+ and `opentelemetry-sdk` 1.25+) provides auto-instrumentation for FastAPI, httpx, SQLAlchemy, and Redis.

**Why it matters for this project:** The current architecture tracks metrics in isolated silos (API health in one module, webhooks in another, incidents in a third). OTel provides a unified signal pipeline that correlates traces across all three.

**How to integrate:**
- Install `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-httpx`, `opentelemetry-instrumentation-sqlalchemy`
- Each outgoing API call to a provider would generate a span with provider_id, endpoint, latency, and status code as attributes
- Webhook processing would create child spans linked to the original provider event
- Export to any OTel-compatible backend (Jaeger, Grafana Tempo, Datadog)

**Links:**
- https://opentelemetry.io/docs/languages/python/
- https://pypi.org/project/opentelemetry-instrumentation-fastapi/

### 2. TimescaleDB for Time-Series Metrics Storage

**What:** TimescaleDB is a PostgreSQL extension that adds hypertable partitioning, continuous aggregates, and compression for time-series data. Since the project already uses PostgreSQL, TimescaleDB can be added without changing the database engine.

**Why it matters:** The `api_call_events`, `webhook_events`, and `health_snapshots` tables are high-volume time-series data. Standard PostgreSQL will degrade as these tables grow. TimescaleDB provides:
- Automatic time-based partitioning (hypertables)
- 90-95% compression on historical data
- Continuous aggregates for pre-computed rollups (hourly, daily averages)
- Built-in retention policies for data lifecycle management

**How to integrate:**
```sql
-- Convert high-volume tables to hypertables
SELECT create_hypertable('api_call_events', 'timestamp');
SELECT create_hypertable('webhook_events', 'received_at');
SELECT create_hypertable('health_snapshots', 'snapshot_at');

-- Add continuous aggregates for dashboard queries
CREATE MATERIALIZED VIEW hourly_provider_health
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', timestamp) AS bucket,
    provider_id,
    avg(latency_ms) AS avg_latency,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency,
    count(*) FILTER (WHERE NOT success) * 100.0 / count(*) AS error_rate_pct
FROM api_call_events
GROUP BY bucket, provider_id;
```

**Links:**
- https://docs.timescale.com/self-hosted/latest/install/
- Docker image: `timescale/timescaledb:latest-pg15`

### 3. Checkly for Synthetic Monitoring

**What:** Checkly is a synthetic monitoring platform that runs API checks and browser checks from multiple geographic regions on a schedule. It provides a Monitoring-as-Code workflow via their CLI and Terraform provider.

**Why it matters:** The current health check architecture (`trigger-jobs/health_check_batch.ts`, `n8n/health_check_loop.json`) runs from a single location. Checkly adds:
- Multi-region checks (detect regional provider issues)
- Playwright-based browser checks for provider UIs (status pages)
- Alerting with PagerDuty, Slack, and webhook integrations
- Built-in SSL certificate and domain expiry monitoring
- Monitoring-as-Code via `checkly.config.ts`

**Links:**
- https://www.checklyhq.com/docs/cli/
- https://www.checklyhq.com/docs/monitoring-as-code/

### 4. Grafana Alloy (formerly Grafana Agent) for Metrics Collection

**What:** Grafana Alloy is a vendor-agnostic telemetry collector that can scrape Prometheus metrics, receive OpenTelemetry signals, and forward them to any backend. It replaces the need for a standalone Prometheus server.

**Why it matters:** The project has Grafana dashboard JSON configs in `grafana/dashboards/` but no metrics pipeline to feed them. Alloy provides a lightweight, single-binary collector that:
- Scrapes the FastAPI Prometheus `/metrics` endpoint
- Receives OTel traces and sends them to Grafana Tempo
- Forwards logs to Grafana Loki
- Runs as a sidecar in Docker Compose

**Links:**
- https://grafana.com/docs/alloy/latest/
- Docker image: `grafana/alloy:latest`

### 5. Resend for Transactional Email Delivery

**What:** Resend is a modern email API built for developers, with native React Email support. The project already uses React Email templates (`emails/degradation_alert.tsx`, `emails/scorecard_report.tsx`) which are directly compatible.

**Why it matters:** The email templates exist but have no send mechanism. The `scorecard_generation.ts` trigger job (line 280) contains a comment "In production, this would use Resend." Resend provides:
- Native React Email rendering (no separate build step)
- Delivery tracking and analytics
- Python SDK (`resend` package on PyPI)
- Webhook delivery notifications

**Links:**
- https://resend.com/docs/send-with-python
- https://pypi.org/project/resend/

### 6. Temporal or Inngest for Durable Workflow Orchestration

**What:** Temporal is a durable workflow execution engine. Inngest is a similar but lighter-weight alternative designed for serverless environments. Both provide reliable background job execution with automatic retries, timeouts, and state persistence.

**Why it matters:** The current architecture splits background work across Trigger.dev (`trigger-jobs/`) and n8n (`n8n/`). This creates two systems to maintain with different paradigms. A unified workflow engine would:
- Consolidate the health check loop, scorecard generation, and incident correlation into one system
- Provide durable execution (jobs survive process crashes)
- Enable complex workflows like "if provider is unhealthy for 30 minutes, escalate severity"
- Offer built-in observability for workflow execution

Inngest is particularly well-suited because it has a Python SDK and works well with FastAPI:
```python
import inngest
client = inngest.Inngest(app_id="integration-health-monitor")

@client.create_function(
    fn_id="health-check-loop",
    trigger=inngest.TriggerCron(cron="*/5 * * * *"),
)
async def health_check_loop(ctx: inngest.Context, step: inngest.Step):
    providers = await step.run("fetch-providers", fetch_active_providers)
    for provider in providers:
        await step.run(f"check-{provider.id}", lambda: check_provider_health(provider))
```

**Links:**
- https://www.inngest.com/docs/reference/python
- https://docs.temporal.io/develop/python

### 7. AI/ML-Powered Anomaly Detection

**What:** Replace the current static threshold-based anomaly detection with ML-powered approaches using libraries like `Prophet` (Meta's time-series forecasting), `PyOD` (Python Outlier Detection), or `scikit-learn` isolation forests.

**Why it matters:** The current `incident_detector.py` uses a fixed `threshold_multiplier` (e.g., 2.0x baseline) which produces false positives during known traffic spikes (marketing campaigns, seasonal peaks) and misses gradual degradation. ML-based detection can:
- Learn seasonal patterns (weekday vs weekend, business hours vs off-hours)
- Adapt thresholds dynamically based on historical behavior
- Detect multivariate anomalies (latency + error rate + webhook delivery degrading together)

**Specific recommendation:** Start with `Prophet` for time-series forecasting (it handles seasonality well) and fall back to isolation forests from `scikit-learn` for multivariate anomaly detection:

```python
from prophet import Prophet
import pandas as pd

# Train on 30 days of p95 latency data
df = pd.DataFrame({'ds': timestamps, 'y': p95_values})
model = Prophet(interval_width=0.95)
model.fit(df)
forecast = model.predict(future_df)
# Anomaly = actual value outside the 95% confidence interval
```

**Links:**
- https://facebook.github.io/prophet/docs/quick_start.html
- https://pyod.readthedocs.io/en/latest/

### 8. SLA.io or Statuspage Integration for Provider Status Correlation

**What:** Programmatically consume provider status pages (Atlassian Statuspage API, which powers status.stripe.com, status.twilio.com, etc.) to correlate internal health data with the provider's own reported status.

**Why it matters:** The codebase documents that in "6 out of 8 major incidents, our monitoring detected the issue before the provider's status page updated" (`api_health_tracker.py` line 16). Automatically consuming status page data would:
- Track the delta between internal detection and provider acknowledgment
- Provide evidence for QBR discussions (provider_scorecard already tracks `appeared_on_status_page`)
- Auto-populate the `IncidentRecord.appeared_on_status_page` field

**Implementation:**
```python
import httpx

async def check_provider_status(status_page_url: str) -> dict:
    # Atlassian Statuspage API: https://statuspage.io/api
    api_url = status_page_url.replace("https://", "https://").rstrip("/") + "/api/v2/status.json"
    async with httpx.AsyncClient() as client:
        response = await client.get(api_url)
        data = response.json()
        return {
            "indicator": data["status"]["indicator"],  # none, minor, major, critical
            "description": data["status"]["description"],
        }
```

### 9. Feature Flags for Fallback Activation

**What:** Use a feature flag service (LaunchDarkly, Unleash, or the open-source Flipt) to control fallback provider activation.

**Why it matters:** The `FallbackConfig` dataclass (in `integration_registry.py` line 133) specifies `activation_method` which can be "automatic", "feature_flag", or "manual". Feature flags for fallback activation enable:
- Instant fallback activation without deployment
- Gradual rollout (route 10% of traffic to fallback, then 50%, then 100%)
- Automatic activation triggered by circuit breaker state
- Audit trail of who activated/deactivated fallbacks and when

Flipt is the recommended option as it is open-source and can run as a sidecar container:

**Links:**
- https://www.flipt.io/docs
- Docker image: `flipt/flipt:latest`

### 10. ClickHouse for High-Cardinality Analytics

**What:** ClickHouse is a column-oriented OLAP database designed for real-time analytics over large datasets. It excels at queries over billions of rows with sub-second response times.

**Why it matters:** As the system scales to monitor more providers across more clients, the `api_call_events` table will grow rapidly. ClickHouse is purpose-built for the exact query patterns this system needs:
- Aggregations over time windows (error rates, percentiles)
- High-cardinality dimensions (provider_id x endpoint x status_code x time)
- Real-time ingestion with immediate queryability

For this project, ClickHouse would serve as the analytics layer alongside PostgreSQL (which remains the transactional store for provider configuration and incidents).

**Links:**
- https://clickhouse.com/docs
- Python client: https://pypi.org/project/clickhouse-connect/

---

## Priority Roadmap

### P0 -- Critical (Weeks 1-2)

These items address fundamental gaps that prevent the system from functioning correctly in production.

| # | Item | Effort | Impact | Files |
|---|------|--------|--------|-------|
| 1 | **Fix API layer method mismatches** -- Align `api/app.py` method calls with actual backend class methods. Remove hardcoded sys.path. | 2 hours | Blocking: API cannot run | `api/app.py` lines 20, 89, 282, 465, 519 |
| 2 | **Wire database layer** -- Connect the PostgreSQL schema to application code using SQLAlchemy async + asyncpg. Replace in-memory stores. | 3-5 days | Blocking: Data does not persist | All `src/*.py` modules, `api/app.py` |
| 3 | **Add test suite** -- Unit tests for circuit breaker logic, anomaly detection, funnel attribution, and delivery gap detection. Integration tests for API endpoints. | 3-4 days | Blocking: No verification of correctness | New `tests/` directory |
| 4 | **Fix CORS configuration** -- Replace `allow_origins=["*"]` with environment-specific origins using pydantic-settings. | 1 hour | Security vulnerability | `api/app.py` line 52 |
| 5 | **Wire webhook signature verification** -- Connect `verify_hmac_signature()` to the webhook receiver endpoint. | 3-4 hours | Security vulnerability | `api/app.py` line 229, `webhook_monitor.py` lines 562-589 |

### P1 -- High Priority (Weeks 3-6)

These items significantly improve reliability, observability, and operational readiness.

| # | Item | Effort | Impact | Files |
|---|------|--------|--------|-------|
| 6 | **Integrate Redis for circuit breaker state** -- Persist circuit breaker state across restarts. Add caching for health snapshots. | 2-3 days | Prevents state loss on restart | `api_health_tracker.py`, `api/app.py`, `docker-compose.yml` |
| 7 | **Connect dashboard to API** -- Replace hardcoded data with fetch calls. Add React Query for data fetching and WebSocket for real-time updates. | 3-4 days | Dashboard shows real data | `dashboard/dashboard.jsx` |
| 8 | **Add Prometheus metrics + Grafana pipeline** -- Instrument FastAPI, wire prometheus-client, add Grafana Alloy as metrics collector. | 2-3 days | Enables existing Grafana dashboards | `api/app.py`, `docker-compose.yml`, `grafana/` |
| 9 | **Integrate structured JSON logging** -- Use python-json-logger with correlation IDs. | 1 day | Debuggable production logs | `api/app.py`, all modules |
| 10 | **Add Alembic migrations** -- Replace raw SQL migration files with Alembic for version-tracked schema evolution. | 1-2 days | Safe schema changes in production | `schema/`, new `alembic/` directory |
| 11 | **Add Resend email integration** -- Wire React Email templates to Resend for degradation alerts and scorecard reports. | 1-2 days | Automated alert delivery | `emails/`, `trigger-jobs/scorecard_generation.ts` |

### P2 -- Medium Priority (Weeks 7-12)

These items add significant capability and improve the system's intelligence.

| # | Item | Effort | Impact | Files |
|---|------|--------|--------|-------|
| 12 | **Adopt OpenTelemetry** -- Unified tracing across API calls, webhook processing, and incident detection. | 3-5 days | Correlated observability across all signals | All modules, `requirements.txt` |
| 13 | **Add TimescaleDB** -- Convert high-volume tables to hypertables with continuous aggregates and compression. | 2-3 days | 10-100x query performance improvement on historical data | `schema/schema.sql`, `docker-compose.yml` |
| 14 | **Implement provider status page integration** -- Programmatically consume Atlassian Statuspage APIs for provider status correlation. | 2-3 days | Automated status page tracking for QBR data | New `src/status_page_monitor.py` |
| 15 | **Consolidate background jobs** -- Migrate from Trigger.dev + n8n to a single workflow engine (Inngest or Temporal). | 3-5 days | Simplified operations, durable execution | `trigger-jobs/`, `n8n/` |
| 16 | **Add feature flags for fallback activation** -- Integrate Flipt for controlling fallback provider routing. | 2-3 days | Instant, auditable fallback activation | `integration_registry.py`, `api/app.py` |
| 17 | **Break dashboard into component architecture** -- Split 1084-line single-file dashboard into proper React component hierarchy with routing. | 2-3 days | Maintainability and extensibility | `dashboard/` |

### P3 -- Future Enhancements (Quarter 2+)

These items represent longer-term investments that add differentiated capabilities.

| # | Item | Effort | Impact | Files |
|---|------|--------|--------|-------|
| 18 | **ML-powered anomaly detection** -- Replace static thresholds with Prophet for seasonality-aware forecasting and PyOD for multivariate anomaly detection. | 1-2 weeks | Fewer false positives, catches gradual degradation | `incident_detector.py` |
| 19 | **Multi-tenant architecture** -- Add client/tenant isolation so the system can monitor integrations for multiple organizations. | 1-2 weeks | Product scalability | All modules, schema |
| 20 | **ClickHouse analytics layer** -- Add ClickHouse for high-cardinality analytics queries on API call and webhook event data. | 1 week | Sub-second analytics at scale | `docker-compose.yml`, new analytics module |
| 21 | **SLA credit automation** -- When SLA breaches are detected (`provider_scorecard.py` line 260), auto-generate credit request documentation with evidence from health data. | 3-5 days | Revenue recovery | `provider_scorecard.py`, `scorecard_report.py` |
| 22 | **API contract testing** -- Use Schemathesis or Dredd to continuously verify that provider APIs conform to their published OpenAPI specs. | 3-5 days | Early detection of breaking changes | New `contract_tests/` directory |
| 23 | **Runbook automation** -- Integrate with PagerDuty's Event Orchestration or Rundeck to automatically execute incident response runbooks when specific anomaly types are detected. | 1-2 weeks | Reduced MTTR | `incident_detector.py` |
| 24 | **Cost anomaly detection** -- Extend the provider scorecard to detect unusual cost spikes (e.g., rate limit exhaustion causing excessive retries). | 3-5 days | Cost control | `provider_scorecard.py` |
| 25 | **Dependency graph visualization** -- Add a D3.js or Mermaid-based interactive dependency graph showing provider relationships, flow dependencies, and real-time health status. | 1 week | Visual blast radius assessment | `dashboard/` |

---

*Generated by architecture review on 2026-03-05. Based on analysis of all source files, schema definitions, infrastructure configuration, and documentation in the integration-health-monitor repository.*
