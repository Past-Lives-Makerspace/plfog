"""Manage Events at /manage/events/ (#831, part 2): the Admin Tools card beside Manage Classes.

A list of every event on the Calendar with the Manage Classes chrome (search, lifecycle
chips, sortable columns, pagination), a per-event screen with the actions its state allows
(the review decisions, Retry sync, Duplicate, Unpublish, Delete) and the two POST actions
that are new here. Everything is ``@events_admin_access_required`` (an admin or a Calendar
Administrator); the decisions, the retry and the delete keep their own endpoints in
``hub.views`` and come back here through ``next``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from classes.lifecycle import facet_rows, resolve_facet
from classes.table import prepare_table
from hub.event_lifecycle import EVENT_FACETS
from hub.view_as import events_admin_access_required
from hub.views import _event_delete_confirm_message, _event_edit_url, _get_hub_context, _google_sync_enabled
from membership.models import CommunityEvent, InvalidEventTransition

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from membership.models import CommunityEventQuerySet

#: The columns a ``?sort=`` may name; anything else falls back to the start time.
_SORTABLE = frozenset({"title", "guild__name", "starts_at", "moderation_state", "rsvp_count"})


def _managed_events() -> CommunityEventQuerySet:
    """Every event Manage Events lists, with what its columns read loaded in the one query.

    Studio hours are left out at the query level: they are a guild's ambient standing hours
    with their own editor on the guild page, never an event anyone reviews, announces or RSVPs
    to. The guild, the creator's and the proposer's members ride along for the Kind and
    Created by cells, and the RSVP count is annotated, so a page of rows costs no query per row.
    """
    return (
        CommunityEvent.objects.exclude(event_type=CommunityEvent.EventType.STUDIO_HOURS)
        .select_related("guild", "created_by__member", "submitted_by__member")
        .annotate(rsvp_count=Count("rsvps", distinct=True))
    )


def _mine(rows: CommunityEventQuerySet, user: User) -> CommunityEventQuerySet:
    """The rows ``user`` created or proposed: the real signed-in user, never a view-as preview."""
    return rows.filter(Q(created_by=user) | Q(submitted_by=user))


@events_admin_access_required
def hub_admin_events(request: HttpRequest) -> HttpResponse:
    """The list: every event but studio hours, chipped by lifecycle, searchable, sortable, paged.

    ``?status=<facet>`` picks a chip (``hub.event_lifecycle.EVENT_FACETS``; junk reads as All),
    ``?mine=1`` keeps only what the signed-in user created or proposed, and the search box
    matches the title, the guild's name and the location. Chip counts and the My Events count
    ignore the search box and each other, as on Manage Classes.
    """
    user: User = request.user  # type: ignore[assignment]  # the gate wraps @login_required
    facet = resolve_facet(EVENT_FACETS, request.GET.get("status", "").strip())
    base = _managed_events()
    rows = facet.apply(base)
    mine_active = request.GET.get("mine", "") == "1"
    if mine_active:
        rows = _mine(rows, user)

    # Every view-computed URL starts from a normalized copy of the GET params: a bogus mine
    # value is stripped rather than echoed, so cruft never rides along on later links.
    normalized = request.GET.copy()
    if not mine_active:
        normalized.pop("mine", None)

    def _url_without(*drop: str, **add: str) -> str:
        params = normalized.copy()
        for key in ("page", *drop):
            params.pop(key, None)
        for key, value in add.items():
            params[key] = value
        return params.urlencode()

    facets = facet_rows(
        EVENT_FACETS,
        base,
        facet,
        lambda key: "?" + _url_without("status", **({"status": key} if key else {})),
    )
    search_preserved = normalized.copy()
    for key in ("q", "page"):
        search_preserved.pop(key, None)
    table = prepare_table(
        request,
        rows,
        search_fields=["title", "guild__name", "location"],
        default_sort="starts_at",
        sortable=_SORTABLE,
    )
    return render(
        request,
        "hub/admin/events_list.html",
        {
            **_get_hub_context(request),
            "facets": facets,
            "selected_status": facet.key,
            "mine_active": mine_active,
            "mine_count": _mine(base, user).count(),
            "mine_toggle_url": _url_without("mine") if mine_active else _url_without(mine="1"),
            "mine_clear_url": _url_without("mine"),
            "search_preserved_fields": list(search_preserved.items()),
            "search_clear_url": _url_without("q"),
            "add_url": reverse("hub_event_add"),
            **table,
        },
    )


@events_admin_access_required
def hub_admin_event(request: HttpRequest, pk: int) -> HttpResponse:
    """One event's admin screen: the header, the Overview tab and the actions its state allows.

    The decisions post to ``hub_event_review_decision``, Retry sync to ``hub_event_retry_sync``
    and Delete to ``hub_event_delete``, each with ``next`` so the admin lands back here (or on
    the list, once the row is gone). Edit opens the composer the public event page would
    (``_event_edit_url``, the one rule): the guild's own for its leadership and admins, the
    Calendar's for a Calendar Administrator on a guild event. Studio hours 404, as they are
    not on the list.
    """
    event = get_object_or_404(_managed_events().select_related("area", "reviewed_by"), pk=pk)
    states = CommunityEvent.ModerationState
    google_sync_enabled = _google_sync_enabled()
    detail_url = reverse("hub_admin_event", args=[event.pk])
    return render(
        request,
        "hub/admin/event_detail.html",
        {
            **_get_hub_context(request),
            "event": event,
            "detail_url": detail_url,
            "list_url": reverse("hub_admin_events"),
            "when": event.when_display_for(event.next_occurrence_start()),
            "edit_url": _event_edit_url(request, event),
            "can_decide": event.moderation_state in (states.PENDING, states.CHANGES_REQUESTED),
            "can_request_changes": event.moderation_state == states.PENDING,
            "can_unpublish": event.moderation_state == states.PUBLISHED,
            "google_sync_enabled": google_sync_enabled,
            "can_retry_sync": google_sync_enabled
            and event.sync_state in (CommunityEvent.SyncState.PENDING, CommunityEvent.SyncState.FAILED),
            "delete_confirm_message": _event_delete_confirm_message(event),
        },
    )


@events_admin_access_required
@require_POST
def hub_admin_event_duplicate(request: HttpRequest, pk: int) -> HttpResponse:
    """Copy the event as a fresh draft and open the copy in the composer."""
    user: User = request.user  # type: ignore[assignment]  # the gate wraps @login_required
    event = get_object_or_404(_managed_events(), pk=pk)
    copy = event.duplicate(by=user)
    messages.success(request, f"Copied as a draft: {copy.title}. Saving it announces it.")
    return redirect(reverse("hub_event_edit", args=[copy.pk]))


@events_admin_access_required
@require_POST
def hub_admin_event_unpublish(request: HttpRequest, pk: int) -> HttpResponse:
    """Take a published event down to a draft; the model refuses any other state."""
    user: User = request.user  # type: ignore[assignment]  # the gate wraps @login_required
    event = get_object_or_404(_managed_events(), pk=pk)
    try:
        event.unpublish(actor=user)
    except InvalidEventTransition:
        messages.info(request, "Only a published event can be unpublished.")
    else:
        messages.success(request, "Event unpublished. It is a draft now, and members no longer see it.")
    return redirect(reverse("hub_admin_event", args=[event.pk]))
