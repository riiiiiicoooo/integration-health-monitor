# Integration Health Monitor - OpenTelemetry Observability

Comprehensive telemetry for monitoring third-party API integrations, provider health, and alert routing. Supports high-volume webhook events and distributed health check tracing.

## Overview

Observes:
- **Health Checks**: API response times, status codes, latency per provider
- **Webhook Events**: Provider health updates, webhook parsing/processing
- **Alerts**: Alert evaluation, routing decisions, notification delivery
- **Integration Health**: Provider uptime, error rates, cascading failures

## Files

- **otel_config.yaml**: Collector config with webhook receiver and health check filtering
- **instrumentation.py**: Custom spans and metrics for health monitoring
- **README.md**: This file

## Setup

### 1. Initialize Telemetry

```python
from observability.instrumentation import (
    initialize_telemetry,
    instrument_fastapi,
    get_health_check_spans,
    get_health_check_metrics,
)

# In application startup:
trace_provider, meter_provider = initialize_telemetry()
instrument_fastapi(app)

# For use throughout application:
spans = get_health_check_spans()
metrics = get_health_check_metrics()
```

### 2. Record Health Check Metrics

```python
from observability.instrumentation import get_health_check_metrics

metrics = get_health_check_metrics()

# After executing health check:
start_time = time.time()
response = check_provider_health("salesforce")
duration_ms = (time.time() - start_time) * 1000

metrics.record_api_response_time(
    duration_ms=duration_ms,
    provider_name="Salesforce",
    provider_id="salesforce-prod",
    provider_tier="tier1",
    status_code=response.status_code,
)

# Update status gauge:
health_status = 2 if response.ok else 1  # 2=healthy, 1=degraded
metrics.record_health_status(
    status=health_status,
    provider_name="Salesforce",
    provider_id="salesforce-prod",
    integration_type="crm",
)
```

### 3. Create Health Check Spans

```python
from observability.instrumentation import get_health_check_spans

spans = get_health_check_spans()

# Wrap health check execution:
with spans.health_check_execution_span(
    provider_name="Salesforce",
    provider_id="salesforce-prod",
    check_type="api_ping",
) as span:
    response = requests.get("https://api.salesforce.com/health")
    span.set_attribute("http.status_code", response.status_code)
    span.set_attribute("response.time_ms", response.elapsed.total_seconds() * 1000)
```

### 4. Process Webhooks

```python
# When receiving webhook from provider:
with spans.webhook_processing_span(
    provider_name="Salesforce",
    webhook_type="maintenance_notification",
    event_id=request.headers.get("X-Event-ID"),
) as span:
    # Parse and validate webhook
    payload = request.json()
    span.set_attribute("webhook.status", payload.get("status"))
    
    metrics.record_alert_sent(
        severity="warning",
        alert_type="maintenance",
        provider_id="salesforce-prod",
    )
```

### 5. Alert Evaluation

```python
with spans.alert_evaluation_span(
    alert_rule_id="rule-uptime-threshold",
    provider_id="salesforce-prod",
    severity="critical",
) as span:
    # Evaluate alert conditions
    uptime = calculate_provider_uptime("salesforce-prod")
    span.set_attribute("uptime_pct", uptime)
    
    if uptime < 95:
        metrics.record_alert_sent(
            severity="critical",
            alert_type="uptime_threshold",
            provider_id="salesforce-prod",
        )
        
        with spans.notification_span(
            alert_id=f"alert-{provider_id}-{int(time.time())}",
            channel="slack",
            recipient="platform-team",
        ):
            # Send notification
            pass
```

## Metrics Reference

### Histograms (Latency)

- **api_response_time_ms**: Response time per provider (tags: provider.name, provider.tier, http.status_code)
- **health_check_duration_ms**: Duration of health check execution
- **alert_evaluation_latency_ms**: Time to evaluate alert conditions
- **webhook_processing_latency_ms**: Time to process webhook

### Gauges (Current State)

- **health_check_status**: Current status per provider (0=down, 1=degraded, 2=healthy)
- **provider_uptime_pct**: Current uptime percentage

### Counters (Cumulative)

- **errors_total**: Errors by provider and type
- **health_check_failures_total**: Failed checks per provider
- **alert_notifications_sent_total**: Alerts sent by severity
- **webhook_events_received_total**: Webhooks received per provider

## Webhook Integration

The collector has a webhook receiver listening on `:8888/health-webhook`:

```bash
curl -X POST http://localhost:8888/health-webhook \
  -H "Content-Type: application/json" \
  -d '{
    "provider_id": "salesforce-prod",
    "provider_name": "Salesforce",
    "status": "degraded",
    "message": "Scheduled maintenance in progress"
  }'
```

This enables:
- Providers to push health updates directly (lower latency)
- Real-time status notifications
- Reduction in polling-based health checks

## Span Context Propagation

The W3C Trace Context standard is used for distributed tracing:

```python
# Propagate trace context in outbound requests:
from opentelemetry.propagate import inject
import requests

headers = {}
inject(headers)  # Adds traceparent, tracestate headers
response = requests.get("https://api.provider.com/health", headers=headers)
```

Enables:
- Correlation across client → health-monitor → provider
- End-to-end latency tracking
- Root cause analysis for cascading failures

## Dashboards

### Key Metrics to Monitor

1. **Provider Response Times**: Alert if p99 > 5 seconds
2. **Error Rates**: Alert if error_rate > 1%
3. **Uptime**: Alert if < 99.5% (SLA threshold)
4. **Health Check Failures**: Alert if consecutive failures > 3
5. **Alert Notification Latency**: Monitor end-to-end alert delivery time

### Grafana Dashboard Queries

```promql
# Provider uptime over time
provider_uptime_pct{integration_type="crm"}

# Error rate by provider
rate(errors_total{provider_id="salesforce-prod"}[5m])

# P95 response time
histogram_quantile(0.95, api_response_time_ms)

# Alert notification rate
rate(alert_notifications_sent_total[5m])
```

## Troubleshooting

### Webhooks Not Appearing in Metrics

1. Verify webhook receiver is configured in otel_config.yaml
2. Check POST to `/health-webhook` is successful (HTTP 200)
3. Verify `webhooks_received_total` counter is incrementing

### Missing Provider Metrics

1. Ensure health check code calls `metrics.record_*()` methods
2. Check provider_id/provider_name are set correctly
3. Verify OTLP exporter credentials in docker-compose

### High Memory Usage

Webhook processing creates high-volume telemetry. The `filter/health_check` processor:
- Drops healthy status pings (except 1 in 100 sample)
- Keeps all error/warning status updates
- Reduces exported volume by ~90%

To adjust, modify the filter rules in otel_config.yaml.

## References

- [OpenTelemetry Webhook Receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/webhookreceiver)
- [W3C Trace Context](https://www.w3.org/TR/trace-context/)
- [OpenTelemetry Metrics](https://opentelemetry.io/docs/reference/specification/metrics/)
