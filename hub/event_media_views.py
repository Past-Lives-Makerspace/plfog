"""The hero and gallery routes behind the Calendar event composers (#831, part 3).

JSON endpoints the hero field (``hub/partials/_event_hero_field.html``) and the gallery
manager (``classes/_components/image_formset.html``, reused with ``offering=event``) post to,
with the shapes of their class twins in ``classes.views``. Every one is POST only and gated by
``membership.permissions.can_edit_event`` on the event, the same answer the composer gives, so
the author of a pending proposal may manage its photos and a stranger is refused.
``event_media_context`` is what the three composers hand those components.
"""

from __future__ import annotations

import json
from typing import Any

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.images import image_upload_refusal, parse_gallery_focus
from membership.models import MAX_EVENT_GALLERY_IMAGES, CommunityEvent, EventImage
from membership.permissions import can_edit_event


def _invalid_payload() -> JsonResponse:
    """The 400 for a body that is not the JSON the route reads; a fresh response per request."""
    return JsonResponse({"error": "Invalid payload."}, status=400)


def event_media_context(event: CommunityEvent) -> dict[str, Any]:
    """What the composers hand the hero field and the gallery manager.

    The photo the cropper mounts on (the original, never the cropped copy: the box is stored
    in the original's pixels) and, for a saved event, the routes the two components post to.
    An unsaved event's components post nothing: its files ride the form instead.
    """
    context: dict[str, Any] = {"hero_source_url": event.photo.url if event.photo else ""}
    if event.pk is not None:
        context.update(
            hero_upload_url=reverse("hub_event_hero_upload", args=[event.pk]),
            photo_delete_url=reverse("hub_event_photo_delete", args=[event.pk]),
            gallery_upload_url=reverse("hub_event_image_upload", args=[event.pk]),
            gallery_reorder_url=reverse("hub_event_image_reorder", args=[event.pk]),
            gallery_image_url_base=event_image_url_base(),
        )
    return context


def event_image_url_base() -> str:
    """The prefix the per-image delete / alt / focus routes hang off (``<base><id>/delete/``).

    Derived from the route rather than typed out, so re-prefixing the URL include can never
    leave the gallery manager posting to a path that 404s.
    """
    return reverse("hub_event_image_delete", args=[0]).removesuffix("0/delete/")


def _editable_event(request: HttpRequest, pk: int) -> CommunityEvent:
    """The event, or a 403 when this viewer may not edit it (``can_edit_event``, the composer's gate)."""
    event = get_object_or_404(CommunityEvent.objects.select_related("guild"), pk=pk)
    if not can_edit_event(request, event):
        raise PermissionDenied("This event's photos are not this viewer's to change.")
    return event


def _editable_image(request: HttpRequest, pk: int) -> EventImage:
    """One gallery image, scoped through the event it belongs to and that event's gate."""
    image = get_object_or_404(EventImage.objects.select_related("event__guild"), pk=pk)
    if not can_edit_event(request, image.event):
        raise PermissionDenied("This event's photos are not this viewer's to change.")
    return image


@login_required
@require_POST
def event_hero_upload(request: HttpRequest, pk: int) -> HttpResponse:
    """Store a new photo from the hero field; the event page shows it whole until the next save brings a box."""
    event = _editable_event(request, pk)
    file = request.FILES.get("image")
    if not file:
        return JsonResponse({"error": "No file provided."}, status=400)
    refusal = image_upload_refusal(file)
    if refusal is not None:
        return JsonResponse({"error": refusal}, status=400)
    event.replace_photo(file)
    return JsonResponse({"url": event.photo.url})


@login_required
@require_POST
def event_image_upload(request: HttpRequest, pk: int) -> HttpResponse:
    """Add one gallery photo, after the existing ones."""
    event = _editable_event(request, pk)
    if event.gallery_images.count() >= MAX_EVENT_GALLERY_IMAGES:
        return JsonResponse({"error": f"An event can have at most {MAX_EVENT_GALLERY_IMAGES} photos."}, status=400)
    file = request.FILES.get("image")
    if not file:
        return JsonResponse({"error": "No file provided."}, status=400)
    refusal = image_upload_refusal(file)
    if refusal is not None:
        return JsonResponse({"error": refusal}, status=400)
    image = event.add_gallery_image(file)
    # Track B: CommunityEvent.objects.filter(pk=event.pk).mark_eventbrite_gallery_changed() lands here.
    return JsonResponse({"id": image.pk, "url": image.image.url, "alt_text": "", "sort_order": image.sort_order})


@login_required
@require_POST
def event_image_reorder(request: HttpRequest, pk: int) -> HttpResponse:
    """Rewrite the gallery order from ``{"order": [image ids]}``; the first id becomes the cover."""
    event = _editable_event(request, pk)
    try:
        order = json.loads(request.body)["order"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return _invalid_payload()
    if not isinstance(order, list):
        return _invalid_payload()
    event.reorder_gallery(order)
    # Track B: CommunityEvent.objects.filter(pk=event.pk).mark_eventbrite_gallery_changed() lands here.
    return JsonResponse({"ok": True})


@login_required
@require_POST
def event_image_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Drop one gallery photo; its file leaves storage only once no row shows it (``EventImage.remove``)."""
    image = _editable_image(request, pk)
    image.remove()
    # Track B: CommunityEvent.objects.filter(pk=image.event_id).mark_eventbrite_gallery_changed() lands here.
    return JsonResponse({"ok": True})


@login_required
@require_POST
def event_image_alt(request: HttpRequest, pk: int) -> HttpResponse:
    """Store the alt text typed under a gallery photo, from ``{"alt_text": str}``."""
    image = _editable_image(request, pk)
    try:
        alt_text = json.loads(request.body)["alt_text"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return _invalid_payload()
    if not isinstance(alt_text, str):
        return _invalid_payload()
    image.set_alt_text(alt_text)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def event_image_focus(request: HttpRequest, pk: int) -> HttpResponse:
    """Store where the gallery frame crops a photo, or reset it to the centre on nulls."""
    image = _editable_image(request, pk)
    try:
        point = parse_gallery_focus(request.body)
    except ValidationError as exc:
        return JsonResponse({"error": exc.messages[0]}, status=400)
    image.set_focus(point)
    return JsonResponse({"ok": True, "position": image.object_position})
