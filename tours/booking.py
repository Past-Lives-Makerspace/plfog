"""Booking, moving and cancelling facility tours (#948), and the staff's moves, cancels and marks (#949).

The one place a :class:`~tours.models.TourBooking` is made, moved, cancelled or marked.
Each takes :func:`tours.availability.lock_bookings` (through ``lock=True``) before reading the tour
it lands on, inside ``transaction.atomic()``, so two visitors racing for the last seat cannot both
get it, and two booking the same time nobody has booked yet end on one guide (#1312): the second
one reads the first one's booking, which claimed that time, and joins the same tour.

What happens next (the visitor's email with its calendar invite, the guide's email, bell and
push, and :data:`tours.signals.tour_booking_changed` for part 5's Google sync) runs only once
the transaction has committed (:func:`_after_commit`).

Staff (a Tour Guide or an admin, #949) move and cancel through the same locks with three
differences: neither the cancel cutoff nor the minimum notice binds them (a tour that has not
started yet is theirs to change), the visitor's email may carry the staff member's message, and
the guide hears about it only when someone else acted. Who may act on which booking, and move
it to whose tour, is the view's check (``tours.views``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import UserProfile
from core.tasks import enqueue_at_on_commit
from core.services.guest_account import find_user_for_email
from membership.models import AdminCapability, Member
from membership.permission_setup import SetupState
from tours import signals
from tours.availability import open_tour_at, tour_at
from tours.models import FacilityTourSettings, TourBooking, TourBookingNote, TourGuideSettings

if TYPE_CHECKING:
    from django.contrib.auth.models import User

FILLED_MESSAGE = "That time just filled up, pick another."
GONE_MESSAGE = "That time is no longer open, pick another."
SAME_TIME_MESSAGE = "That is already your tour time. Pick a different one."
#: A second identical booking (same email, guide and start) inside this long is the same visitor
#: pressing Book twice, answered with the first booking rather than a second one.
REPEAT_WINDOW = timedelta(minutes=10)
ALREADY_BOOKED_MESSAGE = (
    "You're already booked for this time. Check your email for the confirmation with links to reschedule or cancel."
)


def _same_name(first: str, second: str) -> bool:
    """Whether two typed names are the same, ignoring case and spacing."""
    return " ".join(first.split()).casefold() == " ".join(second.split()).casefold()


def seats_message(seats_left: int) -> str:
    """What a visitor reads when their party does not fit: filled up, or how many seats remain."""
    if seats_left <= 0:
        return FILLED_MESSAGE
    if seats_left == 1:
        return "Only 1 seat is left at that time."
    return f"Only {seats_left} seats are left at that time."


class TourBookingError(Exception):
    """A booking, move or cancel the visitor asked for that cannot happen; ``str()`` is what they read."""


class TourFullError(TourBookingError):
    """The tour has fewer seats left than the party needs."""

    def __init__(self, seats_left: int) -> None:
        super().__init__(seats_message(seats_left))
        self.seats_left = seats_left


class TourUnavailableError(TourBookingError):
    """No bookable tour starts then: never offered, a day off, past the horizon or inside the notice."""


class TourAlreadyBookedError(TourBookingError):
    """The same visitor already booked this tour moments ago; they are sent to their email, never the booking."""


class TourLockedError(TourBookingError):
    """The booking can no longer change online; ``reason`` is ``TourBooking.self_serve_refusal``'s."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


#: What staff read when a booking changed under them (#949), by ``TourLockedError.reason``.
STAFF_LOCKED_MESSAGES: dict[str, str] = {
    "cancelled": "This tour was already cancelled.",
    "past": "This tour has already started, so it can no longer be moved or cancelled.",
    "upcoming": "This tour has not started yet. Mark it after it starts.",
    "answered": "This request was already answered.",
    "lapsed": "The confirm by time has passed, so this request was cancelled and the visitor told.",
    "request": "Confirm or decline this request first.",
    "taken": "Another guide already took this request.",
}


#: The booking questions a :class:`Visitor` carries onto its booking (feedback request #32).
ANSWER_FIELDS = ("pronouns", "found_us_through", "referred_by", "guest_notes")


@dataclass(frozen=True)
class Visitor:
    """Who is booking, as the form took it."""

    name: str
    email: str
    phone: str
    party_size: int
    # The optional booking questions (feedback request #32), blank when skipped.
    pronouns: str = ""
    found_us_through: str = ""
    referred_by: str = ""
    guest_notes: str = ""
    wants_studio: bool = False
    studio_description: str = ""
    wants_newsletter: bool = False

    @property
    def answers(self) -> dict[str, Any]:
        """The booking questions' answers, trimmed, and the two boxes, as the booking's fields."""
        answers: dict[str, Any] = {name: getattr(self, name).strip() for name in ANSWER_FIELDS}
        boxes = {"wants_studio": self.wants_studio, "wants_newsletter": self.wants_newsletter}
        return {**answers, **boxes, "studio_description": self.studio_description.strip()}

    @property
    def first_and_last(self) -> tuple[str, str]:
        """The name split at its first space, for a new account's first and last name."""
        first, _, last = self.name.strip().partition(" ")
        return first, last.strip()


def _join_newsletter(booking: TourBooking) -> None:
    """Subscribe the visitor in Mailchimp once the booking commits, when they ticked the box (#35)."""
    if booking.wants_newsletter:
        from core.tasks import enqueue_on_commit
        from tours.tasks import subscribe_tour_visitor

        enqueue_on_commit(subscribe_tour_visitor, booking.pk)


def _after_commit(booking: TourBooking, change: str, **extra: Any) -> None:
    """Tell the visitor and the guide, and send the seam the Google Calendar sync hangs off (#951),
    once the change has committed.

    Two callbacks, each ``robust``: a failure sending one notice is logged, never undoes or hides
    the booking, and never keeps the guide's calendar from hearing of it.
    """
    from tours import notify

    def announce() -> None:
        notify.announce(booking, change, **extra)

    def seam() -> None:
        signals.tour_booking_changed.send(sender=TourBooking, booking=booking, change=change, **extra)

    transaction.on_commit(announce, robust=True)
    transaction.on_commit(seam, robust=True)


def book_tour(
    *,
    guide: Member,
    starts_at: datetime,
    visitor: Visitor,
    user: User | None = None,
    source: str = TourBooking.Source.WEBSITE,
    now: datetime | None = None,
) -> TourBooking:
    """Book ``visitor`` onto the tour at ``starts_at``.

    Visitors pick a time, never a guide (#1312): ``guide`` is whose tour the page showed, and the
    booking lands on whoever holds that time under the lock (:func:`tours.availability.tour_at`).
    That is the same guide unless another booking claimed the time first, in which case the
    visitor joins that tour. ``source`` records how: the tours website, or the front desk kiosk (#950), which books under
    the same notice and horizon. On the website, a guide who confirms first (#1166) gets a request
    instead: it holds its seats like a booking until they confirm or decline it. The kiosk always
    books at once, since the visitor is standing at the front desk.

    The booking links to ``user`` when the visitor is signed in, else to the account the email
    already belongs to (the account itself is left as it is). With neither it stands with no
    account, and none is made (Morlock, 2026-11-22: the form's "Create a free Past Lives account"
    switch is gone): whoever later signs in with that email, verified, claims it
    (:func:`claim_tours`).

    The same visitor pressing Book twice (:func:`_is_repeat`) books once: the second press raises
    :class:`TourAlreadyBookedError` and sends nothing. It never hands back the first booking, whose
    token would let anyone who knows the email see and cancel it.

    Raises:
        TourUnavailableError: no tour of that guide's is bookable at that start now.
        TourFullError: the tour has fewer seats left than ``visitor.party_size``.
        TourAlreadyBookedError: this exact booking was just made (a double press of Book).
    """
    account = _account_for(user, visitor)
    with transaction.atomic():
        kiosk = source == TourBooking.Source.KIOSK
        tour = tour_at(starts_at, lock=True, now=now, guide_notice=not kiosk)
        if tour is None:
            raise TourUnavailableError(GONE_MESSAGE)
        if _is_repeat(tour.guide, tour.starts_at, visitor, now or timezone.now()):
            raise TourAlreadyBookedError(ALREADY_BOOKED_MESSAGE)
        if tour.seats_left < visitor.party_size:
            raise TourFullError(tour.seats_left)
        request = not kiosk and TourGuideSettings.confirms_first(tour.guide)
        booking = TourBooking.objects.create(
            guide=tour.guide,
            starts_at=tour.starts_at,
            ends_at=tour.ends_at,
            party_size=visitor.party_size,
            visitor_name=visitor.name.strip(),
            visitor_email=visitor.email.strip(),
            visitor_phone=visitor.phone.strip(),
            **visitor.answers,
            user=account,
            source=source,
            status=TourBooking.Status.PENDING if request else TourBooking.Status.BOOKED,
        )
        _after_commit(booking, signals.REQUESTED if request else signals.BOOKED)
        _join_newsletter(booking)
        if request:
            from tours.tasks import lapse_tour_request

            enqueue_at_on_commit(lapse_tour_request, booking.confirm_by, booking.pk)
    return booking


def _account_for(user: User | None, visitor: Visitor) -> User | None:
    """The signed in visitor, else the account the email already belongs to."""
    return user if user is not None and user.is_authenticated else find_user_for_email(visitor.email)


def claim_tours(user: User) -> int:
    """Link the tours booked under any of ``user``'s verified emails, and no account yet, to ``user``.

    Morlock, 2026-11-22: in place of asking a visitor to make an account at booking, a tour finds
    its visitor whenever they later sign in or sign up, however long after (tours.apps connects it
    to sign in, signup and an email's confirmation). Only a verified email counts, since signing in
    by emailed code proves the inbox is theirs; a typed one would let anyone claim another's tours.
    A tour already linked to an account is never moved. Returns how many it linked.
    """
    from allauth.account.models import EmailAddress

    emails = [
        email.strip().lower()
        for email in EmailAddress.objects.filter(user=user, verified=True).values_list("email", flat=True)
    ]
    if not emails:
        return 0
    matches = Q()
    for email in emails:
        matches |= Q(visitor_email__iexact=email)
    return TourBooking.objects.filter(matches, user__isnull=True).update(user=user)


def _is_repeat(guide: Member, starts_at: datetime, visitor: Visitor, now: datetime) -> bool:
    """Whether ``visitor`` booked this very tour in the last :data:`REPEAT_WINDOW`: a double press of Book.

    Every typed field must match (email ignoring case, name ignoring case and spacing, party size),
    so someone who only knows another visitor's email makes a booking of their own instead.
    """
    candidates = TourBooking.objects.filter(
        status=TourBooking.Status.BOOKED,
        guide=guide,
        starts_at=starts_at,
        visitor_email__iexact=visitor.email.strip(),
        party_size=visitor.party_size,
        created_at__gte=now - REPEAT_WINDOW,
    ).values_list("visitor_name", flat=True)
    return any(_same_name(name, visitor.name) for name in candidates)


def check_in_tour(booking: TourBooking, *, now: datetime | None = None) -> tuple[TourBooking, bool]:
    """The visitor checked in at the front desk kiosk (#950): stamp ``checked_in_at`` and ping the guide.

    Only a tour still booked for today (local) can be checked in. A second check in changes
    nothing and pings nobody. Never marks attended: the guide does that once the tour starts,
    and only that stamps ``completed_tour_at``.

    Returns:
        The booking and whether this was its first check in.

    Raises:
        TourLockedError: the booking is cancelled, already marked, or not today's.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        locked = TourBooking.objects.select_for_update().get(pk=booking.pk)
        if locked.status != TourBooking.Status.BOOKED:
            raise TourLockedError("cancelled" if locked.status == TourBooking.Status.CANCELLED else "past")
        if timezone.localdate(locked.starts_at) != timezone.localdate(moment):
            raise TourLockedError("not_today")
        if locked.checked_in_at is not None:
            return locked, False
        locked.checked_in_at = moment
        locked.save(update_fields=["checked_in_at"])

        def ping() -> None:
            from tours import notify

            notify.tell_guide_checked_in(locked)

        transaction.on_commit(ping, robust=True)
    return locked, True


def _locked_for_change(booking: TourBooking, now: datetime) -> TourBooking:
    """The booking's row, locked, after checking the visitor may still change it online."""
    locked = TourBooking.objects.select_for_update().get(pk=booking.pk)
    reason = locked.self_serve_refusal(now=now, cancel_cutoff_hours=FacilityTourSettings.load().cancel_cutoff_hours)
    if reason:
        raise TourLockedError(reason)
    return locked


def reschedule_tour(
    booking: TourBooking, *, guide: Member, starts_at: datetime, now: datetime | None = None
) -> TourBooking:
    """Move the visitor's booking to the tour at ``starts_at`` (whoever gives it, #1312; same party).

    Locks the booking, then every booking (:func:`tours.availability.lock_bookings`), and counts the
    seats of the tour it moves to under the lock.
    The invite's SEQUENCE goes up so the visitor's calendar replaces its copy.

    A request (#1166) cannot move until its guide confirms it: the guide said yes to that time.

    Raises:
        TourLockedError: the booking is cancelled, past, inside the cancel cutoff, or a request.
        TourUnavailableError: no bookable tour then, or it is the booking's own tour.
        TourFullError: the tour has fewer seats left than the party.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        locked = _locked_for_change(booking, moment)
        if locked.is_request:
            raise TourLockedError("request")
        return _move(locked, guide=guide, starts_at=starts_at, now=moment, staff=False)


def _move(
    locked: TourBooking,
    *,
    guide: Member,
    starts_at: datetime,
    now: datetime,
    staff: bool,
    **extra: Any,
) -> TourBooking:
    """Move a locked booking onto the tour at ``starts_at``, inside the caller's transaction.

    The visitor's own move lands on whoever holds that time, as a booking does (#1312); staff (an
    accepted proposal) move it to the guide they picked, refused when another guide's booked tour
    overlaps it.
    """
    if staff:
        tour = open_tour_at(guide, starts_at, lock=True, now=now, staff=True, moving=locked)
    else:
        tour = tour_at(starts_at, lock=True, now=now, moving=locked)
    if tour is None:
        raise TourUnavailableError(GONE_MESSAGE)
    # A visitor moving to their own start would only hand it to another guide; staff may (a hand off).
    if locked.starts_at == tour.starts_at and (not staff or locked.guide_id == tour.guide.pk):
        raise TourUnavailableError(SAME_TIME_MESSAGE)
    if tour.seats_left < locked.party_size:
        raise TourFullError(tour.seats_left)
    previous_guide, previous_starts_at = locked.guide, locked.starts_at
    locked.guide = tour.guide
    locked.starts_at = tour.starts_at
    locked.ends_at = tour.ends_at
    locked.sequence += 1
    changed = ["guide", "starts_at", "ends_at", "sequence"]
    if locked.has_open_proposal:
        # A move answers any open proposal (#1283): its links then say it is no longer open.
        locked.drop_proposal()
        changed += list(TourBooking.PROPOSAL_FIELDS)
    if locked.has_open_trade:
        # A trade is of one time (#1308): a move to another one withdraws it.
        locked.drop_trade()
        changed += list(TourBooking.TRADE_FIELDS)
    locked.save(update_fields=changed)
    _after_commit(locked, signals.MOVED, previous_guide=previous_guide, previous_starts_at=previous_starts_at, **extra)
    return locked


def cancel_tour_by_visitor(booking: TourBooking, *, now: datetime | None = None) -> TourBooking:
    """Cancel the booking from the visitor's email link, freeing its seats.

    Raises:
        TourLockedError: the booking is cancelled, past, or inside the cancel cutoff.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        group = _lock_group(booking)
        # A guide's answer may have moved the visitor's link to another row of a No preference
        # request (#1255) while this waited for the group's lock: act on the row holding it now.
        current = TourBooking.objects.filter(self_serve_token=booking.self_serve_token).first() or booking
        locked = _locked_for_change(current, moment)
        # A request (#1166) never sent a calendar invite, so its cancel email carries no retraction.
        was_request = locked.is_request
        if was_request:
            # A No preference request (#1255) is withdrawn from every guide still deciding.
            for other in _other_pending(group, locked):
                other.status = TourBooking.Status.CANCELLED
                other.cancelled_at = moment
                other.cancelled_by = None
                other.save(update_fields=["status", "cancelled_at", "cancelled_by"])
                _after_commit(other, signals.CANCELLED, was_request=True, tell_visitor=False)
        locked.status = TourBooking.Status.CANCELLED
        locked.cancelled_at = moment
        locked.cancelled_by = None
        locked.sequence += 1
        locked.save(update_fields=["status", "cancelled_at", "cancelled_by", "sequence"])
        gather_notes(group, locked)
        _after_commit(locked, signals.CANCELLED, was_request=was_request)
    return locked


def _locked_for_staff(booking: TourBooking, now: datetime) -> TourBooking:
    """The booking's row, locked, after checking it is still booked and has not started (#949).

    No cancel cutoff: staff may change a tour right up to its start. A request (#1166) is confirmed
    or declined, never moved or cancelled.
    """
    locked = TourBooking.objects.select_for_update().get(pk=booking.pk)
    reason = "request" if locked.is_request and locked.starts_at > now else locked.staff_refusal(now)
    if reason:
        raise TourLockedError(reason)
    return locked


def gather_notes(group: list[TourBooking], onto: TourBooking) -> None:
    """Move every note on a No preference request's rows onto ``onto`` (#1274), the row that survives.

    While shared, the rows show one Notes thread (:meth:`TourBooking.note_thread`); once the request
    resolves, the other rows leave the group's view, so their notes join the confirmed row or the
    row holding the visitor's link. Inside the caller's lock on the group.
    """
    if group:
        pks = [row.pk for row in group]
        TourBookingNote.objects.filter(booking__in=pks).exclude(booking=onto).update(booking=onto)
        # Every other row now opens, and posts to, the surviving row's Notes; a note posted on one
        # waits on this lock (``tours.views.tour_booking_note``) and then lands there too.
        # Only rows already out of the request point on: a row still pending reads the whole group's
        # thread itself, and pointing it would make a note posted there wait on another row's lock.
        TourBooking.objects.filter(pk__in=pks).exclude(pk=onto.pk).exclude(status=TourBooking.Status.PENDING).update(
            notes_moved_to=onto
        )
        TourBooking.objects.filter(pk=onto.pk).update(notes_moved_to=None)
        onto.notes_moved_to = None


def lock_for_note(pk: int) -> TourBooking:
    """Lock a booking for a new note (#1274) the way every request answer does, and return it.

    A No preference request's rows first, in pk order (:func:`_lock_group`), then the row itself:
    the order Confirm, Decline, a lapse and the visitor's Cancel take, so a note posted while one of
    them runs waits its turn and can never deadlock it. Its :attr:`TourBooking.note_target` is then
    read fresh. Call inside a transaction.

    Raises:
        TourBooking.DoesNotExist: no such booking.
    """
    _lock_group(TourBooking.objects.get(pk=pk))
    return TourBooking.objects.select_for_update().get(pk=pk)


def _lock_group(booking: TourBooking) -> list[TourBooking]:
    """Every row of the booking's No preference request (#1255), locked in pk order; [] for any other booking.

    Taken before the booking's own row, so a Confirm, a Decline, a lapse and the visitor's Cancel on
    one request always lock in the same order and never deadlock.
    """
    if booking.request_group is None:
        return []
    return list(TourBooking.objects.select_for_update().filter(request_group=booking.request_group).order_by("pk"))


def _other_pending(group: list[TourBooking], booking: TourBooking) -> list[TourBooking]:
    """The rows of a locked group still waiting for their guide, other than ``booking``."""
    return [row for row in group if row.is_request and row.pk != booking.pk]


def _locked_request(booking: TourBooking, now: datetime) -> TourBooking:
    """The request's row, locked, after checking it still waits for an answer and has not started (#1166)."""
    locked = TourBooking.objects.select_for_update().get(pk=booking.pk)
    if locked.was_taken_by_another_guide():
        raise TourLockedError("taken")
    if locked.status == TourBooking.Status.CANCELLED:
        raise TourLockedError("cancelled")
    if locked.starts_at <= now:
        raise TourLockedError("past")
    if not locked.is_request:
        raise TourLockedError("answered")
    if now >= locked.confirm_by:
        raise TourLockedError("lapsed")
    return locked


def lapse_request(booking_id: int, *, now: datetime | None = None) -> TourBooking | None:
    """Cancel a request its guide did not answer by its confirm by time (#1166), freeing its seats.

    Run by :func:`tours.tasks.lapse_tour_request`, queued for the confirm by time when the request
    was made. Safe to run twice and a no-op for a request already answered, cancelled, or (run
    early) not yet due. The visitor is told it could not be confirmed, the guide that it lapsed.

    Returns:
        The cancelled booking, or None when there was nothing to do.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        found = TourBooking.objects.filter(pk=booking_id).first()
        group = _lock_group(found) if found is not None else []
        locked = TourBooking.objects.select_for_update().filter(pk=booking_id).first()
        if locked is None or not locked.is_request or moment < locked.confirm_by:
            return None
        # A No preference request (#1255) lapses for every guide at once; the visitor hears once,
        # on the row that carries their link (the first still pending).
        rows = [row for row in group if row.is_request] or [locked]
        for row in rows:
            row.status = TourBooking.Status.CANCELLED
            row.cancelled_at = moment
            row.cancelled_by = None
            row.save(update_fields=["status", "cancelled_at", "cancelled_by"])
            _after_commit(row, signals.LAPSED, tell_visitor=row is rows[0])
        gather_notes(group, rows[0])
    return rows[0]


def confirm_tour_request(booking: TourBooking, *, actor: User, now: datetime | None = None) -> TourBooking:
    """The guide (or an admin) confirms a visitor's request (#1166): it becomes a booked tour.

    Its seats were held all along, so nothing is counted again. After commit the visitor gets the
    booked email with the calendar invite, and the guide's Google Calendar gets the event.

    Raises:
        TourLockedError: the request was cancelled, already answered, or its tour has started.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        group = _lock_group(booking)
        locked = _locked_request(booking, moment)
        others = _other_pending(group, locked)
        if others:
            _take_shared_request(locked, others, actor=actor, now=moment)
        gather_notes(group, locked)
        locked.status = TourBooking.Status.BOOKED
        locked.request_group = None
        locked.save(update_fields=["status", "request_group"])
        _after_commit(locked, signals.CONFIRMED, actor=actor)
    return locked


def _take_shared_request(locked: TourBooking, others: list[TourBooking], *, actor: User, now: datetime) -> None:
    """``locked``'s guide confirmed a No preference request first (#1255).

    The row takes the visitor's link when another row holds it (the first still pending), and every
    other guide's row is cancelled with the confirming guide as ``cancelled_by``, freeing its seats;
    each of those guides is told another guide took it. Inside the caller's lock on the group.
    """
    holder = min([locked, *others], key=lambda row: row.pk)
    if holder.pk != locked.pk:
        locked.take_visitor_link_from(holder)
    for other in others:
        other.status = TourBooking.Status.CANCELLED
        other.cancelled_at = now
        other.cancelled_by = actor
        other.save(update_fields=["status", "cancelled_at", "cancelled_by"])
        _after_commit(other, signals.TAKEN, tell_visitor=False, taken_by=locked.guide)


def decline_tour_request(
    booking: TourBooking, *, actor: User, message: str = "", now: datetime | None = None
) -> TourBooking:
    """The guide (or an admin) declines a visitor's request (#1166), freeing its seats.

    The visitor is emailed with ``message``; no calendar update, since no invite was ever sent.

    Raises:
        TourLockedError: the request was cancelled, already answered, or its tour has started.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        group = _lock_group(booking)
        locked = _locked_request(booking, moment)
        others = _other_pending(group, locked)
        # Another guide may still take a No preference request (#1255): the visitor hears nothing
        # yet, and the link they hold moves to the next row still pending.
        if others and locked.pk < others[0].pk:
            others[0].take_visitor_link_from(locked)
        locked.status = TourBooking.Status.CANCELLED
        locked.cancelled_at = moment
        locked.cancelled_by = actor
        locked.save(update_fields=["status", "cancelled_at", "cancelled_by"])
        # The notes go with the visitor's link: to the row now holding it, or stay here on the last
        # decline. After the save, so this row, no longer pending, points on.
        gather_notes(group, others[0] if others else locked)
        _after_commit(locked, signals.DECLINED, actor=actor, staff_message=message.strip(), tell_visitor=not others)
    return locked


def propose_tour_time(
    booking: TourBooking,
    *,
    guide: Member,
    starts_at: datetime,
    actor: User,
    message: str = "",
    now: datetime | None = None,
) -> TourBooking:
    """A Tour Guide or an admin asks the visitor to move to the guide's tour at ``starts_at`` (#1283).

    The booking keeps its time: the proposal is recorded and the visitor emailed Accept New Time
    and Keep My Time, with ``message``. A new proposal replaces an open one, whose links then go
    stale. Checked as a staff move is (#949: any open tour that has not started, no cancel cutoff
    or minimum notice, room for the party now), but no seats are held: they are counted again
    when the visitor accepts (:func:`answer_tour_proposal`). Another guide's tour is a
    reassignment, which the booking's own guide or an admin may propose (#1274; the view checks).
    One tour runs at a time (#1312): a tour another guide's booked tour overlaps is refused, the
    booking's own seats never counting against it.

    Raises:
        TourLockedError: the booking is cancelled, has started, or is a request.
        TourUnavailableError: no tour then, or it is the booking's own tour.
        TourFullError: the tour has fewer seats left than the party.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        locked = _locked_for_staff(booking, moment)
        tour = open_tour_at(guide, starts_at, now=moment, staff=True, moving=locked)
        if tour is None:
            raise TourUnavailableError(GONE_MESSAGE)
        if locked.guide_id == guide.pk and locked.starts_at == tour.starts_at:
            raise TourUnavailableError(SAME_TIME_MESSAGE)
        if tour.seats_left < locked.party_size:
            raise TourFullError(tour.seats_left)
        locked.propose_time(guide, tour.starts_at, proposed_by=actor, now=moment)
        written = message.strip()
        transaction.on_commit(lambda: _send_proposal(locked, actor, written), robust=True)
    return locked


def _send_proposal(booking: TourBooking, actor: User, message: str) -> None:
    from tours import notify

    notify.send_proposal_email(booking, actor=actor, staff_message=message)


#: What :func:`answer_tour_proposal` did.
ACCEPTED = "accepted"
KEPT = "kept"
#: The visitor accepted, but the proposed tour filled up or closed since: they keep their time.
UNAVAILABLE = "unavailable"


def answer_tour_proposal(booking: TourBooking, *, accept: bool, proposal: str, now: datetime | None = None) -> str:
    """The visitor accepts or keeps their time against the proposal named by ``proposal`` (#1283).

    Decides on the row-locked booking (:meth:`TourBooking.proposal_refusal`), so a second answer,
    a cancel or a newer proposal that landed first wins and this one changes nothing. Accept moves
    the booking through the same lock and seat count as any move, under the staff rules the guide
    picked it by; when that tour filled up or closed since, the proposal is dropped and the
    booking keeps its time. Keep drops the proposal. Every answer tells the guide, the proposed
    guide and the proposer once it has committed; a move also sends the visitor the moved email.

    Returns:
        :data:`ACCEPTED`, :data:`KEPT`, :data:`UNAVAILABLE`, or a refusal (``"already"``,
        ``"stale"``, ``"passed"``) when nothing changed.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        locked = TourBooking.objects.select_for_update().get(pk=booking.pk)
        refusal = locked.proposal_refusal(proposal, moment)
        if refusal:
            return refusal
        proposed_guide = cast("Member", locked.proposed_guide)
        proposed_starts_at = cast("datetime", locked.proposed_starts_at)
        proposer = locked.proposed_by
        previous_guide, previous_starts_at = locked.guide, locked.starts_at
        answer = KEPT
        if accept:
            try:
                with transaction.atomic():
                    _move(
                        locked,
                        guide=proposed_guide,
                        starts_at=proposed_starts_at,
                        now=moment,
                        staff=True,
                        answered_proposal=True,
                    )
                answer = ACCEPTED
            except (TourUnavailableError, TourFullError):
                answer = UNAVAILABLE
        if answer != ACCEPTED:
            locked.clear_proposal()

        def tell() -> None:
            from tours import notify

            notify.tell_guides_of_answer(
                locked,
                answer,
                proposal=proposal,
                previous_guide=previous_guide,
                previous_starts_at=previous_starts_at,
                proposed_guide=proposed_guide,
                proposed_starts_at=proposed_starts_at,
                proposer=proposer,
            )

        transaction.on_commit(tell, robust=True)
    return answer


# --- Trades between guides (#1308) -----------------------------------------------------------------

#: What a guide reads when a trade can no longer be answered or withdrawn, by :meth:`TourBooking.trade_refusal`.
TRADE_REFUSALS: dict[str, str] = {
    "cancelled": "This tour was cancelled.",
    "past": "This tour has already started.",
    "withdrawn": "This trade was withdrawn.",
}
SAME_GUIDE_MESSAGE = "This tour is already theirs. Pick another guide."
TRADE_OPEN_MESSAGE = "This tour already has a trade waiting. Try again once it is answered."


class TradeRefusedError(TourBookingError):
    """A trade that cannot be made, answered or withdrawn; ``str()`` is what the guide reads."""


class NotYourTradeError(Exception):
    """The member answering or withdrawing a trade is not the one it waits on (the view's 403)."""


def _locked_for_trade(booking: TourBooking, now: datetime) -> TourBooking:
    """The booking's row, locked, after checking it is a booked tour that has not started (#1308)."""
    locked = TourBooking.objects.select_for_update().get(pk=booking.pk)
    reason = locked.staff_refusal(now)
    if reason:
        raise TradeRefusedError(TRADE_REFUSALS[reason])
    return locked


def offer_trade(
    booking: TourBooking, *, to: Member, actor: User, note: str = "", now: datetime | None = None
) -> TourBooking:
    """The tour's guide (or an admin) offers the tour to ``to`` (#1308); it moves only when they accept.

    The time is fixed, so ``to``'s hours, days off and Google busy times do not gate it, and no seats
    change. A new offer replaces an open trade or take, whose asker or answerer is told it was withdrawn.
    Who may offer is the view's check.

    Raises:
        TradeRefusedError: the tour was cancelled or has started, or ``to`` is already its guide.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        locked = _locked_for_trade(booking, moment)
        if locked.guide_id == to.pk:
            raise TradeRefusedError(SAME_GUIDE_MESSAGE)
        # The other side of an open trade, an offer's guide or a take's taker, hears it went.
        replaced = locked.trade_to if locked.has_open_trade else None
        locked.open_trade(to, TourBooking.TradeKind.TRADE, by=actor, note=note.strip(), now=moment)
        name = _first_name(actor)
        _tell_after_commit(locked, "offered", to, name, note=locked.trade_note)
        if replaced is not None and replaced.pk != to.pk:
            _tell_after_commit(locked, "withdrawn", replaced, name)
    return locked


def offer_take(
    booking: TourBooking, *, taker: Member, actor: User, note: str = "", now: datetime | None = None
) -> TourBooking:
    """Another guide asks to take the tour (#1308); its guide approves or declines.

    Raises:
        TradeRefusedError: the tour was cancelled or has started, has no guide, is already the
            taker's, or already has a trade waiting.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        locked = _locked_for_trade(booking, moment)
        guide = locked.guide
        if guide is None or guide.pk == taker.pk:
            raise TradeRefusedError(SAME_GUIDE_MESSAGE)
        if locked.has_open_trade:
            raise TradeRefusedError(TRADE_OPEN_MESSAGE)
        locked.open_trade(taker, TourBooking.TradeKind.TAKE, by=actor, note=note.strip(), now=moment)
        _tell_after_commit(locked, "take_asked", guide, _first_name(actor), note=locked.trade_note)
    return locked


def _waiting_on(locked: TourBooking) -> Member | None:
    """Who the open trade waits on: the guide offered it, or for a take the tour's guide."""
    return locked.guide if locked.is_take else locked.trade_to


def _asker(locked: TourBooking) -> Member | None:
    """Who made the open trade: the tour's guide (or the admin's member) for an offer, the taker for a take."""
    if locked.is_take:
        return locked.trade_to
    by_member: Member | None = getattr(locked.trade_by, "member", None)
    return locked.guide or by_member


def guide_name_of(member: Member) -> str:
    from tours.notify import guide_first_name

    return guide_first_name(member)


def _first_name(user: User) -> str:
    from tours.notify import first_name

    return first_name(user)


def _tell_after_commit(
    booking: TourBooking, change: str, to: Member | None, actor_name: str, *, note: str = ""
) -> None:
    """Tell ``to`` about the trade once it has committed (:func:`tours.notify.tell_trade`); nobody when empty."""
    if to is None:
        return
    from tours import notify

    notice = notify.TradeNotice(booking=booking, change=change, actor_name=actor_name, note=note, at=timezone.now())
    transaction.on_commit(lambda: notify.tell_trade(notice, to), robust=True)


def answer_trade(
    booking: TourBooking, *, accept: bool, key: str, answerer: Member, now: datetime | None = None
) -> TourBooking:
    """The guide a trade waits on accepts (or approves a take) or declines it (#1308).

    Decided on the row-locked booking, so of two answers racing on one trade only the first acts.
    Accept makes ``trade_to`` the tour's guide and sends the ``moved`` seam with ``previous_guide``
    (the Google sync moves the event), tells the asker, and emails the visitor their new guide. The
    time, seats and the visitor's calendar invite do not change. Decline tells the asker only.

    Raises:
        TradeRefusedError: the trade was withdrawn or replaced, or the tour was cancelled or started.
        NotYourTradeError: ``answerer`` is not who the trade waits on (an admin never answers for them).
    """
    moment = now or timezone.now()
    with transaction.atomic():
        locked = (
            TourBooking.objects.select_for_update(of=("self",))
            .select_related("guide", "trade_to", "trade_by")
            .get(pk=booking.pk)
        )
        refusal = locked.trade_refusal(key, moment)
        if refusal:
            raise TradeRefusedError(TRADE_REFUSALS[refusal])
        if locked.trade_answerer_id != answerer.pk:
            raise NotYourTradeError
        asker = _asker(locked)
        name = guide_name_of(answerer)
        taker = cast("Member", locked.trade_to)
        kind = locked.trade_kind
        previous_guide = locked.guide
        changed = ["guide", *TourBooking.TRADE_FIELDS]
        if accept:
            locked.guide = taker
            if locked.has_open_proposal:
                # A new time asked of the visitor (#1283) was on the old guide's tours: it goes with them.
                locked.drop_proposal()
                changed += list(TourBooking.PROPOSAL_FIELDS)
        locked.drop_trade()
        locked.save(update_fields=changed)
        if accept:
            change = "approved" if kind == TourBooking.TradeKind.TAKE else "accepted"
            _tell_after_commit(locked, change, asker, name)
            transaction.on_commit(lambda: _after_trade(locked, previous_guide, key), robust=True)
        else:
            declined = "take_declined" if kind == TourBooking.TradeKind.TAKE else "declined"
            _tell_after_commit(locked, declined, asker, name)
    return locked


def _after_trade(booking: TourBooking, previous_guide: Member | None, key: str) -> None:
    """After an accepted trade: the visitor's new guide email, and the seam the Google sync hangs off."""
    from tours import notify

    notify.send_new_guide_email(booking, trade_key=key)
    signals.tour_booking_changed.send(
        sender=TourBooking,
        booking=booking,
        change=signals.MOVED,
        previous_guide=previous_guide,
        previous_starts_at=booking.starts_at,
        traded=True,
    )


def withdraw_trade(booking: TourBooking, *, key: str, asker: Member, now: datetime | None = None) -> TourBooking:
    """Whoever made a trade takes it back before it is answered (#1308); the guide it waited on is told.

    Raises:
        TradeRefusedError: the trade was already answered, withdrawn or replaced, or the tour was cancelled or started.
        NotYourTradeError: ``asker`` did not make it (the tour's guide for an offer, the taker for a take).
    """
    moment = now or timezone.now()
    with transaction.atomic():
        locked = (
            TourBooking.objects.select_for_update(of=("self",)).select_related("guide", "trade_to").get(pk=booking.pk)
        )
        refusal = locked.trade_refusal(key, moment)
        if refusal:
            raise TradeRefusedError(TRADE_REFUSALS[refusal])
        if locked.trade_asker_id != asker.pk:
            raise NotYourTradeError
        waiting_on = _waiting_on(locked)
        locked.drop_trade()
        locked.save(update_fields=list(TourBooking.TRADE_FIELDS))
        _tell_after_commit(locked, "withdrawn", waiting_on, guide_name_of(asker))
    return locked


def cancel_tour_by_staff(
    booking: TourBooking, *, actor: User, message: str = "", now: datetime | None = None
) -> TourBooking:
    """A Tour Guide or an admin cancels the booking, freeing its seats (#949).

    No cancel cutoff. The visitor gets the cancelled email with ``message``; the guide hears
    about it when someone else cancelled.

    Raises:
        TourLockedError: the booking is cancelled or has started.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        locked = _locked_for_staff(booking, moment)
        locked.status = TourBooking.Status.CANCELLED
        locked.cancelled_at = moment
        locked.cancelled_by = actor
        locked.sequence += 1
        locked.save(update_fields=["status", "cancelled_at", "cancelled_by", "sequence"])
        _after_commit(locked, signals.CANCELLED, actor=actor, staff_message=message.strip())
    return locked


#: What a tour that has started may be marked (#949).
OUTCOMES: tuple[str, ...] = (TourBooking.Status.ATTENDED, TourBooking.Status.NO_SHOW)


def mark_tour_outcome(booking: TourBooking, outcome: str, *, now: datetime | None = None) -> TourBooking:
    """Mark a tour that has started attended or no show (#949); either can be changed later.

    Attended stamps the visitor's ``UserProfile.completed_tour_at`` (the tour's start) when the
    booking is linked to an account whose stamp is still empty; an earlier stamp is never
    overwritten. No show never stamps; correcting attended to no show clears the stamp only when
    it is this tour's own (equal to its start).
    Nobody is emailed.

    Raises:
        ValueError: ``outcome`` is not attended or no show.
        TourLockedError: the booking is cancelled, or the tour has not started yet.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"Unknown tour outcome '{outcome}'")
    moment = now or timezone.now()
    with transaction.atomic():
        locked = TourBooking.objects.select_for_update().get(pk=booking.pk)
        if locked.status == TourBooking.Status.CANCELLED:
            raise TourLockedError("cancelled")
        if locked.starts_at > moment:
            raise TourLockedError("upcoming")
        was_attended = locked.status == TourBooking.Status.ATTENDED
        locked.status = outcome
        locked.save(update_fields=["status"])
        if locked.user is not None:
            if outcome == TourBooking.Status.ATTENDED:
                _stamp_completed_tour(locked.user, locked.starts_at)
            elif was_attended:
                _unstamp_completed_tour(locked.user, locked.starts_at)
        if outcome == TourBooking.Status.ATTENDED and locked.guide is not None:
            # A first Attended tour can finish the guide's Get Set Up (#1167): the badge lands now,
            # whoever marked it; the guide's toast waits for their next visit or this response.
            SetupState.evaluate(locked.guide, AdminCapability.Capability.TOUR_GUIDE)
    return locked


def _stamp_completed_tour(user: User, toured_at: datetime) -> None:
    """Stamp the visitor's ``completed_tour_at`` with the tour's start, only when it is still empty.

    One conditional UPDATE, so two marks racing can never overwrite a stamp the other just wrote.
    """
    UserProfile.objects.get_or_create(user=user)
    UserProfile.objects.filter(user=user, completed_tour_at__isnull=True).update(completed_tour_at=toured_at)


def _unstamp_completed_tour(user: User, toured_at: datetime) -> None:
    """Undo this tour's stamp when attended is corrected away: only a stamp equal to this tour's start.

    A stamp from an earlier tour (or from before this app) is a different instant and stays. When it is cleared,
    the visitor's earliest tour still marked attended stamps it again, so a correction never erases
    a tour they did take.
    """
    cleared = UserProfile.objects.filter(user=user, completed_tour_at=toured_at).update(completed_tour_at=None)
    if cleared:
        still_attended = (
            TourBooking.objects.filter(user=user, status=TourBooking.Status.ATTENDED).order_by("starts_at").first()
        )
        if still_attended is not None:
            _stamp_completed_tour(user, still_attended.starts_at)
