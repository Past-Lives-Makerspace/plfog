"""The front desk kiosk on tours.pastlives.space (#950): check in for a booked tour, or schedule one.

cairn, the lobby screen, opens ``/kiosk/?key=<key>`` once; every page here then needs the kiosk
cookie (:mod:`tours.kiosk`). Without it, every kiosk path sends the visitor to the public booking
page, so today's visitor names never reach the open internet.

**Check in** offers three ways at once: the email the visitor booked with, their name tapped in
today's list, or their name typed. One match checks in (:func:`tours.booking.check_in_tour`) and
welcomes them; several show a pick list; none shows the try again screen. Every check in POST
spends one :func:`core.abuse_limits.record_keyed_attempt`, so a leaked key still cannot probe
names at speed.

**Schedule a tour** is the website's booking flow (:func:`tours.public_views.post_booking`) under
the same notice and horizon, recorded with ``kiosk`` as its source.

**Nothing lingers.** Every page is ``never_cache`` (``Cache-Control: no-store``), every form has
autocomplete off, and each POST redirects to a result page that shows its outcome once
(the session holds it until read), so the back button brings back no visitor's details. The
pages return home after 60 seconds without input and a few seconds after a finished check in or
booking (``static/js/tours-kiosk.js``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache

from core.abuse_limits import client_rate_key, record_keyed_attempt
from tours import kiosk
from tours.booking import ALREADY_BOOKED_MESSAGE, TourLockedError, check_in_tour
from tours.forms import TourBookingForm
from tours.models import FacilityTourSettings, TourBooking
from tours.notify import guide_first_name, when_label
from tours.picker import build_picker, find_tour
from tours.public_views import Attempt, all_open_tours, post_booking, render_page, tours_surface_only

logger = logging.getLogger(__name__)

View = Callable[..., HttpResponse]

#: Check in POSTs per kiosk address: far above a busy day at one front desk, far below probing names.
CHECK_IN_SCOPE = "tour_kiosk_check_in"
CHECK_IN_HOURLY_LIMIT = 60
CHECK_IN_DAILY_LIMIT = 300
#: Seconds without input before a kiosk page returns home, and seconds a finished screen stays up.
IDLE_SECONDS = 60
DONE_SECONDS = 8
#: Where a POST leaves its outcome for the result page to show once.
CHECK_IN_RESULT = "tour_kiosk_check_in"
BOOK_RESULT = "tour_kiosk_booked"


def kiosk_only(view: View) -> View:
    """The tours host, a device holding the kiosk cookie, and never cached; else the booking page."""

    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not kiosk.is_kiosk(request, FacilityTourSettings.load()):
            return redirect("/")
        return view(request, *args, **kwargs)

    return tours_surface_only(never_cache(wrapped))


def _render(request: HttpRequest, template: str, context: dict[str, Any] | None = None) -> HttpResponse:
    """A kiosk page, with how long it waits for input before going home."""
    return render_page(request, template, {"idle_seconds": IDLE_SECONDS, "done_seconds": 0, **(context or {})})


@tours_surface_only
@never_cache
def home(request: HttpRequest) -> HttpResponse:
    """The kiosk home: I Booked a Tour, and Schedule a Tour.

    ``?key=`` with the current key sets the kiosk cookie and comes back here without it in the
    address; a wrong key, or no cookie, is sent to the public booking page. Each visit renews the
    cookie, so a screen in use never loses it.
    """
    tour_settings = FacilityTourSettings.load()
    raw_key = request.GET.get("key")
    if raw_key is not None:
        if not kiosk.key_matches(raw_key, tour_settings):
            return redirect("/")
        to_home = redirect("tours_kiosk:home")
        kiosk.remember_kiosk(request, to_home, tour_settings)
        return to_home
    if not kiosk.is_kiosk(request, tour_settings):
        return redirect("/")
    page = render_page(request, "tours/kiosk/home.html", {"idle_seconds": 0, "done_seconds": 0})
    kiosk.remember_kiosk(request, page, tour_settings)
    return page


@kiosk_only
def check_in(request: HttpRequest) -> HttpResponse:
    """The three ways to check in (GET), and finding the tour a POST names.

    POST ``booking`` (a tapped name or a pick list button), ``email`` or ``name``. The outcome goes
    to :func:`check_in_result` through the session.
    """
    today = timezone.localdate()
    if request.method != "POST":
        return _render(request, "tours/kiosk/check_in.html", {"bookings": list(kiosk.todays_tours(today))})
    allowed, reason = record_keyed_attempt(
        CHECK_IN_SCOPE,
        client_rate_key(request),
        hourly_limit=CHECK_IN_HOURLY_LIMIT,
        daily_limit=CHECK_IN_DAILY_LIMIT,
    )
    if not allowed:
        logger.warning("Tour kiosk check in rate limited (%s)", reason)
        return _result(request, {"kind": "limited"})
    if "booking" in request.POST:
        found = kiosk.find_by_pk(request.POST["booking"], today)
    elif request.POST.get("email", "").strip():
        found = kiosk.find_by_email(request.POST["email"], today)
    else:
        found = kiosk.find_by_name(request.POST.get("name", ""), today)
    if len(found) > 1:
        return _result(request, {"kind": "pick", "bookings": [booking.pk for booking in found]})
    if not found:
        return _result(request, {"kind": "no_match"})
    try:
        booking, _first = check_in_tour(found[0])
    except TourLockedError:
        return _result(request, {"kind": "no_match"})
    return _result(request, {"kind": "welcome", "booking": booking.pk})


def _result(request: HttpRequest, outcome: dict[str, Any]) -> HttpResponse:
    request.session[CHECK_IN_RESULT] = outcome
    return redirect("tours_kiosk:check_in_result")


@kiosk_only
def check_in_result(request: HttpRequest) -> HttpResponse:
    """What a check in came to, shown once: welcome, a pick list, no match, or too many tries.

    Opened again (the back button, a reload), it has nothing to show and goes home.
    """
    outcome: dict[str, Any] | None = request.session.pop(CHECK_IN_RESULT, None)
    if outcome is None:
        return redirect("tours_kiosk:home")
    kind = outcome["kind"]
    if kind == "welcome":
        booking = TourBooking.objects.select_related("guide").filter(pk=outcome["booking"]).first()
        if booking is None:
            return redirect("tours_kiosk:home")
        return render_page(
            request,
            "tours/kiosk/welcome.html",
            {
                "booking": booking,
                "guide_name": guide_first_name(booking.guide),
                "idle_seconds": 0,
                "done_seconds": DONE_SECONDS,
            },
        )
    if kind == "pick":
        bookings = list(kiosk.todays_tours(timezone.localdate()).filter(pk__in=outcome["bookings"]))
        return _render(request, "tours/kiosk/pick.html", {"bookings": bookings})
    return _render(request, "tours/kiosk/no_match.html", {"limited": kind == "limited"})


@kiosk_only
def book(request: HttpRequest) -> HttpResponse:
    """Schedule a tour at the kiosk: the website's date strip, times and form, in kiosk chrome.

    The same notice and horizon as the website (:func:`tours.public_views.all_open_tours`); the
    optional account toggle starts on, as there. A booking lands on :func:`booked`.
    """
    tour_settings = FacilityTourSettings.load()
    if not tour_settings.accepting_bookings:
        return _render(request, "tours/kiosk/book.html", {"closed_message": tour_settings.closed_message})
    tours = all_open_tours()
    if request.method == "POST":
        attempt = post_booking(request, tours, offer_account=True, kiosk=True)
        if attempt.booking is not None:
            request.session[BOOK_RESULT] = {"booking": attempt.booking.pk}
            return redirect("tours_kiosk:booked")
        if attempt.redirect_to:
            return redirect(attempt.redirect_to)
        if attempt.already:
            request.session[BOOK_RESULT] = {"already": True}
            return redirect("tours_kiosk:booked")
        if attempt.error:
            tours = all_open_tours()
    else:
        chosen = find_tour(tours, request.GET.get("tour", ""))
        attempt = Attempt(chosen=chosen)
        if chosen is not None:
            attempt.form = TourBookingForm(
                initial={"tour": chosen.key, "party_size": "1"},
                max_party=chosen.seats_left,
                offer_account=True,
                kiosk=True,
            )
    chosen = attempt.chosen if attempt.form is not None else None
    raw_day = f"{timezone.localdate(chosen.starts_at):%Y-%m-%d}" if chosen else request.GET.get("date", "")
    return _render(
        request,
        "tours/kiosk/book.html",
        {
            "picker": build_picker(tours, raw_day),
            "chosen": chosen,
            "form": attempt.form,
            "error": attempt.error,
            "page_url": reverse("tours_kiosk:book"),
        },
    )


@kiosk_only
def booked(request: HttpRequest) -> HttpResponse:
    """The kiosk booking's confirmation, shown once and then home; never the visitor's own links."""
    outcome: dict[str, Any] | None = request.session.pop(BOOK_RESULT, None)
    if outcome is None:
        return redirect("tours_kiosk:home")
    booking = None
    if "already" not in outcome:
        booking = TourBooking.objects.select_related("guide").filter(pk=outcome["booking"]).first()
        if booking is None:
            return redirect("tours_kiosk:home")
    return render_page(
        request,
        "tours/kiosk/booked.html",
        {
            "booking": booking,
            "when": when_label(booking.starts_at) if booking else "",
            "already_message": ALREADY_BOOKED_MESSAGE,
            "idle_seconds": 0,
            "done_seconds": DONE_SECONDS,
        },
    )
