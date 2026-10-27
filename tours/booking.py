"""Booking, moving and cancelling facility tours (#948).

The one place a :class:`~tours.models.TourBooking` is made, moved or cancelled by a visitor.
Each takes the row lock :func:`tours.availability.open_tour_at` holds for the tour it lands on,
inside ``transaction.atomic()``, before counting seats, so two visitors racing for the last seat
cannot both get it: the second one's count includes the first one's booking and is refused.

What happens next (the visitor's email with its calendar invite, the guide's email, bell and
push, and :data:`tours.signals.tour_booking_changed` for part 5's Google sync) runs only once
the transaction has committed (:func:`_after_commit`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.utils import timezone

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


class TourLockedError(TourBookingError):
    """The booking can no longer change online; ``reason`` is ``TourBooking.self_serve_refusal``'s."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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
    now: datetime | None = None,
) -> TourBooking:
    """Book ``visitor`` onto the guide's tour at ``starts_at``.

    The booking links to ``user`` when the visitor is signed in, else to the account the email
    already belongs to (the account itself is left as it is). With neither and
    ``create_account``, a GUEST account is made through
    :func:`core.services.guest_account.ensure_guest_account` after the booking stands; without
    it, nothing is created. Nor is anything created when the email is a member's who has no
    account: some members are kept without one on purpose (Member 529, Member 733), so the
    booking stands with no account.

    The same visitor pressing Book twice (a booked tour with the same email, guide and start made
    in the last :data:`REPEAT_WINDOW`) gets the first booking back, with nothing sent again.

    Raises:
        TourUnavailableError: no tour of that guide's is bookable at that start now.
        TourFullError: the tour has fewer seats left than ``visitor.party_size``.
    """
    account = user if user is not None and user.is_authenticated else find_user_for_email(visitor.email)
    with transaction.atomic():
        tour = open_tour_at(guide, starts_at, lock=True, now=now)
        if tour is None:
            raise TourUnavailableError(GONE_MESSAGE)
        repeat = (
            TourBooking.objects.filter(
                status=TourBooking.Status.BOOKED,
                guide=guide,
                starts_at=tour.starts_at,
                visitor_email__iexact=visitor.email.strip(),
                created_at__gte=timezone.now() - REPEAT_WINDOW,
            )
            .order_by("created_at")
            .first()
        )
        if repeat is not None:
            return repeat
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
        )
        _after_commit(booking, signals.BOOKED)
    if account is None and create_account and not Member.objects.unlinked_with_email(visitor.email.strip()).exists():
        first, last = visitor.first_and_last
        made = ensure_guest_account(visitor.email, first_name=first, last_name=last)
        if made is not None:
            TourBooking.objects.filter(pk=booking.pk).update(user=made)
            booking.user = made
    return booking


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
        tour = open_tour_at(guide, starts_at, lock=True, now=moment)
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
        _after_commit(locked, signals.MOVED, previous_guide=previous_guide, previous_starts_at=previous_starts_at)
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
