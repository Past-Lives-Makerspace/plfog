"""The Days Off modal (#767): one person's days off, from the Orientation Schedule card on either owner.

A day off belongs to the person, not a guild, so one modal serves both the guild's
Orientations page and the equipment manage page's Orientation tab. What differs is the card's
owner: its gate (``can_edit_orienter_hours`` for a guild, ``can_edit_equipment_orienter_hours``
for equipment, both reused as they are), the page a save lands back on, and its endpoints.
``_Owner`` carries those, and the eight views below are the two owners times the four actions,
each a line or two over the shared handlers.

The modal body is loaded over HTMX per person, like Edit Hours. The date input asks the preview
endpoint for the day's count line and Save label on every change. A valid save cancels the
day's times outside any transaction (``orientations.cancel_day_off``), queues the toast as a
flash and answers 204 + ``HX-Redirect`` to the owner page, where the card's "Off ..." line and
Upcoming Times are fresh; an invalid save re-renders the body with its errors inside the open
modal. Delete is a plain POST from a confirm modal, back to the page the same way.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from hub.forms import OrientationDayOffForm
from membership import orientations
from membership.models import Equipment, Guild, Member, OrientationDayOff
from membership.permissions import can_edit_equipment_orienter_hours, can_edit_orienter_hours


@dataclass(frozen=True)
class _Owner:
    """The card a Days Off modal was opened from: its gate, where a save lands, and its endpoints."""

    key: str  # the owner's URL argument: a guild pk or an equipment slug
    gate: Callable[[HttpRequest, Member], bool]
    return_url: str
    save_url: str
    preview_url: str
    delete_url_name: str


def _guild_owner(pk: int) -> _Owner:
    """The guild's Orientations page as an owner, or a 404."""
    guild = get_object_or_404(Guild, pk=pk)
    return _Owner(
        key=str(guild.pk),
        gate=lambda request, orienter: can_edit_orienter_hours(request, guild, orienter),
        return_url=reverse("hub_guild_orientations", args=[guild.pk]),
        save_url=reverse("hub_guild_orientation_day_off_save", args=[guild.pk]),
        preview_url=reverse("hub_guild_orientation_day_off_preview", args=[guild.pk]),
        delete_url_name="hub_guild_orientation_day_off_delete",
    )


def _equipment_owner(slug: str) -> _Owner:
    """The equipment manage page's Orientation tab as an owner, or a 404."""
    equipment = get_object_or_404(Equipment.objects.select_related("guild"), slug=slug)
    return _Owner(
        key=equipment.slug,
        gate=lambda request, orienter: can_edit_equipment_orienter_hours(request, equipment, orienter),
        return_url=f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation",
        save_url=reverse("hub_equipment_orientation_day_off_save", args=[equipment.slug]),
        preview_url=reverse("hub_equipment_orientation_day_off_preview", args=[equipment.slug]),
        delete_url_name="hub_equipment_orientation_day_off_delete",
    )


def days_off_by_orienter(members: Iterable[Member]) -> dict[str, list[OrientationDayOff]]:
    """Each listed person's upcoming days off for the card's "Off ..." line, in one query.

    Keyed by the pk as a string, which is how the template's ``get_item`` filter looks a dict up.
    """
    grouped: dict[str, list[OrientationDayOff]] = {}
    for day_off in OrientationDayOff.objects.upcoming().for_orienters(members):
        grouped.setdefault(str(day_off.orienter_id), []).append(day_off)
    return grouped


def _target(raw: str) -> Member:
    """The person a request names (``orienter``), or a 404: a day off is always one person's."""
    if not raw.isdigit():
        raise Http404("Unknown orienter.")
    return get_object_or_404(Member, pk=int(raw))


def _form(owner: _Owner, target: Member, data: Any = None) -> OrientationDayOffForm:
    """The person's form, its date input pointed at the owner's preview endpoint for that person.

    Prefixed ``day_off`` so its DOM ids never collide with the page's own date field (the
    one off form on Upcoming Times), the way the Edit Hours modal keeps its own prefix.
    """
    return OrientationDayOffForm(
        data, prefix="day_off", orienter=target, preview_url=f"{owner.preview_url}?orienter={target.pk}"
    )


def _modal(request: HttpRequest, owner: _Owner, target: Member, form: OrientationDayOffForm) -> HttpResponse:
    """Render the modal body: the form, its impact for the chosen date, and the person's upcoming days off."""
    day = form.chosen_day()
    return render(
        request,
        "hub/partials/_orienter_days_off_modal.html",
        {
            "target": target,
            "form": form,
            "times": orientations.times_on_day_off(target, day) if day is not None else None,
            "days_off": list(OrientationDayOff.objects.upcoming().filter(orienter=target)),
            "save_url": owner.save_url,
            "delete_url_name": owner.delete_url_name,
            "owner_key": owner.key,
        },
    )


def _days_off_modal(request: HttpRequest, owner: _Owner) -> HttpResponse:
    """GET ``?orienter=<pk>``: the Days Off modal body for one person, under the owner's hours gate."""
    target = _target(request.GET.get("orienter", ""))
    if not owner.gate(request, target):
        return HttpResponse("Forbidden", status=403)
    return _modal(request, owner, target, _form(owner, target))


def _day_off_preview(request: HttpRequest, owner: _Owner) -> HttpResponse:
    """GET ``?orienter=<pk>&date=``: the count line and Save label for that date, swapped into the open form."""
    target = _target(request.GET.get("orienter", ""))
    if not owner.gate(request, target):
        return HttpResponse("Forbidden", status=403)
    day = _form(owner, target, request.GET).chosen_day()
    return render(
        request,
        "hub/partials/_orienter_day_off_impact.html",
        {"times": orientations.times_on_day_off(target, day) if day is not None else None},
    )


def _saved_message(cancelled: int) -> str:
    """The toast after a save: the day is off, and how many of its times went with it."""
    return f"Day off saved. {cancelled} time{'' if cancelled == 1 else 's'} cancelled."


def _day_off_save(request: HttpRequest, owner: _Owner) -> HttpResponse:
    """POST ``orienter``, ``date``, ``note``: save the day off and cancel the day's times.

    A valid HTMX save answers 204 + ``HX-Redirect`` to the owner page with the toast queued as a
    flash; an invalid one re-renders the modal body with its errors. A plain POST redirects.
    """
    target = _target(request.POST.get("orienter", ""))
    if not owner.gate(request, target):
        return HttpResponse("Forbidden", status=403)
    form = _form(owner, target, request.POST)
    if not form.is_valid():
        return _modal(request, owner, target, form)
    day_off = form.save()
    # Outside any transaction: cancel_slot talks to Stripe for held checkouts.
    cancelled, _emailed = orientations.cancel_day_off(target, day_off.date)
    messages.success(request, _saved_message(cancelled))
    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=204)
        response["HX-Redirect"] = owner.return_url
        return response
    return redirect(owner.return_url)


def _day_off_delete(request: HttpRequest, owner: _Owner, day_off_pk: int) -> HttpResponse:
    """POST: remove a day off, under the owner's gate for its person; the hours generate again next run."""
    day_off = get_object_or_404(OrientationDayOff.objects.select_related("orienter"), pk=day_off_pk)
    if not owner.gate(request, day_off.orienter):
        return HttpResponse("Forbidden", status=403)
    day_off.delete()
    messages.success(request, "Day off removed. Hours that day generate again.")
    return redirect(owner.return_url)


@login_required
def guild_orientation_days_off(request: HttpRequest, pk: int) -> HttpResponse:
    """The Days Off modal body for one of a guild's orienters (HTMX GET, ``?orienter=<pk>``)."""
    return _days_off_modal(request, _guild_owner(pk))


@login_required
def guild_orientation_day_off_preview(request: HttpRequest, pk: int) -> HttpResponse:
    """The count line and Save label for a date, on the guild's page (HTMX GET)."""
    return _day_off_preview(request, _guild_owner(pk))


@login_required
@require_POST
def guild_orientation_day_off_save(request: HttpRequest, pk: int) -> HttpResponse:
    """Save a day off from the guild's page and cancel that day's times."""
    return _day_off_save(request, _guild_owner(pk))


@login_required
@require_POST
def guild_orientation_day_off_delete(request: HttpRequest, pk: int, day_off_pk: int) -> HttpResponse:
    """Remove a day off from the guild's page."""
    return _day_off_delete(request, _guild_owner(pk), day_off_pk)


@login_required
def hub_equipment_orientation_days_off(request: HttpRequest, slug: str) -> HttpResponse:
    """The Days Off modal body for one of the equipment's orienters (HTMX GET, ``?orienter=<pk>``)."""
    return _days_off_modal(request, _equipment_owner(slug))


@login_required
def hub_equipment_orientation_day_off_preview(request: HttpRequest, slug: str) -> HttpResponse:
    """The count line and Save label for a date, on the equipment manage page (HTMX GET)."""
    return _day_off_preview(request, _equipment_owner(slug))


@login_required
@require_POST
def hub_equipment_orientation_day_off_save(request: HttpRequest, slug: str) -> HttpResponse:
    """Save a day off from the equipment manage page and cancel that day's times."""
    return _day_off_save(request, _equipment_owner(slug))


@login_required
@require_POST
def hub_equipment_orientation_day_off_delete(request: HttpRequest, slug: str, day_off_pk: int) -> HttpResponse:
    """Remove a day off from the equipment manage page."""
    return _day_off_delete(request, _equipment_owner(slug), day_off_pk)
