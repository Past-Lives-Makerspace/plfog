"""Cache-backed rolling rate counters.

Two flavors share the same `_bump` mechanics:

* The **global** login-code circuit breaker (:func:`record_send_attempt`) — per-IP and
  per-key limits are handled by allauth itself (ACCOUNT_RATE_LIMITS); this adds a coarse
  total-sends cap on top so a single bad day can't drain the Resend daily quota.
* **Keyed** per-actor counters (:func:`record_keyed_attempt` / :func:`keyed_within_limits`)
  for member-facing actions that need a per-person cap, e.g. the Discord ``/create``
  command's events-per-member limit. For an anonymous caller the actor is the address
  :func:`client_rate_key` picks (the biometric unlock, the public event page's guest RSVP).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.cache import cache

if TYPE_CHECKING:
    from django.http import HttpRequest

HOUR_SECONDS = 3600
DAY_SECONDS = 86400

_HOURLY_KEY = "abuse:login_code:hourly"
_DAILY_KEY = "abuse:login_code:daily"

# A forwarded-for entry is an IP, so anything longer is junk; bound it before it becomes a
# cache key.
_RATE_LIMIT_KEY_MAX_CHARS = 64


def client_rate_key(request: HttpRequest) -> str:
    """The address to rate limit an unauthenticated caller by.

    Deliberately NOT the leftmost ``X-Forwarded-For`` entry, which is what a "client IP"
    helper normally reaches for (``classes.views._client_ip`` included). Proxies APPEND to
    that header, so the leftmost value is whatever the client chose to send. On an
    unauthenticated, csrf-exempt endpoint that makes the rate-limit key itself attacker
    controlled: rotate one header value per request and every attempt lands in a fresh
    bucket, so the cap never applies to the one caller it exists to bound.

    ``CF-Connecting-IP`` is preferred because production is Cloudflare in front of Render
    (a response carries both ``server: cloudflare`` and ``x-render-origin-server``). That is
    TWO proxies, so the rightmost forwarded entry is Render's view of *Cloudflare*, not of
    the member: keying on it would collapse every caller into one bucket, and a single
    attacker could then spend the whole day's allowance and disable the guarded action for
    everybody. Cloudflare overwrites ``CF-Connecting-IP`` on every request it forwards, so a
    client cannot dictate it.

    The fallbacks are the rightmost forwarded entry (one proxy, appended by it) and then
    ``REMOTE_ADDR`` (a direct connection: local dev and tests).

    Known gap, accepted: someone who reaches the Render origin directly, bypassing
    Cloudflare, can send any ``CF-Connecting-IP`` they like. That is no weaker than the
    leftmost-entry behavior this replaced, it requires knowing the origin address, and a
    cap keyed this way is a cost ceiling rather than a defense against guessing a secret.

    This keys on one address, so everyone behind a single NAT shares a bucket, which is why
    a caller's caps are sized for a whole makerspace on one connection.
    """
    connecting = request.META.get("HTTP_CF_CONNECTING_IP", "").strip()
    if connecting:
        return connecting[:_RATE_LIMIT_KEY_MAX_CHARS]
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.rsplit(",", 1)[-1].strip()[:_RATE_LIMIT_KEY_MAX_CHARS]
    return request.META.get("REMOTE_ADDR", "")


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


def _keyed_cache_keys(scope: str, key: str) -> tuple[str, str]:
    """The (hourly, daily) cache keys for a per-actor counter under ``scope``."""
    return f"abuse:{scope}:{key}:hourly", f"abuse:{scope}:{key}:daily"


def record_keyed_attempt(scope: str, key: str, *, hourly_limit: int, daily_limit: int) -> tuple[bool, str | None]:
    """Record one attempt for a specific actor and report whether it's within both limits.

    The per-actor counterpart of :func:`record_send_attempt` — ``scope`` names the
    guarded action (e.g. ``discord_create``) and ``key`` the actor (a member pk), so one
    member's burst can't consume anyone else's allowance. Returns ``(allowed, reason)``
    with ``reason`` naming the cap that was hit ("hourly" or "daily").
    """
    hourly_key, daily_key = _keyed_cache_keys(scope, key)
    hourly = _bump(hourly_key, HOUR_SECONDS)
    daily = _bump(daily_key, DAY_SECONDS)
    if hourly > hourly_limit:
        return False, "hourly"
    if daily > daily_limit:
        return False, "daily"
    return True, None


def keyed_within_limits(scope: str, key: str, *, hourly_limit: int, daily_limit: int) -> bool:
    """Whether the actor still has allowance, WITHOUT recording an attempt.

    A read-only peek for early, friendly refusals (e.g. before building a preview);
    the attempt is recorded later via :func:`record_keyed_attempt` only when the
    guarded action actually happens, so an abandoned preview costs nothing.
    """
    hourly_key, daily_key = _keyed_cache_keys(scope, key)
    return cache.get(hourly_key, 0) < hourly_limit and cache.get(daily_key, 0) < daily_limit


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
