"""
OpenTelemetry Instrumentation for Integration Health Monitor

Configures automatic instrumentation and custom metrics/spans for:
- Provider API health checks
- Health check execution tracing
- Webhook processing from third-party providers
- Alert evaluation and routing
- Error rate tracking by provider

Specialized for high-volume, distributed health check telemetry.
"""

import os
import time
from typing import Optional, Dict, Any, List
from datetime import datetime

from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.propagators import set_default_propagator
from opentelemetry.propagators.jaeger_composite import JaegerComposite

import logging

logger = logging.getLogger(__name__)


def setup_tracing(
    service_name: str = "integration-health-monitor",
    otlp_endpoint: Optional[str] = None,
) -> TracerProvider:
    """
    Configure tracing for integration health monitoring.
    
    Traces include:
    - Health check execution flows
    - Provider webhook processing
    - Alert evaluation decisions
    """
    
    resource = Resource.create({
        "service.name": service_name,
        "service.namespace": "portfolio-integrations",
        "service.version": os.getenv("SERVICE_VERSION", "1.0.0"),
        "deployment.environment": os.getenv("ENVIRONMENT", "production"),
    })
    
    otlp_endpoint = otlp_endpoint or os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"
    )
    
    trace_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
    trace_provider = TracerProvider(resource=resource)
    trace_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
    
    trace.set_tracer_provider(trace_provider)
    set_default_propagator(JaegerComposite())
    
    logger.info(f"Tracing configured for {service_name}")
    return trace_provider


def setup_metrics(
    service_name: str = "integration-health-monitor",
    otlp_endpoint: Optional[str] = None,
) -> MeterProvider:
    """
    Configure metrics for integration health monitoring.
    
    Custom metrics:
    - api_response_time_ms: Histogram of API response latency by provider
    - health_check_status: Gauge of current health status by provider
    - error_rate_by_provider: Counter of errors per provider
    - alert_notifications_sent: Counter of alert notifications by severity
    - provider_uptime_pct: Gauge of provider uptime percentage
    """
    
    otlp_endpoint = otlp_endpoint or os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"
    )
    
    resource = Resource.create({
        "service.name": service_name,
        "service.namespace": "portfolio-integrations",
        "environment": os.getenv("ENVIRONMENT", "production"),
    })
    
    metric_exporter = OTLPMetricExporter(endpoint=otlp_endpoint)
    metric_reader = PeriodicExportingMetricReader(metric_exporter, interval_millis=30000)
    
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    
    meter = meter_provider.get_meter(__name__)
    
    # Histogram: API response time by provider (in milliseconds)
    # Helps identify slow providers affecting overall health checks
    api_response_time = meter.create_histogram(
        name="api_response_time_ms",
        description="API response time by provider",
        unit="ms",
    )
    
    # Health check execution duration (in milliseconds)
    health_check_duration = meter.create_histogram(
        name="health_check_duration_ms",
        description="Duration of health check execution",
        unit="ms",
    )
    
    # Alert evaluation latency
    alert_eval_latency = meter.create_histogram(
        name="alert_evaluation_latency_ms",
        description="Time to evaluate alert conditions",
        unit="ms",
    )
    
    # Webhook processing latency
    webhook_processing_latency = meter.create_histogram(
        name="webhook_processing_latency_ms",
        description="Time to process provider webhook",
        unit="ms",
    )
    
    # Gauge: Current health status (0=down, 1=degraded, 2=healthy)
    # Helps detect cascading provider failures
    health_check_status = meter.create_gauge(
        name="health_check_status",
        description="Current health status (0=down, 1=degraded, 2=healthy)",
        unit="1",
    )
    
    # Gauge: Provider uptime percentage
    provider_uptime = meter.create_gauge(
        name="provider_uptime_pct",
        description="Provider uptime percentage (0-100)",
        unit="%",
    )
    
    # Counter: Errors by provider and error type
    error_count = meter.create_counter(
        name="errors_total",
        description="Total errors by provider and error type",
        unit="1",
    )
    
    # Counter: Health check failures
    health_check_failures = meter.create_counter(
        name="health_check_failures_total",
        description="Total health check failures by provider",
        unit="1",
    )
    
    # Counter: Alert notifications sent
    alerts_sent = meter.create_counter(
        name="alert_notifications_sent_total",
        description="Total alert notifications sent by severity",
        unit="1",
    )
    
    # Counter: Webhook events received
    webhooks_received = meter.create_counter(
        name="webhook_events_received_total",
        description="Total webhook events received by provider",
        unit="1",
    )
    
    logger.info(f"Metrics configured for {service_name}")
    return meter_provider


class HealthCheckSpans:
    """
    Helper class for creating consistent spans for health check operations.
    Enables correlation of health checks across distributed systems.
    """
    
    def __init__(self):
        self.tracer = trace.get_tracer(__name__)
    
    def health_check_execution_span(
        self,
        provider_name: str,
        provider_id: str,
        check_type: str,
    ):
        """
        Create span for a single provider health check.
        
        Attributes:
        - provider.name: Name of provider (e.g., 'Salesforce')
        - provider.id: Provider identifier
        - health_check.type: Type of check (e.g., 'api_ping', 'synthetic_transaction')
        """
        return self.tracer.start_as_current_span(
            name="health_check.execute",
            attributes={
                "provider.name": provider_name,
                "provider.id": provider_id,
                "health_check.type": check_type,
                "service.name": "integration-health-monitor",
            },
        )
    
    def webhook_processing_span(
        self,
        provider_name: str,
        webhook_type: str,
        event_id: str,
    ):
        """
        Create span for webhook event processing.
        
        Tracks: parsing, validation, persistence of webhook data.
        """
        return self.tracer.start_as_current_span(
            name="webhook.process",
            attributes={
                "provider.name": provider_name,
                "webhook.type": webhook_type,
                "event.id": event_id,
                "operation.type": "webhook",
            },
        )
    
    def alert_evaluation_span(
        self,
        alert_rule_id: str,
        provider_id: str,
        severity: str,
    ):
        """
        Create span for alert evaluation logic.
        
        Tracks: condition evaluation, threshold checks, alert routing decisions.
        """
        return self.tracer.start_as_current_span(
            name="alert.evaluate",
            attributes={
                "alert.rule_id": alert_rule_id,
                "provider.id": provider_id,
                "alert.severity": severity,
                "operation.type": "alert",
            },
        )
    
    def notification_span(
        self,
        alert_id: str,
        channel: str,
        recipient: str,
    ):
        """Create span for alert notification delivery."""
        return self.tracer.start_as_current_span(
            name="notification.send",
            attributes={
                "alert.id": alert_id,
                "notification.channel": channel,  # email, slack, pagerduty, etc
                "notification.recipient": recipient,
                "operation.type": "notification",
            },
        )


class HealthCheckMetricsRecorder:
    """
    Helper class for recording health check metrics consistently.
    Encapsulates attribute tagging and unit handling.
    """
    
    def __init__(self):
        self.meter = metrics.get_meter(__name__)
    
    def record_api_response_time(
        self,
        duration_ms: float,
        provider_name: str,
        provider_id: str,
        provider_tier: str,
        status_code: int,
    ):
        """Record API response time for a provider."""
        response_time_histogram = self.meter._instrument_cache.get(
            ("api_response_time_ms", "histogram")
        )
        if response_time_histogram:
            response_time_histogram.record(
                duration_ms,
                {
                    "provider.name": provider_name,
                    "provider.id": provider_id,
                    "provider.tier": provider_tier,
                    "http.status_code": status_code,
                },
            )
    
    def record_health_status(
        self,
        status: int,  # 0=down, 1=degraded, 2=healthy
        provider_name: str,
        provider_id: str,
        integration_type: str,
    ):
        """Record current health status."""
        status_gauge = self.meter._instrument_cache.get(
            ("health_check_status", "gauge")
        )
        if status_gauge:
            status_gauge.set(
                status,
                {
                    "provider.name": provider_name,
                    "provider.id": provider_id,
                    "integration.type": integration_type,
                },
            )
    
    def record_error(
        self,
        error_type: str,
        provider_id: str,
        provider_name: str,
    ):
        """Increment error counter for a provider."""
        error_counter = self.meter._instrument_cache.get(("errors_total", "counter"))
        if error_counter:
            error_counter.add(
                1,
                {
                    "error.type": error_type,
                    "provider.id": provider_id,
                    "provider.name": provider_name,
                },
            )
    
    def record_alert_sent(
        self,
        severity: str,
        alert_type: str,
        provider_id: str,
    ):
        """Record alert notification."""
        alerts_counter = self.meter._instrument_cache.get(
            ("alert_notifications_sent_total", "counter")
        )
        if alerts_counter:
            alerts_counter.add(
                1,
                {
                    "alert.severity": severity,
                    "alert.type": alert_type,
                    "provider.id": provider_id,
                },
            )


def instrument_fastapi(app):
    """Instrument FastAPI app with telemetry."""
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls=".*healthz,.*ready",
        meter_provider=metrics.get_meter_provider(),
        tracer_provider=trace.get_tracer_provider(),
    )
    logger.info("FastAPI instrumented")


def instrument_http_requests():
    """Instrument outbound HTTP requests."""
    RequestsInstrumentor().instrument(
        tracer_provider=trace.get_tracer_provider(),
    )
    logger.info("HTTP requests instrumented")


def initialize_telemetry(
    service_name: str = "integration-health-monitor",
) -> tuple:
    """
    Initialize complete telemetry stack for health monitoring.
    
    Returns: (tracer_provider, meter_provider)
    """
    
    logger.info(f"Initializing OpenTelemetry for {service_name}")
    
    trace_provider = setup_tracing(service_name)
    meter_provider = setup_metrics(service_name)
    
    instrument_http_requests()
    
    logger.info("OpenTelemetry initialization complete")
    
    return trace_provider, meter_provider


def get_health_check_spans() -> HealthCheckSpans:
    """Get helper for health check spans."""
    return HealthCheckSpans()


def get_health_check_metrics() -> HealthCheckMetricsRecorder:
    """Get helper for health check metrics."""
    return HealthCheckMetricsRecorder()
