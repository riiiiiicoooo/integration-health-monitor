# Security Review: Integration Health Monitor

**Review Date:** 2026-03-06
**Scope:** Full source code audit of `integration-health-monitor`
**Reviewer:** Automated security audit (Claude)
**Severity Scale:** CRITICAL / HIGH / MEDIUM / LOW / INFO

---

## Executive Summary

This review identified **23 security findings** across 7 categories. The most critical issues include hardcoded database credentials in infrastructure configuration, complete absence of authentication on all API endpoints, SQL injection vectors in n8n workflow definitions, and Server-Side Request Forgery (SSRF) risk in health check batch jobs that construct URLs from database-stored values.

| Severity | Count |
|----------|-------|
| CRITICAL | 5     |
| HIGH     | 7     |
| MEDIUM   | 7     |
| LOW      | 4     |

---

## 1. Hardcoded Secrets and API Keys

### Finding 1.1 -- Hardcoded PostgreSQL Password in Docker Compose

- **Severity:** CRITICAL
- **File:** `docker-compose.yml`, lines 10, 33
- **Description:** The PostgreSQL password is hardcoded directly in the Docker Compose file. This file is committed to version control, exposing database credentials to anyone with repository access. The same password appears in the `DATABASE_URL` connection string on line 33.
- **Code Evidence:**
  ```yaml
  # Line 10
  POSTGRES_PASSWORD: postgres_dev_password

  # Line 33
  DATABASE_URL: postgresql://postgres:postgres_dev_password@postgres:5432/integration_monitor
  ```
- **Fix:** Replace hardcoded passwords with environment variable references. Use a `.env` file (already in `.gitignore`) or a secrets manager:
  ```yaml
  POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
  DATABASE_URL: postgresql://postgres:${POSTGRES_PASSWORD}@postgres:5432/integration_monitor
  ```

### Finding 1.2 -- Hardcoded pgAdmin Credentials

- **Severity:** HIGH
- **File:** `docker-compose.yml`, lines 74-75
- **Description:** The pgAdmin default email and password are hardcoded with weak, guessable values. pgAdmin provides full database administration access, making this a high-value target.
- **Code Evidence:**
  ```yaml
  PGADMIN_DEFAULT_EMAIL: admin@example.com
  PGADMIN_DEFAULT_PASSWORD: admin_password
  ```
- **Fix:** Move to environment variables and use strong credentials:
  ```yaml
  PGADMIN_DEFAULT_EMAIL: ${PGADMIN_EMAIL}
  PGADMIN_DEFAULT_PASSWORD: ${PGADMIN_PASSWORD}
  ```

### Finding 1.3 -- Hardcoded Absolute Paths Leak Infrastructure Details

- **Severity:** LOW
- **File:** `api/app.py`, line 20; `demo/simulate_24h.py`, line 17
- **Description:** Both files contain hardcoded absolute paths from a specific development session. These paths leak internal infrastructure details (session IDs, directory structure) and would cause import failures in any other environment.
- **Code Evidence:**
  ```python
  # api/app.py line 20
  sys.path.insert(0, '/sessions/youthful-eager-lamport/mnt/Portfolio/integration-health-monitor/src')

  # demo/simulate_24h.py line 17
  sys.path.insert(0, '/sessions/youthful-eager-lamport/mnt/Portfolio/integration-health-monitor/src')
  ```
- **Fix:** Use relative path resolution based on the file's location:
  ```python
  import os
  sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
  ```

---

## 2. SSRF Risk (Server-Side Request Forgery)

### Finding 2.1 -- Unvalidated URL Construction from Database Values in Health Check Batch

- **Severity:** CRITICAL
- **File:** `trigger-jobs/health_check_batch.ts`, lines 77-88
- **Description:** The health check batch job constructs URLs by concatenating `base_url` and `path` values read directly from the database, then issues HTTP fetch requests to those URLs. If an attacker gains write access to the `providers` or `provider_endpoints` tables (e.g., via SQL injection in the n8n workflows), they can redirect health checks to internal network addresses such as cloud metadata endpoints (`http://169.254.169.254/latest/meta-data/`), internal services (`http://localhost:6379/`), or internal network ranges. There is no URL validation or allowlist enforcement.
- **Code Evidence:**
  ```typescript
  // Lines 77-88
  const url = `${ep.base_url}${ep.path}`;
  const response = await fetch(url, {
    method: ep.method,
    timeout: ep.timeout_ms,
    headers: {
      "User-Agent": "IntegrationHealthMonitor/1.0",
      "X-Check-ID": `health-${Date.now()}`,
    },
  });
  ```
- **Fix:** Implement URL validation before making requests:
  ```typescript
  import { URL } from 'url';

  function validateHealthCheckUrl(baseUrl: string, path: string): string {
    const url = new URL(path, baseUrl);

    // Block private/internal IP ranges
    const blockedPatterns = [
      /^(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.)/,
      /^127\./,
      /^169\.254\./,
      /^0\./,
      /localhost/i,
      /\[::1\]/,
    ];

    const hostname = url.hostname;
    for (const pattern of blockedPatterns) {
      if (pattern.test(hostname)) {
        throw new Error(`Blocked internal URL: ${url.toString()}`);
      }
    }

    // Enforce HTTPS only
    if (url.protocol !== 'https:') {
      throw new Error(`Only HTTPS URLs allowed: ${url.toString()}`);
    }

    return url.toString();
  }
  ```

### Finding 2.2 -- SSRF via n8n HTTP Request Node

- **Severity:** HIGH
- **File:** `n8n/health_check_loop.json`, lines 52-53 (HTTP Request node)
- **Description:** The n8n workflow constructs URLs from database-sourced `base_url` and `path` values and makes HTTP requests without URL validation. The same SSRF attack vector as Finding 2.1 applies, but through the n8n orchestration layer.
- **Code Evidence:**
  ```json
  "url": "={{ $node['Loop Endpoints'].json.body.base_url + $node['Loop Endpoints'].json.body.path }}"
  ```
- **Fix:** Add a Code node before the HTTP Request node that validates URLs against an allowlist of external domains. Block requests to private IP ranges, localhost, and cloud metadata endpoints.

---

## 3. Authentication Vulnerabilities

### Finding 3.1 -- No Authentication on Any API Endpoint

- **Severity:** CRITICAL
- **File:** `api/app.py`, lines 101-667
- **Description:** None of the 14 API endpoints implement any form of authentication or authorization. This means anyone with network access can: read all integration health data, view incident details and internal provider configurations, submit arbitrary webhook payloads, acknowledge incidents, and access the dashboard summary. In production, this exposes sensitive operational data about third-party provider reliability, SLA compliance, and incident response status.
- **Code Evidence:**
  ```python
  # All endpoints are unprotected. Example:
  @app.get("/integrations", response_model=IntegrationListResponse, tags=["Health"])
  async def list_integrations():
      # No auth check
      providers = registry.list_all()
      ...

  @app.post("/webhooks/{provider_id}", response_model=WebhookReceiptResponse, tags=["Webhooks"])
  async def receive_webhook(provider_id: str, payload: WebhookPayload):
      # No auth check
      ...

  @app.post("/incidents/{incident_id}/acknowledge", tags=["Incidents"])
  async def acknowledge_incident(incident_id: str, request: AcknowledgeIncidentRequest):
      # No auth check - anyone can acknowledge incidents
      ...
  ```
- **Fix:** Implement API key or JWT-based authentication. At minimum, add an API key dependency:
  ```python
  from fastapi import Depends, Security
  from fastapi.security import APIKeyHeader

  API_KEY_HEADER = APIKeyHeader(name="X-API-Key")

  async def verify_api_key(api_key: str = Security(API_KEY_HEADER)):
      if api_key != os.getenv("API_KEY"):
          raise HTTPException(status_code=403, detail="Invalid API key")

  @app.get("/integrations", dependencies=[Depends(verify_api_key)])
  async def list_integrations():
      ...
  ```
  For webhook endpoints, implement provider-specific signature verification (see Finding 3.2).

### Finding 3.2 -- Webhook Signature Verification Bypassed

- **Severity:** CRITICAL
- **File:** `api/app.py`, line 229
- **Description:** The webhook receiver endpoint hardcodes `signature_valid=True` for every incoming webhook payload. The codebase contains a proper HMAC verification implementation in `src/webhook_monitor.py` (lines 562-589), but it is never invoked from the API layer. This means any attacker can submit fabricated webhook events that will be processed as legitimate provider data, potentially corrupting health metrics and triggering false incidents or masking real ones.
- **Code Evidence:**
  ```python
  # api/app.py line 229
  signature_valid=True,  # Signature verification would happen here
  ```
  Meanwhile, a proper implementation exists but is unused:
  ```python
  # src/webhook_monitor.py lines 562-589
  @staticmethod
  def verify_hmac_signature(
      payload: bytes,
      secret: str,
      received_signature: str,
      algorithm: str = "sha256",
  ) -> bool:
      ...
  ```
- **Fix:** Implement signature verification in the webhook endpoint using the existing helper:
  ```python
  @app.post("/webhooks/{provider_id}")
  async def receive_webhook(provider_id: str, request: Request):
      body = await request.body()
      signature = request.headers.get("X-Webhook-Signature", "")
      secret = get_webhook_secret(provider_id)  # From env/secrets manager

      if not WebhookMonitor.verify_hmac_signature(body, secret, signature):
          raise HTTPException(status_code=401, detail="Invalid webhook signature")
      ...
  ```

### Finding 3.3 -- Unauthenticated Vercel Cron Endpoints

- **Severity:** HIGH
- **File:** `vercel.json`, lines 34-47
- **Description:** Three cron job endpoints are defined without any authentication mechanism. While Vercel cron jobs are triggered by Vercel's infrastructure, these endpoints are also accessible via direct HTTP requests, allowing anyone to trigger batch health checks, scorecard generation, or data cleanup at will.
- **Code Evidence:**
  ```json
  "crons": [
    { "path": "/api/cron/health-check-batch", "schedule": "0 2 * * *" },
    { "path": "/api/cron/scorecard-generation", "schedule": "0 1 1 * *" },
    { "path": "/api/cron/clean-old-events", "schedule": "0 3 * * 0" }
  ]
  ```
- **Fix:** Add a shared secret verification header. Vercel cron jobs send an `Authorization` header with a `CRON_SECRET` value that can be verified:
  ```typescript
  export default async function handler(req, res) {
    if (req.headers.authorization !== `Bearer ${process.env.CRON_SECRET}`) {
      return res.status(401).json({ error: 'Unauthorized' });
    }
    // ... cron logic
  }
  ```

---

## 4. Input Validation (SQL Injection, XSS)

### Finding 4.1 -- SQL Injection in n8n Health Check Workflow

- **Severity:** CRITICAL
- **File:** `n8n/health_check_loop.json`, line 144 (Get Error Rate node)
- **Description:** The SQL query uses string interpolation to inject `provider_id`, `client_id`, and `window_seconds` values directly into the query without parameterization. An attacker who can influence the `provider_id` or `client_id` values (e.g., by registering a provider with a malicious name) can execute arbitrary SQL against the database.
- **Code Evidence:**
  ```sql
  SELECT COUNT(*) as failed_count, ...
  FROM api_call_events
  WHERE provider_id = '{{ $node['Set Variables'].json.provider_id }}'
    AND client_id = '{{ $node['Set Variables'].json.client_id }}'
    AND timestamp >= NOW() - INTERVAL '{{ $node['Loop Endpoints'].json.body.window_seconds }} seconds'
  ```
  The same pattern appears in the "Check Consecutive Failures" node (line 213):
  ```sql
  WHERE provider_id = '{{ $node['Set Variables'].json.provider_id }}'
    AND client_id = '{{ $node['Set Variables'].json.client_id }}'
    AND success = FALSE
  ```
- **Fix:** Use parameterized queries in n8n by switching to the "Query Parameters" option instead of inline interpolation. If n8n does not support parameterized queries natively for this node type, add a validation step that rejects `provider_id` and `client_id` values containing single quotes, semicolons, or SQL keywords.

### Finding 4.2 -- SQL Injection in n8n Incident Correlation Workflow

- **Severity:** CRITICAL
- **File:** `n8n/incident_correlation.json`, lines 22, 39, 236
- **Description:** Multiple SQL queries use unparameterized string interpolation. The most dangerous instance is on line 236 where an `IN` clause is constructed by mapping over an array and wrapping values in single quotes -- a classic SQL injection vector.
- **Code Evidence:**
  ```sql
  -- Line 22 (Get Incident Details)
  WHERE incident_id = '{{ $trigger.body.incident_id }}'

  -- Line 39 (Find Related Incidents)
  WHERE client_id = '{{ $node['Get Incident Details'][0].client_id }}'
    AND incident_id != '{{ $node['Get Incident Details'][0].incident_id }}'

  -- Line 236 (Get Affected Flows) - MOST DANGEROUS
  WHERE fd.client_id = '{{ $node['Get Incident Details'][0].client_id }}'
    AND fd.provider_id IN ({{ $node['Prepare Incident Data'].json.affected_providers.map(p => "'" + p + "'").join(',') }})
  ```
  The line 236 pattern constructs SQL by iterating over an array and wrapping each element with single quotes via JavaScript string concatenation. An attacker-controlled `provider_id` of `'); DROP TABLE incidents; --` would result in valid destructive SQL.
- **Fix:** Replace all string-interpolated queries with parameterized queries. For the `IN` clause, generate numbered parameter placeholders:
  ```javascript
  // Instead of manual string building:
  const placeholders = affected_providers.map((_, i) => `$${i + 2}`).join(',');
  const query = `SELECT fd.flow_name FROM flow_dependencies fd WHERE fd.client_id = $1 AND fd.provider_id IN (${placeholders})`;
  ```

### Finding 4.3 -- No Request Body Size Limit on Webhook Payload

- **Severity:** MEDIUM
- **File:** `api/models.py`, lines 282-288
- **Description:** The `WebhookPayload` model accepts an arbitrary `Dict[str, Any]` payload with no size constraints. An attacker can submit extremely large payloads to consume memory and cause denial of service.
- **Code Evidence:**
  ```python
  class WebhookPayload(BaseModel):
      provider_id: str
      event_type: str
      event_id: str
      timestamp: Optional[datetime] = None
      payload: Dict[str, Any] = Field(default_factory=dict)  # No max size
  ```
- **Fix:** Add payload size validation:
  ```python
  from pydantic import validator

  class WebhookPayload(BaseModel):
      ...
      payload: Dict[str, Any] = Field(default_factory=dict)

      @validator('payload')
      def validate_payload_size(cls, v):
          import json
          if len(json.dumps(v)) > 1_048_576:  # 1MB limit
              raise ValueError('Payload exceeds maximum size of 1MB')
          return v
  ```
  Also configure FastAPI request body limits:
  ```python
  app = FastAPI(max_request_body_size=2_097_152)  # 2MB
  ```

### Finding 4.4 -- XSS Risk in Dashboard (Low due to React)

- **Severity:** LOW
- **File:** `dashboard/dashboard.jsx`
- **Description:** The React dashboard renders provider names, incident IDs, and other data from API responses. React's JSX rendering automatically escapes HTML entities, providing built-in XSS protection. However, if any component uses `dangerouslySetInnerHTML` in the future, or if provider names containing script tags are rendered outside React's virtual DOM, XSS could occur. Currently low risk.
- **Fix:** Maintain the practice of never using `dangerouslySetInnerHTML`. Add server-side input validation to reject provider names containing HTML/script tags.

---

## 5. API Keys/Tokens Exposed in Logs or Responses

### Finding 5.1 -- Debug Mode Enabled in Docker Compose

- **Severity:** MEDIUM
- **File:** `docker-compose.yml`, line 35
- **Description:** The API service runs with `DEBUG: "true"`, which in many frameworks causes detailed error tracebacks (including environment variables, request headers, and internal paths) to be included in HTTP responses. FastAPI in debug mode will return full Python stack traces to the client.
- **Code Evidence:**
  ```yaml
  environment:
    DEBUG: "true"
    LOG_LEVEL: INFO
  ```
- **Fix:** Set `DEBUG: "false"` for any environment beyond local development. Use environment-specific configuration:
  ```yaml
  DEBUG: ${DEBUG:-false}
  ```

### Finding 5.2 -- Bare Exception Silently Swallows Errors

- **Severity:** MEDIUM
- **File:** `api/app.py`, line 618
- **Description:** A bare `except: pass` block in the dashboard summary endpoint silently swallows all exceptions, including keyboard interrupts and system exits. While this prevents information leakage, it hides real errors that need investigation and makes debugging impossible.
- **Code Evidence:**
  ```python
  try:
      flow_health = registry.get_flow_health(flow_name)
      if not flow_health.get("chain_healthy"):
          top_funnels.append({...})
  except:
      pass
  ```
- **Fix:** Catch specific exceptions and log them:
  ```python
  except (KeyError, ValueError) as e:
      logger.warning(f"Failed to get flow health for {flow_name}: {e}")
  ```

### Finding 5.3 -- Upstash Redis URL Exposed in n8n Workflow

- **Severity:** HIGH
- **File:** `n8n/health_check_loop.json`, lines 312-315
- **Description:** The n8n workflow makes an HTTP request to the Upstash Redis URL using `{{ env.UPSTASH_REDIS_URL }}` with `"authentication": "none"`. If the Redis URL contains embedded credentials (which Upstash URLs typically do, format: `https://default:PASSWORD@region.upstash.io`), these credentials flow through n8n's HTTP Request node and may appear in n8n execution logs, error messages, and the n8n UI.
- **Code Evidence:**
  ```json
  {
    "url": "{{ env.UPSTASH_REDIS_URL }}",
    "authentication": "none",
    "method": "POST",
    "body": "=SET circuit_breaker:{{ $node['Set Variables'].json.provider_id }} open EX 3600"
  }
  ```
- **Fix:** Use n8n's built-in credential system for Redis connections instead of embedding the URL directly. Alternatively, use the Upstash REST API with a separate token:
  ```json
  {
    "url": "{{ env.UPSTASH_REDIS_REST_URL }}",
    "authentication": "headerAuth",
    "headerAuthName": "Authorization",
    "headerAuthValue": "Bearer {{ env.UPSTASH_REDIS_TOKEN }}"
  }
  ```

---

## 6. Infrastructure Misconfigurations

### Finding 6.1 -- CORS Wildcard with Credentials Enabled

- **Severity:** HIGH
- **File:** `api/app.py`, lines 51-57
- **Description:** The CORS configuration allows all origins (`*`) while also enabling credentials (`allow_credentials=True`). This combination is explicitly prohibited by the CORS specification (browsers will reject it), but more importantly, it signals a misconfigured security posture. If the wildcard is changed to a specific origin while credentials remain enabled, any XSS on that origin could make authenticated cross-origin requests.
- **Code Evidence:**
  ```python
  app.add_middleware(
      CORSMiddleware,
      allow_origins=["*"],
      allow_credentials=True,
      allow_methods=["*"],
      allow_headers=["*"],
  )
  ```
- **Fix:** Restrict to specific allowed origins:
  ```python
  ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
  app.add_middleware(
      CORSMiddleware,
      allow_origins=ALLOWED_ORIGINS,
      allow_credentials=True,
      allow_methods=["GET", "POST", "PUT"],
      allow_headers=["Authorization", "Content-Type"],
  )
  ```

### Finding 6.2 -- Redis Exposed Without Authentication

- **Severity:** HIGH
- **File:** `docker-compose.yml`, lines 84-97
- **Description:** Redis is exposed on port 6379 to the host machine with no authentication configured. Any process on the host (or anyone with network access) can connect to Redis, read cached data, modify circuit breaker states, or execute arbitrary Redis commands including `FLUSHALL`.
- **Code Evidence:**
  ```yaml
  redis:
    image: redis:7-alpine
    container_name: integration-monitor-cache
    ports:
      - "6379:6379"  # Exposed to host
    # No --requirepass configured
  ```
- **Fix:** Enable Redis authentication and restrict port exposure:
  ```yaml
  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD}
    ports: []  # Remove host port binding; only expose within Docker network
    # Or if host access is needed:
    # ports:
    #   - "127.0.0.1:6379:6379"
  ```

### Finding 6.3 -- PostgreSQL Exposed on Host Network

- **Severity:** MEDIUM
- **File:** `docker-compose.yml`, lines 13-14
- **Description:** PostgreSQL port 5432 is exposed to the host. Combined with the hardcoded weak password (Finding 1.1), any local process or attacker with network access can directly connect to the database.
- **Code Evidence:**
  ```yaml
  ports:
    - "5432:5432"
  ```
- **Fix:** Remove host port binding for production. For development, bind to localhost only:
  ```yaml
  ports:
    - "127.0.0.1:5432:5432"
  ```

### Finding 6.4 -- Server Binds to All Interfaces

- **Severity:** MEDIUM
- **File:** `api/app.py`, line 667; `docker-compose.yml`, line 47
- **Description:** The uvicorn server binds to `0.0.0.0`, accepting connections from any network interface. This is expected inside Docker but dangerous if the application is run directly on a host.
- **Code Evidence:**
  ```python
  # api/app.py line 667
  uvicorn.run(app, host="0.0.0.0", port=8000)

  # docker-compose.yml line 47
  python -m uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload
  ```
- **Fix:** Use environment variables to configure the bind address:
  ```python
  uvicorn.run(app, host=os.getenv("API_HOST", "127.0.0.1"), port=int(os.getenv("API_PORT", "8000")))
  ```

### Finding 6.5 -- Dockerfile Healthcheck References Missing Binary

- **Severity:** LOW
- **File:** `Dockerfile.api`, lines 24-25
- **Description:** The Dockerfile healthcheck uses `curl`, but the base image `python:3.11-slim` does not include curl. The healthcheck will always fail, causing Docker to report the container as unhealthy and potentially triggering restart loops in orchestration systems.
- **Code Evidence:**
  ```dockerfile
  HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
      CMD curl -f http://localhost:8000/health || exit 1
  ```
- **Fix:** Use Python for the healthcheck instead of curl:
  ```dockerfile
  HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
      CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
  ```

### Finding 6.6 -- No Rate Limiting on API Endpoints

- **Severity:** MEDIUM
- **File:** `api/app.py` (all endpoints)
- **Description:** No rate limiting is configured on any endpoint. An attacker can flood the API with requests, overwhelming backend systems, exhausting in-memory data structures, and causing denial of service.
- **Fix:** Add rate limiting middleware:
  ```python
  from slowapi import Limiter
  from slowapi.util import get_remote_address

  limiter = Limiter(key_func=get_remote_address)
  app.state.limiter = limiter

  @app.get("/integrations")
  @limiter.limit("60/minute")
  async def list_integrations(request: Request):
      ...
  ```

---

## 7. Dependency Vulnerabilities

### Finding 7.1 -- Outdated cryptography Package

- **Severity:** MEDIUM
- **File:** `requirements.txt`, line 29
- **Description:** `cryptography==41.0.7` was released in December 2023 and has multiple known CVEs, including memory safety issues in the underlying OpenSSL bindings. Versions 42.x and 43.x contain important security patches.
- **Code Evidence:**
  ```
  cryptography==41.0.7
  ```
- **Fix:** Update to the latest stable version:
  ```
  cryptography>=43.0.0
  ```

### Finding 7.2 -- Outdated aiohttp Package

- **Severity:** MEDIUM
- **File:** `requirements.txt`, line 11
- **Description:** `aiohttp==3.9.1` has known vulnerabilities including HTTP request smuggling and CRLF injection issues that were patched in later 3.9.x releases.
- **Code Evidence:**
  ```
  aiohttp==3.9.1
  ```
- **Fix:** Update to the latest patched version:
  ```
  aiohttp>=3.10.0
  ```

### Finding 7.3 -- General Dependency Pinning Risk

- **Severity:** LOW
- **File:** `requirements.txt`
- **Description:** All dependencies are pinned to exact versions from late 2023 / early 2024. While pinning is good for reproducibility, these specific versions are now over two years old and accumulate unpatched vulnerabilities over time. No automated dependency scanning (e.g., Dependabot, Snyk) appears to be configured.
- **Fix:** Set up automated dependency scanning via GitHub Dependabot or similar. Regularly audit and update dependencies. Consider using version ranges with minimum bounds:
  ```
  fastapi>=0.115.0,<1.0.0
  ```

---

## Remediation Priority

The following remediation order is recommended based on exploitability and impact:

| Priority | Finding | Effort |
|----------|---------|--------|
| 1 | 3.1 -- Add authentication to all API endpoints | Medium |
| 2 | 3.2 -- Implement webhook signature verification | Low |
| 3 | 4.1, 4.2 -- Fix SQL injection in n8n workflows | Medium |
| 4 | 2.1 -- Add SSRF protection to health check URL construction | Medium |
| 5 | 1.1, 1.2 -- Remove hardcoded credentials from docker-compose | Low |
| 6 | 6.1 -- Fix CORS configuration | Low |
| 7 | 6.2 -- Secure Redis with authentication | Low |
| 8 | 3.3 -- Authenticate cron endpoints | Low |
| 9 | 5.3 -- Secure Redis URL in n8n workflow | Low |
| 10 | 6.6 -- Add rate limiting | Medium |
| 11 | 7.1, 7.2 -- Update vulnerable dependencies | Low |
| 12 | Remaining MEDIUM/LOW findings | Low |

---

## Files Reviewed

| File | Findings |
|------|----------|
| `api/app.py` | 1.3, 3.1, 3.2, 5.2, 6.1, 6.4, 6.6 |
| `api/models.py` | 4.3 |
| `docker-compose.yml` | 1.1, 1.2, 5.1, 6.2, 6.3, 6.4 |
| `Dockerfile.api` | 6.5 |
| `trigger-jobs/health_check_batch.ts` | 2.1 |
| `n8n/health_check_loop.json` | 2.2, 4.1, 5.3 |
| `n8n/incident_correlation.json` | 4.2 |
| `vercel.json` | 3.3 |
| `requirements.txt` | 7.1, 7.2, 7.3 |
| `demo/simulate_24h.py` | 1.3 |
| `src/webhook_monitor.py` | (contains unused HMAC helper referenced in 3.2) |
| `dashboard/dashboard.jsx` | 4.4 |
| `observability/instrumentation.py` | No findings |
| `src/integration_registry.py` | No findings |
| `src/api_health_tracker.py` | No findings |
| `src/incident_detector.py` | No findings |
| `src/onboarding_funnel.py` | No findings |
| `src/provider_scorecard.py` | No findings |
| `src/scorecard_report.py` | No findings |
| `schema/schema.sql` | No findings |
| `schema/migrations/001_initial_tables.sql` | No findings |
| `schema/seed.sql` | No findings |
| `.gitignore` | No findings (properly excludes .env) |
| `.env.example` | No findings (contains only placeholders) |
