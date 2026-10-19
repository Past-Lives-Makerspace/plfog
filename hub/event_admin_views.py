"""Manage Events at /manage/events/ (#831, parts 2 and 4): the Admin Tools card beside Manage Classes.

A list of every event on the Calendar with the Manage Classes chrome (search, lifecycle
chips, sortable columns, pagination), a per-event screen with the actions its state allows
(the review decisions, Retry sync, Duplicate, Unpublish, Delete) and the two POST actions
that are new here, and the Guest list tab: every RSVP row, members and guests, with check-in,
Add a guest, Remove, Copy update emails and a CSV. Everything is
``@events_admin_access_required`` (an admin or a Calendar Administrator); the decisions, the
retry and the delete keep their own endpoints in ``hub.views`` and come back here through
``next``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from classes.lifecycle import facet_rows, resolve_facet
from classes.table import prepare_table
from core.htmx import wants_fragment
from hub.event_exports import stream_event_guests_csv
from hub.event_lifecycle import EVENT_FACETS
from hub.forms import EventStaffGuestForm
from hub.toast import trigger_toast
from hub.view_as import events_admin_access_required
from hub.views import _event_delete_confirm_message, _event_edit_url, _get_hub_context, _google_sync_enabled
from membership.models import CommunityEvent, EventRSVP, EventUnpublishError, InvalidEventTransition

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from membership.models import CommunityEventQuerySet

#: The columns a ``?sort=`` may name; anything else falls back to the start time.
_SORTABLE = frozenset({"title", "guild__name", "starts_at", "moderation_state", "people_count"})

#: The Guest list's search box matches a guest's name or email and a member's names.
_GUEST_SEARCH_FIELDS = ["name", "email", "member__full_legal_name", "member__preferred_name"]


def _managed_events() -> CommunityEventQuerySet:
    """Every event Manage Events lists, with what its columns read loaded in the one query.

    Studio hours are left out at the query level: they are a guild's ambient standing hours
    with their own editor on the guild page, never an event anyone reviews, announces or RSVPs
    to. The guild, the creator's and the proposer's members ride along for the Kind and
    Created by cells, and the people count (every RSVP's party size summed, members and guests
    alike) is annotated, so a page of rows costs no query per row.
    """
    return (
        CommunityEvent.objects.exclude(event_type=CommunityEvent.EventType.STUDIO_HOURS)
        .select_related("guild", "created_by__member", "submitted_by__member")
        .annotate(people_count=Coalesce(Sum("rsvps__party_size"), 0))
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
    # Newest first, so All opens on what is coming up rather than on the oldest past event.
    table = prepare_table(
        request,
        rows,
        search_fields=["title", "guild__name", "location"],
        default_sort="starts_at",
        default_dir="desc",
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


def _event_screen_context(request: HttpRequest, event: CommunityEvent, *, active_tab: str) -> dict[str, Any]:
    """What every tab of the per-event screen renders: the header, the tab strip and its links.

    ``templates/hub/admin/event_screen_base.html`` reads these; each tab's view adds its own.
    Edit opens the composer the public event page would (``_event_edit_url``, the one rule):
    the guild's own for its leadership and admins, the Calendar's for a Calendar Administrator
    on a guild event; nobody ``can_edit_event`` refuses gets a link.
    """
    return {
        **_get_hub_context(request),
        "event": event,
        "active_tab": active_tab,
        "detail_url": reverse("hub_admin_event", args=[event.pk]),
        "guests_url": reverse("hub_admin_event_guests", args=[event.pk]),
        "list_url": reverse("hub_admin_events"),
        "when": event.when_display_for(event.next_occurrence_start()),
        "edit_url": _event_edit_url(request, event),
    }


@events_admin_access_required
def hub_admin_event(request: HttpRequest, pk: int) -> HttpResponse:
    """One event's admin screen: the header, the Overview tab and the actions its state allows.

    The decisions post to ``hub_event_review_decision``, Retry sync to ``hub_event_retry_sync``
    and Delete to ``hub_event_delete``, each with ``next`` so the admin lands back here (or on
    the list, once the row is gone). Edit opens the composer the public event page would
    (``_event_edit_url``, through ``_event_screen_context``). Studio hours 404, as they are
    not on the list.
    """
    event = get_object_or_404(_managed_events().select_related("area", "reviewed_by"), pk=pk)
    states = CommunityEvent.ModerationState
    google_sync_enabled = _google_sync_enabled()
    return render(
        request,
        "hub/admin/event_detail.html",
        {
            **_event_screen_context(request, event, active_tab="overview"),
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
    """Take a published event down to a draft; the model refuses any other state, and says
    which service still holds a copy when a removal failed (the event stays published)."""
    user: User = request.user  # type: ignore[assignment]  # the gate wraps @login_required
    event = get_object_or_404(_managed_events(), pk=pk)
    try:
        event.unpublish(actor=user)
    except InvalidEventTransition:
        messages.info(request, "Only a published event can be unpublished.")
    except EventUnpublishError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Event unpublished. It is a draft now, and members no longer see it.")
    return redirect(reverse("hub_admin_event", args=[event.pk]))


# --- The Guest list tab (#831, part 4) -------------------------------------------------------


def _render_guest_list(request: HttpRequest, event: CommunityEvent, *, staff_form: EventStaffGuestForm) -> HttpResponse:
    """The Guest list tab: stat chips, the searchable roster, the update emails and Add a guest.

    The chips count rows by door (members, the public page, staff, Eventbrite) and the rows
    checked in, in one aggregate; the roster is ``prepare_table`` over :meth:`CommunityEvent.guest_list`
    with the search box on ``q``; the update emails are every opted-in guest's, joined for the
    clipboard. ``staff_form`` is blank on the GET and bound with errors after a plain POST.
    """
    sources = EventRSVP.Source
    stats = event.rsvps.aggregate(
        members=Count("pk", filter=Q(member__isnull=False)),
        site=Count("pk", filter=Q(source=sources.SITE)),
        staff=Count("pk", filter=Q(source=sources.STAFF)),
        eventbrite=Count("pk", filter=Q(source=sources.EVENTBRITE)),
        checked_in=Count("pk", filter=Q(checked_in_at__isnull=False)),
    )
    update_emails = list(
        event.rsvps.filter(wants_updates=True).exclude(email="").order_by("email").values_list("email", flat=True)
    )
    table = prepare_table(
        request,
        event.guest_list(),
        search_fields=_GUEST_SEARCH_FIELDS,
        default_sort="created_at",
        sortable=frozenset({"created_at"}),
        search_param="q",
    )
    return render(
        request,
        "hub/admin/event_guests.html",
        {
            **_event_screen_context(request, event, active_tab="guests"),
            "stats": stats,
            "update_emails": ", ".join(update_emails),
            "update_email_count": len(update_emails),
            "staff_form": staff_form,
            **table,
        },
    )


@events_admin_access_required
def hub_admin_event_guests(request: HttpRequest, pk: int) -> HttpResponse:
    """The Guest list tab of one event's admin screen."""
    event = get_object_or_404(_managed_events(), pk=pk)
    return _render_guest_list(request, event, staff_form=EventStaffGuestForm())


@events_admin_access_required
def hub_admin_event_guests_csv(request: HttpRequest, pk: int) -> StreamingHttpResponse:
    """Download the whole guest list as CSV (``hub.event_exports.stream_event_guests_csv``)."""
    event = get_object_or_404(_managed_events(), pk=pk)
    return stream_event_guests_csv(event)


@events_admin_access_required
@require_POST
def hub_admin_event_guest_add(request: HttpRequest, pk: int) -> HttpResponse:
    """Add a guest by hand: the modal's form, upserted by email as ``Added by staff``.

    From the modal (an htmx POST) an invalid form comes back into the modal body with its
    errors, and a saved one answers ``HX-Redirect`` to the tab so the page reloads with the
    new row and the toast. A plain POST re-renders the tab or redirects to it.
    """
    event = get_object_or_404(_managed_events(), pk=pk)
    guests_url = reverse("hub_admin_event_guests", args=[event.pk])
    form = EventStaffGuestForm(request.POST)
    if not form.is_valid():
        if wants_fragment(request):
            return render(request, "hub/admin/_event_guest_add_form.html", {"event": event, "staff_form": form})
        return _render_guest_list(request, event, staff_form=form)
    rsvp = form.save(event)
    event.refresh_discord_announcement()
    messages.success(request, f"Added {rsvp.display_name}.")
    if wants_fragment(request):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = guests_url
        return response
    return redirect(guests_url)


@events_admin_access_required
@require_POST
def hub_admin_event_guest_checkin(request: HttpRequest, pk: int, rsvp_pk: int) -> HttpResponse:
    """Check an RSVP in at the door, or undo it; the row comes back swapped, with a toast."""
    event = get_object_or_404(_managed_events(), pk=pk)
    rsvp = get_object_or_404(event.guest_list(), pk=rsvp_pk)
    checked_in = rsvp.toggle_check_in()
    message = f"{rsvp.display_name} checked in." if checked_in else f"{rsvp.display_name} is no longer checked in."
    if wants_fragment(request):
        response = render(request, "hub/admin/_event_guest_row.html", {"event": event, "rsvp": rsvp})
        trigger_toast(response, message)
        return response
    messages.success(request, message)
    return redirect(reverse("hub_admin_event_guests", args=[event.pk]))


@events_admin_access_required
@require_POST
def hub_admin_event_guest_remove(request: HttpRequest, pk: int, rsvp_pk: int) -> HttpResponse:
    """Take an RSVP off the list, from the row's confirm; an Eventbrite row is refused.

    An Eventbrite row mirrors an order, so the order is the place to cancel it: the next
    webhook would only put a hand-removed row back.
    """
    event = get_object_or_404(_managed_events(), pk=pk)
    rsvp = get_object_or_404(event.guest_list(), pk=rsvp_pk)
    guests_url = reverse("hub_admin_event_guests", args=[event.pk])
    if rsvp.is_read_only:
        messages.info(request, "An Eventbrite RSVP comes and goes with its order. Cancel the order on Eventbrite.")
        return redirect(guests_url)
    name = rsvp.display_name
    rsvp.delete()
    event.refresh_discord_announcement()
    messages.success(request, f"Removed {name}.")
    return redirect(guests_url)
