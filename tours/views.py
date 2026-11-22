"""The Tours page: the Upcoming and Past tabs with their staff actions (#949), the My Hours tab, and
the Settings tab for admins (#947).

The page is opened from the Tours entry in the sidebar, which every active member has (#1278).
Everything here sits behind :func:`hub.view_as.tour_guide_access_required` (Tour Guides and
admins; anyone else opening a tab lands on Give Tours) or :func:`hub.view_as.fog_admin_required`
(the Settings tab). Give Tours and its Become a Tour Guide are
:func:`hub.view_as.give_tours_access_required` (any active member). Which guide's hours a request
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
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.htmx import wants_fragment
from hub.autosave import autosave_refused, autosave_saved, formset_rows, wants_autosave
from hub.calendar_pages import calendar_nav_params
from hub.toast import trigger_toast
from hub.tutorial import TutorialStep
from hub.view_as import (
    ROLE_ADMIN,
    fog_admin_required,
    give_tours_access_required,
    has_tour_guide_access,
    tour_guide_access_required,
)
from membership.models import AdminCapability, Member, WikiPage
from membership.permission_setup import SetupState
from membership.permissions import can_edit_wiki_page
from tours import calendar_sync, coverage, google
from tours.availability import OpenTour, open_tours
from tours.booking import (
    GONE_MESSAGE,
    OUTCOMES,
    STAFF_LOCKED_MESSAGES,
    TourBookingError,
    TourLockedError,
    cancel_tour_by_staff,
    confirm_tour_request,
    decline_tour_request,
    lock_for_note,
    mark_tour_outcome,
    propose_tour_time,
)
from tours.forms import (
    FacilityTourSettingsForm,
    StaffRescheduleForm,
    StaffTourMessageForm,
    MAX_DATED_DAYS_AHEAD,
    TourCalendarChoiceForm,
    TourConfirmModeForm,
    TourDatesForm,
    TourDayOffForm,
    TourHoursFormSet,
    TourNoteForm,
)
from tours.models import (
    MAX_HORIZON_DAYS,
    FacilityTourSettings,
    TourBooking,
    TourBookingNote,
    TourCalendarConnection,
    TourDayOff,
    TourGuideSettings,
    TourHours,
    guide_filter,
    lock_guide_hours,
)
from tours.notify import short_when, when_label
from tours.picker import build_picker, find_tour, guide_and_start
from tours.setup import become_tour_guide

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


def _setup(request: HttpRequest) -> dict[str, Any]:
    """The viewer's own Get Set Up card for Tour Guide (#1167), for the top of every Tours tab.

    None unless the viewer holds the grant (an admin without it gets no card). The load that
    finishes setup, or first sees it finished, toasts the badge.
    """
    member: Member | None = getattr(request.user, "member", None)
    state = SetupState.load(member, AdminCapability.Capability.TOUR_GUIDE) if member is not None else None
    if state is not None and state.just_finished:
        messages.success(request, state.spec.celebration)
    return {"setup": state, "setup_next": request.get_full_path()}


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
    return sorted(people, key=lambda member: member.staff_label.casefold())


def _picker_options(viewer: Member) -> list[dict[str, Any]]:
    """The picker's guides, each with their Google Calendar's state when the feature is set up (#951)."""
    guides = _picker_guides(viewer)
    states: dict[int, str] = {}
    if google.is_configured():
        states = dict(TourCalendarConnection.objects.filter(guide__in=guides).values_list("guide_id", "status"))
    return [
        {
            "pk": guide.pk,
            "name": guide.staff_label,
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
    """The guide's weekly hours formset (dated rows never show in it), new rows starting from Tour Settings' defaults."""
    return TourHoursFormSet(
        data,
        instance=target,
        queryset=TourHours.objects.weekly(),
        prefix=HOURS_PREFIX,
        form_kwargs={"defaults": FacilityTourSettings.load()},
    )


def _day_off_form(target: Member, data: Any = None) -> TourDayOffForm:
    return TourDayOffForm(data, prefix=DAY_OFF_PREFIX, guide=target)


def _dates_form(target: Member, data: Any = None) -> TourDatesForm:
    return TourDatesForm(data, guide=target, defaults=FacilityTourSettings.load())


def _calendar_config(
    target: Member, dated: list[TourHours], dates_form: TourDatesForm, *, add: date | None = None
) -> dict[str, Any]:
    """What the Available On calendar (``plTourDates``) draws: the pickable range, the days that
    already have dated hours (a dot), the days off (struck through) and any picks to restore, or
    the day a Calendar tab gap asked to add (``add``, #1274)."""
    today = timezone.localdate()
    last = today + timedelta(days=MAX_DATED_DAYS_AHEAD)
    return {
        "today": today.isoformat(),
        "last": last.isoformat(),
        "dated": sorted({row.date.isoformat() for row in dated if row.date is not None}),
        "off": [day.isoformat() for day in TourDayOff.objects.filter(guide=target).covered_days(today, last)],
        "picked": dates_form.picked_iso if dates_form.is_bound or add is None else [add.isoformat()],
    }


def _day_to_add(raw: str) -> date | None:
    """The ``?add=YYYY-MM-DD`` day a Calendar tab gap links with (#1274), when Add Dates may pick it; else None."""
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        return None
    today = timezone.localdate()
    return day if today <= day <= today + timedelta(days=MAX_DATED_DAYS_AHEAD) else None


def _render_hours(
    request: HttpRequest,
    target: Member,
    *,
    formset: Any = None,
    day_off_form: TourDayOffForm | None = None,
    open_day_off: bool = False,
    hours_refused: bool = False,
    dates_form: TourDatesForm | None = None,
    add: date | None = None,
) -> HttpResponse:
    viewer = _viewer(request)
    is_admin = _is_admin(request)
    is_self = target.pk == viewer.pk
    connection = TourCalendarConnection.objects.filter(guide=target).first()
    dates_form = dates_form if dates_form is not None else _dates_form(target)
    dated = list(TourHours.objects.upcoming_dated().filter(guide=target))
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
            "dates_form": dates_form,
            "open_dates": dates_form.is_bound or add is not None,
            "dated_hours": dated,
            "dates_calendar": _calendar_config(target, dated, dates_form, add=add),
            "confirm_form": TourConfirmModeForm(instance=TourGuideSettings.objects.filter(guide=target).first()),
            **_setup(request),
        },
    )


@tour_guide_access_required
def tour_hours(request: HttpRequest) -> HttpResponse:
    """The Tours page's My Hours tab: the weekly hours editor and the days off (``?guide=`` for an admin).

    ``?add=YYYY-MM-DD`` (a gap on the Calendar tab, #1274) opens Add Dates with that day picked.
    """
    target = _target_guide(request, request.GET.get("guide", ""))
    return _render_hours(request, target, add=_day_to_add(request.GET.get("add", "")))


@tour_guide_access_required
@require_POST
def tour_hours_save(request: HttpRequest) -> HttpResponse:
    """Save one guide's whole set of hours rows (adds, edits and deletes in one POST).

    The posted ``guide`` is checked before the formset is read. The Every Week card saves itself
    as it is edited (#1279, ``hub.autosave``): its posts answer JSON, each row's pk in posted
    order or a 422 with every error keyed by field name (an overlap on its row); a row deleted
    elsewhere since the page loaded is skipped, named under ``gone`` and toasted. Without the
    script, valid is a flash and a redirect back to the tab, invalid the tab again with each
    row's errors, its rows open.
    """
    target = _target_guide(request, request.POST.get("guide", ""))
    formset = _hours_formset(target, request.POST)
    # The overlap check and the save share one lock on the guide (#1160), so a save at the same
    # moment cannot land a clashing row between them.
    with transaction.atomic():
        lock_guide_hours(target)
        saved = formset.is_valid()
        if saved:
            formset.save()
    if wants_autosave(request):
        if not saved:
            return autosave_refused(formset)
        gone = formset.gone_rows
        response = autosave_saved({HOURS_PREFIX: formset_rows(formset)}, gone={HOURS_PREFIX: gone} if gone else None)
        if gone:
            # Deleted elsewhere (another tab, an admin on ?guide=): the rest saved, the page drops it.
            trigger_toast(
                response, "Some of these hours were deleted elsewhere, so they are gone from this page.", "info"
            )
        return response
    if not saved:
        return _render_hours(request, target, formset=formset, hours_refused=True)
    messages.success(request, "Tour hours saved.")
    return redirect(_hours_url(_viewer(request), target))


@tour_guide_access_required
@require_POST
def tour_confirm_mode_save(request: HttpRequest) -> HttpResponse:
    """Save whether the guide's bookings wait for their Confirm (#1166): the Confirming Bookings card.

    The card saves itself as it is edited (#1279): an autosave post answers JSON, a refusal a 422
    under the field. Without the script, a flash and a redirect back to the tab.
    """
    target = _target_guide(request, request.POST.get("guide", ""))
    instance = TourGuideSettings.objects.filter(guide=target).first() or TourGuideSettings(guide=target)
    form = TourConfirmModeForm(request.POST, instance=instance)
    if not form.is_valid():
        if wants_autosave(request):
            return autosave_refused(form)
        messages.error(request, str(next(iter(form.errors.values()))[0]))
        return redirect(_hours_url(_viewer(request), target))
    saved = form.save()
    if wants_autosave(request):
        return autosave_saved()
    messages.success(request, f"Saved. {saved.summary}")
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


@tour_guide_access_required
@require_POST
def tour_dates_save(request: HttpRequest) -> HttpResponse:
    """Add hours on every date picked in the Available On calendar (#1160), all or nothing.

    Valid: "Added 3 dates." and a redirect back to the tab. Invalid: the tab again with the panel
    open, the picks and fields kept, and one message per problem.
    """
    target = _target_guide(request, request.POST.get("guide", ""))
    form = _dates_form(target, request.POST)
    added = 0
    with transaction.atomic():
        lock_guide_hours(target)
        saved = form.is_valid()
        if saved:
            added = len(form.save())
    if not saved:
        return _render_hours(request, target, dates_form=form)
    messages.success(request, f"Added {added} date{'' if added == 1 else 's'}.")
    return redirect(_hours_url(_viewer(request), target))


@tour_guide_access_required
@require_POST
def tour_dates_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete one dated window (#1160): the guide's own, or anyone's for an admin (403 otherwise).

    Weekly rows are not found here (404); the Every Week editor deletes those.
    """
    row = get_object_or_404(TourHours.objects.dated(), pk=pk)
    target = _target_guide(request, str(row.guide_id))
    row.delete()
    messages.success(request, "Hours deleted.")
    return redirect(_hours_url(_viewer(request), target))


@tour_guide_access_required  # a member who gives no tours lands on Give Tours, like every other tab
@fog_admin_required
def tour_settings(request: HttpRequest) -> HttpResponse:
    """The Tours page's Settings tab (admins only): the front desk kiosk link (#950), then horizon,
    notice, cutoff, defaults and the bookings switch.

    Tour Settings save themselves as they are edited (#1279): an autosave post answers JSON, a
    refusal a 422 under the field, which leaves every stored value as it was. Without the
    script, a flash and a redirect, or the tab again with the errors.
    """
    instance = FacilityTourSettings.load()
    form = FacilityTourSettingsForm(request.POST or None, instance=instance)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            if wants_autosave(request):
                return autosave_saved()
            messages.success(request, "Tour Settings saved.")
            return redirect("tours:settings")
        if wants_autosave(request):
            return autosave_refused(form)
    return render(request, "tours/settings.html", {"form": form, "kiosk_url": _kiosk_url(instance), **_setup(request)})


@tour_guide_access_required
def tour_guide(request: HttpRequest) -> HttpResponse:
    """The Guide tab (#1167): the wiki page an admin linked to Tour Guide, and the way back to a hidden card."""
    page = WikiPage.guide_page_for(AdminCapability.Capability.TOUR_GUIDE)
    if page is not None:
        page = WikiPage.objects.select_related("updated_by").prefetch_related("facts").get(pk=page.pk)
    return render(
        request,
        "tours/guide.html",
        {
            "tab": "guide",
            "is_admin": _is_admin(request),
            "page": page,
            "facts": list(page.facts.all()) if page is not None else [],
            "toc": page.toc() if page is not None and page.has_written_body else [],
            "can_edit": page is not None and can_edit_wiki_page(request, page),
            **_setup(request),
        },
    )


#: The toast on My Hours after Become a Tour Guide (#1278).
BECAME_GUIDE_MESSAGE = "You're a Tour Guide now. Set the hours you can give tours, and visitors can book you."
#: The toast for a second Become a Tour Guide that raced the first past the view's check.
ALREADY_GUIDE_MESSAGE = "You're already a Tour Guide."


#: Give Tours' tutorial (#1278), one step per section of the Tour Guide Guide wiki page, drawn by
#: ``hub/partials/_tutorial.html``.
GIVE_TOURS_TUTORIAL = (
    TutorialStep(
        "Setting Up",
        (
            "On My Hours, set the times you can give tours: Every Week for hours that repeat, or Available On for "
            "dates you pick.",
            "Each window is carved into back to back tours of the length you choose, each with 1 to 50 seats.",
            "Add Days Off for when you are away. Your hours offer nothing on those days, and tours already booked "
            "stay booked.",
            "Connect your Google Calendar on My Hours if you use one. Your tours land on it, and times it shows you "
            "busy are not offered to visitors.",
            "The Calendar tab shows every guide's hours. Days With No Tour Hours lists the days nobody covers yet, "
            "and Add My Hours fills one.",
        ),
    ),
    TutorialStep(
        "When Someone Books",
        (
            "Visitors book an open time inside your hours online at tours.pastlives.space, or at the kiosk in the "
            "lobby.",
            "You get an email, a bell and a push for each booking, and an email and a bell when a visitor moves or "
            "cancels.",
            "To confirm or decline each booking yourself, pick Confirm First under Confirming Bookings on My Hours.",
            "Reschedule or cancel from the Upcoming tab: press the ... on the tour for Edit Guide or Time, or Cancel "
            "Tour. The visitor is emailed, with your note if you add one.",
        ),
    ),
    TutorialStep(
        "A Tour Day",
        (
            "Visitors who booked a day or more ahead get a reminder email 24 hours before the tour.",
            'Meet them in the lobby. When they check in at the kiosk you get a bell and a push that says "Your '
            '10:00 tour is here".',
            "Afterwards, mark the tour Attended or No Show on the Past tab.",
        ),
    ),
    TutorialStep(
        "The Lobby Kiosk",
        (
            "The screen in the lobby has two buttons, I Booked a Tour and Schedule a Tour.",
            "Visitors check in with the email they booked with or their name, and the kiosk tells them you will be "
            "right with them.",
            "Walk ins can book an open time right there.",
        ),
    ),
    TutorialStep(
        "Getting Paid",
        (
            "When a visitor you gave a tour to becomes a member, you're paid $50.",
            "Mark each tour Attended on the Past tab: that is the record of who you toured.",
            "Want to stop later? Delete your hours on My Hours, or ask an admin to turn Tour Guide off.",
        ),
    ),
)


@give_tours_access_required
def tour_give(request: HttpRequest) -> HttpResponse:
    """Give Tours (#1278): why giving tours is worth it, and the tutorial that ends in Become a Tour Guide.

    Where every member who gives no tours lands from the Tours entry. A Tour Guide or admin who
    opens it sees it too, with Open My Hours in place of the button that grants.
    """
    return render(
        request, "tours/give.html", {"is_guide": has_tour_guide_access(request), "tutorial_steps": GIVE_TOURS_TUTORIAL}
    )


@give_tours_access_required
@require_POST
def tour_become_guide(request: HttpRequest) -> HttpResponse:
    """Become a Tour Guide, the tutorial's last step (#1278): grant Tour Guide, then My Hours with a toast.

    A Tour Guide or admin pressing it again changes nothing and goes to My Hours.
    """
    if has_tour_guide_access(request):
        return redirect("tours:hours")
    # Two presses racing both pass the check above; the one that loses the lock is told so.
    messages.success(request, BECAME_GUIDE_MESSAGE if become_tour_guide(_viewer(request)) else ALREADY_GUIDE_MESSAGE)
    return redirect("tours:hours")


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
    """Pick which of the guide's own calendars tours are written to; their upcoming tours move there.

    The picker saves on change (#1279): an autosave post answers JSON, a refusal a 422 under it.
    """
    connection = get_object_or_404(
        TourCalendarConnection, guide=_viewer(request), status=TourCalendarConnection.Status.CONNECTED
    )
    form = TourCalendarChoiceForm(request.POST, connection=connection)
    if not form.is_valid():
        if wants_autosave(request):
            return autosave_refused(form)
        messages.error(request, "Pick one of your calendars from the list.")
        return redirect("tours:hours")
    if form.cleaned_data["calendar"] != connection.calendar_id:
        calendar_sync.choose_calendar(connection, form.cleaned_data["calendar"])
    if wants_autosave(request):
        return autosave_saved()
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
    """Whose bookings the Upcoming and Past tables list (#1274): every guide's (None), or ``?guide=``'s.

    Every Tour Guide sees every guide's bookings, read only on the rows that are not theirs
    (:func:`_may_act`); ``?guide=`` with their own pk is My Tours.
    """
    raw = request.GET.get("guide", "")
    if not raw:
        return None
    if not raw.isdigit():
        raise Http404("Unknown guide.")
    return get_object_or_404(Member, pk=int(raw))


def _filter_guides() -> list[Member]:
    """The table's guide filter: everyone who gives tours, and anyone who has bookings, by name."""
    people = Member.objects.filter(guide_filter() | Q(tour_bookings__isnull=False)).distinct()
    return sorted(people, key=lambda member: member.staff_label.casefold())


#: The Upcoming table's status chips (#1274): ``?show=`` and the statuses each one lists.
SHOW_CHIPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "all": ("All", TourBooking.UPCOMING),
    "requested": ("Requested", (TourBooking.Status.PENDING,)),
    "confirmed": ("Confirmed", (TourBooking.Status.BOOKED,)),
}


def _may_act(request: HttpRequest, booking: TourBooking) -> bool:
    """Whether the viewer may edit, cancel, mark or email the visitor about ``booking``: its guide, or an admin."""
    return _is_admin(request) or booking.guide_id == _viewer(request).pk


def _with_row_rights(request: HttpRequest, bookings: list[TourBooking]) -> list[TourBooking]:
    """Stamp each row with what the viewer may do on it, for its "..." menu (never stored)."""
    viewer_pk = _viewer(request).pk
    is_admin = _is_admin(request)
    for booking in bookings:
        booking.can_act = is_admin or booking.guide_id == viewer_pk  # type: ignore[attr-defined]
        booking.can_answer = booking.is_request and booking.guide_id == viewer_pk  # type: ignore[attr-defined]
        # Felix (#1274): any active member may become a Tour Guide (#1278), so a visitor's email and
        # phone reach only their own guide and the admins; never rendered for anyone else.
        booking.can_see_contact = booking.can_act  # type: ignore[attr-defined]
    return bookings


def _render_bookings(request: HttpRequest, tab: str) -> HttpResponse:
    """One of the two table tabs (#1274): every guide's bookings, or one guide's, paged, with each row's menu."""
    now = timezone.now()
    guide = _list_guide(request)
    show = request.GET.get("show", "all") if tab == UPCOMING else "all"
    if show not in SHOW_CHIPS:
        show = "all"
    bookings = TourBooking.objects.select_related("guide", "proposed_guide").annotate(note_count=Count("notes"))
    listed = bookings.upcoming(now) if tab == UPCOMING else bookings.past(now)
    if guide is not None:
        listed = listed.filter(guide=guide)
    requested_count = 0
    if tab == UPCOMING:
        requested_count = listed.filter(status=TourBooking.Status.PENDING).count()
        listed = listed.filter(status__in=SHOW_CHIPS[show][1])
    page = Paginator(listed, BOOKINGS_PER_PAGE).get_page(request.GET.get("page"))
    listed_bookings = _with_row_rights(request, list(page.object_list))
    TourBooking.attach_shared_with(listed_bookings)
    _count_shared_notes(listed_bookings)
    viewer = _viewer(request)
    params: dict[str, Any] = {"guide": guide.pk if guide else "", "show": show}
    url = reverse("tours:past" if tab == PAST else "tours:upcoming")
    chips = [
        {
            "key": key,
            "label": label,
            "count": requested_count if key == "requested" else 0,
            "url": _with_query(url, {**params, "show": key}),
        }
        for key, (label, _statuses) in SHOW_CHIPS.items()
    ]
    return render(
        request,
        "tours/bookings.html",
        {
            "tab": tab,
            "is_admin": _is_admin(request),
            "viewer_pk": viewer.pk,
            "page": page,
            "bookings": listed_bookings,
            "filter_guides": _filter_guides(),
            "filter_guide": guide,
            "show": show,
            "chips": chips if tab == UPCOMING else [],
            "is_filtered": guide is not None or show != "all",
            "base_params": _with_query("", params).lstrip("?"),
            "notes_open": _notes_to_open(request),
            "notes_next": request.get_full_path(),
            **_setup(request),
        },
    )


def _with_query(url: str, params: dict[str, Any]) -> str:
    """``url`` with ``params``, leaving out blanks and the default ``show=all``."""
    query = {key: value for key, value in params.items() if value and not (key == "show" and value == "all")}
    return f"{url}?{urlencode(query)}" if query else url


def _count_shared_notes(bookings: list[TourBooking]) -> None:
    """A shared request's rows show one Notes thread (#1274), so each counts the whole group's notes (one query)."""
    groups = {booking.request_group for booking in bookings if booking.request_group is not None}
    if not groups:
        return
    counts = dict(
        TourBookingNote.objects.filter(booking__request_group__in=groups)
        .values_list("booking__request_group")
        .annotate(total=Count("pk"))
    )
    for booking in bookings:
        if booking.request_group is not None:
            booking.note_count = counts.get(booking.request_group, 0)  # type: ignore[attr-defined]


def _notes_to_open(request: HttpRequest) -> int | None:
    """The booking whose Notes the page opens on arrival (``?notes=<pk>``, a bell's link), or None."""
    raw = request.GET.get("notes", "")
    if not raw.isdigit() or not TourBooking.objects.filter(pk=int(raw)).exists():
        return None
    return int(raw)


@tour_guide_access_required
def tour_upcoming(request: HttpRequest) -> HttpResponse:
    """The Tours page's first tab: every guide's booked tours and requests that have not started, soonest first (#1274: ``?guide=``, ``?show=``)."""
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
    """Whose open tours an Edit picks from: the booking's guide by default.

    Anyone else is a reassignment, open to whoever may act on the booking (#1274: its guide hands
    it on, or an admin; :func:`_staff_booking` has already checked), and must give tours (404
    otherwise). A booking with no guide starts from the viewer's own tours.
    """
    own = booking.guide if booking.guide is not None else _viewer(request)
    if not raw or raw == str(own.pk):
        return own
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
    """The Edit modal's guide picker: everyone who gives tours and has tour hours, plus the one shown."""
    people = Member.objects.filter(guide_filter(), tour_hours__isnull=False).distinct()
    found = {member.pk: member for member in people}
    found.setdefault(destination.pk, destination)
    return sorted(found.values(), key=lambda member: member.staff_label.casefold())


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
            "move_guides": _move_guides(guide),
            "picker": build_picker(tours, day),
            "chosen": chosen,
            "chosen_when": when_label(chosen.starts_at) if chosen else "",
            "form": form,
            "error": error,
            "move_url": reverse("tours:booking_reschedule", args=[booking.pk]),
        },
    )


def _request_for_its_guide(request: HttpRequest, pk: int) -> TourBooking:
    """The request a Confirm or Decline names, only for the tour's own guide (#1166).

    Felix's rule overrides the admins see everything one here: an admin sees another guide's
    request on Upcoming but never answers it for them (403).
    """
    booking = get_object_or_404(TourBooking.objects.select_related("guide"), pk=pk)
    if booking.guide_id is None or booking.guide_id != _viewer(request).pk:
        raise PermissionDenied("Only the tour's guide can confirm or decline it.")
    return booking


def _back_url(request: HttpRequest, fallback: str = "tours:upcoming") -> str:
    """The Tours page or Home (#1213) the action came from (htmx's ``HX-Current-URL``), else ``fallback``'s tab."""
    current = request.headers.get("HX-Current-URL", "")
    parts = urlsplit(current)
    same_site = url_has_allowed_host_and_scheme(
        current, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    if same_site and parts.path == reverse("hub_home"):
        return parts.path
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
    shows the confirm with the optional message to the visitor. The POST asks the visitor first
    (#1283, :func:`tours.booking.propose_tour_time`): the booking keeps its time until they accept,
    and the row shows it waiting. A refusal shows the modal again with why. The
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
        proposed = propose_tour_time(
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
        request,
        f"We asked {proposed.visitor_name} to move to {short_when(target[1])}. "
        "Their tour stays put until they accept, and you'll hear their answer.",
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
def tour_booking_confirm(request: HttpRequest, pk: int) -> HttpResponse:
    """Confirm a visitor's request from the Upcoming tab (#1166): it becomes a booked tour, and the
    visitor gets the booked email with the calendar invite."""
    booking = _request_for_its_guide(request, pk)
    try:
        confirmed = confirm_tour_request(booking, actor=cast("User", request.user))
    except TourLockedError as exc:
        messages.error(request, STAFF_LOCKED_MESSAGES[exc.reason])
        return _done(request)
    messages.success(
        request, f"{confirmed.visitor_name}'s tour on {short_when(confirmed.starts_at)} is confirmed. We emailed them."
    )
    return _done(request)


@tour_guide_access_required
@require_POST
def tour_booking_decline(request: HttpRequest, pk: int) -> HttpResponse:
    """Decline a visitor's request from the Upcoming tab's confirm (#1166), with an optional message;
    its seats are free again."""
    booking = _request_for_its_guide(request, pk)
    form = StaffTourMessageForm(request.POST)
    if not form.is_valid():
        messages.error(request, str(form.errors["message"][0]))
        return _done(request)
    try:
        declined = decline_tour_request(booking, actor=cast("User", request.user), message=form.cleaned_data["message"])
    except TourLockedError as exc:
        messages.error(request, STAFF_LOCKED_MESSAGES[exc.reason])
        return _done(request)
    messages.success(
        request, f"{declined.visitor_name}'s request for {short_when(declined.starts_at)} is declined. We emailed them."
    )
    return _done(request)


# --- Notes (#1274) ---------------------------------------------------------------------------------


def _notes_context(request: HttpRequest, booking: TourBooking, *, form: TourNoteForm | None = None) -> dict[str, Any]:
    """What the Notes modal draws: the booking's thread and the box, with the visitor switch for whoever may act."""
    may_email = _may_email(request, booking)
    return {
        "booking": booking,
        "thread_messages": list(booking.note_thread().select_related("author").prefetch_related("photos")),
        "note_form": form
        if form is not None
        else TourNoteForm(may_email=may_email, auto_id=f"id_tournote{booking.pk}_%s"),
        "may_email": may_email,
        "email_note": _email_note(request, booking),
        "notes_next": _safe_tours_next(request),
    }


def _email_note(request: HttpRequest, booking: TourBooking) -> str:
    """Why the Notes box offers no email to the visitor, as its note line says it ("" when it does)."""
    if not _may_act(request, booking):
        return "Only the tour's guide or an admin can email the visitor."
    if booking.status == TourBooking.Status.CANCELLED:
        return "This tour is cancelled, so notes stay here."
    if booking.is_shared_request:
        return "Notes can be emailed to the visitor once a guide confirms this request."
    return ""


def _may_email(request: HttpRequest, booking: TourBooking) -> bool:
    """Whether the viewer may email a note to this booking's visitor: they may act on it, and the row allows it."""
    return _may_act(request, booking) and booking.may_email_visitor


def _safe_tours_next(request: HttpRequest) -> str:
    """The Upcoming or Past table the Notes modal came from (``next``, filters kept), so Add Note lands back on it.

    Anything else, another site or another page, is Upcoming.
    """
    raw = request.POST.get("next") or request.GET.get("next") or ""
    tables = {reverse("tours:upcoming"), reverse("tours:past")}
    if url_has_allowed_host_and_scheme(raw, allowed_hosts={request.get_host()}) and urlsplit(raw).path in tables:
        return raw
    return reverse("tours:upcoming")


def _with_notes_open(url: str, booking: TourBooking) -> str:
    """``url`` with ``?notes=<pk>`` in place of any it had, so the page opens the Notes modal again."""
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "notes"]
    query.append(("notes", str(booking.pk)))
    return f"{parts.path}?{urlencode(query)}"


@tour_guide_access_required
def tour_booking_notes(request: HttpRequest, pk: int) -> HttpResponse:
    """GET: a booking's notes and the box to add one, for the Notes modal (any Tour Guide or admin).

    A row whose notes moved (a shared request another guide took, #1274) shows the surviving row's.

    A plain GET (a typed address, a history restore) lands on Upcoming with the modal open.
    """
    booking = get_object_or_404(TourBooking.objects.select_related("guide", "notes_moved_to__guide"), pk=pk).note_target
    if not wants_fragment(request):
        return redirect(_with_notes_open(reverse("tours:upcoming"), booking))
    return render(request, "tours/partials/_notes.html", _notes_context(request, booking))


@tour_guide_access_required
@require_POST
def tour_booking_note(request: HttpRequest, pk: int) -> HttpResponse:
    """POST: add a note to a booking; with ``email_visitor`` also email it to the visitor.

    Any Tour Guide or admin may add a note. A row whose notes moved to another row of its resolved
    No preference request posts there (``TourBooking.note_target``). Emailing the visitor is for whoever may act on the
    booking (its guide, or an admin): anyone else asking for it gets a 403, before anything is
    saved. Lands back on the page it came from with the Notes modal open and a flash.
    """
    get_object_or_404(TourBooking, pk=pk)
    with transaction.atomic():
        # The request's rows and then this row are locked before its note target is read, in the
        # order a Confirm or Decline takes them: one that moves the notes meanwhile finishes first,
        # and the note lands on the row they went to, never on the one they left (#1274).
        locked = lock_for_note(pk)
        booking = TourBooking.objects.select_related("guide").get(pk=locked.note_target.pk)
        return _post_note(request, booking)


def _post_note(request: HttpRequest, booking: TourBooking) -> HttpResponse:
    """Add the posted note to ``booking`` (already resolved to its note target, inside the lock)."""
    back = _with_notes_open(_safe_tours_next(request), booking)
    may_email = _may_email(request, booking)
    if request.POST.get("email_visitor") and not may_email:
        raise PermissionDenied(
            "Only the tour's guide or an admin can email its visitor, and not on a shared or cancelled request."
        )
    form = TourNoteForm(request.POST, request.FILES, may_email=may_email)
    if not form.is_valid():
        messages.error(request, f"Not added. {form.error_summary()}")
        return redirect(back)
    note = TourBookingNote.post(
        author=cast("User", request.user),
        body=form.cleaned_data["body"],
        photos=form.cleaned_data["photos"],
        booking=booking,
        sent_to_visitor=bool(form.cleaned_data.get("email_visitor")),
    )
    messages.success(request, "Note added." + note.told_sentence())
    return redirect(back)


# --- Calendar (#1274) -------------------------------------------------------------------------------


@tour_guide_access_required
def tour_calendar(request: HttpRequest) -> HttpResponse:
    """The Calendar tab: every guide's hours and tours on the shared calendar, and the days nobody covers."""
    week_offset, month_offset, event_page = calendar_nav_params(request)
    return render(
        request,
        "tours/calendar.html",
        {
            "tab": "calendar",
            "is_admin": _is_admin(request),
            "cal": coverage.calendar_context(
                viewer=_viewer(request),
                is_admin=_is_admin(request),
                week_offset=week_offset,
                month_offset=month_offset,
                event_page=event_page,
            ),
            "gaps": coverage.uncovered_days(),
            "horizon_days": FacilityTourSettings.load().booking_horizon_days,
            **_setup(request),
        },
    )


@tour_guide_access_required
def tour_calendar_events(request: HttpRequest) -> HttpResponse:
    """The Calendar tab's grid and list for its Week and Month navigation (htmx); a plain GET lands on the tab."""
    if not wants_fragment(request):
        return redirect("tours:calendar")
    week_offset, month_offset, event_page = calendar_nav_params(request)
    cal = coverage.calendar_context(
        viewer=_viewer(request),
        is_admin=_is_admin(request),
        week_offset=week_offset,
        month_offset=month_offset,
        event_page=event_page,
    )
    return render(request, "hub/partials/calendar_content.html", cal)


def _with_setup_finished(request: HttpRequest, booking: TourBooking, outcome: str, text: str) -> str:
    """The Attended toast, plus the badge line when this tour finishes the guide's Get Set Up (#1167).

    Only the guide's own mark celebrates; an admin marking someone else's tour finishes their
    setup quietly, and the guide sees the toast on their next visit.
    """
    if outcome != TourBooking.Status.ATTENDED or booking.guide is None or booking.guide.user_id != request.user.pk:
        return text
    state = SetupState.load(booking.guide, AdminCapability.Capability.TOUR_GUIDE)
    if state is None or not state.just_finished:
        return text
    return f"{text} {state.spec.celebration}"


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
        text = _with_setup_finished(request, booking, outcome, text)
    if request.headers.get("HX-Request") != "true":
        messages.add_message(request, messages.SUCCESS if tone == "success" else messages.ERROR, text)
        return redirect(_back_url(request, "tours:past"))
    booking.note_count = booking.note_thread().count()  # type: ignore[attr-defined]
    response = render(
        request,
        "tours/partials/_booking_row.html",
        {
            "booking": _with_row_rights(request, [booking])[0],
            "tab": PAST,
            "notes_next": _back_url(request, "tours:past"),
            "is_admin": _is_admin(request),
            "viewer_pk": _viewer(request).pk,
        },
    )
    trigger_toast(response, text, tone)
    return response
