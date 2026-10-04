"""The Orientations page (#502 part 2) and the per type photo delete endpoint.

``/orientations/`` lists every orientation a member can sign up for, guild owned and
equipment owned, as cards: a photo, the owner, the next three times, and the member's
own state (completed, payment pending, requested or confirmed, paused). The booking
controls post to the same roads the guild and equipment pages use, with
``next=/orientations/`` so the member lands back here (``hub.views._orientation_return``).

Kept out of ``hub/views.py`` so that module stops growing; the shared section builder and
the request helpers are imported from it.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import BooleanField, ExpressionWrapper, Q
from django.db.models.functions import Coalesce, Lower
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from hub.forms import OrientationCustomRequestForm
from hub.views import _get_hub_context, _get_member, _orientation_sections, _require_can_manage_orientations
from membership.models import (
    Guild,
    OrientationAvailabilityBlock,
    OrientationBooking,
    OrientationSlot,
    OrientationType,
)
from membership.permissions import can_manage_equipment

#: How many times a card lists before it says "More times" (the owner page lists them all).
CARD_TIME_CAP = 3

#: The owner chips: All, Guilds, Equipment (``?owner=``).
OWNER_FILTERS = ("guild", "equipment")

_LIVE_STATUSES = (
    OrientationBooking.Status.REQUESTED,
    OrientationBooking.Status.CONFIRMED,
    OrientationBooking.Status.PENDING_PAYMENT,
)


def _listed_types(member: Any, owner_filter: str, query: str) -> list[OrientationType]:
    """Every type the page lists, in one query, each annotated ``is_listed``.

    Listed: an active type of a visible guild whose orientations are enabled, or an active
    type of active equipment. Also any other type the member holds a live booking or a
    checkout hold on (the equipment page's pinning rule), so retiring a type or pausing its
    owner never takes the member's Cancel or Resume payment control away; those carry
    ``is_listed=False`` and render their state block only. Ordered by owner name, then the
    owner's own order.
    """
    listed = Q(is_active=True) & (
        Q(guild__in=Guild.objects.visible(), guild__orientation_settings__is_enabled=True)
        | Q(equipment__is_active=True)
    )
    wanted = listed
    if member is not None:
        pinned = OrientationBooking.objects.filter(member=member, status__in=_LIVE_STATUSES).values(
            "orientation_type_id"
        )
        wanted = listed | Q(pk__in=pinned)
    types = OrientationType.objects.filter(wanted)
    if owner_filter == "guild":
        types = types.filter(guild__isnull=False)
    elif owner_filter == "equipment":
        types = types.filter(equipment__isnull=False)
    if query:
        types = types.filter(name__icontains=query)
    return list(
        types.annotate(is_listed=ExpressionWrapper(listed, output_field=BooleanField()))
        .select_related("guild", "guild__orientation_settings", "equipment", "equipment__guild")
        .order_by(Lower(Coalesce("guild__name", "equipment__name")), "sort_order", "name")
    )


def _paused_message(orientation_type: OrientationType) -> str:
    """Why a type takes no bookings right now, or "" when it does.

    A closed guild says its closed message; closed equipment says its own; a pinned type
    whose owner or row is switched off says the plain sentence.
    """
    if not orientation_type.is_listed:  # type: ignore[attr-defined]
        return "This orientation is paused."
    if orientation_type.is_equipment_owned:
        equipment = orientation_type.equipment
        assert equipment is not None  # is_equipment_owned
        if equipment.is_closed:
            return equipment.closed_message or "This orientation is paused."
        return ""
    settings_obj = orientation_type.guild.orientation_settings  # type: ignore[union-attr]
    if settings_obj.is_closed:
        return settings_obj.closed_message or "This guild isn't taking orientation bookings right now. Check back soon."
    return ""


@login_required
def hub_orientations(request: HttpRequest) -> HttpResponse:
    """The Orientations page: one card per orientation a member can sign up for.

    Built in one pass with a fixed number of queries however many types are listed: the
    types, their bookable slots, the open windows of their guilds (with every window's
    free time read in two queries), the member's state through the shared section
    builder, and the late fee. Image positions are read once per owner, because a box
    crop reads the picture's size from storage.
    """
    from billing.late_fees import unpaid_fee_for

    member = _get_member(request)
    owner_filter = request.GET.get("owner", "")
    if owner_filter not in OWNER_FILTERS:
        owner_filter = ""
    query = request.GET.get("q", "").strip()
    types = _listed_types(member, owner_filter, query)

    paused = {t.pk: _paused_message(t) for t in types}
    bookable_types = [t for t in types if not paused[t.pk]]
    slots_by_type: dict[int, list[OrientationSlot]] = {}
    if bookable_types:
        slots = (
            OrientationSlot.objects.bookable()
            .filter(orientation_type__in=bookable_types)
            .with_seat_holding_count()  # is_full reads the annotation: no COUNT per row
            .select_related("orientation_type", "orienter")
            .order_by("starts_at")
        )
        for slot in slots:
            if slot.is_full:
                continue  # a card lists times a member can take; the owner page shows the full ones
            slot.with_display = slot.with_label
            slots_by_type.setdefault(slot.orientation_type_id, []).append(slot)

    open_guild_ids = {t.guild_id for t in bookable_types if t.guild_id is not None}
    blocks = (
        list(
            OrientationAvailabilityBlock.objects.upcoming()
            .filter(guild_id__in=open_guild_ids)
            .select_related("orienter", "guild")
            .order_by("starts_at")
        )
        if open_guild_ids
        else []
    )
    free_by_block = OrientationAvailabilityBlock.free_intervals_by_block(blocks)

    sections = _orientation_sections(types, member, slots_by_type, slot_cap=CARD_TIME_CAP)
    position_by_owner: dict[tuple[str, int], str] = {}
    for section in sections:
        orientation_type = section["type"]
        open_for_type = not (section["is_oriented"] or section["booking"] or section["hold"])
        section["paused_message"] = paused[orientation_type.pk]
        type_blocks = (
            [
                block
                for block in blocks
                if block.guild_id == orientation_type.guild_id
                and block.valid_starts_for(orientation_type, free=free_by_block[block.pk])
            ]
            if open_for_type and not section["paused_message"] and orientation_type.guild_id is not None
            else []
        )
        section["blocks"] = type_blocks[:CARD_TIME_CAP]
        type_slots = slots_by_type.get(orientation_type.pk, []) if open_for_type else []
        section["more_times"] = len(type_slots) > CARD_TIME_CAP or len(type_blocks) > CARD_TIME_CAP
        section["custom_form"] = None
        if (
            open_for_type
            and not section["paused_message"]
            and not section["slots"]
            and not type_blocks
            and orientation_type.guild_id is not None
            and orientation_type.guild.orientation_settings.allow_custom_requests  # type: ignore[union-attr]
        ):
            # The type rides a hidden input; the form only renders the time and the note,
            # with ids of its own so two cards' fields never share one.
            section["custom_form"] = OrientationCustomRequestForm(auto_id=f"id_custom_{orientation_type.pk}_%s")
        image = orientation_type.card_image
        section["image"] = image
        owner = orientation_type.card_image_owner
        if owner is None:
            section["image_position"] = "50% 50%"
        else:
            key = (owner._meta.label, owner.pk)
            if key not in position_by_owner:
                position_by_owner[key] = owner.hero_object_position
            section["image_position"] = position_by_owner[key]

    return render(
        request,
        "hub/orientations.html",
        {
            **_get_hub_context(request),
            "open_sections": [s for s in sections if not s["is_oriented"]],
            "done_sections": [s for s in sections if s["is_oriented"]],
            "owner_filter": owner_filter,
            "query": query,
            "is_filtered": bool(owner_filter or query),
            "next_url": request.get_full_path(),
            "unpaid_late_fee": unpaid_fee_for(member) if member is not None else None,
        },
    )


@login_required
@require_POST
def hub_orientation_type_photo_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """POST-only: clear an orientation type's own photo (the ``image_field`` delete endpoint).

    Gated like the editor the photo lives on: a guild owned type by whoever may run the
    guild's orientations, an equipment owned one by the equipment's managers. The card
    goes back to the owner's picture. Lands back on the editor's orientation tab.
    """
    orientation_type = get_object_or_404(OrientationType.objects.select_related("guild", "equipment"), pk=pk)
    if orientation_type.is_equipment_owned:
        equipment = orientation_type.equipment
        assert equipment is not None  # is_equipment_owned
        if not can_manage_equipment(request, equipment):
            return HttpResponse("Forbidden", status=403)
        back = f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation"
    else:
        guild = orientation_type.guild
        assert guild is not None  # exactly one owner (ck_orienttype_one_owner)
        forbidden = _require_can_manage_orientations(request, guild)
        if forbidden is not None:
            return forbidden
        back = f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations"
    if orientation_type.photo:
        orientation_type.photo.delete(save=True)
        messages.success(request, "Photo removed.")
    return redirect(back)
