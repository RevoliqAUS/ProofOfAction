"""
Kick Chat Bot Module

Handles:
- Listening to channel chat messages
- Detecting !challenge commands
- Sending messages to chat
- Dispatching AI judge results
"""

import os
import re
import logging
from typing import Optional, Dict, Any, Callable, Awaitable
from dataclasses import dataclass
from datetime import datetime

import httpx

from .oauth import KickOAuth, TokenResponse, API_HOST

logger = logging.getLogger(__name__)


@dataclass
class ChallengeCommand:
    """Parsed !challenge command data"""
    description: str
    time_limit: str  # e.g., "30s", "5m", "1h"
    channel_id: str
    sender_username: str
    sender_user_id: str
    message_id: str
    raw_message: str
    timestamp: datetime


@dataclass
class ChatMessage:
    """Incoming chat message data"""
    message_id: str
    channel_id: str
    content: str
    sender_username: str
    sender_user_id: str
    timestamp: datetime
    is_broadcaster: bool = False
    is_moderator: bool = False


class KickBot:
    """Kick Chat Bot for challenge detection and AI judging"""

    # Regex pattern for !challenge command
    # Format: !challenge <description> <time_limit>
    # Example: !challenge score 3 consecutive three-pointers 5m
    CHALLENGE_PATTERN = re.compile(
        r"^!challenge\s+(.+?)\s+(\d+[smh])$",
        re.IGNORECASE | re.UNICODE,
    )

    def __init__(
        self,
        oauth: Optional[KickOAuth] = None,
        on_challenge: Optional[Callable[[ChallengeCommand], Awaitable[None]]] = None,
    ):
        """
        Initialize Kick Bot.

        Args:
            oauth: KickOAuth instance for authentication
            on_challenge: Async callback when !challenge command is detected
        """
        self.oauth = oauth or KickOAuth()
        self.on_challenge = on_challenge

        # Monitored channels (channel_id -> channel_slug mapping)
        self._channels: Dict[str, str] = {}

        # Active challenges (challenge_id -> ChallengeCommand)
        self._active_challenges: Dict[str, ChallengeCommand] = {}

        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self):
        """Close HTTP client and OAuth connections"""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
        await self.oauth.close()

    # ===================== Channel Management =====================

    async def add_channel(self, channel_id: str, channel_slug: str):
        """Add a channel to monitor"""
        self._channels[channel_id] = channel_slug
        logger.info(f"Now monitoring channel: {channel_slug} (ID: {channel_id})")

    async def remove_channel(self, channel_id: str):
        """Remove a channel from monitoring"""
        if channel_id in self._channels:
            slug = self._channels.pop(channel_id)
            logger.info(f"Stopped monitoring channel: {slug}")

    def is_monitoring(self, channel_id: str) -> bool:
        """Check if a channel is being monitored"""
        return channel_id in self._channels

    # ===================== Message Processing =====================

    def parse_challenge_command(
        self, message: ChatMessage
    ) -> Optional[ChallengeCommand]:
        """
        Parse a chat message for !challenge command.

        Returns ChallengeCommand if valid, None otherwise.
        """
        match = self.CHALLENGE_PATTERN.match(message.content.strip())
        if not match:
            return None

        description = match.group(1).strip()
        time_limit = match.group(2).strip()

        return ChallengeCommand(
            description=description,
            time_limit=time_limit,
            channel_id=message.channel_id,
            sender_username=message.sender_username,
            sender_user_id=message.sender_user_id,
            message_id=message.message_id,
            raw_message=message.content,
            timestamp=message.timestamp,
        )

    async def process_message(self, message: ChatMessage) -> Optional[ChallengeCommand]:
        """
        Process an incoming chat message.

        If it's a !challenge command, parse it and trigger callback.
        """
        # Skip if not monitoring this channel
        if not self.is_monitoring(message.channel_id):
            return None

        # Try to parse as challenge command
        challenge = self.parse_challenge_command(message)
        if challenge:
            logger.info(
                f"Challenge detected from @{challenge.sender_username}: "
                f"{challenge.description} ({challenge.time_limit})"
            )

            # Store active challenge
            self._active_challenges[challenge.message_id] = challenge

            # Trigger callback
            if self.on_challenge:
                try:
                    await self.on_challenge(challenge)
                except Exception as e:
                    logger.error(f"Error in challenge callback: {e}")

            return challenge

        return None

    # ===================== Chat API =====================

    async def send_message(
        self,
        channel_id: str,
        content: str,
        token: Optional[TokenResponse] = None,
        reply_to_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a message to a Kick channel chat.

        POST https://api.kick.com/public/v1/chat

        Args:
            channel_id: Target channel ID (will be converted to int)
            content: Message content (max 500 chars)
            token: User token with chat:write scope
            reply_to_message_id: Optional message ID to reply to
        """
        # Debug: log token source
        if token is None:
            logger.warning("[send_message] No token provided, falling back to App Access Token")
            token = await self.oauth.get_app_access_token()
            logger.info(f"[send_message] Using App Access Token: {token.access_token[:20]}...")
        else:
            logger.info(f"[send_message] Using provided User Access Token: {token.access_token[:20]}...")

        client = await self._get_client()

        # Truncate message if too long
        if len(content) > 500:
            content = content[:497] + "..."

        # Kick API requires broadcaster_user_id as integer, type must be "user"
        payload = {
            "broadcaster_user_id": int(channel_id),
            "content": content,
            "type": "user",
        }

        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id

        headers = {
            "Authorization": f"Bearer {token.access_token}",
            "Content-Type": "application/json",
        }

        # Debug: log request details
        logger.info(f"[send_message] POST {API_HOST}/public/v1/chat")
        logger.info(f"[send_message] Headers: Authorization: Bearer {token.access_token[:20]}...")
        logger.info(f"[send_message] Payload: {payload}")

        try:
            response = await client.post(
                f"{API_HOST}/public/v1/chat",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()

            result = response.json() if response.content else {}
            logger.info(f"Sent message to channel {channel_id}")
            return result

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to send message: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            raise

    # ===================== AI Judge Integration =====================

    async def send_judge_result(
        self,
        channel_id: str,
        challenge: ChallengeCommand,
        result: Dict[str, Any],
        token: Optional[TokenResponse] = None,
    ) -> Dict[str, Any]:
        """
        Send AI judge result to chat.

        Args:
            channel_id: Target channel ID
            challenge: Original challenge command
            result: AI analysis result dict
            token: User token with chat:write scope
        """
        is_goal = result.get("is_goal", False)
        confidence = result.get("confidence", 0.0)
        reasoning = result.get("reasoning", "")
        cheat_suspected = result.get("cheat_suspected", False)

        # Build result message
        if is_goal and not cheat_suspected:
            status_emoji = "✅"
            status_text = "VERIFIED"
        elif is_goal and cheat_suspected:
            status_emoji = "⚠️"
            status_text = "SUSPICIOUS"
        else:
            status_emoji = "❌"
            status_text = "REJECTED"

        # Get tx_hash from result if available
        tx_hash = result.get("tx_hash", "pending")

        message = (
            f"{status_emoji} [AI REFEREE RESULT] "
            f"Challenge: {challenge.description} | "
            f"Verdict: {status_text} | "
            f"Confidence: {confidence * 100:.0f}% | "
            f"On-chain proof: {tx_hash}"
        )

        return await self.send_message(
            channel_id=channel_id,
            content=message,
            token=token,
            reply_to_message_id=challenge.message_id,
        )

    async def announce_challenge(
        self,
        channel_id: str,
        challenge: ChallengeCommand,
        token: Optional[TokenResponse] = None,
    ) -> Dict[str, Any]:
        """
        Announce a new challenge in chat.

        Args:
            channel_id: Target channel ID
            challenge: Challenge command to announce
            token: User token with chat:write scope
        """
        message = (
            f"🎯 [NEW CHALLENGE] {challenge.description} | "
            f"Started by: @{challenge.sender_username} | "
            f"Time limit: {challenge.time_limit} | "
            f"Upload your video proof when done!"
        )

        return await self.send_message(
            channel_id=channel_id,
            content=message,
            token=token,
        )

    # ===================== Channel Info =====================

    async def get_channel_info(
        self,
        channel_id: str,
        use_app_token: bool = True,
    ) -> Dict[str, Any]:
        """Get channel information from Kick API"""
        return await self.oauth.api_request(
            "GET",
            f"/public/v1/channels/{channel_id}",
            use_app_token=use_app_token,
        )

    async def get_user_info(
        self,
        user_id: str,
        use_app_token: bool = True,
    ) -> Dict[str, Any]:
        """Get user information from Kick API"""
        return await self.oauth.api_request(
            "GET",
            f"/public/v1/users/{user_id}",
            use_app_token=use_app_token,
        )
