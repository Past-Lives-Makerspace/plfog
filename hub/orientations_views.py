"""The Orientations page (#502), the per type photo delete endpoint, Record Orientation (#630) and the QR sheet (#631).

``/orientations/`` lists every orientation a member can sign up for, guild owned and
equipment owned, as cards: a photo, the owner, the next three times, and the member's
own state (completed, payment pending, requested or confirmed, paused). The booking
controls post to the same roads the guild and equipment pages use, with
``next=/orientations/`` so the member lands back here (``hub.views._orientation_return``).

Kept out of ``hub/views.py`` so that module stops growing; the shared section builder and
the request helpers are imported from it.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from billing import orientation_bills
from hub.calendar_pages import calendar_nav_params, orientations_calendar_context
from hub.forms import (
    NewOrientationTypeForm,
    OrientationRecordForm,
)
from hub.orientation_bookings import bookings_pane_context
from hub.views import (
    _custom_request_forms,
    _get_hub_context,
    _get_member,
    _guild_orientations_url,
    _orientation_bookings_tab,
    _orientation_sections,
    _require_can_manage_orientations,
    _require_can_run_orientation_type,
    _safe_next,
)
from membership.models import (
    Guild,
    Member,
    OrientationAvailabilityBlock,
    OrientationBooking,
    OrientationError,
    OrientationRecord,
    OrientationSlot,
    OrientationType,
)
from membership.permissions import (
    can_manage_equipment,
    guilds_for_new_orientation,
    holds_orientor_role,
    manageable_orientation_types,
    manages_orientations,
)

logger = logging.getLogger(__name__)

#: How many times a card lists before it says "More times" (the owner page lists them all).
CARD_TIME_CAP = 3

#: The owner chips: All, Guilds, Equipment (``?owner=``).
OWNER_FILTERS = ("guild", "equipment")

#: The panes ``?view=`` may open; anything else opens List.
PANES = ("calendar", "bookings")

#: The Calendar pane's localStorage salt in the shared calendar shell.
CALENDAR_KEY = "orientations"


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
    posted yet." A guild slot's orientor reads "Bob P." when two of that guild's leadership
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
    # Propose a time (#733): guild and equipment cards alike, by the type's own rule.
    section["custom_form"], section["custom_amount_form"] = (
        _custom_request_forms(orientation_type) if nothing_to_book else (None, None)
    )
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
    their guilds' orientor labels, their bookable slots, the open windows of their guilds
    (with every window's free time read in two queries), the member's state through the
    shared section builder, and the late fee.

    ``?view=calendar`` opens the Calendar pane, the only time this view builds the calendar;
    otherwise the pane fetches it from :func:`hub_orientations_calendar_events` when the member
    first switches to it, so the List view costs no calendar queries. ``?view=bookings`` opens
    the Bookings pane (#626) the same way, built only then and otherwise fetched from
    :func:`hub_orientations_bookings`.
    """
    from billing import payouts
    from billing.late_fees import unpaid_fee_for

    member = _get_member(request)
    pane = request.GET.get("view", "")
    if pane not in PANES:
        pane = "list"
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
            # The same slots the shared builder offers: none inside the type's Booking notice (#766).
            type_slots=orientation_type.offerable_slots(slots_by_type.get(orientation_type.pk, [])) if bookable else [],
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
            "unpaid_late_fee": unpaid_fee_for(member) if member is not None else None,
            # Settings, Payouts nudge (#662): members with the Orientor role (#1097), until they finish Stripe signup.
            "payouts_nudge": member is not None and holds_orientor_role(request) and payouts.needs_nudge(member),
            "pane": pane,
            "calendar": orientations_calendar_context() if pane == "calendar" else None,
            "calendar_key": CALENDAR_KEY,
            # The header's "+ Add an Orientation" targets (#637): one query, preview aware.
            "add_orientation_guilds": guilds_for_new_orientation(request),
            # The Bookings pane's own keys, merged in only when it opens (#626).
            **(bookings_pane_context(request) if pane == "bookings" else {}),
        },
    )


@login_required
def hub_orientations_bookings(request: HttpRequest) -> HttpResponse:
    """HTMX partial: the Bookings pane alone (#626), honouring the page's query string.

    The page's Bookings tab loads it the first time a member opens the tab from List or
    Calendar, and the pane reloads its table from it after a refund (``refund-done``). That
    refresh asks for ``part=body``: it swaps in only the table's body, so the builder skips
    what sits above it.
    """
    context = bookings_pane_context(request, body_only=request.GET.get("part") == "body")
    return render(request, "hub/partials/orientation_bookings_pane.html", context)


@login_required
def hub_orientations_calendar_events(request: HttpRequest) -> HttpResponse:
    """HTMX partial: the Orientations calendar's grid and list, for its Week and Month navigation.

    ``?shell=1`` returns the whole calendar (view toggle and legend too), which the page's
    Calendar pane loads the first time a member opens it.
    """
    week_offset, month_offset, event_page = calendar_nav_params(request)
    cal = orientations_calendar_context(week_offset=week_offset, month_offset=month_offset, event_page=event_page)
    if request.GET.get("shell"):
        return render(request, "hub/partials/guild_calendar_app.html", {"cal": cal, "cal_key": CALENDAR_KEY})
    return render(request, "hub/partials/calendar_content.html", cal)


@login_required
def hub_orientation_add(request: HttpRequest) -> HttpResponse:
    """The Add an Orientation page (#680): one form, the Guild first, then the type's fields.

    Open to whoever sees "+ Add an Orientation" on ``/orientations/``: the viewer's
    ``guilds_for_new_orientation`` (preview aware) is the gate and the Guild choices, and an
    empty list answers 403 like the orientation editors. One guild starts selected, and so
    does ``?guild=<pk>`` when it is one of the choices. A valid Save creates the guild owned
    type, materialises that guild's slots as the types editor does, and lands on the guild's
    Orientations page, where its hours are set. Nothing is announced.
    """
    from membership import orientations

    guilds = guilds_for_new_orientation(request)
    if not guilds:
        return HttpResponse("Forbidden", status=403)
    form = NewOrientationTypeForm(request.POST or None, request.FILES or None, guilds=guilds)
    if request.method == "POST" and form.is_valid():
        orientation_type = form.save()
        guild = form.cleaned_data["guild"]
        orientations.generate_slots(guild=guild)
        messages.success(request, f"{orientation_type.name} added. Set its hours below.")
        return redirect(_guild_orientations_url(guild))
    asked = request.GET.get("guild", "")
    if request.method != "POST" and asked.isdigit() and any(guild.pk == int(asked) for guild in guilds):
        form.fields["guild"].initial = int(asked)
    return render(
        request,
        "hub/orientation_add.html",
        {**_get_hub_context(request), "form": form, "max_upload_image_bytes": settings.MAX_UPLOAD_IMAGE_BYTES},
    )


@login_required
def hub_orientation_type_permalink(request: HttpRequest, pk: int) -> HttpResponse:
    """The stable link an orientation QR sheet encodes (#631): redirect to where the type books now.

    Its card on the Orientations page when the page lists it, else its owner page's
    orientation anchor. Login first, like every hub page, so a logged out scan reaches the
    login page with this link as ``next`` and lands here, then on the booking, afterwards.
    A temporary redirect, so a phone never caches an old target.
    """
    orientation_type = get_object_or_404(OrientationType.objects.select_related("guild", "equipment"), pk=pk)
    return redirect(orientation_type.booking_landing_path())


def _require_can_print_type(request: HttpRequest, orientation_type: OrientationType) -> HttpResponse | None:
    """403 unless the request runs ``orientation_type`` (#630's type gate), with the reason when it cannot print."""
    forbidden = _require_can_run_orientation_type(request, orientation_type)
    if forbidden is not None:
        return forbidden
    if orientation_type.qr_sheet_refusal:
        return HttpResponse(orientation_type.qr_sheet_refusal, status=403)
    return None


@login_required
def hub_orientation_type_flyer(request: HttpRequest, pk: int) -> HttpResponse:
    """The orientation QR sheet (#631): one printable Letter page whose QR books this orientation."""
    orientation_type = get_object_or_404(
        OrientationType.objects.select_related("guild", "equipment", "equipment__guild", "area"), pk=pk
    )
    forbidden = _require_can_print_type(request, orientation_type)
    if forbidden is not None:
        return forbidden
    owner = orientation_type.card_image_owner
    return render(
        request,
        "hub/orientation_type_flyer.html",
        {
            "orientation_type": orientation_type,
            "qr_svg": orientation_type.qr_svg(),
            "image": orientation_type.card_image,
            "image_position": owner.hero_object_position if owner is not None else "50% 50%",
        },
    )


@login_required
def hub_orientation_type_qr(request: HttpRequest, pk: int, fmt: str) -> HttpResponse:
    """Download the orientation's QR as SVG or PNG, gated like the sheet."""
    orientation_type = get_object_or_404(OrientationType.objects.select_related("guild", "equipment"), pk=pk)
    forbidden = _require_can_print_type(request, orientation_type)
    if forbidden is not None:
        return forbidden
    if fmt == "svg":
        response = HttpResponse(orientation_type.qr_svg(), content_type="image/svg+xml")
    elif fmt == "png":
        response = HttpResponse(orientation_type.qr_png_bytes(), content_type="image/png")
    else:
        raise Http404
    response["Content-Disposition"] = f'attachment; filename="orientation-{orientation_type.pk}-qr.{fmt}"'
    return response


@login_required
@require_POST
def hub_orientation_type_photo_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """POST-only: clear an orientation type's own photo (the ``image_field`` delete endpoint).

    Gated like the editor the photo lives on: a guild owned type by whoever may run the
    guild's orientations, an equipment owned one by the equipment's managers. The card
    goes back to the owner's picture. Lands back on the editor's orientation page or tab.
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
        back = _guild_orientations_url(guild)
    if orientation_type.photo:
        orientation_type.photo.delete(save=True)
        messages.success(request, "Photo removed.")
    return redirect(back)


def _record_with_bill(request: HttpRequest, form: OrientationRecordForm, back: str) -> HttpResponse:
    """Record Orientation with Send a bill ticked (#1111): a billed booking and its one email.

    A refusal the service raises (no account, a live booking, an amount) and a double click
    that hits the one live booking per type constraint both land back with the reason and
    write nothing. A sent bill lands on the tab's Past list, Oriented = Yes, where its row is:
    the posted ``next`` is the Oriented list's Upcoming chip, which a past orientation is not on.
    """
    member = form.recorded_member
    try:
        booking = form.send_bill(sent_by=cast(User, request.user))
    except OrientationError as exc:
        messages.error(request, str(exc))
        return redirect(back)
    except IntegrityError:
        messages.error(request, f"{member.display_name} already has a booking for this orientation.")
        return redirect(back)
    messages.success(
        request,
        f"Recorded the {booking.orientation_type.name} orientation for {member.display_name} "
        f"and sent a {booking.bill_amount_display} bill.",
    )
    return redirect(f"{_orientation_bookings_tab()}&show=past&oriented=yes")


@login_required
@require_POST
def hub_orientation_record(request: HttpRequest) -> HttpResponse:
    """POST only: Record Orientation on the Bookings tab (#630), for an orientation done outside the app.

    The admin member edit flow's form with a member picker, offering only the types the viewer
    runs (:func:`membership.permissions.manageable_orientation_types`, the action scope), so a
    crafted POST naming another owner's type fails the form and writes nothing. Silent like the
    admin flow: no email, no Discord, one activity row with the viewer as actor. A refused form
    lands back on the tab with the reasons as messages.
    """
    if not manages_orientations(request):
        return HttpResponse("Forbidden", status=403)
    back = _safe_next(request, _orientation_bookings_tab())
    form = OrientationRecordForm(
        None, request.POST, type_queryset=manageable_orientation_types(request, OrientationType.objects.all())
    )
    if not form.is_valid():
        for error in dict.fromkeys(str(message) for field_errors in form.errors.values() for message in field_errors):
            messages.error(request, error)
        return redirect(back)
    if form.sends_bill:
        return _record_with_bill(request, form, back)
    try:
        # A savepoint: a double click passes clean() twice and the second save hits the
        # one record per member per type constraint; answer it as the form's duplicate refusal.
        with transaction.atomic():
            record = form.save(recorded_by=cast(User, request.user))
    except IntegrityError:
        messages.error(request, f"{form.recorded_member.display_name} already completed this orientation.")
        return redirect(back)
    messages.success(
        request, f"Recorded the {record.orientation_type.name} orientation for {record.member.display_name}."
    )
    return redirect(back)


@login_required
@require_POST
def hub_orientation_record_remove(request: HttpRequest, pk: int) -> HttpResponse:
    """POST only: remove a hand recorded orientation from the Bookings tab (#630).

    Whoever could have recorded it may remove it: the type's gate, as recording reads. The
    member's access closes again at once. Silent like recording.
    """
    record = get_object_or_404(OrientationRecord.objects.with_related(), pk=pk)
    forbidden = _require_can_run_orientation_type(request, record.orientation_type)
    if forbidden is not None:
        return forbidden
    name, member_name = record.orientation_type.name, record.member.display_name
    record.remove(removed_by=cast(User, request.user))
    messages.success(request, f"Removed the {name} orientation record for {member_name}.")
    return redirect(_safe_next(request, _orientation_bookings_tab()))


# --- A bill for an orientation that already happened (#1111) -----------------------------


def _own_bill_or_404(request: HttpRequest, booking_pk: int) -> OrientationBooking:
    """The request member's own billed booking, whatever its state, else 404."""
    booking = get_object_or_404(orientation_bills.bills(), pk=booking_pk)
    member = _get_member(request)
    if member is None or booking.member_id != member.pk:
        raise Http404("No such bill.")
    return booking


@login_required
def hub_orientation_bill(request: HttpRequest, booking_pk: int) -> HttpResponse:
    """The bill's own page (#1111): what it is for, the amount, where it stands, and Pay while unpaid.

    The bill email links here rather than straight to Stripe: a GET never mints a session
    (mail clients prefetch links), so the page shows the bill and only its POST pays it.
    """
    booking = _own_bill_or_404(request, booking_pk)
    return render(request, "hub/orientation_bill.html", {**_get_hub_context(request), **_bill_context(booking)})


@login_required
@require_POST
def hub_orientation_bill_pay(request: HttpRequest, booking_pk: int) -> HttpResponse:
    """Pay an unpaid bill (#1111): mint a fresh Checkout Session and go there.

    The member's own bill only, else 404; a paid or cancelled bill is refused back to its page.
    """
    booking = _own_bill_or_404(request, booking_pk)
    try:
        checkout_url = orientation_bills.start_bill_checkout(booking)
    except OrientationError as exc:
        messages.error(request, str(exc))
        return redirect("hub_orientation_bill", booking_pk=booking.pk)
    except Exception:
        logger.exception("Orientation bill checkout failed for booking %s.", booking.pk)
        messages.error(request, "We couldn't open the payment page just now. Try again in a minute.")
        return redirect("hub_orientation_bill", booking_pk=booking.pk)
    return redirect(checkout_url)


@login_required
def hub_orientation_bill_return(request: HttpRequest, token: str) -> HttpResponse:
    """The Stripe ``success_url`` landing for a bill (#1111): paid, not confirmed yet, or a bad token.

    An unpaid bill is reconciled against Stripe at once (race safe against the webhook), so
    the page normally reads Paid on the first visit; otherwise it says Stripe has not
    confirmed yet and keeps the Pay button. A bad token renders 400.
    """
    from django.core.signing import BadSignature

    try:
        booking = orientation_bills.read_checkout_token(token)
    except (BadSignature, OrientationBooking.DoesNotExist):
        return render(
            request, "hub/orientation_bill.html", {**_get_hub_context(request), "state": "invalid"}, status=400
        )
    member = _get_member(request)
    if member is None or booking.member_id != member.pk:
        raise Http404("No such bill.")
    if booking.bill_is_unpaid and orientation_bills.reconcile_landed_checkout(booking) == "paid":
        booking.refresh_from_db()
    context = _bill_context(booking)
    if booking.bill_is_unpaid:
        context["state"] = "pending"
    return render(request, "hub/orientation_bill.html", {**_get_hub_context(request), **context})


@login_required
def hub_orientation_bill_checkout_cancelled(request: HttpRequest, token: str) -> HttpResponse:
    """The Stripe ``cancel_url`` landing for a bill (#1111): it is still due; back to its page."""
    from django.core.signing import BadSignature

    try:
        booking = orientation_bills.read_checkout_token(token)
    except (BadSignature, OrientationBooking.DoesNotExist):
        return redirect("hub_home")
    member = _get_member(request)
    if member is None or booking.member_id != member.pk:
        raise Http404("No such bill.")
    if booking.bill_is_unpaid:
        messages.info(request, "Your bill is still due. Pay it from the Pay button any time.")
    return redirect("hub_orientation_bill", booking_pk=booking.pk)


def _bill_context(booking: OrientationBooking) -> dict[str, Any]:
    """What the bill page reads: the booking, its state, what it is for and who ran it."""
    return {
        "bill": booking,
        "state": booking.bill_state,
        "bill_item": orientation_bills.item_label(booking),
        "bill_oriented_by": orientation_bills.oriented_by_name(booking),
    }


# --- Resend Bill and Cancel Bill on the Bookings tab (#1111 part 2) ------------------------


def _bill_for_staff(request: HttpRequest, booking_pk: int) -> tuple[OrientationBooking, HttpResponse | None]:
    """A billed booking and the type gate's refusal, if any: whoever may record for the type may act on its bill."""
    booking = get_object_or_404(orientation_bills.bills(), pk=booking_pk)
    return booking, _require_can_run_orientation_type(request, booking.orientation_type)


def _bills_landing(request: HttpRequest) -> str:
    """Where Resend and Cancel land: the posted ``next``, else the tab's Past list, where a bill's row is."""
    return _safe_next(request, f"{_orientation_bookings_tab()}&show=past")


@login_required
@require_POST
def hub_orientation_bill_resend(request: HttpRequest, booking_pk: int) -> HttpResponse:
    """Resend Bill (#1111 part 2): the same bill email again, while it is owed.

    Gated like recording (:func:`_require_can_run_orientation_type`, admins included); a paid or
    cancelled bill is refused with the reason and sends nothing.
    """
    booking, forbidden = _bill_for_staff(request, booking_pk)
    if forbidden is not None:
        return forbidden
    try:
        orientation_bills.resend_bill(booking)
    except OrientationError as exc:
        messages.error(request, str(exc))
        return redirect(_bills_landing(request))
    messages.success(request, f"Sent the {booking.bill_amount_display} bill to {booking.member.display_name} again.")
    return redirect(_bills_landing(request))


@login_required
@require_POST
def hub_orientation_bill_cancel(request: HttpRequest, booking_pk: int) -> HttpResponse:
    """Cancel Bill (#1111 part 2): nothing is owed any more; the member stays oriented and is not emailed.

    Gated like recording, admins included; a paid or cancelled bill is refused and nothing changes.
    """
    booking, forbidden = _bill_for_staff(request, booking_pk)
    if forbidden is not None:
        return forbidden
    try:
        orientation_bills.cancel_bill(booking, actor=cast(User, request.user))
    except OrientationError as exc:
        messages.error(request, str(exc))
        return redirect(_bills_landing(request))
    messages.success(request, f"Cancelled the {booking.bill_amount_display} bill for {booking.member.display_name}.")
    return redirect(_bills_landing(request))
