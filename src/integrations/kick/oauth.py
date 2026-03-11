"""
Kick OAuth 2.1 + PKCE Authentication Module

Supports:
- App Access Token (Client Credentials flow) - server-to-server
- User Access Token (Authorization Code flow with PKCE) - user login

References:
- OAuth Host: https://id.kick.com
- API Host: https://api.kick.com
"""

import os
import base64
import hashlib
import secrets
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

OAUTH_HOST = "https://id.kick.com"
API_HOST = "https://api.kick.com"

# Available scopes (pre-approved for this app)
AVAILABLE_SCOPES = [
    "user:read",
    "channel:read",
    "chat:write",
    "events:subscribe",
    "channel:read:rewards",
    "channel:write:rewards",
    "kicks:read",
]


@dataclass
class TokenResponse:
    """OAuth token response data"""
    access_token: str
    token_type: str
    expires_in: int
    refresh_token: Optional[str] = None
    scope: Optional[str] = None
    expires_at: Optional[datetime] = None

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.utcnow() >= self.expires_at


class KickOAuth:
    """Kick OAuth 2.1 + PKCE authentication handler"""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = None,
    ):
        self.client_id = client_id or os.getenv("KICK_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("KICK_CLIENT_SECRET")
        self.redirect_uri = redirect_uri or os.getenv(
            "KICK_REDIRECT_URL", "http://localhost:8000/kick/callback"
        )

        if not self.client_id:
            raise ValueError("KICK_CLIENT_ID is required")
        if not self.client_secret:
            raise ValueError("KICK_CLIENT_SECRET is required")

        # Token storage (in production, use a proper store like Redis)
        self._app_token: Optional[TokenResponse] = None
        self._user_tokens: Dict[str, TokenResponse] = {}
        self._current_user_token: Optional[TokenResponse] = None  # Most recent user token

        # PKCE state storage (code_verifier per state)
        self._pkce_states: Dict[str, str] = {}

        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    @property
    def user_access_token(self) -> Optional[TokenResponse]:
        """Get the most recent user access token"""
        if self._current_user_token:
            logger.debug(f"[OAuth] user_access_token exists, expires_at: {self._current_user_token.expires_at}")
        else:
            logger.debug("[OAuth] user_access_token is None")
        return self._current_user_token

    # ===================== PKCE Helpers =====================

    @staticmethod
    def _generate_code_verifier() -> str:
        """Generate a cryptographically random code verifier (43-128 chars)"""
        return secrets.token_urlsafe(64)[:128]

    @staticmethod
    def _generate_code_challenge(verifier: str) -> str:
        """Generate S256 code challenge from verifier"""
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    @staticmethod
    def _generate_state() -> str:
        """Generate a random state parameter for CSRF protection"""
        return secrets.token_urlsafe(32)

    # ===================== App Access Token (Client Credentials) =====================

    async def get_app_access_token(self, force_refresh: bool = False) -> TokenResponse:
        """
        Get App Access Token using Client Credentials flow.
        Used for server-to-server API calls without user context.
        """
        if not force_refresh and self._app_token and not self._app_token.is_expired():
            return self._app_token

        client = await self._get_client()

        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        try:
            response = await client.post(
                f"{OAUTH_HOST}/oauth/token",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            token_data = response.json()

            self._app_token = TokenResponse(
                access_token=token_data["access_token"],
                token_type=token_data.get("token_type", "Bearer"),
                expires_in=token_data.get("expires_in", 3600),
                scope=token_data.get("scope"),
                expires_at=datetime.utcnow()
                + timedelta(seconds=token_data.get("expires_in", 3600) - 60),
            )

            logger.info("Successfully obtained App Access Token")
            return self._app_token

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to get App Access Token: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Error getting App Access Token: {e}")
            raise

    # ===================== User Access Token (Authorization Code + PKCE) =====================

    def get_authorization_url(
        self,
        scopes: Optional[list] = None,
        state: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Generate the authorization URL for user login.
        Returns dict with 'url' and 'state' for later verification.
        """
        if scopes is None:
            scopes = AVAILABLE_SCOPES

        state = state or self._generate_state()
        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)

        # Store verifier for later exchange
        self._pkce_states[state] = code_verifier

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        # Use urlencode for proper URL encoding of parameters
        query_string = urlencode(params)
        auth_url = f"{OAUTH_HOST}/oauth/authorize?{query_string}"

        logger.info(f"Generated authorization URL with state: {state}")

        return {
            "url": auth_url,
            "state": state,
        }

    async def exchange_code_for_token(
        self,
        code: str,
        state: str,
    ) -> TokenResponse:
        """
        Exchange authorization code for User Access Token.
        Must be called after user completes OAuth flow.
        """
        code_verifier = self._pkce_states.pop(state, None)
        if not code_verifier:
            raise ValueError("Invalid or expired state parameter")

        client = await self._get_client()

        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "code": code,
            "code_verifier": code_verifier,
        }

        try:
            response = await client.post(
                f"{OAUTH_HOST}/oauth/token",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            token_data = response.json()

            token = TokenResponse(
                access_token=token_data["access_token"],
                token_type=token_data.get("token_type", "Bearer"),
                expires_in=token_data.get("expires_in", 3600),
                refresh_token=token_data.get("refresh_token"),
                scope=token_data.get("scope"),
                expires_at=datetime.utcnow()
                + timedelta(seconds=token_data.get("expires_in", 3600) - 60),
            )

            # Store token (keyed by state for simplicity; in production use user_id)
            self._user_tokens[state] = token
            self._current_user_token = token  # Keep reference to most recent token

            logger.info("Successfully exchanged code for User Access Token")
            return token

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to exchange code for token: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Error exchanging code for token: {e}")
            raise

    async def refresh_user_token(self, refresh_token: str) -> TokenResponse:
        """Refresh a User Access Token using the refresh token"""
        client = await self._get_client()

        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
        }

        try:
            response = await client.post(
                f"{OAUTH_HOST}/oauth/token",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            token_data = response.json()

            token = TokenResponse(
                access_token=token_data["access_token"],
                token_type=token_data.get("token_type", "Bearer"),
                expires_in=token_data.get("expires_in", 3600),
                refresh_token=token_data.get("refresh_token"),
                scope=token_data.get("scope"),
                expires_at=datetime.utcnow()
                + timedelta(seconds=token_data.get("expires_in", 3600) - 60),
            )

            logger.info("Successfully refreshed User Access Token")
            return token

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to refresh token: {e.response.text}")
            raise

    async def revoke_token(self, token: str) -> bool:
        """Revoke an access or refresh token"""
        client = await self._get_client()

        data = {
            "token": token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        try:
            response = await client.post(
                f"{OAUTH_HOST}/oauth/revoke",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            logger.info("Token revoked successfully")
            return True

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to revoke token: {e.response.text}")
            return False

    # ===================== API Request Helper =====================

    async def api_request(
        self,
        method: str,
        endpoint: str,
        token: Optional[TokenResponse] = None,
        use_app_token: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Make an authenticated API request to Kick API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., /api/v1/users/me)
            token: User token to use (if not using app token)
            use_app_token: If True, use App Access Token
            **kwargs: Additional arguments passed to httpx request
        """
        if use_app_token:
            token = await self.get_app_access_token()
        elif token is None:
            raise ValueError("Either token or use_app_token must be provided")

        client = await self._get_client()

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token.access_token}"
        headers.setdefault("Content-Type", "application/json")

        url = f"{API_HOST}{endpoint}"

        response = await client.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()

        if response.content:
            return response.json()
        return {}
