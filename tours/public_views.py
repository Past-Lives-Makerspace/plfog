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
from datetime import date, datetime, timedelta
from functools import wraps
from typing import Any

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from membership.models import Member
from tours.availability import OpenTour, open_tours, parse_tour_key
from tours.booking import (
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

View = Callable[..., HttpResponse]


def tours_surface_only(view: View) -> View:
    """404 a tour view anywhere but the tours host."""

    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if getattr(request, "surface", None) != "tours":
            raise Http404("Not available on this surface.")
        return view(request, *args, **kwargs)

    return wrapped


@dataclass(frozen=True)
class DayChoice:
    """One day in the date strip."""

    day: date
    selected: bool


@dataclass(frozen=True)
class TimeChoice:
    """One tour among the picked day's times. ``guide_name`` is set when two guides share the time."""

    tour: OpenTour
    guide_name: str


@dataclass(frozen=True)
class Picker:
    """The date strip and the picked day's times."""

    days: list[DayChoice]
    day: date | None
    times: list[TimeChoice]


def _all_open_tours() -> list[OpenTour]:
    """Every tour visitors can book now (``open_tours`` clamps the range to the horizon and notice)."""
    today = timezone.localdate()
    return open_tours(today, today + timedelta(days=MAX_HORIZON_DAYS))


def _picker(tours: list[OpenTour], raw_day: str) -> Picker:
    """The days that have a tour, ``raw_day`` (``YYYY-MM-DD``) picked when it is one of them, else the first."""
    by_day: dict[date, list[OpenTour]] = {}
    for tour in tours:
        by_day.setdefault(timezone.localdate(tour.starts_at), []).append(tour)
    try:
        wanted: date | None = date.fromisoformat(raw_day)
    except ValueError:
        wanted = None
    picked = wanted if wanted in by_day else next(iter(by_day), None)
    day_tours = by_day.get(picked, []) if picked is not None else []
    starts = [tour.starts_at for tour in day_tours]
    times = [
        TimeChoice(tour=tour, guide_name=tour.guide.display_name.split()[0] if starts.count(tour.starts_at) > 1 else "")
        for tour in day_tours
    ]
    return Picker(days=[DayChoice(day=day, selected=day == picked) for day in by_day], day=picked, times=times)


def _find(tours: list[OpenTour], key: str) -> OpenTour | None:
    """The tour ``key`` names among ``tours``, or None."""
    return next((tour for tour in tours if tour.key == key), None)


def _guide_for(key: str) -> tuple[Member, datetime] | None:
    """The guide and UTC start a posted tour key names, or None for a key that names no member."""
    parsed = parse_tour_key(key)
    if parsed is None:
        return None
    guide = Member.objects.filter(pk=parsed[0]).first()
    return (guide, parsed[1]) if guide is not None else None


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


def _limit_party(form: TourBookingForm, tour: OpenTour) -> None:
    """Offer 1 to the tour's seats left again on a re-shown form (a POST offered up to a full tour)."""
    form.fields["party_size"].choices = [  # type: ignore[attr-defined]
        (str(size), str(size)) for size in range(1, max(tour.seats_left, 1) + 1)
    ]


def _page(request: HttpRequest, template: str, context: dict[str, Any], *, status: int = 200) -> HttpResponse:
    """Render a tours page with what every one of them shows."""
    return render(
        request,
        template,
        {"address": TOUR_ADDRESS, "support_email": support_email(), **context},
        status=status,
    )


@tours_surface_only
def book(request: HttpRequest) -> HttpResponse:
    """The booking page (the host's root): the date strip, the day's times, and the form for the picked one.

    GET ``?date=YYYY-MM-DD`` picks a day and ``?tour=<key>`` a time, which opens the form. A POST
    books through :func:`tours.booking.book_tour`; a tour that filled up meanwhile shows the times
    again with "That time just filled up, pick another." While Tour Settings is not accepting
    bookings the page shows the closed message instead.
    """
    tour_settings = FacilityTourSettings.load()
    if not tour_settings.accepting_bookings:
        return _page(request, "tours/public/closed.html", {"closed_message": tour_settings.closed_message})
    tours = _all_open_tours()
    offer_account = _offers_account(request)
    error = ""
    form: TourBookingForm | None = None
    if request.method == "POST":
        chosen = _find(tours, request.POST.get("tour", ""))
        posted = TourBookingForm(request.POST, max_party=MAX_SEATS, offer_account=offer_account)
        if posted.is_valid():
            data = posted.cleaned_data
            target = _guide_for(data["tour"])
            try:
                if target is None:
                    raise TourBookingError(GONE_MESSAGE)
                booking = book_tour(
                    guide=target[0],
                    starts_at=target[1],
                    visitor=Visitor(data["name"], data["email"], data["phone"], data["party_size"]),
                    user=request.user if request.user.is_authenticated else None,
                    create_account=bool(data.get("create_account")),
                )
            except TourBookingError as exc:
                error = str(exc)
                tours = _all_open_tours()
                chosen = None
            else:
                return redirect(f"{reverse('tours_public:manage', args=[booking.self_serve_token])}?booked=1")
        elif chosen is None:
            error = GONE_MESSAGE
        else:
            _limit_party(posted, chosen)
            form = posted
    else:
        chosen = _find(tours, request.GET.get("tour", ""))
        if chosen is not None:
            form = TourBookingForm(
                initial={"tour": chosen.key, "party_size": "1", **_prefill(request)},
                max_party=chosen.seats_left,
                offer_account=offer_account,
            )
    raw_day = f"{timezone.localdate(chosen.starts_at):%Y-%m-%d}" if chosen else request.GET.get("date", "")
    return _page(
        request,
        "tours/public/book.html",
        {
            "picker": _picker(tours, raw_day),
            "chosen": chosen if form is not None else None,
            "form": form,
            "error": error,
            "page_url": request.path,
        },
    )


def _booking(token: str) -> TourBooking | None:
    """The booking a self serve token names, or None for a tampered or unknown one."""
    return TourBooking.objects.select_related("guide").filter(self_serve_token=token).first()


def _invalid(request: HttpRequest) -> HttpResponse:
    return _page(request, "tours/public/refused.html", {"reason": "invalid"}, status=404)


def _refusal(booking: TourBooking) -> str:
    """Why the visitor may not change this booking online right now, or ""."""
    return booking.self_serve_refusal(
        now=timezone.now(), cancel_cutoff_hours=FacilityTourSettings.load().cancel_cutoff_hours
    )


def _refused(request: HttpRequest, booking: TourBooking, reason: str) -> HttpResponse:
    return _page(request, "tours/public/refused.html", {"reason": reason, **_booking_context(booking)})


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
    return _page(
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
    """Pick another time for the booking (the same date strip), then confirm it with a POST."""
    booking = _booking(token)
    if booking is None:
        return _invalid(request)
    reason = _refusal(booking)
    if reason:
        return _refused(request, booking, reason)
    error = ""
    if request.method == "POST":
        target = _guide_for(request.POST.get("tour", ""))
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
        for tour in _all_open_tours()
        if tour.seats_left >= booking.party_size
        and not (tour.guide.pk == booking.guide_id and tour.starts_at == booking.starts_at)
    ]
    chosen = None if error else _find(tours, request.GET.get("tour", ""))
    raw_day = f"{timezone.localdate(chosen.starts_at):%Y-%m-%d}" if chosen else request.GET.get("date", "")
    return _page(
        request,
        "tours/public/reschedule.html",
        {
            **_booking_context(booking),
            "picker": _picker(tours, raw_day),
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
    return _page(request, "tours/public/cancel.html", _booking_context(booking))
