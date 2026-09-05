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
    EquipmentOrientationHoursWindowFormSet,
    EquipmentOrientationSlotForm,
    EquipmentOrientationTypeFormSet,
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
        .with_seat_holding_count()
        .select_related("orientation_type")
        .order_by("starts_at")
    )
    slots_by_type: dict[int, list[Any]] = {}
    for slot in slots:
        slot.seats_open = max(slot.seats - slot.seat_holding_count, 0)
        slots_by_type.setdefault(slot.orientation_type_id, []).append(slot)
    # slot_cap=None: the day picker needs the full 8 week carved set, not a flat 30.
    sections = _orientation_sections(types, member, slots_by_type, slot_cap=None)
    for section in sections:
        section["days"] = _slot_days(section["slots"])
        open_days = [day for day in section["days"] if day["open_count"]]
        # Default chip = the first day with an open slot, else the first day (its rows
        # all read Full and the all-full line shows), so the row never renders empty.
        section["default_day"] = (open_days or section["days"] or [{"iso": ""}])[0]["iso"]
        section["all_full"] = bool(section["days"]) and not open_days
    return sections


def _slot_days(slots: list[Any]) -> list[dict[str, Any]]:
    """Group ``starts_at``-ordered picker slots (with ``seats_open``) by local date for the day chips."""
    days: list[dict[str, Any]] = []
    for slot in slots:
        local_day = timezone.localtime(slot.starts_at).date()
        if not days or days[-1]["date"] != local_day:
            days.append({"date": local_day, "iso": local_day.isoformat(), "open_count": 0, "slots": []})
        days[-1]["slots"].append(slot)
        if slot.seats_open:
            days[-1]["open_count"] += 1
    return days


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


def _orientation_tab_context(equipment: Equipment) -> dict[str, Any]:
    """The Orientation tab's read-only lists: pending requests + upcoming slots with attendees."""
    from membership.models import OrientationBooking, OrientationSlot

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
        .select_related("orientation_type")
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
    for slot in upcoming_slots:
        slot.attendee_bookings = attendees_by_slot.get(slot.pk, [])
    slot_days = _upcoming_slot_days(upcoming_slots)
    return {
        "orientation_pending_requests": pending_requests,
        "orientation_upcoming_slots": upcoming_slots,
        "orientation_slot_days": slot_days,
        "orientation_has_later_days": any(day["is_later"] for day in slot_days),
    }


_UPCOMING_SLOTS_NEAR_DAYS = 14


def _upcoming_slot_days(slots: list[Any]) -> list[dict[str, Any]]:
    """Group the manage tab's ``starts_at``-ordered upcoming slots by local date.

    A day opens by default when anyone holds a seat on it (an attendee row or a
    checkout hold); days beyond the near horizon sit behind a client-side "Show
    later days" button so eight weeks of carved slots never render as a wall.
    """
    later_after = timezone.localdate() + timedelta(days=_UPCOMING_SLOTS_NEAR_DAYS)
    days: list[dict[str, Any]] = []
    for slot in slots:
        local_day = timezone.localtime(slot.starts_at).date()
        if not days or days[-1]["date"] != local_day:
            days.append(
                {
                    "date": local_day,
                    "slots": [],
                    "booked_count": 0,
                    "open_by_default": False,
                    "is_later": local_day > later_after,
                }
            )
        days[-1]["slots"].append(slot)
        days[-1]["booked_count"] += slot.active_booking_count
        if slot.attendee_bookings or slot.hold_count:
            days[-1]["open_by_default"] = True
    return days


def _render_manage(
    request: HttpRequest,
    equipment: Equipment,
    *,
    form: EquipmentForm | None = None,
    staff_add_form: EquipmentStaffAddForm | None = None,
    hours_formset: Any = None,
    settings_form: EquipmentSettingsForm | None = None,
    orientation_types_formset: Any = None,
    orientation_hours_formset: Any = None,
    slot_add_form: EquipmentOrientationSlotForm | None = None,
    active_tab: str = "details",
) -> HttpResponse:
    """Render the manage panel with the given (possibly error-bearing) forms."""
    return render(
        request,
        "hub/equipment_manage.html",
        {
            **_get_hub_context(request),
            **_orientation_tab_context(equipment),
            "orientation_hours_formset": orientation_hours_formset
            if orientation_hours_formset is not None
            else EquipmentOrientationHoursWindowFormSet(
                initial=equipment.orientation_hours_windows(),
                prefix="ohours",
                form_kwargs={"equipment": equipment},
            ),
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
            else EquipmentOrientationSlotForm(equipment=equipment),
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
    member_name = staff.member.display_name
    staff.delete()
    messages.success(request, f"{member_name} no longer manages the {equipment.name}.")
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


def _orientation_hours_save_message(*, removed: int, kept: int) -> str:
    """The Orientation Hours flash — the counts appear whenever a delete, pause, or re-grid retired slots."""
    parts = ["Hours saved."]
    if removed:
        parts.append(f"Removed {removed} upcoming open slot{'' if removed == 1 else 's'}.")
    if kept:
        pronoun = "it" if kept == 1 else "them"
        parts.append(
            f"{kept} booked slot{'' if kept == 1 else 's'} kept. Cancel {pronoun} from the Upcoming Slots card."
        )
    return " ".join(parts)


@login_required
@equipment_feature_required
@require_POST
def hub_equipment_orientation_hours_save(request: HttpRequest, slug: str) -> HttpResponse:
    """POST — save the Orientation Hours window formset and regenerate the tool's slots.

    One Save for the card (FRONTEND rule 21); a per-row Delete flips its hidden
    DELETE and resubmits this same form. The model reconciles windows into per-day
    rules (retiring what a delete, pause, or re-grid no longer wants) and the
    service carves the new grid immediately, so the Upcoming Slots card is honest
    on the redirect.
    """
    from membership import orientations

    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    formset = EquipmentOrientationHoursWindowFormSet(
        request.POST,
        initial=equipment.orientation_hours_windows(),
        prefix="ohours",
        form_kwargs={"equipment": equipment},
    )
    if formset.is_valid():
        _deleted_rules, removed, kept = equipment.apply_orientation_hours_windows(
            [form.cleaned_data for form in formset if form.cleaned_data and not form.cleaned_data.get("DELETE")]
        )
        orientations.generate_slots(equipment=equipment)
        messages.success(request, _orientation_hours_save_message(removed=removed, kept=kept))
        return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation")
    return _render_manage(request, equipment, orientation_hours_formset=formset, active_tab="orientation")


@login_required
@equipment_feature_required
@require_POST
def hub_equipment_orientation_slot_add(request: HttpRequest, slug: str) -> HttpResponse:
    """POST — add a one-off MANUAL orientation slot (guild None, orienter None)."""
    from membership.models import OrientationSlot

    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    form = EquipmentOrientationSlotForm(request.POST, equipment=equipment)
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
