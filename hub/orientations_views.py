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
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from hub.forms import OrientationCustomRequestForm
from hub.views import (
    _can_access_orientations,
    _get_hub_context,
    _get_member,
    _orientation_sections,
    _require_can_manage_orientations,
)
from membership.models import Guild, Member, OrientationAvailabilityBlock, OrientationSlot, OrientationType
from membership.permissions import can_manage_equipment

#: How many times a card lists before it says "More times" (the owner page lists them all).
CARD_TIME_CAP = 3

#: The owner chips: All, Guilds, Equipment (``?owner=``).
OWNER_FILTERS = ("guild", "equipment")


def _filtered_types(member: Member | None, owner_filter: str, query: str) -> list[OrientationType]:
    """The model's listed types for ``member``, narrowed by the owner chip and the search."""
    listed = OrientationType.objects.listed_for(member)
    if owner_filter == "guild":
        listed = listed.filter(guild__isnull=False)
    elif owner_filter == "equipment":
        listed = listed.filter(equipment__isnull=False)
    if query:
        listed = listed.filter(name__icontains=query)
    return list(listed)


def _open_slots(
    bookable_types: list[OrientationType], labels_by_guild: dict[int, dict[int, str]]
) -> tuple[dict[int, list[OrientationSlot]], set[int]]:
    """The bookable times each card can offer, and the types whose every posted time is full.

    A card lists only times a member can take; a full one is left off (the owner page shows
    it as Full) but remembered, so a card with nothing else says so rather than "No times are
    posted yet." A guild slot's orienter reads "Bob P." when two of that guild's leadership
    share a first name, as on the guild page.
    """
    slots_by_type: dict[int, list[OrientationSlot]] = {}
    full_type_ids: set[int] = set()
    if not bookable_types:
        return slots_by_type, full_type_ids
    slots = (
        OrientationSlot.objects.bookable()
        .filter(orientation_type__in=bookable_types)
        .with_seat_holding_count()  # is_full reads the annotation: no COUNT per row
        .select_related("orientation_type", "orienter")
        .order_by("starts_at")
    )
    for slot in slots:
        if slot.is_full:
            full_type_ids.add(slot.orientation_type_id)
            continue
        label = labels_by_guild.get(slot.guild_id or 0, {}).get(slot.orienter_id or 0, "")
        slot.with_display = f"with {label}" if label else slot.with_label
        slots_by_type.setdefault(slot.orientation_type_id, []).append(slot)
    return slots_by_type, full_type_ids


def _dress_card(
    section: dict[str, Any],
    *,
    bookable: bool,
    type_slots: list[OrientationSlot],
    type_blocks: list[OrientationAvailabilityBlock],
    has_full_slots: bool,
    position_by_owner: dict[tuple[str, int], str],
) -> None:
    """Add what a card shows beyond the shared section: times, the fallbacks and its picture.

    ``bookable`` is whether the card is open for the member (not completed, booked or
    paused); ``type_slots`` and ``type_blocks`` are every bookable time the type has (only the first
    three render); ``position_by_owner`` caches each borrowed picture's crop, read once per
    owner because a box crop reads the picture's size from storage.
    """
    orientation_type = section["type"]
    section["blocks"] = type_blocks[:CARD_TIME_CAP]
    section["more_times"] = len(type_slots) > CARD_TIME_CAP or len(type_blocks) > CARD_TIME_CAP
    # Only an open card offers anything: a completed, booked or paused one shows its state.
    nothing_to_book = bookable and not type_slots and not type_blocks
    # Posted times exist but every one is taken: the card says so and links the owner page.
    section["all_full"] = nothing_to_book and has_full_slots
    section["custom_form"] = None
    if (
        nothing_to_book
        and orientation_type.guild is not None
        and orientation_type.guild.orientation_settings.allow_custom_requests
    ):
        # The type rides a hidden input; the form only renders the time and the note,
        # with ids of its own so two cards' fields never share one.
        section["custom_form"] = OrientationCustomRequestForm(auto_id=f"id_custom_{orientation_type.pk}_%s")
    section["image"] = orientation_type.card_image
    owner = orientation_type.card_image_owner
    if owner is None:
        section["image_position"] = "50% 50%"
        return
    key = (owner._meta.label, owner.pk)
    if key not in position_by_owner:
        position_by_owner[key] = owner.hero_object_position
    section["image_position"] = position_by_owner[key]


@login_required
def hub_orientations(request: HttpRequest) -> HttpResponse:
    """The Orientations page: one card per orientation a member can sign up for.

    Which types, and why one is paused, are the model's (``OrientationType.objects.listed_for``,
    ``OrientationType.paused_message``); the view applies the chips and the search. Built
    in one pass with a fixed number of queries however many types are listed: the types,
    their guilds' orienter labels, their bookable slots, the open windows of their guilds
    (with every window's free time read in two queries), the member's state through the
    shared section builder, and the late fee.
    """
    from billing.late_fees import unpaid_fee_for

    member = _get_member(request)
    owner_filter = request.GET.get("owner", "")
    if owner_filter not in OWNER_FILTERS:
        owner_filter = ""
    query = request.GET.get("q", "").strip()
    types = _filtered_types(member, owner_filter, query)

    bookable_types = [t for t in types if not t.paused_message]
    open_guilds = {t.guild_id: t.guild for t in bookable_types if t.guild is not None}
    slots_by_type, full_type_ids = _open_slots(bookable_types, Guild.orienter_name_labels_for(open_guilds.values()))
    blocks = (
        list(
            OrientationAvailabilityBlock.objects.upcoming()
            .filter(guild_id__in=set(open_guilds))
            .select_related("orienter", "guild")
            .order_by("starts_at")
        )
        if open_guilds
        else []
    )
    free_by_block = OrientationAvailabilityBlock.free_intervals_by_block(blocks)

    sections = _orientation_sections(types, member, slots_by_type, slot_cap=CARD_TIME_CAP)
    position_by_owner: dict[tuple[str, int], str] = {}
    for section in sections:
        orientation_type = section["type"]
        section["paused_message"] = orientation_type.paused_message
        bookable = not (section["is_oriented"] or section["booking"] or section["hold"] or section["paused_message"])
        _dress_card(
            section,
            bookable=bookable,
            type_slots=slots_by_type.get(orientation_type.pk, []) if bookable else [],
            type_blocks=[
                block
                for block in blocks
                if bookable
                and block.guild_id == orientation_type.guild_id
                and block.valid_starts_for(orientation_type, free=free_by_block[block.pk])
            ],
            has_full_slots=bookable and orientation_type.pk in full_type_ids,
            position_by_owner=position_by_owner,
        )

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
            # The dashboard's own gate: leads, staff and admins get a way into it.
            "can_manage_orientations": _can_access_orientations(request),
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
