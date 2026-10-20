"""The HQ page and its edit pages (Track C, part 1), the volunteer sign-up page (part 2), the wine wall (part 3)
and the Marketing Tracker (Track D, part 1)."""

from __future__ import annotations

from dataclasses import asdict
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
    organizer_required,
    renders_as_admin,
    viewer_member,
)
from hq.exports import stream_donors_csv, stream_roster_csv
from hq.forms import (
    MARKETING_TEAM_LIST_ID,
    HubCardForm,
    HubForm,
    HubGoalFormSet,
    MarketingActionForm,
    VolunteerInfoForm,
    VolunteerShiftForm,
    VolunteerShiftFormSet,
    WineBottleForm,
    WineryLogoForm,
)
from hq.models import EventHub, HubCard, ShiftSignup, VolunteerShift
from hq.services import VolunteerBand, VolunteerInfo, goal_rows
from hub.toast import trigger_toast
from hub.view_as import fog_admin_required
from membership.models import Member

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
