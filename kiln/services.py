"""Kiln ticket steps that touch more than one model (#691).

Saving a ticket (answers, photos, cover and flags in one step), loading a firing, posting
to a ticket's reply thread with its notification, and unloading a firing with its ready
for pickup notices; plus the reads behind a maker's kiln home (the Ceramics Guild's Kiln
Tickets tab, or a guest's ``/kiln/``) and the crew's Unload, Kiln Log and firing pages.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.urls import reverse
from django.utils import dateformat, timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

from kiln.access import can_file_tickets, can_run_kiln, kiln_home_url, kiln_is_open, maker_type_for
from kiln.forms import ACTION_COVER, ACTION_REMOVE, ExceptionNote, KilnTicketForm
from kiln.models import KilnFiring, KilnReply, KilnTicket, member_name

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from membership.models import Guild, Member

# How many fired tickets a kiln home shows before "Show older tickets".
HISTORY_PAGE = 10


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


def primary_emails(user_path: str) -> Prefetch:
    """Prefetch the primary email of the users at ``user_path``, where ``Member.primary_email`` reads it.

    Without it, naming a member with no name on file (``kiln.models.member_name``) costs a
    query for each one on the page.
    """
    from allauth.account.models import EmailAddress

    return Prefetch(
        f"{user_path}__emailaddress_set",
        queryset=EmailAddress.objects.filter(primary=True),
        to_attr="_primary_emailaddresses",
    )


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
    """The waiting tickets, the longest wait first, with what every tile shows loaded at once.

    The makers' primary email rows come along too: a maker with no name on file is named by
    their email (``member_name``), which would otherwise cost a query per tile.
    """
    tickets = (
        KilnTicket.objects.waiting()
        .select_related("maker__user", "clay")
        .prefetch_related("photos", "flags", "studio_glazes", primary_emails("maker__user"))
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
    from core.events.resolvers import kiln_crew

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
    # The kiln crew minus the author: a crew member writing on their own ticket is not told.
    crew_user_ids = {user.pk for user, _reason in kiln_crew({}) if user.pk != author.user_id}
    emit(
        KILN_MAKER_REPLIED,
        target=reply,
        context=context,
        url=ticket.member_url,
        period=period,
        recipient_user_ids=crew_user_ids,
    )


# ---- unloading -------------------------------------------------------------------------


class FiringAlreadyUnloaded(Exception):
    """Another crew member unloaded this firing first; nothing was changed or sent."""

    def __init__(self, firing: KilnFiring) -> None:
        super().__init__(firing.pk)
        self.firing = firing

    @property
    def message(self) -> str:
        """The toast the second crew member sees."""
        firing = self.firing
        when = dateformat.format(timezone.localtime(firing.unloaded_at), "g:i a")
        return (
            f"{firing.name} was already unloaded by {firing.unloaded_by_name} at {when}. "
            "Nothing changed and no one was notified twice."
        )


def in_kiln_firings() -> list[KilnFiring]:
    """The firings waiting to be unloaded, oldest load first, with their ticket counts and loaders."""
    return list(
        KilnFiring.objects.in_kiln()
        .select_related("loaded_by__user")
        .prefetch_related(primary_emails("loaded_by__user"))
        .annotate(ticket_count=Count("tickets"))
    )


def unload_sheet(firing: KilnFiring) -> list[KilnTicket]:
    """The tickets still in ``firing``, as Unload shows them: cover photo, maker and glazes loaded at once."""
    return list(
        firing.tickets.filter(status=KilnTicket.Status.LOADED)
        .select_related("maker__user", "clay")
        .prefetch_related("photos", "studio_glazes", primary_emails("maker__user"))
        .order_by("maker_id", "pk")
    )


@dataclass(frozen=True)
class UnloadResult:
    """What Mark fired and notify did."""

    firing: KilnFiring
    fired: int
    returned: int
    notified: int

    @property
    def message(self) -> str:
        """The toast the crew member sees."""
        fired = f"{self.fired} ticket{'' if self.fired == 1 else 's'} ready for pickup"
        returned = f", {self.returned} back in the queue" if self.returned else ""
        makers = f"{self.notified} maker{'' if self.notified == 1 else 's'} notified"
        return f"{self.firing.name} is unloaded: {fired}{returned}. {makers}."


def unload_kiln(*, firing_pk: int, exceptions: dict[int, ExceptionNote], by: Member) -> UnloadResult:
    """Unload a firing: mark its tickets fired or send them back, record the notes, tell each maker once.

    One transaction locks the firing row first, so a second crew member pressing Mark fired
    and notify on the same firing waits, then finds it unloaded and is refused
    (:class:`FiringAlreadyUnloaded`) with nothing written and nothing sent. Every ticket of
    the firing still in the kiln is fired unless it is in ``exceptions``; an exception is
    fired with its note, or goes back to the queue (status In the queue, firing cleared, so
    the maker can edit it again). Each note is saved on the ticket's thread as a crew reply
    that carries the firing, what happened and the outcome. It does not send a message
    notification of its own: the maker reads it in the one ready for pickup notice
    (:func:`_notify_makers`), sent after the transaction commits.

    Args:
        firing_pk: The firing being unloaded.
        exceptions: The unticked tickets by pk. A pk that is not in the firing is ignored.
        by: The crew member unloading.

    Returns:
        How many tickets were fired and sent back, and how many makers were notified.

    Raises:
        FiringAlreadyUnloaded: The firing was unloaded before this request got the lock.
        KilnFiring.DoesNotExist: No such firing.
    """
    with transaction.atomic():
        firing = KilnFiring.objects.select_for_update().get(pk=firing_pk)
        if firing.is_unloaded:
            raise FiringAlreadyUnloaded(_with_unloader(firing))
        # Lock the rows on their own: a select_related join through the maker's nullable user
        # would be an outer join, which PostgreSQL refuses to lock.
        tickets = list(firing.tickets.select_for_update().filter(status=KilnTicket.Status.LOADED).order_by("pk"))
        now = timezone.now()
        notes = [exceptions[t.pk] for t in tickets if t.pk in exceptions]
        returned = [n.ticket_pk for n in notes if n.back_to_queue]
        fired = [t.pk for t in tickets if t.pk not in returned]
        KilnTicket.objects.filter(pk__in=fired).update(status=KilnTicket.Status.FIRED, fired_at=now, updated_at=now)
        KilnTicket.objects.filter(pk__in=returned).update(
            status=KilnTicket.Status.SUBMITTED, firing=None, updated_at=now
        )
        replies = KilnReply.objects.bulk_create(
            [
                KilnReply(
                    ticket_id=n.ticket_pk,
                    author=by,
                    body=n.note,
                    firing=firing,
                    what_happened=n.what_happened,
                    outcome=n.outcome,
                )
                for n in notes
            ]
        )
        firing.unloaded_by = by
        firing.unloaded_at = now
        firing.save(update_fields=["unloaded_by", "unloaded_at"])
    notified = _notify_makers(firing, [t.pk for t in tickets], {r.ticket_id: r for r in replies}, by)
    return UnloadResult(firing=firing, fired=len(fired), returned=len(returned), notified=notified)


def _with_unloader(firing: KilnFiring) -> KilnFiring:
    """The firing again with who unloaded it, for the refusal's toast."""
    return (
        KilnFiring.objects.select_related("unloaded_by__user")
        .prefetch_related(primary_emails("unloaded_by__user"))
        .get(pk=firing.pk)
    )


# ---- the ready for pickup notice ---------------------------------------------------------


def _ticket_numbers(tickets: list[KilnTicket]) -> str:
    """ "Ticket 409", "Tickets 409 and 412", "Tickets 401, 409 and 412"."""
    numbers = [str(t.pk) for t in tickets]
    if len(numbers) == 1:
        return f"Ticket {numbers[0]}"
    return f"Tickets {', '.join(numbers[:-1])} and {numbers[-1]}"


@dataclass(frozen=True)
class PickupNotice:
    """One maker's notice for one unloaded firing: every piece of theirs in it, and the crew's notes.

    A maker whose pieces all came out gets "ready for pickup"; one whose only piece went back
    to the queue gets "back in the queue" with the note; a maker with both gets one notice
    that says both. Never more than one per maker per firing.
    """

    firing: KilnFiring
    unloaded_by: Member
    fired: list[KilnTicket]
    returned: list[KilnTicket]
    notes: dict[int, KilnReply]

    @property
    def headline(self) -> str:
        if not self.returned:
            return "Your piece is ready for pickup" if len(self.fired) == 1 else "Your pieces are ready for pickup"
        if not self.fired:
            return "Your piece is back in the queue" if len(self.returned) == 1 else "Your pieces are back in the queue"
        return "Some of your pieces are ready for pickup"

    @property
    def summary(self) -> str:
        """The bell's line: what came out, what went back, and whether the crew left a note."""
        parts: list[str] = []
        if self.fired:
            pronoun = "It is" if len(self.fired) == 1 else "They are"
            parts.append(
                f"{_ticket_numbers(self.fired)} came out of {self.firing.name}. {pronoun} on the pickup shelf."
            )
        if self.returned:
            verb = "was" if len(self.returned) == 1 else "were"
            parts.append(f"{_ticket_numbers(self.returned)} {verb} not fired and {verb} put back in the queue.")
        if self.notes:
            parts.append("The kiln crew left you a note.")
        return " ".join(parts)

    def _outcome(self, ticket: KilnTicket) -> str:
        return "Back in the queue" if ticket in self.returned else "Ready for pickup"

    def _note_text(self, ticket: KilnTicket) -> str:
        note = self.notes.get(ticket.pk)
        if note is None:
            return ""
        return f"{note.get_what_happened_display()}. {member_name(self.unloaded_by)}: {note.body}"

    @property
    def tickets(self) -> list[KilnTicket]:
        return sorted(self.fired + self.returned, key=lambda t: t.pk)

    @property
    def pieces_text(self) -> str:
        """One line per piece for the plain text email, with any note under it."""
        lines: list[str] = []
        for ticket in self.tickets:
            lines.append(f"{ticket} ({ticket.summary}): {self._outcome(ticket)}")
            note = self._note_text(ticket)
            if note:
                lines.append(f"  {note}")
        return "\n".join(lines)

    @property
    def pieces_block(self) -> SafeString:
        """The pieces as the email's table, escaped here because the renderer passes it through."""
        rows = format_html_join(
            "",
            '<tr><td style="padding:12px;border-top:1px solid #e3e7ec;">'
            '<a href="{}" style="color:#092E4C;font-weight:700;">{}</a> '
            '<span style="font-size:14px;color:#5B6B77;">{} · {}</span>{}</td></tr>',
            (
                (
                    ticket_url(ticket),
                    str(ticket),
                    ticket.summary,
                    self._outcome(ticket),
                    format_html('<br><span style="white-space:pre-line;">{}</span>', self._note_text(ticket))
                    if ticket.pk in self.notes
                    else "",
                )
                for ticket in self.tickets
            ),
        )
        return format_html(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="margin:0 0 22px;border:1px solid #e3e7ec;border-radius:10px;">{}</table>',
            rows,
        )

    @property
    def unloaded_line(self) -> str:
        when = dateformat.format(timezone.localtime(self.firing.unloaded_at), "D, M j")
        return f"Unloaded {when} by {member_name(self.unloaded_by)}."

    @property
    def url(self) -> str:
        """The one ticket when there is one, else My Tickets."""
        tickets = self.tickets
        return ticket_url(tickets[0]) if len(tickets) == 1 else f"{settings.MEMBER_BASE_URL}{reverse('kiln:mine')}"


def ticket_url(ticket: KilnTicket) -> str:
    """Absolute link to a ticket's page, for a notice."""
    return f"{settings.MEMBER_BASE_URL}{reverse('kiln:detail', args=[ticket.pk])}"


def _notify_makers(firing: KilnFiring, ticket_pks: list[int], notes: dict[int, KilnReply], by: Member) -> int:
    """Send each maker with a piece in ``firing`` one notice, and say how many it reached.

    The period is the firing, so even a retry of the same firing never tells a maker twice.
    The crew member unloading is not told about their own pieces. Guests (class students)
    are told like members: the notice goes to the maker's account.
    """
    from core.events.emit import emit
    from core.events.registry import KILN_READY_FOR_PICKUP

    tickets = (
        KilnTicket.objects.filter(pk__in=ticket_pks)
        .exclude(maker=by)
        .select_related("maker__user")
        .order_by("maker_id", "pk")
    )
    by_maker: dict[int, list[KilnTicket]] = {}
    for ticket in tickets:
        by_maker.setdefault(ticket.maker_id, []).append(ticket)
    reached = 0
    for maker_tickets in by_maker.values():
        notice = PickupNotice(
            firing=firing,
            unloaded_by=by,
            fired=[t for t in maker_tickets if t.status == KilnTicket.Status.FIRED],
            returned=[t for t in maker_tickets if t.status == KilnTicket.Status.SUBMITTED],
            notes={t.pk: notes[t.pk] for t in maker_tickets if t.pk in notes},
        )
        sent = emit(
            KILN_READY_FOR_PICKUP,
            target=firing,
            context={
                "user": maker_tickets[0].maker.user,
                "headline": notice.headline,
                "summary": notice.summary,
                "firing_name": firing.name,
                "pieces_text": notice.pieces_text,
                "pieces_block": notice.pieces_block,
                "unloaded_line": notice.unloaded_line,
                "tickets_url": notice.url,
            },
            url=notice.url,
            period=f"firing-{firing.pk}",
        )
        # A member with no login (kept on purpose after account merges) has nobody to tell.
        reached += sent.recipient_count > 0
    return reached


# ---- the kiln log ----------------------------------------------------------------------


def _exceptions() -> Prefetch:
    return Prefetch("exceptions", queryset=KilnReply.objects.order_by("pk"))


def kiln_log() -> list[KilnFiring]:
    """Every firing, newest first, with who loaded and unloaded it, its ticket count and its exceptions.

    A ticket sent back to the queue at the unload has left the firing, so it is counted
    through its note: the count is the pieces that went in.
    """
    return list(
        KilnFiring.objects.order_by("-loaded_at", "-pk")
        .select_related("loaded_by__user", "unloaded_by__user")
        .prefetch_related(primary_emails("loaded_by__user"), primary_emails("unloaded_by__user"), _exceptions())
        .annotate(
            ticket_count=Count("tickets", distinct=True)
            + Count(
                "exceptions",
                filter=Q(exceptions__outcome=KilnReply.Outcome.BACK_TO_QUEUE),
                distinct=True,
            )
        )
    )


@dataclass(frozen=True)
class FiringRow:
    """A ticket on a firing's page, with the crew's unload note when it has one."""

    ticket: KilnTicket
    note: KilnReply | None

    @property
    def returned(self) -> bool:
        return self.note is not None and self.note.outcome == KilnReply.Outcome.BACK_TO_QUEUE


def firing_sheet(pk: int) -> tuple[KilnFiring, list[FiringRow]]:
    """One firing and every ticket that went in, including the ones sent back to the queue.

    Raises:
        KilnFiring.DoesNotExist: No such firing.
    """
    tile = ("maker__user", "clay")
    tile_prefetch = ("photos", "studio_glazes", primary_emails("maker__user"))
    firing = (
        KilnFiring.objects.select_related("loaded_by__user", "unloaded_by__user")
        .prefetch_related(
            primary_emails("loaded_by__user"),
            primary_emails("unloaded_by__user"),
            Prefetch(
                "exceptions",
                queryset=KilnReply.objects.select_related(*(f"ticket__{p}" for p in tile)).prefetch_related(
                    *(f"ticket__{p}" for p in ("photos", "studio_glazes")),
                    primary_emails("ticket__maker__user"),
                ),
            ),
            Prefetch(
                "tickets",
                queryset=KilnTicket.objects.select_related(*tile).prefetch_related(*tile_prefetch),
            ),
        )
        .get(pk=pk)
    )
    notes = {note.ticket_id: note for note in firing.exceptions.all()}
    rows = [FiringRow(ticket=t, note=notes.pop(t.pk, None)) for t in firing.tickets.all()]
    rows += [FiringRow(ticket=note.ticket, note=note) for note in notes.values()]
    return firing, sorted(rows, key=lambda r: r.ticket.pk)


# ---- a maker's kiln home ---------------------------------------------------------------


def tickets_for(member: Member) -> QuerySet[KilnTicket]:
    """A maker's tickets, fired ones by when they came out (newest first), with what a row shows."""
    return (
        member.kiln_tickets.newest_fired_first()  # type: ignore[attr-defined]  # KilnTicketQuerySet manager
        .select_related("clay")
        .prefetch_related("photos", "flags", "studio_glazes")
    )


@dataclass(frozen=True)
class KilnHome:
    """A maker's tickets by status, and for the crew and admins the counts their links show.

    One shape for both homes: a guest's ``/kiln/`` page and everyone else's Kiln Tickets tab on
    the Ceramics Guild page (``kiln/partials/_home.html``).
    """

    drafts: list[KilnTicket]
    queued: list[KilnTicket]
    loaded: list[KilnTicket]
    history: list[KilnTicket]
    has_older: bool
    older_url: str
    runs_kiln: bool
    waiting_count: int
    unload_count: int

    @property
    def is_empty(self) -> bool:
        return not (self.drafts or self.queued or self.loaded or self.history)


def kiln_home(member: Member, *, show_older: bool, runs_kiln: bool, older_url: str) -> KilnHome:
    """``member``'s kiln home in a fixed number of queries, however many tickets they have.

    Args:
        member: The maker.
        show_older: Show every fired ticket, not just the newest :data:`HISTORY_PAGE`.
        runs_kiln: Whether the viewer sees the crew links (:func:`kiln.access.can_run_kiln`);
            their two counts are read only then.
        older_url: Where "Show older tickets" goes on this home.
    """
    by_status: dict[str, list[KilnTicket]] = {status: [] for status in KilnTicket.Status.values}
    for ticket in tickets_for(member):
        by_status[ticket.status].append(ticket)
    history = by_status[KilnTicket.Status.FIRED]
    return KilnHome(
        drafts=by_status[KilnTicket.Status.DRAFT],
        queued=by_status[KilnTicket.Status.SUBMITTED],
        loaded=by_status[KilnTicket.Status.LOADED],
        history=history if show_older else history[:HISTORY_PAGE],
        has_older=not show_older and len(history) > HISTORY_PAGE,
        older_url=older_url,
        runs_kiln=runs_kiln,
        waiting_count=KilnTicket.objects.waiting().count() if runs_kiln else 0,
        unload_count=KilnFiring.objects.in_kiln().count() if runs_kiln else 0,
    )


def guild_kiln_home(guild: Guild, member: Member | None, *, guilds_surface: bool, show_older: bool) -> KilnHome | None:
    """The Kiln Tickets tab on a guild page, or None where the tab does not show.

    It shows on the kiln guild's page alone, on the members surface, to a signed in maker:
    everyone while the launch switch is on, the crew and admins while it is off. Every other
    guild page returns before any query.
    """
    if guild.slug != settings.KILN_GUILD_SLUG or guilds_surface or member is None or not can_file_tickets(member):
        return None
    runs_kiln = can_run_kiln(member, guild)
    if not runs_kiln and not kiln_is_open():
        return None
    return kiln_home(member, show_older=show_older, runs_kiln=runs_kiln, older_url=kiln_home_url(older=True))
