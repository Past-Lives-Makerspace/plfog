"""The cubby waitlist (#976 part 4, #980): members waiting for any cubby in a guild whose cubbies are all taken.

A heads up, never a hold. Every change that opens a cubby calls :func:`open_for_waitlist`, which
waits for that change to commit and then tells the member first in line (``cubbies.waitlist_opened``,
bell and a forced email) and takes them off the list. Whoever rents first gets the cubby, so a
notified member who was too slow joins again at the back.

* **One opening, one notice.** The pick runs under the guild row lock every cubby change takes, so
  two cubbies opening at once tell two different members, never the same one twice. It re-reads
  the cubby under that lock and tells nobody when the cubby is not open any more (rented again,
  out of service, archived, or the guild's Cubbies tab is off).
* **No notice for a change that rolled back,** because nothing is picked until the change commits.
* **A retried webhook tells nobody new:** the callers call only on the change itself (a rental
  becoming Ended, a hold deleted), and a retry finds the rental already Ended.
* **One waiting entry per member per guild** (``uq_cubbywait_open``). Leaving and joining again
  makes a new entry at the back. A member who already rents here may still join.

Each cubby that opens tells one person; nothing is scheduled.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from membership.models import Cubby, CubbyRental, CubbyWaitlistEntry, Guild

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from membership.models import Member

logger = logging.getLogger(__name__)

#: The event a member on the list hears when a cubby opens.
OPENED_EVENT = "cubbies.waitlist_opened"


class WaitlistError(Exception):
    """A waitlist action refused, with the sentence the member sees."""


def _lock_guild(guild_id: int) -> None:
    """Part 1's one lock for every cubby change (``membership.cubbies``)."""
    Guild.objects.select_for_update().filter(pk=guild_id).first()


# ── Reads ────────────────────────────────────────────────────────────────────


def _rentable_cubbies(guild: Guild) -> QuerySet[Cubby]:
    """The guild's cubbies a member could rent right now: active, in service and held by nobody."""
    return (
        Cubby.objects.active()
        .filter(unit__guild=guild, out_of_service=False)
        .exclude(rentals__status__in=CubbyRental.LIVE_STATUSES)
    )


def is_full(guild: Guild) -> bool:
    """Whether the guild has cubbies and none is open: rented, held, out of service or archived all count as taken."""
    return Cubby.objects.active().filter(unit__guild=guild).exists() and not _rentable_cubbies(guild).exists()


def entry_for(guild: Guild, member: Member) -> CubbyWaitlistEntry | None:
    """The member's waiting entry in ``guild``, or None."""
    return CubbyWaitlistEntry.objects.waiting().filter(guild=guild, member=member).first()


def place_in_line(entry: CubbyWaitlistEntry) -> int:
    """Where a waiting entry stands, counting from 1: how many waiting entries joined before it, plus one."""
    return (
        CubbyWaitlistEntry.objects.waiting()
        .filter(guild_id=entry.guild_id, joined_at__lte=entry.joined_at)
        .exclude(joined_at=entry.joined_at, pk__gt=entry.pk)
        .count()
    )


def line(guild: Guild) -> list[CubbyWaitlistEntry]:
    """The guild's waiting entries in the order they are told, with each member loaded."""
    return list(CubbyWaitlistEntry.objects.in_line().filter(guild=guild).select_related("member"))


def waiting_count(guild: Guild) -> int:
    """How many are waiting in ``guild``, for the staff Waitlist chip."""
    return CubbyWaitlistEntry.objects.waiting().filter(guild=guild).count()


# ── The member's buttons and staff Remove ────────────────────────────────────


def join(guild: Guild, member: Member) -> CubbyWaitlistEntry:
    """Notify Me When One Opens: the member's waiting entry, made at the back when they have none.

    Under the guild lock, so a double tap keeps one entry. Refused while a cubby is open (rent it
    instead) or while the guild shows no cubbies to members.

    Raises:
        WaitlistError: The guild's Cubbies tab is off, it has no cubbies, or one is open.
    """
    with transaction.atomic():
        _lock_guild(guild.pk)
        guild.refresh_from_db(fields=["show_cubbies_tab"])
        existing = entry_for(guild, member)
        if existing is not None:
            return existing
        if not guild.show_cubbies_tab or not Cubby.objects.active().filter(unit__guild=guild).exists():
            raise WaitlistError("This guild isn't renting cubbies right now.")
        if not is_full(guild):
            raise WaitlistError("A cubby is open right now, so you can rent it instead.")
        return CubbyWaitlistEntry.objects.create(guild=guild, member=member)


def leave(guild: Guild, member: Member) -> bool:
    """Leave the List: True when the member was waiting here and is not any more."""
    return bool(CubbyWaitlistEntry.objects.waiting().filter(guild=guild, member=member).update(left_at=timezone.now()))


def remove(entry: CubbyWaitlistEntry) -> bool:
    """Guild staff's Remove: the entry leaves the line, and the member is not told. False when it already had."""
    return bool(CubbyWaitlistEntry.objects.waiting().filter(pk=entry.pk).update(left_at=timezone.now()))


def leave_everywhere(member: Member) -> None:
    """A member deleting their account leaves every list at once, so no opening is spent on them."""
    CubbyWaitlistEntry.objects.waiting().filter(member=member).update(left_at=timezone.now())


# ── A cubby opened ───────────────────────────────────────────────────────────


def open_for_waitlist(cubby: Cubby) -> None:
    """``cubby`` just became open: once this change commits, tell the member first in its guild's line.

    Call it from inside the change that opened the cubby (or right after it). Nothing happens if
    the change rolls back. A failure while telling is logged, never raised into the caller, so a
    webhook that ended a rental still answers 200.
    """
    cubby_pk = cubby.pk

    def notify_waitlist() -> None:
        # A named function, not a partial: Django logs a robust callback's failure by its __qualname__.
        notify_next(cubby_pk)

    transaction.on_commit(notify_waitlist, robust=True)


def open_all_for_waitlist(guild: Guild) -> None:
    """Every open cubby in ``guild`` counts as an opening (its Cubbies tab just came on): one notice each."""
    for cubby in _rentable_cubbies(guild):
        open_for_waitlist(cubby)


def notify_next(cubby_pk: int) -> CubbyWaitlistEntry | None:
    """Tell the member first in line that ``cubby_pk`` is open, and take them off the list.

    Under the guild lock: the cubby must still be open and the guild's tab on, or nobody is told.
    The pick skips a member whose account was deleted or switched off.

    Returns:
        The entry told, or None when the cubby is not open any more or nobody is waiting.
    """
    cubby = Cubby.objects.select_related("unit__guild").get(pk=cubby_pk)
    guild = cubby.unit.guild
    with transaction.atomic():
        _lock_guild(guild.pk)
        guild.refresh_from_db(fields=["show_cubbies_tab"])
        if not guild.show_cubbies_tab or not _rentable_cubbies(guild).filter(pk=cubby.pk).exists():
            return None
        entry = (
            CubbyWaitlistEntry.objects.in_line()
            .filter(guild=guild, member__deleted_at__isnull=True, member__user__is_active=True)
            .select_related("member__user")
            .first()
        )
        if entry is None:
            return None
        entry.notified_at = timezone.now()
        entry.notified_cubby = cubby
        entry.save(update_fields=["notified_at", "notified_cubby"])
    notify_opened(entry, cubby)
    return entry


def email_context(entry: CubbyWaitlistEntry, cubby: Cubby) -> dict[str, object]:
    """What ``membership/emails/cubby_waitlist_opened.{txt,html}`` reads."""
    from membership.cubby_rentals import _absolute_url, tab_url

    guild = cubby.unit.guild
    return {
        "entry": entry,
        "cubby": cubby,
        "guild": guild,
        "first_name": entry.member.display_name.split(" ")[0],
        "tab_url": _absolute_url(tab_url(guild)),
        "guild_url": _absolute_url(reverse("hub_guild_detail", args=[guild.slug])),
    }


def opened_subject(cubby: Cubby) -> str:
    """ "A cubby just opened at Ceramics Guild": the email subject and the bell title."""
    return f"A cubby just opened at {cubby.unit.guild.name}"


def notify_opened(entry: CubbyWaitlistEntry, cubby: Cubby) -> None:
    """``cubbies.waitlist_opened``: the bell and a forced email, once per entry."""
    from core.events.senders import emit_with_email_shell
    from membership.cubby_rentals import tab_url

    subject = opened_subject(cubby)
    emit_with_email_shell(
        OPENED_EVENT,
        target=entry,
        context={"user": entry.member.user},
        subject=subject,
        text_template="membership/emails/cubby_waitlist_opened.txt",
        html_template="membership/emails/cubby_waitlist_opened.html",
        template_context=email_context(entry, cubby),
        in_app_title=subject,
        in_app_body=f"Cubby {cubby.label} on the {cubby.unit.name} is open. Whoever rents it first gets it.",
        url=tab_url(cubby.unit.guild),
        period=f"cubby_waitlist:{entry.pk}:opened",
    )
