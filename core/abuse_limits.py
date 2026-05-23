"""Global circuit breaker for outgoing login-code emails.

Per-IP and per-key rate limits are handled by allauth itself (configured via
ACCOUNT_RATE_LIMITS). This module adds a coarse total-sends cap on top, as a
last-resort guardrail against a determined attacker who manages to rotate IPs
or otherwise bypass the per-key limits. The goal is to keep a single bad day
from draining the Resend daily quota and locking out real members.
"""

from __future__ import annotations

from django.core.cache import cache

HOUR_SECONDS = 3600
DAY_SECONDS = 86400

_HOURLY_KEY = "abuse:login_code:hourly"
_DAILY_KEY = "abuse:login_code:daily"


def _bump(key: str, ttl_seconds: int) -> int:
    """Increment a rolling counter, returning the new value. Sets TTL on first hit."""
    if cache.add(key, 1, ttl_seconds):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, ttl_seconds)
        return 1


def record_send_attempt(*, hourly_limit: int, daily_limit: int) -> tuple[bool, str | None]:
    """Record a send and report whether it's within both limits.

    Returns (allowed, reason). When allowed is False, reason names which cap
    was hit ("hourly" or "daily") so the caller can log it. The counters are
    always incremented so logs reflect attempted-send volume, not just delivered.
    """
    hourly = _bump(_HOURLY_KEY, HOUR_SECONDS)
    daily = _bump(_DAILY_KEY, DAY_SECONDS)
    if hourly > hourly_limit:
        return False, "hourly"
    if daily > daily_limit:
        return False, "daily"
    return True, None


def current_counts() -> dict[str, int]:
    """Return current rolling counts for observability/tests."""
    return {
        "hourly": cache.get(_HOURLY_KEY, 0),
        "daily": cache.get(_DAILY_KEY, 0),
    }


def reset() -> None:
    """Clear both counters. Test-only helper."""
    cache.delete(_HOURLY_KEY)
    cache.delete(_DAILY_KEY)
