"""The staff Cubbies manager (#977): a guild's shelf units, its cubbies, its Pricing and the tab switch.

Every view is gated by :func:`membership.permissions.can_edit_guild` (admins and officers reach
every guild; a lead or staff member only their own). A view that names a shelf unit or a cubby
reads the guild from that record, never from the URL, so a crafted POST naming another guild's
cubby is refused for anyone who does not run that guild.

Pages: Layout (``hub_guild_cubbies``, the shelf builder), a cubby's page (the builder with the
edit panel beside the cubby's unit: ``hub_cubby_add`` for an empty square, ``hub_cubby_edit``)
and Pricing (``hub_guild_cubbies_pricing``). Part 3 (#979) adds Rentals.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from hub.autosave import autosave_saved, wants_autosave
from hub.cubby_forms import CubbyForm, CubbyPricingForm, GuildCubbiesSettingsForm, ShelfUnitForm
from hub.toast import trigger_toast
from hub.views import _get_hub_context, _get_member
from membership import cubbies
from membership.models import Cubby, Guild, ShelfUnit
from membership.permissions import can_edit_guild, manageable_guilds


def _forbidden(request: HttpRequest, guild: Guild) -> HttpResponse | None:
    """403 unless this request may edit ``guild`` (``view_as`` aware), else None."""
    if not can_edit_guild(request, guild):
        return HttpResponse("Forbidden", status=403)
    return None


def _layout_url(guild: Guild, anchor: str = "") -> str:
    url = reverse("hub_guild_cubbies", args=[guild.pk])
    return f"{url}#{anchor}" if anchor else url


def _unit_prefix(unit: ShelfUnit) -> str:
    return f"unit-{unit.pk}"


def _unit_cards(layout: cubbies.CubbyLayout) -> list[dict[str, Any]]:
    """Each active unit with a fresh name and size form, for the Layout tab."""
    return [
        {"view": view, "form": ShelfUnitForm(instance=view.unit, prefix=_unit_prefix(view.unit))}
        for view in layout.units
    ]


def _manage_context(request: HttpRequest, guild: Guild, tab: str) -> dict[str, Any]:
    """What every tab of the manager shares: the guild, its picker, the switch strip and the tab row."""
    guilds = list(manageable_guilds(request))
    return {
        **_get_hub_context(request),
        "guild": guild,
        "cubby_tab": tab,
        "picker_guilds": guilds if len(guilds) > 1 else [],
        "settings_form": GuildCubbiesSettingsForm(instance=guild, prefix="cubbies"),
    }


def _render_layout(
    request: HttpRequest,
    guild: Guild,
    *,
    unit_add_form: ShelfUnitForm | None = None,
    panel: dict[str, Any] | None = None,
    status: int = 200,
) -> HttpResponse:
    layout = cubbies.CubbyLayout.load(guild)
    context = {
        **_manage_context(request, guild, "layout"),
        "layout": layout,
        "unit_cards": _unit_cards(layout),
        "unit_add_form": unit_add_form or ShelfUnitForm(prefix="new-unit"),
        "open_add_modal": unit_add_form is not None,
        "panel": panel,
    }
    if panel is not None:
        unit = panel["unit"]
        context["unit_cards"] = [card for card in context["unit_cards"] if card["view"].unit.pk == unit.pk]
    return render(request, "hub/cubbies/manage.html", context, status=status)


@login_required
def hub_cubbies_home(request: HttpRequest) -> HttpResponse:
    """Admin Tools' Cubbies card: the manager of ``?guild=<pk>`` when it is one this viewer runs, else the first."""
    guilds = list(manageable_guilds(request))
    if not guilds:
        return HttpResponse("Forbidden", status=403)
    asked = request.GET.get("guild", "")
    chosen = [guild for guild in guilds if str(guild.pk) == asked] or guilds
    return redirect("hub_guild_cubbies", pk=chosen[0].pk)


@login_required
def hub_guild_cubbies(request: HttpRequest, pk: int) -> HttpResponse:
    """The Layout tab: every shelf unit as a grid to build on, and what is archived."""
    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _forbidden(request, guild)
    if forbidden is not None:
        return forbidden
    return _render_layout(request, guild)


@login_required
def hub_guild_cubbies_pricing(request: HttpRequest, pk: int) -> HttpResponse:
    """The Pricing tab: the guild's monthly minimum and suggestion, and Who gets paid."""
    from billing import payouts

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _forbidden(request, guild)
    if forbidden is not None:
        return forbidden
    form = CubbyPricingForm(request.POST or None, instance=guild)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Pricing saved.")
        return redirect("hub_guild_cubbies_pricing", pk=guild.pk)
    payee_status = payouts.payee_status(SimpleNamespace(payee=guild.cubby_payee), _get_member(request))
    return render(
        request,
        "hub/cubbies/manage.html",
        {**_manage_context(request, guild, "pricing"), "pricing_form": form, "payee_status": payee_status},
    )


@login_required
@require_POST
def hub_guild_cubbies_settings_save(request: HttpRequest, pk: int) -> HttpResponse:
    """Save the Cubbies tab switch, from Guild Settings (autosave JSON) or the manager's strip (htmx)."""
    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _forbidden(request, guild)
    if forbidden is not None:
        return forbidden
    # The manager's strip posts strip=1 under the "cubbies" prefix; Guild Settings posts the bare field.
    from_strip = bool(request.POST.get("strip"))
    form = GuildCubbiesSettingsForm(request.POST, instance=guild, prefix="cubbies" if from_strip else None)
    form.is_valid()  # one boolean: it cannot fail, and save() raises loudly if it ever did
    form.save()
    if wants_autosave(request):
        return autosave_saved()
    if from_strip and request.headers.get("HX-Request"):
        response = render(
            request,
            "hub/cubbies/_live_strip.html",
            {"guild": guild, "settings_form": GuildCubbiesSettingsForm(instance=guild, prefix="cubbies")},
        )
        trigger_toast(response, "Cubbies tab on." if guild.show_cubbies_tab else "Cubbies tab off.")
        return response
    messages.success(request, "Saved.")
    if from_strip:
        return redirect("hub_guild_cubbies", pk=guild.pk)
    return redirect(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=cubbies")


@login_required
@require_POST
def hub_shelf_unit_add(request: HttpRequest, pk: int) -> HttpResponse:
    """Add Shelf Unit: its name, rows and how many across. A refusal reopens the modal with its errors."""
    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _forbidden(request, guild)
    if forbidden is not None:
        return forbidden
    form = ShelfUnitForm(request.POST, prefix="new-unit")
    if not form.is_valid():
        return _render_layout(request, guild, unit_add_form=form)
    unit = form.save(commit=False)
    unit.guild = guild
    unit.save()
    messages.success(request, f"{unit.name} added. Tap a square to place a cubby.")
    return redirect(_layout_url(guild, f"cubby-unit-{unit.pk}"))


def _stepped(unit: ShelfUnit, data: QueryDict) -> QueryDict:
    """The posted unit form with a stepper's one row or column more or less applied (``step=rows:-1``)."""
    stepped = data.copy()
    step = data.get("step", "")
    if ":" in step:
        name, _sep, delta = step.partition(":")
        if name in ("rows", "columns") and delta in ("1", "-1"):
            key = f"{_unit_prefix(unit)}-{name}"
            try:
                current = int(data.get(key, getattr(unit, name)))
            except ValueError:
                current = getattr(unit, name)
            stepped[key] = str(current + int(delta))
    return stepped


@login_required
@require_POST
def hub_shelf_unit_save(request: HttpRequest, unit_pk: int) -> HttpResponse:
    """A unit card's name and steppers. Shrinking past a cubby is refused, naming it.

    htmx swaps the card back (with Saved, or the refusal under the steppers); without it the
    post lands back on Layout.
    """
    unit = get_object_or_404(ShelfUnit.objects.select_related("guild").active(), pk=unit_pk)
    guild = unit.guild
    forbidden = _forbidden(request, guild)
    if forbidden is not None:
        return forbidden
    form = ShelfUnitForm(_stepped(unit, request.POST), instance=unit, prefix=_unit_prefix(unit))
    saved = False
    if form.is_valid():
        try:
            cubbies.resize_unit(
                unit,
                name=form.cleaned_data["name"],
                rows=form.cleaned_data["rows"],
                columns=form.cleaned_data["columns"],
            )
            saved = True
        except ValidationError as refusal:
            for name, problems in refusal.message_dict.items():
                for problem in problems:
                    form.add_error(name, problem)
    unit.refresh_from_db()
    if not request.headers.get("HX-Request"):
        if not saved:
            messages.error(request, " ".join(str(e) for errors in form.errors.values() for e in errors))
        return redirect(_layout_url(guild, f"cubby-unit-{unit.pk}"))
    layout = cubbies.CubbyLayout.load(guild)
    selected = request.POST.get("selected", "")
    card = {
        "view": layout.unit_view(unit.pk),
        "form": form if not saved else ShelfUnitForm(instance=unit, prefix=_unit_prefix(unit)),
    }
    return render(
        request,
        "hub/cubbies/_unit_card.html",
        {"card": card, "guild": guild, "saved": saved, "selected_pk": int(selected) if selected.isdigit() else None},
    )


@login_required
@require_POST
def hub_shelf_unit_archive(request: HttpRequest, unit_pk: int) -> HttpResponse:
    """Archive a unit: it and its cubbies leave the guild page; Show Archived can bring it back."""
    unit = get_object_or_404(ShelfUnit.objects.select_related("guild"), pk=unit_pk)
    forbidden = _forbidden(request, unit.guild)
    if forbidden is not None:
        return forbidden
    if not unit.is_archived:
        cubbies.archive_unit(unit)
        messages.success(request, f"{unit.name} archived.")
    return redirect(_layout_url(unit.guild))


@login_required
@require_POST
def hub_shelf_unit_restore(request: HttpRequest, unit_pk: int) -> HttpResponse:
    """Restore an archived unit, unless one of its cubby labels is now taken in the guild."""
    unit = get_object_or_404(ShelfUnit.objects.select_related("guild"), pk=unit_pk)
    forbidden = _forbidden(request, unit.guild)
    if forbidden is not None:
        return forbidden
    if unit.is_archived:
        try:
            cubbies.restore_unit(unit)
            messages.success(request, f"{unit.name} restored.")
        except ValidationError as refusal:
            messages.error(request, " ".join(refusal.messages))
    return redirect(_layout_url(unit.guild, f"cubby-unit-{unit.pk}"))


def _cubby_page(
    request: HttpRequest, unit: ShelfUnit, cubby: Cubby | None, initial: dict[str, int] | None
) -> HttpResponse:
    """GET or POST a cubby's page: the builder with this cubby's unit and its edit panel."""
    guild = unit.guild
    forbidden = _forbidden(request, guild)
    if forbidden is not None:
        return forbidden
    instance = cubby or Cubby(unit=unit)
    if request.method == "POST":
        form = CubbyForm(request.POST, request.FILES, instance=instance, unit=unit)
        if form.is_valid():
            saved = form.save(commit=False)
            try:
                cubbies.save_cubby(saved)
            except ValidationError as refusal:
                for name, problems in refusal.message_dict.items():
                    for problem in problems:
                        form.add_error(name, problem)
            else:
                messages.success(request, f"Cubby {saved.label} saved.")
                return redirect(_layout_url(guild, f"cubby-unit-{unit.pk}"))
    else:
        form = CubbyForm(instance=instance, unit=unit, initial=initial)
    panel = {"form": form, "unit": unit, "cubby": cubby}
    return _render_layout(request, guild, panel=panel)


@login_required
def hub_cubby_add(request: HttpRequest, unit_pk: int) -> HttpResponse:
    """Place a new cubby on an empty square (``?row=&column=``, which only start the form)."""
    unit = get_object_or_404(ShelfUnit.objects.select_related("guild").active(), pk=unit_pk)
    initial: dict[str, int] = {}
    for name in ("row", "column"):
        value = request.GET.get(name, "")
        if value.isdigit():
            initial[name] = int(value)
    return _cubby_page(request, unit, None, initial)


@login_required
def hub_cubby_edit(request: HttpRequest, cubby_pk: int) -> HttpResponse:
    """A placed cubby's page. The guild is its unit's."""
    cubby = get_object_or_404(Cubby.objects.active().select_related("unit__guild"), pk=cubby_pk)
    return _cubby_page(request, cubby.unit, cubby, None)


def _cubby_for_staff(request: HttpRequest, cubby_pk: int) -> tuple[Cubby, HttpResponse | None]:
    cubby = get_object_or_404(Cubby.objects.select_related("unit__guild"), pk=cubby_pk)
    return cubby, _forbidden(request, cubby.unit.guild)


@login_required
@require_POST
def hub_cubby_archive(request: HttpRequest, cubby_pk: int) -> HttpResponse:
    """Archive a cubby: it leaves the shelf and its square opens."""
    cubby, forbidden = _cubby_for_staff(request, cubby_pk)
    if forbidden is not None:
        return forbidden
    if not cubby.is_archived:
        cubbies.archive_cubby(cubby)
        messages.success(request, f"Cubby {cubby.label} archived.")
    return redirect(_layout_url(cubby.unit.guild, f"cubby-unit-{cubby.unit_id}"))


@login_required
@require_POST
def hub_cubby_restore(request: HttpRequest, cubby_pk: int) -> HttpResponse:
    """Put an archived cubby back, unless its square or label was taken meanwhile."""
    cubby, forbidden = _cubby_for_staff(request, cubby_pk)
    if forbidden is not None:
        return forbidden
    if cubby.is_archived:
        try:
            cubbies.restore_cubby(cubby)
            messages.success(request, f"Cubby {cubby.label} restored.")
        except ValidationError as refusal:
            messages.error(request, " ".join(refusal.messages))
    return redirect(_layout_url(cubby.unit.guild, f"cubby-unit-{cubby.unit_id}"))


@login_required
@require_POST
def hub_cubby_photo_delete(request: HttpRequest, cubby_pk: int) -> HttpResponse:
    """Remove a cubby's photo and land back on its page."""
    cubby, forbidden = _cubby_for_staff(request, cubby_pk)
    if forbidden is not None:
        return forbidden
    if cubby.is_archived:
        raise Http404("That cubby is archived.")
    if cubby.photo:
        cubby.photo = None  # Cubby.save deletes the old file from storage (delete_orphan_on_replace)
        cubby.save(update_fields=["photo"])
        messages.success(request, "Photo removed.")
    return redirect("hub_cubby_edit", cubby_pk=cubby.pk)
