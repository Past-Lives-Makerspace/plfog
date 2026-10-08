"""Kiln ticket screens for makers, and the crew's Load the Kiln, ticket view and lists (#691)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.htmx import wants_fragment
from kiln.access import crew_required, is_crew, kiln_open_required, maker_required
from kiln.forms import (
    ACTION_COVER,
    ACTION_DRAFT,
    ACTION_REMOVE,
    KilnTicketForm,
    ListOptionForm,
    LoadKilnForm,
    ManualFlagForm,
    ReplyForm,
)
from kiln.models import ClayOption, GlazeOption, KilnFiring, KilnFlag, KilnTicket, ListOption, ListOptionNameTaken
from kiln.services import TicketNotEditable, load_kiln, load_queue, post_reply, primary_emails, save_ticket

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from membership.models import Member

# How many fired tickets My Tickets shows before "Show older tickets".
HISTORY_PAGE = 10

LIST_MODELS: dict[str, type[ListOption]] = {"clay": ClayOption, "glaze": GlazeOption}


def _member(request: HttpRequest) -> Member:
    member: Member = request.user.member  # type: ignore[union-attr]  # maker_required / crew_required ran
    return member


def _tickets_for(member: Member) -> QuerySet[KilnTicket]:
    return member.kiln_tickets.select_related("clay").prefetch_related("photos", "flags", "studio_glazes")


def _crew_tabs() -> dict[str, Any]:
    """What the crew's tab row needs: the count on Load the Kiln."""
    return {"is_crew": True, "waiting_count": KilnTicket.objects.waiting().count()}


@login_required
@kiln_open_required
@maker_required
def my_tickets(request: HttpRequest) -> HttpResponse:
    """My Tickets: drafts, the queue and the kiln on top, fired tickets below."""
    member = _member(request)
    tickets = list(_tickets_for(member))
    by_status: dict[str, list[KilnTicket]] = {status: [] for status in KilnTicket.Status.values}
    for ticket in tickets:
        by_status[ticket.status].append(ticket)
    show_older = request.GET.get("older") == "1"
    history = by_status[KilnTicket.Status.FIRED]
    context = {
        "drafts": by_status[KilnTicket.Status.DRAFT],
        "queued": by_status[KilnTicket.Status.SUBMITTED],
        "loaded": by_status[KilnTicket.Status.LOADED],
        "history": history if show_older else history[:HISTORY_PAGE],
        "has_older": not show_older and len(history) > HISTORY_PAGE,
        "is_crew": False,
        "kiln_tab": "mine",
    }
    if is_crew(member):
        context.update(_crew_tabs())
    return render(request, "kiln/my_tickets.html", context)


def _form_context(form: KilnTicketForm, ticket: KilnTicket | None, copied_from: KilnTicket | None) -> dict[str, Any]:
    photos = list(ticket.photos.all()) if ticket is not None else []
    return {
        "form": form,
        "ticket": ticket,
        "photos": photos,
        "copied_from": copied_from,
        "flag_notes": KilnFlag.MAKER_NOTES,
    }


def _after_save(request: HttpRequest, form: KilnTicketForm, ticket: KilnTicket, was_draft: bool) -> HttpResponse:
    """Where each button lands: the photo buttons back on the form, the rest on My Tickets."""
    if form.action in (ACTION_COVER, ACTION_REMOVE):
        return redirect(f"{reverse('kiln:edit', args=[ticket.pk])}#kiln-photos")
    if form.is_strict:
        text = f"Ticket {ticket.pk} is in the queue." if was_draft else f"Ticket {ticket.pk} is updated."
        messages.success(request, text)
    else:
        messages.success(request, "Draft saved. Finish it any time from My Tickets.")
    return redirect("kiln:mine")


def _handle_form(request: HttpRequest, ticket: KilnTicket | None, copied_from: KilnTicket | None) -> HttpResponse:
    member = _member(request)
    photo_pks = [p.pk for p in ticket.photos.all()] if ticket is not None else []
    instance = ticket if ticket is not None else KilnTicket(maker=member)
    if request.method == "POST":
        was_draft = instance.status == KilnTicket.Status.DRAFT
        form = KilnTicketForm(
            request.POST,
            request.FILES,
            instance=instance,
            action=request.POST.get("action", ACTION_DRAFT),
            maker_photo_pks=photo_pks,
        )
        if form.is_valid():
            try:
                saved = save_ticket(form, member)
            except TicketNotEditable:
                return _already_loaded(request, instance)
            return _after_save(request, form, saved, was_draft)
    else:
        initial = copied_from.copy_initial() if copied_from is not None else None
        form = KilnTicketForm(instance=instance, initial=initial, maker_photo_pks=photo_pks)
    return render(request, "kiln/ticket_form.html", _form_context(form, ticket, copied_from))


@login_required
@kiln_open_required
@maker_required
def ticket_new(request: HttpRequest) -> HttpResponse:
    """A new ticket, blank or copied from one of the maker's own (``?from=<pk>``)."""
    copied_from = None
    source = request.GET.get("from", "")
    if source:
        if not source.isdigit():
            raise Http404("No such ticket.")
        copied_from = get_object_or_404(_tickets_for(_member(request)), pk=int(source))
    return _handle_form(request, None, copied_from)


@login_required
@kiln_open_required
@maker_required
def ticket_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Change a draft, or a ticket in the queue until the crew loads it."""
    ticket = get_object_or_404(_tickets_for(_member(request)), pk=pk)
    if not ticket.is_editable:
        return _already_loaded(request, ticket)
    return _handle_form(request, ticket, None)


def _already_loaded(request: HttpRequest, ticket: KilnTicket) -> HttpResponse:
    messages.info(request, "This piece is already in the kiln, so the ticket can no longer change.")
    return redirect("kiln:detail", pk=ticket.pk)


def _visible_ticket(member: Member, pk: int, *, crew: bool) -> KilnTicket:
    """A ticket the viewer may open (any, for the crew; their own, for a maker), or a 404."""
    tickets = (
        KilnTicket.objects.visible_to(member, crew=crew)
        .select_related("maker__user", "clay", "firing__loaded_by__user")
        .prefetch_related(
            "photos",
            "studio_glazes",
            "flags__added_by",
            "flags__cleared_by",
            "replies__author__user",
            primary_emails("maker__user"),
            primary_emails("firing__loaded_by__user"),
            primary_emails("replies__author__user"),
        )
    )
    return get_object_or_404(tickets, pk=pk)


@login_required
@kiln_open_required
@maker_required
def ticket_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """One ticket. A maker sees their own; the crew see any, with every flag and the maker's contact."""
    member = _member(request)
    crew = is_crew(member)
    ticket = _visible_ticket(member, pk, crew=crew)
    context = {
        "ticket": ticket,
        "photos": list(ticket.photos.all()),
        "replies": list(ticket.replies.all()),
        "viewer": member,
        "reply_form": ReplyForm(),
    }
    if crew:
        context["flag_form"] = ManualFlagForm()
        return render(request, "kiln/ticket_detail_crew.html", context)
    return render(request, "kiln/ticket_detail.html", context)


def _to_messages(ticket: KilnTicket) -> HttpResponse:
    return redirect(f"{reverse('kiln:detail', args=[ticket.pk])}#kiln-messages")


@login_required
@kiln_open_required
@maker_required
@require_POST
def ticket_reply(request: HttpRequest, pk: int) -> HttpResponse:
    """Post to a ticket's thread: the maker on their own ticket, the crew on any."""
    member = _member(request)
    ticket = _visible_ticket(member, pk, crew=is_crew(member))
    form = ReplyForm(request.POST)
    if not form.is_valid():
        messages.error(request, form.first_error)
        return _to_messages(ticket)
    post_reply(ticket, member, form.cleaned_data["body"])
    messages.success(request, "Message sent.")
    return _to_messages(ticket)


@login_required
@kiln_open_required
@crew_required
@require_POST
def flag_add(request: HttpRequest, pk: int) -> HttpResponse:
    """The crew flag a ticket by hand, with a note only the crew see."""
    ticket = get_object_or_404(KilnTicket, pk=pk)
    form = ManualFlagForm(request.POST)
    if not form.is_valid():
        messages.error(request, form.first_error)
    else:
        ticket.add_flag(form.cleaned_data["note"], by=_member(request))
        messages.success(request, "Flag added. Only the crew can see it.")
    return redirect("kiln:detail", pk=ticket.pk)


@login_required
@kiln_open_required
@crew_required
@require_POST
def flag_clear(request: HttpRequest, pk: int, flag_pk: int) -> HttpResponse:
    """The crew checked a flag, automatic or by hand, and clear it."""
    flag = get_object_or_404(KilnFlag, pk=flag_pk, ticket_id=pk)
    flag.clear(by=_member(request))
    messages.success(request, "Flag cleared.")
    return redirect("kiln:detail", pk=pk)


# ---- loading the kiln ------------------------------------------------------------------


@login_required
@kiln_open_required
@crew_required
def load(request: HttpRequest) -> HttpResponse:
    """Load the Kiln: every waiting ticket as a photo tile; tick the ones going in and confirm."""
    if request.method == "POST":
        form = LoadKilnForm(request.POST)
        if not form.is_valid():
            messages.error(request, form.first_error)
            return redirect("kiln:load")
        firing_type = form.cleaned_data["firing_type"]
        result = load_kiln(
            firing_type=firing_type,
            ticket_pks=[t.pk for t in form.cleaned_data["tickets"]],
            by=_member(request),
        )
        if result.firing is None:
            messages.error(request, result.message)
        else:
            messages.success(request, result.message)
        return redirect(f"{reverse('kiln:load')}?type={firing_type}")
    queue = load_queue()
    chosen = request.GET.get("type", "")
    firing_type = chosen if chosen in KilnTicket.FiringType.values else queue.longest_waiting_type
    context = {
        "queue": queue,
        "firing_type": firing_type,
        "next_number": KilnFiring.next_number(),
        "kiln_tab": "load",
        "is_crew": True,
        "waiting_count": len(queue.tickets),
    }
    return render(request, "kiln/load.html", context)


# ---- the crew's lists ------------------------------------------------------------------


def _list_model(kind: str) -> type[ListOption]:
    if kind not in LIST_MODELS:
        raise Http404("No such list.")
    return LIST_MODELS[kind]


def _list_card_context(kind: str, error: str = "", saved_pk: int | None = None) -> dict[str, Any]:
    from django.db.models import Count

    model = _list_model(kind)
    options = list(model.objects.annotate(ticket_count=Count("tickets")).select_related("archived_by"))  # type: ignore[attr-defined]
    return {
        "kind": kind,
        "title": "Clay Bodies" if kind == "clay" else "Studio Glazes",
        "noun": "clay" if kind == "clay" else "glaze",
        "active": [o for o in options if o.archived_at is None],
        "archived": [o for o in options if o.archived_at is not None],
        "error": error,
        "saved_pk": saved_pk,
    }


@login_required
@kiln_open_required
@crew_required
def lists(request: HttpRequest) -> HttpResponse:
    """The clay and studio glaze lists every new ticket offers."""
    context = {
        "cards": [_list_card_context("clay"), _list_card_context("glaze")],
        "kiln_tab": "lists",
        **_crew_tabs(),
    }
    return render(request, "kiln/lists.html", context)


def _list_response(request: HttpRequest, kind: str, error: str = "", saved_pk: int | None = None) -> HttpResponse:
    """The refreshed card for htmx, or the Lists page for a plain form post."""
    if wants_fragment(request):
        return render(request, "kiln/partials/_list_card.html", {"card": _list_card_context(kind, error, saved_pk)})
    if error:
        messages.error(request, error)
    return redirect("kiln:lists")


def _first_error(form: ListOptionForm) -> str:
    return str(form.errors["name"][0])


@login_required
@kiln_open_required
@crew_required
@require_POST
def list_add(request: HttpRequest, kind: str) -> HttpResponse:
    """Add a clay or glaze to the end of its list."""
    model = _list_model(kind)
    form = ListOptionForm(request.POST, model=model)
    if not form.is_valid():
        return _list_response(request, kind, error=_first_error(form))
    option = model.objects.append(form.cleaned_data["name"])  # type: ignore[attr-defined]
    return _list_response(request, kind, saved_pk=option.pk)


@login_required
@kiln_open_required
@crew_required
@require_POST
def list_rename(request: HttpRequest, kind: str, pk: int) -> HttpResponse:
    """Fix the spelling of an option. Tickets that use it show the new name."""
    model = _list_model(kind)
    option = get_object_or_404(model, pk=pk)
    form = ListOptionForm(request.POST, model=model, instance=option)
    if not form.is_valid():
        return _list_response(request, kind, error=_first_error(form))
    option.name = form.cleaned_data["name"]
    option.save(update_fields=["name"])
    return _list_response(request, kind, saved_pk=option.pk)


@login_required
@kiln_open_required
@crew_required
@require_POST
def list_archive(request: HttpRequest, kind: str, pk: int) -> HttpResponse:
    """Take an option off new tickets. Old tickets keep showing it."""
    option = get_object_or_404(_list_model(kind), pk=pk)
    option.archive(by=_member(request))
    return _list_response(request, kind)


@login_required
@kiln_open_required
@crew_required
@require_POST
def list_restore(request: HttpRequest, kind: str, pk: int) -> HttpResponse:
    """Offer an archived option on new tickets again."""
    option = get_object_or_404(_list_model(kind), pk=pk)
    try:
        option.restore()
    except ListOptionNameTaken:
        return _list_response(request, kind, error=f"{option.name} is already on the list.")
    return _list_response(request, kind)
