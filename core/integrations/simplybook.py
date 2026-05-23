"""Simplybook JSON-RPC client.

Reads credentials from environment via ``django.conf.settings`` so deployment
config keeps the same shape across environments. All public methods return
bool/None and never raise — Simplybook downtime must not block the surfaces
that use it (account overview, instructor roster).

Auth: Simplybook exchanges the long-lived API key for a short-lived session
token via JSON-RPC ``getToken`` on ``/login``. The token is cached per worker
in a module-level dict keyed by api_key+company_login with the server-reported
expiry. Subsequent calls reuse the token until it expires.

The class layout intentionally mirrors ``core.integrations.mailchimp`` so the
caller pattern (``Client.from_settings()`` → check ``.enabled`` → call method)
stays consistent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 5.0
_TOKEN_LIFETIME_FALLBACK_SECONDS = 3600  # If Simplybook doesn't return an expiry, assume 1h.
_TOKEN_REFRESH_BUFFER_SECONDS = 60  # Refresh a minute before expiry to avoid edge races.

# Process-local token cache: { (api_key, company_login): (token, expires_at_epoch) }
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}


@dataclass(frozen=True)
class SimplybookConfig:
    api_key: str
    company_login: str

    @property
    def login_url(self) -> str:
        return "https://user-api.simplybook.me/login"

    @property
    def admin_url(self) -> str:
        return "https://user-api.simplybook.me/admin"


class SimplybookClient:
    """Minimal Simplybook JSON-RPC client. Disabled when settings are blank."""

    def __init__(self, config: SimplybookConfig | None) -> None:
        self.config = config

    @classmethod
    def from_settings(cls) -> SimplybookClient:
        from django.conf import settings

        api_key = getattr(settings, "SIMPLYBOOK_API_KEY", "") or ""
        company_login = getattr(settings, "SIMPLYBOOK_COMPANY_LOGIN", "") or ""
        if not api_key or not company_login:
            return cls(config=None)
        return cls(config=SimplybookConfig(api_key=api_key, company_login=company_login))

    @property
    def enabled(self) -> bool:
        return self.config is not None

    def _get_token(self) -> str | None:
        """Return a valid session token, fetching one from Simplybook if needed."""
        if self.config is None:  # pragma: no cover - guarded by self.enabled
            return None
        cache_key = (self.config.api_key, self.config.company_login)
        cached = _token_cache.get(cache_key)
        now = time.time()
        if cached and cached[1] - _TOKEN_REFRESH_BUFFER_SECONDS > now:
            return cached[0]
        payload = {
            "jsonrpc": "2.0",
            "method": "getToken",
            "params": [self.config.company_login, self.config.api_key],
            "id": 1,
        }
        try:
            response = requests.post(
                self.config.login_url,
                json=payload,
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            logger.warning("Simplybook getToken network error: %s", exc)
            return None
        if not response.ok:
            logger.warning("Simplybook getToken failed: %s %s", response.status_code, response.text[:200])
            return None
        try:
            data = response.json()
        except ValueError:
            logger.warning("Simplybook getToken returned non-JSON: %s", response.text[:200])
            return None
        token = data.get("result") if isinstance(data, dict) else None
        if not token:
            logger.warning("Simplybook getToken returned no result: %s", str(data)[:200])
            return None
        # Simplybook tokens don't carry an explicit expiry; cache with a safe fallback TTL.
        _token_cache[cache_key] = (token, now + _TOKEN_LIFETIME_FALLBACK_SECONDS)
        return token

    def _rpc(self, method: str, params: list) -> object | None:
        """POST a JSON-RPC call to ``/admin``. Returns the ``result`` field or None."""
        if self.config is None:  # pragma: no cover - guarded by self.enabled
            return None
        token = self._get_token()
        if not token:
            return None
        headers = {
            "X-Company-Login": self.config.company_login,
            "X-Token": token,
        }
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        try:
            response = requests.post(
                self.config.admin_url,
                json=payload,
                headers=headers,
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            logger.warning("Simplybook %s network error: %s", method, exc)
            return None
        if not response.ok:
            logger.warning("Simplybook %s failed: %s %s", method, response.status_code, response.text[:200])
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        return data.get("result") if isinstance(data, dict) else None

    def has_completed_tour(self, email: str) -> bool:
        """Return True when Simplybook has at least one finished tour booking for ``email``.

        Uses the ``getBookings`` admin RPC with a filter on client email and
        booking status ``approved``. The bool is best-effort — any error
        (network, auth, schema drift) returns False so callers don't lock the
        user out of features when Simplybook is down.
        """
        result = self._rpc(
            "getBookings",
            [{"client_email": email, "status": "approved"}],
        )
        if not isinstance(result, list):
            return False
        return len(result) > 0
