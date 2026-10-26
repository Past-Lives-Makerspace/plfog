"""The Tours page (#947): the My Hours tab, and the Settings tab for admins.

The page is opened from the Tours card on the Tools page (``hub_admin_tools``), never the sidebar.
Everything here sits behind :func:`hub.view_as.tour_guide_access_required` (Tour Guides and
admins) or :func:`hub.view_as.fog_admin_required` (the Settings tab). Which guide's hours a request
may touch is decided once, in :func:`_target_guide`: a guide their own, an admin anyone who gives
tours, so a guide who names another guide in a POST gets a 403 before any form is read.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from hub.view_as import ROLE_ADMIN, fog_admin_required, tour_guide_access_required
from membership.models import Member
from tours.forms import FacilityTourSettingsForm, TourDayOffForm, TourHoursFormSet
from tours.models import FacilityTourSettings, TourDayOff, guide_filter

HOURS_PREFIX = "hours"
DAY_OFF_PREFIX = "day_off"
#: Members who already have tour hours, whatever their permission says now: an admin can still open them.
_HAS_HOURS = Q(tour_hours__isnull=False)


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
    return render(
        request,
        "tours/hours.html",
        {
            "target": target,
            "is_self": target.pk == viewer.pk,
            "is_admin": is_admin,
            "picker_guides": _picker_guides(viewer) if is_admin else [],
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
    """The Tours page's Settings tab (admins only): horizon, notice, cutoff, defaults and the bookings switch."""
    instance = FacilityTourSettings.load()
    form = FacilityTourSettingsForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Tour Settings saved.")
        return redirect("tours:settings")
    return render(request, "tours/settings.html", {"form": form})
