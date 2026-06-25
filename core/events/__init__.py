"""The event-driven notification spine (Phase 1 — the foundation, built additively).

This package is the new foundation for the notification redesign described in
``docs/superpowers/plans/2026-06-24-notification-architecture-redesign.md``. It
is built **alongside** the existing ``core/triggers.py`` + ``core/notifications.dispatch``
machinery and changes NO existing send behavior. Senders migrate onto ``emit()``
in a later phase.

Public surface:

* :data:`EVENTS` / :func:`get_event` — the event registry (§2.1), seeded from the
  legacy trigger catalogue.
* :func:`emit` — the single emission point (§2.2): writes the activity row,
  resolves recipients, fans out to enabled channels, idempotent via
  :class:`core.models.EventDelivery`.
* :mod:`core.events.resolvers` — the role × scope recipient resolvers (§3).
* :mod:`core.events.channels` — the pluggable channel adapters (§2.4).
* :mod:`core.events.preferences` — channel-generic, backward-compatible preference
  resolution (§2.7), falling back to the legacy ``NotificationPreference`` columns.
"""

from __future__ import annotations

from core.events.emit import emit
from core.events.registry import (
    EVENTS,
    Channel,
    ChannelDefault,
    ChannelSpec,
    EventType,
    Recipients,
    get_event,
)

__all__ = [
    "EVENTS",
    "Channel",
    "ChannelDefault",
    "ChannelSpec",
    "EventType",
    "Recipients",
    "emit",
    "get_event",
]
