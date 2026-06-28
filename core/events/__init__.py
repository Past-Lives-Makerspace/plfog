"""The event-driven notification spine.

This package is the foundation for the notification redesign described in
``docs/superpowers/plans/2026-06-24-notification-architecture-redesign.md``. After the
migration phases it is the **only** send path: every notification flows through
``emit()`` (the old ``core.notifications.dispatch`` fan-out has been removed). The
legacy ``core/triggers.py`` catalogue is retained only as the structural seed for the
event registry.

Public surface:

* :data:`EVENTS` / :func:`get_event` — the event registry (§2.1), seeded from the
  legacy trigger catalogue.
* :func:`emit` — the single emission point (§2.2): writes the activity row,
  resolves recipients, fans out to enabled channels, idempotent via
  :class:`core.models.EventDelivery`.
* :mod:`core.events.resolvers` — the role × scope recipient resolvers (§3).
* :mod:`core.events.channels` — the pluggable channel adapters (§2.4).
* :mod:`core.events.preferences` — channel-generic preference resolution (§2.7) on the
  unified per-(event, channel) ``NotificationPreference`` model.
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
