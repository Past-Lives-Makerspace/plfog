"""Booking, moving and cancelling facility tours (#948), and the staff's moves, cancels and marks (#949).

The one place a :class:`~tours.models.TourBooking` is made, moved, cancelled or marked.
Each takes the row lock :func:`tours.availability.open_tour_at` holds for the tour it lands on,
inside ``transaction.atomic()``, before counting seats, so two visitors racing for the last seat
cannot both get it: the second one's count includes the first one's booking and is refused.

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
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.utils import timezone

from core.models import UserProfile
from core.services.guest_account import ensure_guest_account, find_user_for_email
from membership.models import Member
from tours import signals
from tours.availability import open_tour_at
from tours.models import FacilityTourSettings, TourBooking

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
}


@dataclass(frozen=True)
class Visitor:
    """Who is booking, as the form took it."""

    name: str
    email: str
    phone: str
    party_size: int

    @property
    def first_and_last(self) -> tuple[str, str]:
        """The name split at its first space, for a new account's first and last name."""
        first, _, last = self.name.strip().partition(" ")
        return first, last.strip()


def _after_commit(booking: TourBooking, change: str, **extra: Any) -> None:
    """Tell the visitor and the guide, and send the part 5 seam, once the change has committed.

    ``robust``: a failure sending one notice is logged and never undoes or hides the booking.
    """
    from tours import notify

    def announce() -> None:
        notify.announce(booking, change, **extra)
        signals.tour_booking_changed.send(sender=TourBooking, booking=booking, change=change, **extra)

    transaction.on_commit(announce, robust=True)


def book_tour(
    *,
    guide: Member,
    starts_at: datetime,
    visitor: Visitor,
    user: User | None = None,
    create_account: bool = False,
    source: str = TourBooking.Source.WEBSITE,
    now: datetime | None = None,
) -> TourBooking:
    """Book ``visitor`` onto the guide's tour at ``starts_at``.

    ``source`` records how: the tours website, or the front desk kiosk (#950), which books under
    the same notice and horizon.

    The booking links to ``user`` when the visitor is signed in, else to the account the email
    already belongs to (the account itself is left as it is). With neither and
    ``create_account``, a GUEST account is made through
    :func:`core.services.guest_account.ensure_guest_account` after the booking stands; without
    it, nothing is created. Nor is anything created when the email is a member's who has no
    account: some members are kept without one on purpose (Member 529, Member 733), so the
    booking stands with no account.

    The same visitor pressing Book twice (:func:`_is_repeat`) books once: the second press raises
    :class:`TourAlreadyBookedError` and sends nothing. It never hands back the first booking, whose
    token would let anyone who knows the email see and cancel it.

    Raises:
        TourUnavailableError: no tour of that guide's is bookable at that start now.
        TourFullError: the tour has fewer seats left than ``visitor.party_size``.
        TourAlreadyBookedError: this exact booking was just made (a double press of Book).
    """
    account = user if user is not None and user.is_authenticated else find_user_for_email(visitor.email)
    with transaction.atomic():
        tour = open_tour_at(guide, starts_at, lock=True, now=now)
        if tour is None:
            raise TourUnavailableError(GONE_MESSAGE)
        if _is_repeat(guide, tour.starts_at, visitor, now or timezone.now()):
            raise TourAlreadyBookedError(ALREADY_BOOKED_MESSAGE)
        if tour.seats_left < visitor.party_size:
            raise TourFullError(tour.seats_left)
        booking = TourBooking.objects.create(
            guide=guide,
            starts_at=tour.starts_at,
            ends_at=tour.ends_at,
            party_size=visitor.party_size,
            visitor_name=visitor.name.strip(),
            visitor_email=visitor.email.strip(),
            visitor_phone=visitor.phone.strip(),
            user=account,
            source=source,
        )
        _after_commit(booking, signals.BOOKED)
    if account is None and create_account and not Member.objects.unlinked_with_email(visitor.email.strip()).exists():
        first, last = visitor.first_and_last
        made = ensure_guest_account(visitor.email, first_name=first, last_name=last)
        if made is not None:
            TourBooking.objects.filter(pk=booking.pk).update(user=made)
            booking.user = made
    return booking


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
    """Move the visitor's booking to the guide's tour at ``starts_at`` (any guide's tour, same party).

    Locks the booking, then the tour it moves to, and counts that tour's seats under the lock.
    The invite's SEQUENCE goes up so the visitor's calendar replaces its copy.

    Raises:
        TourLockedError: the booking is cancelled, past, or inside the cancel cutoff.
        TourUnavailableError: no bookable tour then, or it is the booking's own tour.
        TourFullError: the tour has fewer seats left than the party.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        locked = _locked_for_change(booking, moment)
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
    """Move a locked booking onto the guide's tour at ``starts_at``, inside the caller's transaction."""
    tour = open_tour_at(guide, starts_at, lock=True, now=now, staff=staff)
    if tour is None:
        raise TourUnavailableError(GONE_MESSAGE)
    if locked.guide_id == guide.pk and locked.starts_at == tour.starts_at:
        raise TourUnavailableError(SAME_TIME_MESSAGE)
    if tour.seats_left < locked.party_size:
        raise TourFullError(tour.seats_left)
    previous_guide, previous_starts_at = locked.guide, locked.starts_at
    locked.guide = guide
    locked.starts_at = tour.starts_at
    locked.ends_at = tour.ends_at
    locked.sequence += 1
    locked.save(update_fields=["guide", "starts_at", "ends_at", "sequence"])
    _after_commit(locked, signals.MOVED, previous_guide=previous_guide, previous_starts_at=previous_starts_at, **extra)
    return locked


def cancel_tour_by_visitor(booking: TourBooking, *, now: datetime | None = None) -> TourBooking:
    """Cancel the booking from the visitor's email link, freeing its seats.

    Raises:
        TourLockedError: the booking is cancelled, past, or inside the cancel cutoff.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        locked = _locked_for_change(booking, moment)
        locked.status = TourBooking.Status.CANCELLED
        locked.cancelled_at = moment
        locked.cancelled_by = None
        locked.sequence += 1
        locked.save(update_fields=["status", "cancelled_at", "cancelled_by", "sequence"])
        _after_commit(locked, signals.CANCELLED)
    return locked


def _locked_for_staff(booking: TourBooking, now: datetime) -> TourBooking:
    """The booking's row, locked, after checking it is still booked and has not started (#949).

    No cancel cutoff: staff may change a tour right up to its start.
    """
    locked = TourBooking.objects.select_for_update().get(pk=booking.pk)
    reason = locked.staff_refusal(now)
    if reason:
        raise TourLockedError(reason)
    return locked


def reschedule_tour_by_staff(
    booking: TourBooking,
    *,
    guide: Member,
    starts_at: datetime,
    actor: User,
    message: str = "",
    now: datetime | None = None,
) -> TourBooking:
    """A Tour Guide or an admin moves the booking to the guide's tour at ``starts_at`` (#949).

    The visitor's locks and seat count (:func:`reschedule_tour`), with neither the cancel cutoff
    nor the minimum notice: any tour that has not started yet. Another guide's tour is a
    reassignment, which only an admin may make (the view checks). The visitor gets the moved
    email with ``message``; each guide who is not ``actor`` hears about it.

    Raises:
        TourLockedError: the booking is cancelled or has started.
        TourUnavailableError: no tour then, or it is the booking's own tour.
        TourFullError: the tour has fewer seats left than the party.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        locked = _locked_for_staff(booking, moment)
        return _move(
            locked,
            guide=guide,
            starts_at=starts_at,
            now=moment,
            staff=True,
            actor=actor,
            staff_message=message.strip(),
        )


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
