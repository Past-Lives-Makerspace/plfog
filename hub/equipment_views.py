"""Equipment directory views (equipment-reservations spec §6/§7 — PR 1).

The member-facing Equipment index and detail pages, the admin-gated add form, and
the manage panel (Details + Staff tabs). All views are thin per CLAUDE.md: parse
request → permission guard → form/model call → redirect or render. The schedule,
booking, and hours surfaces land in PR 2.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from hub.forms import EquipmentForm, EquipmentStaffAddForm
from hub.views import _get_hub_context, _get_member
from membership.models import Equipment, EquipmentQuerySet, EquipmentStaffMembership, Guild, Member
from membership.permissions import can_create_equipment, can_manage_equipment


def _equipment_queryset() -> EquipmentQuerySet:
    """The base queryset every equipment view reads — FKs prefetched, no per-row queries."""
    return Equipment.objects.select_related("guild", "space", "required_orientation", "required_orientation__guild")


def _require_can_manage(request: HttpRequest, equipment: Equipment) -> HttpResponse | None:
    """Return a 403 response if the user cannot manage ``equipment``, else None."""
    if not can_manage_equipment(request, equipment):
        return HttpResponse("Forbidden", status=403)
    return None


def _member_access_sets(member: Member | None) -> tuple[set[int], set[int]]:
    """The member's completed orientation-type pks and joined-guild pks, in two queries.

    The bulk input to :meth:`Equipment.access_state` so the index page never runs
    per-card access queries. Empty sets for an unlinked viewer — every card then
    reads "Membership inactive", which is the honest state.
    """
    if member is None:
        return set(), set()
    oriented = set(member.orientation_bookings.filter(is_completed=True).values_list("orientation_type_id", flat=True))
    guilds = set(member.guild_memberships.values_list("guild_id", flat=True))
    return oriented, guilds


@login_required
def hub_equipment_index(request: HttpRequest) -> HttpResponse:
    """The Equipment directory — card grid with guild/kind/search filters and access badges."""
    member = _get_member(request)
    base = _equipment_queryset().active()
    guild_filter = request.GET.get("guild", "")
    kind_filter = request.GET.get("kind", "")
    query = request.GET.get("q", "").strip()
    filtered = base
    if guild_filter == "standalone":
        filtered = filtered.standalone()
    elif guild_filter:
        filtered = filtered.filter(guild__slug=guild_filter)
    if kind_filter in Equipment.Kind.values:
        filtered = filtered.filter(kind=kind_filter)
    if query:
        filtered = filtered.filter(name__icontains=query)
    oriented_ids, guild_ids = _member_access_sets(member)
    cards = [
        {
            "equipment": equipment,
            "access_state": equipment.access_state(member, oriented_type_ids=oriented_ids, member_guild_ids=guild_ids),
        }
        for equipment in filtered
    ]
    return render(
        request,
        "hub/equipment_index.html",
        {
            **_get_hub_context(request),
            "cards": cards,
            "filter_guilds": Guild.objects.filter(equipment__is_active=True).distinct().order_by("name"),
            "has_standalone": base.standalone().exists(),
            "has_any_equipment": base.exists(),
            "guild_filter": guild_filter,
            "kind_filter": kind_filter,
            "query": query,
            "is_filtered": bool(guild_filter or kind_filter or query),
            "can_create": can_create_equipment(request),
        },
    )


@login_required
def hub_equipment_add(request: HttpRequest) -> HttpResponse:
    """Admin-gated create form — full admins and EQUIPMENT capability holders only."""
    if not can_create_equipment(request):
        return HttpResponse("Forbidden", status=403)
    form = EquipmentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        equipment = form.save()
        messages.success(request, "Equipment added.")
        return redirect("hub_equipment_detail", slug=equipment.slug)
    return render(request, "hub/equipment_add.html", {**_get_hub_context(request), "form": form})


@login_required
def hub_equipment_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """The equipment mini-page — hero, requirements banner, About (schedule joins in PR 2)."""
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    manages = can_manage_equipment(request, equipment)
    if not equipment.is_active and not manages:
        raise Http404("This equipment has been retired.")
    member = _get_member(request)
    access_state = equipment.access_state(member)
    orientation_type = equipment.required_orientation
    orientation_booking = None
    orientation_url = ""
    if member is not None and orientation_type is not None and access_state == Equipment.AccessState.NEEDS_ORIENTATION:
        orientation_booking = member.active_orientation_for_type(orientation_type)
        orientation_url = (
            reverse("hub_guild_detail", args=[orientation_type.guild.slug])
            + f"?tab=orientations&type={orientation_type.pk}#guild-orientation"
        )
    return render(
        request,
        "hub/equipment_detail.html",
        {
            **_get_hub_context(request),
            "equipment": equipment,
            "access_state": access_state,
            "orientation_booking": orientation_booking,
            "orientation_url": orientation_url,
            "can_manage": manages,
        },
    )


def _render_manage(
    request: HttpRequest,
    equipment: Equipment,
    *,
    form: EquipmentForm | None = None,
    staff_add_form: EquipmentStaffAddForm | None = None,
    active_tab: str = "details",
) -> HttpResponse:
    """Render the manage panel with the given (possibly error-bearing) forms."""
    return render(
        request,
        "hub/equipment_manage.html",
        {
            **_get_hub_context(request),
            "equipment": equipment,
            "form": form if form is not None else EquipmentForm(instance=equipment),
            "staff_memberships": list(equipment.staff_memberships.select_related("member", "granted_by")),
            "staff_add_form": staff_add_form
            if staff_add_form is not None
            else EquipmentStaffAddForm(equipment=equipment),
            "active_tab": active_tab,
        },
    )


@login_required
def hub_equipment_manage(request: HttpRequest, slug: str) -> HttpResponse:
    """The manage panel — Details + Staff tabs (Hours & Limits and Reservations join in PR 2)."""
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    active_tab = request.GET.get("tab", "details")
    if active_tab not in {"details", "staff"}:
        active_tab = "details"
    return _render_manage(request, equipment, active_tab=active_tab)


@login_required
@require_POST
def hub_equipment_details_save(request: HttpRequest, slug: str) -> HttpResponse:
    """POST-only — save the manage panel's Details tab (the same form as the add page)."""
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    form = EquipmentForm(request.POST, request.FILES, instance=equipment)
    if form.is_valid():
        form.save()
        messages.success(request, "Saved.")
        return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=details")
    return _render_manage(request, equipment, form=form, active_tab="details")


@login_required
@require_POST
def hub_equipment_photo_delete(request: HttpRequest, slug: str) -> HttpResponse:
    """POST-only — clear the equipment photo (the ``image_field`` component's delete endpoint)."""
    equipment = get_object_or_404(Equipment, slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    if equipment.photo:
        equipment.photo.delete(save=True)
        messages.success(request, "Photo removed.")
    return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=details")


@login_required
@require_POST
def hub_equipment_staff_add(request: HttpRequest, slug: str) -> HttpResponse:
    """POST-only — grant a member a manager role on this equipment."""
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    form = EquipmentStaffAddForm(request.POST, equipment=equipment)
    if form.is_valid():
        EquipmentStaffMembership.objects.create(
            equipment=equipment,
            member=form.cleaned_data["member"],
            granted_by=_get_member(request),
        )
        messages.success(request, "Manager added.")
        return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=staff")
    return _render_manage(request, equipment, staff_add_form=form, active_tab="staff")


@login_required
@require_POST
def hub_equipment_staff_remove(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """POST-only — remove a member's manager role from this equipment."""
    equipment = get_object_or_404(Equipment, slug=slug)
    forbidden = _require_can_manage(request, equipment)
    if forbidden is not None:
        return forbidden
    staff = get_object_or_404(EquipmentStaffMembership, pk=pk, equipment=equipment)
    member_name = staff.member.display_name
    staff.delete()
    messages.success(request, f"{member_name} no longer manages the {equipment.name}.")
    return redirect(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=staff")
