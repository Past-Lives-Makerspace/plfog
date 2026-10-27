"""The Tours page: the Upcoming and Past tabs with their staff actions (#949), the My Hours tab, and
the Settings tab for admins (#947).

The page is opened from the Tours card on the Tools page (``hub_admin_tools``), never the sidebar.
Everything here sits behind :func:`hub.view_as.tour_guide_access_required` (Tour Guides and
admins) or :func:`hub.view_as.fog_admin_required` (the Settings tab). Which guide's hours a request
may touch is decided once, in :func:`_target_guide`: a guide their own, an admin anyone who gives
tours, so a guide who names another guide in a POST gets a 403 before any form is read.

The Upcoming and Past tabs list :class:`~tours.models.TourBooking` rows themselves, never the
carved tours, since a booking is matched to its tour by guide and start alone (#948's known limit).
Which booking a request may act on is decided once, in :func:`_staff_booking` (a guide their own,
an admin any), and whose tour it may move to in :func:`_destination_guide` (a guide their own, an
admin any guide's: a reassignment), so a forged POST gets the same 403 the page would.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.htmx import wants_fragment
from hub.toast import trigger_toast
from hub.view_as import ROLE_ADMIN, fog_admin_required, tour_guide_access_required
from membership.models import Member
from tours import calendar_sync, google
from tours.availability import OpenTour, open_tours
from tours.booking import (
    GONE_MESSAGE,
    OUTCOMES,
    STAFF_LOCKED_MESSAGES,
    TourBookingError,
    TourLockedError,
    cancel_tour_by_staff,
    mark_tour_outcome,
    reschedule_tour_by_staff,
)
from tours.forms import (
    FacilityTourSettingsForm,
    StaffRescheduleForm,
    StaffTourMessageForm,
    TourCalendarChoiceForm,
    TourDayOffForm,
    TourHoursFormSet,
)
from tours.models import (
    MAX_HORIZON_DAYS,
    FacilityTourSettings,
    TourBooking,
    TourCalendarConnection,
    TourDayOff,
    guide_filter,
)
from tours.notify import short_when, when_label
from tours.picker import build_picker, find_tour, guide_and_start

if TYPE_CHECKING:
    from django.contrib.auth.models import User

HOURS_PREFIX = "hours"
DAY_OFF_PREFIX = "day_off"
#: Members who already have tour hours, whatever their permission says now: an admin can still open them.
_HAS_HOURS = Q(tour_hours__isnull=False)
#: Bookings per page on the Upcoming and Past tabs.
BOOKINGS_PER_PAGE = 25
UPCOMING = "upcoming"
PAST = "past"
#: The session key holding the Connect Google Calendar flow's ``state`` (#951), checked on the callback.
GOOGLE_STATE_SESSION_KEY = "tours_google_oauth_state"
#: How the admin's guide picker names each guide's Google Calendar (#951).
CALENDAR_STATE_LABELS: dict[str, str] = {
    TourCalendarConnection.Status.CONNECTED: "Calendar connected",
    TourCalendarConnection.Status.BROKEN: "Calendar needs reconnecting",
}
NO_CALENDAR_LABEL = "No calendar"


def _is_admin(request: HttpRequest) -> bool:
    """Whether the request is an admin by the actual role, which may edit anyone's tour hours."""
    view_as = getattr(request, "view_as", None)
    return view_as is not None and view_as.has_actual(ROLE_ADMIN)


def _viewer(request: HttpRequest) -> Member:
    """The signed in member; an account with none has no tour hours of its own (404)."""
    member: Member | None = getattr(request.user, "member", None)
    if member is None:
        raise Http404("This account has no member record.")
    return member


def _target_guide(request: HttpRequest, raw: str) -> Member:
    """The guide whose hours this request reads or writes.

    Blank or the viewer's own pk is the viewer. Anyone else is an admin's choice only (403 for
    a guide), and must give tours or already have tour hours (404 otherwise).
    """
    viewer = _viewer(request)
    if not raw or raw == str(viewer.pk):
        return viewer
    if not raw.isdigit():
        raise Http404("Unknown guide.")
    if not _is_admin(request):
        raise PermissionDenied("Only an admin can edit another guide's tour hours.")
    candidates = Member.objects.filter(guide_filter() | _HAS_HOURS).distinct()
    return get_object_or_404(candidates, pk=int(raw))


def _picker_guides(viewer: Member) -> list[Member]:
    """The admin's guide picker: everyone who gives tours (Tour Guides and admins, ``guide_filter``)
    and anyone with tour hours, the viewer left out (they are "My hours"). An admin always counts
    as a guide, so one with no hours yet can be picked and given some."""
    people = Member.objects.filter(guide_filter() | _HAS_HOURS).exclude(pk=viewer.pk).distinct()
    return sorted(people, key=lambda member: member.display_name.casefold())


def _picker_options(viewer: Member) -> list[dict[str, Any]]:
    """The picker's guides, each with their Google Calendar's state when the feature is set up (#951)."""
    guides = _picker_guides(viewer)
    states: dict[int, str] = {}
    if google.is_configured():
        states = dict(TourCalendarConnection.objects.filter(guide__in=guides).values_list("guide_id", "status"))
    return [
        {
            "pk": guide.pk,
            "name": guide.display_name,
            "calendar": CALENDAR_STATE_LABELS.get(states.get(guide.pk, ""), NO_CALENDAR_LABEL)
            if google.is_configured()
            else "",
        }
        for guide in guides
    ]


def _hours_url(viewer: Member, target: Member) -> str:
    """The My Hours tab for ``target``: the bare URL for the viewer's own, ``?guide=`` for anyone else's."""
    url = reverse("tours:hours")
    return url if target.pk == viewer.pk else f"{url}?guide={target.pk}"


def _hours_formset(target: Member, data: Any = None) -> Any:
    """The guide's hours formset, new rows starting from Tour Settings' defaults."""
    return TourHoursFormSet(
        data,
        instance=target,
        prefix=HOURS_PREFIX,
        form_kwargs={"defaults": FacilityTourSettings.load()},
    )


def _day_off_form(target: Member, data: Any = None) -> TourDayOffForm:
    return TourDayOffForm(data, prefix=DAY_OFF_PREFIX, guide=target)


def _render_hours(
    request: HttpRequest,
    target: Member,
    *,
    formset: Any = None,
    day_off_form: TourDayOffForm | None = None,
    open_day_off: bool = False,
    hours_refused: bool = False,
) -> HttpResponse:
    viewer = _viewer(request)
    is_admin = _is_admin(request)
    is_self = target.pk == viewer.pk
    connection = TourCalendarConnection.objects.filter(guide=target).first()
    return render(
        request,
        "tours/hours.html",
        {
            "target": target,
            "is_self": is_self,
            "is_admin": is_admin,
            "picker_guides": _picker_options(viewer) if is_admin else [],
            "gcal_configured": google.is_configured(),
            "gcal": connection,
            "gcal_form": TourCalendarChoiceForm(connection=connection)
            if is_self and connection is not None and not connection.is_broken
            else None,
            "formset": formset if formset is not None else _hours_formset(target),
            "day_off_form": day_off_form if day_off_form is not None else _day_off_form(target),
            "open_day_off": open_day_off,
            "hours_refused": hours_refused,
            "days_off": list(TourDayOff.objects.upcoming().filter(guide=target)),
        },
    )


@tour_guide_access_required
def tour_hours(request: HttpRequest) -> HttpResponse:
    """The Tours page's My Hours tab: the weekly hours editor and the days off (``?guide=`` for an admin)."""
    return _render_hours(request, _target_guide(request, request.GET.get("guide", "")))


@tour_guide_access_required
@require_POST
def tour_hours_save(request: HttpRequest) -> HttpResponse:
    """Save one guide's whole set of hours rows (adds, edits and deletes in one POST).

    The posted ``guide`` is checked before the formset is read. Valid: a flash and a redirect
    back to the tab. Invalid: the tab again with each row's errors, its rows open.
    """
    target = _target_guide(request, request.POST.get("guide", ""))
    formset = _hours_formset(target, request.POST)
    if not formset.is_valid():
        return _render_hours(request, target, formset=formset, hours_refused=True)
    formset.save()
    messages.success(request, "Tour hours saved.")
    return redirect(_hours_url(_viewer(request), target))


@tour_guide_access_required
@require_POST
def tour_day_off_save(request: HttpRequest) -> HttpResponse:
    """Save a day off, or a run of days off, for one guide, from the Add Day Off modal.

    Valid: a flash, then ``HX-Redirect`` back to the tab over HTMX (a plain redirect otherwise).
    Invalid: the modal body again with its errors over HTMX, or the whole tab with the modal open.
    """
    target = _target_guide(request, request.POST.get("guide", ""))
    form = _day_off_form(target, request.POST)
    is_htmx = request.headers.get("HX-Request") == "true"
    if not form.is_valid():
        if is_htmx:
            return render(
                request,
                "tours/partials/_day_off_form.html",
                {"target": target, "is_self": target.pk == _viewer(request).pk, "day_off_form": form},
            )
        return _render_hours(request, target, day_off_form=form, open_day_off=True)
    day_off = form.save()
    messages.success(request, f"Day off saved: {day_off.label}.")
    url = _hours_url(_viewer(request), target)
    if is_htmx:
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return redirect(url)


@tour_guide_access_required
@require_POST
def tour_day_off_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Remove a day off: the guide's own, or anyone's for an admin (403 otherwise)."""
    day_off = get_object_or_404(TourDayOff.objects.select_related("guide"), pk=pk)
    target = _target_guide(request, str(day_off.guide_id))
    day_off.delete()
    messages.success(request, "Day off removed. Those days offer tours again.")
    return redirect(_hours_url(_viewer(request), target))


@fog_admin_required
def tour_settings(request: HttpRequest) -> HttpResponse:
    """The Tours page's Settings tab (admins only): the front desk kiosk link (#950), then horizon,
    notice, cutoff, defaults and the bookings switch."""
    instance = FacilityTourSettings.load()
    form = FacilityTourSettingsForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Tour Settings saved.")
        return redirect("tours:settings")
    return render(request, "tours/settings.html", {"form": form, "kiosk_url": _kiosk_url(instance)})


def _kiosk_url(house_rules: FacilityTourSettings) -> str:
    """The link the lobby screen opens once to become the kiosk (#950), or "" before one is made."""
    if not house_rules.kiosk_key:
        return ""
    return f"{settings.TOURS_BASE_URL}{reverse('tours_kiosk:home')}?{urlencode({'key': house_rules.kiosk_key})}"


@fog_admin_required
@require_POST
def tour_kiosk_key(request: HttpRequest) -> HttpResponse:
    """Create the kiosk key, or rotate it (#950): a screen holding the old one shows the public booking page."""
    house_rules = FacilityTourSettings.load()
    rotating = bool(house_rules.kiosk_key)
    house_rules.rotate_kiosk_key()
    if rotating:
        messages.success(request, "Kiosk key rotated. Open the new link on the lobby screen.")
    else:
        messages.success(request, "Kiosk link created. Open it once on the lobby screen.")
    return redirect("tours:settings")


# --- Google Calendar (#951) ---------------------------------------------------------------------


@tour_guide_access_required
def tour_google_connect(request: HttpRequest) -> HttpResponse:
    """Start Connect (or Reconnect) Google Calendar for the signed in guide: a ``state`` in the session, then Google.

    Only ever the viewer's own calendar: Google signs in whoever presses it, so an admin cannot
    connect one for someone else.
    """
    _viewer(request)
    if not google.is_configured():
        messages.error(request, "Connecting a Google Calendar is not set up yet.")
        return redirect("tours:hours")
    state = secrets.token_urlsafe(32)
    request.session[GOOGLE_STATE_SESSION_KEY] = state
    return redirect(google.authorize_url(state, login_hint=cast("User", request.user).email))


@tour_guide_access_required
def tour_google_callback(request: HttpRequest) -> HttpResponse:
    """Where Google sends the guide back: check ``state``, store the connection, land on My Hours.

    The ``state`` is popped from the session first, so it works once. A cancel on Google's screen,
    a ``state`` that does not match, an unticked permission and a Google failure each land on My
    Hours with what to do, never an error page.
    """
    guide = _viewer(request)
    expected = request.session.pop(GOOGLE_STATE_SESSION_KEY, "")
    if request.GET.get("error"):
        messages.info(request, "Google Calendar was not connected.")
        return redirect("tours:hours")
    state, code = request.GET.get("state", ""), request.GET.get("code", "")
    if not (code and state and expected and secrets.compare_digest(state, expected)):
        messages.error(request, "We could not check that Google sign in. Press Connect Google Calendar to try again.")
        return redirect("tours:hours")
    if not google.is_configured():
        messages.error(request, "Connecting a Google Calendar is not set up yet.")
        return redirect("tours:hours")
    try:
        calendar_sync.connect_from_code(guide, code)
    except google.GoogleMissingScopeError:
        messages.error(
            request,
            "Google did not share everything tours need. Connect again and leave every box ticked on Google's screen.",
        )
    except google.GoogleError:
        messages.error(request, "We could not connect your Google Calendar. Please try again.")
    else:
        messages.success(
            request,
            "Google Calendar connected. Tours you give will be added to it, and times it shows you busy "
            "are not offered to visitors.",
        )
    return redirect("tours:hours")


@tour_guide_access_required
@require_POST
def tour_google_calendar(request: HttpRequest) -> HttpResponse:
    """Pick which of the guide's own calendars tours are written to; their upcoming tours move there."""
    connection = get_object_or_404(
        TourCalendarConnection, guide=_viewer(request), status=TourCalendarConnection.Status.CONNECTED
    )
    form = TourCalendarChoiceForm(request.POST, connection=connection)
    if not form.is_valid():
        messages.error(request, "Pick one of your calendars from the list.")
        return redirect("tours:hours")
    if form.cleaned_data["calendar"] != connection.calendar_id:
        calendar_sync.choose_calendar(connection, form.cleaned_data["calendar"])
    messages.success(request, f"Tours will be written to {connection.calendar_label}.")
    return redirect("tours:hours")


@tour_guide_access_required
@require_POST
def tour_google_disconnect(request: HttpRequest) -> HttpResponse:
    """Disconnect the guide's Google Calendar (after the confirm): revoke the token at Google and delete it here."""
    connection = get_object_or_404(TourCalendarConnection, guide=_viewer(request))
    if calendar_sync.disconnect(connection):
        messages.success(request, "Google Calendar disconnected. Tours already on it stay there.")
    else:
        messages.info(
            request,
            "Google Calendar disconnected here, but Google did not confirm it. You can also remove Past Lives "
            "from the apps connected to your Google Account.",
        )
    return redirect("tours:hours")


# --- Upcoming and Past (#949) ------------------------------------------------------------------


def _list_guide(request: HttpRequest) -> Member | None:
    """Whose bookings the Upcoming and Past tabs list: a guide's own; an admin's every guide's (None) or ``?guide=``.

    A guide naming another guide gets a 403, like every other way into someone else's tours.
    """
    viewer = _viewer(request)
    raw = request.GET.get("guide", "")
    if not _is_admin(request):
        if raw and raw != str(viewer.pk):
            raise PermissionDenied("Only an admin can see another guide's tours.")
        return viewer
    if not raw:
        return None
    if not raw.isdigit():
        raise Http404("Unknown guide.")
    return get_object_or_404(Member, pk=int(raw))


def _filter_guides() -> list[Member]:
    """The admin's guide filter: everyone who gives tours, and anyone who has bookings, by name."""
    people = Member.objects.filter(guide_filter() | Q(tour_bookings__isnull=False)).distinct()
    return sorted(people, key=lambda member: member.display_name.casefold())


def _render_bookings(request: HttpRequest, tab: str) -> HttpResponse:
    """One of the two list tabs: the guide's bookings (or an admin's pick), paged."""
    now = timezone.now()
    is_admin = _is_admin(request)
    guide = _list_guide(request)
    bookings = TourBooking.objects.select_related("guide")
    listed = bookings.upcoming(now) if tab == UPCOMING else bookings.past(now)
    if guide is not None:
        listed = listed.filter(guide=guide)
    page = Paginator(listed, BOOKINGS_PER_PAGE).get_page(request.GET.get("page"))
    filtered = is_admin and guide is not None
    return render(
        request,
        "tours/bookings.html",
        {
            "tab": tab,
            "is_admin": is_admin,
            "page": page,
            "bookings": list(page.object_list),
            "filter_guides": _filter_guides() if is_admin else [],
            "filter_guide": guide if filtered else None,
            "base_params": urlencode({"guide": guide.pk}) if filtered and guide is not None else "",
        },
    )


@tour_guide_access_required
def tour_upcoming(request: HttpRequest) -> HttpResponse:
    """The Tours page's first tab: booked tours that have not started, soonest first (``?guide=`` for an admin)."""
    return _render_bookings(request, UPCOMING)


@tour_guide_access_required
def tour_past(request: HttpRequest) -> HttpResponse:
    """The Past tab: tours that have started, newest first, each to mark attended or no show."""
    return _render_bookings(request, PAST)


def _staff_booking(request: HttpRequest, pk: int) -> TourBooking:
    """The booking a row action names: the guide's own, or any for an admin (403 otherwise)."""
    booking = get_object_or_404(TourBooking.objects.select_related("guide"), pk=pk)
    if not _is_admin(request) and booking.guide_id != _viewer(request).pk:
        raise PermissionDenied("Only an admin can change another guide's tour.")
    return booking


def _destination_guide(request: HttpRequest, booking: TourBooking, raw: str) -> Member:
    """Whose open tours a reschedule picks from: the booking's guide by default.

    Anyone else is a reassignment, an admin's only (403 for a guide), and must give tours (404
    otherwise). A booking with no guide starts from the viewer's own tours.
    """
    own = booking.guide if booking.guide is not None else _viewer(request)
    if not raw or raw == str(own.pk):
        return own
    if not _is_admin(request):
        raise PermissionDenied("Only an admin can move a tour to another guide.")
    if not raw.isdigit():
        raise Http404("Unknown guide.")
    return get_object_or_404(Member.objects.filter(guide_filter()).distinct(), pk=int(raw))


def _staff_tours(booking: TourBooking, guide: Member) -> list[OpenTour]:
    """The guide's open tours a staff member may move the booking to: room for the party, not its own tour.

    No minimum notice (any tour that has not started), within the horizon.
    """
    today = timezone.localdate()
    return [
        tour
        for tour in open_tours(today, today + timedelta(days=MAX_HORIZON_DAYS), guide=guide, staff=True)
        if tour.seats_left >= booking.party_size
        and not (tour.guide.pk == booking.guide_id and tour.starts_at == booking.starts_at)
    ]


def _move_guides(destination: Member) -> list[Member]:
    """The admin's destination picker: everyone who gives tours and has tour hours, plus the one shown."""
    people = Member.objects.filter(guide_filter(), tour_hours__isnull=False).distinct()
    found = {member.pk: member for member in people}
    found.setdefault(destination.pk, destination)
    return sorted(found.values(), key=lambda member: member.display_name.casefold())


def _render_reschedule(
    request: HttpRequest,
    booking: TourBooking,
    guide: Member,
    *,
    raw_tour: str = "",
    raw_day: str = "",
    form: StaffRescheduleForm | None = None,
    error: str = "",
) -> HttpResponse:
    """The Reschedule modal's body: the guide picker (admins), the date strip and times, or the confirm."""
    tours = _staff_tours(booking, guide)
    chosen = find_tour(tours, raw_tour)
    if chosen is not None and form is None:
        form = StaffRescheduleForm(initial={"tour": chosen.key})
    if chosen is None:
        if form is not None and not error:
            # A refused message on a time that filled or closed meanwhile: say so over the picker.
            error = GONE_MESSAGE
        form = None
    day = f"{timezone.localdate(chosen.starts_at):%Y-%m-%d}" if chosen else raw_day
    is_admin = _is_admin(request)
    return render(
        request,
        "tours/partials/_reschedule.html",
        {
            "booking": booking,
            "when": when_label(booking.starts_at),
            "destination": guide,
            "is_admin": is_admin,
            "move_guides": _move_guides(guide) if is_admin else [],
            "picker": build_picker(tours, day),
            "chosen": chosen,
            "chosen_when": when_label(chosen.starts_at) if chosen else "",
            "form": form,
            "error": error,
            "move_url": reverse("tours:booking_reschedule", args=[booking.pk]),
        },
    )


def _back_url(request: HttpRequest, fallback: str = "tours:upcoming") -> str:
    """The Tours page the action came from (htmx's ``HX-Current-URL``), else ``fallback``'s tab."""
    current = request.headers.get("HX-Current-URL", "")
    parts = urlsplit(current)
    same_site = url_has_allowed_host_and_scheme(
        current, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    if same_site and parts.path.startswith(reverse("tours:upcoming")):
        return f"{parts.path}?{parts.query}" if parts.query else parts.path
    return reverse(fallback)


def _done(request: HttpRequest) -> HttpResponse:
    """Back to the tab the action came from, where the flash shows as a toast: ``HX-Redirect`` over htmx."""
    url = _back_url(request)
    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return redirect(url)


@tour_guide_access_required
def tour_booking_reschedule(request: HttpRequest, pk: int) -> HttpResponse:
    """The Reschedule modal (GET, a fragment) and the move itself (POST), for a guide or an admin.

    GET ``?guide=`` (admins) picks whose tours, ``?date=`` a day and ``?tour=<key>`` a time, which
    shows the confirm with the optional message to the visitor. The POST moves the booking through
    :func:`tours.booking.reschedule_tour_by_staff`; a refusal shows the modal again with why. The
    posted tour's guide goes through :func:`_destination_guide`, so a guide cannot reassign by a
    forged key. A plain GET (not htmx) lands on the Upcoming tab.
    """
    booking = _staff_booking(request, pk)
    if request.method != "POST":
        if not wants_fragment(request):
            return redirect("tours:upcoming")
        reason = booking.staff_refusal(timezone.now())
        if reason:
            return render(
                request,
                "tours/partials/_reschedule_locked.html",
                {"booking": booking, "message": STAFF_LOCKED_MESSAGES[reason]},
            )
        guide = _destination_guide(request, booking, request.GET.get("guide", ""))
        return _render_reschedule(
            request, booking, guide, raw_tour=request.GET.get("tour", ""), raw_day=request.GET.get("date", "")
        )
    form = StaffRescheduleForm(request.POST)
    target = guide_and_start(request.POST.get("tour", ""))
    guide = _destination_guide(request, booking, str(target[0].pk) if target else "")
    if target is None:
        return _render_reschedule(request, booking, guide, error=GONE_MESSAGE)
    if not form.is_valid():
        return _render_reschedule(request, booking, guide, raw_tour=form.data["tour"], form=form)
    try:
        moved = reschedule_tour_by_staff(
            booking,
            guide=guide,
            starts_at=target[1],
            actor=cast("User", request.user),
            message=form.cleaned_data["message"],
        )
    except TourLockedError as exc:
        messages.error(request, STAFF_LOCKED_MESSAGES[exc.reason])
        return _done(request)
    except TourBookingError as exc:
        return _render_reschedule(request, booking, guide, error=str(exc))
    messages.success(
        request, f"{moved.visitor_name}'s tour moved to {short_when(moved.starts_at)}. We emailed them the new time."
    )
    return _done(request)


@tour_guide_access_required
@require_POST
def tour_booking_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    """Cancel a booking from the Upcoming tab's confirm, with an optional message to the visitor.

    Frees the seats and emails the visitor the cancellation with its calendar update, at any time
    before the tour starts (no cancel cutoff for staff).
    """
    booking = _staff_booking(request, pk)
    form = StaffTourMessageForm(request.POST)
    if not form.is_valid():
        messages.error(request, str(form.errors["message"][0]))
        return _done(request)
    try:
        cancelled = cancel_tour_by_staff(
            booking, actor=cast("User", request.user), message=form.cleaned_data["message"]
        )
    except TourLockedError as exc:
        messages.error(request, STAFF_LOCKED_MESSAGES[exc.reason])
        return _done(request)
    messages.success(
        request, f"{cancelled.visitor_name}'s tour on {short_when(cancelled.starts_at)} is cancelled. We emailed them."
    )
    return _done(request)


@tour_guide_access_required
@require_POST
def tour_booking_mark(request: HttpRequest, pk: int) -> HttpResponse:
    """Mark a tour on the Past tab attended or no show (``outcome``); over htmx the row comes back with a toast."""
    booking = _staff_booking(request, pk)
    outcome = request.POST.get("outcome", "")
    if outcome not in OUTCOMES:
        return HttpResponseBadRequest("Unknown outcome.")
    try:
        booking = mark_tour_outcome(booking, outcome)
    except TourLockedError as exc:
        text, tone = STAFF_LOCKED_MESSAGES[exc.reason], "error"
        booking.refresh_from_db()
    else:
        label = "attended" if outcome == TourBooking.Status.ATTENDED else "a no show"
        text, tone = f"{booking.visitor_name} marked {label}.", "success"
    if request.headers.get("HX-Request") != "true":
        messages.add_message(request, messages.SUCCESS if tone == "success" else messages.ERROR, text)
        return redirect(_back_url(request, "tours:past"))
    response = render(
        request,
        "tours/partials/_booking_row.html",
        {"booking": booking, "tab": PAST, "is_admin": _is_admin(request)},
    )
    trigger_toast(response, text, tone)
    return response
