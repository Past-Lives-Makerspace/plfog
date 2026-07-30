"""Voting-cycle timing + the per-member scheduled reminder sources.

The monthly guild funding vote *closes at month end* — the instant the calendar
month rolls over (midnight on the 1st of the next month, in the project timezone).
Reminders hang off that close, fired N days before it where N is
``VotingSettings.reminder_lead_days`` (configurable, replacing the old fixed 48h):

* :data:`voting.closing_soon` — to each member who has voted, carrying their own
  recorded 1st/2nd/3rd (:func:`closing_soon_occurrences`);
* :data:`voting.vote_soon` — to each paying, signed-in member who hasn't voted yet
  (:func:`vote_soon_occurrences`);
* :data:`voting.officers_closing_soon` — a turnout heads-up to guild leadership
  (:func:`officers_closing_soon_occurrences`).

Both are *per-member* sources: the spine renders one message per ``emit`` (the
context is shared across recipients), so personalization is done by emitting once
per member — each occurrence carries that member's context and resolves via the
per-member ``registrant`` resolver. They share the same close anchor/offset, so
they are all due in the same 15-minute tick and each member's
:class:`core.models.EventDelivery` (``user:<pk>`` × ``voting:YYYY-MM``) dedupes to
once per cycle.

The :func:`previous_cycle_label` / :func:`close_period` / :func:`cycle_start`
helpers serve the auto-snapshot job (``take_cycle_snapshot``).
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.utils import timezone

from core.events.registry import VOTING_CLOSING_SOON, VOTING_OFFICERS_CLOSING_SOON, VOTING_VOTE_SOON

if TYPE_CHECKING:
    from collections.abc import Iterable

    from core.events.scheduler import ScheduledOccurrence


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


def cycle_start(moment: datetime) -> datetime:
    """The first instant of the month that *just closed* relative to ``moment``.

    Once the calendar has rolled into a new month, the cycle that just closed is the
    previous month — its start is midnight on the 1st of that month (an aware
    datetime). Used as the duplicate-snapshot window guard for the auto-snapshot job.
    Handles the January rollover (previous month is the prior December).
    """
    if moment.month == 1:
        year, month = moment.year - 1, 12
    else:
        year, month = moment.year, moment.month - 1
    naive_start = datetime(year, month, 1, 0, 0, 0)
    return timezone.make_aware(naive_start, timezone.get_current_timezone())


def previous_cycle_label(moment: datetime) -> str:
    """The label of the cycle that just closed relative to ``moment`` (e.g. ``"May 2026"``)."""
    return cycle_start(moment).strftime("%B %Y")


def close_period(moment: datetime) -> str:
    """The once-per-cycle dedupe bucket for the just-closed cycle (``"voting_close:YYYY-MM"``)."""
    return f"voting_close:{cycle_start(moment):%Y-%m}"


def _format_pool(amount: Decimal) -> str:
    """Format a dollar pool for member copy: ``$1,000`` whole, ``$1,050.50`` with cents."""
    if amount == amount.to_integral_value():
        return f"${amount:,.0f}"
    return f"${amount:,.2f}"


def cycle_turnout_stats() -> dict[str, str]:
    """Live turnout + pool figures for the current cycle, as ready-to-render strings.

    Computed once per source run (not per member) so the reminder emails can carry
    "N members have voted" and the running pool without an N+1 over the audience:

    * ``turnout_count`` — active members who have cast a vote;
    * ``not_voted_count`` — paying, signed-in members with no vote yet (the audience
      the "Vote soon" nudge targets);
    * ``pool_display`` — the funding pool as it stands, ``max(paying voters × $10,
      floor)``, matching the snapshot's own formula (:func:`vote_calculator.calculate_results`).
    """
    from membership.models import Member, VotingSettings
    from membership.vote_calculator import DOLLARS_PER_MEMBER

    voted = Member.objects.active().filter(vote_preference__isnull=False)
    turnout_count = voted.count()
    paying_voters = voted.paying().count()
    not_voted_count = (
        Member.objects.paying().active().filter(user__last_login__isnull=False, vote_preference__isnull=True).count()
    )
    floor = VotingSettings.load().minimum_pool_floor
    pool = max(Decimal(DOLLARS_PER_MEMBER * paying_voters), floor)
    return {
        "turnout_count": str(turnout_count),
        "not_voted_count": str(not_voted_count),
        "pool_display": _format_pool(pool),
    }


def closing_soon_occurrences(now: datetime) -> Iterable[ScheduledOccurrence]:
    """Yield one ``voting.closing_soon`` occurrence per voted member (a scheduler source).

    Gated on ``VotingSettings.reminders_enabled``. Fires
    ``reminder_lead_days`` before the close, to every active member who has cast a
    vote, each occurrence carrying that member's own recorded 1st/2nd/3rd. The
    per-member ``registrant`` resolver finds their linked user (dropping no-account /
    no-email members and honoring email opt-outs); the ``voting:YYYY-MM`` period
    dedupes each member to once per cycle.
    """
    from membership.models import Member, VotingSettings

    settings = VotingSettings.load()
    if not settings.reminders_enabled:
        return

    from django.urls import reverse

    from core.events.scheduler import ScheduledOccurrence
    from membership.orientations import _absolute_url

    close = month_end_close(now)
    offset = timedelta(days=-settings.reminder_lead_days)
    label = cycle_label(now)
    closes_on = closes_on_display(now)
    period = cycle_period(now)
    voting_url = _absolute_url(reverse("hub_guild_voting"))
    stats = cycle_turnout_stats()
    voted = (
        Member.objects.active()
        .filter(vote_preference__isnull=False)
        .select_related(
            "user",
            "vote_preference__guild_1st",
            "vote_preference__guild_2nd",
            "vote_preference__guild_3rd",
        )
    )
    for member in voted:
        pref = member.vote_preference
        yield ScheduledOccurrence(
            event_key=VOTING_CLOSING_SOON,
            anchor=close,
            offset=offset,
            context={
                "member": member,
                "member_name": member.display_name,
                "cycle_label": label,
                "closes_on": closes_on,
                "vote_1st": pref.guild_1st.name,
                "vote_2nd": pref.guild_2nd.name,
                "vote_3rd": pref.guild_3rd.name,
                "turnout_count": stats["turnout_count"],
                "pool_display": stats["pool_display"],
                "voting_url": voting_url,
            },
            period=period,
            url=voting_url,
        )


def vote_soon_occurrences(now: datetime) -> Iterable[ScheduledOccurrence]:
    """Yield one ``voting.vote_soon`` occurrence per signed-in member who hasn't voted.

    Gated on ``VotingSettings.reminders_enabled`` *and* ``send_vote_soon_enabled``.
    Same anchor/offset as :func:`closing_soon_occurrences`. The audience is paying,
    active members who have logged in at least once but have no vote yet — nudging a
    non-paying member who can't vote would be noise. The per-member ``registrant``
    resolver + the ``voting:YYYY-MM`` period give the same safety + dedupe.
    """
    from membership.models import Member, VotingSettings

    settings = VotingSettings.load()
    if not (settings.reminders_enabled and settings.send_vote_soon_enabled):
        return

    from django.urls import reverse

    from core.events.scheduler import ScheduledOccurrence
    from membership.orientations import _absolute_url

    close = month_end_close(now)
    offset = timedelta(days=-settings.reminder_lead_days)
    label = cycle_label(now)
    closes_on = closes_on_display(now)
    period = cycle_period(now)
    voting_url = _absolute_url(reverse("hub_guild_voting"))
    stats = cycle_turnout_stats()
    non_voters = (
        Member.objects.paying()
        .active()
        .filter(user__last_login__isnull=False, vote_preference__isnull=True)
        .select_related("user")
    )
    for member in non_voters:
        yield ScheduledOccurrence(
            event_key=VOTING_VOTE_SOON,
            anchor=close,
            offset=offset,
            context={
                "member": member,
                "member_name": member.display_name,
                "cycle_label": label,
                "closes_on": closes_on,
                "turnout_count": stats["turnout_count"],
                "pool_display": stats["pool_display"],
                "voting_url": voting_url,
            },
            period=period,
            url=voting_url,
        )


def officers_closing_soon_occurrences(now: datetime) -> Iterable[ScheduledOccurrence]:
    """Yield the single ``voting.officers_closing_soon`` heads-up to guild leadership.

    Gated on ``VotingSettings.reminders_enabled`` *and* ``send_officer_reminder_enabled``.
    Same close anchor/offset as the member reminders, so it fires in the same tick. This
    is one shared occurrence — the ``ALL_GUILD_LEADS`` resolver expands it to every active
    guild lead, staffer, and FOG guild officer, and ``emit`` fans out per recipient
    (deduped on ``user:<pk>`` × the ``voting:YYYY-MM`` period). The context carries live
    turnout so officers can see how many members still need to vote before rallying them.
    """
    from membership.models import VotingSettings

    settings = VotingSettings.load()
    if not (settings.reminders_enabled and settings.send_officer_reminder_enabled):
        return

    from django.urls import reverse

    from core.events.scheduler import ScheduledOccurrence
    from membership.orientations import _absolute_url

    voting_url = _absolute_url(reverse("hub_guild_voting"))
    stats = cycle_turnout_stats()
    yield ScheduledOccurrence(
        event_key=VOTING_OFFICERS_CLOSING_SOON,
        anchor=month_end_close(now),
        offset=timedelta(days=-settings.reminder_lead_days),
        context={
            "cycle_label": cycle_label(now),
            "closes_on": closes_on_display(now),
            "turnout_count": stats["turnout_count"],
            "not_voted_count": stats["not_voted_count"],
            "pool_display": stats["pool_display"],
            "voting_url": voting_url,
        },
        period=cycle_period(now),
        url=voting_url,
    )
