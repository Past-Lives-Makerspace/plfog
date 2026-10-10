"""Equipment directory views (equipment-reservations spec §6/§7 — PR 1 + PR 2).

The member-facing Equipment index and detail pages (with the schedule + Book a
Time flow), the admin-gated add form, and the manage panel (Details, Staff,
Hours & Limits, Reservations). All views are thin per CLAUDE.md: parse request →
permission guard → form/model/service call → toast, redirect, or render.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Any, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from hub.calendar_pages import calendar_nav_params, reservations_calendar_context
from hub.forms import (
    EquipmentBlockForm,
    EquipmentForm,
    EquipmentHoursWindowFormSet,
    EquipmentManagerCancelForm,
    EquipmentOrientationAvailabilityFormSet,
    EquipmentOrientationRequestsForm,
    EquipmentOrientationSlotForm,
    EquipmentOrientationTypeFormSet,
    EquipmentReservationForm,
    EquipmentSettingsForm,
    EquipmentStaffAddForm,
    OrientationCustomRequestForm,
)
from hub.toast import trigger_toast
from hub.reservation_bookings import reservation_bookings_context
from hub.views import (
    _apply_hours_formset,
    _custom_request_forms,
    _get_hub_context,
    _get_member,
    _hours_save_message,
    _orientation_return,
    _personal_hours_prefix,
    _safe_next,
    _send_custom_request,
    attach_blocking_reservations,
)
from membership import equipment as equipment_service
from membership.models import (
    Equipment,
    EquipmentError,
    EquipmentQuerySet,
    EquipmentReservation,
    EquipmentStaffMembership,
    Guild,
    Member,
    duration_label,
)
from membership.permissions import can_create_equipment, can_manage_equipment, creatable_equipment_kinds

logger = logging.getLogger("hub")

#: The panes ``?view=`` may open on the Reservations page; anything else opens List.
PANES = ("calendar", "bookings")

#: The Reservations page Calendar pane's localStorage salt in the shared calendar shell.
CALENDAR_KEY = "reservations"


def _equipment_queryset() -> EquipmentQuerySet:
    """The base queryset every equipment view reads — FKs prefetched, no per-row queries.

    The unlocking rows arrive with their types and the types' owners (the rows' manager joins
    them), so the ways to qualify (#747) cost one prefetch query for any number of cards.
    """
    return Equipment.objects.select_related("guild", "space").prefetch_related(
        "owned_orientation_types", "unlocking_orientation_rows"
    )


def _require_can_manage(request: HttpRequest, equipment: Equipment) -> HttpResponse | None:
    """Return a 403 response if the user cannot manage ``equipment``, else None."""
    if not can_manage_equipment(request, equipment):
        return HttpResponse("Forbidden", status=403)
    return None


def _member_oriented_type_ids(member: Member | None) -> set[int]:
    """The member's completed orientation-type pks, in a fixed number of queries.

    The bulk input to :meth:`Equipment.access_state` so the index page never runs
    per-card access queries. Completed types come from the one resolver, so a
    hand-entered record (issue #465) opens a gate here exactly as a completed booking
    does. An empty set for an unlinked viewer — every card then reads "Membership
    inactive", which is the honest state.
    """
    if member is None:
        return set()
    return member.completed_orientation_type_ids()


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
        names = ", ".join(booking.member.short_name for booking in slot.seat_holders)
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
    privacy decision), managers' blocks ("Held · reason", #657) and booked
    orientation slots ("Orientation · Sam R.", the same visibility norm). Busy
    segments carry ``kind`` so the template can tell them apart.
    """
    day_start = timezone.make_aware(datetime.combine(selected_day, time.min))
    day_end = day_start + timedelta(days=1)
    busy_list: list[dict[str, Any]] = [
        {
            "kind": "block" if reservation.is_block else "reservation",
            "starts_at": reservation.starts_at,
            "ends_at": reservation.ends_at,
            "label": f"Held · {reservation.purpose}" if reservation.is_block else "",
            "reservation": None if reservation.is_block else reservation,
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
        disabled = not equipment.is_within_horizon(day) or day.weekday() not in active_weekdays
        days.append({"date": day, "disabled": disabled})
    if selected_day is None or not equipment.is_within_horizon(selected_day):
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
                {"v": minutes, "label": duration_label(minutes)} for minutes in equipment.durations_for(start)
            ]
            for start in starts
        }

    from billing.late_fees import unpaid_fee_for
    from membership.late_cancel import booking_sentence, cancel_sentence, policy_for_equipment

    policy = policy_for_equipment(equipment)
    blockers = equipment.booking_blockers(member)
    my_reservations: list[EquipmentReservation] = []
    unpaid_late_fee = None
    # A manager of this equipment never pays a late fee on it (#633), so no fee copy shows them. Role based,
    # like the exemption in EquipmentReservation.cancel(), not the preview aware ``manages``.
    fee_exempt = member is not None and member.can_manage_equipment(equipment)
    if member is not None:
        now = timezone.now()
        my_reservations = list(
            equipment.reservations.reservations()
            .filter(member=member, ends_at__gt=now)
            .exclude(status=EquipmentReservation.Status.CANCELLED, cancelled_by=member)
            .order_by("starts_at")
        )
        for reservation in my_reservations:
            # The cancel modal's fee line, only while a cancel right now would be late (#456).
            reservation.late_cancel_warning = (
                cancel_sentence(policy) if not fee_exempt and policy.is_late(reservation.starts_at, now=now) else ""
            )
        unpaid_late_fee = unpaid_fee_for(member)
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
        # Members' bookings only; a manager's block shows on the timeline as held time instead (#657).
        "upcoming_reservations": list(equipment.reservations.upcoming().reservations().select_related("member")[:20]),
        "manages": manages,
        # Under the Book a Time form and appended to its Reserve prompt; "" when no fee applies.
        "late_cancel_sentence": "" if fee_exempt else booking_sentence(policy),
        # The block until paid (#456): the requirements banner shows it with a Pay button.
        "unpaid_late_fee": unpaid_late_fee,
    }


def _attach_running_orientations(equipment_list: Sequence[Equipment], *, now: datetime) -> None:
    """Give every card its ``current_orientation_slots`` (booked orientations running now) in two queries.

    A slot counts on every card its type lists in "Equipment it uses" (#658), so one
    orientation on the press and the lathe shows both cards reserved.
    """
    from membership.models import OrientationSlot

    card_pks = [equipment.pk for equipment in equipment_list]
    running = (
        OrientationSlot.objects.holding_seats()
        .filter(orientation_type__uses_equipment__in=card_pks, starts_at__lt=now, ends_at__gt=now)
        .select_related("orientation_type")
        .prefetch_related(
            Prefetch(
                "orientation_type__uses_equipment",
                queryset=Equipment.objects.filter(pk__in=card_pks).only("pk"),
                to_attr="listed_cards",
            )
        )
    )
    by_equipment: dict[int, list[Any]] = {pk: [] for pk in card_pks}
    for slot in running:
        for listed in slot.orientation_type.listed_cards:
            by_equipment[listed.pk].append(slot)
    for equipment in equipment_list:
        equipment.current_orientation_slots = by_equipment[equipment.pk]


def reservation_cards(member: Member | None, queryset: EquipmentQuerySet) -> list[dict[str, Any]]:
    """The card dicts ``hub/partials/equipment_cards.html`` renders: the one definition of a card (#502).

    The Reservations page and the guild page's Reservations tab both build their grid here,
    so a card cannot read differently on the two. ``queryset`` is the caller's filtered
    :func:`_equipment_queryset`; this adds everything the partial reads, in a fixed number of
    queries however many cards there are: the locked card's link annotation, the staff behind
    the Staff line (#615), the hours and the right now reservations behind the availability
    line, the running orientations, the member's access sets and one fee lookup. An empty
    grid costs only its own read.

    Args:
        member: The viewer, or ``None`` for an unlinked account (every card then reads
            "Membership inactive").
        queryset: The items to show, in display order.

    Returns:
        One ``{"equipment", "access_state", "availability"}`` dict per item.
    """
    from billing.late_fees import unpaid_fee_for

    now = timezone.now()
    equipment_list = list(
        queryset.with_unlocking_orientations_listed()
        .with_staff()
        .prefetch_related(
            "hours_rules",
            Prefetch(
                "reservations",
                queryset=EquipmentReservation.objects.confirmed().filter(starts_at__lte=now, ends_at__gt=now),
                to_attr="current_reservations",
            ),
        )
    )
    if not equipment_list:
        return []
    _attach_running_orientations(equipment_list, now=now)
    oriented_ids = _member_oriented_type_ids(member)
    # The block until paid (#456) is a per-member state: one lookup for the whole grid.
    has_unpaid_fee = member is not None and unpaid_fee_for(member) is not None
    return [
        {
            "equipment": equipment,
            "access_state": equipment.access_state(
                member, oriented_type_ids=oriented_ids, has_unpaid_fee=has_unpaid_fee
            ),
            "availability": equipment.availability_line(),
        }
        for equipment in equipment_list
    ]


@login_required
def hub_equipment_index(request: HttpRequest) -> HttpResponse:
    """The Equipment directory — card grid with guild/kind/search filters and access badges.

    ``?view=calendar`` opens the Calendar pane, the only time this view builds the calendar;
    otherwise the pane fetches it from :func:`hub_equipment_calendar_events` when the member
    first switches to it, so the List view costs no calendar queries. ``?view=bookings`` opens
    the Bookings pane (#627) the same way, built only then and otherwise fetched from
    :func:`hub_equipment_bookings`.
    """
    member = _get_member(request)
    pane = request.GET.get("view", "")
    if pane not in PANES:
        pane = "list"
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
    cards = reservation_cards(member, filtered)
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
            "pane": pane,
            "calendar": reservations_calendar_context() if pane == "calendar" else None,
            "calendar_key": CALENDAR_KEY,
            # The Bookings pane's own keys, merged in only when it opens (#627).
            **(reservation_bookings_context(request) if pane == "bookings" else {}),
        },
    )


@login_required
def hub_equipment_bookings(request: HttpRequest) -> HttpResponse:
    """HTMX partial: the Reservations page's Bookings pane alone (#627), honouring the page's query string.

    The page's Bookings tab loads it the first time a member opens the tab from List or
    Calendar, and the pane reloads its table from it after a late fee refund (``refund-done``).
    """
    return render(request, "hub/partials/reservation_bookings_pane.html", reservation_bookings_context(request))


@login_required
def hub_equipment_calendar_events(request: HttpRequest) -> HttpResponse:
    """HTMX partial: the Reservations calendar's grid and list, for its Week and Month navigation.

    ``?shell=1`` returns the whole calendar (view toggle and legend too), which the page's
    Calendar pane loads the first time a member opens it.
    """
    week_offset, month_offset, event_page = calendar_nav_params(request)
    cal = reservations_calendar_context(week_offset=week_offset, month_offset=month_offset, event_page=event_page)
    if request.GET.get("shell"):
        return render(request, "hub/partials/guild_calendar_app.html", {"cal": cal, "cal_key": CALENDAR_KEY})
    return render(request, "hub/partials/calendar_content.html", cal)


def _form_scope(request: HttpRequest, equipment: Equipment | None = None) -> dict[str, Any]:
    """The ``kinds`` and ``guilds`` kwargs an :class:`EquipmentForm` gets for this request (#502).

    Empty (the full pickers, today's rules) for anyone who may create a tool: full admins,
    EQUIPMENT holders, and, through the manage panel, guild leads and per item managers,
    whose list is empty. A Space Manager's list has no TOOL in it, so they get their kinds
    plus the item's current kind (a tool they manage through a guild still validates) in
    ``Equipment.Kind`` order, and the guilds they lead or staff plus the item's current
    guild, so they cannot file a space under a guild they are not part of and hand its
    management to that guild's staff. Standalone always stays available.
    """
    kinds = creatable_equipment_kinds(request)
    if not kinds or Equipment.Kind.TOOL in kinds:
        return {}
    # A narrowed list only ever comes from the SPACE_MANAGER capability, which lives on the
    # request's linked member, so there is one here.
    member = cast(Member, _get_member(request))
    allowed = set(kinds)
    guild_filter = Q(pk__in=member.staffed_guilds.values("pk"))
    if equipment is not None:
        allowed.add(equipment.kind)
        guild_filter |= Q(pk=equipment.guild_id)
    return {
        "kinds": [value for value in Equipment.Kind.values if value in allowed],
        "guilds": Guild.objects.filter(guild_filter),
    }


@login_required
def hub_equipment_add(request: HttpRequest) -> HttpResponse:
    """The create form, gated on the kinds this request may create (#502).

    Full admins and EQUIPMENT holders create every kind; a Space Manager creates rooms and
    spaces only, under the guilds they lead or staff or standalone, and the form itself
    narrows the pickers and refuses the rest (:func:`_form_scope`). A creator who could
    not otherwise manage what they just made (a Space Manager has no site tier) gets an
    ``EquipmentStaffMembership`` row so the manage page opens for them.
    """
    if not creatable_equipment_kinds(request):
        return HttpResponse("Forbidden", status=403)
    form = EquipmentForm(request.POST or None, request.FILES or None, **_form_scope(request))
    if request.method == "POST" and form.is_valid():
        equipment = form.save()
        creator = _get_member(request)
        if creator is not None and not can_manage_equipment(request, equipment):
            EquipmentStaffMembership.objects.create(equipment=equipment, member=creator, granted_by=creator)
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
    from membership.models import OrientationSlot

    # Active types first in the owner's order, then any retired one the member is holding
    # (the same pinning rule as the Orientations page, OrientationTypeQuerySet.held_condition).
    types = list(
        equipment.owned_orientation_types.active_or_held_by(member).order_by("-is_active", "sort_order", "name")
    )
    if not types:
        return []
    slots = (
        OrientationSlot.objects.bookable()
        .filter(orientation_type__in=types)
        .with_seat_holding_count()  # is_full reads the annotation: no COUNT per row
        .select_related("orientation_type", "orienter")
        .order_by("starts_at")
    )
    slots_by_type: dict[int, list[Any]] = {}
    for slot in slots:
        # "with Dana" on a manager's personal slot; empty for a shared slot.
        slot.with_display = slot.with_label
        slots_by_type.setdefault(slot.orientation_type_id, []).append(slot)
    # No cap: the guild list's five per page pager bounds the view.
    sections = _orientation_sections(types, member, slots_by_type, slot_cap=None)
    for section in sections:
        # Propose a time (#733): an open section whose every posted time is taken, or that has none.
        open_for_type = not (section["is_oriented"] or section["booking"] or section["hold"])
        no_open_time = all(slot.is_full for slot in section["slots"])
        section["custom_form"], section["custom_amount_form"] = (
            _custom_request_forms(section["type"]) if open_for_type and no_open_time else (None, None)
        )
    return sections


@login_required
@require_POST
def hub_equipment_orientation_request_custom(request: HttpRequest, slug: str) -> HttpResponse:
    """POST-only — a member proposes their own time for one of the equipment's orientations (#733).

    The guilds' custom request road for an equipment owner: refused unless the equipment
    takes custom requests (:attr:`Equipment.takes_custom_orientation_requests`), so a
    crafted post with the switch off books nothing. The one seat request goes to the
    equipment's managers to confirm, a priced type pays first, and a donation type asks
    for its amount, all through the shared tail (``hub.views._send_custom_request``).
    """
    equipment = get_object_or_404(Equipment.objects.select_related("guild"), slug=slug)
    member = _get_member(request)
    if member is None:
        messages.error(request, "You need a member profile to request an orientation.")
        return _orientation_return(request, equipment)
    if not equipment.takes_custom_orientation_requests:
        messages.error(request, "This equipment isn't taking custom orientation requests right now.")
        return _orientation_return(request, equipment)
    form = OrientationCustomRequestForm(request.POST, equipment=equipment)
    if not form.is_valid():
        messages.error(request, "Pick one of this equipment's orientations and a valid future time.")
        return _orientation_return(request, equipment)
    return _send_custom_request(
        request,
        equipment,
        member,
        form,
        sent_message="Your orientation request was sent. A manager will confirm a time.",
    )


@login_required
def hub_equipment_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """The equipment mini-page — hero, requirements banner, Orientation section, schedule, Staff, About.

    ``?day=YYYY-MM-DD`` opens the schedule on that day (the Reservations calendar links here
    that way), with the week strip paged to show it.
    """
    equipment = get_object_or_404(_equipment_queryset().with_staff(), slug=slug)
    manages = can_manage_equipment(request, equipment)
    if not equipment.is_active and not manages:
        raise Http404("This equipment has been retired.")
    member = _get_member(request)
    day = _parse_day(request.GET.get("day", ""))
    schedule = _schedule_context(equipment, member, week_offset=_strip_week_of(day), selected_day=day, manages=manages)
    # The schedule builder already looked the fee up once; the banner state reads the same answer.
    access_state = equipment.access_state(member, has_unpaid_fee=schedule["unpaid_late_fee"] is not None)
    # One row per unlocking orientation (#656): its live booking, Book link or paused note, way by way (#747).
    orientation_unlock_ways = (
        equipment.orientation_unlock_ways(member)
        if member is not None and access_state == Equipment.AccessState.NEEDS_ORIENTATION
        else []
    )
    unlock_ways = [[unlock.orientation_type for unlock in way] for way in orientation_unlock_ways]
    return render(
        request,
        "hub/equipment_detail.html",
        {
            **_get_hub_context(request),
            **schedule,
            "equipment": equipment,
            "access_state": access_state,
            "orientation_unlocks": [unlock for way in orientation_unlock_ways for unlock in way],
            "orientation_unlock_ways": orientation_unlock_ways,
            # The grouped sentence (#747) only when a way needs several orientations; otherwise today's copy.
            "orientation_requirement_sentence": (
                equipment.requirement_sentence(unlock_ways) if Equipment.ways_are_grouped(unlock_ways) else ""
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


def _strip_week_of(day: date | None) -> int:
    """The week strip page that shows ``day``: 0 for no day or a past one (the strip clamps past the horizon)."""
    if day is None:
        return 0
    return max((day - timezone.localdate()).days // 7, 0)


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
@require_POST
def hub_equipment_reservation_cancel(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """POST — cancel a reservation: the member's own (no reason), or a manager's (reason required).

    The manage-tab variant is marked by its ``reason`` field: a manager posting it
    takes the manager path even for THEIR OWN row (reason honored, in-progress
    allowed, redirect back to the tab). Deliberately no retired-equipment 404 here —
    a member must always be able to back out of a retired tool's reservation.

    The Reservations page's Bookings tab (#627) posts a ``next``. On the self route that
    makes the answer a full page (a message, then ``next``; a late cancel goes straight to
    Stripe Checkout, which is why that form is unboosted) instead of the schedule partial;
    on the manager route it replaces the manage tab as the landing. Gates unchanged.
    """
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    reservation = get_object_or_404(EquipmentReservation, pk=pk, equipment=equipment)
    member = _get_member(request)
    if member is None:
        return HttpResponse("Forbidden", status=403)
    manager_route = "reason" in request.POST and can_manage_equipment(request, equipment)
    if reservation.member_id == member.pk and not manager_route:
        if "next" in request.POST:
            # A posted next that is not safe still gets a page back, the Bookings tab, never the schedule fragment.
            next_url = _safe_next(request, "") or f"{reverse('hub_equipment_index')}?view=bookings"
            return _self_cancel_to_page(request, reservation, member, next_url)
        week_offset = _parse_week_value(request.POST.get("week", "0"))
        selected_day = _parse_day(request.POST.get("day", ""))
        try:
            fee = reservation.cancel(member)
        except EquipmentError as exc:
            response = _render_schedule(request, equipment, week_offset=week_offset, selected_day=selected_day)
            trigger_toast(response, str(exc), "error")
            return response
        response = _render_schedule(request, equipment, week_offset=week_offset, selected_day=selected_day)
        if fee is None:
            trigger_toast(response, "Reservation cancelled.", "success")
            return response
        # A late cancel (#456): straight to Stripe Checkout. The modal posts through htmx, and
        # an XHR cannot follow a cross-origin 302, so the redirect rides the HX-Redirect header.
        from billing import late_fees

        try:
            checkout_url = late_fees.start_fee_checkout(fee)
        except Exception:
            logger.exception("Late fee checkout failed for reservation %s.", reservation.pk)
            trigger_toast(
                response,
                "Reservation cancelled. A late cancellation fee applies; use the Pay button to pay it.",
                "info",
            )
            return response
        response["HX-Redirect"] = checkout_url
        return response
    if not can_manage_equipment(request, equipment):
        return HttpResponse("Forbidden", status=403)
    form = EquipmentManagerCancelForm(request.POST)
    back = _safe_next(request, f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=reservations")
    if not form.is_valid():
        messages.error(request, "Please tell the member why.")
        return redirect(back)
    try:
        reservation.cancel(member, reason=form.cleaned_data["reason"], as_manager=True)
    except EquipmentError as exc:
        messages.error(request, str(exc))
        return redirect(back)
    # A manager cancelling their own row emails nobody, so there is nobody to have told.
    told = "" if reservation.member_id == member.pk else " The member has been told."
    messages.success(request, f"Reservation cancelled.{told}")
    return redirect(back)


def _self_cancel_to_page(
    request: HttpRequest, reservation: EquipmentReservation, member: Member, next_url: str
) -> HttpResponse:
    """A member's own cancel from the Bookings tab (#627): a Django message, then ``next``.

    A late cancel creates the fee as it does from the schedule and sends the member straight
    to Stripe Checkout; the tab's form is unboosted, so the browser follows that redirect.
    """
    from billing import late_fees

    try:
        fee = reservation.cancel(member)
    except EquipmentError as exc:
        messages.error(request, str(exc))
        return redirect(next_url)
    if fee is None:
        messages.success(request, "Reservation cancelled.")
        return redirect(next_url)
    try:
        checkout_url = late_fees.start_fee_checkout(fee)
    except Exception:
        logger.exception("Late fee checkout failed for reservation %s.", reservation.pk)
        messages.info(request, "Reservation cancelled. A late cancellation fee applies; use the Pay button to pay it.")
        return redirect(next_url)
    return redirect(checkout_url)


@login_required
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
    ``orienter_members()`` entry with their personal rules, a Former Managers group
    for orphan rules, the shared (orienter-less) rules, and the flat upcoming slot
    list with attendee sub-rows. ``can_edit_others_hours`` gates the whole-schedule
    view and the Runs with picker; a plain manager sees only their own group.
    """
    from membership.models import OrientationAvailability, OrientationBooking, OrientationSlot
    from membership.permissions import can_edit_equipment_orienter_hours

    viewer = _get_member(request)
    can_edit_others_hours = can_edit_equipment_orienter_hours(request, equipment, None)
    managers = equipment.orienter_members()
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
    for slot in upcoming_slots:
        slot.attendee_bookings = attendees_by_slot.get(slot.pk, [])
    # A manager may post a slot over an existing reservation (they might mean to
    # bump it); such a slot renders muted with "Blocked by ..." and never reaches
    # members (bookable() hides it) until the reservation is cancelled.
    attach_blocking_reservations(upcoming_slots)
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


def _manage_late_fees(equipment: Equipment) -> list[tuple[Any, Any]]:
    """The equipment's late cancellation fees (#456) newest first, each with its Waive form.

    Its reservations' fees and its owned orientations' fees, in one query carrying what
    each row's label and state read. The form per row has the fee's own prefix so N
    modals on one page never share a field id. Everyone who can open the manage page may
    waive every fee here: they all follow ``can_manage_equipment`` for this equipment.
    """
    from billing.forms import LateFeeWaiveForm
    from billing.models import LateCancellationFee

    fees = (
        LateCancellationFee.objects.filter(
            Q(reservation__equipment=equipment) | Q(orientation_booking__orientation_type__equipment=equipment)
        )
        .select_related(
            "member",
            "waived_by__member",
            "reservation__equipment",
            "orientation_booking__slot",
            "orientation_booking__orientation_type",
        )
        .order_by("-created_at", "-pk")
    )
    return [(fee, LateFeeWaiveForm(fee=fee)) for fee in fees]


def _render_manage(
    request: HttpRequest,
    equipment: Equipment,
    *,
    form: EquipmentForm | None = None,
    staff_add_form: EquipmentStaffAddForm | None = None,
    hours_formset: Any = None,
    settings_form: EquipmentSettingsForm | None = None,
    orientation_types_formset: Any = None,
    orientation_requests_form: EquipmentOrientationRequestsForm | None = None,
    slot_add_form: EquipmentOrientationSlotForm | None = None,
    block_form: EquipmentBlockForm | None = None,
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
            "form": form if form is not None else EquipmentForm(instance=equipment, **_form_scope(request, equipment)),
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
            "orientation_requests_form": orientation_requests_form
            if orientation_requests_form is not None
            else EquipmentOrientationRequestsForm(instance=equipment, prefix=EquipmentOrientationRequestsForm.PREFIX),
            # The per type photo field (#502) rejects an oversized file before it posts.
            "max_upload_image_bytes": settings.MAX_UPLOAD_IMAGE_BYTES,
            "slot_add_form": slot_add_form
            if slot_add_form is not None
            else EquipmentOrientationSlotForm(
                equipment=equipment,
                acting_member=_get_member(request),
                lock_to_acting=orientation_ctx["slot_form_locked"],
            ),
            # The hub's standard Paginator + table_pagination partial, capped at 25 rows.
            "manage_reservations": Paginator(
                equipment.reservations.upcoming().reservations().select_related("member"), 25
            ).get_page(request.GET.get("page", 1)),
            # The Block Time card and its Upcoming Blocks list (#657).
            "block_form": block_form if block_form is not None else EquipmentBlockForm(),
            "manage_blocks": list(equipment.reservations.upcoming().blocks().select_related("member")),
            # The Reservations tab's late fee card (#456): the rows and where its Waive returns to.
            "manage_late_fees": _manage_late_fees(equipment),
            "manage_late_fees_next": f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=reservations",
            "active_tab": active_tab,
        },
    )


@login_required
@require_POST
def hub_equipment_block_add(request: HttpRequest, slug: str) -> HttpResponse:
    """POST — hold a span on the manage tab (#657); a refused block re-renders the card with its message."""
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    member = _get_member(request)
    if member is None:
        return HttpResponse("Forbidden", status=403)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    form = EquipmentBlockForm(request.POST)
    if form.is_valid():
        try:
            equipment_service.block_time(
                equipment,
                member,
                form.cleaned_data["starts_at"],
                form.cleaned_data["ends_at"],
                reason=form.cleaned_data["reason"],
            )
        except EquipmentError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Time blocked. Members can't book over it.")
            return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=reservations")
    return _render_manage(request, equipment, block_form=form, active_tab="reservations")


@login_required
@require_POST
def hub_equipment_block_remove(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """POST — release a block (#657): no reason asked, nobody emailed, no fee."""
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    member = _get_member(request)
    if member is None:
        return HttpResponse("Forbidden", status=403)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    block = get_object_or_404(EquipmentReservation, pk=pk, equipment=equipment, kind=EquipmentReservation.Kind.BLOCK)
    try:
        block.remove_block(member)
    except EquipmentError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Block removed. The time is open again.")
    return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=reservations")


@login_required
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


def _require_can_print(request: HttpRequest, equipment: Equipment) -> HttpResponse | None:
    """403 unless the request may print ``equipment``'s QR sheet, with the reason when it is retired.

    The manage gate: ``can_manage_equipment`` already holds everyone ``Equipment.is_run_by``
    names (the tool's staff rows and its guild's lead and staff), plus admins and the
    EQUIPMENT capability. A runner of a retired item hears why it cannot be printed.
    """
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    if equipment.qr_sheet_refusal:
        return HttpResponse(equipment.qr_sheet_refusal, status=403)
    return None


@login_required
def hub_equipment_flyer(request: HttpRequest, slug: str) -> HttpResponse:
    """The equipment QR sheet (#631): one printable Letter page to post at the machine.

    A QR to the equipment page, where a member reserves it or sees the orientation it needs,
    and, when it requires an orientation that is on, a second QR straight to booking it.
    """
    equipment = get_object_or_404(_equipment_queryset().select_related("area"), slug=slug)
    forbidden = _require_can_print(request, equipment)
    if forbidden is not None:
        return forbidden
    return render(
        request,
        "hub/equipment_flyer.html",
        {
            "equipment": equipment,
            "qr_svg": equipment.qr_svg(),
            "orientations": equipment.qr_sheet_orientations,
            "requirement": equipment.qr_sheet_requirement,
        },
    )


@login_required
def hub_equipment_qr(request: HttpRequest, slug: str, fmt: str) -> HttpResponse:
    """Download the equipment page QR as SVG or PNG, gated like the sheet."""
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    forbidden = _require_can_print(request, equipment)
    if forbidden is not None:
        return forbidden
    if fmt == "svg":
        response = HttpResponse(equipment.qr_svg(), content_type="image/svg+xml")
    elif fmt == "png":
        response = HttpResponse(equipment.qr_png_bytes(), content_type="image/png")
    else:
        raise Http404
    response["Content-Disposition"] = f'attachment; filename="{equipment.slug}-qr.{fmt}"'
    return response


@login_required
@require_POST
def hub_equipment_details_save(request: HttpRequest, slug: str) -> HttpResponse:
    """POST-only — save the manage panel's Details tab (the same form as the add page)."""
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    form = EquipmentForm(request.POST, request.FILES, instance=equipment, **_form_scope(request, equipment))
    if form.is_valid():
        form.save()
        messages.success(request, "Saved.")
        return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=details")
    return _render_manage(request, equipment, form=form, active_tab="details")


@login_required
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
    # Retire their personal hours ONLY when they no longer RUN orientations here at all —
    # they may still be on the owning guild's leadership, and those hours keep generating.
    # is_run_by is the same narrow set generation, the roster, and the booking gate read.
    if not equipment.is_run_by(removed_member):
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
    formset = EquipmentOrientationTypeFormSet(request.POST, request.FILES, instance=equipment, prefix="otypes")
    # The custom request switch (#733) saves only when the card that draws it posted.
    requests_form = (
        EquipmentOrientationRequestsForm(
            request.POST, instance=equipment, prefix=EquipmentOrientationRequestsForm.PREFIX
        )
        if EquipmentOrientationRequestsForm.SHOWN_FIELD in request.POST
        else None
    )
    if formset.is_valid() and (requests_form is None or requests_form.is_valid()):
        formset.save()
        if requests_form is not None:
            requests_form.save()
        messages.success(request, "Saved.")
        return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation")
    return _render_manage(
        request,
        equipment,
        orientation_types_formset=formset,
        orientation_requests_form=requests_form,
        active_tab="orientation",
    )


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
                card="Upcoming Slots",  # the equipment tab keeps its own card name (#532)
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
