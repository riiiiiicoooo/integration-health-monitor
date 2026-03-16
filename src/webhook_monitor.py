"""
Webhook Monitor — Tracks delivery reliability for every webhook-based provider.

PM-authored reference implementation. Built because webhook failures were the
#1 source of customer-facing issues across all four client engagements, and
they were completely invisible until this module existed.

The Plaid incident was the trigger: webhook delivery dropped to 84% for two
weeks and nobody noticed. Bank linking was silently breaking. Support tickets
went up. Nobody connected the dots until we built this.

Key insight: synchronous API failures are loud (they throw errors). Webhook
failures are silent (the data just doesn't show up). You need a dedicated
monitoring system that tracks what SHOULD have arrived vs. what DID arrive.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from collections import defaultdict
from typing import Optional
import statistics
import hashlib
import hmac
from sqlalchemy.orm import Session
from sqlalchemy import desc, and_
import logging

logger = logging.getLogger("webhook_monitor")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class WebhookStatus(Enum):
    """Delivery status of a single webhook event."""
    RECEIVED = "received"              # Arrived and acknowledged (200)
    PROCESSING = "processing"          # Received, being handled
    PROCESSED = "processed"            # Successfully processed
    FAILED_VALIDATION = "failed_validation"    # Signature verification failed
    FAILED_PROCESSING = "failed_processing"    # Our handler threw an error
    DEAD_LETTERED = "dead_lettered"    # Max retries exhausted, moved to DLQ
    DUPLICATE = "duplicate"            # Already processed (idempotency check)


class DeliveryTrend(Enum):
    """Direction of webhook delivery rate change."""
    STABLE = "stable"
    IMPROVING = "improving"
    DEGRADING = "degrading"
    CRITICAL = "critical"              # Below 70% — incident threshold


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class WebhookEvent:
    """A single webhook event received from a provider."""
    event_id: str                      # Provider's event ID (for dedup)
    provider_id: str                   # Which provider sent this
    event_type: str                    # e.g., "payment_intent.succeeded"
    received_at: datetime              # When our receiver got it
    provider_timestamp: Optional[datetime]  # When the provider says it happened
    payload_size_bytes: int
    signature_valid: bool
    status: WebhookStatus
    processing_time_ms: Optional[int] = None   # How long our handler took
    error_message: Optional[str] = None
    retry_count: int = 0               # How many times the provider retried


@dataclass
class DeliveryGap:
    """A detected gap in webhook delivery — events we expected but didn't get.

    These are the silent failures. The provider generated events, but we
    never received the webhooks. Detected by comparing expected volume
    against actual received volume.
    """
    provider_id: str
    gap_start: datetime
    gap_end: Optional[datetime]        # None = gap is still open
    expected_events: int               # How many we expected in this window
    received_events: int               # How many we actually got
    delivery_rate_pct: float           # received / expected * 100
    severity: str                      # "warning" or "critical"
    affected_event_types: list[str]    # Which event types are missing


@dataclass
class DeadLetterEntry:
    """An event that exhausted all retries and couldn't be processed.

    Dead letter queue entries need manual intervention or automated
    backfill from the provider's API.
    """
    event_id: str
    provider_id: str
    event_type: str
    first_received_at: datetime
    last_attempt_at: datetime
    total_attempts: int
    last_error: str
    raw_payload: dict                  # Stored for manual reprocessing
    resolved: bool = False
    resolved_at: Optional[datetime] = None
    resolution_notes: str = ""


# ---------------------------------------------------------------------------
# Webhook Monitor
# ---------------------------------------------------------------------------

class WebhookMonitor:
    """Monitors webhook delivery reliability across all providers.

    Core responsibilities:
    1. Track every webhook event (received, failed, duplicate)
    2. Detect delivery gaps (expected vs. actual volume)
    3. Manage dead letter queue (events that can't be processed)
    4. Calculate delivery rates and trends per provider
    5. Provide data for the dashboard and incident_detector

    Persistence:
    - Uses database for all events and DLQ entries
    - Uses Redis for recent event deduplication (TTL: 24h)
    - In-memory cache for expected_volume (static configuration)
    """

    def __init__(self, session_factory: Optional[object] = None, redis_client: Optional[object] = None):
        self.session_factory = session_factory
        self.redis_client = redis_client
        self._expected_volume: dict[str, int] = {}       # provider_id -> expected/hour (cached)

    # -- Configuration ------------------------------------------------------

    def set_expected_volume(self, provider_id: str, events_per_hour: int) -> None:
        """Set the baseline expected webhook volume for a provider.

        This comes from the WebhookConfig in the integration registry.
        Used to detect delivery gaps — if we expect 200 events/hour
        from Plaid and only get 120, something is wrong.
        """
        self._expected_volume[provider_id] = events_per_hour

    # -- Event Ingestion ----------------------------------------------------

    def record_event(self, event: WebhookEvent) -> WebhookEvent:
        """Record a webhook event and apply processing logic.

        This is called by the webhook_receiver for every incoming webhook.
        Returns the event with updated status after dedup and validation.
        """
        # Idempotency check using Redis (24h window)
        redis_key = f"webhook:event_id:{event.event_id}"
        if self.redis_client and self.redis_client.get(redis_key):
            event.status = WebhookStatus.DUPLICATE
        else:
            # Track in Redis with 24h TTL
            if self.redis_client:
                self.redis_client.set(redis_key, event.received_at.isoformat(), ttl=86400)

            # Signature validation
            if not event.signature_valid:
                event.status = WebhookStatus.FAILED_VALIDATION
            else:
                # Valid and not duplicate
                event.status = WebhookStatus.RECEIVED

        # Persist to database
        if self.session_factory:
            try:
                from db import WebhookEventModel
                session = self.session_factory()
                db_event = WebhookEventModel(
                    event_id=event.event_id,
                    provider_id=event.provider_id,
                    event_type=event.event_type,
                    received_at=event.received_at,
                    provider_timestamp=event.provider_timestamp,
                    payload_size_bytes=event.payload_size_bytes,
                    signature_valid=event.signature_valid,
                    status=event.status.value,
                    processing_time_ms=event.processing_time_ms,
                    error_message=event.error_message,
                    retry_count=event.retry_count,
                )
                session.add(db_event)
                session.commit()
                session.close()
            except Exception as e:
                logger.error(f"Failed to persist webhook event: {e}")

        return event

    def mark_processed(
        self, event_id: str, processing_time_ms: int
    ) -> None:
        """Mark an event as successfully processed by our handler."""
        if not self.session_factory:
            raise RuntimeError("Database session factory not initialized")

        try:
            from db import WebhookEventModel
            session = self.session_factory()
            event = session.query(WebhookEventModel).filter_by(event_id=event_id).first()
            if event and event.status != WebhookStatus.DUPLICATE.value:
                event.status = WebhookStatus.PROCESSED.value
                event.processing_time_ms = processing_time_ms
                session.commit()
                session.close()
            else:
                session.close()
                raise KeyError(f"Event '{event_id}' not found or already finalized.")
        except Exception as e:
            logger.error(f"Failed to mark event as processed: {e}")
            raise

    def mark_failed(
        self, event_id: str, error_message: str, send_to_dlq: bool = False
    ) -> None:
        """Mark an event as failed during processing.

        If send_to_dlq is True, the event is added to the dead letter queue
        for manual intervention.
        """
        if not self.session_factory:
            raise RuntimeError("Database session factory not initialized")

        try:
            from db import WebhookEventModel, DeadLetterQueueModel
            session = self.session_factory()
            event = session.query(WebhookEventModel).filter_by(event_id=event_id).first()

            if event and event.status != WebhookStatus.DUPLICATE.value:
                event.status = WebhookStatus.FAILED_PROCESSING.value
                event.error_message = error_message

                if send_to_dlq:
                    event.status = WebhookStatus.DEAD_LETTERED.value
                    dlq_entry = DeadLetterQueueModel(
                        event_id=event.event_id,
                        provider_id=event.provider_id,
                        event_type=event.event_type,
                        first_received_at=event.received_at,
                        last_attempt_at=datetime.now(),
                        total_attempts=event.retry_count + 1,
                        last_error=error_message,
                        raw_payload={},
                    )
                    session.add(dlq_entry)

                session.commit()
                session.close()
            else:
                session.close()
                raise KeyError(f"Event '{event_id}' not found or already finalized.")
        except Exception as e:
            logger.error(f"Failed to mark event as failed: {e}")
            raise

    # -- Delivery Rate Calculation ------------------------------------------

    def get_delivery_rate(
        self, provider_id: str, window_hours: int = 1
    ) -> dict:
        """Calculate webhook delivery rate for a provider over a time window.

        Delivery rate = successfully received events / expected events.
        This is the core metric for detecting silent webhook failures.
        """
        if not self.session_factory:
            raise RuntimeError("Database session factory not initialized")

        try:
            from db import WebhookEventModel
            session = self.session_factory()
            cutoff = datetime.now() - timedelta(hours=window_hours)

            provider_events = session.query(WebhookEventModel).filter(
                and_(
                    WebhookEventModel.provider_id == provider_id,
                    WebhookEventModel.received_at >= cutoff
                )
            ).all()

            total_received = len(provider_events)
            valid_received = len([
                e for e in provider_events
                if e.status not in (
                    WebhookStatus.FAILED_VALIDATION.value,
                    WebhookStatus.DUPLICATE.value
                )
            ])
            failed = len([
                e for e in provider_events
                if e.status in (
                    WebhookStatus.FAILED_VALIDATION.value,
                    WebhookStatus.FAILED_PROCESSING.value,
                    WebhookStatus.DEAD_LETTERED.value,
                )
            ])
            duplicates = len([
                e for e in provider_events
                if e.status == WebhookStatus.DUPLICATE.value
            ])

            session.close()

            expected = self._expected_volume.get(provider_id, 0) * window_hours
            delivery_rate = (valid_received / expected * 100) if expected > 0 else 0.0

            return {
                "provider_id": provider_id,
                "window_hours": window_hours,
                "expected_events": expected,
                "total_received": total_received,
                "valid_received": valid_received,
                "failed": failed,
                "duplicates": duplicates,
                "delivery_rate_pct": round(delivery_rate, 2),
                "is_healthy": delivery_rate >= 95.0,
                "is_critical": delivery_rate < 70.0,
            }
        except Exception as e:
            logger.error(f"Failed to get delivery rate: {e}")
            raise

    def get_delivery_rates_all_providers(
        self, window_hours: int = 1
    ) -> list[dict]:
        """Get delivery rates for all providers with expected volume configured."""
        return [
            self.get_delivery_rate(pid, window_hours)
            for pid in self._expected_volume
        ]

    # -- Delivery Gap Detection ---------------------------------------------

    def detect_gaps(
        self,
        provider_id: str,
        check_interval_minutes: int = 15,
        lookback_hours: int = 4,
    ) -> list[DeliveryGap]:
        """Detect gaps in webhook delivery by checking volume in time windows.

        Divides the lookback period into intervals and checks whether each
        interval received the expected number of events. Consecutive intervals
        below threshold are merged into a single gap.

        Returns new gaps detected (does not include previously detected gaps).
        """
        if not self.session_factory:
            raise RuntimeError("Database session factory not initialized")

        if provider_id not in self._expected_volume:
            return []

        expected_per_interval = (
            self._expected_volume[provider_id]
            * check_interval_minutes / 60
        )
        if expected_per_interval == 0:
            return []

        try:
            from db import WebhookEventModel
            session = self.session_factory()

            now = datetime.now()
            lookback_start = now - timedelta(hours=lookback_hours)

            new_gaps = []
            current_gap_start = None
            current_gap_expected = 0
            current_gap_received = 0

            interval = timedelta(minutes=check_interval_minutes)
            window_start = lookback_start

            while window_start < now:
                window_end = min(window_start + interval, now)

                received = len(
                    session.query(WebhookEventModel).filter(
                        and_(
                            WebhookEventModel.provider_id == provider_id,
                            WebhookEventModel.received_at >= window_start,
                            WebhookEventModel.received_at < window_end,
                            ~WebhookEventModel.status.in_([
                                WebhookStatus.FAILED_VALIDATION.value,
                                WebhookStatus.DUPLICATE.value,
                            ])
                        )
                    ).all()
                )

                rate = received / expected_per_interval * 100 if expected_per_interval > 0 else 100

                if rate < 70:  # Below critical threshold
                    if current_gap_start is None:
                        current_gap_start = window_start
                    current_gap_expected += int(expected_per_interval)
                    current_gap_received += received
                else:
                    # Close any open gap
                    if current_gap_start is not None:
                        gap_rate = (
                            current_gap_received / current_gap_expected * 100
                            if current_gap_expected > 0 else 0
                        )
                        new_gaps.append(DeliveryGap(
                            provider_id=provider_id,
                            gap_start=current_gap_start,
                            gap_end=window_end,
                            expected_events=current_gap_expected,
                            received_events=current_gap_received,
                            delivery_rate_pct=round(gap_rate, 2),
                            severity="critical" if gap_rate < 50 else "warning",
                            affected_event_types=self._get_event_types_in_window(
                                provider_id, current_gap_start, window_end, session
                            ),
                        ))
                        current_gap_start = None
                        current_gap_expected = 0
                        current_gap_received = 0

                window_start = window_end

            # Handle gap that extends to current time (still open)
            if current_gap_start is not None:
                gap_rate = (
                    current_gap_received / current_gap_expected * 100
                    if current_gap_expected > 0 else 0
                )
                new_gaps.append(DeliveryGap(
                    provider_id=provider_id,
                    gap_start=current_gap_start,
                    gap_end=None,  # Still open
                    expected_events=current_gap_expected,
                    received_events=current_gap_received,
                    delivery_rate_pct=round(gap_rate, 2),
                    severity="critical" if gap_rate < 50 else "warning",
                    affected_event_types=self._get_event_types_in_window(
                        provider_id, current_gap_start, now, session
                    ),
                ))

            session.close()
            return new_gaps
        except Exception as e:
            logger.error(f"Failed to detect gaps: {e}")
            raise

    def _get_event_types_in_window(
        self, provider_id: str, start: datetime, end: datetime, session: Optional[object] = None
    ) -> list[str]:
        """Get unique event types received in a time window."""
        if session is None:
            if not self.session_factory:
                return []
            session = self.session_factory()
            should_close = True
        else:
            should_close = False

        try:
            from db import WebhookEventModel
            events = session.query(WebhookEventModel.event_type).filter(
                and_(
                    WebhookEventModel.provider_id == provider_id,
                    WebhookEventModel.received_at >= start,
                    WebhookEventModel.received_at < end
                )
            ).distinct().all()
            return [e[0] for e in events]
        finally:
            if should_close:
                session.close()

    # -- Dead Letter Queue --------------------------------------------------

    def get_dlq_entries(
        self,
        provider_id: Optional[str] = None,
        unresolved_only: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DeadLetterEntry]:
        """Get dead letter queue entries, optionally filtered.

        Args:
            provider_id: Filter by provider
            unresolved_only: Only unresolved entries
            limit: Maximum number of entries to return
            offset: Pagination offset

        Returns:
            List of DeadLetterEntry objects
        """
        if not self.session_factory:
            raise RuntimeError("Database session factory not initialized")

        try:
            from db import DeadLetterQueueModel
            session = self.session_factory()
            query = session.query(DeadLetterQueueModel)

            if provider_id:
                query = query.filter_by(provider_id=provider_id)
            if unresolved_only:
                query = query.filter_by(resolved=False)

            entries = query.order_by(desc(DeadLetterQueueModel.created_at)).limit(limit).offset(offset).all()
            session.close()

            return [
                DeadLetterEntry(
                    event_id=e.event_id,
                    provider_id=e.provider_id,
                    event_type=e.event_type,
                    first_received_at=e.first_received_at,
                    last_attempt_at=e.last_attempt_at,
                    total_attempts=e.total_attempts,
                    last_error=e.last_error,
                    raw_payload=e.raw_payload,
                    resolved=e.resolved,
                    resolved_at=e.resolved_at,
                    resolution_notes=e.resolution_notes,
                )
                for e in entries
            ]
        except Exception as e:
            logger.error(f"Failed to get DLQ entries: {e}")
            raise

    def resolve_dlq_entry(
        self, event_id: str, resolution_notes: str
    ) -> None:
        """Mark a DLQ entry as resolved after manual intervention."""
        if not self.session_factory:
            raise RuntimeError("Database session factory not initialized")

        try:
            from db import DeadLetterQueueModel
            session = self.session_factory()
            entry = session.query(DeadLetterQueueModel).filter(
                and_(
                    DeadLetterQueueModel.event_id == event_id,
                    DeadLetterQueueModel.resolved == False
                )
            ).first()

            if entry:
                entry.resolved = True
                entry.resolved_at = datetime.now()
                entry.resolution_notes = resolution_notes
                session.commit()
                session.close()
            else:
                session.close()
                raise KeyError(f"Unresolved DLQ entry '{event_id}' not found.")
        except Exception as e:
            logger.error(f"Failed to resolve DLQ entry: {e}")
            raise

    def get_dlq_summary(self) -> dict:
        """Summary of dead letter queue status for the dashboard."""
        if not self.session_factory:
            raise RuntimeError("Database session factory not initialized")

        try:
            from db import DeadLetterQueueModel
            session = self.session_factory()

            unresolved = session.query(DeadLetterQueueModel).filter_by(resolved=False).all()
            resolved = session.query(DeadLetterQueueModel).filter_by(resolved=True).all()

            by_provider = defaultdict(int)
            oldest_unresolved = None

            for entry in unresolved:
                by_provider[entry.provider_id] += 1
                if oldest_unresolved is None or entry.first_received_at < oldest_unresolved:
                    oldest_unresolved = entry.first_received_at

            session.close()

            return {
                "total_unresolved": len(unresolved),
                "total_resolved": len(resolved),
                "by_provider": dict(by_provider),
                "oldest_unresolved": oldest_unresolved,
                "oldest_age_hours": (
                    (datetime.now() - oldest_unresolved).total_seconds() / 3600
                    if oldest_unresolved else 0
                ),
            }
        except Exception as e:
            logger.error(f"Failed to get DLQ summary: {e}")
            raise

    # -- Trend Analysis -----------------------------------------------------

    def get_delivery_trend(
        self,
        provider_id: str,
        window_hours: int = 24,
        intervals: int = 6,
    ) -> dict:
        """Calculate delivery rate trend over time.

        Splits the window into equal intervals and calculates delivery
        rate for each. Used by the dashboard to show trend lines and
        by incident_detector to detect gradual degradation.
        """
        if not self.session_factory:
            raise RuntimeError("Database session factory not initialized")

        try:
            from db import WebhookEventModel
            session = self.session_factory()

            interval_hours = window_hours / intervals
            rates = []
            now = datetime.now()

            for i in range(intervals):
                interval_end = now - timedelta(hours=i * interval_hours)
                interval_start = interval_end - timedelta(hours=interval_hours)

                events_in_interval = session.query(WebhookEventModel).filter(
                    and_(
                        WebhookEventModel.provider_id == provider_id,
                        WebhookEventModel.received_at >= interval_start,
                        WebhookEventModel.received_at < interval_end,
                        ~WebhookEventModel.status.in_([
                            WebhookStatus.FAILED_VALIDATION.value,
                            WebhookStatus.DUPLICATE.value,
                        ])
                    )
                ).all()

                expected = self._expected_volume.get(provider_id, 0) * interval_hours
                rate = len(events_in_interval) / expected * 100 if expected > 0 else 0
                rates.append({
                    "interval_start": interval_start.isoformat(),
                    "interval_end": interval_end.isoformat(),
                    "delivery_rate_pct": round(rate, 2),
                    "events_received": len(events_in_interval),
                    "events_expected": int(expected),
                })

            session.close()

            rates.reverse()  # Chronological order

            # Determine trend direction
            if len(rates) >= 3:
                recent_avg = statistics.mean(r["delivery_rate_pct"] for r in rates[-2:])
                older_avg = statistics.mean(r["delivery_rate_pct"] for r in rates[:2])

                if recent_avg < 70:
                    trend = DeliveryTrend.CRITICAL
                elif recent_avg < older_avg - 10:
                    trend = DeliveryTrend.DEGRADING
                elif recent_avg > older_avg + 10:
                    trend = DeliveryTrend.IMPROVING
                else:
                    trend = DeliveryTrend.STABLE
            else:
                trend = DeliveryTrend.STABLE

            return {
                "provider_id": provider_id,
                "window_hours": window_hours,
                "trend": trend.value,
                "intervals": rates,
            }
        except Exception as e:
            logger.error(f"Failed to get delivery trend: {e}")
            raise

    # -- Processing Time Analysis -------------------------------------------

    def get_processing_time_stats(
        self, provider_id: str, window_hours: int = 24
    ) -> dict:
        """Processing time statistics for successfully handled webhooks.

        High processing time means our handler is slow, which can cause
        the provider to think delivery failed (if we don't respond 200 fast enough).
        """
        if not self.session_factory:
            raise RuntimeError("Database session factory not initialized")

        try:
            from db import WebhookEventModel
            session = self.session_factory()
            cutoff = datetime.now() - timedelta(hours=window_hours)

            events = session.query(WebhookEventModel).filter(
                and_(
                    WebhookEventModel.provider_id == provider_id,
                    WebhookEventModel.received_at >= cutoff,
                    WebhookEventModel.status == WebhookStatus.PROCESSED.value,
                    WebhookEventModel.processing_time_ms.isnot(None)
                )
            ).all()

            times = [e.processing_time_ms for e in events]
            session.close()

            if not times:
                return {
                    "provider_id": provider_id,
                    "sample_count": 0,
                    "p50_ms": 0,
                    "p95_ms": 0,
                    "p99_ms": 0,
                    "max_ms": 0,
                }

            sorted_times = sorted(times)
            p95_idx = int(len(sorted_times) * 0.95)
            p99_idx = int(len(sorted_times) * 0.99)

            return {
                "provider_id": provider_id,
                "sample_count": len(times),
                "p50_ms": round(statistics.median(sorted_times), 1),
                "p95_ms": sorted_times[min(p95_idx, len(sorted_times) - 1)],
                "p99_ms": sorted_times[min(p99_idx, len(sorted_times) - 1)],
                "max_ms": max(sorted_times),
                "at_risk": sorted_times[min(p95_idx, len(sorted_times) - 1)] > 2000,
            }
        except Exception as e:
            logger.error(f"Failed to get processing time stats: {e}")
            raise

    # -- Signature Verification Helpers -------------------------------------

    @staticmethod
    def verify_hmac_signature(
        payload: bytes,
        secret: str,
        received_signature: str,
        algorithm: str = "sha256",
    ) -> bool:
        """Verify HMAC webhook signature.

        Every provider signs webhooks differently, but the pattern is
        the same: HMAC the payload with a shared secret, compare to
        the signature header.

        This is the generic implementation. Provider-specific quirks
        (Stripe's timestamp prefix, Twilio's URL inclusion) would be
        handled in provider-specific adapter methods.
        """
        hash_func = getattr(hashlib, algorithm, None)
        if hash_func is None:
            raise ValueError(f"Unsupported hash algorithm: {algorithm}")

        expected = hmac.new(
            secret.encode("utf-8"),
            payload,
            hash_func,
        ).hexdigest()

        return hmac.compare_digest(expected, received_signature)

    # -- Dashboard Summary --------------------------------------------------

    def get_monitor_summary(self, window_hours: int = 1) -> dict:
        """Complete webhook monitoring summary for the dashboard.

        Aggregates delivery rates, DLQ status, and active gaps
        into a single payload.
        """
        try:
            delivery_rates = self.get_delivery_rates_all_providers(window_hours)
            dlq = self.get_dlq_summary()

            unhealthy_providers = [
                r for r in delivery_rates if not r["is_healthy"]
            ]
            critical_providers = [
                r for r in delivery_rates if r["is_critical"]
            ]

            return {
                "window_hours": window_hours,
                "total_providers_monitored": len(delivery_rates),
                "providers_healthy": len(delivery_rates) - len(unhealthy_providers),
                "providers_unhealthy": len(unhealthy_providers),
                "providers_critical": len(critical_providers),
                "unhealthy_details": unhealthy_providers,
                "critical_details": critical_providers,
                "dead_letter_queue": dlq,
                "active_gaps": [],  # Gaps stored in DB; queried via detect_gaps()
            }
        except Exception as e:
            logger.error(f"Failed to get monitor summary: {e}")
            raise


# ---------------------------------------------------------------------------
# Usage Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from datetime import datetime, timedelta

    monitor = WebhookMonitor()

    # Configure expected volumes (from integration registry)
    monitor.set_expected_volume("plaid", 200)
    monitor.set_expected_volume("stripe", 350)
    monitor.set_expected_volume("twilio", 120)
    monitor.set_expected_volume("kyc_provider", 85)

    # Simulate receiving webhook events
    now = datetime.now()
    events = [
        WebhookEvent(
            event_id=f"evt_plaid_{i}",
            provider_id="plaid",
            event_type="TRANSACTIONS.DEFAULT_UPDATE",
            received_at=now - timedelta(minutes=i),
            provider_timestamp=now - timedelta(minutes=i + 1),
            payload_size_bytes=2048,
            signature_valid=True,
            status=WebhookStatus.RECEIVED,
            processing_time_ms=150 + (i * 10),
        )
        for i in range(50)  # Only 50 events in an hour (expected: 200)
    ]

    for event in events:
        recorded = monitor.record_event(event)
        monitor.mark_processed(event.event_id, event.processing_time_ms)

    # Add some Stripe events (healthy)
    for i in range(340):
        event = WebhookEvent(
            event_id=f"evt_stripe_{i}",
            provider_id="stripe",
            event_type="payment_intent.succeeded",
            received_at=now - timedelta(minutes=i * 0.17),
            provider_timestamp=None,
            payload_size_bytes=1024,
            signature_valid=True,
            status=WebhookStatus.RECEIVED,
            processing_time_ms=80,
        )
        monitor.record_event(event)
        monitor.mark_processed(event.event_id, 80)

    # Check delivery rates
    print("=== WEBHOOK DELIVERY RATES ===\n")
    rates = monitor.get_delivery_rates_all_providers(window_hours=1)
    for rate in rates:
        status = "✅" if rate["is_healthy"] else ("🔴" if rate["is_critical"] else "⚠️")
        print(
            f"{status} {rate['provider_id']}: "
            f"{rate['delivery_rate_pct']}% "
            f"({rate['valid_received']}/{rate['expected_events']} events)"
        )

    # Check for delivery gaps
    print("\n=== DELIVERY GAP DETECTION ===\n")
    gaps = monitor.detect_gaps("plaid", check_interval_minutes=15, lookback_hours=1)
    if gaps:
        for gap in gaps:
            print(
                f"⚠️  Gap detected: {gap.provider_id} "
                f"({gap.delivery_rate_pct}% delivery, "
                f"severity: {gap.severity})"
            )
    else:
        print("No delivery gaps detected.")

    # DLQ summary
    print("\n=== DEAD LETTER QUEUE ===\n")
    dlq = monitor.get_dlq_summary()
    print(f"Unresolved: {dlq['total_unresolved']}")
    print(f"Resolved: {dlq['total_resolved']}")

    # Monitor summary
    print("\n=== MONITOR SUMMARY ===\n")
    summary = monitor.get_monitor_summary()
    print(f"Providers monitored: {summary['total_providers_monitored']}")
    print(f"Healthy: {summary['providers_healthy']}")
    print(f"Unhealthy: {summary['providers_unhealthy']}")
    print(f"Critical: {summary['providers_critical']}")
