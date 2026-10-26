"""Needs attention: the checks that hold guild staff to what they should be doing regularly (#953).

Each :class:`Rule` in :data:`RULES` is one thing a guild should have in order. Once a day
``check_needs_attention`` runs every active guild against every rule. A guild that breaks one
opens a :class:`~membership.models.GuildNeedsAttention` row, and each of its leads and staff gets
one inbox item for it, plus one reminder 30 days later while it stays open. When the guild is
fixed, the row closes and the open items are marked read: on the next daily run, or at once when
the guild's settings are saved (:func:`clear_fixed`).

A second rule is a new entry in :data:`RULES` plus its specs. Two rules must link to different
places, because a fixed rule clears the inbox items by their link.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.events.emit import emit
from core.events.registry import GUILD_NEEDS_ATTENTION, Channel
from core.models import EventDelivery, Notification
from membership.models import Guild, GuildNeedsAttention, NextMeetingSources

REMINDER_AFTER = timedelta(days=30)


@dataclass(frozen=True)
class Rule:
    """One Needs attention rule.

    Args:
        key: Stable identifier, stored on :class:`GuildNeedsAttention` and in the delivery periods.
        body: The inbox item's text.
        tab: The guild settings tab the item opens (``hub_guild_edit`` with ``?tab=``).
        is_broken: Whether a guild breaks the rule, given the sources loaded for every guild.
    """

    key: str
    body: str
    tab: str
    is_broken: Callable[[Guild, NextMeetingSources], bool]

    def url(self, guild_pk: int) -> str:
        """Where the inbox item goes: the guild's settings, on this rule's tab."""
        return f"{reverse('hub_guild_edit', args=[guild_pk])}?tab={self.tab}"


def _nothing_coming_up(guild: Guild, sources: NextMeetingSources) -> bool:
    return not guild.has_something_coming_up(sources)


RULES: list[Rule] = [
    Rule(
        key="nothing_coming_up",
        body="No upcoming meeting has been set for your guild yet. Would you like to set one up now?",
        tab="meetings",
        is_broken=_nothing_coming_up,
    ),
]

_RULES_BY_KEY: dict[str, Rule] = {rule.key: rule for rule in RULES}


@dataclass
class CheckResult:
    """What one run of the check did, for the job's output line."""

    guilds: int = 0
    open: int = 0
    items: int = 0
    reminders: int = 0
    cleared: int = 0


def item_title(guild: Guild) -> str:
    """The inbox item's headline, naming the guild."""
    return f"Needs attention: {guild.name}"


def check_all() -> CheckResult:
    """Run every active guild against every rule: open, nudge, remind and clear."""
    result = CheckResult()
    guilds = list(Guild.objects.filter(is_active=True).prefetch_related("events").order_by("name"))
    result.guilds = len(guilds)
    sources = NextMeetingSources.load(guilds)
    open_rows = {(row.guild_id, row.rule): row for row in GuildNeedsAttention.objects.open()}
    for guild in guilds:
        for rule in RULES:
            row = open_rows.pop((guild.pk, rule.key), None)
            if not rule.is_broken(guild, sources):
                if row is not None:
                    _clear(row)
                    result.cleared += 1
                continue
            if row is None:
                row = GuildNeedsAttention.objects.create(guild=guild, rule=rule.key)
            result.open += 1
            items, reminders = _nudge(guild, rule, row)
            result.items += items
            result.reminders += reminders
    # Whatever is left belongs to a guild that is hidden or deleted now, or to a rule that
    # was removed: nobody should be asked to fix it any more.
    for row in open_rows.values():
        _clear(row)
        result.cleared += 1
    return result


def clear_fixed(guild: Guild) -> int:
    """Close this guild's open rows whose rule it no longer breaks, at once; returns how many.

    Called when the guild's settings are saved, so fixing it from the item's link clears the
    item without waiting for the daily run. One query when nothing is open.
    """
    rows = list(GuildNeedsAttention.objects.open().filter(guild=guild))
    if not rows:
        return 0
    guild = Guild.objects.prefetch_related("events").get(pk=guild.pk)
    sources = NextMeetingSources.load([guild])
    cleared = 0
    for row in rows:
        rule = _RULES_BY_KEY.get(row.rule)
        if rule is None or not rule.is_broken(guild, sources):
            _clear(row)
            cleared += 1
    return cleared


def _clear(row: GuildNeedsAttention) -> None:
    """Close the stretch and mark its open inbox items read, for everyone it reached."""
    with transaction.atomic():
        row.cleared_at = timezone.now()
        row.save(update_fields=["cleared_at"])
        rule = _RULES_BY_KEY.get(row.rule)
        if rule is not None:
            Notification.objects.mark_read_for(url=rule.url(row.guild_id), triggers=[GUILD_NEEDS_ATTENTION])


def _period(guild: Guild, rule: Rule, row: GuildNeedsAttention) -> str:
    return f"guild:{guild.pk}:{rule.key}:{row.pk}"


def _nudge(guild: Guild, rule: Rule, row: GuildNeedsAttention) -> tuple[int, int]:
    """Send the stretch's first item to whoever has not had it, then any reminders now due.

    The delivery ledger keeps it to one item per person per stretch, so a daily re-run sends
    nothing new. A person's reminder is due 30 days after their own first item, so a staff
    member added halfway through is not reminded on the day they are first told. The reminder
    marks that person's earlier item read, so the bell shows the guild once. Returns the number
    of first items and reminders sent.
    """
    first_period = _period(guild, rule, row)
    first = _send(guild, rule, first_period)
    due_user_ids = {
        int(target_ref.removeprefix("user:"))
        for target_ref in EventDelivery.objects.filter(
            event_key=GUILD_NEEDS_ATTENTION,
            channel=Channel.IN_APP.value,
            period=first_period,
            created_at__lte=timezone.now() - REMINDER_AFTER,
        ).values_list("target_ref", flat=True)
    }
    if not due_user_ids:
        return len(first), 0
    sent_at = timezone.now()
    reminded = _send(guild, rule, f"{first_period}:reminder", recipient_user_ids=due_user_ids)
    if reminded:
        Notification.objects.unread().filter(
            user_id__in=reminded, trigger=GUILD_NEEDS_ATTENTION, url=rule.url(guild.pk), created_at__lt=sent_at
        ).update(read_at=timezone.now())
    return len(first), len(reminded)


def _send(guild: Guild, rule: Rule, period: str, *, recipient_user_ids: set[int] | None = None) -> set[int]:
    """Emit the rule's item for the guild in ``period``; returns who got a new inbox item.

    Without ``recipient_user_ids`` it goes to the guild's lead and staff (the event's resolver).
    """
    result = emit(
        GUILD_NEEDS_ATTENTION,
        context={"guild": guild},
        title=item_title(guild),
        body=rule.body,
        url=rule.url(guild.pk),
        period=period,
        recipient_user_ids=recipient_user_ids,
    )
    return {user_id for user_id, channel in result.delivered if channel is Channel.IN_APP}
