"""Sync a user's Simplybook tour status into their UserProfile.

The UserProfile holds two fields the surfaces read:
- ``completed_tour_at``: stamp when Simplybook first reported a completed tour.
- ``tour_status_checked_at``: when we last asked Simplybook about this user.

These functions are best-effort: any error (Simplybook down, no profile,
disabled integration) results in a no-op so callers never have to wrap them.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)


def refresh_if_stale(user, *, max_age_hours: int = 24) -> None:
    """Poll Simplybook for this user's tour status if the cache is stale.

    Called lazily from views that show tour status (account overview, instructor
    roster) so users see a refreshed value at most every ``max_age_hours``.
    Skips silently when Simplybook is disabled or the user lacks a profile.
    """
    profile = getattr(user, "profile", None)
    if profile is None:
        return
    cutoff = timezone.now() - timedelta(hours=max_age_hours)
    if profile.tour_status_checked_at and profile.tour_status_checked_at >= cutoff:
        return
    _refresh(profile)


def force_refresh(user) -> None:
    """Poll Simplybook regardless of cache age. Used by the nightly sync command."""
    profile = getattr(user, "profile", None)
    if profile is None:
        return
    _refresh(profile)


def _refresh(profile) -> None:
    from core.integrations.simplybook import SimplybookClient

    client = SimplybookClient.from_settings()
    if not client.enabled:
        return
    email = (profile.user.email or "").strip()
    if not email:
        return
    completed = client.has_completed_tour(email)
    now = timezone.now()
    update_fields = ["tour_status_checked_at"]
    profile.tour_status_checked_at = now
    if completed and profile.completed_tour_at is None:
        profile.completed_tour_at = now
        update_fields.append("completed_tour_at")
    profile.save(update_fields=update_fields)
