"""The HQ page and its edit pages (Track C, part 1), the volunteer sign-up page (part 2), the wine wall (part 3),
the Marketing Tracker (Track D, part 1), the Sponsorship Tracker (Track D, part 2), Vendor Tables (Track D, part 3)
the Market Check-in (Track D, part 4), the event flyer and sharing page (Track D, part 5) and the PL Ledger's budget
(Track D, part 6a), money in (Track D, part 6b), and team and tally (Track D, part 6c).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseBadRequest, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from core.abuse_limits import record_keyed_attempt
from core.htmx import wants_fragment
from hq import services
from hq.access import (
    actual_member,
    admin_required,
    can_check_in,
    card_view,
    check_in_required,
    hub_or_404,
    is_actual_admin,
    organizer_page,
    organizer_required,
    renders_as_admin,
    viewer_member,
)
from hq.exports import (
    stream_bar_sales_csv,
    stream_donors_csv,
    stream_ledger_csv,
    stream_market_reports_csv,
    stream_people_csv,
    stream_roster_csv,
    stream_sponsors_csv,
    stream_staff_time_csv,
    stream_vendor_tables_csv,
    stream_volunteer_hours_csv,
)
from hq.forms import (
    LEDGER_TEAM_LIST_ID,
    MARKETING_TEAM_LIST_ID,
    RECEIPT_NOT_TAKEN,
    SPONSOR_TEAM_LIST_ID,
    BarBatchForm,
    BarQuickForm,
    BudgetLineEditForm,
    BudgetLineForm,
    CardSharingForm,
    DoorCountForm,
    FlyerLinksForm,
    FlyerUploadForm,
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
    MarketingActionForm,
    MarketReportForm,
    ReceiptForm,
    SponsorEditForm,
    SponsorForm,
    SponsorLogForm,
    StaffTimeForm,
    TicketPricesForm,
    TicketsForm,
    VendorFloorForm,
    VendorTableEditForm,
    VendorTableForm,
    VolunteerHoursForm,
    VolunteerInfoForm,
    VolunteerShiftForm,
    VolunteerShiftFormSet,
    WineBottleForm,
    WineryLogoForm,
)
from hq.holiday_bizarre import (
    CHECKIN_FOOTER,
    DOOR_CLICKER_HINT,
    DOOR_GUEST_LIST,
    FLYER_COMING_SOON,
    FLYER_LAST_YEAR_NOTE,
    FLYER_SHARE_LEDE,
    FLYER_TABLE_ASK,
    FLYER_TABLE_EMAIL,
    FLYER_TABLE_TITLE,
    LEDGER_FOOTER,
    LEDGER_INTRO,
    LEDGER_RECEIPT_HINT,
    LEDGER_TITLE,
    NO_DOUBLE_ENTRY_TITLE,
    SPONSOR_TIERS,
    TALLY_EYEBROW,
    TALLY_TITLE,
    TALLY_TITLE_GOLD,
    VENDOR_SETUP_NOTE,
    WINE_WALL_BENEFIT,
)
from hq.models import (
    DOOR_STEPS,
    RECEIPT_KINDS,
    BudgetLine,
    EventHub,
    FlyerSlot,
    GuildDemo,
    HubCard,
    LaunchTask,
    LedgerEntry,
    LedgerSettings,
    MarketingKind,
    MarketReport,
    ShiftSignup,
    Sponsor,
    VendorTable,
    VolunteerHours,
    VolunteerShift,
    donor_key,
    format_hours,
    format_money,
)
from hq.services import VolunteerBand, VolunteerInfo, goal_rows
from hub.toast import trigger_client_event, trigger_toast
from hub.view_as import fog_admin_required, has_events_admin_access
from membership.models import Guild, Member

if TYPE_CHECKING:
    from django.forms import BaseInlineFormSet

    # The shifts formset's type: the factory's result is a value, so the view names the generic it builds.
    ShiftFormSet = BaseInlineFormSet[VolunteerShift, EventHub, VolunteerShiftForm]


def index(request: HttpRequest) -> HttpResponse:
    """``/hq/``: the open hub's page. With none open, an admin goes to a closed hub if one exists, else to
    the Load Lee's HQ page; everyone else gets a 404."""
    hub = EventHub.objects.open()
    if hub is not None:
        return redirect(hub.get_absolute_url())
    if renders_as_admin(request):
        closed = EventHub.objects.order_by("-created_at").first()
        return redirect(closed.get_absolute_url() if closed is not None else "hq:setup")
    raise Http404("No HQ is open.")


def hub_page(request: HttpRequest, slug: str) -> HttpResponse:
    """The HQ page. No login required: a signed-out visitor sees the Everyone doors.

    A non-admin sees only the doors they may open; with none, the page is the themed 404 (as a closed hub is),
    signed in or out, so the sidebar entry and the page agree on who has an HQ.
    """
    hub = hub_or_404(slug, request)
    is_admin = renders_as_admin(request)
    member = viewer_member(request)
    cards = list(hub.cards.prefetch_related("team", "can_open"))
    if not is_admin:
        cards = [card for card in cards if not card.is_hidden and card.is_open_to(member, is_admin=False)]
        if not cards:
            raise Http404("No doors are open to you.")
    wine = services.wine_band(hub)
    return render(
        request,
        "hq/page.html",
        {
            "hub": hub,
            "cards": cards,
            "goals": goal_rows(hub),
            "countdown": hub.countdown_parts(timezone.now()),
            # Lee's Bottles by type under the goals: every kind, once the wall has a bottle.
            "bottle_kinds": wine.every_kind if wine.bottles else [],
            "can_edit": is_admin,
        },
    )


@fog_admin_required
def setup(request: HttpRequest) -> HttpResponse:
    """``/hq/new/``: load Lee's HQ (her doors, goals and shifts from :mod:`hq.holiday_bizarre`) with one button.

    There is no blank form: the HQ is always Lee's. Once a hub exists this page sends an admin to it.
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
            "Lee's HQ is loaded. Every door is admins only until you open it, and Edit HQ links the calendar event.",
        )
        return redirect(report.hub.get_absolute_url())
    return render(request, "hq/setup.html")


@fog_admin_required
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


@fog_admin_required
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


@fog_admin_required
@require_POST
def hero_delete(request: HttpRequest, slug: str) -> HttpResponse:
    hub = get_object_or_404(EventHub, slug=slug)
    hub.remove_hero()
    messages.success(request, "Hero image removed.")
    return redirect("hq:edit", slug=hub.slug)


@fog_admin_required
def card_edit(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """One card: its words, who may open it (the audience, Can open and Team, as the share panel has them), hidden,
    and its place in the order.

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


@fog_admin_required
def card_share(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """The share panel for one card: the audience and the Can open and Team pickers, saved in one POST.

    Admins only, by the actual role. The HQ page loads the panel into its modal over htmx; a GET without htmx is the
    panel on its own page, so it works with no script. A saved htmx post answers with the panel (as saved), the
    card's chip out of band, a toast and ``hq-share-saved`` (which closes the modal); a refused one with the panel
    and its errors. A plain post lands on the HQ page with a message, or renders the panel's page with its errors.
    """
    hub = get_object_or_404(EventHub, slug=slug)
    card = get_object_or_404(hub.cards, pk=pk)
    if request.method == "POST":
        form = CardSharingForm(request.POST, instance=card)
        if form.is_valid():
            card = form.save()
            message = f"{card.title} is shared with {card.audience_chip.lower()}{_people_words(card)}."
            if not wants_fragment(request):
                messages.success(request, message)
                return redirect(hub.get_absolute_url())
            card = hub.cards.prefetch_related("team", "can_open").get(pk=card.pk)
            response = render(
                request,
                "hq/partials/_share_answer.html",
                {"hub": hub, "card": card, "form": CardSharingForm(instance=card)},
            )
            trigger_toast(response, message)
            trigger_client_event(response, "hq-share-saved", {"pk": card.pk})
            return response
    else:
        form = CardSharingForm(instance=card)
    context = {"hub": hub, "card": card, "form": form}
    if wants_fragment(request):
        return render(request, "hq/partials/_share_form.html", context)
    return render(request, "hq/card_share.html", context)


def _people_words(card: HubCard) -> str:
    """ " and 3 people" for the share toast, "" when the lists name nobody."""
    count = card.people_count
    if not count:
        return ""
    return f" and {count} {'person' if count == 1 else 'people'}"


@fog_admin_required
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


@card_view(HubCard.Kind.VOLUNTEERS)
def volunteers(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The volunteer sign-up page: the band, Your Info, the shifts by phase, and the organisers' roster."""
    return _volunteers_page(request, hub, card, form=None)


def _volunteers_page(
    request: HttpRequest, hub: EventHub, card: HubCard, *, form: VolunteerInfoForm | None
) -> HttpResponse:
    member = viewer_member(request)
    is_admin = renders_as_admin(request)
    person = services.person_for(request, member)
    if form is None:
        form = VolunteerInfoForm(initial=asdict(person) if person is not None else None)
    shifts = list(hub.shifts.with_counts())
    # Rendering reads the effective role: an admin previewing as a guest sees no roster. The
    # roster page and the CSV keep the actual role through organizer_required.
    can_organize = card.can_organize(member, is_admin=is_admin)
    roster = services.roster_rows(hub) if can_organize else []
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
            "can_organize": can_organize,
            "roster": roster,
            "roster_emails": services.roster_emails(roster),
            "wine_url": _wine_url(hub, member, is_admin),
        },
    )


def _wine_url(hub: EventHub, member: Member | None, is_admin: bool) -> str:
    """Where the Wine Wall link goes, or "" when that card is not open to this viewer (or has no page yet)."""
    return _door_url(hub, HubCard.Kind.WINE_WALL, member, is_admin)


def _door_url(hub: EventHub, kind: str, member: Member | None, is_admin: bool) -> str:
    """Where a link to the ``kind`` door goes, or "" when that card is missing, hidden or not open to this viewer."""
    card = hub.cards.prefetch_related("team", "can_open").filter(kind=kind).first()
    if card is None or (card.is_hidden and not is_admin) or not card.is_open_to(member, is_admin=is_admin):
        return ""
    return card.url


@require_POST
@card_view(HubCard.Kind.VOLUNTEERS)
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
@card_view(HubCard.Kind.VOLUNTEERS)
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
@card_view(HubCard.Kind.VOLUNTEERS)
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


@organizer_required(HubCard.Kind.VOLUNTEERS)
def volunteer_roster(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The roster on its own page: everyone signed up, their shifts, Copy emails and the CSV."""
    roster = services.roster_rows(hub)
    return render(
        request,
        "hq/volunteer_roster.html",
        {"hub": hub, "card": card, "roster": roster, "roster_emails": services.roster_emails(roster)},
    )


@organizer_required(HubCard.Kind.VOLUNTEERS)
def volunteer_roster_csv(request: HttpRequest, *, hub: EventHub, card: HubCard) -> StreamingHttpResponse:
    """The roster as a CSV download, one row per person per shift."""
    return stream_roster_csv(
        ShiftSignup.objects.filter(shift__hub=hub), hours=services.hours_by_email(hub), filename_stem=hub.slug
    )


# ---- Wine wall (part 3) ----


def _wall_url(hub: EventHub) -> str:
    return reverse("hq:wine", args=[hub.slug])


def _identity(request: HttpRequest, member: Member | None) -> tuple[str, str]:
    """Who is acting, as on the volunteer sheet: the Your Info name and email this browser saved, else the
    member's own name and primary email, else blanks. The email is what a guest's bottles are keyed on."""
    person = services.person_for(request, member)
    if person is None:
        return "", ""
    return person.name, person.email


@card_view(HubCard.Kind.WINE_WALL)
def wine(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The wine wall: the band, Log a Bottle, All Bottles, the wineries, and the donors for organisers.

    A POST logs a bottle (multipart, so the form opts out of hx-boost). A guest's post goes
    through the honeypot and the rate limit the volunteer sign-ups share.
    """
    member = viewer_member(request)
    name, email = _identity(request, member)
    if request.method != "POST":
        return _wine_page(request, hub, card, form=WineBottleForm(initial={"credited_name": name}))
    form = WineBottleForm(request.POST, request.FILES)
    if not form.is_valid():
        return _wine_page(request, hub, card, form=form)
    if form.is_honeypot_hit:
        return redirect(_wall_url(hub))
    if member is None and not _guest_within_limits(request, services.RATE_SCOPE_BOTTLE):
        form.add_error(None, "Too many bottles from this connection for now. Try again in an hour.")
        return _wine_page(request, hub, card, form=form)
    bottle = services.log_bottle(hub, form.to_fields(), member=member, logger_name=name, logger_email=email)
    messages.success(request, f"{bottle.name} is on the wall. Thank you!")
    return redirect(_wall_url(hub))


def _wine_page(
    request: HttpRequest,
    hub: EventHub,
    card: HubCard,
    *,
    form: WineBottleForm,
    logo_form: WineryLogoForm | None = None,
) -> HttpResponse:
    """Render the wall. ``form`` bound means Log a Bottle failed and opens with its errors.

    Rendering reads the effective role, as the volunteer page does: an admin previewing as a
    guest sees no donors, no logo form and no Delete. The action views keep the actual role. The logo form is a
    setting, so it shows to admins alone, never to the card's Team.
    """
    member = viewer_member(request)
    is_admin = renders_as_admin(request)
    can_organize = card.can_organize(member, is_admin=is_admin)
    _name, email = _identity(request, member)
    bottles = list(hub.bottles.all())
    rows = services.wineries(hub)
    donors = services.donor_rows(hub) if can_organize else []
    # Organisers see Lee's Under $25 flag on a bottle worth less than one ticket; nobody else pays for the read.
    ticket_cents = services.ticket_price_cents(hub) if can_organize else 0
    if is_admin and logo_form is None:
        logo_form = WineryLogoForm(names=[row.name for row in rows])
    return render(
        request,
        "hq/wine.html",
        {
            "hub": hub,
            "card": card,
            "form": form,
            "form_open": form.is_bound,
            "is_guest": member is None,
            "band": services.wine_band(hub),
            "bottles": bottles,
            # The bottles this viewer logged or organises: their Delete, and their note, show.
            "manageable": {
                b.pk for b in bottles if b.can_be_deleted_by(member, email=email, is_organizer=can_organize)
            },
            "wineries": rows,
            "logo_form": logo_form if is_admin else None,
            "can_organize": can_organize,
            "donors": donors,
            "donor_emails": services.donor_emails(donors),
            "donors_thanked": sum(1 for row in donors if row.thanked),
            "donors_back": DONORS_BACK_WALL,
            "ticket_cents": ticket_cents,
            "ticket_display": format_money(ticket_cents),
            "max_upload_bytes": settings.MAX_UPLOAD_IMAGE_BYTES,
        },
    )


@require_POST
@card_view(HubCard.Kind.WINE_WALL)
def bottle_delete(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Take a bottle off the wall: its logger (by member, or by the email it was logged under) or an
    organiser, by their actual roles; anyone else gets a 403."""
    bottle = get_object_or_404(hub.bottles, pk=pk)
    member = actual_member(request)
    is_organizer = card.can_organize(member, is_admin=is_actual_admin(request))
    _name, email = _identity(request, member)
    try:
        services.delete_bottle(bottle, member=member, email=email, is_organizer=is_organizer)
    except services.NotYourBottle as exc:
        raise PermissionDenied(str(exc)) from exc
    messages.success(request, f"{bottle.name} is off the wall.")
    return redirect(_wall_url(hub))


@require_POST
@admin_required(HubCard.Kind.WINE_WALL)
def winery_logo(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Upload a logo for a winery named on the wall, replacing the one under that name."""
    form = WineryLogoForm(request.POST, request.FILES, names=[row.name for row in services.wineries(hub)])
    if not form.is_valid():
        name, _email = _identity(request, viewer_member(request))
        return _wine_page(request, hub, card, form=WineBottleForm(initial={"credited_name": name}), logo_form=form)
    logo = services.set_winery_logo(hub, form.cleaned_data["name"], form.cleaned_data["logo"])
    messages.success(request, f"Logo saved for {logo.name}.")
    return redirect(_wall_url(hub))


# Where a donor's Thanked toggle lands after a plain post: the wall, or (by default) the donors page.
DONORS_BACK_WALL = "wall"


def _donors_context(hub: EventHub, donors: list[services.DonorRow]) -> dict[str, Any]:
    return {
        "hub": hub,
        "donors": donors,
        "donor_emails": services.donor_emails(donors),
        "donors_thanked": sum(1 for row in donors if row.thanked),
        "donors_back": "",
    }


@organizer_required(HubCard.Kind.WINE_WALL)
def wine_donors(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The donors on their own page: every giver, their type, email and listing, Thanked, Copy donor emails and the
    CSV."""
    return render(request, "hq/wine_donors.html", {**_donors_context(hub, services.donor_rows(hub)), "card": card})


@organizer_required(HubCard.Kind.WINE_WALL)
def wine_donors_csv(request: HttpRequest, *, hub: EventHub, card: HubCard) -> StreamingHttpResponse:
    """The donors table as a CSV download, one row per giver."""
    return stream_donors_csv(services.donor_rows(hub), filename_stem=hub.slug)


@require_POST
@organizer_required(HubCard.Kind.WINE_WALL)
def wine_donor_thanked(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Mark a wine donor thanked, or take the mark off (``thanked`` "1" or not), by the actual role. Over htmx the
    donor's row comes back with the thanked count out of band; a plain post lands back where it came from. A giver no
    bottle names is a 404."""
    name = request.POST.get("giver", "")
    thanked = request.POST.get("thanked") == "1"
    if services.donor_row(hub, donor_key(name)) is None:
        raise Http404("No such donor.")
    services.set_donor_thanked(hub, name, thanked=thanked)
    donors = services.donor_rows(hub)
    row = next(row for row in donors if row.key == donor_key(name))
    message = f"{row.name} is marked thanked." if thanked else f"{row.name} is back on the list to thank."
    back = DONORS_BACK_WALL if request.POST.get("back") == DONORS_BACK_WALL else ""
    if not wants_fragment(request):
        messages.success(request, message)
        return redirect(_wall_url(hub) if back else reverse("hq:wine_donors", args=[hub.slug]))
    context = {**_donors_context(hub, donors), "row": row, "donors_back": back}
    response = render(request, "hq/partials/_donor_row_answer.html", context)
    trigger_toast(response, message, "success")
    return response


# ---- Marketing Tracker (Track D, part 1) ----


def _marketing_url(hub: EventHub) -> str:
    return reverse("hq:marketing", args=[hub.slug])


@card_view(HubCard.Kind.MARKETING)
def marketing(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The Marketing Tracker: the six totals, Log a Marketing Action for organisers, and the log, newest first."""
    return render(request, "hq/marketing.html", _marketing_context(request, hub, card, MarketingActionForm()))


def _marketing_context(request: HttpRequest, hub: EventHub, card: HubCard, form: MarketingActionForm) -> dict[str, Any]:
    """What the page and its log fragment render. The form and Delete show by the effective role, so a preview hides them."""
    return {
        "hub": hub,
        "card": card,
        "form": form,
        "can_organize": card.can_organize(viewer_member(request), is_admin=renders_as_admin(request)),
        "tiles": services.marketing_tiles(services.marketing_totals(hub)),
        "actions": list(hub.marketing_actions.all()),
        "team": sorted({member.display_name for member in card.team.all()}),
        "team_list_id": MARKETING_TEAM_LIST_ID,
    }


@require_POST
@organizer_required(HubCard.Kind.MARKETING)
def marketing_add(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Add one action to the log, by the actual role. Over htmx the log comes back with a toast, keeping the
    kind, date and who for the next entry; a plain post lands back on the page with a message."""
    form = MarketingActionForm(request.POST)
    fragment = wants_fragment(request)
    template = "hq/partials/_marketing_log.html" if fragment else "hq/marketing.html"
    if not form.is_valid():
        return render(request, template, _marketing_context(request, hub, card, form))
    services.log_marketing_action(hub, form.cleaned_data, member=actual_member(request))
    if not fragment:
        messages.success(request, "Added.")
        return redirect(_marketing_url(hub))
    fresh = MarketingActionForm(initial=form.kept_for_next())
    response = render(request, template, _marketing_context(request, hub, card, fresh))
    trigger_toast(response, "Added.", "success")
    return response


@require_POST
@organizer_required(HubCard.Kind.MARKETING)
def marketing_delete(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Take one action off the log (organisers, by the actual role)."""
    get_object_or_404(hub.marketing_actions, pk=pk).delete()
    messages.success(request, "Taken off the log.")
    return redirect(_marketing_url(hub))


# ---- Sponsorship Tracker (Track D, part 2) ----


def _sponsors_url(hub: EventHub, show: str = services.SPONSOR_FILTER_ALL) -> str:
    url = reverse("hq:sponsors", args=[hub.slug])
    return url if show == services.SPONSOR_FILTER_ALL else f"{url}?filter={show}"


@dataclass(frozen=True)
class SponsorRow:
    """One row in the pipeline: the sponsor, and for an organiser its edit and log forms and whether it opens."""

    sponsor: Sponsor
    edit_form: SponsorEditForm | None
    log_form: SponsorLogForm | None
    is_open: bool


def _edit_prefix(sponsor: Sponsor) -> str:
    return f"sponsor-{sponsor.pk}"


def _log_prefix(sponsor: Sponsor) -> str:
    return f"log-{sponsor.pk}"


def _sponsor_row(
    sponsor: Sponsor,
    *,
    can_organize: bool,
    is_open: bool,
    edit_form: SponsorEditForm | None = None,
    log_form: SponsorLogForm | None = None,
) -> SponsorRow:
    """``sponsor``'s pipeline row; an organiser's carries its edit and log forms, fresh unless refused ones are given."""
    if not can_organize:
        return SponsorRow(sponsor=sponsor, edit_form=None, log_form=None, is_open=is_open)
    if edit_form is None:
        edit_form = SponsorEditForm(initial=SponsorEditForm.initial_for(sponsor), prefix=_edit_prefix(sponsor))
    if log_form is None:
        log_form = SponsorLogForm(prefix=_log_prefix(sponsor))
    return SponsorRow(sponsor=sponsor, edit_form=edit_form, log_form=log_form, is_open=is_open)


def _organizes(request: HttpRequest, card: HubCard) -> bool:
    """Whether the effective role renders as one of ``card``'s organisers."""
    return card.can_organize(viewer_member(request), is_admin=renders_as_admin(request))


@card_view(HubCard.Kind.SPONSORSHIP)
def sponsors(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The Sponsorship Tracker: the guidance line, the band, Follow up, + Add a Sponsor and the pipeline for
    organisers, and the tiers. ``?filter=`` picks the pipeline's group."""
    show = services.sponsor_filter(request.GET.get("filter", ""))
    return render(request, "hq/sponsors.html", _sponsors_context(request, hub, card, show=show))


def _sponsors_context(
    request: HttpRequest,
    hub: EventHub,
    card: HubCard,
    *,
    show: str,
    add_form: SponsorForm | None = None,
    add_open: bool = False,
    open_pk: int | None = None,
    edit_form: SponsorEditForm | None = None,
    log_form: SponsorLogForm | None = None,
) -> dict[str, Any]:
    """What the page and its board fragment render, by the effective role: an organiser gets the contact details,
    the forms, the log and the CSV; everyone else the band, the follow-up count, the names, tiers and statuses.

    ``open_pk`` is the row to render open (the one refused on a plain post), listed whatever the filter, with
    ``edit_form`` and ``log_form`` in place of its fresh forms when they came back with errors.
    """
    member = viewer_member(request)
    is_admin = renders_as_admin(request)
    can_organize = card.can_organize(member, is_admin=is_admin)
    today = timezone.localdate()
    if not can_organize and show == services.SPONSOR_FILTER_DONORS:
        # Last year's gifts are the organisers' notes; the filter would list who gave.
        show = services.SPONSOR_FILTER_ALL
    sponsors_qs = services.filtered_sponsors(hub, show, keep_pk=open_pk)
    if can_organize:
        sponsors_qs = sponsors_qs.prefetch_related("log_entries")
    rows = []
    for sponsor in sponsors_qs:
        is_open = sponsor.pk == open_pk
        rows.append(
            _sponsor_row(
                sponsor,
                can_organize=can_organize,
                is_open=is_open,
                edit_form=edit_form if is_open else None,
                log_form=log_form if is_open else None,
            )
        )
    return {
        "hub": hub,
        "card": card,
        "can_organize": can_organize,
        "band": services.sponsor_band(hub),
        "follow_ups": services.follow_ups(hub, today=today),
        "today": today,
        "add_form": add_form if add_form is not None else SponsorForm(),
        # A refused add comes back open with its errors; a saved one stays open for the next.
        "add_open": add_open or add_form is not None,
        "rows": rows,
        "has_sponsors": bool(rows) or hub.sponsors.exists(),
        "show": show,
        "filters": [
            (value, label)
            for value, label in services.SPONSOR_FILTERS
            if can_organize or value != services.SPONSOR_FILTER_DONORS
        ],
        "team": sorted({named.display_name for named in card.team.all()}),
        "team_list_id": SPONSOR_TEAM_LIST_ID,
        "tiers": SPONSOR_TIERS,
        "wine_benefit": WINE_WALL_BENEFIT,
        "wine_url": _wine_url(hub, member, is_admin),
    }


def _sponsor_board(request: HttpRequest, context: dict[str, Any], message: str = "") -> HttpResponse:
    """+ Add a sponsor's answer: the board over htmx (with a toast when ``message`` is set), or the whole page for
    a plain post that failed."""
    if not wants_fragment(request):
        return render(request, "hq/sponsors.html", context)
    response = render(request, "hq/partials/_sponsor_board.html", context)
    if message:
        trigger_toast(response, message, "success")
    return response


def _sponsor_row_answer(
    request: HttpRequest,
    hub: EventHub,
    card: HubCard,
    sponsor: Sponsor,
    *,
    show: str,
    edit_form: SponsorEditForm | None = None,
    log_form: SponsorLogForm | None = None,
    message: str = "",
) -> HttpResponse:
    """A row's Save or Add to log answer. Over htmx: that row, open, for its target, and the band and Follow up out
    of band (with a toast when ``message`` is set), so the other rows and the add form keep what is typed in them.
    A plain post that failed gets the whole page with the row open. Either way the row comes back even when it no
    longer matches the filter ``show``, so a status change or a refused form stays where it was edited."""
    if not wants_fragment(request):
        context = _sponsors_context(
            request, hub, card, show=show, open_pk=sponsor.pk, edit_form=edit_form, log_form=log_form
        )
        return render(request, "hq/sponsors.html", context)
    can_organize = _organizes(request, card)
    today = timezone.localdate()
    context = {
        "hub": hub,
        "can_organize": can_organize,
        "row": _sponsor_row(sponsor, can_organize=can_organize, is_open=True, edit_form=edit_form, log_form=log_form),
        "today": today,
        "show": show,
        "band": services.sponsor_band(hub),
        "follow_ups": services.follow_ups(hub, today=today),
    }
    response = render(request, "hq/partials/_sponsor_row_answer.html", context)
    if message:
        trigger_toast(response, message, "success")
    return response


def _posted_filter(request: HttpRequest) -> str:
    return services.sponsor_filter(request.POST.get("filter", ""))


@require_POST
@organizer_required(HubCard.Kind.SPONSORSHIP)
def sponsor_add(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """+ Add a Sponsor, by the actual role. Over htmx the board comes back with the form open and empty and a
    "Sponsor added." toast; a plain post lands back on the page with a message."""
    show = _posted_filter(request)
    form = SponsorForm(request.POST)
    if not form.is_valid():
        return _sponsor_board(request, _sponsors_context(request, hub, card, show=show, add_form=form))
    services.add_sponsor(hub, form.to_fields(), member=actual_member(request))
    if not wants_fragment(request):
        messages.success(request, "Sponsor added.")
        return redirect(_sponsors_url(hub, show))
    context = _sponsors_context(request, hub, card, show=show, add_open=True)
    return _sponsor_board(request, context, "Sponsor added.")


@require_POST
@organizer_required(HubCard.Kind.SPONSORSHIP)
def sponsor_edit(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Save one row's inline edit, by the actual role; a new status adds its line to the contact log."""
    sponsor = get_object_or_404(hub.sponsors, pk=pk)
    show = _posted_filter(request)
    form = SponsorEditForm(request.POST, prefix=_edit_prefix(sponsor))
    if not form.is_valid():
        return _sponsor_row_answer(request, hub, card, sponsor, show=show, edit_form=form)
    services.update_sponsor(sponsor, form.to_fields())
    if not wants_fragment(request):
        messages.success(request, f"{sponsor.organization} saved.")
        return redirect(_sponsors_url(hub, show))
    return _sponsor_row_answer(request, hub, card, sponsor, show=show, message="Saved.")


@require_POST
@organizer_required(HubCard.Kind.SPONSORSHIP)
def sponsor_log(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Add one line to a sponsor's contact log, by the actual role."""
    sponsor = get_object_or_404(hub.sponsors, pk=pk)
    show = _posted_filter(request)
    form = SponsorLogForm(request.POST, prefix=_log_prefix(sponsor))
    if not form.is_valid():
        return _sponsor_row_answer(request, hub, card, sponsor, show=show, log_form=form)
    services.log_contact(sponsor, text=form.cleaned_data["text"], by=form.cleaned_data["by"])
    if not wants_fragment(request):
        messages.success(request, "Added to the log.")
        return redirect(_sponsors_url(hub, show))
    return _sponsor_row_answer(request, hub, card, sponsor, show=show, message="Added to the log.")


@require_POST
@organizer_required(HubCard.Kind.SPONSORSHIP)
def sponsor_delete(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Take a sponsor and its contact log off the tracker (organisers, by the actual role)."""
    sponsor = get_object_or_404(hub.sponsors, pk=pk)
    sponsor.delete()
    messages.success(request, f"{sponsor.organization} is off the tracker.")
    return redirect(_sponsors_url(hub, _posted_filter(request)))


@organizer_required(HubCard.Kind.SPONSORSHIP)
def sponsors_csv(request: HttpRequest, *, hub: EventHub, card: HubCard) -> StreamingHttpResponse:
    """Every sponsor on the tracker as a CSV download, one row each, in pipeline order."""
    return stream_sponsors_csv(hub.sponsors.all(), filename_stem=hub.slug)


# ---- Vendor Tables (Track D, part 3) ----


def _vendors_url(hub: EventHub, show: str = services.VENDOR_FILTER_ALL) -> str:
    url = reverse("hq:vendors", args=[hub.slug])
    return url if show == services.VENDOR_FILTER_ALL else f"{url}?filter={show}"


@dataclass(frozen=True)
class VendorRow:
    """One row in the table requests: the request and its edit form (bound and refused, or fresh from the row)."""

    table: VendorTable
    form: VendorTableEditForm
    # A refusal the form does not carry: a Save from a page older than the row (``VENDOR_STALE_SAVE``).
    error: str = ""


# A row's Save whose ``updated_at`` no longer matches the database: someone else saved it since the page loaded.
VENDOR_STALE_SAVE = "Someone else changed this table. Your page has been refreshed; make your change again."


def _vendor_prefix(table: VendorTable) -> str:
    return f"vendor-{table.pk}"


def _vendor_form_id(table: VendorTable) -> str:
    return f"hq-vt-row-{table.pk}"


def _vendor_edit_form(table: VendorTable, guilds: list[Guild], data: Any = None) -> VendorTableEditForm:
    return VendorTableEditForm(
        data, instance=table, guilds=guilds, prefix=_vendor_prefix(table), form_id=_vendor_form_id(table)
    )


def _vendor_team(card: HubCard) -> tuple[str, str]:
    """Who manages the door, from the first person on its Team by pk: their display name for the header ("" for nobody
    or a blank name) and their first name for the Requested tile, else the vendor team."""
    first = card.team.order_by("pk").first()
    if first is None:
        return "", services.VENDOR_TEAM_FALLBACK
    name = first.display_name.strip()
    return name, (name.split() or [services.VENDOR_TEAM_FALLBACK])[0]


def _vendors_context(
    request: HttpRequest,
    hub: EventHub,
    card: HubCard,
    *,
    show: str,
    add_form: VendorTableForm | None = None,
    add_open: bool = False,
    floor_form: VendorFloorForm | None = None,
    keep_pk: int | None = None,
    row_form: VendorTableEditForm | None = None,
    row_error: str = "",
) -> dict[str, Any]:
    """What the page and its board render. Organisers alone reach it, so every row carries its edit form.

    ``keep_pk`` is a request kept in view whatever the filter (the one just added or refused), ``row_form``
    that row's bound form when its Save was refused, and ``row_error`` a refusal shown on that row.
    """
    guilds = services.active_guilds()
    band_context = _vendor_band_context(hub, card)
    band: services.VendorBand = band_context["band"]
    rows = []
    for table in services.filtered_vendor_tables(hub, show, keep_pk=keep_pk):
        kept = table.pk == keep_pk
        form = row_form if row_form is not None and kept else _vendor_edit_form(table, guilds)
        rows.append(VendorRow(table=table, form=form, error=row_error if kept else ""))
    return {
        **band_context,
        "hub": hub,
        "card": card,
        "managed_by": _vendor_team(card)[0],
        "add_form": add_form if add_form is not None else VendorTableForm(guilds=guilds),
        # A refused add comes back open with its errors; a saved one stays open for the next.
        "add_open": add_open or add_form is not None,
        "rows": rows,
        "has_tables": band.total > 0,
        "show": show,
        "filters": services.VENDOR_FILTERS,
        "floor_form": floor_form if floor_form is not None else _floor_form(band),
    }


def _vendor_band_context(hub: EventHub, card: HubCard, band: services.VendorBand | None = None) -> dict[str, Any]:
    """What the band renders: the counts, Lee's setup note, and who the requests wait on."""
    return {
        "band": band if band is not None else services.vendor_band(hub),
        "setup_note": VENDOR_SETUP_NOTE,
        "waiting_on": _vendor_team(card)[1],
    }


def _floor_form(band: services.VendorBand) -> VendorFloorForm:
    return VendorFloorForm(initial={"capacity": band.capacity, "map_note": band.map_note})


@organizer_page(HubCard.Kind.VENDOR_TABLES)
def vendors(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Vendor Tables: the band, + Add a Table Request, the table requests with ``?filter=``, and the space map's
    capacity. Organisers only (the card's gate, then a 403), because every row is a contact."""
    show = services.vendor_filter(request.GET.get("filter", ""))
    return render(request, "hq/vendors.html", _vendors_context(request, hub, card, show=show))


def _vendor_posted_filter(request: HttpRequest) -> str:
    return services.vendor_filter(request.POST.get("filter", ""))


@require_POST
@organizer_required(HubCard.Kind.VENDOR_TABLES)
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
@organizer_required(HubCard.Kind.VENDOR_TABLES)
def vendor_edit(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Save one row, by the actual role. Over htmx only that row comes back (with the band out of band), so
    what is typed in any other row stays put; a refused Save comes back with its errors and saves nothing.

    A Save from a page older than the row (its ``updated_at`` no longer matches) is refused whole: the row comes
    back as the database holds it, with :data:`VENDOR_STALE_SAVE`, so one organiser never overwrites another.
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
            return _vendor_row_answer(request, hub, card, fresh)
        if not form.is_valid():
            if not fragment:
                context = _vendors_context(request, hub, card, show=show, keep_pk=table.pk, row_form=form)
                return render(request, "hq/vendors.html", context)
            # The refused form changed the row in memory; the row's own words come from the database.
            saved = hub.vendor_tables.select_related("guild", "created_by").get(pk=table.pk)
            return _vendor_row_answer(request, hub, card, VendorRow(table=saved, form=form))
        services.update_vendor_table(table, form.to_fields())
    if not fragment:
        messages.success(request, f"{table.name} saved.")
        return redirect(_vendors_url(hub, show))
    response = _vendor_row_answer(request, hub, card, VendorRow(table=table, form=_vendor_edit_form(table, guilds)))
    trigger_toast(response, "Saved.", "success")
    return response


def _vendor_row_answer(request: HttpRequest, hub: EventHub, card: HubCard, row: VendorRow) -> HttpResponse:
    """The row for an htmx Save, with the band swapped out of band so the counts follow the row's status."""
    context = {**_vendor_band_context(hub, card), "hub": hub, "row": row, "show": _vendor_posted_filter(request)}
    return render(request, "hq/partials/_vendor_row_answer.html", context)


@require_POST
@organizer_required(HubCard.Kind.VENDOR_TABLES)
def vendor_delete(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Take a table request off the list (organisers, by the actual role), then the page with the filter."""
    table = get_object_or_404(hub.vendor_tables, pk=pk)
    table.delete()
    messages.success(request, f"{table.name} is off the list.")
    return redirect(_vendors_url(hub, _vendor_posted_filter(request)))


@require_POST
@admin_required(HubCard.Kind.VENDOR_TABLES)
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


@organizer_required(HubCard.Kind.VENDOR_TABLES)
def vendors_csv(request: HttpRequest, *, hub: EventHub, card: HubCard) -> StreamingHttpResponse:
    """Every table request as a CSV download, one row each, in list order."""
    return stream_vendor_tables_csv(hub.vendor_tables.select_related("guild", "created_by"), filename_stem=hub.slug)


# ---- Market Check-in (Track D, part 4) ----


def _checkin_url(hub: EventHub) -> str:
    return reverse("hq:checkin", args=[hub.slug])


def _report_message(report: MarketReport) -> str:
    """Lee's line beside the button once a report is saved: the sales when there are any."""
    if report.sales_cents:
        return f"Saved! {format_money(report.sales_cents)} in sales. Congratulations!"
    return "Saved! Come back after the market to add your sales."


def _hours_message(entry: VolunteerHours) -> str:
    unit = "hour" if entry.hours == 1 else "hours"
    return f"Logged {entry.hours_display} {unit}. Thank you!"


def _report_context(
    hub: EventHub, member: Member, *, form: MarketReportForm | None = None, message: str = ""
) -> dict[str, Any]:
    """What the report section renders for ``member``: their report (the Saved chip), the confirmed table that is
    theirs, and the form, bound to the report, else starting from the table's name and guild or their own."""
    report = services.report_for(hub, member)
    table = services.confirmed_table(hub, member)
    if form is None:
        guilds = services.active_guilds()
        if report is not None:
            form = MarketReportForm(instance=report, guilds=guilds)
        else:
            initial = services.report_initial(member, table)
            form = MarketReportForm(instance=MarketReport(hub=hub, member=member), initial=initial, guilds=guilds)
    return {"hub": hub, "report": report, "vendor_table": table, "report_form": form, "report_message": message}


def _hours_context(
    hub: EventHub, member: Member, *, form: VolunteerHoursForm | None = None, message: str = ""
) -> dict[str, Any]:
    """What the hours section renders for ``member``: the form and their own entries, newest date first."""
    entries = services.member_hours(hub, member)
    return {
        "hub": hub,
        "hours_form": form if form is not None else VolunteerHoursForm(),
        "entries": entries,
        "my_total": format_hours(services.total_hours(entries)),
        "hours_message": message,
    }


def _checkin_context(
    request: HttpRequest,
    hub: EventHub,
    card: HubCard,
    *,
    report_form: MarketReportForm | None = None,
    hours_form: VolunteerHoursForm | None = None,
) -> dict[str, Any]:
    """What the page renders, by the effective role: the hero and the thank-you board for everyone the card is
    open to; the report and the hours forms for a member who may check in (:func:`hq.access.can_check_in`); a
    sign in link for a signed out visitor; the organisers' links for organisers."""
    member = viewer_member(request)
    is_admin = renders_as_admin(request)
    login = f"{resolve_url(settings.LOGIN_URL)}?{urlencode({REDIRECT_FIELD_NAME: _checkin_url(hub)})}"
    context: dict[str, Any] = {
        "hub": hub,
        "card": card,
        "countdown": services.checkin_countdown(hub, timezone.now()),
        "thanks": services.checkin_thanks(hub),
        "footer": CHECKIN_FOOTER,
        "band": services.checkin_band(hub),
        "board": services.thank_you_board(hub),
        "me_pk": member.pk if member is not None else None,
        "signed_in": request.user.is_authenticated,
        "login_url": login,
        "may_check_in": False,
        "can_organize": card.can_organize(member, is_admin=is_admin),
        "volunteer_url": _door_url(hub, HubCard.Kind.VOLUNTEERS, member, is_admin),
    }
    if member is not None and can_check_in(card, member, is_admin=is_admin):
        context["may_check_in"] = True
        context.update(_report_context(hub, member, form=report_form))
        context.update(_hours_context(hub, member, form=hours_form))
    return context


def _refused_page(
    request: HttpRequest, hub: EventHub, card: HubCard, form: MarketReportForm | VolunteerHoursForm, **forms: Any
) -> HttpResponse:
    """A plain post the form refused: the page again with the bound form, or, when the page would not show that
    form (an admin previewing as Guest acts as themselves but sees the page as the preview), a redirect back with
    the form's errors as a message so they are not lost."""
    context = _checkin_context(request, hub, card, **forms)
    if context["may_check_in"]:
        return render(request, "hq/checkin.html", context)
    errors = " ".join(str(error) for field_errors in form.errors.values() for error in field_errors)
    messages.error(request, errors)
    return redirect(_checkin_url(hub))


@card_view(HubCard.Kind.MARKET_CHECKIN)
def checkin(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The Market Check-in: the hero, Artists: your market report and Volunteers: log your hours for a member,
    and the volunteer thank-you board for everyone the card is open to."""
    return render(request, "hq/checkin.html", _checkin_context(request, hub, card))


@require_POST
@check_in_required(HubCard.Kind.MARKET_CHECKIN)
def checkin_report(request: HttpRequest, *, hub: EventHub, card: HubCard, member: Member) -> HttpResponse:
    """Save my report (or Update my report): the member's one report on the hub, by the actual member. Over htmx
    the report section comes back with Lee's message; a plain post lands back on the page with it."""
    report = services.report_for(hub, member)
    instance = report if report is not None else MarketReport(hub=hub, member=member)
    form = MarketReportForm(request.POST, instance=instance, guilds=services.active_guilds())
    fragment = wants_fragment(request)
    if not form.is_valid():
        if not fragment:
            return _refused_page(request, hub, card, form, report_form=form)
        return render(request, "hq/partials/_checkin_report.html", _report_context(hub, member, form=form))
    message = _report_message(services.save_report(hub, member, form.to_fields()))
    if not fragment:
        messages.success(request, message)
        return redirect(_checkin_url(hub))
    return render(request, "hq/partials/_checkin_report.html", _report_context(hub, member, message=message))


@require_POST
@check_in_required(HubCard.Kind.MARKET_CHECKIN)
def checkin_hours_log(request: HttpRequest, *, hub: EventHub, card: HubCard, member: Member) -> HttpResponse:
    """Log my hours, by the actual member. Over htmx the hours section comes back (keeping the date and task) with
    the hero's figures and the thank-you board out of band; a plain post lands back on the page with a message."""
    form = VolunteerHoursForm(request.POST)
    fragment = wants_fragment(request)
    if not form.is_valid():
        if not fragment:
            return _refused_page(request, hub, card, form, hours_form=form)
        return render(request, "hq/partials/_checkin_hours.html", _hours_context(hub, member, form=form))
    message = _hours_message(services.log_hours(hub, member, form.cleaned_data))
    if not fragment:
        messages.success(request, message)
        return redirect(_checkin_url(hub))
    fresh = VolunteerHoursForm(initial=form.kept_for_next())
    context = {
        **_hours_context(hub, member, form=fresh, message=message),
        "countdown": services.checkin_countdown(hub, timezone.now()),
        "band": services.checkin_band(hub),
        "board": services.thank_you_board(hub),
        "me_pk": member.pk,
    }
    return render(request, "hq/partials/_checkin_hours_answer.html", context)


@require_POST
@check_in_required(HubCard.Kind.MARKET_CHECKIN)
def checkin_hours_delete(
    request: HttpRequest, *, hub: EventHub, card: HubCard, member: Member, pk: int
) -> HttpResponse:
    """Delete one hours entry: the member's own, or any for an organiser (by the actual role); 403 otherwise.
    Lands on the organisers' hours page when the delete came from there, else on the check-in page."""
    entry = get_object_or_404(hub.volunteer_hours, pk=pk)
    is_organizer = card.can_organize(member, is_admin=is_actual_admin(request))
    try:
        services.delete_hours(entry, member=member, is_organizer=is_organizer)
    except services.NotYourHours as exc:
        raise PermissionDenied(str(exc)) from exc
    messages.success(request, f"Deleted {entry.hours_display} h on {entry.date:%b} {entry.date.day}.")
    if is_organizer and request.POST.get("back") == "hours":
        return redirect("hq:checkin_hours", slug=hub.slug)
    return redirect(_checkin_url(hub))


@organizer_page(HubCard.Kind.MARKET_CHECKIN)
def checkin_reports(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The organisers' market reports: the totals, sales by guild, and every report (Lee's "staff can see")."""
    return render(
        request,
        "hq/checkin_reports.html",
        {
            "hub": hub,
            "card": card,
            "band": services.checkin_band(hub),
            "by_guild": services.sales_by_guild(hub),
            "reports": list(hub.market_reports.select_related("member", "guild")),
        },
    )


@organizer_page(HubCard.Kind.MARKET_CHECKIN)
def checkin_hours(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The organisers' volunteer hours: the totals and every entry, newest date first, each with a Delete."""
    return render(
        request,
        "hq/checkin_hours.html",
        {
            "hub": hub,
            "card": card,
            "band": services.checkin_band(hub),
            "entries": list(hub.volunteer_hours.select_related("member")),
        },
    )


@organizer_required(HubCard.Kind.MARKET_CHECKIN)
def checkin_reports_csv(request: HttpRequest, *, hub: EventHub, card: HubCard) -> StreamingHttpResponse:
    """Every market report as a CSV download, one row each, by name."""
    return stream_market_reports_csv(hub.market_reports.select_related("member", "guild"), filename_stem=hub.slug)


@organizer_required(HubCard.Kind.MARKET_CHECKIN)
def checkin_hours_csv(request: HttpRequest, *, hub: EventHub, card: HubCard) -> StreamingHttpResponse:
    """Every hours entry as a CSV download, newest date first."""
    return stream_volunteer_hours_csv(hub.volunteer_hours.select_related("member"), filename_stem=hub.slug)


# ---- Event flyer and sharing (Track D, part 5) ----


def _flyer_url(hub: EventHub) -> str:
    return reverse("hq:flyer", args=[hub.slug])


@dataclass(frozen=True)
class FlyerUpload:
    """One of the organisers' upload forms: the slot, the file stored there now (or none) and the form."""

    slot: str
    label: str
    current: Any
    is_pdf: bool
    form: FlyerUploadForm


def _flyer_share_context(hub: EventHub, page: Any) -> dict[str, Any]:
    """What Help spread the word and Find the event render: the link to share (the event's public page, else the
    HQ), Facebook's share dialog for it, the message with the link, What to expect (the wine wall's prices read from
    the ledger) and the page's two links."""
    link = services.share_link(hub)
    return {
        "hub": hub,
        "page": page,
        "share_link": link,
        "facebook_url": services.facebook_share_url(link),
        "share_text": services.share_text(page.share_message, link),
        "share_lede": FLYER_SHARE_LEDE,
        "what_to_expect": services.what_to_expect(services.ledger_settings(hub)),
    }


def _flyer_context(
    request: HttpRequest,
    hub: EventHub,
    card: HubCard,
    *,
    links_form: FlyerLinksForm | None = None,
    upload_form: FlyerUploadForm | None = None,
) -> dict[str, Any]:
    """What the page renders, by the effective role. The RSVP figure and the businesses with flyers are read live
    (the event's people count, the Marketing Tracker's flyer count); the Marketing Tracker link shows only when that
    card is open to the viewer. Admins get Update this page: the two upload forms (``upload_form`` in place of
    its slot's fresh one when it came back with errors) and the links form. The files and links are settings, so the
    card's Team never gets the box (``admin_required`` on each POST)."""
    member = viewer_member(request)
    is_admin = renders_as_admin(request)
    page = services.flyer_page(hub)
    event = hub.event
    # The Marketing Tracker and Vendor Tables links, in one read of the cards: only the doors open to this viewer.
    door_urls = _door_urls(hub, member, is_admin)
    context: dict[str, Any] = {
        **_flyer_share_context(hub, page),
        "card": card,
        "can_configure": is_admin,
        "coming_soon": FLYER_COMING_SOON,
        "last_year_note": FLYER_LAST_YEAR_NOTE,
        "rsvp_count": services.rsvp_count(event) if event is not None else None,
        "rsvp_url": f"{reverse('hub_event_detail', args=[event.pk])}#whos-coming" if event is not None else "",
        "flyers_out": services.marketing_totals(hub)[MarketingKind.FLYER],
        "marketing_url": door_urls.get(HubCard.Kind.MARKETING, ""),
        # Members: want a table to sell your work? Lee's card, linking Vendor Tables when that door is open to them.
        "table_title": FLYER_TABLE_TITLE,
        "table_ask": format_html(
            FLYER_TABLE_ASK,
            email=format_html('<a href="mailto:{0}" class="pl-hq-fl__mail">{0}</a>', FLYER_TABLE_EMAIL),
        ),
        "setup_note": VENDOR_SETUP_NOTE,
        "vendors_url": door_urls.get(HubCard.Kind.VENDOR_TABLES, ""),
    }
    if is_admin:
        uploads = []
        for slot in FlyerSlot:
            form = upload_form if upload_form is not None and upload_form.slot == slot else None
            current = getattr(page, slot)
            uploads.append(
                FlyerUpload(
                    slot=slot.value,
                    label=slot.label,
                    current=current if current else None,
                    is_pdf=page.flyer_is_pdf if slot == FlyerSlot.THIS_YEAR else page.last_year_is_pdf,
                    form=form if form is not None else FlyerUploadForm(slot=slot),
                )
            )
        context.update(
            {
                "uploads": uploads,
                "links_form": links_form if links_form is not None else FlyerLinksForm(instance=page),
                "max_image_bytes": settings.MAX_UPLOAD_IMAGE_BYTES,
                "max_pdf_bytes": settings.MAX_UPLOAD_DOCUMENT_BYTES,
            }
        )
    return context


@card_view(HubCard.Kind.FLYER)
def flyer(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The event flyer and sharing page: this year's flyer to view, download and print, the share buttons, the
    links to the event, the RSVP and flyer figures read live, last year's flyer, and Update this page for
    organisers."""
    return render(request, "hq/flyer.html", _flyer_context(request, hub, card))


def _flyer_slot(slot: str) -> FlyerSlot:
    """The slot named in the URL; any other word is a 404."""
    if slot not in FlyerSlot.values:
        raise Http404("No such flyer.")
    return FlyerSlot(slot)


@card_view(HubCard.Kind.FLYER)
def flyer_download(request: HttpRequest, *, hub: EventHub, card: HubCard, slot: str) -> FileResponse:
    """One flyer as a download, gated like the page: whoever may open the page may save the file. It is named for
    the hub's slug and the slot, ``holiday-bizarre-2026-flyer.jpg`` or ``holiday-bizarre-2026-last-year-flyer.pdf``
    (the slug already carries the year); a slot with no file, or any other word, is a 404. Served from here
    rather than linked straight to storage, because a browser ignores ``download`` on a link to another host and opens
    the file instead."""
    which = _flyer_slot(slot)
    stored = getattr(services.flyer_page(hub), which)
    if not stored:
        raise Http404("No flyer up yet.")
    response = FileResponse(stored.open("rb"))
    filename = f"{hub.slug}-{which.value.replace('_', '-')}{Path(stored.name).suffix.lower()}"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@require_POST
@admin_required(HubCard.Kind.FLYER)
def flyer_upload(request: HttpRequest, *, hub: EventHub, card: HubCard, slot: str) -> HttpResponse:
    """Upload or replace one flyer, by the actual role. A plain multipart post (the form opts out of hx-boost); a
    refused file renders the page with the form's error."""
    which = _flyer_slot(slot)
    form = FlyerUploadForm(request.POST, request.FILES, slot=which)
    if not form.is_valid():
        return render(request, "hq/flyer.html", _flyer_context(request, hub, card, upload_form=form))
    services.set_flyer(hub, which, form.cleaned_data["file"])
    messages.success(request, f"{which.label} uploaded.")
    return redirect(_flyer_url(hub))


@require_POST
@admin_required(HubCard.Kind.FLYER)
def flyer_remove(request: HttpRequest, *, hub: EventHub, card: HubCard, slot: str) -> HttpResponse:
    """Take one flyer off the page and out of storage, by the actual role."""
    which = _flyer_slot(slot)
    if services.remove_flyer(hub, which):
        messages.success(request, f"{which.label} removed.")
    else:
        messages.info(request, f"There was no {which.label} to remove.")
    return redirect(_flyer_url(hub))


@require_POST
@admin_required(HubCard.Kind.FLYER)
def flyer_links(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Save the two links and the share message, by the actual role. Over htmx the form comes back with a toast,
    and Help spread the word and Find the event out of band; a plain post lands back on the page with a message."""
    form = FlyerLinksForm(request.POST, instance=services.flyer_page(hub))
    fragment = wants_fragment(request)
    if not form.is_valid():
        if not fragment:
            return render(request, "hq/flyer.html", _flyer_context(request, hub, card, links_form=form))
        return render(request, "hq/partials/_flyer_links.html", {"hub": hub, "links_form": form})
    page = services.save_flyer_links(hub, form.cleaned_data)
    if not fragment:
        messages.success(request, "Saved.")
        return redirect(_flyer_url(hub))
    context = {**_flyer_share_context(hub, page), "links_form": FlyerLinksForm(instance=page)}
    response = render(request, "hq/partials/_flyer_links_answer.html", context)
    trigger_toast(response, "Saved.", "success")
    return response


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
    wine: dict[str, Any] | None = None,
    launch: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
    guilds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What the page renders. Organisers alone reach it, so every section carries its forms.

    ``keep_line`` is a line whose refused edit (``line_edit``, or ``line_error`` for a stale Save) renders open in its
    row; ``receipt_form`` a refused receipt upload shown on its entry's row; ``approval_error`` a stale approval Save,
    shown over a fresh approval form that renders open. ``bar`` and ``wine`` carry a refused form of the Drink sales
    or Wine Wall section into :func:`_bar_context` or :func:`_wine_ledger_context`, and ``launch``, ``staff`` and
    ``guilds`` one of the launch checklist, Staff time or Guild demos (D6c) into theirs.
    """
    figures = _ledger_figures(request, hub)
    found, band, doors = figures.found, figures.band, figures.doors
    return {
        **_figures_context(request, hub, figures),
        **_ledger_entries_context(hub, band, show, receipt_form=receipt_form),
        **_bar_context(hub, found, doors.bar, **(bar or {})),
        **_wine_ledger_context(request, hub, found, doors.wine, **(wine or {})),
        **_people_context(hub, figures),
        **_launch_context(hub, totals=figures.launch, **(launch or {})),
        **_staff_context(hub, figures, **(staff or {})),
        **_guilds_context(hub, doors.guilds, **(guilds or {})),
        **_vol_context(hub, figures),
        **_flyer_section_context(figures),
        "team_names": _team_names(card),
        "door_hint": _door_hint(request, hub),
        "team_list_id": LEDGER_TEAM_LIST_ID,
        "card": card,
        "title": LEDGER_TITLE,
        "intro": LEDGER_INTRO,
        "receipt_hint": LEDGER_RECEIPT_HINT,
        "footer": LEDGER_FOOTER,
        # The section bar's Sponsors and Vendor tables links: only the doors open to the viewer.
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


@organizer_page(HubCard.Kind.LEDGER)
def ledger(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The PL Ledger: the board summary, the budget lines, Log a Sponsor or Expense, the ledger with ``?filter=`` and
    the receipts report. Organisers only (the card's gate, then a 403), because it is the event's money."""
    show = services.ledger_filter(request.GET.get("filter", ""))
    return render(request, "hq/ledger.html", _ledger_context(request, hub, card, show=show))


@require_POST
@admin_required(HubCard.Kind.LEDGER)
def ledger_approval(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Change the board's approval and its note, by the actual role. A plain post: the approval names the kinds, the
    filters and a column, so the page comes back whole. A refused one renders the page with the form open.

    The form carries the approval it was rendered with (``seen_*``, as the D6b settings forms do): a Save after someone
    else changed the approval (:class:`hq.services.StaleSave`) comes back with the form as the database holds it, with
    :data:`APPROVAL_STALE_SAVE`. A price or tickets save in between never makes it stale.
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
@admin_required(HubCard.Kind.LEDGER)
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
@admin_required(HubCard.Kind.LEDGER)
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
@admin_required(HubCard.Kind.LEDGER)
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
@organizer_required(HubCard.Kind.LEDGER)
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
@organizer_required(HubCard.Kind.LEDGER)
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
@organizer_required(HubCard.Kind.LEDGER)
def ledger_entry_delete(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Take an entry out of the ledger, with its receipt, by the actual role; then the page with the filter."""
    entry = get_object_or_404(hub.ledger_entries, pk=pk)
    entry.delete()
    messages.success(request, f"{entry.source} is out of the ledger.")
    return redirect(_ledger_url(hub, _ledger_posted_filter(request)))


@organizer_required(HubCard.Kind.LEDGER)
def ledger_csv(request: HttpRequest, *, hub: EventHub, card: HubCard) -> StreamingHttpResponse:
    """The whole ledger as a CSV download, oldest first, in Lee's columns."""
    entries = hub.ledger_entries.select_related("line").order_by("date", "created_at", "pk")
    return stream_ledger_csv(entries, approval=services.ledger_settings(hub).approval_words, filename_stem=hub.slug)


@organizer_page(HubCard.Kind.LEDGER)
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


def _wine_section_url(hub: EventHub) -> str:
    return f"{reverse('hq:ledger', args=[hub.slug])}#hq-lg-wine"


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


def _wine_links(request: HttpRequest, hub: EventHub) -> tuple[str, str]:
    """Where the Wine Wall section's two links go, by the effective role: the wall (when its card is open to the viewer)
    and the donors page (when the viewer organises the wall); "" for either the viewer may not open."""
    member = viewer_member(request)
    is_admin = renders_as_admin(request)
    card = hub.cards.prefetch_related("team", "can_open").filter(kind=HubCard.Kind.WINE_WALL).first()
    if card is None or (card.is_hidden and not is_admin) or not card.is_open_to(member, is_admin=is_admin):
        return "", ""
    donors = reverse("hq:wine_donors", args=[hub.slug]) if card.can_organize(member, is_admin=is_admin) else ""
    return card.url, donors


def _wine_ledger_context(
    request: HttpRequest,
    hub: EventHub,
    found: LedgerSettings,
    wine: services.WineBand,
    *,
    tickets_form: TicketsForm | None = None,
    ticket_prices_form: TicketPricesForm | None = None,
    tickets_error: str = "",
    ticket_prices_error: str = "",
) -> dict[str, Any]:
    """What the Wine Wall section renders: the wall's bottles and value read from its band, the tickets with Lee's
    revenue at goal, the donors thanked, the two links, and the tickets and ticket prices forms."""
    wall_url, donors_url = _wine_links(request, hub)
    return {
        "approval": found.approval_words,
        "wine": wine,
        "tickets": services.ticket_totals(found, wine),
        "donor_thanks": services.donor_thanks(hub),
        "wall_url": wall_url,
        "donors_url": donors_url,
        "tickets_form": tickets_form
        if tickets_form is not None
        else TicketsForm(found=found, prefix="tickets", initial=TicketsForm.initial_for(found)),
        "tickets_error": tickets_error,
        "ticket_prices_form": ticket_prices_form
        if ticket_prices_form is not None
        else TicketPricesForm(found=found, prefix="ticket-prices", initial=TicketPricesForm.initial_for(found)),
        "ticket_prices_open": ticket_prices_form is not None or bool(ticket_prices_error),
        "ticket_prices_error": ticket_prices_error,
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
@organizer_required(HubCard.Kind.LEDGER)
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
@organizer_required(HubCard.Kind.LEDGER)
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
@organizer_required(HubCard.Kind.LEDGER)
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
@admin_required(HubCard.Kind.LEDGER)
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
@organizer_required(HubCard.Kind.LEDGER)
def ledger_bar_delete(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Delete one sale from the bar, by the actual role; then the section."""
    sale = get_object_or_404(hub.bar_sales, pk=pk)
    sale.delete()
    messages.success(request, f"Deleted {sale.summary}.")
    return redirect(_bar_url(hub))


@organizer_required(HubCard.Kind.LEDGER)
def ledger_bar_csv(request: HttpRequest, *, hub: EventHub, card: HubCard) -> StreamingHttpResponse:
    """The drink sales as a CSV download, oldest first, with Lee's totals row last."""
    sales = hub.bar_sales.order_by("created_at", "pk")
    return stream_bar_sales_csv(sales, totals=_bar_totals(hub), filename_stem=hub.slug)


def _wine_answer(request: HttpRequest, hub: EventHub, found: LedgerSettings, **forms: Any) -> HttpResponse:
    """The Wine Wall section with every figure the tickets move out of band (:data:`FIGURES_ALL`): the tally's ticket
    sales and cash, the goals, the board paragraph and Raised per staff hour."""
    figures = _ledger_figures(request, hub)
    context = {"hub": hub, **_wine_ledger_context(request, hub, found, figures.doors.wine, **forms)}
    return _figures_answer(request, hub, "hq/partials/_ledger_wine_answer.html", context, figures=figures)


def _wine_refused(request: HttpRequest, hub: EventHub, card: HubCard, refusal: dict[str, Any]) -> HttpResponse:
    """A refused tickets or ticket prices save: the page, or over htmx the Wine Wall section, with ``refusal`` (the
    form with its errors, or a stale save's error over the figures the database holds)."""
    if not wants_fragment(request):
        context = _ledger_context(request, hub, card, show=services.LEDGER_FILTER_ALL, wine=refusal)
        return render(request, "hq/ledger.html", context)
    return _wine_answer(request, hub, services.ledger_settings(hub), **refusal)


def _save_wine_figures(
    request: HttpRequest,
    hub: EventHub,
    card: HubCard,
    form: TicketsForm | TicketPricesForm,
    *,
    key: str,
    saved_message: str,
) -> HttpResponse:
    """Save the tickets or the ticket prices (``key`` names which form), refusing one from an older page. Over htmx the
    Wine Wall section comes back whole with a toast; a plain post lands back on the section."""
    fragment = wants_fragment(request)
    if not form.is_valid():
        return _wine_refused(request, hub, card, {f"{key}_form": form})
    try:
        saved = services.save_ledger_figures(hub, form.to_fields(), seen=form.seen())
    except services.StaleSave:  # the figures changed since the page loaded; show what the database holds
        return _wine_refused(request, hub, card, {f"{key}_error": services.SETTINGS_STALE_SAVE})
    if not fragment:
        messages.success(request, saved_message)
        return redirect(_wine_section_url(hub))
    response = _wine_answer(request, hub, saved)
    trigger_toast(response, saved_message, "success")
    return response


@require_POST
@organizer_required(HubCard.Kind.LEDGER)
def ledger_tickets(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The wine wall's tickets sold (singles and two-ticket deals), by the actual role."""
    found = services.ledger_settings(hub)
    form = TicketsForm(request.POST, found=found, prefix="tickets")
    return _save_wine_figures(request, hub, card, form, key="tickets", saved_message="Tickets updated.")


@require_POST
@admin_required(HubCard.Kind.LEDGER)
def ledger_ticket_prices(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The wine wall's two ticket prices, by the actual role; the ticket inputs' labels follow them."""
    found = services.ledger_settings(hub)
    form = TicketPricesForm(request.POST, found=found, prefix="ticket-prices")
    return _save_wine_figures(request, hub, card, form, key="ticket_prices", saved_message="Ticket prices saved.")


# ---- PL Ledger, team and tally (Track D, part 6c) ----

# A row's Save whose ``updated_at`` no longer matches the database: someone else saved it since the page loaded.
LAUNCH_STALE_SAVE = "Someone else changed this task. Your page has been refreshed; make your change again."
GUILD_STALE_SAVE = "Someone else changed this guild. Your page has been refreshed; make your change again."


def _ledger_section_url(hub: EventHub, anchor: str) -> str:
    return f"{reverse('hq:ledger', args=[hub.slug])}#{anchor}"


def _door_urls(hub: EventHub, member: Member | None, is_admin: bool) -> dict[str, str]:
    """Where each door's page is for this viewer, in one read of the cards: a ``HubCard.Kind`` to its page, for every
    live door that is shown and open to them (:func:`_door_url`'s rule for all of them at once)."""
    urls: dict[str, str] = {}
    for card in hub.cards.prefetch_related("team", "can_open").exclude(kind=HubCard.Kind.PLACEHOLDER):
        if (card.is_hidden and not is_admin) or not card.is_open_to(member, is_admin=is_admin):
            continue
        if card.url:
            urls[card.kind] = card.url
    return urls


def _team_names(card: HubCard) -> list[str]:
    """The ledger card Team's display names, for the team datalist (Owner, Who, Contacted by)."""
    return sorted({named.display_name for named in card.team.all() if named.display_name.strip()})


@dataclass(frozen=True)
class LedgerFigures:
    """Every figure the ledger's tally, goals and board paragraph read, gathered once per request: the settings, the
    ledger's band, every other door's figures, the doors open to the viewer, the launch checklist and the flyer page."""

    found: LedgerSettings
    band: services.LedgerBand
    doors: services.DoorFigures
    door_urls: dict[str, str]
    launch: services.LaunchTotals
    flyer: Any


def _ledger_figures(request: HttpRequest, hub: EventHub) -> LedgerFigures:
    found = services.ledger_settings(hub)
    band = services.ledger_band(hub, found)
    return LedgerFigures(
        found=found,
        band=band,
        doors=services.door_figures(hub, band, found),
        door_urls=_door_urls(hub, viewer_member(request), renders_as_admin(request)),
        launch=services.launch_totals(hub),
        flyer=services.flyer_page(hub),
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
            flyer_ready=bool(figures.flyer.flyer),
            door_urls=figures.door_urls,
        ),
        # Staff time's figures (hq/partials/_ledger_staff_figures.html): Raised per staff hour reads the event cash.
        "staff": doors.staff,
        "volunteer_hours": doors.checkin,
        # Change the goals is Edit HQ's goals formset, an admin page, by the effective role as the page renders.
        "edit_goals_url": f"{reverse('hq:edit', args=[hub.slug])}#hq-edit-goals" if renders_as_admin(request) else "",
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
    """What Attendance and Vendors renders: the attendance and the vendors' figures, read from Vendor Tables and the
    Market Check-in, the door clicker, Lee's No double entry note with the two doors, and the reports."""
    urls = figures.door_urls
    return {
        "attendance": figures.doors.attendance,
        "people_vendors": figures.doors.vendors,
        "checkin": figures.doors.checkin,
        "reports": list(hub.market_reports.select_related("guild")),
        "vendors_url": urls.get(HubCard.Kind.VENDOR_TABLES, ""),
        "checkin_url": urls.get(HubCard.Kind.MARKET_CHECKIN, ""),
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
    """What Staff time renders: the hours by person and by area against the event cash, the volunteer hours from the
    Market Check-in, Log time (open when refused) and every entry with its Delete."""
    return {
        "staff": figures.doors.staff,
        "volunteer_hours": figures.doors.checkin,
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


def _vol_context(hub: EventHub, figures: LedgerFigures) -> dict[str, Any]:
    """What Volunteer hours renders, read only from the Market Check-in: the totals, the top ten and the two doors."""
    urls = figures.door_urls
    return {
        "vol_band": figures.doors.checkin,
        "vol_board": services.thank_you_board(hub, limit=VOL_BOARD_LIMIT),
        "signup_url": urls.get(HubCard.Kind.VOLUNTEERS, ""),
        "checkin_url": urls.get(HubCard.Kind.MARKET_CHECKIN, ""),
    }


# How many volunteers the ledger's Volunteer hours shows (Lee's top ten; the Market Check-in's board shows fifteen).
VOL_BOARD_LIMIT = 10


def _flyer_section_context(figures: LedgerFigures) -> dict[str, Any]:
    """What Event flyer renders, read only: the flyer page's flyer and the Marketing Tracker's businesses with it up."""
    urls = figures.door_urls
    return {
        "hub_flyer": figures.flyer,
        "flyer_count": figures.doors.flyers,
        "flyer_url": urls.get(HubCard.Kind.FLYER, ""),
        "marketing_url": urls.get(HubCard.Kind.MARKETING, ""),
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
@organizer_required(HubCard.Kind.LEDGER)
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


@organizer_required(HubCard.Kind.LEDGER)
def ledger_people_csv(request: HttpRequest, *, hub: EventHub, card: HubCard) -> StreamingHttpResponse:
    """The vendors' reports with the totals and the attendance as a CSV download, in Lee's columns."""
    reports = hub.market_reports.select_related("guild")
    return stream_people_csv(
        reports,
        vendors=services.vendor_band(hub).confirmed,
        checkin=services.checkin_band(hub),
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
@organizer_required(HubCard.Kind.LEDGER)
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
@organizer_required(HubCard.Kind.LEDGER)
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
@organizer_required(HubCard.Kind.LEDGER)
def ledger_launch_delete(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Take a task off the launch checklist, by the actual role; then the section."""
    task = get_object_or_404(hub.launch_tasks, pk=pk)
    task.delete()
    messages.success(request, f"{task.task} is off the checklist.")
    return redirect(_ledger_section_url(hub, "hq-lg-launch"))


@require_POST
@organizer_required(HubCard.Kind.LEDGER)
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
    entry = services.log_staff_time(hub, form.to_fields(), team=list(card.team.all()), member=actual_member(request))
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
@organizer_required(HubCard.Kind.LEDGER)
def ledger_staff_delete(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Delete one stretch of staff time, by the actual role; then the section."""
    entry = get_object_or_404(hub.staff_time, pk=pk)
    entry.delete()
    messages.success(request, f"Deleted {entry.who}'s {entry.hours_words}.")
    return redirect(_ledger_section_url(hub, "hq-lg-staff"))


@organizer_required(HubCard.Kind.LEDGER)
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
@organizer_required(HubCard.Kind.LEDGER)
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
@organizer_required(HubCard.Kind.LEDGER)
def ledger_guilds_add_all(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Add every active guild not on the list yet, by the actual role, each with its guild lead."""
    added = services.add_every_active_guild(hub)
    message = f"Added {added} guild{'' if added == 1 else 's'}." if added else "Every active guild is on the list."
    if not wants_fragment(request):
        messages.success(request, message)
        return redirect(_ledger_section_url(hub, "hq-lg-guilds"))
    return _guilds_section(request, hub, message)


@require_POST
@organizer_required(HubCard.Kind.LEDGER)
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
@organizer_required(HubCard.Kind.LEDGER)
def ledger_guild_delete(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Take a guild off the Guild demos list, by the actual role; then the section."""
    row = get_object_or_404(hub.guild_demos.select_related("guild"), pk=pk)
    row.delete()
    messages.success(request, f"{row.guild.name} is off the list.")
    return redirect(_ledger_section_url(hub, "hq-lg-guilds"))
