"""The HQ page and its edit pages (Track C, part 1)."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from hq.access import hub_or_404, renders_as_admin, viewer_member
from hq.forms import HubCardForm, HubForm, HubGoalFormSet
from hq.models import EventHub, HubCard
from hq.services import goal_rows
from hub.view_as import fog_admin_required


def index(request: HttpRequest) -> HttpResponse:
    """``/hq/``: the open hub's page. With none open, an admin is sent to make one; everyone else gets a 404."""
    hub = EventHub.objects.open()
    if hub is not None:
        return redirect(hub.get_absolute_url())
    if renders_as_admin(request):
        return redirect("hq:create")
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
def create(request: HttpRequest) -> HttpResponse:
    """Make a hub. Its nine door cards come with it, so the page is never empty."""
    return _hub_editor(request, None)


@fog_admin_required
def edit(request: HttpRequest, slug: str) -> HttpResponse:
    """The hub form with its goals."""
    return _hub_editor(request, get_object_or_404(EventHub, slug=slug))


def _hub_editor(request: HttpRequest, hub: EventHub | None) -> HttpResponse:
    instance = hub if hub is not None else EventHub()
    if request.method == "POST":
        form = HubForm(request.POST, request.FILES, instance=instance)
        formset = HubGoalFormSet(request.POST, instance=instance, prefix="goals")
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                saved = form.save()
                formset.instance = saved
                formset.save()
                if hub is None:
                    saved.seed_default_cards()
            if hub is None:
                messages.success(request, "HQ made. Its nine doors are admins only until you open them.")
            else:
                messages.success(request, "HQ saved.")
            return redirect(saved.get_absolute_url())
    else:
        form = HubForm(instance=instance)
        formset = HubGoalFormSet(instance=instance, prefix="goals")
    return render(
        request,
        "hq/edit.html",
        {"hub": hub, "form": form, "formset": formset, "max_upload_bytes": settings.MAX_UPLOAD_IMAGE_BYTES},
    )


@fog_admin_required
@require_POST
def hero_delete(request: HttpRequest, slug: str) -> HttpResponse:
    hub = get_object_or_404(EventHub, slug=slug)
    hub.remove_hero()
    messages.success(request, "Hero image removed.")
    return redirect("hq:edit", slug=hub.slug)


@fog_admin_required
def card_edit(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """One card: its words, who may open it, the named members, hidden, and its place in the order."""
    hub = get_object_or_404(EventHub, slug=slug)
    card = get_object_or_404(hub.cards, pk=pk)
    if request.method == "POST":
        form = HubCardForm(request.POST, instance=card)
        if form.is_valid():
            form.save()
            messages.success(request, "Card saved.")
            return redirect(hub.get_absolute_url())
    else:
        form = HubCardForm(instance=card)
    return render(request, "hq/card_edit.html", _card_context(hub, card, form))


def _card_context(hub: EventHub, card: HubCard, form: HubCardForm) -> dict[str, Any]:
    rows = form.member_rows()
    return {
        "hub": hub,
        "card": card,
        "form": form,
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
