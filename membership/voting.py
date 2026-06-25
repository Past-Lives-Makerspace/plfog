"""Voting-cycle timing + the scheduled 48h-reminder source (design §2.6, §4).

The monthly guild funding vote *closes at month end* — the instant the calendar
month rolls over (midnight on the 1st of the next month, in the project timezone).
Two notification points hang off that close:

* :data:`voting.closing_48h` — fired 48 hours *before* the close, to all voters
  (:func:`closing_48h_occurrences`, walked by the generalized scheduler in
  ``send_voting_reminders``);
* :data:`voting.results_published` — fired *after* the close, once a
  :class:`membership.models.FundingSnapshot` is taken (see ``FundingSnapshot.take``).

This module owns only the *timing* + the scheduler *source*. It supersedes the old
``send_voting_reminders`` (month-end − 3 days, in-app only, ``ScheduledNotificationMarker``
dedupe) and the dead ``voting_cycle_open`` trigger: there is now ONE voting-reminder
path, deduped on :class:`core.models.EventDelivery` via the ``voting:YYYY-MM`` period.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from django.utils import timezone

from core.events.registry import VOTING_CLOSING_48H

if TYPE_CHECKING:
    from collections.abc import Iterable

    from core.events.scheduler import ScheduledOccurrence

# How far before the close the reminder fires (Decision 5: 48 hours).
REMINDER_OFFSET = timedelta(hours=-48)


def cycle_label(moment: datetime) -> str:
    """The voting cycle label for ``moment`` (e.g. ``"June 2026"``)."""
    return moment.strftime("%B %Y")


def cycle_period(moment: datetime) -> str:
    """The dedupe period bucket for the cycle containing ``moment`` (``"voting:YYYY-MM"``)."""
    return f"voting:{moment:%Y-%m}"


def month_end_close(moment: datetime) -> datetime:
    """The close instant for the voting cycle containing ``moment``.

    The cycle closes when the month rolls over: midnight on the 1st of the *next*
    month, in the active (project) timezone. Returned as an aware datetime so the
    scheduler's window math compares like-for-like with ``timezone.now()``.
    """
    if moment.month == 12:
        next_year, next_month = moment.year + 1, 1
    else:
        next_year, next_month = moment.year, moment.month + 1
    naive_close = datetime(next_year, next_month, 1, 0, 0, 0)
    return timezone.make_aware(naive_close, timezone.get_current_timezone())


def closes_on_display(moment: datetime) -> str:
    """A member-friendly date for when the cycle closes (e.g. ``"June 30, 2026"``).

    The close *instant* is midnight on the 1st of next month, but members think of
    voting as closing on the last day of the current month — so we display that last
    day, matching ``membership.cycle.get_cycle_context``.
    """
    last_day = calendar.monthrange(moment.year, moment.month)[1]
    return moment.replace(day=last_day).strftime("%B %d, %Y").replace(" 0", " ")


def closing_48h_occurrences(now: datetime) -> Iterable[ScheduledOccurrence]:
    """Yield the ``voting.closing_48h`` occurrence due around ``now`` (a scheduler source).

    Computes the current cycle's close instant and anchors a single occurrence at
    ``close − 48h``. The scheduler's :func:`core.events.scheduler.run_due` due-checks it
    against the 15-minute tick window and fires it via ``emit`` exactly once per cycle
    (deduped on the ``voting:YYYY-MM`` period). Recipients resolve to ``all_voters``;
    the copy's merge fields ride in the context.

    Note this is a *generator* of at most one occurrence — the proven scheduler shape
    (one source → candidate occurrences → due-check) with no per-feature dedupe row.
    """
    from core.events.scheduler import ScheduledOccurrence

    close = month_end_close(now)
    yield ScheduledOccurrence(
        event_key=VOTING_CLOSING_48H,
        anchor=close,
        offset=REMINDER_OFFSET,
        context={
            "member_name": "there",
            "cycle_label": cycle_label(now),
            "closes_on": closes_on_display(now),
            "voting_url": "/guilds/voting/",
        },
        period=cycle_period(now),
        url="/guilds/voting/",
    )
