"""The HQ page and its edit pages (Track C, part 1), the volunteer sign-up page (part 2), Vendor Tables (Track D,
part 3) and the PL Ledger's budget (Track D, part 6a), money in (Track D, part 6b), and team and tally (Track D, part
6c). #1307 removed the wine wall, the Marketing and Sponsorship Trackers, the Market Check-in and the flyer page.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.http import require_GET, require_POST

from core.abuse_limits import record_keyed_attempt
from core.htmx import wants_fragment
from hq import services
from hq.access import (
    actual_member,
    hq_gate,
    hub_or_404,
    officers,
    viewer_member,
)
from hq.exports import (
    stream_bar_sales_csv,
    stream_ledger_csv,
    stream_people_csv,
    stream_roster_csv,
    stream_staff_time_csv,
    stream_vendor_tables_csv,
)
from hq.forms import (
    LEDGER_TEAM_LIST_ID,
    RECEIPT_NOT_TAKEN,
    BarBatchForm,
    BarQuickForm,
    BudgetLineEditForm,
    BudgetLineForm,
    DoorCountForm,
    GuildDemoAddForm,
    GuildDemoEditForm,
    HubCardForm,
    HubForm,
    DrinkPricesForm,
    HubGoalFormSet,
    LaunchTaskEditForm,
    LaunchTaskForm,
    LedgerApprovalForm,
    LedgerEntryForm,
    ReceiptForm,
    StaffTimeForm,
    VendorFloorForm,
    VendorImportForm,
    VendorDiscordChannelForm,
    VendorTableEditForm,
    VendorTableForm,
    VolunteerInfoForm,
    VolunteerShiftForm,
    VolunteerShiftFormSet,
)
from hq.holiday_bizarre import (
    DOOR_CLICKER_HINT,
    DOOR_GUEST_LIST,
    LEDGER_FOOTER,
    LEDGER_INTRO,
    LEDGER_RECEIPT_HINT,
    LEDGER_TITLE,
    NO_DOUBLE_ENTRY_TITLE,
    TALLY_EYEBROW,
    TALLY_TITLE,
    TALLY_TITLE_GOLD,
    VENDOR_SETUP_NOTE,
)
from hq.models import (
    DOOR_STEPS,
    RECEIPT_KINDS,
    BudgetLine,
    EventHub,
    GuildDemo,
    HubCard,
    LaunchTask,
    LedgerEntry,
    LedgerSettings,
    ShiftSignup,
    VendorTable,
    VolunteerShift,
)
from hq.services import VolunteerBand, VolunteerInfo, goal_rows
from hq import vendor_discord
from hq.vendor_import import ImportRefused
from hub.toast import trigger_toast
from hub.view_as import has_events_admin_access
from membership.models import Guild

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.forms import BaseInlineFormSet

    # The shifts formset's type: the factory's result is a value, so the view names the generic it builds.
    ShiftFormSet = BaseInlineFormSet[VolunteerShift, EventHub, VolunteerShiftForm]


@hq_gate()
def index(request: HttpRequest) -> HttpResponse:
    """``/hq/``: the open hub's page. With none open, the newest closed hub, else the Load Lee's HQ page."""
    hub = EventHub.objects.open() or EventHub.objects.order_by("-created_at").first()
    return redirect(hub.get_absolute_url() if hub is not None else "hq:setup")


@hq_gate()
def hub_page(request: HttpRequest, slug: str) -> HttpResponse:
    """The HQ page: every door, for whoever runs the HQ, a hidden one with its Hidden chip."""
    hub = hub_or_404(slug)
    cards = list(hub.cards.all())
    return render(
        request,
        "hq/page.html",
        {
            "hub": hub,
            "cards": cards,
            "goals": goal_rows(hub),
            "countdown": hub.countdown_parts(timezone.now()),
        },
    )


@hq_gate()
def setup(request: HttpRequest) -> HttpResponse:
    """``/hq/new/``: load Lee's HQ (her doors, goals and shifts from :mod:`hq.holiday_bizarre`) with one button.

    There is no blank form: the HQ is always Lee's. Once a hub exists this page sends the viewer to it.
    """
    existing = EventHub.objects.order_by("-created_at").first()
    if existing is not None:
        return redirect(existing.get_absolute_url())
    if request.method == "POST":
        from hq.management.commands.seed_holiday_bizarre import seed

        with transaction.atomic():
            report = seed()
        messages.success(
            request,
            "Lee's HQ is loaded. Edit HQ links the calendar event.",
        )
        return redirect(report.hub.get_absolute_url())
    return render(request, "hq/setup.html")


@hq_gate()
def edit(request: HttpRequest, slug: str) -> HttpResponse:
    """The hub form with its goals."""
    return _hub_editor(request, get_object_or_404(EventHub, slug=slug))


def _hub_editor(request: HttpRequest, hub: EventHub) -> HttpResponse:
    if request.method == "POST":
        form = HubForm(request.POST, request.FILES, instance=hub)
        formset = HubGoalFormSet(request.POST, instance=hub, prefix="goals")
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                saved = form.save()
                formset.save()
            messages.success(request, "HQ saved.")
            return redirect(saved.get_absolute_url())
    else:
        form = HubForm(instance=hub)
        formset = HubGoalFormSet(instance=hub, prefix="goals")
    return render(
        request,
        "hq/edit.html",
        {"hub": hub, "form": form, "formset": formset, "max_upload_bytes": settings.MAX_UPLOAD_IMAGE_BYTES},
    )


@hq_gate()
@require_POST
def doors_update(request: HttpRequest, slug: str) -> HttpResponse:
    """Bring Lee's doors up to date on this hub: built doors adopt their placeholders, untouched words take her latest,
    a typed in Raised becomes the counted event cash, and a counted goal the hub lacks by kind is added
    (:func:`hq.services.seed_goals`)."""
    with transaction.atomic():
        # The row lock makes a double-submit wait for the first, so it finds the doors and goals made and adds none.
        hub = get_object_or_404(EventHub.objects.select_for_update(), slug=slug)
        seeded = hub.seed_default_cards()
        goals = services.seed_goals(hub)
        seeded.goals_added, seeded.goals_adopted = goals.added, goals.adopted
        summary = seeded.summary
    messages.success(request, summary)
    return redirect(hub.get_absolute_url())


@hq_gate()
@require_POST
def hero_delete(request: HttpRequest, slug: str) -> HttpResponse:
    hub = get_object_or_404(EventHub, slug=slug)
    hub.remove_hero()
    messages.success(request, "Hero image removed.")
    return redirect("hq:edit", slug=hub.slug)


@hq_gate()
def card_edit(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """One card: its words, hidden, and its place in the order.

    The volunteers card's page also carries the shifts formset; the one Save posts both.
    """
    hub = get_object_or_404(EventHub, slug=slug)
    card = get_object_or_404(hub.cards, pk=pk)
    data = request.POST if request.method == "POST" else None
    form = HubCardForm(data, instance=card)
    shifts = _shift_formset(hub, card, data)
    if request.method == "POST" and form.is_valid() and (shifts is None or shifts.is_valid()):
        with transaction.atomic():
            form.save()
            if shifts is not None:
                shifts.save()
        messages.success(request, "Card saved.")
        return redirect(hub.get_absolute_url())
    return render(request, "hq/card_edit.html", _card_context(hub, card, form, shifts))


def _shift_formset(hub: EventHub, card: HubCard, data: Any) -> ShiftFormSet | None:
    """The shifts formset for the volunteers card, None for any other kind."""
    if card.kind != HubCard.Kind.VOLUNTEERS:
        return None
    return VolunteerShiftFormSet(data, instance=hub, prefix="shifts", queryset=hub.shifts.with_counts())


def _card_context(hub: EventHub, card: HubCard, form: HubCardForm, shifts: ShiftFormSet | None) -> dict[str, Any]:
    return {
        "hub": hub,
        "card": card,
        "form": form,
        "shift_formset": shifts,
        "directions": HubCard.Direction,
    }


@hq_gate()
@require_POST
def card_move(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """Move a card one place up or down (``dir=up|down``) and land back on its edit page."""
    hub = get_object_or_404(EventHub, slug=slug)
    card = get_object_or_404(hub.cards, pk=pk)
    direction = request.POST.get("dir", "")
    if direction not in HubCard.Direction.values:
        return HttpResponseBadRequest("dir must be up or down.")
    card.move(direction)
    return redirect("hq:card_edit", slug=hub.slug, pk=card.pk)


# ---- Volunteer shifts (part 2) ----

_MESSAGE_LEVELS = {"success": messages.SUCCESS, "error": messages.ERROR, "info": messages.INFO}


def _volunteers_url(hub: EventHub) -> str:
    return reverse("hq:volunteers", args=[hub.slug])


@hq_gate(HubCard.Kind.VOLUNTEERS)
def volunteers(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The volunteer sign-up page: the band, Your Info, the shifts by phase, and the organisers' roster."""
    return _volunteers_page(request, hub, card, form=None)


def _volunteers_page(
    request: HttpRequest, hub: EventHub, card: HubCard, *, form: VolunteerInfoForm | None
) -> HttpResponse:
    member = viewer_member(request)
    person = services.person_for(request, member)
    if form is None:
        form = VolunteerInfoForm(initial=asdict(person) if person is not None else None)
    shifts = list(hub.shifts.with_counts())
    roster = services.roster_rows(hub)
    return render(
        request,
        "hq/volunteers.html",
        {
            "hub": hub,
            "card": card,
            "form": form,
            "person": person,
            "info_saved": VolunteerInfo.from_session(request) is not None,
            "is_guest": member is None,
            "band": VolunteerBand.of(shifts, services.volunteer_people(hub)),
            "phases": services.phase_groups(shifts),
            "mine": services.signed_up_shift_ids(hub, person.email) if person is not None else set(),
            "roster": roster,
            "roster_emails": services.roster_emails(roster),
        },
    )


def _door_url(hub: EventHub, kind: str) -> str:
    """Where a link to the ``kind`` door goes, or "" when the hub has no such card (or its page is not built)."""
    card = hub.cards.filter(kind=kind).first()
    return card.url if card is not None else ""


@require_POST
@hq_gate(HubCard.Kind.VOLUNTEERS)
def volunteer_info(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Save the Your Info step to the session. A honeypot hit reports success and saves nothing."""
    form = VolunteerInfoForm(request.POST)
    if not form.is_valid():
        return _volunteers_page(request, hub, card, form=form)
    if form.is_honeypot_hit:
        return redirect(_volunteers_url(hub))
    form.to_info().save_to(request)
    messages.success(request, "Your info is saved. Pick your shifts below.")
    return redirect(_volunteers_url(hub))


@require_POST
@hq_gate(HubCard.Kind.VOLUNTEERS)
def shift_signup(request: HttpRequest, *, hub: EventHub, card: HubCard, shift_pk: int) -> HttpResponse:
    """Sign Me Up on one shift, for the person in the session or the signed in member."""
    shift = get_object_or_404(hub.shifts, pk=shift_pk)
    member = viewer_member(request)
    person = services.person_for(request, member)
    if person is None:
        return _shift_answer(request, hub, shift, "Save your info in step 1 first.", "error")
    if member is None and not _guest_within_limits(request, services.RATE_SCOPE):
        message = "Too many sign-ups from this connection for now. Try again in an hour."
        return _shift_answer(request, hub, shift, message, "error")
    try:
        services.sign_up(shift, person, member=member)
    except services.AlreadySignedUp:
        return _shift_answer(request, hub, shift, f"You are already on {shift.role}.", "info")
    except services.ShiftFull:
        return _shift_answer(request, hub, shift, f"{shift.role} filled up just now.", "error")
    return _shift_answer(request, hub, shift, f"You are on {shift.role}. Thank you!", "success")


@require_POST
@hq_gate(HubCard.Kind.VOLUNTEERS)
def shift_remove(request: HttpRequest, *, hub: EventHub, card: HubCard, shift_pk: int) -> HttpResponse:
    """Remove Me from one shift."""
    shift = get_object_or_404(hub.shifts, pk=shift_pk)
    person = services.person_for(request, viewer_member(request))
    if person is None:
        return _shift_answer(request, hub, shift, "Save your info in step 1 first.", "error")
    try:
        services.remove(shift, person.email)
    except services.NotSignedUp:
        return _shift_answer(request, hub, shift, f"You were not on {shift.role}.", "info")
    return _shift_answer(request, hub, shift, f"You are off {shift.role}.", "success")


def _guest_within_limits(request: HttpRequest, scope: str) -> bool:
    """Count one guest write under ``scope`` (a shift sign-up or a bottle) against the connection; False past the cap."""
    allowed, _reason = record_keyed_attempt(
        scope,
        services.rate_key(request),
        hourly_limit=services.RATE_HOURLY_LIMIT,
        daily_limit=services.RATE_DAILY_LIMIT,
    )
    return allowed


def _shift_answer(request: HttpRequest, hub: EventHub, shift: VolunteerShift, message: str, kind: str) -> HttpResponse:
    """The refreshed row and band with a toast for an htmx post; a message and the page for a plain one."""
    if not wants_fragment(request):
        messages.add_message(request, _MESSAGE_LEVELS[kind], message)
        return redirect(_volunteers_url(hub))
    person = services.person_for(request, viewer_member(request))
    shifts = list(hub.shifts.with_counts())
    fresh = next(row for row in shifts if row.pk == shift.pk)
    mine = services.signed_up_shift_ids(hub, person.email) if person is not None else set()
    response = render(
        request,
        "hq/partials/_shift_answer.html",
        {
            "hub": hub,
            "shift": fresh,
            "person": person,
            "mine": mine,
            "band": VolunteerBand.of(shifts, services.volunteer_people(hub)),
        },
    )
    trigger_toast(response, message, kind)
    return response


@hq_gate(HubCard.Kind.VOLUNTEERS)
def volunteer_roster(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The roster on its own page: everyone signed up, their shifts, Copy emails and the CSV."""
    roster = services.roster_rows(hub)
    return render(
        request,
        "hq/volunteer_roster.html",
        {"hub": hub, "card": card, "roster": roster, "roster_emails": services.roster_emails(roster)},
    )


@hq_gate(HubCard.Kind.VOLUNTEERS)
def volunteer_roster_csv(request: HttpRequest, *, hub: EventHub, card: HubCard) -> StreamingHttpResponse:
    """The roster as a CSV download, one row per person per shift."""
    return stream_roster_csv(ShiftSignup.objects.filter(shift__hub=hub), filename_stem=hub.slug)


# ---- Vendor Tables (Track D, part 3) ----


def _vendors_url(hub: EventHub, show: str = services.VENDOR_FILTER_ALL) -> str:
    url = reverse("hq:vendors", args=[hub.slug])
    return url if show == services.VENDOR_FILTER_ALL else f"{url}?filter={show}"


@dataclass(frozen=True)
class VendorRow:
    """One row in the table requests: the request and its edit form (bound and refused, or fresh from the row)."""

    table: VendorTable
    form: VendorTableEditForm
    # A refusal the form does not carry: a save from a page older than the row (``VENDOR_STALE_SAVE``).
    error: str = ""


# A row's save whose ``updated_at`` no longer matches the database: someone else saved it since the page loaded.
VENDOR_STALE_SAVE = "Someone else changed this table. Your page has been refreshed; make your change again."


def _vendor_prefix(table: VendorTable) -> str:
    return f"vendor-{table.pk}"


def _vendor_form_id(table: VendorTable) -> str:
    return f"hq-vt-row-{table.pk}"


def _vendor_edit_form(table: VendorTable, guilds: list[Guild], data: Any = None) -> VendorTableEditForm:
    return VendorTableEditForm(
        data, instance=table, guilds=guilds, prefix=_vendor_prefix(table), form_id=_vendor_form_id(table)
    )


def _vendors_context(
    request: HttpRequest,
    hub: EventHub,
    card: HubCard,
    *,
    show: str,
    add_form: VendorTableForm | None = None,
    add_open: bool = False,
    floor_form: VendorFloorForm | None = None,
    discord_form: VendorDiscordChannelForm | None = None,
    discord: DiscordPanel | None = None,
    keep_pk: int | None = None,
    row_form: VendorTableEditForm | None = None,
    row_error: str = "",
) -> dict[str, Any]:
    """What the page and its board render. Organisers alone reach it, so every row carries its edit form.

    ``keep_pk`` is a request kept in view whatever the filter (the one just added or refused), ``row_form``
    that row's bound form when its Save was refused, and ``row_error`` a refusal shown on that row. ``discord`` is
    Check Discord's panel once it was pressed; the page loads without it, since it reads Discord.
    """
    guilds = services.active_guilds()
    band_context = _vendor_band_context(hub, card)
    band: services.VendorBand = band_context["band"]
    discord_channel_id = services.vendor_discord_channel_id(hub)
    rows = []
    for table in services.filtered_vendor_tables(hub, show, keep_pk=keep_pk):
        kept = table.pk == keep_pk
        form = row_form if row_form is not None and kept else _vendor_edit_form(table, guilds)
        rows.append(VendorRow(table=table, form=form, error=row_error if kept else ""))
    return {
        **band_context,
        "hub": hub,
        "card": card,
        "add_form": add_form if add_form is not None else VendorTableForm(guilds=guilds),
        # A refused add comes back open with its errors; a saved one stays open for the next.
        "add_open": add_open or add_form is not None,
        "rows": rows,
        "has_tables": band.total > 0,
        "show": show,
        "filters": services.VENDOR_FILTERS,
        "floor_form": floor_form if floor_form is not None else _floor_form(band),
        "discord_channel_id": discord_channel_id,
        "discord_form": (
            discord_form
            if discord_form is not None
            else VendorDiscordChannelForm(initial={"discord_channel_id": discord_channel_id})
        ),
        "discord": discord,
    }


def _vendor_band_context(hub: EventHub, card: HubCard, band: services.VendorBand | None = None) -> dict[str, Any]:
    """What the band renders: the counts, Lee's setup note, and who the requests wait on."""
    return {
        "band": band if band is not None else services.vendor_band(hub),
        "setup_note": VENDOR_SETUP_NOTE,
        "waiting_on": services.VENDOR_TEAM_FALLBACK,
    }


def _floor_form(band: services.VendorBand) -> VendorFloorForm:
    return VendorFloorForm(initial={"capacity": band.capacity, "map_note": band.map_note})


@hq_gate(HubCard.Kind.VENDOR_TABLES)
def vendors(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Vendor Tables: the band, + Add a Table Request, the table requests with ``?filter=``, and the space map's
    capacity. Organisers only (the card's gate, then a 403), because every row is a contact."""
    show = services.vendor_filter(request.GET.get("filter", ""))
    context = _vendors_context(request, hub, card, show=show)
    # An import's answer shows on the one page load after it.
    context["import_answer"] = request.session.pop(_vendor_import_key(hub), None)
    return render(request, "hq/vendors.html", context)


def _vendor_posted_filter(request: HttpRequest) -> str:
    return services.vendor_filter(request.POST.get("filter", ""))


@require_POST
@hq_gate(HubCard.Kind.VENDOR_TABLES)
def vendor_add(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """+ Add a Table Request, by the actual role: requested, or the waitlist while the map is full. Over htmx the
    board comes back with the form open and empty and a toast; a plain post lands back on the page with a message."""
    show = _vendor_posted_filter(request)
    form = VendorTableForm(request.POST, guilds=services.active_guilds())
    fragment = wants_fragment(request)
    template = "hq/partials/_vendor_board.html" if fragment else "hq/vendors.html"
    if not form.is_valid():
        return render(request, template, _vendors_context(request, hub, card, show=show, add_form=form))
    table = services.add_vendor_table(hub, form.to_fields(), member=actual_member(request))
    message = (
        "Added to the waitlist (tables are full)." if table.status == VendorTable.Status.WAITLIST else "Request added."
    )
    if not fragment:
        messages.success(request, message)
        return redirect(_vendors_url(hub, show))
    context = _vendors_context(request, hub, card, show=show, add_open=True, keep_pk=table.pk)
    response = render(request, template, context)
    trigger_toast(response, message, "success")
    return response


@require_POST
@hq_gate(HubCard.Kind.VENDOR_TABLES)
def vendor_edit(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Save one row, by the actual role: the row's form posts itself over htmx on every change (no Save button).
    The answer is all out of band, so no box is replaced while someone types: the row's state slot (the new
    ``updated_at`` stamp and the saved status) and the band, with a toast. A refused value comes back as the slot
    with its errors, and nothing is saved. A save that filled a member vendor's blank contact boxes from their record
    (``services.fill_vendor_contacts``) also sends back those boxes' inputs alone, so the row shows what was saved.

    A save from a page older than the row (its ``updated_at`` no longer matches) is refused whole: the whole row
    comes back as the database holds it, with :data:`VENDOR_STALE_SAVE`, so one organiser never overwrites another.
    """
    show = _vendor_posted_filter(request)
    guilds = services.active_guilds()
    fragment = wants_fragment(request)
    with transaction.atomic():
        # The row lock holds a second Save until the first is in, so the second sees the new stamp and is refused.
        table = get_object_or_404(
            hub.vendor_tables.select_related("guild", "created_by").select_for_update(of=("self",)), pk=pk
        )
        form = _vendor_edit_form(table, guilds, request.POST)
        if form.is_stale:
            if not fragment:
                context = _vendors_context(request, hub, card, show=show, keep_pk=table.pk, row_error=VENDOR_STALE_SAVE)
                return render(request, "hq/vendors.html", context)
            fresh = VendorRow(table=table, form=_vendor_edit_form(table, guilds), error=VENDOR_STALE_SAVE)
            return _vendor_row_answer(request, hub, card, fresh, whole=True)
        if not form.is_valid():
            if not fragment:
                context = _vendors_context(request, hub, card, show=show, keep_pk=table.pk, row_form=form)
                return render(request, "hq/vendors.html", context)
            # The refused form changed the row in memory; the row's own words come from the database.
            saved = hub.vendor_tables.select_related("guild", "created_by").get(pk=table.pk)
            return _vendor_row_answer(request, hub, card, VendorRow(table=saved, form=form))
        fields = form.to_fields()
        services.update_vendor_table(table, fields)
    if not fragment:
        messages.success(request, f"{table.name} saved.")
        return redirect(_vendors_url(hub, show))
    # The boxes posted blank that the save filled from a member vendor's record (#1049).
    filled = [name for name in services.VENDOR_CONTACT_FIELDS if not fields[name] and getattr(table, name)]
    row = VendorRow(table=table, form=_vendor_edit_form(table, guilds))
    response = _vendor_row_answer(request, hub, card, row, filled=filled)
    trigger_toast(response, "Saved.", "success")
    return response


def _vendor_row_answer(
    request: HttpRequest,
    hub: EventHub,
    card: HubCard,
    row: VendorRow,
    *,
    whole: bool = False,
    filled: Sequence[str] = (),
) -> HttpResponse:
    """The answer to a row's htmx autosave, all out of band: the row's state slot (or, with ``whole``, the whole row
    for a refused stale save), each box in ``filled`` (the contact boxes the save filled from a member vendor's
    record) as its input alone, then the band so the counts follow the row's status."""
    for name in filled:
        row.form.fields[name].widget.attrs["hx-swap-oob"] = "true"
    context = {
        **_vendor_band_context(hub, card),
        "hub": hub,
        "row": row,
        "filled": [row.form[name] for name in filled],
        "show": _vendor_posted_filter(request),
        "whole": whole,
    }
    return render(request, "hq/partials/_vendor_row_answer.html", context)


@require_POST
@hq_gate(HubCard.Kind.VENDOR_TABLES)
def vendor_delete(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Take a table request off the list (organisers, by the actual role), then the page with the filter."""
    table = get_object_or_404(hub.vendor_tables, pk=pk)
    table.delete()
    messages.success(request, f"{table.name} is off the list.")
    return redirect(_vendors_url(hub, _vendor_posted_filter(request)))


@require_POST
@hq_gate(HubCard.Kind.VENDOR_TABLES)
def vendor_floor(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Save the space map's capacity and note, by the actual role. Over htmx the capacity form comes back with the
    band out of band and a toast; a plain post lands back on the page with a message."""
    form = VendorFloorForm(request.POST)
    fragment = wants_fragment(request)
    if not form.is_valid():
        if not fragment:
            context = _vendors_context(request, hub, card, show=services.VENDOR_FILTER_ALL, floor_form=form)
            return render(request, "hq/vendors.html", context)
        return _vendor_floor_answer(request, hub, card, form)
    services.set_vendor_floor(hub, capacity=form.cleaned_data["capacity"], map_note=form.cleaned_data["map_note"])
    if not fragment:
        messages.success(request, "Capacity saved.")
        return redirect(_vendors_url(hub))
    band = services.vendor_band(hub)
    response = _vendor_floor_answer(request, hub, card, _floor_form(band), band=band)
    trigger_toast(response, "Capacity saved.", "success")
    return response


def _vendor_floor_answer(
    request: HttpRequest,
    hub: EventHub,
    card: HubCard,
    form: VendorFloorForm,
    *,
    band: services.VendorBand | None = None,
) -> HttpResponse:
    """The capacity form, still open, for an htmx post, with the band out of band so space left follows the map."""
    context = {**_vendor_band_context(hub, card, band), "hub": hub, "floor_form": form}
    return render(request, "hq/partials/_vendor_floor_answer.html", context)


# The most problems an import's answer lists; past it the rest are counted ("And 4 more.").
VENDOR_IMPORT_PROBLEMS_SHOWN = 10


def _vendor_import_key(hub: EventHub) -> str:
    return f"hq_vendor_import:{hub.pk}"


@require_POST
@hq_gate(HubCard.Kind.VENDOR_TABLES)
def vendor_import(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Import CSV, by the actual role: a plain multipart post that adds and updates table requests from a file in
    the columns of Kate's sheet, then lands back on the page.

    The answer is one toast with the counts, and the same counts with each problem by row ("Row 7: ...", the first
    ten, then how many more) in a panel over the list on the next page load, kept in the session for that one
    load. A file that cannot be read at all is refused whole, with nothing written, and the panel says why.
    """
    form = VendorImportForm(request.POST, request.FILES)
    refusal = ""
    if not form.is_valid():
        refusal = str(form.errors["file"][0])
    else:
        try:
            result = services.import_vendor_tables(hub, form.cleaned_data["file"], member=actual_member(request))
        except ImportRefused as exc:
            refusal = str(exc)
    if refusal:
        messages.error(request, refusal)
        request.session[_vendor_import_key(hub)] = {"summary": refusal, "refused": True, "problems": [], "more": 0}
        return redirect(_vendors_url(hub))
    summary = services.import_summary(result)
    if result.problems:
        messages.warning(request, summary)
    else:
        messages.success(request, summary)
    shown = [str(problem) for problem in result.problems[:VENDOR_IMPORT_PROBLEMS_SHOWN]]
    request.session[_vendor_import_key(hub)] = {
        "summary": summary,
        "refused": False,
        "problems": shown,
        "more": len(result.problems) - len(shown),
    }
    return redirect(_vendors_url(hub))


# ---- Check Discord (#1014) ----


@dataclass(frozen=True)
class DiscordPanel:
    """Check Discord's answer: the channel as it is named, who is new there, whether nicknames could be read, or the
    one line that says why there is no list."""

    label: str
    posters: list[vendor_discord.Poster] = field(default_factory=list)
    error: str = ""
    nicknames_read: bool = True


# The toast when a poster was dealt with in another tab (or is on the list) by the time Add or Skip was pressed.
DISCORD_POSTER_GONE = "Someone already added or skipped them. The list is up to date."
DISCORD_NO_CHANNEL = "There is no Discord channel to check. An admin sets it on this page."
# Add or Skip with no kept read: the check ran out (or the channel changed), so nothing is written.
DISCORD_STALE = "This list is out of date. Press Check Discord to read the channel again."
# A plain post's message level for each toast level.
_MESSAGE_LEVELS = {"success": messages.SUCCESS, "info": messages.INFO, "error": messages.ERROR}


def _discord_panel(hub: EventHub, read: vendor_discord.ChannelRead) -> DiscordPanel:
    return DiscordPanel(
        label=read.label, posters=services.discord_posters(hub, read), nicknames_read=read.nicknames_read
    )


def _check_discord(hub: EventHub) -> DiscordPanel:
    """Read ``hub``'s Discord channel once (``vendor_discord.read_channel``, through the module so specs can replace
    it) and keep the read for Add and Skip; the panel, or why the channel could not be read."""
    channel_id = services.vendor_discord_channel_id(hub)
    if not channel_id:
        return DiscordPanel(label="", error=DISCORD_NO_CHANNEL)
    try:
        read = vendor_discord.read_channel(channel_id)
    except vendor_discord.DiscordUnreadable as exc:
        return DiscordPanel(label=f"#{channel_id}", error=f"Discord could not be read. {exc}")
    services.keep_discord_read(hub, read)
    return _discord_panel(hub, read)


@require_GET
@hq_gate(HubCard.Kind.VENDOR_TABLES)
def vendor_discord_check(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Check Discord, by the actual role: read the hub's channel now and list each new poster with Add as Request
    and Skip. Over htmx the panel alone comes back into the list; without it, the page with the panel. The list's
    filter rides along, so Add's answer keeps it."""
    show = services.vendor_filter(request.GET.get("filter", ""))
    panel = _check_discord(hub)
    if wants_fragment(request):
        return render(request, "hq/partials/_vendor_discord.html", {"hub": hub, "discord": panel, "show": show})
    return render(request, "hq/vendors.html", _vendors_context(request, hub, card, show=show, discord=panel))


@dataclass(frozen=True)
class _DiscordPress:
    """What an Add or Skip did: the panel after it, the toast, and the service's answer (None when nothing was
    written)."""

    panel: DiscordPanel
    message: str
    level: str
    result: Any = None


def _discord_press(
    request: HttpRequest, hub: EventHub, press: Callable[[vendor_discord.ChannelRead, str, list[str]], Any]
) -> _DiscordPress:
    """Run ``press`` (Add or Skip) on the read the last Check Discord kept, never reading Discord again, with the
    poster and the post ids the form sent. With no kept read of the hub's channel nothing is written and the panel
    says to check again; a poster someone else dealt with first is :data:`DISCORD_POSTER_GONE`."""
    channel_id = services.vendor_discord_channel_id(hub)
    read = services.kept_discord_read(hub, channel_id)
    if read is None:
        label = f"#{channel_id}" if channel_id else ""
        return _DiscordPress(DiscordPanel(label=label, error=DISCORD_STALE), DISCORD_STALE, "info")
    services.keep_discord_read(hub, read)
    try:
        result = press(read, request.POST.get("author", ""), request.POST.getlist("message"))
    except services.DiscordPosterGone:
        return _DiscordPress(_discord_panel(hub, read), DISCORD_POSTER_GONE, "info")
    return _DiscordPress(_discord_panel(hub, read), "", "success", result)


@require_POST
@hq_gate(HubCard.Kind.VENDOR_TABLES)
def vendor_discord_add(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Add as Request, by the actual role: on the read the check kept, the poster becomes a table request (+ Add's
    capacity rule), unless they were added or skipped first. Over htmx the board comes back with the list's filter,
    the new row in view, the counts moved and the panel without them, and a toast; a plain post renders the page."""
    show = _vendor_posted_filter(request)
    member = actual_member(request)
    done = _discord_press(
        request,
        hub,
        lambda read, author_id, message_ids: services.add_discord_poster(
            hub, read, author_id=author_id, message_ids=message_ids, member=member
        ),
    )
    table: VendorTable | None = done.result
    message = done.message
    if table is not None:
        waitlisted = table.status == VendorTable.Status.WAITLIST
        message = f"{table.name} added to the waitlist (tables are full)." if waitlisted else f"{table.name} added."
    keep_pk = table.pk if table is not None else None
    context = _vendors_context(request, hub, card, show=show, keep_pk=keep_pk, discord=done.panel)
    if not wants_fragment(request):
        messages.add_message(request, _MESSAGE_LEVELS[done.level], message)
        return render(request, "hq/vendors.html", context)
    response = render(request, "hq/partials/_vendor_board.html", context)
    trigger_toast(response, message, done.level)
    return response


@require_POST
@hq_gate(HubCard.Kind.VENDOR_TABLES)
def vendor_discord_skip(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Skip, by the actual role: on the read the check kept, the poster's posts that were shown are recorded as
    skipped, so they leave Check Discord until they post again. Over htmx the panel comes back without them, with a
    toast; a plain post renders the page."""
    show = _vendor_posted_filter(request)
    member = actual_member(request)
    done = _discord_press(
        request,
        hub,
        lambda read, author_id, message_ids: services.skip_discord_poster(
            hub, read, author_id=author_id, message_ids=message_ids, member=member
        ),
    )
    message = f"Skipped {done.result.name}." if done.result is not None else done.message
    if not wants_fragment(request):
        messages.add_message(request, _MESSAGE_LEVELS[done.level], message)
        return render(request, "hq/vendors.html", _vendors_context(request, hub, card, show=show, discord=done.panel))
    response = render(request, "hq/partials/_vendor_discord.html", {"hub": hub, "discord": done.panel, "show": show})
    trigger_toast(response, message, done.level)
    return response


@require_POST
@hq_gate(HubCard.Kind.VENDOR_TABLES)
def vendor_discord_channel(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Save the Discord channel Check Discord reads, by the actual role.
    An id that is not digits is refused and the page comes back with the error; a blank one hides Check Discord."""
    form = VendorDiscordChannelForm(request.POST)
    if not form.is_valid():
        context = _vendors_context(request, hub, card, show=services.VENDOR_FILTER_ALL, discord_form=form)
        return render(request, "hq/vendors.html", context)
    channel_id = form.cleaned_data["discord_channel_id"]
    services.set_vendor_discord_channel(hub, channel_id)
    messages.success(request, "Discord channel saved." if channel_id else "Discord channel removed.")
    return redirect(_vendors_url(hub))


@hq_gate(HubCard.Kind.VENDOR_TABLES)
def vendors_csv(request: HttpRequest, *, hub: EventHub, card: HubCard) -> StreamingHttpResponse:
    """Every table request as a CSV download, one row each, in list order."""
    return stream_vendor_tables_csv(hub.vendor_tables.select_related("guild", "created_by"), filename_stem=hub.slug)


# ---- PL Ledger, budget (Track D, part 6a) ----


def _ledger_url(hub: EventHub, show: str = services.LEDGER_FILTER_ALL) -> str:
    url = reverse("hq:ledger", args=[hub.slug])
    return url if show == services.LEDGER_FILTER_ALL else f"{url}?filter={show}"


# A line's Save whose ``updated_at`` no longer matches the database: someone else saved it since the page loaded.
LINE_STALE_SAVE = "Someone else changed this line. Your page has been refreshed; make your change again."

# The approval's Save whose approval no longer matches the database: someone else saved it since the page loaded.
APPROVAL_STALE_SAVE = "Someone else changed the approval. Your page has been refreshed; make your change again."


@dataclass(frozen=True)
class LedgerLineRow:
    """One budget line in the table: its totals and its edit form (bound and refused, or fresh from the line)."""

    totals: services.LineTotals
    form: BudgetLineEditForm
    # A refusal the form does not carry: a Save from a page older than the line (``LINE_STALE_SAVE``).
    error: str = ""
    # Whether the edit row renders open: a refused Save keeps it open.
    open: bool = False


@dataclass(frozen=True)
class LedgerEntryRow:
    """One entry in the ledger: the entry, its Type chip's words, and the Add receipt form when it needs one."""

    entry: LedgerEntry
    kind_label: str
    receipt_form: ReceiptForm | None


def _line_prefix(line: BudgetLine) -> str:
    return f"line-{line.pk}"


def _line_edit_form(line: BudgetLine, data: Any = None) -> BudgetLineEditForm:
    return BudgetLineEditForm(data, instance=line, prefix=_line_prefix(line))


def _ledger_posted_filter(request: HttpRequest) -> str:
    return services.ledger_filter(request.POST.get("filter", ""))


def _entry_form(band: services.LedgerBand, data: Any = None, files: Any = None, **kwargs: Any) -> LedgerEntryForm:
    return LedgerEntryForm(
        data, files, lines=[totals.line for totals in band.lines], approval=band.approval, prefix="entry", **kwargs
    )


def _fresh_entry_form(band: services.LedgerBand, initial: dict[str, Any] | None = None) -> LedgerEntryForm:
    start = {"kind": LedgerEntry.Kind.SPONSORED, "date": timezone.localdate()}
    return _entry_form(band, initial={**start, **(initial or {})})


def _line_rows(
    band: services.LedgerBand, *, keep_pk: int | None = None, form: BudgetLineEditForm | None = None, error: str = ""
) -> list[LedgerLineRow]:
    rows = []
    for totals in band.lines:
        kept = totals.line.pk == keep_pk
        rows.append(
            LedgerLineRow(
                totals=totals,
                form=form if form is not None and kept else _line_edit_form(totals.line),
                error=error if kept else "",
                open=kept and (form is not None or bool(error)),
            )
        )
    return rows


def _entry_rows(
    hub: EventHub, band: services.LedgerBand, show: str, *, receipt_form: ReceiptForm | None = None
) -> list[LedgerEntryRow]:
    rows = []
    for entry in services.filtered_entries(hub, show):
        form = None
        if entry.needs_receipt:
            form = receipt_form if receipt_form is not None and receipt_form.entry.pk == entry.pk else None
            form = form if form is not None else ReceiptForm(entry=entry)
        rows.append(
            LedgerEntryRow(
                entry=entry, kind_label=services.ledger_kind_short(entry.kind, band.approval), receipt_form=form
            )
        )
    return rows


def _ledger_entries_context(
    hub: EventHub, band: services.LedgerBand, show: str, *, receipt_form: ReceiptForm | None = None
) -> dict[str, Any]:
    """What the Ledger section renders: the filters, the entries in view and whether the ledger has any at all."""
    return {
        "hub": hub,
        "show": show,
        "filters": services.ledger_filters(band.approval),
        "entry_rows": _entry_rows(hub, band, show, receipt_form=receipt_form),
        "has_entries": band.entries > 0,
        "max_image_bytes": settings.MAX_UPLOAD_IMAGE_BYTES,
        "max_pdf_bytes": settings.MAX_UPLOAD_DOCUMENT_BYTES,
    }


def _ledger_board_context(
    hub: EventHub, band: services.LedgerBand, doors: services.DoorFigures | None = None
) -> dict[str, Any]:
    """What the board summary renders: the band, Lee's paragraph and the text Copy board summary copies."""
    doors = doors if doors is not None else services.door_figures(hub, band)
    return {
        "hub": hub,
        "band": band,
        "board_line": services.board_line(band, doors),
        "board_summary": services.board_summary(band, doors),
    }


def _ledger_context(
    request: HttpRequest,
    hub: EventHub,
    card: HubCard,
    *,
    show: str,
    approval_form: LedgerApprovalForm | None = None,
    approval_error: str = "",
    line_form: BudgetLineForm | None = None,
    line_open: bool = False,
    entry_form: LedgerEntryForm | None = None,
    keep_line: int | None = None,
    line_edit: BudgetLineEditForm | None = None,
    line_error: str = "",
    receipt_form: ReceiptForm | None = None,
    bar: dict[str, Any] | None = None,
    launch: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
    guilds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What the page renders. Organisers alone reach it, so every section carries its forms.

    ``keep_line`` is a line whose refused edit (``line_edit``, or ``line_error`` for a stale Save) renders open in its
    row; ``receipt_form`` a refused receipt upload shown on its entry's row; ``approval_error`` a stale approval Save,
    shown over a fresh approval form that renders open. ``bar`` carries a refused form of the Drink sales section into
    :func:`_bar_context`, and ``launch``, ``staff`` and
    ``guilds`` one of the launch checklist, Staff time or Guild demos (D6c) into theirs.
    """
    figures = _ledger_figures(request, hub)
    found, band, doors = figures.found, figures.band, figures.doors
    return {
        **_figures_context(request, hub, figures),
        **_ledger_entries_context(hub, band, show, receipt_form=receipt_form),
        **_bar_context(hub, found, doors.bar, **(bar or {})),
        **_people_context(hub, figures),
        **_launch_context(hub, totals=figures.launch, **(launch or {})),
        **_staff_context(hub, figures, **(staff or {})),
        **_guilds_context(hub, doors.guilds, **(guilds or {})),
        "team_names": _officer_names(),
        "door_hint": _door_hint(request, hub),
        "team_list_id": LEDGER_TEAM_LIST_ID,
        "card": card,
        "title": LEDGER_TITLE,
        "intro": LEDGER_INTRO,
        "receipt_hint": LEDGER_RECEIPT_HINT,
        "footer": LEDGER_FOOTER,
        # The section bar's Vendor tables link, when the hub has that door.
        "door_urls": figures.door_urls,
        "approval_form": approval_form
        if approval_form is not None
        else LedgerApprovalForm(found=found, initial=LedgerApprovalForm.initial_for(found)),
        "approval_error": approval_error,
        "line_form": line_form if line_form is not None else BudgetLineForm(),
        "line_open": line_open or line_form is not None,
        "line_rows": _line_rows(band, keep_pk=keep_line, form=line_edit, error=line_error),
        "entry_form": entry_form if entry_form is not None else _fresh_entry_form(band),
        "receipt_kinds": sorted(RECEIPT_KINDS),
    }


@hq_gate(HubCard.Kind.LEDGER)
def ledger(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The PL Ledger: the board summary, the budget lines, Log a Sponsor or Expense, the ledger with ``?filter=`` and
    the receipts report. Organisers only (the card's gate, then a 403), because it is the event's money."""
    show = services.ledger_filter(request.GET.get("filter", ""))
    return render(request, "hq/ledger.html", _ledger_context(request, hub, card, show=show))


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_approval(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Change the board's approval and its note, by the actual role. A plain post: the approval names the kinds, the
    filters and a column, so the page comes back whole. A refused one renders the page with the form open.

    The form carries the approval it was rendered with (``seen_*``, as the D6b settings forms do): a Save after someone
    else changed the approval (:class:`hq.services.StaleSave`) comes back with the form as the database holds it, with
    :data:`APPROVAL_STALE_SAVE`. A price save in between never makes it stale.
    """
    form = LedgerApprovalForm(request.POST, found=services.ledger_settings(hub))
    if not form.is_valid():
        context = _ledger_context(request, hub, card, show=services.LEDGER_FILTER_ALL, approval_form=form)
        return render(request, "hq/ledger.html", context)
    try:
        services.save_approval(hub, **form.to_fields(), seen=form.seen())
    except services.StaleSave:
        context = _ledger_context(
            request, hub, card, show=services.LEDGER_FILTER_ALL, approval_error=APPROVAL_STALE_SAVE
        )
        return render(request, "hq/ledger.html", context)
    messages.success(request, "Approval saved.")
    return redirect(_ledger_url(hub))


def _line_select_context(hub: EventHub, band: services.LedgerBand, chosen: int) -> dict[str, Any]:
    """The log form's Budget line select, sent out of band so a line added or renamed is offered at once, chosen."""
    return {"entry_line_form": _fresh_entry_form(band, {"line": chosen}), "entry_line_oob": True}


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_line_add(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Add a budget line, by the actual role. Over htmx the lines section comes back with the form open and empty,
    and the log form's line select out of band with the new line chosen; a plain post lands back with a message."""
    form = BudgetLineForm(request.POST)
    fragment = wants_fragment(request)
    show = _ledger_posted_filter(request)
    if not form.is_valid():
        if not fragment:
            return render(request, "hq/ledger.html", _ledger_context(request, hub, card, show=show, line_form=form))
        band = services.ledger_band(hub)
        context = {"hub": hub, "band": band, "line_rows": _line_rows(band), "line_form": form, "line_open": True}
        return render(request, "hq/partials/_ledger_lines.html", {**context, "show": show})
    line = services.add_budget_line(hub, form.to_fields())
    if not fragment:
        messages.success(request, "Line added.")
        return redirect(_ledger_url(hub, show))
    band = services.ledger_band(hub)
    context = {
        "hub": hub,
        "band": band,
        "show": show,
        "line_rows": _line_rows(band),
        "line_form": BudgetLineForm(),
        "line_open": True,
        **_line_select_context(hub, band, line.pk),
    }
    response = render(request, "hq/partials/_ledger_lines_answer.html", context)
    trigger_toast(response, "Line added.", "success")
    return response


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_line_edit(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Save one budget line, by the actual role. Over htmx only that line comes back, with the totals, the log form's
    line select, the ledger, the Drink sales tiles, the goals and the board panel's figures out of band (they name the
    line, or read the bar's cost); a refused Save comes back open with its errors.

    A Save from a page older than the line (:class:`hq.services.StaleSave`) comes back with the line as the database
    holds it, with :data:`LINE_STALE_SAVE`.
    """
    show = _ledger_posted_filter(request)
    fragment = wants_fragment(request)
    line = get_object_or_404(hub.budget_lines, pk=pk)
    form = _line_edit_form(line, request.POST)
    valid = form.is_valid()
    stale = False
    if valid:
        try:
            line = services.update_budget_line(line, form.to_fields(), seen=form.seen())
        except services.StaleSave:
            valid, stale = False, True
    if not valid and not fragment:
        context = _ledger_context(
            request,
            hub,
            card,
            show=show,
            keep_line=line.pk,
            line_edit=None if stale else form,
            line_error=LINE_STALE_SAVE if stale else "",
        )
        return render(request, "hq/ledger.html", context)
    if valid and not fragment:
        messages.success(request, f"{line.name} saved.")
        return redirect(_ledger_url(hub, show))
    if not valid:
        band = services.ledger_band(hub)
        if stale:
            rows = _line_rows(band, keep_pk=line.pk, error=LINE_STALE_SAVE)
        else:
            rows = _line_rows(band, keep_pk=line.pk, form=form)
        row = next(row for row in rows if row.totals.line.pk == line.pk)
        return render(
            request,
            "hq/partials/_ledger_line_answer.html",
            {"hub": hub, "band": band, "show": show, "row": row, "line_oob": False},
        )
    figures = _ledger_figures(request, hub)
    band = figures.band
    row = next(row for row in _line_rows(band) if row.totals.line.pk == line.pk)
    context = {
        "hub": hub,
        "show": show,
        "row": row,
        "line_oob": True,
        **_line_select_context(hub, band, line.pk),
        **_ledger_entries_context(hub, band, show),
        # A line moved into or out of Drinks changes the bar's cost, so the Drink sales tiles follow it, and with them
        # the goals' Drink sales row and the board paragraph's drink sales (FIGURES_BUDGET).
        "bar": figures.doors.bar,
    }
    response = _figures_answer(
        request, hub, "hq/partials/_ledger_line_answer.html", context, figures=figures, parts=FIGURES_BUDGET
    )
    trigger_toast(response, "Saved.", "success")
    return response


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_line_delete(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Take a budget line off, by the actual role; a line with entries is refused by name. Then the page."""
    line = get_object_or_404(hub.budget_lines, pk=pk)
    try:
        services.delete_budget_line(line)
    except services.LineHasEntries as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{line.name} is off the budget.")
    return redirect(_ledger_url(hub, _ledger_posted_filter(request)))


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_entry_add(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Log a sponsor or expense, by the actual role. A multipart plain post (the form opts out of hx-boost, for the
    receipt); a refused one renders the page with the form and its errors."""
    show = _ledger_posted_filter(request)
    band = services.ledger_band(hub)
    form = _entry_form(band, request.POST, request.FILES)
    if not form.is_valid():
        return render(request, "hq/ledger.html", _ledger_context(request, hub, card, show=show, entry_form=form))
    entry = services.add_ledger_entry(hub, form.to_fields(), member=actual_member(request))
    messages.success(request, "Added with receipt." if entry.receipt else "Added to the ledger.")
    return redirect(_ledger_url(hub, show))


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_receipt(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Add (or replace) an entry's receipt, by the actual role: a multipart plain post from its ledger row. A sponsored
    entry takes none; a refused file renders the page with the error on that row."""
    show = _ledger_posted_filter(request)
    entry = get_object_or_404(hub.ledger_entries.select_related("line"), pk=pk)
    if not entry.takes_receipt:
        messages.error(request, RECEIPT_NOT_TAKEN)
        return redirect(_ledger_url(hub, show))
    form = ReceiptForm(request.POST, request.FILES, entry=entry)
    if not form.is_valid():
        return render(request, "hq/ledger.html", _ledger_context(request, hub, card, show=show, receipt_form=form))
    services.attach_receipt(entry, form.cleaned_data["receipt"])
    messages.success(request, "Receipt attached.")
    return redirect(_ledger_url(hub, show))


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_entry_delete(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Take an entry out of the ledger, with its receipt, by the actual role; then the page with the filter."""
    entry = get_object_or_404(hub.ledger_entries, pk=pk)
    entry.delete()
    messages.success(request, f"{entry.source} is out of the ledger.")
    return redirect(_ledger_url(hub, _ledger_posted_filter(request)))


@hq_gate(HubCard.Kind.LEDGER)
def ledger_csv(request: HttpRequest, *, hub: EventHub, card: HubCard) -> StreamingHttpResponse:
    """The whole ledger as a CSV download, oldest first, in Lee's columns."""
    entries = hub.ledger_entries.select_related("line").order_by("date", "created_at", "pk")
    return stream_ledger_csv(entries, approval=services.ledger_settings(hub).approval_words, filename_stem=hub.slug)


@hq_gate(HubCard.Kind.LEDGER)
def ledger_receipts(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The receipts report: every purchase and refund oldest first with its receipt (an image inline, a PDF as a link)
    under the approval's totals, a printable page for bookkeeping."""
    band = services.ledger_band(hub)
    entries = [
        LedgerEntryRow(entry=entry, kind_label=services.ledger_kind_short(entry.kind, band.approval), receipt_form=None)
        for entry in services.receipt_entries(hub)
    ]
    return render(
        request,
        "hq/ledger_receipts.html",
        {
            "hub": hub,
            "card": card,
            "band": band,
            "rows": entries,
            "missing": sum(1 for row in entries if not row.entry.receipt),
            "today": timezone.localdate(),
        },
    )


# ---- PL Ledger, money in (Track D, part 6b) ----


def _bar_url(hub: EventHub) -> str:
    return f"{reverse('hq:ledger', args=[hub.slug])}#hq-lg-bar"


def _bar_totals(hub: EventHub) -> services.BarTotals:
    found = services.ledger_settings(hub)
    return services.bar_totals(hub, found, cost_cents=services.ledger_band(hub, found).bar_cost_cents)


def _prices_form(found: LedgerSettings, data: Any = None) -> DrinkPricesForm:
    return DrinkPricesForm(data, found=found, prefix="prices", initial=DrinkPricesForm.initial_for(found))


def _batch_form(found: LedgerSettings, data: Any = None, initial: dict[str, Any] | None = None) -> BarBatchForm:
    return BarBatchForm(data, found=found, prefix="batch", initial=initial)


def _bar_context(
    hub: EventHub,
    found: LedgerSettings,
    bar: services.BarTotals,
    *,
    batch_form: BarBatchForm | None = None,
    prices_form: DrinkPricesForm | None = None,
    prices_error: str = "",
) -> dict[str, Any]:
    """What the Drink sales section renders: the tiles, the +1 buttons at the hub's prices, Paid by, Set drink prices,
    Log a batch and the sales newest first. A refused batch or price form comes back bound and open."""
    return {
        "bar": bar,
        "quick_buttons": services.quick_buttons(found, bar),
        "quick_form": BarQuickForm(prefix="quick"),
        "batch_form": batch_form if batch_form is not None else _batch_form(found),
        "batch_open": batch_form is not None,
        "prices_form": prices_form if prices_form is not None else _prices_form(found),
        "prices_open": prices_form is not None or bool(prices_error),
        "prices_error": prices_error,
        "bar_sales": list(hub.bar_sales.all()),
    }


def _bar_answer(request: HttpRequest, hub: EventHub, message: str, tone: str = "success") -> HttpResponse:
    """The htmx answer to a +1 tap or Undo last: the tiles, the buttons' counts and the sales table out of band, with
    the tally, the goals, the board panel's figures and Staff time's (each reads the bar's sales in the event cash),
    and a toast. Paid by stays as it is, so the next tap keeps the chosen way to pay."""
    figures = _ledger_figures(request, hub)
    bar = figures.doors.bar
    context = {
        "hub": hub,
        "bar": bar,
        "bar_sales": list(hub.bar_sales.all()),
        "quick_buttons": services.quick_buttons(figures.found, bar),
    }
    response = _figures_answer(request, hub, "hq/partials/_ledger_bar_answer.html", context, figures=figures)
    trigger_toast(response, message, tone)
    return response


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_bar_quick(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """One +1 tap on the night, by the actual role: one drink at the hub's price, paid as chosen. Over htmx the tiles
    and the table come back out of band with Lee's "Added 1 IPA (Card)."; a plain post lands back on the section."""
    form = BarQuickForm(request.POST, prefix="quick")
    fragment = wants_fragment(request)
    if not form.is_valid():
        refusal = "Choose a drink and how it was paid."
        if not fragment:
            messages.error(request, refusal)
            return redirect(_bar_url(hub))
        response = HttpResponse("")
        trigger_toast(response, refusal, "error")
        return response
    sale = services.log_quick_sale(
        hub, drink=form.cleaned_data["drink"], pay=form.cleaned_data["pay"], member=actual_member(request)
    )
    message = f"Added {sale.summary}."
    if not fragment:
        messages.success(request, message)
        return redirect(_bar_url(hub))
    return _bar_answer(request, hub, message)


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_bar_undo(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Undo last, by the actual role: takes back the newest +1 the signed in organiser tapped, never anyone else's and
    never a batch."""
    sale = services.undo_quick_sale(hub, actual_member(request))
    message = "Nothing of yours to undo." if sale is None else f"Took back {sale.summary}."
    tone = "info" if sale is None else "success"
    if not wants_fragment(request):
        (messages.info if sale is None else messages.success)(request, message)
        return redirect(_bar_url(hub))
    return _bar_answer(request, hub, message, tone)


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_bar_batch(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Log a batch, by the actual role. Over htmx the form comes back (open, its errors, or fresh keeping the drink and
    Paid by); a saved one sends the tiles and the table out of band with every figure the sales move
    (:data:`FIGURES_ALL`). A plain post lands back on the section."""
    found = services.ledger_settings(hub)
    form = _batch_form(found, request.POST)
    fragment = wants_fragment(request)
    if not form.is_valid():
        if not fragment:
            context = _ledger_context(request, hub, card, show=services.LEDGER_FILTER_ALL, bar={"batch_form": form})
            return render(request, "hq/ledger.html", context)
        return render(
            request, "hq/partials/_ledger_bar_batch.html", {"hub": hub, "batch_form": form, "batch_open": True}
        )
    services.add_bar_batch(hub, form.to_fields(), member=actual_member(request))
    if not fragment:
        messages.success(request, "Sales added.")
        return redirect(_bar_url(hub))
    figures = _ledger_figures(request, hub)
    context = {
        "hub": hub,
        "batch_form": _batch_form(figures.found, initial={key: form.cleaned_data[key] for key in ("drink", "pay")}),
        "batch_open": True,
        "bar": figures.doors.bar,
        "bar_sales": list(hub.bar_sales.all()),
    }
    response = _figures_answer(request, hub, "hq/partials/_ledger_bar_batch_answer.html", context, figures=figures)
    trigger_toast(response, "Sales added.", "success")
    return response


def _prices_refused(
    request: HttpRequest, hub: EventHub, card: HubCard, form: DrinkPricesForm, *, error: str
) -> HttpResponse:
    """A refused Set drink prices: the page with the form open, or over htmx the form alone, with ``error`` (a stale
    save's) or the form's own errors."""
    if not wants_fragment(request):
        bar = {"prices_form": form, "prices_error": error}
        context = _ledger_context(request, hub, card, show=services.LEDGER_FILTER_ALL, bar=bar)
        return render(request, "hq/ledger.html", context)
    return render(
        request,
        "hq/partials/_ledger_bar_prices.html",
        {"hub": hub, "prices_form": form, "prices_open": True, "prices_error": error},
    )


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_bar_prices(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Set drink prices and last year's bar sales, by the actual role. A save from a page older than the prices is
    refused (:data:`hq.services.SETTINGS_STALE_SAVE`) and the form comes back with what the database holds. Over htmx
    the form comes back with the +1 buttons, the tiles and the goals (the Drink sales row says 2025) out of band, so
    Paid by keeps its choice."""
    found = services.ledger_settings(hub)
    form = _prices_form(found, request.POST)
    fragment = wants_fragment(request)
    if not form.is_valid():
        return _prices_refused(request, hub, card, form, error="")
    try:
        saved = services.save_ledger_figures(hub, form.to_fields(), seen=form.seen())
    except services.StaleSave:
        refused = _prices_form(services.ledger_settings(hub))
        return _prices_refused(request, hub, card, refused, error=services.SETTINGS_STALE_SAVE)
    if not fragment:
        messages.success(request, "Prices saved.")
        return redirect(_bar_url(hub))
    figures = _ledger_figures(request, hub)
    bar = figures.doors.bar
    context = {
        "hub": hub,
        "prices_form": _prices_form(saved),
        "prices_open": False,
        "prices_error": "",
        "quick_buttons": services.quick_buttons(saved, bar),
        "bar": bar,
    }
    response = _figures_answer(
        request, hub, "hq/partials/_ledger_bar_prices_answer.html", context, figures=figures, parts=FIGURES_GOALS
    )
    trigger_toast(response, "Prices saved.", "success")
    return response


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_bar_delete(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Delete one sale from the bar, by the actual role; then the section."""
    sale = get_object_or_404(hub.bar_sales, pk=pk)
    sale.delete()
    messages.success(request, f"Deleted {sale.summary}.")
    return redirect(_bar_url(hub))


@hq_gate(HubCard.Kind.LEDGER)
def ledger_bar_csv(request: HttpRequest, *, hub: EventHub, card: HubCard) -> StreamingHttpResponse:
    """The drink sales as a CSV download, oldest first, with Lee's totals row last."""
    sales = hub.bar_sales.order_by("created_at", "pk")
    return stream_bar_sales_csv(sales, totals=_bar_totals(hub), filename_stem=hub.slug)


# ---- PL Ledger, team and tally (Track D, part 6c) ----

# A row's Save whose ``updated_at`` no longer matches the database: someone else saved it since the page loaded.
LAUNCH_STALE_SAVE = "Someone else changed this task. Your page has been refreshed; make your change again."
GUILD_STALE_SAVE = "Someone else changed this guild. Your page has been refreshed; make your change again."


def _ledger_section_url(hub: EventHub, anchor: str) -> str:
    return f"{reverse('hq:ledger', args=[hub.slug])}#{anchor}"


def _door_urls(hub: EventHub) -> dict[str, str]:
    """Where each door's page is, in one read of the cards: a ``HubCard.Kind`` to its page, for every live door with a
    page (:func:`_door_url`'s rule for all of them at once)."""
    return {card.kind: card.url for card in hub.cards.exclude(kind=HubCard.Kind.PLACEHOLDER) if card.url}


def _officer_names() -> list[str]:
    """The guild officers' display names, for the name suggestions (Who, Owner, Who's asking, Contacted by)."""
    return sorted({member.greeting_name for member in officers() if member.greeting_name.strip()})


@dataclass(frozen=True)
class LedgerFigures:
    """Every figure the ledger's tally, goals and board paragraph read, gathered once per request: the settings, the
    ledger's band, every other door's figures, the doors the hub has and the launch checklist."""

    found: LedgerSettings
    band: services.LedgerBand
    doors: services.DoorFigures
    door_urls: dict[str, str]
    launch: services.LaunchTotals


def _ledger_figures(request: HttpRequest, hub: EventHub) -> LedgerFigures:
    found = services.ledger_settings(hub)
    band = services.ledger_band(hub, found)
    return LedgerFigures(
        found=found,
        band=band,
        doors=services.door_figures(hub, band, found),
        door_urls=_door_urls(hub),
        launch=services.launch_totals(hub),
    )


def _figures_context(request: HttpRequest, hub: EventHub, figures: LedgerFigures) -> dict[str, Any]:
    """What the running tally, the goals and the board paragraph render, every figure read from its door."""
    doors = figures.doors
    return {
        **_ledger_board_context(hub, figures.band, doors),
        "tally": services.ledger_tally(hub, now=timezone.now(), doors=doors, door_urls=figures.door_urls),
        "tally_eyebrow": TALLY_EYEBROW,
        "tally_title": TALLY_TITLE,
        "tally_title_gold": TALLY_TITLE_GOLD,
        "goal_groups": services.ledger_goals(
            figures.band,
            doors,
            launch=figures.launch,
            door_urls=figures.door_urls,
        ),
        # Staff time's figures (hq/partials/_ledger_staff_figures.html): Raised per staff hour reads the event cash.
        "staff": doors.staff,
        # Change the goals is Edit HQ's goals formset.
        "edit_goals_url": f"{reverse('hq:edit', args=[hub.slug])}#hq-edit-goals",
    }


# The figures an htmx answer sends out of band (hq/partials/_ledger_figures_oob.html), by what its post moves: the
# running tally, the goals, the board panel's figures (its paragraph with them) and Staff time's figures (Raised per
# staff hour reads the event cash). Every post that moves a tallied figure sends all four; Set drink prices moves only
# the Drink sales goal's 2025 line, and a budget line's Save the bar's cost the goal and the paragraph read.
FIGURES_ALL = frozenset({"tally", "goals", "board", "staff"})
FIGURES_GOALS = frozenset({"goals"})
FIGURES_BUDGET = frozenset({"goals", "board"})


def _figures_answer(
    request: HttpRequest,
    hub: EventHub,
    template: str,
    context: dict[str, Any],
    *,
    figures: LedgerFigures | None = None,
    parts: frozenset[str] = FIGURES_ALL,
) -> HttpResponse:
    """An htmx answer that also sends ``parts`` of the figures out of band (:data:`FIGURES_ALL` by default), since
    each of them reads what the post just changed. ``figures`` is the request's one reading when the view has it."""
    figures = figures if figures is not None else _ledger_figures(request, hub)
    return render(request, template, {**context, **_figures_context(request, hub, figures), "figures_oob": parts})


def _door_hint(request: HttpRequest, hub: EventHub) -> str:
    """The door clicker's line: it counts walk-ins only, and an RSVP checks in on the Guest list, which links to the
    hub's event's Guest list tab in Manage Events when there is an event and the viewer may open Manage Events."""
    guest_list: str = DOOR_GUEST_LIST
    if hub.event_id is not None and has_events_admin_access(request):
        url = reverse("hub_admin_event_guests", args=[hub.event_id])
        guest_list = format_html('<a href="{}" class="pl-hq-lg__link" data-hq-lg-guest-list>{}</a>', url, guest_list)
    return format_html(DOOR_CLICKER_HINT, guest_list=guest_list)


def _event_day(hub: EventHub) -> str:
    """ " on Dec 4", the event's day in Lee's guild demos lede; blank with no event."""
    if hub.event is None:
        return ""
    day = timezone.localdate(hub.event.starts_at)
    return f" on {day:%b} {day.day}"


def _people_context(hub: EventHub, figures: LedgerFigures) -> dict[str, Any]:
    """What Attendance and Vendors renders: the attendance, the confirmed vendors from Vendor Tables, the door clicker
    and Lee's No double entry note."""
    return {
        "attendance": figures.doors.attendance,
        "people_vendors": figures.doors.vendors,
        "vendors_url": figures.door_urls.get(HubCard.Kind.VENDOR_TABLES, ""),
        "no_double_title": NO_DOUBLE_ENTRY_TITLE,
        "door_steps": DOOR_STEPS,
    }


@dataclass(frozen=True)
class LaunchRow:
    """One task on the launch checklist: the task, its edit form (bound and refused, or fresh), whether it is late on
    the Portland date, and any refusal the form lacks (a stale Save)."""

    task: LaunchTask
    form: LaunchTaskEditForm
    late: bool
    error: str = ""


def _launch_form(task: LaunchTask, data: Any = None) -> LaunchTaskEditForm:
    return LaunchTaskEditForm(
        data, instance=task, prefix=f"task-{task.pk}", form_id=f"hq-lg-task-{task.pk}", row_name=task.task
    )


def _launch_row(task: LaunchTask, form: LaunchTaskEditForm | None = None, error: str = "") -> LaunchRow:
    return LaunchRow(
        task=task,
        form=form if form is not None else _launch_form(task),
        late=task.is_late(timezone.localdate()),
        error=error,
    )


def _launch_context(
    hub: EventHub,
    *,
    totals: services.LaunchTotals,
    add_form: LaunchTaskForm | None = None,
    add_open: bool = False,
    keep_pk: int | None = None,
    row_form: LaunchTaskEditForm | None = None,
    row_error: str = "",
) -> dict[str, Any]:
    """What the launch checklist renders: Lee's summary, each task as a row of its own form, and Add a task (open when
    it was just used or refused). ``keep_pk`` is a task whose refused Save (``row_form``, or ``row_error``) shows."""
    rows = []
    for task in hub.launch_tasks.all():
        kept = task.pk == keep_pk
        rows.append(_launch_row(task, row_form if kept else None, row_error if kept else ""))
    return {
        "launch_rows": rows,
        "launch_totals": totals,
        "launch_form": add_form if add_form is not None else LaunchTaskForm(prefix="launch"),
        "launch_open": add_open or add_form is not None,
    }


def _staff_context(hub: EventHub, figures: LedgerFigures, *, form: StaffTimeForm | None = None) -> dict[str, Any]:
    """What Staff time renders: the hours by person and by area against the event cash, Log time (open when refused)
    and every entry with its Delete."""
    return {
        "staff": figures.doors.staff,
        "staff_entries": list(hub.staff_time.all()),
        "staff_form": form
        if form is not None
        else StaffTimeForm(prefix="staff", initial={"date": timezone.localdate()}),
        "staff_open": form is not None,
    }


@dataclass(frozen=True)
class GuildRow:
    """One guild on the Guild demos list: the row, its edit form (bound and refused, or fresh) and any refusal the form
    lacks (a stale Save)."""

    row: GuildDemo
    form: GuildDemoEditForm
    error: str = ""


def _guild_form(row: GuildDemo, data: Any = None) -> GuildDemoEditForm:
    return GuildDemoEditForm(
        data, instance=row, prefix=f"guild-{row.pk}", form_id=f"hq-lg-guild-{row.pk}", row_name=row.guild.name
    )


def _guilds_context(
    hub: EventHub,
    totals: services.GuildDemoTotals,
    *,
    add_form: GuildDemoAddForm | None = None,
    add_open: bool = False,
    keep_pk: int | None = None,
    row_form: GuildDemoEditForm | None = None,
    row_error: str = "",
) -> dict[str, Any]:
    """What Guild demos renders: Lee's summary, every guild on the list as a row of its own form, Add a guild (the
    active guilds not on it yet) and Add every active guild."""
    rows = []
    for row in hub.guild_demos.select_related("guild"):
        kept = row.pk == keep_pk
        form = row_form if row_form is not None and kept else _guild_form(row)
        rows.append(GuildRow(row=row, form=form, error=row_error if kept else ""))
    missing = services.guilds_to_add(hub)
    return {
        "guild_rows": rows,
        "guild_summary": totals.summary(_event_day(hub)),
        "guild_add_form": add_form if add_form is not None else GuildDemoAddForm(prefix="guild-add", guilds=missing),
        "guild_add_open": add_open or add_form is not None,
        "guilds_missing": len(missing),
    }


# The plain post's message for a door tap, by the toast's tone.
_MESSAGE_LEVELS_ADD = {"success": messages.success, "info": messages.info}


def _people_answer(request: HttpRequest, hub: EventHub, message: str, tone: str = "success") -> HttpResponse:
    """The htmx answer to a door tap (the clicker swaps nothing in place): the Attendance and vendors figures, the tally,
    the goals and the board paragraph, each out of band, and a toast."""
    figures = _ledger_figures(request, hub)
    context = {"hub": hub, **_people_context(hub, figures)}
    response = _figures_answer(request, hub, "hq/partials/_ledger_door_answer.html", context, figures=figures)
    trigger_toast(response, message, tone)
    return response


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_door(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """One tap on the door clicker, by the actual role: +1, +5 or +10 walk-ins, or Undo 1, which appends a -1 and is
    refused with nothing written once the walk-ins are at zero. Over htmx the figures come back out of band with a
    toast; a plain post lands back on the section with a message."""
    form = DoorCountForm(request.POST)
    fragment = wants_fragment(request)
    if not form.is_valid():
        refusal = "Choose +1, +5, +10 or Undo 1."
        if not fragment:
            messages.error(request, refusal)
            return redirect(_ledger_section_url(hub, "hq-lg-people"))
        response = HttpResponse("")
        trigger_toast(response, refusal, "error")
        return response
    delta: int = form.cleaned_data["delta"]
    try:
        services.count_at_door(hub, delta, member=actual_member(request))
    except services.NothingToUndo:
        message, tone = "Nothing to take back: the door count is at zero.", "info"
    else:
        message, tone = (f"Added {delta} at the door." if delta > 0 else "Took 1 back."), "success"
    if not fragment:
        _MESSAGE_LEVELS_ADD[tone](request, message)
        return redirect(_ledger_section_url(hub, "hq-lg-people"))
    return _people_answer(request, hub, message, tone)


@hq_gate(HubCard.Kind.LEDGER)
def ledger_people_csv(request: HttpRequest, *, hub: EventHub, card: HubCard) -> StreamingHttpResponse:
    """The confirmed vendors and the attendance as a CSV download, in Lee's columns."""
    return stream_people_csv(
        vendors=services.vendor_band(hub).confirmed,
        attendance=services.attendance(hub),
        filename_stem=hub.slug,
    )


def _launch_section(request: HttpRequest, hub: EventHub, message: str = "", **kwargs: Any) -> HttpResponse:
    """The launch checklist over htmx, with the figures out of band (the goals count its tasks) and a toast."""
    context = {
        "hub": hub,
        "team_list_id": LEDGER_TEAM_LIST_ID,
        **_launch_context(hub, totals=services.launch_totals(hub), **kwargs),
    }
    response = _figures_answer(request, hub, "hq/partials/_ledger_launch_answer.html", context)
    if message:
        trigger_toast(response, message, "success")
    return response


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_launch_add(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Add a task to the launch checklist, by the actual role. Over htmx the checklist comes back with Add a task open
    and empty; a refused one comes back with its errors, and nothing is written."""
    form = LaunchTaskForm(request.POST, prefix="launch")
    fragment = wants_fragment(request)
    if not form.is_valid():
        if not fragment:
            context = _ledger_context(request, hub, card, show=services.LEDGER_FILTER_ALL, launch={"add_form": form})
            return render(request, "hq/ledger.html", context)
        return _launch_section(request, hub, add_form=form)
    services.add_launch_task(hub, form.to_fields())
    if not fragment:
        messages.success(request, "Task added.")
        return redirect(_ledger_section_url(hub, "hq-lg-launch"))
    return _launch_section(request, hub, "Task added.", add_open=True)


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_launch_edit(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Save one task, by the actual role. Over htmx only that row comes back, with the checklist's summary and the
    figures out of band. A Save from a page older than the task (:class:`hq.services.StaleSave`) comes back as the
    database holds it with :data:`LAUNCH_STALE_SAVE`."""
    fragment = wants_fragment(request)
    task = get_object_or_404(hub.launch_tasks, pk=pk)
    form = _launch_form(task, request.POST)
    valid = form.is_valid()
    stale = False
    if valid:
        try:
            task = services.update_launch_task(task, form.to_fields(), seen=form.seen())
        except services.StaleSave:
            valid, stale = False, True
    if valid and not fragment:
        messages.success(request, f"{task.task} saved.")
        return redirect(_ledger_section_url(hub, "hq-lg-launch"))
    refusal: dict[str, Any] = {}
    if stale:
        refusal = {"keep_pk": task.pk, "row_error": LAUNCH_STALE_SAVE}
    elif not valid:
        refusal = {"keep_pk": task.pk, "row_form": form}
    if not fragment:
        context = _ledger_context(request, hub, card, show=services.LEDGER_FILTER_ALL, launch=refusal)
        return render(request, "hq/ledger.html", context)
    saved = hub.launch_tasks.get(pk=task.pk)
    if stale:
        row = _launch_row(saved, error=LAUNCH_STALE_SAVE)
    elif not valid:
        row = _launch_row(saved, form)
    else:
        row = _launch_row(saved)
    context = {
        "hub": hub,
        "row": row,
        "launch_totals": services.launch_totals(hub),
        "team_list_id": LEDGER_TEAM_LIST_ID,
    }
    response = _figures_answer(request, hub, "hq/partials/_ledger_launch_row_answer.html", context)
    if valid:
        trigger_toast(response, "Saved.", "success")
    return response


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_launch_delete(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Take a task off the launch checklist, by the actual role; then the section."""
    task = get_object_or_404(hub.launch_tasks, pk=pk)
    task.delete()
    messages.success(request, f"{task.task} is off the checklist.")
    return redirect(_ledger_section_url(hub, "hq-lg-launch"))


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_staff_add(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Log staff time, by the actual role. Over htmx the section comes back keeping Who, the date and the area for the
    next entry, with the figures out of band (the board paragraph says the staff time); a refused one writes nothing."""
    form = StaffTimeForm(request.POST, prefix="staff")
    fragment = wants_fragment(request)
    if not form.is_valid():
        if not fragment:
            context = _ledger_context(request, hub, card, show=services.LEDGER_FILTER_ALL, staff={"form": form})
            return render(request, "hq/ledger.html", context)
        figures = _ledger_figures(request, hub)
        context = {"hub": hub, "team_list_id": LEDGER_TEAM_LIST_ID, **_staff_context(hub, figures, form=form)}
        return render(request, "hq/partials/_ledger_staff_answer.html", context)
    entry = services.log_staff_time(hub, form.to_fields(), team=list(officers()), member=actual_member(request))
    message = f"Logged {entry.hours_words}."
    if not fragment:
        messages.success(request, message)
        return redirect(_ledger_section_url(hub, "hq-lg-staff"))
    figures = _ledger_figures(request, hub)
    fresh = StaffTimeForm(prefix="staff", initial=form.kept_for_next())
    context = {
        "hub": hub,
        "team_list_id": LEDGER_TEAM_LIST_ID,
        **_staff_context(hub, figures),
        "staff_form": fresh,
        "staff_open": True,
    }
    response = _figures_answer(request, hub, "hq/partials/_ledger_staff_answer.html", context, figures=figures)
    trigger_toast(response, message, "success")
    return response


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_staff_delete(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Delete one stretch of staff time, by the actual role; then the section."""
    entry = get_object_or_404(hub.staff_time, pk=pk)
    entry.delete()
    messages.success(request, f"Deleted {entry.who}'s {entry.hours_words}.")
    return redirect(_ledger_section_url(hub, "hq-lg-staff"))


@hq_gate(HubCard.Kind.LEDGER)
def ledger_staff_csv(request: HttpRequest, *, hub: EventHub, card: HubCard) -> StreamingHttpResponse:
    """The staff time as a CSV download, oldest first, with the total row last."""
    entries = hub.staff_time.select_related("created_by").order_by("date", "created_at", "pk")
    totals = services.staff_totals(hub, cash_cents=services.event_cash(hub).total_cents)
    return stream_staff_time_csv(entries, totals=totals, filename_stem=hub.slug)


def _guilds_section(
    request: HttpRequest, hub: EventHub, message: str = "", tone: str = "success", **kwargs: Any
) -> HttpResponse:
    """Guild demos over htmx, with the figures out of band (the tally and goals count the guilds) and a toast."""
    context = {
        "hub": hub,
        "team_list_id": LEDGER_TEAM_LIST_ID,
        **_guilds_context(hub, services.guild_demo_totals(hub), **kwargs),
    }
    response = _figures_answer(request, hub, "hq/partials/_ledger_guilds_answer.html", context)
    if message:
        trigger_toast(response, message, tone)
    return response


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_guild_add(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Put one active guild on the Guild demos list, by the actual role (its lead as typed, else the guild's lead). A
    guild already listed or no longer active is refused, and nothing is written; one another add listed after the form
    was read is refused by name (:class:`hq.services.GuildAlreadyListed`), never a 500."""
    form = GuildDemoAddForm(request.POST, prefix="guild-add", guilds=services.guilds_to_add(hub))
    fragment = wants_fragment(request)
    if not form.is_valid():
        if not fragment:
            context = _ledger_context(request, hub, card, show=services.LEDGER_FILTER_ALL, guilds={"add_form": form})
            return render(request, "hq/ledger.html", context)
        return _guilds_section(request, hub, add_form=form)
    try:
        row = services.add_guild_demo(hub, form.cleaned_data["guild"], lead=form.cleaned_data["lead"])
    except services.GuildAlreadyListed as exc:  # another add got there first; the row stands as it is
        if not fragment:
            messages.error(request, str(exc))
            return redirect(_ledger_section_url(hub, "hq-lg-guilds"))
        return _guilds_section(request, hub, str(exc), "error")
    message = f"{row.guild.name} added."
    if not fragment:
        messages.success(request, message)
        return redirect(_ledger_section_url(hub, "hq-lg-guilds"))
    return _guilds_section(request, hub, message, add_open=True)


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_guilds_add_all(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Add every active guild not on the list yet, by the actual role, each with its guild lead."""
    added = services.add_every_active_guild(hub)
    message = f"Added {added} guild{'' if added == 1 else 's'}." if added else "Every active guild is on the list."
    if not wants_fragment(request):
        messages.success(request, message)
        return redirect(_ledger_section_url(hub, "hq-lg-guilds"))
    return _guilds_section(request, hub, message)


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_guild_edit(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Save one guild's row, by the actual role. Over htmx only that row comes back, with the summary and the figures
    out of band. A Save from a page older than the row (:class:`hq.services.StaleSave`) comes back as the database
    holds it with :data:`GUILD_STALE_SAVE`."""
    fragment = wants_fragment(request)
    row = get_object_or_404(hub.guild_demos.select_related("guild"), pk=pk)
    form = _guild_form(row, request.POST)
    valid = form.is_valid()
    stale = False
    if valid:
        try:
            row = services.update_guild_demo(row, form.to_fields(), seen=form.seen())
        except services.StaleSave:
            valid, stale = False, True
    if valid and not fragment:
        messages.success(request, f"{row.guild.name} saved.")
        return redirect(_ledger_section_url(hub, "hq-lg-guilds"))
    refusal: dict[str, Any] = {}
    if stale:
        refusal = {"keep_pk": row.pk, "row_error": GUILD_STALE_SAVE}
    elif not valid:
        refusal = {"keep_pk": row.pk, "row_form": form}
    if not fragment:
        context = _ledger_context(request, hub, card, show=services.LEDGER_FILTER_ALL, guilds=refusal)
        return render(request, "hq/ledger.html", context)
    saved = hub.guild_demos.select_related("guild").get(pk=row.pk)
    if stale:
        answer = GuildRow(row=saved, form=_guild_form(saved), error=GUILD_STALE_SAVE)
    elif not valid:
        answer = GuildRow(row=saved, form=form)
    else:
        answer = GuildRow(row=saved, form=_guild_form(saved))
    context = {
        "hub": hub,
        "guild_row": answer,
        "guild_summary": services.guild_demo_totals(hub).summary(_event_day(hub)),
        "team_list_id": LEDGER_TEAM_LIST_ID,
    }
    response = _figures_answer(request, hub, "hq/partials/_ledger_guild_row_answer.html", context)
    if valid:
        trigger_toast(response, "Saved.", "success")
    return response


@require_POST
@hq_gate(HubCard.Kind.LEDGER)
def ledger_guild_delete(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Take a guild off the Guild demos list, by the actual role; then the section."""
    row = get_object_or_404(hub.guild_demos.select_related("guild"), pk=pk)
    row.delete()
    messages.success(request, f"{row.guild.name} is off the list.")
    return redirect(_ledger_section_url(hub, "hq-lg-guilds"))
