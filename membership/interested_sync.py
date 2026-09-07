"""Fold Discord's native Scheduled-Event "Interested" marks into event RSVPs.

The Events tab is the one RSVP surface a bot cannot put a button on — members click
Discord's own Interested bell there. This module mirrors that list into the same
:class:`~membership.models.EventRSVP` table the ✅ button and the hub page feed, so all
three doors converge on one attendee list. One-way per source: an Interested mark adds
an ``interested``-sourced RSVP; clearing it removes only that row — button and hub RSVPs
are untouchable here (:meth:`CommunityEvent.reconcile_interested` enforces it).

Runs every 15 minutes via the scheduled-job dispatcher (``sync_interested_rsvps``).
Best-effort per event: a Discord error on one event (deleted remotely, rate limit) is
logged and skipped, never aborting the sweep.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def sync_interested_rsvps() -> int:
    """Sweep every upcoming pushed event; return how many had their RSVP list changed.

    Scope: PUBLISHED/SCHEDULED CommunityEvents holding a ``discord_event_id`` that are
    still upcoming (end in the future, or recurring — the same "still alive" shape the
    ``/cancel`` picker uses). A changed list refreshes the announcement embed so the
    Attendees field stays live; the refresh is best-effort by design.
    """
    from django.db.models import Q
    from django.utils import timezone

    from core.integrations.discord_events import DiscordEventsError, DiscordScheduledEventsClient
    from membership.models import CommunityEvent

    client = DiscordScheduledEventsClient.from_settings()
    if not client.enabled:
        return 0

    states = (CommunityEvent.ModerationState.PUBLISHED, CommunityEvent.ModerationState.SCHEDULED)
    events = (
        CommunityEvent.objects.filter(moderation_state__in=states)
        .exclude(discord_event_id="")
        .filter(Q(ends_at__gte=timezone.now()) | ~Q(recurrence=CommunityEvent.Recurrence.NONE))
    )
    changed_count = 0
    for event in events:
        try:
            interested = set(client.list_interested_user_ids(client.server_id, event.discord_event_id))
        except DiscordEventsError:
            logger.warning("Interested sync skipped event %s: Discord call failed.", event.pk, exc_info=True)
            continue
        if event.reconcile_interested(interested):
            changed_count += 1
            event.refresh_discord_announcement()
    return changed_count
