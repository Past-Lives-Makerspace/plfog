"""Equipment directory views (equipment-reservations spec §6/§7 — PR 1 + PR 2).

The member-facing Equipment index and detail pages (with the schedule + Book a
Time flow), the admin-gated add form, and the manage panel (Details, Staff,
Hours & Limits, Reservations). All views are thin per CLAUDE.md: parse request →
permission guard → form/model/service call → toast, redirect, or render.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from hub.forms import (
    EquipmentForm,
    EquipmentHoursWindowFormSet,
    EquipmentManagerCancelForm,
    EquipmentReservationForm,
    EquipmentSettingsForm,
    EquipmentStaffAddForm,
)
from hub.toast import trigger_toast
from hub.views import _get_hub_context, _get_member
from membership import equipment as equipment_service
from membership.models import (
    Equipment,
    EquipmentError,
    EquipmentQuerySet,
    EquipmentReservation,
    EquipmentStaffMembership,
    Guild,
    Member,
)
from membership.permissions import can_create_equipment, can_manage_equipment


def equipment_feature_required(view_func: Any) -> Any:
    """404 every equipment view while the Site Settings toggle is off.

    A disabled feature is fully dark — member pages, booking POSTs, and manage
    surfaces alike; Site Settings is where it comes back. Mirrors the
    ``help_page_enabled`` gate's early-check mechanism, answering 404 instead of a
    redirect so crafted requests learn nothing.
    """
    from functools import wraps

    @wraps(view_func)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        from core.models import SiteConfiguration

        if not SiteConfiguration.load().equipment_page_enabled:
            raise Http404("The Equipment page is turned off.")
        return view_func(request, *args, **kwargs)

    return wrapper


def _equipment_queryset() -> EquipmentQuerySet:
    """The base queryset every equipment view reads — FKs prefetched, no per-row queries."""
    return Equipment.objects.select_related("guild", "space", "required_orientation", "required_orientation__guild")


def _require_can_manage(request: HttpRequest, equipment: Equipment) -> HttpResponse | None:
    """Return a 403 response if the user cannot manage ``equipment``, else None."""
    if not can_manage_equipment(request, equipment):
        return HttpResponse("Forbidden", status=403)
    return None


def _member_access_sets(member: Member | None) -> tuple[set[int], set[int]]:
    """The member's completed orientation-type pks and joined-guild pks, in two queries.

    The bulk input to :meth:`Equipment.access_state` so the index page never runs
    per-card access queries. Empty sets for an unlinked viewer — every card then
    reads "Membership inactive", which is the honest state.
    """
    if member is None:
        return set(), set()
    oriented = set(member.orientation_bookings.filter(is_completed=True).values_list("orientation_type_id", flat=True))
    guilds = set(member.guild_memberships.values_list("guild_id", flat=True))
    return oriented, guilds


def _duration_label(minutes: int) -> str:
    """A friendly duration label: 30 -> "30 minutes", 60 -> "1 hour", 90 -> "1.5 hours"."""
    if minutes < 60:
        return f"{minutes} minutes"
    hours = minutes / 60
    if hours == int(hours):
        count = int(hours)
        return f"{count} hour{'' if count == 1 else 's'}"
    return f"{hours:g} hours"


def _day_timeline(equipment: Equipment, selected_day: date) -> list[dict[str, Any]]:
    """The selected day's ordered free/busy segments for the timeline list.

    Each open window is split around the day's confirmed reservations; busy
    segments carry the reservation (reserver name + purpose are shown to every
    logged-in member, the locked privacy decision).
    """
    day_start = timezone.make_aware(datetime.combine(selected_day, time.min))
    busy_list = list(
        EquipmentReservation.objects.overlapping(equipment, day_start, day_start + timedelta(days=1))
        .select_related("member")
        .order_by("starts_at")
    )
    timeline: list[dict[str, Any]] = []
    for window_start, window_end in equipment.open_intervals_for_day(selected_day):
        cursor = window_start
        for reservation in busy_list:
            if reservation.ends_at <= cursor or reservation.starts_at >= window_end:
                continue
            if reservation.starts_at > cursor:
                timeline.append({"is_free": True, "starts_at": cursor, "ends_at": reservation.starts_at})
            timeline.append(
                {
                    "is_free": False,
                    "starts_at": max(reservation.starts_at, window_start),
                    "ends_at": min(reservation.ends_at, window_end),
                    "reservation": reservation,
                }
            )
            cursor = max(cursor, reservation.ends_at)
        if cursor < window_end:
            timeline.append({"is_free": True, "starts_at": cursor, "ends_at": window_end})
    if selected_day == timezone.localdate():
        timeline = _clip_free_segments_to_now(timeline)
    return timeline


def _clip_free_segments_to_now(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Today's already-elapsed open time is not "Open" — clip free segments to now.

    Matches the start select, which never offers past starts. Past busy segments
    keep their label; who had the tool is honest history.
    """
    now = timezone.now()
    clipped: list[dict[str, Any]] = []
    for segment in timeline:
        if segment["is_free"]:
            if segment["ends_at"] <= now:
                continue
            if segment["starts_at"] < now:
                segment = {**segment, "starts_at": now}
        clipped.append(segment)
    return clipped


def _schedule_context(
    equipment: Equipment,
    member: Member | None,
    *,
    week_offset: int = 0,
    selected_day: date | None = None,
    manages: bool = False,
) -> dict[str, Any]:
    """Everything the schedule partial renders: the week strip, the day timeline,
    the Book a Time selects, and the member's + everyone's reservation lists.

    ``week_offset`` pages the 7-day strip (0 = today first) within the booking
    horizon. ``selected_day`` defaults to the first bookable day on the strip.
    All wall-clock math is local (Portland) time.
    """
    today = timezone.localdate()
    horizon = today + timedelta(days=equipment.max_advance_days)
    max_offset = max((horizon - today).days // 7, 0)
    week_offset = max(0, min(week_offset, max_offset))
    strip_start = today + timedelta(days=7 * week_offset)
    active_weekdays = {rule.weekday for rule in equipment.hours_rules.active()}
    days: list[dict[str, Any]] = []
    for i in range(7):
        day = strip_start + timedelta(days=i)
        disabled = day < today or day > horizon or day.weekday() not in active_weekdays
        days.append({"date": day, "disabled": disabled})
    if selected_day is None or selected_day < today or selected_day > horizon:
        selected_day = next((entry["date"] for entry in days if not entry["disabled"]), None)
    for entry in days:
        entry["selected"] = entry["date"] == selected_day

    timeline: list[dict[str, Any]] = []
    starts: list[Any] = []
    durations_data: dict[str, list[dict[str, Any]]] = {}
    if selected_day is not None:
        timeline = _day_timeline(equipment, selected_day)
        starts = equipment.free_starts_for_day(selected_day)
        durations_data = {
            start.isoformat(): [
                {"v": minutes, "label": _duration_label(minutes)} for minutes in equipment.durations_for(start)
            ]
            for start in starts
        }

    blockers = equipment.booking_blockers(member)
    my_reservations: list[EquipmentReservation] = []
    if member is not None:
        now = timezone.now()
        my_reservations = list(
            equipment.reservations.filter(member=member, ends_at__gt=now)
            .exclude(status=EquipmentReservation.Status.CANCELLED, cancelled_by=member)
            .order_by("starts_at")
        )
    return {
        "equipment": equipment,
        "week_offset": week_offset,
        # String forms for template |add composition (the self-cancel hx-vals JSON).
        "week_offset_str": str(week_offset),
        "selected_day_str": selected_day.isoformat() if selected_day is not None else "",
        "has_prev_week": week_offset > 0,
        "has_next_week": week_offset < max_offset,
        "days": days,
        "selected_day": selected_day,
        "timeline": timeline,
        "starts": starts,
        "durations_data": durations_data,
        "can_book": not blockers and bool(starts),
        "blockers": blockers,
        "has_hours": bool(active_weekdays),
        "my_reservations": my_reservations,
        "upcoming_reservations": list(equipment.reservations.upcoming().select_related("member")[:20]),
        "manages": manages,
    }


@login_required
@equipment_feature_required
def hub_equipment_index(request: HttpRequest) -> HttpResponse:
    """The Equipment directory — card grid with guild/kind/search filters and access badges."""
    member = _get_member(request)
    base = _equipment_queryset().active()
    guild_filter = request.GET.get("guild", "")
    kind_filter = request.GET.get("kind", "")
    query = request.GET.get("q", "").strip()
    filtered = base
    if guild_filter == "standalone":
        filtered = filtered.standalone()
    elif guild_filter:
        filtered = filtered.filter(guild__slug=guild_filter)
    if kind_filter in Equipment.Kind.values:
        filtered = filtered.filter(kind=kind_filter)
    if query:
        filtered = filtered.filter(name__icontains=query)
    # The availability line reads hours + right-now reservations from these prefetches —
    # one queryset for the whole grid, no per-card queries.
    now = timezone.now()
    filtered = filtered.prefetch_related(
        "hours_rules",
        Prefetch(
            "reservations",
            queryset=EquipmentReservation.objects.confirmed().filter(starts_at__lte=now, ends_at__gt=now),
            to_attr="current_reservations",
        ),
    )
    oriented_ids, guild_ids = _member_access_sets(member)
    cards = [
        {
            "equipment": equipment,
            "access_state": equipment.access_state(member, oriented_type_ids=oriented_ids, member_guild_ids=guild_ids),
            "availability": equipment.availability_line(),
        }
        for equipment in filtered
    ]
    return render(
        request,
        "hub/equipment_index.html",
        {
            **_get_hub_context(request),
            "cards": cards,
            "filter_guilds": Guild.objects.filter(equipment__is_active=True).distinct().order_by("name"),
            "has_standalone": base.standalone().exists(),
            "has_any_equipment": base.exists(),
            "guild_filter": guild_filter,
            "kind_filter": kind_filter,
            "query": query,
            "is_filtered": bool(guild_filter or kind_filter or query),
            "can_create": can_create_equipment(request),
        },
    )


@login_required
@equipment_feature_required
def hub_equipment_add(request: HttpRequest) -> HttpResponse:
    """Admin-gated create form — full admins and EQUIPMENT capability holders only."""
    if not can_create_equipment(request):
        return HttpResponse("Forbidden", status=403)
    form = EquipmentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        equipment = form.save()
        messages.success(request, "Equipment added.")
        return redirect("hub_equipment_detail", slug=equipment.slug)
    return render(request, "hub/equipment_add.html", {**_get_hub_context(request), "form": form})


@login_required
@equipment_feature_required
def hub_equipment_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """The equipment mini-page — hero, requirements banner, About (schedule joins in PR 2)."""
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    manages = can_manage_equipment(request, equipment)
    if not equipment.is_active and not manages:
        raise Http404("This equipment has been retired.")
    member = _get_member(request)
    access_state = equipment.access_state(member)
    orientation_type = equipment.required_orientation
    orientation_booking = None
    orientation_url = ""
    if member is not None and orientation_type is not None and access_state == Equipment.AccessState.NEEDS_ORIENTATION:
        orientation_booking = member.active_orientation_for_type(orientation_type)
        orientation_url = (
            reverse("hub_guild_detail", args=[orientation_type.guild.slug])
            + f"?tab=orientations&type={orientation_type.pk}#guild-orientation"
        )
    return render(
        request,
        "hub/equipment_detail.html",
        {
            **_get_hub_context(request),
            **_schedule_context(equipment, member, manages=manages),
            "equipment": equipment,
            "access_state": access_state,
            "orientation_booking": orientation_booking,
            "orientation_url": orientation_url,
            "can_manage": manages,
        },
    )


def _parse_week_value(raw: str) -> int:
    """A week strip offset from a raw param, defaulting to 0; garbage is 0 (the strip clamps anyway)."""
    return int(raw) if raw.lstrip("-").isdigit() else 0


def _parse_week(request: HttpRequest) -> int:
    """The ?week= strip offset from the query string."""
    return _parse_week_value(request.GET.get("week", "0"))


def _parse_day(raw: str) -> date | None:
    """An ISO ?day= value, or None for absent/garbage (the context picks a default)."""
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _render_schedule(
    request: HttpRequest,
    equipment: Equipment,
    *,
    week_offset: int = 0,
    selected_day: date | None = None,
) -> HttpResponse:
    """Render the HTMX-swapped schedule partial for the current member."""
    member = _get_member(request)
    manages = can_manage_equipment(request, equipment)
    context = _schedule_context(equipment, member, week_offset=week_offset, selected_day=selected_day, manages=manages)
    return render(request, "hub/partials/equipment_schedule.html", context)


def _require_visible(request: HttpRequest, equipment: Equipment) -> None:
    """Raise Http404 when retired equipment is fetched by a non-manager.

    Mirrors the detail page's gate on the schedule/reserve endpoints, so a crafted
    request can neither read a retired tool's roster nor book it.
    """
    if not equipment.is_active and not can_manage_equipment(request, equipment):
        raise Http404("This equipment has been retired.")


@login_required
@equipment_feature_required
def hub_equipment_schedule(request: HttpRequest, slug: str) -> HttpResponse:
    """GET — the schedule partial (week strip + day timeline + booking form), HTMX-swapped."""
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    _require_visible(request, equipment)
    return _render_schedule(
        request,
        equipment,
        week_offset=_parse_week(request),
        selected_day=_parse_day(request.GET.get("day", "")),
    )


@login_required
@equipment_feature_required
@require_POST
def hub_equipment_reserve(request: HttpRequest, slug: str) -> HttpResponse:
    """POST — make an instant reservation; re-render the schedule partial with a toast.

    A lost race (or any engine guard) comes back as the friendly error toast plus a
    refreshed start list — the member never sees a dead page, and the strip stays on
    the week they were looking at.
    """
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    _require_visible(request, equipment)
    member = _get_member(request)
    if member is None:
        return HttpResponse("Forbidden", status=403)
    week_offset = _parse_week_value(request.POST.get("week", "0"))
    selected_day = _parse_day(request.POST.get("day", ""))
    form = EquipmentReservationForm(request.POST)
    if not form.is_valid():
        response = _render_schedule(request, equipment, week_offset=week_offset, selected_day=selected_day)
        trigger_toast(response, "Please pick one of the listed times.", "error")
        return response
    try:
        reservation = equipment_service.reserve(
            equipment,
            member,
            form.cleaned_data["starts_at"],
            form.cleaned_data["duration_minutes"],
            purpose=form.cleaned_data["purpose"],
        )
    except EquipmentError as exc:
        response = _render_schedule(request, equipment, week_offset=week_offset, selected_day=selected_day)
        trigger_toast(response, str(exc), "error")
        return response
    local_start = timezone.localtime(reservation.starts_at)
    response = _render_schedule(request, equipment, week_offset=week_offset, selected_day=local_start.date())
    trigger_toast(response, f"Reserved. See you {local_start:%A}.", "success")
    return response


@login_required
@equipment_feature_required
@require_POST
def hub_equipment_reservation_cancel(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """POST — cancel a reservation: the member's own (no reason), or a manager's (reason required).

    The manage-tab variant is marked by its ``reason`` field: a manager posting it
    takes the manager path even for THEIR OWN row (reason honored, in-progress
    allowed, redirect back to the tab). Deliberately no retired-equipment 404 here —
    a member must always be able to back out of a retired tool's reservation.
    """
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    reservation = get_object_or_404(EquipmentReservation, pk=pk, equipment=equipment)
    member = _get_member(request)
    if member is None:
        return HttpResponse("Forbidden", status=403)
    manager_route = "reason" in request.POST and can_manage_equipment(request, equipment)
    if reservation.member_id == member.pk and not manager_route:
        week_offset = _parse_week_value(request.POST.get("week", "0"))
        selected_day = _parse_day(request.POST.get("day", ""))
        try:
            reservation.cancel(member)
        except EquipmentError as exc:
            response = _render_schedule(request, equipment, week_offset=week_offset, selected_day=selected_day)
            trigger_toast(response, str(exc), "error")
            return response
        response = _render_schedule(request, equipment, week_offset=week_offset, selected_day=selected_day)
        trigger_toast(response, "Reservation cancelled.", "success")
        return response
    if not can_manage_equipment(request, equipment):
        return HttpResponse("Forbidden", status=403)
    form = EquipmentManagerCancelForm(request.POST)
    manage_tab = f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=reservations"
    if not form.is_valid():
        messages.error(request, "Please tell the member why.")
        return redirect(manage_tab)
    try:
        reservation.cancel(member, reason=form.cleaned_data["reason"], as_manager=True)
    except EquipmentError as exc:
        messages.error(request, str(exc))
        return redirect(manage_tab)
    messages.success(request, "Reservation cancelled. The member has been told.")
    return redirect(manage_tab)


@login_required
@equipment_feature_required
@require_POST
def hub_equipment_hours_save(request: HttpRequest, slug: str) -> HttpResponse:
    """POST — save the whole Hours & Limits tab: the hours formset plus closure + limits.

    One Save for the tab (FRONTEND rule 21); a per-row Delete flips its hidden DELETE
    and resubmits this same form, so closure and limit edits are never lost.
    """
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    hours_formset = EquipmentHoursWindowFormSet(request.POST, initial=equipment.hours_windows(), prefix="hours")
    settings_form = EquipmentSettingsForm(request.POST, instance=equipment)
    if hours_formset.is_valid() and settings_form.is_valid():
        equipment.apply_hours_windows(
            [form.cleaned_data for form in hours_formset if form.cleaned_data and not form.cleaned_data.get("DELETE")]
        )
        settings_form.save()
        messages.success(request, "Saved.")
        return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=hours")
    return _render_manage(
        request,
        equipment,
        hours_formset=hours_formset,
        settings_form=settings_form,
        active_tab="hours",
    )


def _render_manage(
    request: HttpRequest,
    equipment: Equipment,
    *,
    form: EquipmentForm | None = None,
    staff_add_form: EquipmentStaffAddForm | None = None,
    hours_formset: Any = None,
    settings_form: EquipmentSettingsForm | None = None,
    active_tab: str = "details",
) -> HttpResponse:
    """Render the manage panel with the given (possibly error-bearing) forms."""
    return render(
        request,
        "hub/equipment_manage.html",
        {
            **_get_hub_context(request),
            "equipment": equipment,
            "form": form if form is not None else EquipmentForm(instance=equipment),
            "staff_memberships": list(equipment.staff_memberships.select_related("member", "granted_by")),
            "staff_add_form": staff_add_form
            if staff_add_form is not None
            else EquipmentStaffAddForm(equipment=equipment),
            "hours_formset": hours_formset
            if hours_formset is not None
            else EquipmentHoursWindowFormSet(initial=equipment.hours_windows(), prefix="hours"),
            "settings_form": settings_form if settings_form is not None else EquipmentSettingsForm(instance=equipment),
            "manager_cancel_form": EquipmentManagerCancelForm(),
            # The hub's standard Paginator + table_pagination partial, capped at 25 rows.
            "manage_reservations": Paginator(equipment.reservations.upcoming().select_related("member"), 25).get_page(
                request.GET.get("page", 1)
            ),
            "active_tab": active_tab,
        },
    )


@login_required
@equipment_feature_required
def hub_equipment_manage(request: HttpRequest, slug: str) -> HttpResponse:
    """The manage panel — Details, Staff, Hours & Limits, and Reservations tabs."""
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    active_tab = request.GET.get("tab", "details")
    if active_tab not in {"details", "staff", "hours", "reservations"}:
        active_tab = "details"
    return _render_manage(request, equipment, active_tab=active_tab)


@login_required
@equipment_feature_required
@require_POST
def hub_equipment_details_save(request: HttpRequest, slug: str) -> HttpResponse:
    """POST-only — save the manage panel's Details tab (the same form as the add page)."""
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    form = EquipmentForm(request.POST, request.FILES, instance=equipment)
    if form.is_valid():
        form.save()
        messages.success(request, "Saved.")
        return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=details")
    return _render_manage(request, equipment, form=form, active_tab="details")


@login_required
@equipment_feature_required
@require_POST
def hub_equipment_photo_delete(request: HttpRequest, slug: str) -> HttpResponse:
    """POST-only — clear the equipment photo (the ``image_field`` component's delete endpoint)."""
    equipment = get_object_or_404(Equipment, slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    if equipment.photo:
        equipment.photo.delete(save=True)
        messages.success(request, "Photo removed.")
    return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=details")


@login_required
@equipment_feature_required
@require_POST
def hub_equipment_staff_add(request: HttpRequest, slug: str) -> HttpResponse:
    """POST-only — grant a member a manager role on this equipment."""
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    form = EquipmentStaffAddForm(request.POST, equipment=equipment)
    if form.is_valid():
        EquipmentStaffMembership.objects.create(
            equipment=equipment,
            member=form.cleaned_data["member"],
            granted_by=_get_member(request),
        )
        messages.success(request, "Manager added.")
        return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=staff")
    return _render_manage(request, equipment, staff_add_form=form, active_tab="staff")


@login_required
@equipment_feature_required
@require_POST
def hub_equipment_staff_remove(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """POST-only — remove a member's manager role from this equipment."""
    equipment = get_object_or_404(Equipment, slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    staff = get_object_or_404(EquipmentStaffMembership, pk=pk, equipment=equipment)
    member_name = staff.member.display_name
    staff.delete()
    messages.success(request, f"{member_name} no longer manages the {equipment.name}.")
    return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=staff")
