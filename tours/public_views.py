"""The visitor's pages on tours.pastlives.space (#948): book a tour, and move or cancel it from the email.

Every view here answers only on the tours surface (``request.surface == "tours"``, set by
``core.middleware.SurfaceMiddleware`` for ``TOURS_HOSTS``) and 404s on every other host. The host's
root is :func:`book`. No login: the email links carry the booking's ``self_serve_token``, a random
unguessable string stored on the row, so a link works as long as the booking exists, and a
tampered or unknown token finds no row and lands on a friendly page. Reschedule and Cancel show
a page on GET and act only on POST, so an email client prefetching the link changes nothing; both
refuse a cancelled or past tour and anything inside the cancel cutoff, on GET and POST alike.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import wraps
from typing import Any

import logging

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from core.abuse_limits import client_rate_key, record_keyed_attempt
from membership.models import Member
from tours.availability import OpenTour, open_tours
from tours.calendar_sync import refresh_stale_busy
from tours.booking import (
    ALREADY_BOOKED_MESSAGE,
    TourAlreadyBookedError,
    GONE_MESSAGE,
    TourBookingError,
    TourLockedError,
    Visitor,
    book_tour,
    cancel_tour_by_visitor,
    reschedule_tour,
)
from tours.forms import TourBookingForm
from tours.models import MAX_HORIZON_DAYS, MAX_SEATS, TOUR_ADDRESS, FacilityTourSettings, TourBooking
from tours.notify import guide_first_name, support_email, when_label
from tours.picker import build_picker, find_tour, guide_and_start

logger = logging.getLogger(__name__)

View = Callable[..., HttpResponse]

#: The booking POST's rate limit, keyed by ``core.abuse_limits.client_rate_key``: a cost ceiling per
#: address on an unauthenticated write that sends email, the guest RSVP's caps (a front desk's
#: shared wifi still fits a few families booking at once).
BOOKING_SCOPE = "tour_booking"
BOOKING_HOURLY_LIMIT = 10
BOOKING_DAILY_LIMIT = 30
RATE_LIMITED_MESSAGE = "Too many bookings from this connection. Try again in an hour."
#: The front desk kiosk's own booking limit (#950): cairn shares the makerspace's public address
#: with every phone on the Wi-Fi, so a kiosk (a request holding the kiosk cookie) never spends the
#: website's allowance, nor the website the kiosk's. The kiosk check in's caps.
KIOSK_BOOKING_SCOPE = "tour_kiosk_booking"
KIOSK_BOOKING_HOURLY_LIMIT = 60
KIOSK_BOOKING_DAILY_LIMIT = 300


def tours_surface_only(view: View) -> View:
    """404 a tour view anywhere but the tours host."""

    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if getattr(request, "surface", None) != "tours":
            raise Http404("Not available on this surface.")
        return view(request, *args, **kwargs)

    return wrapped


def all_open_tours() -> list[OpenTour]:
    """Every tour visitors can book now (``open_tours`` clamps the range to the horizon and notice).

    The booking pages and the kiosk come through here, so it first reads again any guide's Google
    Calendar busy times older than 15 minutes (#951): bounded to a few seconds, and on any failure
    the tours are listed from the busy times already cached.
    """
    try:
        refresh_stale_busy()
    except Exception:
        # Never an error page for a visitor: the cached busy times (or none) still apply.
        logger.exception("Reading tour guides' Google Calendar busy times failed")
    today = timezone.localdate()
    return open_tours(today, today + timedelta(days=MAX_HORIZON_DAYS))


def _offers_account(request: HttpRequest) -> bool:
    """The account toggle shows to a signed out visitor; a signed in one already has an account."""
    return not request.user.is_authenticated


def _prefill(request: HttpRequest) -> dict[str, str]:
    """A signed in visitor's name and email."""
    user = request.user
    if not user.is_authenticated:
        return {}
    member: Member | None = getattr(user, "member", None)
    name = user.get_full_name().strip() or (member.display_name if member is not None else "")
    return {"name": name, "email": user.email}


def limit_party(form: TourBookingForm, tour: OpenTour) -> None:
    """Offer 1 to the tour's seats left again on a re-shown form (a POST offered up to a full tour)."""
    form.fields["party_size"].choices = [  # type: ignore[attr-defined]
        (str(size), str(size)) for size in range(1, max(tour.seats_left, 1) + 1)
    ]


def render_page(request: HttpRequest, template: str, context: dict[str, Any], *, status: int = 200) -> HttpResponse:
    """Render a tours page with what every one of them shows."""
    return render(
        request,
        template,
        {"address": TOUR_ADDRESS, "support_email": support_email(), **context},
        status=status,
    )


@dataclass
class Attempt:
    """What a booking POST came to: the booking made, a page to send the visitor to, or what to show again."""

    booking: TourBooking | None = None
    redirect_to: str = ""
    already: bool = False
    error: str = ""
    form: TourBookingForm | None = None
    chosen: OpenTour | None = None


def _try_booking(request: HttpRequest, form: TourBookingForm, *, kiosk: bool) -> Attempt:
    """Book a valid form's tour: the booking on success, else the reason to show.

    At the kiosk (#950) the booking records ``kiosk`` as its source and never links to whoever
    might be signed in on the lobby screen.
    """
    data = form.cleaned_data
    target = guide_and_start(data["tour"])
    signed_in = request.user if request.user.is_authenticated and not kiosk else None
    try:
        if target is None:
            raise TourBookingError(GONE_MESSAGE)
        booking = book_tour(
            guide=target[0],
            starts_at=target[1],
            visitor=Visitor(data["name"], data["email"], data["phone"], data["party_size"]),
            user=signed_in,
            create_account=bool(data.get("create_account")),
            source=TourBooking.Source.KIOSK if kiosk else TourBooking.Source.WEBSITE,
        )
    except TourAlreadyBookedError:
        return Attempt(already=True)
    except TourBookingError as exc:
        return Attempt(error=str(exc))
    return Attempt(booking=booking)


def post_booking(request: HttpRequest, tours: list[OpenTour], offer_account: bool, *, kiosk: bool = False) -> Attempt:
    """A booking POST, cheapest refusal first: the rate limit (one attempt per POST), the honeypot
    (a filled one is sent back to the page and books nothing), the form, then the booking.

    The kiosk (#950) books through here too, with ``kiosk`` set (only its views pass it, each behind
    the kiosk cookie), spending its own limit."""
    allowed, reason = record_keyed_attempt(
        KIOSK_BOOKING_SCOPE if kiosk else BOOKING_SCOPE,
        client_rate_key(request),
        hourly_limit=KIOSK_BOOKING_HOURLY_LIMIT if kiosk else BOOKING_HOURLY_LIMIT,
        daily_limit=KIOSK_BOOKING_DAILY_LIMIT if kiosk else BOOKING_DAILY_LIMIT,
    )
    if not allowed:
        logger.warning("Tour booking rate limited (%s)", reason)
        return Attempt(error=RATE_LIMITED_MESSAGE)
    posted = TourBookingForm(request.POST, max_party=MAX_SEATS, offer_account=offer_account, kiosk=kiosk)
    if posted.honeypot_tripped:
        return Attempt(redirect_to=request.path)
    if posted.is_valid():
        return _try_booking(request, posted, kiosk=kiosk)
    chosen = find_tour(tours, request.POST.get("tour", ""))
    if chosen is None:
        return Attempt(error=GONE_MESSAGE)
    limit_party(posted, chosen)
    return Attempt(form=posted, chosen=chosen)


@tours_surface_only
def book(request: HttpRequest) -> HttpResponse:
    """The booking page (the host's root): the date strip, the day's times, and the form for the picked one.

    GET ``?date=YYYY-MM-DD`` picks a day and ``?tour=<key>`` a time, which opens the form. A POST
    books through :func:`tours.booking.book_tour` (:func:`post_booking`); a tour that filled up
    meanwhile shows the times again with why. While Tour Settings is not accepting bookings the
    page shows the closed message instead.
    """
    tour_settings = FacilityTourSettings.load()
    if not tour_settings.accepting_bookings:
        return render_page(request, "tours/public/closed.html", {"closed_message": tour_settings.closed_message})
    tours = all_open_tours()
    offer_account = _offers_account(request)
    if request.method == "POST":
        attempt = post_booking(request, tours, offer_account)
        if attempt.booking is not None:
            return redirect(f"{reverse('tours_public:manage', args=[attempt.booking.self_serve_token])}?booked=1")
        if attempt.redirect_to:
            return redirect(attempt.redirect_to)
        if attempt.already:
            # Token free on purpose: the booking is the visitor's to open from their own email.
            return render_page(request, "tours/public/already.html", {"message": ALREADY_BOOKED_MESSAGE})
        if attempt.error:
            tours = all_open_tours()
    else:
        chosen = find_tour(tours, request.GET.get("tour", ""))
        attempt = Attempt(chosen=chosen)
        if chosen is not None:
            attempt.form = TourBookingForm(
                initial={"tour": chosen.key, "party_size": "1", **_prefill(request)},
                max_party=chosen.seats_left,
                offer_account=offer_account,
            )
    chosen = attempt.chosen if attempt.form is not None else None
    raw_day = f"{timezone.localdate(chosen.starts_at):%Y-%m-%d}" if chosen else request.GET.get("date", "")
    return render_page(
        request,
        "tours/public/book.html",
        {
            "picker": build_picker(tours, raw_day),
            "chosen": chosen,
            "form": attempt.form,
            "error": attempt.error,
            "page_url": request.path,
        },
    )


def _booking(token: str) -> TourBooking | None:
    """The booking a self serve token names, or None for a tampered or unknown one."""
    return TourBooking.objects.select_related("guide").filter(self_serve_token=token).first()


def _invalid(request: HttpRequest) -> HttpResponse:
    return render_page(request, "tours/public/refused.html", {"reason": "invalid"}, status=404)


def _refusal(booking: TourBooking) -> str:
    """Why the visitor may not change this booking online right now, or ""."""
    return booking.self_serve_refusal(
        now=timezone.now(), cancel_cutoff_hours=FacilityTourSettings.load().cancel_cutoff_hours
    )


def _refused(request: HttpRequest, booking: TourBooking, reason: str) -> HttpResponse:
    return render_page(request, "tours/public/refused.html", {"reason": reason, **_booking_context(booking)})


def _booking_context(booking: TourBooking) -> dict[str, Any]:
    return {"booking": booking, "when": when_label(booking.starts_at), "guide_name": guide_first_name(booking.guide)}


@tours_surface_only
def manage(request: HttpRequest, token: str) -> HttpResponse:
    """The visitor's booking: when and where, and the Reschedule and Cancel buttons while they apply.

    The confirmation after booking (``?booked=1``) and the landing after a move (``?moved=1``) or
    a cancel (``?cancelled=1``).
    """
    booking = _booking(token)
    if booking is None:
        return _invalid(request)
    return render_page(
        request,
        "tours/public/manage.html",
        {
            **_booking_context(booking),
            "refusal": _refusal(booking),
            "just": next((flag for flag in ("booked", "moved", "cancelled") if flag in request.GET), ""),
        },
    )


@tours_surface_only
def reschedule(request: HttpRequest, token: str) -> HttpResponse:
    """Pick another time for the booking (the same date strip), then confirm it with a POST.

    While Tour Settings is not accepting bookings no time is offered and a POST moves nothing:
    the page shows the closed message, and the booking can still be cancelled.
    """
    booking = _booking(token)
    if booking is None:
        return _invalid(request)
    reason = _refusal(booking)
    if reason:
        return _refused(request, booking, reason)
    tour_settings = FacilityTourSettings.load()
    if not tour_settings.accepting_bookings:
        return render_page(
            request,
            "tours/public/closed.html",
            {"closed_message": tour_settings.closed_message, **_booking_context(booking)},
        )
    error = ""
    if request.method == "POST":
        target = guide_and_start(request.POST.get("tour", ""))
        try:
            if target is None:
                raise TourBookingError(GONE_MESSAGE)
            reschedule_tour(booking, guide=target[0], starts_at=target[1])
        except TourLockedError as exc:
            return _refused(request, booking, exc.reason)
        except TourBookingError as exc:
            error = str(exc)
        else:
            return redirect(f"{reverse('tours_public:manage', args=[token])}?moved=1")
    tours = [
        tour
        for tour in all_open_tours()
        if tour.seats_left >= booking.party_size
        and not (tour.guide.pk == booking.guide_id and tour.starts_at == booking.starts_at)
    ]
    chosen = None if error else find_tour(tours, request.GET.get("tour", ""))
    raw_day = f"{timezone.localdate(chosen.starts_at):%Y-%m-%d}" if chosen else request.GET.get("date", "")
    return render_page(
        request,
        "tours/public/reschedule.html",
        {
            **_booking_context(booking),
            "picker": build_picker(tours, raw_day),
            "chosen": chosen,
            "chosen_when": when_label(chosen.starts_at) if chosen else "",
            "error": error,
            "page_url": request.path,
        },
    )


@tours_surface_only
def cancel(request: HttpRequest, token: str) -> HttpResponse:
    """Confirm (GET) and cancel (POST) the booking; a GET changes nothing."""
    booking = _booking(token)
    if booking is None:
        return _invalid(request)
    reason = _refusal(booking)
    if reason:
        return _refused(request, booking, reason)
    if request.method == "POST":
        try:
            cancel_tour_by_visitor(booking)
        except TourLockedError as exc:
            return _refused(request, booking, exc.reason)
        return redirect(f"{reverse('tours_public:manage', args=[token])}?cancelled=1")
    return render_page(request, "tours/public/cancel.html", _booking_context(booking))
