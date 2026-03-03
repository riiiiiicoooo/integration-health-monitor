"""
Webhook Receiver — FastAPI endpoint for ingesting webhooks from all providers.

PM-authored reference implementation. This is the front door for every
webhook event from every third-party provider. It handles signature
verification, event normalization, and routing to the appropriate handler.

Design decisions:
- Single receiver, multiple routes. Each provider gets its own URL path
  (e.g., /webhooks/stripe/events, /webhooks/plaid/events) so we can apply
  provider-specific signature verification without parsing the payload first.
- Always acknowledge fast. We return 200 immediately and process async.
  If our handler takes > 5 seconds, some providers (Stripe, Twilio) will
  consider delivery failed and retry, creating duplicates.
- Normalize everything. Providers all send different payload shapes. We
  normalize to a common WebhookEvent format before passing to the monitor.

In production, the receiver writes to a message queue (SQS, Kafka) and
processing happens async. This prototype demonstrates the ingestion logic,
signature verification, and provider routing inline.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import logging

# FastAPI imports — would be installed in production
# from fastapi import FastAPI, Request, Response, HTTPException, Header
# from pydantic import BaseModel
# import uvicorn

# For the prototype, we simulate the FastAPI layer with plain classes
# to keep the repo runnable without installing dependencies.


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("webhook_receiver")
logging.basicConfig(level=logging.INFO)


# ---------------------------------------------------------------------------
# Provider-Specific Signature Verification
# ---------------------------------------------------------------------------

class SignatureVerifier:
    """Handles signature verification for each provider.

    Every provider signs webhooks differently. This class encapsulates
    the provider-specific quirks so the receiver doesn't need to know
    the details.

    In production, secrets come from a vault (AWS Secrets Manager, etc.).
    For the prototype, we accept them as constructor arguments.
    """

    def __init__(self, provider_secrets: dict[str, str]):
        """
        Args:
            provider_secrets: Mapping of provider_id -> webhook signing secret
        """
        self._secrets = provider_secrets

    def verify_stripe(
        self, payload: bytes, signature_header: str
    ) -> bool:
        """Verify Stripe webhook signature.

        Stripe's format: t=timestamp,v1=signature
        They HMAC-SHA256 the string "{timestamp}.{payload}" with the
        webhook signing secret.
        """
        secret = self._secrets.get("stripe")
        if not secret:
            logger.warning("No Stripe webhook secret configured")
            return False

        try:
            elements = dict(
                pair.split("=", 1)
                for pair in signature_header.split(",")
            )
            timestamp = elements.get("t", "")
            received_sig = elements.get("v1", "")

            signed_payload = f"{timestamp}.".encode() + payload
            expected = hmac.new(
                secret.encode("utf-8"),
                signed_payload,
                hashlib.sha256,
            ).hexdigest()

            return hmac.compare_digest(expected, received_sig)

        except (ValueError, KeyError) as e:
            logger.error(f"Stripe signature parsing failed: {e}")
            return False

    def verify_plaid(
        self, payload: bytes, verification_header: str
    ) -> bool:
        """Verify Plaid webhook signature.

        Plaid uses JWKs for webhook verification. For the prototype,
        we use a simplified HMAC approach. Production would use the
        plaid-python SDK's webhook verification.
        """
        secret = self._secrets.get("plaid")
        if not secret:
            logger.warning("No Plaid webhook secret configured")
            return False

        expected = hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, verification_header)

    def verify_twilio(
        self, payload: bytes, signature_header: str, request_url: str
    ) -> bool:
        """Verify Twilio webhook signature.

        Twilio includes the full request URL in the signature calculation,
        which means the signature changes if you put a load balancer or
        proxy in front of the receiver. This is a common gotcha.
        """
        secret = self._secrets.get("twilio")
        if not secret:
            logger.warning("No Twilio webhook secret configured")
            return False

        # Twilio signs: URL + sorted POST params
        # For simplicity, we sign URL + body
        signed_content = request_url.encode() + payload
        expected = hmac.new(
            secret.encode("utf-8"),
            signed_content,
            hashlib.sha1,
        ).hexdigest()

        return hmac.compare_digest(expected, signature_header)

    def verify_generic_hmac(
        self,
        provider_id: str,
        payload: bytes,
        signature_header: str,
        algorithm: str = "sha256",
    ) -> bool:
        """Generic HMAC verification for providers without special quirks."""
        secret = self._secrets.get(provider_id)
        if not secret:
            logger.warning(f"No webhook secret configured for {provider_id}")
            return False

        hash_func = getattr(hashlib, algorithm, None)
        if hash_func is None:
            logger.error(f"Unsupported hash algorithm: {algorithm}")
            return False

        expected = hmac.new(
            secret.encode("utf-8"),
            payload,
            hash_func,
        ).hexdigest()

        return hmac.compare_digest(expected, signature_header)


# ---------------------------------------------------------------------------
# Event Normalization
# ---------------------------------------------------------------------------

@dataclass
class NormalizedEvent:
    """Provider-agnostic webhook event format.

    Every provider sends different JSON shapes. We normalize to this
    common format so downstream processing (webhook_monitor, incident
    handlers) don't need provider-specific parsing logic.
    """
    event_id: str                      # Provider's unique event ID
    provider_id: str                   # Which provider sent this
    event_type: str                    # Normalized event type
    provider_timestamp: Optional[datetime]  # When the provider says it happened
    received_at: datetime              # When we received it
    payload_size_bytes: int            # For monitoring payload sizes
    signature_valid: bool              # Did signature verification pass?
    raw_payload: dict                  # Original payload for handler
    metadata: dict = field(default_factory=dict)  # Additional extracted fields


class EventNormalizer:
    """Extracts common fields from provider-specific webhook payloads.

    Each provider sends different JSON structures. This normalizer
    knows where to find the event ID, type, and timestamp in each
    provider's payload.
    """

    def normalize_stripe(self, payload: dict) -> NormalizedEvent:
        """Normalize Stripe webhook event.

        Stripe structure:
        {
            "id": "evt_xxx",
            "type": "payment_intent.succeeded",
            "created": 1234567890,
            "data": { "object": { ... } }
        }
        """
        return NormalizedEvent(
            event_id=payload.get("id", "unknown"),
            provider_id="stripe",
            event_type=payload.get("type", "unknown"),
            provider_timestamp=datetime.fromtimestamp(
                payload.get("created", 0), tz=timezone.utc
            ) if payload.get("created") else None,
            received_at=datetime.now(timezone.utc),
            payload_size_bytes=len(json.dumps(payload)),
            signature_valid=True,  # Set by receiver after verification
            raw_payload=payload,
            metadata={
                "api_version": payload.get("api_version"),
                "object_id": payload.get("data", {}).get("object", {}).get("id"),
            },
        )

    def normalize_plaid(self, payload: dict) -> NormalizedEvent:
        """Normalize Plaid webhook event.

        Plaid structure:
        {
            "webhook_type": "TRANSACTIONS",
            "webhook_code": "DEFAULT_UPDATE",
            "item_id": "xxx",
            "new_transactions": 5
        }
        """
        webhook_type = payload.get("webhook_type", "UNKNOWN")
        webhook_code = payload.get("webhook_code", "UNKNOWN")

        return NormalizedEvent(
            event_id=f"plaid_{payload.get('item_id', 'unknown')}_{webhook_code}_{int(datetime.now().timestamp())}",
            provider_id="plaid",
            event_type=f"{webhook_type}.{webhook_code}",
            provider_timestamp=None,  # Plaid doesn't include timestamps
            received_at=datetime.now(timezone.utc),
            payload_size_bytes=len(json.dumps(payload)),
            signature_valid=True,
            raw_payload=payload,
            metadata={
                "item_id": payload.get("item_id"),
                "webhook_type": webhook_type,
                "webhook_code": webhook_code,
            },
        )

    def normalize_twilio(self, payload: dict) -> NormalizedEvent:
        """Normalize Twilio webhook event.

        Twilio sends form-encoded POST data, converted to dict:
        {
            "MessageSid": "SMxxx",
            "MessageStatus": "delivered",
            "To": "+1234567890",
            "From": "+0987654321"
        }
        """
        message_sid = payload.get("MessageSid", payload.get("SmsSid", "unknown"))
        status = payload.get("MessageStatus", payload.get("SmsStatus", "unknown"))

        return NormalizedEvent(
            event_id=f"twilio_{message_sid}_{status}",
            provider_id="twilio",
            event_type=f"message.{status}",
            provider_timestamp=None,
            received_at=datetime.now(timezone.utc),
            payload_size_bytes=len(json.dumps(payload)),
            signature_valid=True,
            raw_payload=payload,
            metadata={
                "message_sid": message_sid,
                "status": status,
                "to": payload.get("To"),
                "error_code": payload.get("ErrorCode"),
            },
        )

    def normalize_kyc(self, payload: dict) -> NormalizedEvent:
        """Normalize KYC provider webhook event.

        Generic structure:
        {
            "event_id": "evt_xxx",
            "event_type": "verification.completed",
            "created_at": "2024-01-15T10:30:00Z",
            "data": { ... }
        }
        """
        created_at = payload.get("created_at")
        if created_at:
            try:
                provider_ts = datetime.fromisoformat(
                    created_at.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                provider_ts = None
        else:
            provider_ts = None

        return NormalizedEvent(
            event_id=payload.get("event_id", "unknown"),
            provider_id="kyc_provider",
            event_type=payload.get("event_type", "unknown"),
            provider_timestamp=provider_ts,
            received_at=datetime.now(timezone.utc),
            payload_size_bytes=len(json.dumps(payload)),
            signature_valid=True,
            raw_payload=payload,
            metadata={
                "verification_id": payload.get("data", {}).get("verification_id"),
                "decision": payload.get("data", {}).get("decision"),
            },
        )

    def normalize_sendgrid(self, payload: dict) -> NormalizedEvent:
        """Normalize SendGrid webhook event.

        SendGrid sends arrays of events. Each item:
        {
            "sg_message_id": "xxx",
            "event": "delivered",
            "timestamp": 1234567890,
            "email": "user@example.com"
        }

        Note: This normalizes a SINGLE event from the array.
        The receiver is responsible for iterating over the batch.
        """
        return NormalizedEvent(
            event_id=f"sg_{payload.get('sg_message_id', 'unknown')}_{payload.get('event', 'unknown')}",
            provider_id="sendgrid",
            event_type=payload.get("event", "unknown"),
            provider_timestamp=datetime.fromtimestamp(
                payload.get("timestamp", 0), tz=timezone.utc
            ) if payload.get("timestamp") else None,
            received_at=datetime.now(timezone.utc),
            payload_size_bytes=len(json.dumps(payload)),
            signature_valid=True,
            raw_payload=payload,
            metadata={
                "email": payload.get("email"),
                "sg_message_id": payload.get("sg_message_id"),
                "reason": payload.get("reason"),
            },
        )

    def normalize(self, provider_id: str, payload: dict) -> NormalizedEvent:
        """Route to the correct provider-specific normalizer."""
        normalizers = {
            "stripe": self.normalize_stripe,
            "plaid": self.normalize_plaid,
            "twilio": self.normalize_twilio,
            "kyc_provider": self.normalize_kyc,
            "sendgrid": self.normalize_sendgrid,
        }

        normalizer = normalizers.get(provider_id)
        if normalizer:
            return normalizer(payload)

        # Fallback: generic normalization for unknown providers
        return NormalizedEvent(
            event_id=payload.get("id", payload.get("event_id", f"unknown_{int(datetime.now().timestamp())}")),
            provider_id=provider_id,
            event_type=payload.get("type", payload.get("event_type", payload.get("event", "unknown"))),
            provider_timestamp=None,
            received_at=datetime.now(timezone.utc),
            payload_size_bytes=len(json.dumps(payload)),
            signature_valid=True,
            raw_payload=payload,
        )


# ---------------------------------------------------------------------------
# Webhook Receiver
# ---------------------------------------------------------------------------

class WebhookReceiver:
    """Receives, verifies, normalizes, and routes webhook events.

    This is the main entry point for all incoming webhooks. In production,
    this would be a FastAPI application. The prototype demonstrates the
    full processing pipeline without the HTTP layer.

    Processing pipeline:
    1. Receive raw payload + headers
    2. Verify signature (provider-specific)
    3. Normalize to common format
    4. Check for duplicates (idempotency)
    5. Route to handler
    6. Record in webhook_monitor

    Production FastAPI routes would look like:

        @app.post("/webhooks/stripe/events")
        async def receive_stripe(request: Request):
            payload = await request.body()
            signature = request.headers.get("Stripe-Signature", "")
            return receiver.process("stripe", payload, {"Stripe-Signature": signature})

        @app.post("/webhooks/plaid/events")
        async def receive_plaid(request: Request):
            payload = await request.body()
            verification = request.headers.get("Plaid-Verification", "")
            return receiver.process("plaid", payload, {"Plaid-Verification": verification})
    """

    def __init__(
        self,
        provider_secrets: dict[str, str],
        handlers: Optional[dict] = None,
    ):
        self._verifier = SignatureVerifier(provider_secrets)
        self._normalizer = EventNormalizer()
        self._handlers = handlers or {}
        self._processed_events: set[str] = set()  # For idempotency
        self._event_log: list[NormalizedEvent] = []
        self._stats = {
            "total_received": 0,
            "signature_valid": 0,
            "signature_invalid": 0,
            "duplicates_rejected": 0,
            "processed_successfully": 0,
            "processing_errors": 0,
            "by_provider": {},
        }

    # -- Provider Route Configuration ---------------------------------------

    # Maps provider_id to the signature header name and verification method
    PROVIDER_ROUTES = {
        "stripe": {
            "signature_header": "Stripe-Signature",
            "verify_method": "verify_stripe",
        },
        "plaid": {
            "signature_header": "Plaid-Verification",
            "verify_method": "verify_plaid",
        },
        "twilio": {
            "signature_header": "X-Twilio-Signature",
            "verify_method": "verify_twilio",
        },
        "kyc_provider": {
            "signature_header": "X-Signature",
            "verify_method": "verify_generic_hmac",
            "algorithm": "sha256",
        },
        "sendgrid": {
            "signature_header": "X-Twilio-Email-Event-Webhook-Signature",
            "verify_method": "verify_generic_hmac",
            "algorithm": "sha256",
        },
    }

    # -- Main Processing Pipeline -------------------------------------------

    def process(
        self,
        provider_id: str,
        raw_payload: bytes,
        headers: dict[str, str],
        request_url: str = "",
    ) -> dict:
        """Process an incoming webhook event.

        This is the main entry point. In production, each FastAPI route
        calls this with the provider_id, raw body, and relevant headers.

        Returns a response dict with status and any error details.
        The HTTP layer would convert this to the appropriate status code.
        """
        self._stats["total_received"] += 1
        provider_stats = self._stats["by_provider"].setdefault(
            provider_id, {"received": 0, "valid": 0, "invalid": 0, "processed": 0}
        )
        provider_stats["received"] += 1

        # Step 1: Verify signature
        sig_valid = self._verify_signature(
            provider_id, raw_payload, headers, request_url
        )

        if not sig_valid:
            self._stats["signature_invalid"] += 1
            provider_stats["invalid"] += 1
            logger.warning(
                f"Webhook signature verification failed for {provider_id}"
            )
            # Return 200 even on invalid signature to prevent retry spam.
            # Log the failure for investigation.
            return {
                "status": "rejected",
                "reason": "signature_verification_failed",
                "provider_id": provider_id,
            }

        self._stats["signature_valid"] += 1
        provider_stats["valid"] += 1

        # Step 2: Parse payload
        try:
            payload_dict = json.loads(raw_payload)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse webhook payload from {provider_id}: {e}")
            return {
                "status": "error",
                "reason": "invalid_json",
                "provider_id": provider_id,
            }

        # Step 3: Handle SendGrid batch events
        if provider_id == "sendgrid" and isinstance(payload_dict, list):
            results = []
            for event in payload_dict:
                result = self._process_single_event(provider_id, event)
                results.append(result)
            return {
                "status": "accepted",
                "provider_id": provider_id,
                "events_processed": len(results),
                "results": results,
            }

        # Step 4: Process single event
        result = self._process_single_event(provider_id, payload_dict)
        return result

    def _process_single_event(
        self, provider_id: str, payload: dict
    ) -> dict:
        """Process a single normalized event."""
        # Normalize
        event = self._normalizer.normalize(provider_id, payload)
        event.signature_valid = True  # Already verified at this point

        # Idempotency check
        if event.event_id in self._processed_events:
            self._stats["duplicates_rejected"] += 1
            logger.info(
                f"Duplicate webhook rejected: {provider_id}/{event.event_id}"
            )
            return {
                "status": "duplicate",
                "event_id": event.event_id,
                "provider_id": provider_id,
            }

        # Record and process
        self._processed_events.add(event.event_id)
        self._event_log.append(event)

        # Route to handler
        handler = self._handlers.get(provider_id)
        if handler:
            try:
                handler(event)
                self._stats["processed_successfully"] += 1
                provider_stats = self._stats["by_provider"][provider_id]
                provider_stats["processed"] += 1
            except Exception as e:
                self._stats["processing_errors"] += 1
                logger.error(
                    f"Handler error for {provider_id}/{event.event_id}: {e}"
                )
                return {
                    "status": "processing_error",
                    "event_id": event.event_id,
                    "provider_id": provider_id,
                    "error": str(e),
                }
        else:
            # No handler registered — log and acknowledge
            self._stats["processed_successfully"] += 1
            logger.info(
                f"No handler for {provider_id}, event logged: "
                f"{event.event_type} ({event.event_id})"
            )

        return {
            "status": "accepted",
            "event_id": event.event_id,
            "event_type": event.event_type,
            "provider_id": provider_id,
        }

    def _verify_signature(
        self,
        provider_id: str,
        payload: bytes,
        headers: dict[str, str],
        request_url: str,
    ) -> bool:
        """Route to provider-specific signature verification."""
        route = self.PROVIDER_ROUTES.get(provider_id)
        if not route:
            logger.warning(
                f"No signature verification configured for {provider_id}. "
                f"Accepting without verification."
            )
            return True

        sig_header = headers.get(route["signature_header"], "")
        if not sig_header:
            logger.warning(
                f"Missing signature header '{route['signature_header']}' "
                f"from {provider_id}"
            )
            return False

        method_name = route["verify_method"]

        if method_name == "verify_stripe":
            return self._verifier.verify_stripe(payload, sig_header)
        elif method_name == "verify_plaid":
            return self._verifier.verify_plaid(payload, sig_header)
        elif method_name == "verify_twilio":
            return self._verifier.verify_twilio(payload, sig_header, request_url)
        elif method_name == "verify_generic_hmac":
            return self._verifier.verify_generic_hmac(
                provider_id, payload, sig_header, route.get("algorithm", "sha256")
            )

        return False

    # -- Stats & Monitoring -------------------------------------------------

    def get_stats(self) -> dict:
        """Receiver statistics for the dashboard."""
        return {
            **self._stats,
            "unique_events_processed": len(self._processed_events),
            "duplicate_rate_pct": round(
                self._stats["duplicates_rejected"]
                / max(self._stats["total_received"], 1) * 100, 2
            ),
            "signature_failure_rate_pct": round(
                self._stats["signature_invalid"]
                / max(self._stats["total_received"], 1) * 100, 2
            ),
        }

    def get_recent_events(
        self, provider_id: Optional[str] = None, limit: int = 20
    ) -> list[dict]:
        """Get recent events for debugging and monitoring."""
        events = self._event_log
        if provider_id:
            events = [e for e in events if e.provider_id == provider_id]

        return [
            {
                "event_id": e.event_id,
                "provider_id": e.provider_id,
                "event_type": e.event_type,
                "received_at": e.received_at.isoformat(),
                "payload_size_bytes": e.payload_size_bytes,
                "metadata": e.metadata,
            }
            for e in events[-limit:]
        ]


# ---------------------------------------------------------------------------
# FastAPI Application (Production Reference)
# ---------------------------------------------------------------------------

FASTAPI_APP_TEMPLATE = '''
"""
Production FastAPI application for webhook ingestion.

Deploy behind a load balancer with TLS termination.
Each provider gets a dedicated route for independent monitoring and
signature verification.

Run: uvicorn webhook_receiver:app --host 0.0.0.0 --port 8000

Health check: GET /health
Metrics: GET /metrics
"""

from fastapi import FastAPI, Request, Response
import uvicorn

app = FastAPI(
    title="Integration Health Monitor - Webhook Receiver",
    description="Receives and processes webhooks from third-party API providers",
    version="1.0.0",
)

# Initialize receiver with secrets from vault
receiver = WebhookReceiver(
    provider_secrets={
        "stripe": os.environ["STRIPE_WEBHOOK_SECRET"],
        "plaid": os.environ["PLAID_WEBHOOK_SECRET"],
        "twilio": os.environ["TWILIO_AUTH_TOKEN"],
        "kyc_provider": os.environ["KYC_WEBHOOK_SECRET"],
        "sendgrid": os.environ["SENDGRID_WEBHOOK_SECRET"],
    },
)


@app.post("/webhooks/stripe/events")
async def receive_stripe(request: Request):
    payload = await request.body()
    headers = {"Stripe-Signature": request.headers.get("Stripe-Signature", "")}
    result = receiver.process("stripe", payload, headers)
    return Response(status_code=200, content="ok")


@app.post("/webhooks/plaid/events")
async def receive_plaid(request: Request):
    payload = await request.body()
    headers = {"Plaid-Verification": request.headers.get("Plaid-Verification", "")}
    result = receiver.process("plaid", payload, headers)
    return Response(status_code=200, content="ok")


@app.post("/webhooks/twilio/status")
async def receive_twilio(request: Request):
    payload = await request.body()
    headers = {"X-Twilio-Signature": request.headers.get("X-Twilio-Signature", "")}
    result = receiver.process("twilio", payload, headers, str(request.url))
    return Response(status_code=200, content="ok")


@app.post("/webhooks/kyc/events")
async def receive_kyc(request: Request):
    payload = await request.body()
    headers = {"X-Signature": request.headers.get("X-Signature", "")}
    result = receiver.process("kyc_provider", payload, headers)
    return Response(status_code=200, content="ok")


@app.post("/webhooks/sendgrid/events")
async def receive_sendgrid(request: Request):
    payload = await request.body()
    headers = {
        "X-Twilio-Email-Event-Webhook-Signature":
            request.headers.get("X-Twilio-Email-Event-Webhook-Signature", "")
    }
    result = receiver.process("sendgrid", payload, headers)
    return Response(status_code=200, content="ok")


@app.get("/health")
async def health_check():
    stats = receiver.get_stats()
    return {
        "status": "healthy",
        "total_events_processed": stats["total_received"],
        "signature_failure_rate": stats["signature_failure_rate_pct"],
    }


@app.get("/metrics")
async def metrics():
    return receiver.get_stats()
'''


# ---------------------------------------------------------------------------
# Usage Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Simulate webhook processing without FastAPI

    receiver = WebhookReceiver(
        provider_secrets={
            "stripe": "whsec_test_secret_123",
            "plaid": "plaid_webhook_secret_456",
            "twilio": "twilio_auth_token_789",
            "kyc_provider": "kyc_secret_abc",
            "sendgrid": "sg_webhook_key_def",
        },
    )

    # Simulate Stripe webhook
    stripe_payload = json.dumps({
        "id": "evt_1234567890",
        "type": "payment_intent.succeeded",
        "created": 1706900000,
        "api_version": "2024-04-10",
        "data": {
            "object": {
                "id": "pi_abc123",
                "amount": 15000,
                "currency": "usd",
                "status": "succeeded",
            }
        }
    }).encode()

    # Create valid signature for testing
    timestamp = "1706900001"
    signed = f"{timestamp}.".encode() + stripe_payload
    sig = hmac.new(
        b"whsec_test_secret_123", signed, hashlib.sha256
    ).hexdigest()
    stripe_sig = f"t={timestamp},v1={sig}"

    result = receiver.process(
        "stripe",
        stripe_payload,
        {"Stripe-Signature": stripe_sig},
    )
    print(f"Stripe webhook: {result}")

    # Simulate Plaid webhook
    plaid_payload = json.dumps({
        "webhook_type": "TRANSACTIONS",
        "webhook_code": "DEFAULT_UPDATE",
        "item_id": "item_abc123",
        "new_transactions": 5,
    }).encode()

    plaid_sig = hmac.new(
        b"plaid_webhook_secret_456", plaid_payload, hashlib.sha256
    ).hexdigest()

    result = receiver.process(
        "plaid",
        plaid_payload,
        {"Plaid-Verification": plaid_sig},
    )
    print(f"Plaid webhook:  {result}")

    # Simulate KYC provider webhook
    kyc_payload = json.dumps({
        "event_id": "evt_kyc_001",
        "event_type": "verification.completed",
        "created_at": "2024-02-03T14:30:00Z",
        "data": {
            "verification_id": "ver_xyz789",
            "decision": "approved",
        }
    }).encode()

    kyc_sig = hmac.new(
        b"kyc_secret_abc", kyc_payload, hashlib.sha256
    ).hexdigest()

    result = receiver.process(
        "kyc_provider",
        kyc_payload,
        {"X-Signature": kyc_sig},
    )
    print(f"KYC webhook:    {result}")

    # Simulate duplicate (should be rejected)
    result = receiver.process(
        "kyc_provider",
        kyc_payload,
        {"X-Signature": kyc_sig},
    )
    print(f"KYC duplicate:  {result}")

    # Simulate invalid signature
    result = receiver.process(
        "stripe",
        stripe_payload,
        {"Stripe-Signature": "t=123,v1=invalid_signature"},
    )
    print(f"Invalid sig:    {result}")

    # Print stats
    print(f"\n=== RECEIVER STATS ===\n")
    stats = receiver.get_stats()
    print(f"Total received: {stats['total_received']}")
    print(f"Signature valid: {stats['signature_valid']}")
    print(f"Signature invalid: {stats['signature_invalid']}")
    print(f"Duplicates rejected: {stats['duplicates_rejected']}")
    print(f"Processed: {stats['processed_successfully']}")
    print(f"Duplicate rate: {stats['duplicate_rate_pct']}%")
    print(f"Sig failure rate: {stats['signature_failure_rate_pct']}%")

    print(f"\nPer provider:")
    for pid, pstats in stats["by_provider"].items():
        print(f"  {pid}: {pstats}")

    print(f"\n=== RECENT EVENTS ===\n")
    for event in receiver.get_recent_events():
        print(f"  {event['provider_id']:<15} {event['event_type']:<35} {event['event_id']}")
