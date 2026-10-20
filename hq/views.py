"""The HQ page and its edit pages (Track C, part 1), the volunteer sign-up page (part 2), the wine wall (part 3),
the Marketing Tracker (Track D, part 1), the Sponsorship Tracker (Track D, part 2), Vendor Tables (Track D, part 3)
the Market Check-in (Track D, part 4) and the event flyer and sharing page (Track D, part 5).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from core.abuse_limits import record_keyed_attempt
from core.htmx import wants_fragment
from hq import services
from hq.access import (
    actual_member,
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
    stream_donors_csv,
    stream_market_reports_csv,
    stream_roster_csv,
    stream_sponsors_csv,
    stream_vendor_tables_csv,
    stream_volunteer_hours_csv,
)
from hq.forms import (
    MARKETING_TEAM_LIST_ID,
    SPONSOR_TEAM_LIST_ID,
    FlyerLinksForm,
    FlyerUploadForm,
    HubCardForm,
    HubForm,
    HubGoalFormSet,
    MarketingActionForm,
    MarketReportForm,
    SponsorEditForm,
    SponsorForm,
    SponsorLogForm,
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
    FLYER_COMING_SOON,
    FLYER_LAST_YEAR_NOTE,
    FLYER_SHARE_LEDE,
    SPONSOR_TIERS,
    VENDOR_SETUP_NOTE,
    WINE_WALL_BENEFIT,
)
from hq.models import (
    EventHub,
    FlyerSlot,
    HubCard,
    MarketingKind,
    MarketReport,
    ShiftSignup,
    Sponsor,
    VendorTable,
    VolunteerHours,
    VolunteerShift,
    format_hours,
    format_money,
)
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
    """Bring Lee's doors up to date on this hub: built doors adopt their placeholders, untouched words take her latest,
    and a counted goal the hub lacks by kind is added (:func:`hq.services.seed_goals`)."""
    with transaction.atomic():
        # The row lock makes a double-submit wait for the first, so it finds the doors and goals made and adds none.
        hub = get_object_or_404(EventHub.objects.select_for_update(), slug=slug)
        seeded = hub.seed_default_cards()
        seeded.goals_added = services.seed_goals(hub).added
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
    return _door_url(hub, HubCard.Kind.WINE_WALL, member, is_admin)


def _door_url(hub: EventHub, kind: str, member: Member | None, is_admin: bool) -> str:
    """Where a link to the ``kind`` door goes, or "" when that card is missing, hidden or not open to this viewer."""
    card = hub.cards.prefetch_related("named_members").filter(kind=kind).first()
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
        "team": sorted({named.display_name for named in card.named_members.all()}),
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
    """Who manages the door, from its first named member by pk: their display name for the header ("" for nobody
    or a blank name) and their first name for the Requested tile, else the vendor team."""
    first = card.named_members.order_by("pk").first()
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
        table = get_object_or_404(hub.vendor_tables.select_related("guild").select_for_update(of=("self",)), pk=pk)
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
        "countdown": hub.countdown_parts(timezone.now()),
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
        "countdown": hub.countdown_parts(timezone.now()),
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
    HQ), Facebook's share dialog for it, the message with the link, and the page's two links."""
    link = services.share_link(hub)
    return {
        "hub": hub,
        "page": page,
        "share_link": link,
        "facebook_url": services.facebook_share_url(link),
        "share_text": services.share_text(page.share_message, link),
        "share_lede": FLYER_SHARE_LEDE,
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
    card is open to the viewer. Organisers get Update this page: the two upload forms (``upload_form`` in place of
    its slot's fresh one when it came back with errors) and the links form."""
    member = viewer_member(request)
    is_admin = renders_as_admin(request)
    page = services.flyer_page(hub)
    event = hub.event
    can_organize = card.can_organize(member, is_admin=is_admin)
    context: dict[str, Any] = {
        **_flyer_share_context(hub, page),
        "card": card,
        "can_organize": can_organize,
        "coming_soon": FLYER_COMING_SOON,
        "last_year_note": FLYER_LAST_YEAR_NOTE,
        "rsvp_count": services.rsvp_count(event) if event is not None else None,
        "rsvp_url": f"{reverse('hub_event_detail', args=[event.pk])}#whos-coming" if event is not None else "",
        "flyers_out": services.marketing_totals(hub)[MarketingKind.FLYER],
        "marketing_url": _door_url(hub, HubCard.Kind.MARKETING, member, is_admin),
    }
    if can_organize:
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


@require_POST
@organizer_required(HubCard.Kind.FLYER)
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
@organizer_required(HubCard.Kind.FLYER)
def flyer_remove(request: HttpRequest, *, hub: EventHub, card: HubCard, slot: str) -> HttpResponse:
    """Take one flyer off the page and out of storage, by the actual role."""
    which = _flyer_slot(slot)
    if services.remove_flyer(hub, which):
        messages.success(request, f"{which.label} removed.")
    else:
        messages.info(request, f"There was no {which.label} to remove.")
    return redirect(_flyer_url(hub))


@require_POST
@organizer_required(HubCard.Kind.FLYER)
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
