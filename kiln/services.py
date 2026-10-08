"""Kiln ticket steps that touch more than one model (#691).

Saving a ticket (answers, photos, cover and flags in one step), loading a firing, and
posting to a ticket's reply thread with its notification.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from kiln.access import kiln_guild, maker_type_for
from kiln.forms import ACTION_COVER, ACTION_REMOVE, KilnTicketForm
from kiln.models import KilnFiring, KilnReply, KilnTicket

if TYPE_CHECKING:
    from membership.models import Member


class TicketNotEditable(Exception):
    """The crew loaded the piece while the maker was editing it, so the edit is refused."""


def save_ticket(form: KilnTicketForm, maker: Member) -> KilnTicket:
    """Save a valid ticket form for ``maker`` and apply the button that was pressed.

    New photos are stored first, so a Remove or Make cover pressed in the same request sees
    them. A strict save (Submit, or any save of a ticket already in the queue) puts the
    ticket in the queue and re-runs the automatic flags.

    An existing ticket's row is locked first and checked still editable, so an edit that
    arrives just after the crew loaded the piece cannot write the old status back over the
    load (:func:`load_kiln` takes the same lock).

    Args:
        form: A bound, valid :class:`KilnTicketForm`.
        maker: The signed-in member saving it; becomes the maker of a new ticket.

    Returns:
        The saved ticket.

    Raises:
        TicketNotEditable: The ticket was loaded since the maker opened it.
    """
    with transaction.atomic():
        ticket = form.save(commit=False)
        if ticket.pk is None:
            ticket.maker = maker
        else:
            status = list(KilnTicket.objects.select_for_update().filter(pk=ticket.pk).values_list("status", flat=True))
            if status[0] not in (KilnTicket.Status.DRAFT, KilnTicket.Status.SUBMITTED):
                raise TicketNotEditable(ticket.pk)
        ticket.maker_type = maker_type_for(ticket.maker)
        ticket.save()
        form.save_glazes(ticket)
        for upload in form.new_photos:
            ticket.add_photo(upload)
        if form.action == ACTION_REMOVE and form.target_photo_pk is not None:
            ticket.remove_photo(form.target_photo_pk)
        if form.action == ACTION_COVER and form.target_photo_pk is not None:
            ticket.set_cover(form.target_photo_pk)
        if form.is_strict:
            ticket.submit()
    return ticket


# ---- loading ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadQueue:
    """Every ticket waiting for the kiln, and the counts the Load the Kiln filters show."""

    tickets: list[KilnTicket]

    @property
    def bisque_count(self) -> int:
        return sum(1 for t in self.tickets if t.firing_type == KilnTicket.FiringType.BISQUE)

    @property
    def glaze_count(self) -> int:
        return sum(1 for t in self.tickets if t.firing_type == KilnTicket.FiringType.GLAZE)

    @property
    def flagged_count(self) -> int:
        return sum(1 for t in self.tickets if t.open_flags())

    @property
    def longest_waiting_type(self) -> str:
        """The firing the longest waiting piece needs; bisque when nothing waits."""
        return self.tickets[0].firing_type if self.tickets else KilnTicket.FiringType.BISQUE


def load_queue() -> LoadQueue:
    """The waiting tickets, the longest wait first, with what every tile shows loaded at once."""
    tickets = (
        KilnTicket.objects.waiting()
        .select_related("maker", "clay")
        .prefetch_related("photos", "flags", "studio_glazes")
    )
    return LoadQueue(tickets=list(tickets))


@dataclass(frozen=True)
class LoadResult:
    """What a Confirm loaded did: the new firing (if anything went in) and what was skipped."""

    firing: KilnFiring | None
    loaded: int
    skipped: int

    @property
    def message(self) -> str:
        """The toast the crew member sees."""
        skipped = ""
        if self.skipped:
            noun = "ticket was" if self.skipped == 1 else "tickets were"
            skipped = (
                f" {self.skipped} {noun} left out: already loaded by someone else, changed by the maker, "
                "or not this firing's type."
            )
        if self.firing is None:
            return f"Nothing was loaded.{skipped}"
        noun = "ticket" if self.loaded == 1 else "tickets"
        return f"{self.firing.name} is loaded with {self.loaded} {noun}.{skipped}"


def load_kiln(*, firing_type: str, ticket_pks: Iterable[int], by: Member) -> LoadResult:
    """Start a firing and move the ticked tickets into it, all in one transaction.

    Every ticked row is locked before it is read, so two crew members confirming at once
    cannot both load a piece: the second one waits, then sees it already loaded and skips
    it. A ticket the maker changed to the other firing type, or that is no longer in the
    queue, is skipped the same way. No firing is made when nothing goes in. Makers are not
    notified; their ticket simply reads In the kiln.

    Args:
        firing_type: ``KilnTicket.FiringType`` of the firing.
        ticket_pks: The tickets the crew member ticked.
        by: The crew member confirming the load.

    Returns:
        The firing (or None) and how many tickets went in and were left out.
    """
    wanted = set(ticket_pks)
    with transaction.atomic():
        locked = KilnTicket.objects.select_for_update().filter(pk__in=wanted).order_by("pk")
        going_in = [t.pk for t in locked if t.status == KilnTicket.Status.SUBMITTED and t.firing_type == firing_type]
        if not going_in:
            return LoadResult(firing=None, loaded=0, skipped=len(wanted))
        firing = KilnFiring.start(firing_type, by)
        KilnTicket.objects.filter(pk__in=going_in).update(
            status=KilnTicket.Status.LOADED, firing=firing, updated_at=timezone.now()
        )
    return LoadResult(firing=firing, loaded=len(going_in), skipped=len(wanted) - len(going_in))


# ---- replies ---------------------------------------------------------------------------


def post_reply(ticket: KilnTicket, author: Member, body: str) -> KilnReply:
    """Add a message to a ticket's thread and tell the other side.

    The maker writing tells the crew (the kiln guild's lead and staff, minus the author when
    the maker is crew too); anyone else writing is the crew and tells the maker, who may be
    a class guest. Bell, push and email follow each person's own switches. No Discord.
    """
    reply = KilnReply.objects.create(ticket=ticket, author=author, body=body)
    _notify_reply(reply)
    return reply


def _notify_reply(reply: KilnReply) -> None:
    from core.events.emit import emit
    from core.events.registry import KILN_CREW_REPLIED, KILN_MAKER_REPLIED

    ticket, author = reply.ticket, reply.author
    context: dict[str, object] = {
        "author_name": reply.author_name,
        "ticket_label": f"Ticket {ticket.pk}",
        "ticket_summary": ticket.summary,
        "reply_body": reply.body,
        "ticket_url": ticket.member_url,
    }
    # The period is the message itself: each one notifies once, and a retry never twice.
    period = f"reply-{reply.pk}"
    if reply.from_crew:
        emit(
            KILN_CREW_REPLIED,
            target=reply,
            context={**context, "user": ticket.maker.user},
            url=ticket.member_url,
            period=period,
        )
        return
    guild = kiln_guild()
    crew = guild.leadership_members() if guild is not None else []
    crew_user_ids = {m.user_id for m in crew if m.user_id is not None and m.pk != author.pk}
    emit(
        KILN_MAKER_REPLIED,
        target=reply,
        context={**context, "guild": guild},
        url=ticket.member_url,
        period=period,
        recipient_user_ids=crew_user_ids,
    )
