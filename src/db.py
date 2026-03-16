"""
Database Layer — SQLAlchemy ORM with connection pooling and Redis caching.

Replaces in-memory storage with persistent database backend.
Uses QueuePool for connection pooling (pool_size=20) and Redis for high-frequency data.

Connection pooling strategy:
- pool_size=20: Pre-allocated connections in the pool
- max_overflow=10: Allow 10 extra connections beyond pool_size
- pool_recycle=3600: Recycle connections after 1 hour to handle DB restarts
- pool_pre_ping=True: Verify connections are alive before using them

Redis is used for:
1. Recent webhook/health event windows (hot path optimization)
2. Session deduplication (event_id tracking)
3. Metrics aggregation (sliding windows)

Database is the source of truth for:
1. All events (webhook, API calls, incidents)
2. Historical data and trends
3. DLQ entries and resolution history
4. Scorecard records
"""

from sqlalchemy import create_engine, Column, String, Integer, Float, Boolean, DateTime, JSON, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.pool import QueuePool
from datetime import datetime, timedelta
import redis
import json
import logging

logger = logging.getLogger("db")

# Database configuration
DATABASE_URL = "sqlite:///./integration_health.db"  # In production: postgresql://user:pass@host:5432/db
REDIS_URL = "redis://localhost:6379/0"  # In production: environment variable

# Create SQLAlchemy base
Base = declarative_base()


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------

class WebhookEventModel(Base):
    """Persisted webhook event."""
    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True)
    event_id = Column(String(255), unique=True, nullable=False, index=True)
    provider_id = Column(String(100), nullable=False, index=True)
    event_type = Column(String(255), nullable=False)
    received_at = Column(DateTime, nullable=False, index=True)
    provider_timestamp = Column(DateTime, nullable=True)
    payload_size_bytes = Column(Integer, default=0)
    signature_valid = Column(Boolean, default=True)
    status = Column(String(50), nullable=False)
    processing_time_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    dlq_entry = relationship("DeadLetterQueueModel", uselist=False, back_populates="webhook_event")


class DeadLetterQueueModel(Base):
    """Dead letter queue entry."""
    __tablename__ = "dead_letter_queue"

    id = Column(Integer, primary_key=True)
    event_id = Column(String(255), ForeignKey("webhook_events.event_id"), unique=True, nullable=False)
    provider_id = Column(String(100), nullable=False, index=True)
    event_type = Column(String(255), nullable=False)
    first_received_at = Column(DateTime, nullable=False)
    last_attempt_at = Column(DateTime, nullable=False)
    total_attempts = Column(Integer, default=1)
    last_error = Column(Text, nullable=False)
    raw_payload = Column(JSON, default={})
    resolved = Column(Boolean, default=False, index=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    webhook_event = relationship("WebhookEventModel", back_populates="dlq_entry")


class APICallEventModel(Base):
    """Persisted API call event."""
    __tablename__ = "api_call_events"

    id = Column(Integer, primary_key=True)
    provider_id = Column(String(100), nullable=False, index=True)
    endpoint = Column(String(500), nullable=False)
    method = Column(String(10), nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    response_status_code = Column(Integer, nullable=False)
    latency_ms = Column(Float, nullable=False)
    success = Column(Boolean, nullable=False, index=True)
    error_category = Column(String(50), nullable=True)
    retry_attempt = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class HealthSnapshotModel(Base):
    """Point-in-time health snapshot."""
    __tablename__ = "health_snapshots"

    id = Column(Integer, primary_key=True)
    provider_id = Column(String(100), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    window_seconds = Column(Integer, nullable=False)

    # Latency percentiles
    latency_p50_ms = Column(Float, default=0)
    latency_p95_ms = Column(Float, default=0)
    latency_p99_ms = Column(Float, default=0)
    latency_max_ms = Column(Float, default=0)

    # Error rates
    total_requests = Column(Integer, default=0)
    successful_requests = Column(Integer, default=0)
    failed_requests = Column(Integer, default=0)
    error_rate_pct = Column(Float, default=0)
    errors_by_category = Column(JSON, default={})
    errors_by_status_code = Column(JSON, default={})

    # Circuit breaker
    circuit_state = Column(String(20), default="closed")
    health_status = Column(String(20), default="unknown")
    requests_per_minute = Column(Float, default=0)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class AnomalyReadingModel(Base):
    """Anomaly reading for incident detection."""
    __tablename__ = "anomaly_readings"

    id = Column(Integer, primary_key=True)
    provider_id = Column(String(100), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    metric_name = Column(String(255), nullable=False)
    current_value = Column(Float, nullable=False)
    baseline_value = Column(Float, nullable=False)
    threshold_value = Column(Float, nullable=False)
    is_anomalous = Column(Boolean, default=False, index=True)
    anomaly_type = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class IncidentModel(Base):
    """Persisted incident."""
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True)
    incident_id = Column(String(255), unique=True, nullable=False, index=True)
    provider_id = Column(String(100), nullable=False, index=True)
    provider_name = Column(String(255), nullable=False)
    anomaly_type = Column(String(100), nullable=False)
    severity = Column(String(20), nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True)

    # Detection
    detected_at = Column(DateTime, nullable=False, index=True)
    detection_rule = Column(String(255), nullable=False)
    current_value = Column(Float, nullable=False)
    baseline_value = Column(Float, nullable=False)
    threshold_value = Column(Float, nullable=False)

    # Impact
    affected_flows = Column(JSON, default=[])
    blast_radius = Column(String(100), nullable=False)
    estimated_users_affected = Column(Integer, default=0)

    # Response timeline
    acknowledged_at = Column(DateTime, nullable=True)
    acknowledged_by = Column(String(255), nullable=True)
    mitigated_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_notes = Column(Text, default="")

    # Alert routing
    alert_channels = Column(JSON, default=[])

    # Timeline events
    timeline = Column(JSON, default=[])

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class UptimeRecordModel(Base):
    """Provider uptime record for scorecard."""
    __tablename__ = "uptime_records"

    id = Column(Integer, primary_key=True)
    provider_id = Column(String(100), nullable=False, index=True)
    period_start = Column(DateTime, nullable=False, index=True)
    period_end = Column(DateTime, nullable=False)
    total_minutes = Column(Float, nullable=False)
    available_minutes = Column(Float, nullable=False)
    downtime_minutes = Column(Float, default=0)
    incident_count = Column(Integer, default=0)
    uptime_pct = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class IncidentRecordModel(Base):
    """Simplified incident record for scorecard."""
    __tablename__ = "incident_records"

    id = Column(Integer, primary_key=True)
    provider_id = Column(String(100), nullable=False, index=True)
    incident_id = Column(String(255), nullable=False, unique=True)
    occurred_at = Column(DateTime, nullable=False, index=True)
    duration_minutes = Column(Float, nullable=False)
    severity = Column(String(50), nullable=False)
    root_cause = Column(Text, nullable=False)
    appeared_on_status_page = Column(Boolean, default=False)
    mttr_minutes = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class LatencyRecordModel(Base):
    """Latency snapshot for scorecard."""
    __tablename__ = "latency_records"

    id = Column(Integer, primary_key=True)
    provider_id = Column(String(100), nullable=False, index=True)
    period_start = Column(DateTime, nullable=False, index=True)
    period_end = Column(DateTime, nullable=False)
    p50_ms = Column(Float, nullable=False)
    p95_ms = Column(Float, nullable=False)
    p99_ms = Column(Float, nullable=False)
    sample_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class WebhookReliabilityRecordModel(Base):
    """Webhook delivery reliability record for scorecard."""
    __tablename__ = "webhook_reliability_records"

    id = Column(Integer, primary_key=True)
    provider_id = Column(String(100), nullable=False, index=True)
    period_start = Column(DateTime, nullable=False, index=True)
    period_end = Column(DateTime, nullable=False)
    expected_deliveries = Column(Integer, nullable=False)
    actual_deliveries = Column(Integer, nullable=False)
    delivery_rate_pct = Column(Float, nullable=False)
    avg_delivery_latency_ms = Column(Float, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class CostRecordModel(Base):
    """Cost data for scorecard."""
    __tablename__ = "cost_records"

    id = Column(Integer, primary_key=True)
    provider_id = Column(String(100), nullable=False, index=True)
    period_start = Column(DateTime, nullable=False, index=True)
    period_end = Column(DateTime, nullable=False)
    total_cost = Column(Float, nullable=False)
    total_api_calls = Column(Integer, nullable=False)
    successful_calls = Column(Integer, nullable=False)
    failed_calls = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class NormalizedEventModel(Base):
    """Normalized webhook event from receiver."""
    __tablename__ = "normalized_events"

    id = Column(Integer, primary_key=True)
    event_id = Column(String(255), unique=True, nullable=False, index=True)
    provider_id = Column(String(100), nullable=False, index=True)
    event_type = Column(String(255), nullable=False)
    provider_timestamp = Column(DateTime, nullable=True)
    received_at = Column(DateTime, nullable=False, index=True)
    payload_size_bytes = Column(Integer, default=0)
    signature_valid = Column(Boolean, default=True)
    raw_payload = Column(JSON, default={})
    metadata = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ---------------------------------------------------------------------------
# Database Initialization
# ---------------------------------------------------------------------------

def init_database(database_url: str = DATABASE_URL) -> sessionmaker:
    """Initialize database with connection pooling.

    Args:
        database_url: Database connection URL

    Returns:
        Session factory bound to the engine
    """
    engine = create_engine(
        database_url,
        poolclass=QueuePool,
        pool_size=20,           # Pre-allocated connections
        max_overflow=10,        # Allow 10 extra connections
        pool_recycle=3600,      # Recycle connections after 1 hour
        pool_pre_ping=True,     # Verify connection health before use
        echo=False              # Set to True for SQL debugging
    )

    # Create all tables
    Base.metadata.create_all(engine)

    # Return session factory
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal


# ---------------------------------------------------------------------------
# Redis Connection
# ---------------------------------------------------------------------------

class RedisClient:
    """Redis client for caching and session tracking."""

    def __init__(self, redis_url: str = REDIS_URL):
        """Initialize Redis connection.

        Args:
            redis_url: Redis connection URL
        """
        try:
            self.client = redis.from_url(redis_url, decode_responses=True)
            self.client.ping()
            logger.info("Redis connection established")
        except Exception as e:
            logger.warning(f"Redis unavailable: {e}. Continuing without caching.")
            self.client = None

    def set(self, key: str, value: str, ttl: int = 3600) -> bool:
        """Set a key in Redis.

        Args:
            key: Redis key
            value: Value (JSON string)
            ttl: Time-to-live in seconds

        Returns:
            True if set, False if Redis unavailable
        """
        if not self.client:
            return False
        try:
            self.client.setex(key, ttl, value)
            return True
        except Exception as e:
            logger.error(f"Redis set failed: {e}")
            return False

    def get(self, key: str) -> str | None:
        """Get a value from Redis.

        Args:
            key: Redis key

        Returns:
            Value if found, None otherwise
        """
        if not self.client:
            return None
        try:
            return self.client.get(key)
        except Exception as e:
            logger.error(f"Redis get failed: {e}")
            return None

    def delete(self, key: str) -> bool:
        """Delete a key from Redis.

        Args:
            key: Redis key

        Returns:
            True if deleted, False otherwise
        """
        if not self.client:
            return False
        try:
            self.client.delete(key)
            return True
        except Exception as e:
            logger.error(f"Redis delete failed: {e}")
            return False

    def add_to_set(self, key: str, value: str, ttl: int = 3600) -> bool:
        """Add value to a Redis set.

        Args:
            key: Redis key
            value: Value to add
            ttl: Time-to-live in seconds

        Returns:
            True if added, False otherwise
        """
        if not self.client:
            return False
        try:
            self.client.sadd(key, value)
            self.client.expire(key, ttl)
            return True
        except Exception as e:
            logger.error(f"Redis add_to_set failed: {e}")
            return False

    def is_member(self, key: str, value: str) -> bool:
        """Check if value is in a Redis set.

        Args:
            key: Redis key
            value: Value to check

        Returns:
            True if member, False otherwise
        """
        if not self.client:
            return False
        try:
            return self.client.sismember(key, value)
        except Exception as e:
            logger.error(f"Redis is_member failed: {e}")
            return False


def init_redis(redis_url: str = REDIS_URL) -> RedisClient:
    """Initialize Redis client.

    Args:
        redis_url: Redis connection URL

    Returns:
        RedisClient instance
    """
    return RedisClient(redis_url)
