"""
Kick Webhook Handler Module

Handles:
- Receiving webhook events from Kick
- Signature verification using Kick's public key
- Processing livestream.status.updated events
- Processing chat.message.sent events
"""

import os
import json
import logging
import hashlib
import hmac
from typing import Optional, Callable, Awaitable, Dict, Any
from datetime import datetime
from enum import Enum

import httpx
from fastapi import APIRouter, Request, Response, HTTPException, Header

from .bot import KickBot, ChatMessage

logger = logging.getLogger(__name__)

# Kick webhook public key endpoint (for signature verification)
KICK_PUBLIC_KEY_URL = "https://api.kick.com/.well-known/jwks.json"


class WebhookEventType(str, Enum):
    """Kick webhook event types"""
    LIVESTREAM_STATUS_UPDATED = "livestream.status.updated"
    CHAT_MESSAGE_SENT = "chat.message.sent"
    CHANNEL_FOLLOWED = "channel.followed"
    CHANNEL_SUBSCRIPTION_NEW = "channel.subscription.new"
    CHANNEL_SUBSCRIPTION_RENEWED = "channel.subscription.renewed"
    CHANNEL_SUBSCRIPTION_GIFTS = "channel.subscription.gifts"


class KickWebhookHandler:
    """Handler for Kick webhook events with signature verification"""

    def __init__(
        self,
        bot: Optional[KickBot] = None,
        webhook_secret: Optional[str] = None,
    ):
        """
        Initialize webhook handler.

        Args:
            bot: KickBot instance for processing chat events
            webhook_secret: Webhook signing secret from Kick dashboard
        """
        self.bot = bot
        self.webhook_secret = webhook_secret or os.getenv("KICK_WEBHOOK_SECRET")

        # Event callbacks
        self._on_livestream_start: Optional[Callable[[Dict], Awaitable[None]]] = None
        self._on_livestream_end: Optional[Callable[[Dict], Awaitable[None]]] = None
        self._on_chat_message: Optional[Callable[[ChatMessage], Awaitable[None]]] = None

        # Kick public key cache
        self._public_keys: Optional[Dict] = None

        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    # ===================== Callback Registration =====================

    def on_livestream_start(self, callback: Callable[[Dict], Awaitable[None]]):
        """Register callback for livestream start events"""
        self._on_livestream_start = callback
        return callback

    def on_livestream_end(self, callback: Callable[[Dict], Awaitable[None]]):
        """Register callback for livestream end events"""
        self._on_livestream_end = callback
        return callback

    def on_chat_message(self, callback: Callable[[ChatMessage], Awaitable[None]]):
        """Register callback for chat message events"""
        self._on_chat_message = callback
        return callback

    # ===================== Signature Verification =====================

    async def fetch_public_keys(self) -> Dict:
        """Fetch Kick's public keys for webhook verification"""
        if self._public_keys:
            return self._public_keys

        try:
            client = await self._get_client()
            response = await client.get(KICK_PUBLIC_KEY_URL)
            response.raise_for_status()
            self._public_keys = response.json()
            logger.info("Fetched Kick public keys for webhook verification")
            return self._public_keys

        except Exception as e:
            logger.error(f"Failed to fetch Kick public keys: {e}")
            raise

    def verify_signature_hmac(
        self,
        payload: bytes,
        signature: str,
        timestamp: str,
    ) -> bool:
        """
        Verify webhook signature using HMAC-SHA256.

        The signature is computed as:
        HMAC-SHA256(webhook_secret, timestamp + "." + payload)
        """
        if not self.webhook_secret:
            logger.warning("Webhook secret not configured, skipping verification")
            return True

        try:
            # Construct the signed message
            message = f"{timestamp}.".encode() + payload

            # Compute expected signature
            expected_sig = hmac.new(
                self.webhook_secret.encode(),
                message,
                hashlib.sha256,
            ).hexdigest()

            # Compare signatures (constant-time comparison)
            return hmac.compare_digest(expected_sig, signature)

        except Exception as e:
            logger.error(f"Signature verification failed: {e}")
            return False

    async def verify_request(
        self,
        request: Request,
        x_kick_signature: Optional[str] = None,
        x_kick_timestamp: Optional[str] = None,
    ) -> bytes:
        """
        Verify incoming webhook request.

        Returns the raw body if valid, raises HTTPException if invalid.
        """
        body = await request.body()

        # Extract headers
        signature = x_kick_signature or request.headers.get("X-Kick-Signature", "")
        timestamp = x_kick_timestamp or request.headers.get("X-Kick-Timestamp", "")

        if not signature or not timestamp:
            logger.warning("Missing webhook signature headers")
            # In development, allow unsigned requests
            if os.getenv("KICK_WEBHOOK_VERIFY", "true").lower() == "false":
                return body
            raise HTTPException(status_code=401, detail="Missing signature headers")

        if not self.verify_signature_hmac(body, signature, timestamp):
            logger.error("Invalid webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

        return body

    # ===================== Event Processing =====================

    async def process_event(self, event_type: str, data: Dict) -> None:
        """
        Process a webhook event based on its type.

        Args:
            event_type: Type of webhook event
            data: Event payload data
        """
        logger.info(f"Processing webhook event: {event_type}")

        if event_type == WebhookEventType.LIVESTREAM_STATUS_UPDATED:
            await self._handle_livestream_status(data)

        elif event_type == WebhookEventType.CHAT_MESSAGE_SENT:
            await self._handle_chat_message(data)

        else:
            logger.debug(f"Unhandled event type: {event_type}")

    async def _handle_livestream_status(self, data: Dict) -> None:
        """Handle livestream.status.updated event"""
        is_live = data.get("is_live", False)
        channel_id = str(data.get("broadcaster_user_id", ""))
        channel_name = data.get("broadcaster_user_login", "")

        logger.info(
            f"Livestream {'started' if is_live else 'ended'}: "
            f"{channel_name} (ID: {channel_id})"
        )

        if is_live and self._on_livestream_start:
            await self._on_livestream_start(data)
        elif not is_live and self._on_livestream_end:
            await self._on_livestream_end(data)

    async def _handle_chat_message(self, data: Dict) -> None:
        """Handle chat.message.sent event"""
        try:
            message = ChatMessage(
                message_id=str(data.get("message_id", "")),
                channel_id=str(data.get("broadcaster_user_id", "")),
                content=data.get("content", ""),
                sender_username=data.get("sender", {}).get("username", ""),
                sender_user_id=str(data.get("sender", {}).get("user_id", "")),
                timestamp=datetime.utcnow(),
                is_broadcaster=data.get("sender", {}).get("is_broadcaster", False),
                is_moderator=data.get("sender", {}).get("is_moderator", False),
            )

            logger.debug(
                f"Chat message from @{message.sender_username}: {message.content[:50]}"
            )

            # Trigger callback
            if self._on_chat_message:
                await self._on_chat_message(message)

            # Also process through bot if available
            if self.bot:
                await self.bot.process_message(message)

        except Exception as e:
            logger.error(f"Error processing chat message: {e}")


# ===================== FastAPI Router =====================

kick_webhook_router = APIRouter(prefix="/kick", tags=["Kick Webhooks"])

# Global webhook handler instance
_webhook_handler: Optional[KickWebhookHandler] = None


def get_webhook_handler() -> KickWebhookHandler:
    """Get or create the global webhook handler"""
    global _webhook_handler
    if _webhook_handler is None:
        _webhook_handler = KickWebhookHandler()
    return _webhook_handler


def set_webhook_handler(handler: KickWebhookHandler):
    """Set the global webhook handler"""
    global _webhook_handler
    _webhook_handler = handler


@kick_webhook_router.post("/webhook")
async def receive_webhook(
    request: Request,
    x_kick_signature: Optional[str] = Header(None, alias="X-Kick-Signature"),
    x_kick_timestamp: Optional[str] = Header(None, alias="X-Kick-Timestamp"),
    x_kick_event_type: Optional[str] = Header(None, alias="X-Kick-Event-Type"),
):
    """
    Receive webhook events from Kick.

    Supported events:
    - livestream.status.updated: Triggered when a stream starts or ends
    - chat.message.sent: Triggered when a chat message is sent

    Headers:
    - X-Kick-Signature: HMAC-SHA256 signature for verification
    - X-Kick-Timestamp: Unix timestamp of the event
    - X-Kick-Event-Type: Type of the event
    """
    handler = get_webhook_handler()

    # Verify signature
    try:
        body = await handler.verify_request(
            request,
            x_kick_signature=x_kick_signature,
            x_kick_timestamp=x_kick_timestamp,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Webhook verification error: {e}")
        raise HTTPException(status_code=400, detail="Verification failed")

    # Parse event
    try:
        event_data = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Get event type from header or payload
    event_type = x_kick_event_type or event_data.get("event_type", "")

    if not event_type:
        raise HTTPException(status_code=400, detail="Missing event type")

    # Process event asynchronously
    try:
        await handler.process_event(event_type, event_data)
    except Exception as e:
        logger.error(f"Error processing webhook event: {e}")
        # Return 200 to prevent retries for processing errors
        return {"status": "error", "message": str(e)}

    return {"status": "ok", "event_type": event_type}
