"""The admin Locations page at /manage/locations/ (#616).

A list of every location with an add form, and an edit page per location. Both are admin only
(``@fog_admin_required``), plain POSTs that redirect back with a message, which the hub shows
as a toast. Deactivating is the Active switch on the edit page: a location is never deleted
here, because classes, events, orientations and equipment may still point at it.
"""

from __future__ import annotations

from django.contrib import messages
from django.db.models import Count, IntegerField, Model, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from hub.forms import LocationForm
from hub.view_as import fog_admin_required
from hub.views import _get_hub_context
from classes.models import ClassOffering
from membership.models import CommunityEvent, Equipment, Location, OrientationType


def _area_count(model: type[Model]) -> Coalesce:
    """How many rows of ``model`` are set to the outer location, as a correlated subquery.

    One subquery per kind keeps the list one query: four reverse joins in the outer query would
    multiply each other's rows before counting.
    """
    counted = (
        model._default_manager.filter(area=OuterRef("pk"))
        .order_by()
        .values("area")
        .annotate(total=Count("pk"))
        .values("total")
    )
    return Coalesce(Subquery(counted, output_field=IntegerField()), Value(0))


@fog_admin_required
def hub_admin_locations(request: HttpRequest) -> HttpResponse:
    """List every location and add a new one."""
    form = LocationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        location = form.save()
        messages.success(request, f"Added {location.name}.")
        return redirect("hub_admin_locations")
    locations = (
        Location.objects.select_related("guild")
        .prefetch_related("shares_space_with")
        .annotate(
            class_total=_area_count(ClassOffering),
            event_total=_area_count(CommunityEvent),
            orientation_total=_area_count(OrientationType),
            equipment_total=_area_count(Equipment),
        )
        .order_by("-is_active", "name")
    )
    return render(
        request,
        "hub/admin/locations.html",
        {**_get_hub_context(request), "locations": locations, "form": form},
    )


@fog_admin_required
def hub_admin_location_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit one location: its name, guild, note, the locations it shares space with, and Active."""
    location = get_object_or_404(Location, pk=pk)
    form = LocationForm(request.POST or None, instance=location)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Saved {location.name}.")
        return redirect("hub_admin_locations")
    return render(
        request,
        "hub/admin/location_edit.html",
        {**_get_hub_context(request), "location": location, "form": form},
    )
