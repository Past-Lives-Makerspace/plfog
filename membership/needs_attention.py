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

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.events.emit import emit
from core.events.registry import GUILD_NEEDS_ATTENTION, Channel
from core.models import EventDelivery, Notification
from membership.models import Guild, GuildNeedsAttention, NextMeetingSources

logger = logging.getLogger(__name__)

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
    failed_guild_pks: list[int] = field(default_factory=list)


class NeedsAttentionCheckError(Exception):
    """Some guilds could not be checked. Every other guild was, and each failure is logged."""

    def __init__(self, result: CheckResult) -> None:
        self.result = result
        pks = ", ".join(str(pk) for pk in result.failed_guild_pks)
        super().__init__(f"Needs attention check failed for {len(result.failed_guild_pks)} guild(s): {pks}")


def item_title(guild: Guild) -> str:
    """The inbox item's headline, naming the guild."""
    return f"Needs attention: {guild.name}"


def check_all() -> CheckResult:
    """Run every active guild against every rule: open, nudge, remind and clear.

    A guild that raises is logged with its pk and skipped, so the others are still checked;
    then one :class:`NeedsAttentionCheckError` names every guild that failed, so the scheduled
    run is recorded as failed. A failed guild keeps its open rows.

    Raises:
        NeedsAttentionCheckError: When any guild could not be checked.
    """
    result = CheckResult()
    guilds = list(Guild.objects.filter(is_active=True).prefetch_related("events").order_by("name"))
    result.guilds = len(guilds)
    sources = NextMeetingSources.load(guilds)
    open_rows = {(row.guild_id, row.rule): row for row in GuildNeedsAttention.objects.open()}
    for guild in guilds:
        try:
            with transaction.atomic():
                _check_guild(guild, sources, open_rows, result)
        except Exception:
            logger.exception("Needs attention check failed for guild pk=%s", guild.pk)
            result.failed_guild_pks.append(guild.pk)
    # Whatever is left belongs to a guild that is hidden or deleted now, or to a rule that
    # was removed: nobody should be asked to fix it any more.
    for (guild_pk, _rule_key), row in open_rows.items():
        if guild_pk not in result.failed_guild_pks:
            _clear(row)
            result.cleared += 1
    if result.failed_guild_pks:
        raise NeedsAttentionCheckError(result)
    return result


def _check_guild(
    guild: Guild,
    sources: NextMeetingSources,
    open_rows: dict[tuple[int, str], GuildNeedsAttention],
    result: CheckResult,
) -> None:
    """Run one guild against every rule, taking its handled rows out of ``open_rows``."""
    for rule in RULES:
        row = open_rows.get((guild.pk, rule.key))
        if not rule.is_broken(guild, sources):
            if row is not None:
                _clear(row)
                result.cleared += 1
        else:
            if row is None:
                row = GuildNeedsAttention.objects.create(guild=guild, rule=rule.key)
            result.open += 1
            items, reminders = _nudge(guild, rule, row)
            result.items += items
            result.reminders += reminders
        open_rows.pop((guild.pk, rule.key), None)


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
    member added halfway through is not reminded on the day they are first told, and goes only
    to people who still lead or staff the guild. Someone who left has their open item marked
    read instead. The reminder marks that person's earlier item read, so the bell shows the
    guild once. Returns the number of first items and reminders sent.
    """
    url = rule.url(guild.pk)
    current_user_ids = {member.user_id for member in guild.leadership_members() if member.user_id is not None}
    Notification.objects.unread().filter(trigger=GUILD_NEEDS_ATTENTION, url=url).exclude(
        user_id__in=current_user_ids
    ).update(read_at=timezone.now())
    first_period = _period(guild, rule, row)
    first = _send(guild, rule, first_period)
    due_user_ids = current_user_ids & {
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
            user_id__in=reminded, trigger=GUILD_NEEDS_ATTENTION, url=url, created_at__lt=sent_at
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
