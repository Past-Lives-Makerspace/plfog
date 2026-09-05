"""Equipment directory views (equipment-reservations spec §6/§7 — PR 1 + PR 2).

The member-facing Equipment index and detail pages (with the schedule + Book a
Time flow), the admin-gated add form, and the manage panel (Details, Staff,
Hours & Limits, Reservations). All views are thin per CLAUDE.md: parse request →
permission guard → form/model/service call → toast, redirect, or render.
"""

from __future__ import annotations

from collections.abc import Sequence
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
    EquipmentOrientationAvailabilityFormSet,
    EquipmentOrientationSlotForm,
    EquipmentOrientationTypeFormSet,
    EquipmentReservationForm,
    EquipmentSettingsForm,
    EquipmentStaffAddForm,
)
from hub.toast import trigger_toast
from hub.views import _apply_hours_formset, _get_hub_context, _get_member, _hours_save_message, _personal_hours_prefix
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
    return Equipment.objects.select_related(
        "guild", "space", "required_orientation", "required_orientation__guild", "required_orientation__equipment"
    ).prefetch_related("owned_orientation_types")


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


def _short_name(display_name: str) -> str:
    """ "Sam Reyes" -> "Sam R.": the reserver-name norm, shortened for a busy timeline row."""
    parts = display_name.split()
    if len(parts) < 2:
        return display_name
    return f"{parts[0]} {parts[-1][0]}."


def _orientation_busy_items(equipment: Equipment, day_start: datetime, day_end: datetime) -> list[dict[str, Any]]:
    """The day's seat-holding orientation slots as busy timeline items labeled "Orientation · Sam R."."""
    from membership.models import OrientationBooking, OrientationSlot

    seat_holding = (
        OrientationBooking.objects.filter(
            status__in=[
                OrientationBooking.Status.PENDING_PAYMENT,
                OrientationBooking.Status.REQUESTED,
                OrientationBooking.Status.CONFIRMED,
            ]
        )
        .select_related("member")
        .order_by("pk")  # booking order, so the label reads the same on every render
    )
    slots = (
        OrientationSlot.objects.holding_seats_on(equipment, day_start, day_end)
        .prefetch_related(Prefetch("bookings", queryset=seat_holding, to_attr="seat_holders"))
        .order_by("starts_at")
    )
    items: list[dict[str, Any]] = []
    for slot in slots:
        names = ", ".join(_short_name(booking.member.display_name) for booking in slot.seat_holders)
        items.append(
            {
                "kind": "orientation",
                "starts_at": slot.starts_at,
                "ends_at": slot.ends_at,
                "label": f"Orientation · {names}",
                "reservation": None,
            }
        )
    return items


def _day_timeline(equipment: Equipment, selected_day: date) -> list[dict[str, Any]]:
    """The selected day's ordered free/busy segments for the timeline list.

    Each open window is split around the day's busy items: confirmed reservations
    (reserver name + purpose are shown to every logged-in member, the locked
    privacy decision) and booked orientation slots ("Orientation · Sam R.", the
    same visibility norm). Busy segments carry ``kind`` so the template can tell
    them apart.
    """
    day_start = timezone.make_aware(datetime.combine(selected_day, time.min))
    day_end = day_start + timedelta(days=1)
    busy_list: list[dict[str, Any]] = [
        {
            "kind": "reservation",
            "starts_at": reservation.starts_at,
            "ends_at": reservation.ends_at,
            "label": "",
            "reservation": reservation,
        }
        for reservation in EquipmentReservation.objects.overlapping(equipment, day_start, day_end).select_related(
            "member"
        )
    ]
    busy_list.extend(_orientation_busy_items(equipment, day_start, day_end))
    busy_list.sort(key=lambda item: item["starts_at"])
    timeline: list[dict[str, Any]] = []
    for window_start, window_end in equipment.open_intervals_for_day(selected_day):
        cursor = window_start
        for item in busy_list:
            if item["ends_at"] <= cursor or item["starts_at"] >= window_end:
                continue
            if item["starts_at"] > cursor:
                timeline.append({"is_free": True, "starts_at": cursor, "ends_at": item["starts_at"]})
            # Clamp to the cursor and the window: a legacy overlap (a reservation and a
            # booked orientation from before the guards) renders as consecutive segments,
            # and one that straddles closing time draws nothing rather than an inverted row.
            segment_start = max(item["starts_at"], cursor)
            segment_end = min(item["ends_at"], window_end)
            cursor = max(cursor, item["ends_at"])
            if segment_start >= segment_end:
                continue
            timeline.append(
                {
                    "is_free": False,
                    "kind": item["kind"],
                    "starts_at": segment_start,
                    "ends_at": segment_end,
                    "reservation": item["reservation"],
                    "label": item["label"],
                }
            )
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
        # The timeline's legend line renders only where an orientation could ever show.
        "has_orientations": equipment.owned_orientation_types.active().exists(),
        "my_reservations": my_reservations,
        "upcoming_reservations": list(equipment.reservations.upcoming().select_related("member")[:20]),
        "manages": manages,
    }


def _attach_running_orientations(equipment_list: Sequence[Equipment], *, now: datetime) -> None:
    """Give every card its ``current_orientation_slots`` (booked orientations running now) in one query."""
    from membership.models import OrientationSlot

    running = (
        OrientationSlot.objects.holding_seats()
        .filter(
            orientation_type__equipment__in=[equipment.pk for equipment in equipment_list],
            starts_at__lt=now,
            ends_at__gt=now,
        )
        .select_related("orientation_type")
    )
    by_equipment: dict[int, list[Any]] = {}
    for slot in running:
        by_equipment.setdefault(slot.orientation_type.equipment_id, []).append(slot)
    for equipment in equipment_list:
        equipment.current_orientation_slots = by_equipment.get(equipment.pk, [])


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
    equipment_list = list(filtered)
    _attach_running_orientations(equipment_list, now=now)
    oriented_ids, guild_ids = _member_access_sets(member)
    cards = [
        {
            "equipment": equipment,
            "access_state": equipment.access_state(member, oriented_type_ids=oriented_ids, member_guild_ids=guild_ids),
            "availability": equipment.availability_line(),
        }
        for equipment in equipment_list
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


def _equipment_orientation_sections(equipment: Equipment, member: Member | None) -> list[dict[str, Any]]:
    """The equipment page's renderable orientation sections (shared builder underneath).

    Renderable = the equipment's active owned types ∪ any inactive type the member
    still holds a live booking or checkout hold on — retiring a type mid-flow must
    never make a member's Cancel / Resume payment controls vanish (they render the
    state block only; ``bookable()`` already keeps inactive types slot-free).
    """
    from hub.views import _orientation_sections
    from membership.models import OrientationBooking, OrientationSlot

    types = list(equipment.owned_orientation_types.active())
    if member is not None:
        live_statuses = [
            OrientationBooking.Status.REQUESTED,
            OrientationBooking.Status.CONFIRMED,
            OrientationBooking.Status.PENDING_PAYMENT,
        ]
        pinned = member.orientation_bookings.filter(
            orientation_type__equipment=equipment, status__in=live_statuses
        ).select_related("orientation_type")
        known = {t.pk for t in types}
        for booking in pinned:
            if booking.orientation_type_id not in known:
                types.append(booking.orientation_type)
                known.add(booking.orientation_type_id)
    if not types:
        return []
    slots = (
        OrientationSlot.objects.bookable()
        .filter(orientation_type__in=types)
        .select_related("orientation_type", "orienter")
        .order_by("starts_at")
    )
    slots_by_type: dict[int, list[Any]] = {}
    for slot in slots:
        # "with Dana" on a manager's personal slot; empty for a shared slot.
        slot.with_display = slot.with_label
        slots_by_type.setdefault(slot.orientation_type_id, []).append(slot)
    # No cap: the guild list's five per page pager bounds the view.
    return _orientation_sections(types, member, slots_by_type, slot_cap=None)


@login_required
@equipment_feature_required
def hub_equipment_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """The equipment mini-page — hero, requirements banner, Orientation section, schedule, About."""
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    manages = can_manage_equipment(request, equipment)
    if not equipment.is_active and not manages:
        raise Http404("This equipment has been retired.")
    member = _get_member(request)
    access_state = equipment.access_state(member)
    orientation_type = equipment.required_orientation
    orientation_booking = None
    orientation_url = ""
    required_orientation_paused = False
    if member is not None and orientation_type is not None and access_state == Equipment.AccessState.NEEDS_ORIENTATION:
        orientation_booking = member.active_orientation_for_type(orientation_type)
        # Owner-aware: an equipment-owned required type anchors down THIS page; a
        # guild-owned one keeps the guild deep link, byte-identical.
        orientation_url = orientation_type.orientation_anchor_path()
        # A paused gate (inactive type, retired owner, or closed guild settings) with
        # no live booking must never render a dead "Book the Orientation" link.
        required_orientation_paused = orientation_booking is None and not orientation_type.is_accepting
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
            "required_orientation_paused": required_orientation_paused,
            "required_orientation_is_equipment_owned": (
                orientation_type.is_equipment_owned if orientation_type is not None else False
            ),
            "orientation_sections": _equipment_orientation_sections(equipment, member),
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
    from membership import orientations

    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    # Read BEFORE the settings form binds: its validation writes the posted value onto
    # this same instance, so a later read could never see the flip.
    was_closed = equipment.is_closed
    hours_formset = EquipmentHoursWindowFormSet(request.POST, initial=equipment.hours_windows(), prefix="hours")
    settings_form = EquipmentSettingsForm(request.POST, instance=equipment)
    if hours_formset.is_valid() and settings_form.is_valid():
        equipment.apply_hours_windows(
            [form.cleaned_data for form in hours_formset if form.cleaned_data and not form.cleaned_data.get("DELETE")]
        )
        settings_form.save()
        if was_closed and not equipment.is_closed:
            # A reopened tool must not sit orientation-empty until the nightly job.
            orientations.generate_slots(equipment=equipment)
        messages.success(request, "Saved.")
        return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=hours")
    return _render_manage(
        request,
        equipment,
        hours_formset=hours_formset,
        settings_form=settings_form,
        active_tab="hours",
    )


def _orientation_tab_context(request: HttpRequest, equipment: Equipment) -> dict[str, Any]:
    """The Orientation tab's lists: pending requests, the Orientation Schedule overview, shared rows, slots.

    The guild Orientations tab's shape, per manager: one overview group per
    ``manager_members()`` entry with their personal rules, a Former Managers group
    for orphan rules, the shared (orienter-less) rules, and the flat upcoming slot
    list with attendee sub-rows. ``can_edit_others_hours`` gates the whole-schedule
    view and the Runs with picker; a plain manager sees only their own group.
    """
    from membership.models import OrientationAvailability, OrientationBooking, OrientationSlot
    from membership.permissions import can_edit_equipment_orienter_hours

    viewer = _get_member(request)
    can_edit_others_hours = can_edit_equipment_orienter_hours(request, equipment, None)
    managers = equipment.manager_members()
    manager_ids = {member.pk for member in managers}
    rules_by_orienter: dict[int, list[Any]] = {}
    orphan_orienters: dict[int, Any] = {}
    personal_rules = (
        OrientationAvailability.objects.for_equipment(equipment)
        .exclude(orienter=None)
        .select_related("orienter", "orientation_type")
    )
    for rule in personal_rules:
        orienter_id = rule.orienter_id
        assert orienter_id is not None  # guaranteed by the exclude(orienter=None) filter
        rules_by_orienter.setdefault(orienter_id, []).append(rule)
        if orienter_id not in manager_ids:
            orphan_orienters[orienter_id] = rule.orienter
    orienter_overview = [(member, rules_by_orienter.get(member.pk, [])) for member in managers]
    former_managers_overview = sorted(
        ((member, rules_by_orienter[pk]) for pk, member in orphan_orienters.items()),
        key=lambda pair: pair[0].display_name.lower(),
    )
    shared_rules = list(
        OrientationAvailability.objects.for_equipment(equipment).guild_level().select_related("orientation_type")
    )
    pending_requests = list(
        OrientationBooking.objects.filter(
            orientation_type__equipment=equipment, status=OrientationBooking.Status.REQUESTED
        )
        .select_related("member", "slot", "orientation_type")
        .order_by("slot__starts_at")
    )
    upcoming_slots = list(
        OrientationSlot.objects.filter(orientation_type__equipment=equipment)
        .upcoming()
        .with_active_booking_count()
        .with_pending_hold_count()
        .select_related("orientation_type", "orienter")
        .order_by("starts_at")
    )
    attendees_by_slot: dict[int, list[Any]] = {}
    attendee_rows = (
        OrientationBooking.objects.filter(
            slot__in=[slot.pk for slot in upcoming_slots],
            status__in=[OrientationBooking.Status.REQUESTED, OrientationBooking.Status.CONFIRMED],
        )
        .select_related("member")
        .order_by("requested_at")
    )
    for booking in attendee_rows:
        attendees_by_slot.setdefault(booking.slot_id, []).append(booking)
    # A manager may post a slot over an existing reservation (they might mean to
    # bump it); such a slot renders muted with "Blocked by ..." and never reaches
    # members (bookable() hides it) until the reservation is cancelled.
    reservations = (
        list(equipment.reservations.upcoming().select_related("member").order_by("starts_at")) if upcoming_slots else []
    )
    for slot in upcoming_slots:
        slot.attendee_bookings = attendees_by_slot.get(slot.pk, [])
        slot.blocking_reservation = next(
            (
                reservation
                for reservation in reservations
                if reservation.starts_at < slot.ends_at and reservation.ends_at > slot.starts_at
            ),
            None,
        )
    return {
        "orientation_pending_requests": pending_requests,
        "orientation_upcoming_slots": upcoming_slots,
        "orienter_overview": orienter_overview,
        "former_managers_overview": former_managers_overview,
        "shared_rules": shared_rules,
        "can_edit_others_hours": can_edit_others_hours,
        "show_my_hours_card": viewer is not None and viewer.pk in manager_ids,
        "viewer_member_pk": viewer.pk if viewer is not None else None,
        "slot_form_locked": not can_edit_others_hours,
    }


def _render_manage(
    request: HttpRequest,
    equipment: Equipment,
    *,
    form: EquipmentForm | None = None,
    staff_add_form: EquipmentStaffAddForm | None = None,
    hours_formset: Any = None,
    settings_form: EquipmentSettingsForm | None = None,
    orientation_types_formset: Any = None,
    slot_add_form: EquipmentOrientationSlotForm | None = None,
    active_tab: str = "details",
) -> HttpResponse:
    """Render the manage panel with the given (possibly error-bearing) forms."""
    orientation_ctx = _orientation_tab_context(request, equipment)
    return render(
        request,
        "hub/equipment_manage.html",
        {
            **_get_hub_context(request),
            **orientation_ctx,
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
            "orientation_types_formset": orientation_types_formset
            if orientation_types_formset is not None
            else EquipmentOrientationTypeFormSet(instance=equipment, prefix="otypes"),
            "slot_add_form": slot_add_form
            if slot_add_form is not None
            else EquipmentOrientationSlotForm(
                equipment=equipment,
                acting_member=_get_member(request),
                lock_to_acting=orientation_ctx["slot_form_locked"],
            ),
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
    if active_tab not in {"details", "staff", "hours", "reservations", "orientation"}:
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
    removed_member = staff.member
    member_name = removed_member.display_name
    staff.delete()
    message = f"{member_name} no longer manages the {equipment.name}."
    # Retire their personal hours ONLY when they no longer manage the tool at all — they
    # may still be owning-guild staff or an admin, and those hours must keep generating.
    if not removed_member.can_manage_equipment(equipment):
        from membership import orientations

        _removed, booked_remaining = orientations.retire_equipment_orienter(equipment, removed_member)
        if booked_remaining:
            message += (
                f" They still have {booked_remaining} upcoming booked "
                f"orientation{'' if booked_remaining == 1 else 's'}. Cancel them from the "
                "Upcoming Slots card on the Orientation tab if they won't be run."
            )
    messages.success(request, message)
    return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=staff")


@login_required
@equipment_feature_required
@require_POST
def hub_equipment_orientation_types_save(request: HttpRequest, slug: str) -> HttpResponse:
    """POST — save the Orientation Types formset (create/edit/retire/delete).

    Mirrors the guild editor's types save minus slot regeneration (equipment has no
    recurring rules to materialize). The shared base formset supplies both delete
    guards: booking history, and a type some equipment's requirement points at.
    """
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    formset = EquipmentOrientationTypeFormSet(request.POST, instance=equipment, prefix="otypes")
    if formset.is_valid():
        formset.save()
        messages.success(request, "Saved.")
        return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation")
    return _render_manage(request, equipment, orientation_types_formset=formset, active_tab="orientation")


def _hours_scope_target(request: HttpRequest, raw: str) -> Member | None:
    """Resolve an ``orienter`` value to its Member, or None for the shared scope (garbage is a 404)."""
    if not raw:
        return None
    if not raw.isdigit():
        raise Http404("Unknown orienter scope.")
    return get_object_or_404(Member, pk=int(raw))


def _hours_scope_queryset(equipment: Equipment, target: Member | None) -> Any:
    """The rules one Edit Hours modal edits: a manager's personal rows, or the shared rows."""
    from membership.models import OrientationAvailability

    rules = OrientationAvailability.objects.for_equipment(equipment)
    return rules.for_orienter(target) if target is not None else rules.guild_level()


@login_required
@equipment_feature_required
def hub_equipment_orientation_hours_form(request: HttpRequest, slug: str) -> HttpResponse:
    """Return the Edit Hours modal's formset partial for one manager, or the shared rows (HTMX GET).

    ``?orienter=<pk>`` scopes to that manager; empty scopes to the shared (orienter-less)
    rows. Gated by ``can_edit_equipment_orienter_hours`` (403 otherwise), the same gate the
    save uses, so a former manager's leftover rows are editable by the "others" tier.
    """
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    from membership.permissions import can_edit_equipment_orienter_hours

    target = _hours_scope_target(request, request.GET.get("orienter", ""))
    if not can_edit_equipment_orienter_hours(request, equipment, target):
        return HttpResponse("Forbidden", status=403)
    formset = EquipmentOrientationAvailabilityFormSet(
        prefix="modal_rules",
        queryset=_hours_scope_queryset(equipment, target),
        form_kwargs={"equipment": equipment},
    )
    return render(
        request,
        "hub/partials/_orienter_hours_modal_form.html",
        {
            "target": target,
            "formset": formset,
            "hours_save_url": reverse("hub_equipment_orientation_hours_save", args=[equipment.slug]),
        },
    )


_EQUIPMENT_SHARED_FAREWELL = (
    "Shared hours deleted. From now on recurring hours are personal. "
    "Use an Any manager one time slot for shared coverage."
)


@login_required
@equipment_feature_required
@require_POST
def hub_equipment_orientation_hours_save(request: HttpRequest, slug: str) -> HttpResponse:
    """Save one scope of recurring orientation hours from the Edit Hours modal (HTMX POST).

    The posted ``orienter_scope`` selects the target (a manager's rows, or empty for the
    shared rows); ``formset_prefix`` must be ``modal_rules`` on an HTMX request (404
    otherwise, like the guild modal). Gate is ``can_edit_equipment_orienter_hours``.
    Deleted rows retire via ``retire_rule``, new rows are stamped with the scope's
    manager, and saved hours materialize slots immediately. A valid save answers 204 +
    ``HX-Redirect`` to the Orientation tab; an invalid one re-renders the bound partial
    inside the modal.
    """
    from membership import orientations
    from membership.permissions import can_edit_equipment_orienter_hours

    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    target = _hours_scope_target(request, request.POST.get("orienter_scope", ""))
    if not can_edit_equipment_orienter_hours(request, equipment, target):
        return HttpResponse("Forbidden", status=403)
    is_htmx = bool(request.headers.get("HX-Request"))
    prefix = _personal_hours_prefix(request, is_htmx=is_htmx)
    formset = EquipmentOrientationAvailabilityFormSet(
        request.POST,
        prefix=prefix,
        queryset=_hours_scope_queryset(equipment, target),
        form_kwargs={"equipment": equipment},
    )
    if formset.is_valid():
        deleted_rules, removed, kept = _apply_hours_formset(formset, target=target)
        orientations.generate_slots(equipment=equipment)
        shared_emptied = target is None and not _hours_scope_queryset(equipment, None).exists()
        messages.success(
            request,
            _hours_save_message(
                deleted_rules=deleted_rules,
                removed=removed,
                kept=kept,
                shared_farewell=_EQUIPMENT_SHARED_FAREWELL if shared_emptied else None,
            ),
        )
        response = HttpResponse(status=204)
        response["HX-Redirect"] = f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation"
        return response
    return render(
        request,
        "hub/partials/_orienter_hours_modal_form.html",
        {
            "target": target,
            "formset": formset,
            "hours_save_url": reverse("hub_equipment_orientation_hours_save", args=[equipment.slug]),
        },
    )


@login_required
@equipment_feature_required
@require_POST
def hub_equipment_orientation_slot_add(request: HttpRequest, slug: str) -> HttpResponse:
    """POST — add a one time MANUAL orientation slot (guild None; Runs with a manager or any manager)."""
    from membership.models import OrientationSlot
    from membership.permissions import can_edit_equipment_orienter_hours

    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    form = EquipmentOrientationSlotForm(
        request.POST,
        equipment=equipment,
        acting_member=_get_member(request),
        lock_to_acting=not can_edit_equipment_orienter_hours(request, equipment, None),
    )
    if form.is_valid():
        slot = form.save(commit=False)
        slot.source = OrientationSlot.Source.MANUAL
        slot.save()
        messages.success(request, "Time added.")
        return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation")
    # Bound re-render: the reveal form comes back OPEN with its errors visible.
    return _render_manage(request, equipment, slot_add_form=form, active_tab="orientation")


@login_required
@equipment_feature_required
@require_POST
def hub_equipment_orientation_slot_cancel(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """POST — cancel an orientation slot: full per-booking cancel fan-out + hold release."""
    from membership import orientations
    from membership.models import OrientationSlot

    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    slot = get_object_or_404(OrientationSlot, pk=pk, orientation_type__equipment=equipment)
    orientations.cancel_slot(slot, reason=request.POST.get("reason", ""))
    messages.success(request, "Orientation time cancelled. Everyone booked on it has been notified.")
    return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation")
