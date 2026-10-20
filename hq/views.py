"""The HQ page and its edit pages (Track C, part 1), the volunteer sign-up page (part 2), the wine wall (part 3),
the Marketing Tracker (Track D, part 1), the Sponsorship Tracker (Track D, part 2) and Vendor Tables (Track D, part 3).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.abuse_limits import record_keyed_attempt
from core.htmx import wants_fragment
from hq import services
from hq.access import (
    actual_member,
    card_view,
    hub_or_404,
    is_actual_admin,
    organizer_page,
    organizer_required,
    renders_as_admin,
    viewer_member,
)
from hq.exports import stream_donors_csv, stream_roster_csv, stream_sponsors_csv, stream_vendor_tables_csv
from hq.forms import (
    MARKETING_TEAM_LIST_ID,
    SPONSOR_TEAM_LIST_ID,
    HubCardForm,
    HubForm,
    HubGoalFormSet,
    MarketingActionForm,
    SponsorEditForm,
    SponsorForm,
    SponsorLogForm,
    VendorFloorForm,
    VendorTableEditForm,
    VendorTableForm,
    VolunteerInfoForm,
    VolunteerShiftForm,
    VolunteerShiftFormSet,
    WineBottleForm,
    WineryLogoForm,
)
from hq.holiday_bizarre import SPONSOR_TIERS, VENDOR_SETUP_NOTE, WINE_WALL_BENEFIT
from hq.models import EventHub, HubCard, ShiftSignup, Sponsor, VendorTable, VolunteerShift
from hq.services import VolunteerBand, VolunteerInfo, goal_rows
from hub.toast import trigger_toast
from hub.view_as import fog_admin_required
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
    """The HQ page. No login required: a signed-out visitor sees the public doors."""
    hub = hub_or_404(slug, request)
    is_admin = renders_as_admin(request)
    member = viewer_member(request)
    cards = list(hub.cards.prefetch_related("named_members"))
    if not is_admin:
        cards = [card for card in cards if not card.is_hidden and card.is_open_to(member, is_admin=False)]
    return render(
        request,
        "hq/page.html",
        {
            "hub": hub,
            "cards": cards,
            "goals": goal_rows(hub),
            "countdown": hub.countdown_parts(timezone.now()),
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
    """Bring Lee's doors up to date on this hub: built doors adopt their placeholders, untouched words take her latest."""
    with transaction.atomic():
        # The row lock makes a double-submit wait for the first, so it finds the doors made and adds none.
        hub = get_object_or_404(EventHub.objects.select_for_update(), slug=slug)
        summary = hub.seed_default_cards().summary
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
    """One card: its words, who may open it, the named members, hidden, and its place in the order.

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
    rows = form.member_rows()
    return {
        "hub": hub,
        "card": card,
        "form": form,
        "shift_formset": shifts,
        "member_rows": rows,
        # A failed save on the named members opens the row so the error is in view.
        "named_open": "named_members" in form.errors,
        "directions": HubCard.Direction,
    }


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
    card = hub.cards.prefetch_related("named_members").filter(kind=HubCard.Kind.WINE_WALL).first()
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
    return stream_roster_csv(ShiftSignup.objects.filter(shift__hub=hub), filename_stem=hub.slug)


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
    guest sees no donors, no logo form and no Delete. The action views keep the actual role.
    """
    member = viewer_member(request)
    can_organize = card.can_organize(member, is_admin=renders_as_admin(request))
    _name, email = _identity(request, member)
    bottles = list(hub.bottles.all())
    rows = services.wineries(hub)
    donors = services.donor_rows(hub) if can_organize else []
    if can_organize and logo_form is None:
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
            "logo_form": logo_form if can_organize else None,
            "can_organize": can_organize,
            "donors": donors,
            "donor_emails": services.donor_emails(donors),
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
@organizer_required(HubCard.Kind.WINE_WALL)
def winery_logo(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """Upload a logo for a winery named on the wall, replacing the one under that name."""
    form = WineryLogoForm(request.POST, request.FILES, names=[row.name for row in services.wineries(hub)])
    if not form.is_valid():
        name, _email = _identity(request, viewer_member(request))
        return _wine_page(request, hub, card, form=WineBottleForm(initial={"credited_name": name}), logo_form=form)
    logo = services.set_winery_logo(hub, form.cleaned_data["name"], form.cleaned_data["logo"])
    messages.success(request, f"Logo saved for {logo.name}.")
    return redirect(_wall_url(hub))


@organizer_required(HubCard.Kind.WINE_WALL)
def wine_donors(request: HttpRequest, *, hub: EventHub, card: HubCard) -> HttpResponse:
    """The donors on their own page: every giver, their type, email and listing, Copy donor emails and the CSV."""
    donors = services.donor_rows(hub)
    return render(
        request,
        "hq/wine_donors.html",
        {"hub": hub, "card": card, "donors": donors, "donor_emails": services.donor_emails(donors)},
    )


@organizer_required(HubCard.Kind.WINE_WALL)
def wine_donors_csv(request: HttpRequest, *, hub: EventHub, card: HubCard) -> StreamingHttpResponse:
    """The donors table as a CSV download, one row per giver."""
    return stream_donors_csv(services.donor_rows(hub), filename_stem=hub.slug)


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
        "team": sorted({member.display_name for member in card.named_members.all()}),
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

    ``open_pk`` is the row to render open (the one just saved, logged or refused), with ``edit_form`` and
    ``log_form`` in place of its fresh forms when they came back with errors.
    """
    member = viewer_member(request)
    is_admin = renders_as_admin(request)
    can_organize = card.can_organize(member, is_admin=is_admin)
    today = timezone.localdate()
    if not can_organize and show == services.SPONSOR_FILTER_DONORS:
        # Last year's gifts are the organisers' notes; the filter would list who gave.
        show = services.SPONSOR_FILTER_ALL
    sponsors_qs = services.filtered_sponsors(hub, show)
    if can_organize:
        sponsors_qs = sponsors_qs.prefetch_related("log_entries")
    rows = []
    for sponsor in sponsors_qs:
        is_open = sponsor.pk == open_pk
        row_edit: SponsorEditForm | None = None
        row_log: SponsorLogForm | None = None
        if can_organize:
            row_edit = edit_form if is_open and edit_form is not None else None
            if row_edit is None:
                row_edit = SponsorEditForm(initial=SponsorEditForm.initial_for(sponsor), prefix=_edit_prefix(sponsor))
            row_log = log_form if is_open and log_form is not None else None
            if row_log is None:
                row_log = SponsorLogForm(prefix=_log_prefix(sponsor))
        rows.append(SponsorRow(sponsor=sponsor, edit_form=row_edit, log_form=row_log, is_open=is_open))
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
        "team": sorted({named.display_name for named in card.named_members.all()}),
        "team_list_id": SPONSOR_TEAM_LIST_ID,
        "tiers": SPONSOR_TIERS,
        "wine_benefit": WINE_WALL_BENEFIT,
        "wine_url": _wine_url(hub, member, is_admin),
    }


def _sponsor_board(request: HttpRequest, context: dict[str, Any], message: str = "") -> HttpResponse:
    """The board over htmx (with a toast when ``message`` is set), or the whole page for a plain post that failed."""
    if not wants_fragment(request):
        return render(request, "hq/sponsors.html", context)
    response = render(request, "hq/partials/_sponsor_board.html", context)
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
        context = _sponsors_context(request, hub, card, show=show, open_pk=sponsor.pk, edit_form=form)
        return _sponsor_board(request, context)
    services.update_sponsor(sponsor, form.to_fields())
    if not wants_fragment(request):
        messages.success(request, f"{sponsor.organization} saved.")
        return redirect(_sponsors_url(hub, show))
    context = _sponsors_context(request, hub, card, show=show, open_pk=sponsor.pk)
    return _sponsor_board(request, context, "Saved.")


@require_POST
@organizer_required(HubCard.Kind.SPONSORSHIP)
def sponsor_log(request: HttpRequest, *, hub: EventHub, card: HubCard, pk: int) -> HttpResponse:
    """Add one line to a sponsor's contact log, by the actual role."""
    sponsor = get_object_or_404(hub.sponsors, pk=pk)
    show = _posted_filter(request)
    form = SponsorLogForm(request.POST, prefix=_log_prefix(sponsor))
    if not form.is_valid():
        context = _sponsors_context(request, hub, card, show=show, open_pk=sponsor.pk, log_form=form)
        return _sponsor_board(request, context)
    services.log_contact(sponsor, text=form.cleaned_data["text"], by=form.cleaned_data["by"])
    if not wants_fragment(request):
        messages.success(request, "Added to the log.")
        return redirect(_sponsors_url(hub, show))
    context = _sponsors_context(request, hub, card, show=show, open_pk=sponsor.pk)
    return _sponsor_board(request, context, "Added to the log.")


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


def _vendor_prefix(table: VendorTable) -> str:
    return f"vendor-{table.pk}"


def _vendor_form_id(table: VendorTable) -> str:
    return f"hq-vt-row-{table.pk}"


def _vendor_edit_form(table: VendorTable, guilds: list[Guild], data: Any = None) -> VendorTableEditForm:
    return VendorTableEditForm(
        data, instance=table, guilds=guilds, prefix=_vendor_prefix(table), form_id=_vendor_form_id(table)
    )


def _vendor_team(card: HubCard) -> tuple[str, str]:
    """Who manages the door, from its first named member: their display name for the header ("" for nobody) and
    their first name for the Requested tile, else the vendor team."""
    named = list(card.named_members.all())
    if not named:
        return "", services.VENDOR_TEAM_FALLBACK
    name = named[0].display_name
    return name, name.split()[0]


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
) -> dict[str, Any]:
    """What the page and its board render. Organisers alone reach it, so every row carries its edit form.

    ``keep_pk`` is a request kept in view whatever the filter (the one just added or refused), and
    ``row_form`` that row's bound form when its Save was refused.
    """
    guilds = services.active_guilds()
    band_context = _vendor_band_context(hub, card)
    band: services.VendorBand = band_context["band"]
    rows = []
    for table in services.filtered_vendor_tables(hub, show, keep_pk=keep_pk):
        form = row_form if row_form is not None and table.pk == keep_pk else _vendor_edit_form(table, guilds)
        rows.append(VendorRow(table=table, form=form))
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
    what is typed in any other row stays put; a refused Save comes back with its errors and saves nothing."""
    table = get_object_or_404(hub.vendor_tables.select_related("guild"), pk=pk)
    show = _vendor_posted_filter(request)
    guilds = services.active_guilds()
    form = _vendor_edit_form(table, guilds, request.POST)
    fragment = wants_fragment(request)
    if not form.is_valid():
        if not fragment:
            context = _vendors_context(request, hub, card, show=show, keep_pk=table.pk, row_form=form)
            return render(request, "hq/vendors.html", context)
        # The refused form changed the row in memory; the row's own words come from the database.
        saved = hub.vendor_tables.select_related("guild").get(pk=table.pk)
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
@organizer_required(HubCard.Kind.VENDOR_TABLES)
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
