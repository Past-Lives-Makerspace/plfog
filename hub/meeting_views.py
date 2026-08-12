"""Meeting workspace views (Meetings spec §6.0 / §6.3 — phase 2).

The autosave surface for the one-page agenda-and-minutes workspace. All views are
thin per CLAUDE.md: parse request → permission guard (``can_edit_meeting``) →
``assert_editable`` (403 on locked, per §5.2) → whitelisted-field cleaner →
``save(update_fields=…)`` → 204 + ``HX-Trigger: meeting-saved`` (or a partial
render for the add/move endpoints). An unlisted field is a 400 (fail loudly); an
invalid value is a 422 + error toast.

Lifecycle (approve / unlock / delete), the carryover panel, and the proposal
routes (propose / decide / withdraw) are phase 3; the Meetings home (§6.2) and
the calendar-event routes (§6.3) are phase 4 and live at the bottom.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, time, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import URLValidator
from django.db.models import Count, Prefetch, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.html_sanitize import sanitize_rich_html
from hub.forms import (
    MeetingAttachmentForm,
    MeetingCreateForm,
    MeetingItemProposalForm,
    MeetingProposalDecisionForm,
    half_hour_time_choices,
)
from hub.toast import trigger_toast
from hub.view_as import ROLE_ADMIN, fog_admin_required
from hub.views import _get_hub_context
from membership.models import (
    CommunityEvent,
    Guild,
    InvalidProposalTransition,
    Meeting,
    MeetingActionItem,
    MeetingAgendaItem,
    MeetingAttachment,
    MeetingAttendee,
    MeetingItemProposal,
    MeetingLockedError,
    Member,
    NextMeeting,
)
from membership.permissions import can_edit_meeting, can_propose_to_meeting, editable_meeting_scopes

if TYPE_CHECKING:
    from django.contrib.auth.models import User


# --- Response helpers (the §5.2 contract) -------------------------------------


def _saved() -> HttpResponse:
    """204 + the ``meeting-saved`` trigger the savestate pill listens for."""
    response = HttpResponse(status=204)
    _set_saved_trigger(response)
    return response


def _set_saved_trigger(response: HttpResponse) -> None:
    """Stamp ``HX-Trigger`` with ``meeting-saved`` for the savestate pill."""
    response["HX-Trigger"] = json.dumps({"meeting-saved": {}})


def _invalid(message: str) -> HttpResponse:
    """422 + error toast — the field re-renders its last-saved value client-side."""
    response = HttpResponse(message, status=422)
    trigger_toast(response, message, "error")
    return response


def _hx_redirect(url: str) -> HttpResponse:
    """204 + ``HX-Redirect`` — a real browser navigation from an HTMX confirm POST.

    The lifecycle confirm modals POST via HTMX, so a plain 302 would be swapped
    inline; the header redirect (the ``hub_admin_member_add`` idiom) navigates the
    whole page and the queued Django message renders on the destination.
    """
    response = HttpResponse(status=204)
    response["HX-Redirect"] = url
    return response


def _guard(request: HttpRequest, meeting: Meeting) -> HttpResponse | None:
    """Permission + lock guard for every mutating route: 403 or None (proceed).

    A locked meeting 403s server-side (the absorbed app's "Approved minutes are
    locked" contract) — the client's lock-race branch keys on this status.
    """
    if not can_edit_meeting(request, meeting):
        return HttpResponse("Forbidden", status=403)
    try:
        meeting.assert_editable()
    except MeetingLockedError:
        return HttpResponse("Approved minutes are locked.", status=403)
    return None


# --- Field cleaners (whitelist values; raise ValueError with the toast copy) --


def _clean_bool(value: str) -> bool:
    if value in ("true", "on", "1"):
        return True
    if value in ("false", "off", "0", ""):
        return False
    raise ValueError("Invalid value.")


def _clean_char(max_length: int) -> Callable[[str], str]:
    def clean(value: str) -> str:
        stripped = value.strip()
        if len(stripped) > max_length:
            raise ValueError(f"Keep it under {max_length} characters.")
        return stripped

    return clean


def _clean_text(value: str) -> str:
    return value.strip()


def _clean_date(value: str) -> date | None:
    if not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError("That doesn't look like a date.") from None


_HALF_HOUR_VALUES = {choice_value for choice_value, _ in half_hour_time_choices(required=True)}


def _clean_half_hour(value: str) -> time | None:
    """A value from the shared half-hour dropdown (rule 19), or blank for time TBD."""
    if not value.strip():
        return None
    if value not in _HALF_HOUR_VALUES:
        raise ValueError("Pick a time from the list.")
    hour_str, minute_str = value.split(":")
    return time(int(hour_str), int(minute_str))


def _clean_url(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""
    try:
        URLValidator()(stripped)
    except ValidationError:
        raise ValueError("That doesn't look like a link.") from None
    return stripped


def _autosave(
    request: HttpRequest,
    *,
    fields: dict[str, Callable[[str], Any]],
    apply: Callable[[str, Any], None],
) -> HttpResponse:
    """The shared autosave core: whitelist → clean → apply → 204/400/422 (§5.2)."""
    field = request.POST.get("field", "")
    if field not in fields:
        return HttpResponse("Unknown field.", status=400)
    try:
        value = fields[field](request.POST.get("value", ""))
        apply(field, value)
    except ValueError as exc:
        return _invalid(str(exc))
    return _saved()


# --- Meeting-level autosave ---------------------------------------------------

_MEETING_FIELDS: dict[str, Callable[[str], Any]] = {
    "is_special": _clean_bool,
    "special_title": _clean_char(120),
    "scheduled_date": _clean_date,
    "scheduled_time": _clean_half_hour,
    "video_call_url": _clean_url,
    "special_notes": sanitize_rich_html,
    "other_notes": sanitize_rich_html,
}

# Saves that must reach an OWNED calendar event (§5.2) — sync_event() itself
# no-ops without an owned link, so calling it unconditionally here is safe.
_EVENT_SYNC_FIELDS = frozenset({"scheduled_date", "scheduled_time", "is_special", "special_title", "video_call_url"})


def _apply_meeting_save(meeting: Meeting, field: str, value: Any) -> None:
    """One meeting-level field save, with the §5.2 coupled-field rule.

    Turning ``is_special`` off clears ``special_title`` in the SAME save, so the
    §4.1 check constraint can never trip from a lone-field autosave; symmetrically,
    ``special_title`` can't be set on a Monthly meeting.
    """
    if field == "special_title" and not meeting.is_special and value:
        raise ValueError("Turn on Special meeting before naming it.")
    setattr(meeting, field, value)
    update_fields = [field, "updated_at"]
    if field == "is_special" and value is False:
        meeting.special_title = ""
        update_fields.insert(1, "special_title")
    meeting.save(update_fields=update_fields)
    if field in _EVENT_SYNC_FIELDS:
        meeting.sync_event()


@login_required
@require_POST
def hub_meeting_save(request: HttpRequest, pk: int) -> HttpResponse:
    """Autosave one meeting-level field (§6.0 ``/meetings/<pk>/save/``)."""
    meeting = get_object_or_404(Meeting.objects.select_related("guild", "event"), pk=pk)
    guard = _guard(request, meeting)
    if guard is not None:
        return guard
    return _autosave(request, fields=_MEETING_FIELDS, apply=partial(_apply_meeting_save, meeting))


# --- Workspace GET ------------------------------------------------------------


def _attendee_picker_context(meeting: Meeting) -> dict[str, Any]:
    """The roster ``<select>`` options: scope roster minus already-added members."""
    if meeting.guild is not None:
        roster = meeting.guild.roster_members()
    else:
        # Council roster: everyone holding lead/staff authority in any guild.
        roster = Member.objects.filter(
            Q(pk__in=Guild.objects.filter(guild_lead__isnull=False).values("guild_lead"))
            | Q(guild_staff_roles__isnull=False)
        ).distinct()
    added = meeting.attendees.filter(member__isnull=False).values("member")
    return {"roster_options": roster.exclude(pk__in=added).order_by("full_legal_name")}


# Mirrors Guild.next_meeting_occurrence's forward window — a year of occurrences.
_OCCURRENCE_HORIZON_DAYS = 370


def _council_cadence() -> NextMeeting | None:
    """The soonest upcoming occurrence of any published LEAD_MEETING event (site-wide).

    Mirrors Guild.next_meeting_occurrence for the council row, which has no Guild FK to
    derive a cadence from. Returns None when no published lead-meeting event exists.
    """
    today = timezone.localdate()
    horizon = today + timedelta(days=_OCCURRENCE_HORIZON_DAYS)
    now = timezone.now()
    events = CommunityEvent.objects.filter(
        event_type=CommunityEvent.EventType.LEAD_MEETING,
        moderation_state=CommunityEvent.ModerationState.PUBLISHED,
        guild__isnull=True,
    )
    for event in events:
        for occurrence in event.occurrences_in(today, horizon):
            if occurrence >= now:
                return NextMeeting(when=occurrence, location=event.location, has_time=True)
    return None


def _scope_events(meeting: Meeting) -> Any:
    """The scope's published calendar events — a guild's own, or site-wide for council."""
    if meeting.guild is not None:
        return CommunityEvent.objects.published().filter(guild=meeting.guild)
    return CommunityEvent.objects.published().filter(guild__isnull=True)


def _linkable_events(meeting: Meeting) -> list[dict[str, Any]]:
    """The 'link an existing event' candidates (§6.3): the scope's upcoming published
    events, each with its concrete occurrence dates (recurring series expand virtually,
    so the modal's occurrence ``<select>`` needs them pre-computed)."""
    today = timezone.localdate()
    horizon = today + timedelta(days=_OCCURRENCE_HORIZON_DAYS)
    entries: list[dict[str, Any]] = []
    for event in _scope_events(meeting).upcoming().order_by("starts_at"):
        occurrences = [timezone.localtime(when).date() for when in event.occurrences_in(today, horizon)[:12]]
        if occurrences:
            entries.append(
                {
                    "event": event,
                    "occurrences": occurrences,
                    "recurring": event.recurrence != CommunityEvent.Recurrence.NONE,
                }
            )
    return entries


def _workspace_context(request: HttpRequest, meeting: Meeting) -> dict[str, Any]:
    """The full draft/locked × editor/read-only rendering context (§6.3)."""
    can_edit = can_edit_meeting(request, meeting)
    is_editable = can_edit and not meeting.is_locked
    view_as = getattr(request, "view_as", None)
    user: User = request.user  # type: ignore[assignment]  # every caller is @login_required
    return {
        **_get_hub_context(request),
        "meeting": meeting,
        "items": meeting.items.all(),
        "attendees": meeting.attendees.all(),
        "attachments": meeting.attachments.all(),
        "can_edit": can_edit,
        "is_editable": is_editable,
        # The Unlock button mirrors the endpoint's gate: ACTUAL admin role, so a
        # view-as preview can't grant or hide it inconsistently (fog_admin_required).
        "can_unlock": meeting.is_locked and view_as is not None and view_as.has_actual(ROLE_ADMIN),
        # The propose button is for members who can't edit (§6.3) — editors add directly.
        "can_propose": not can_edit and can_propose_to_meeting(request, meeting),
        # Deeper than carryover_for's select_related: the panel's group headers render
        # each source meeting's display_title, which touches guild.
        "carryover_actions": (
            meeting.carryover_actions().select_related("item__meeting__guild")
            if is_editable
            else MeetingActionItem.objects.none()
        ),
        "pending_proposals": (
            meeting.proposals.filter(state=MeetingItemProposal.State.PENDING).select_related("proposed_by__member")
            if is_editable
            else MeetingItemProposal.objects.none()
        ),
        "my_proposals": meeting.proposals.filter(proposed_by=user)
        .exclude(state=MeetingItemProposal.State.WITHDRAWN)
        .select_related("created_item"),
        "time_choices": half_hour_time_choices(required=False),
        "attachment_form": MeetingAttachmentForm(),
        "open_attachment_modal": False,
        # The §6.3 calendar block: the link-existing candidates are only worth
        # computing for an editable, still-unlinked draft (the modal's only home).
        "linkable_events": _linkable_events(meeting) if is_editable and meeting.event_id is None else [],
        "can_add_to_calendar": meeting.scheduled_date is not None and meeting.scheduled_time is not None,
        **_attendee_picker_context(meeting),
    }


def _workspace_queryset() -> Any:
    """The workspace GET queryset — prefetched so ``open_action_count`` and the
    proposer credits stay N+1-free however many items the agenda grows."""
    item_qs = MeetingAgendaItem.objects.select_related("proposed_by__member").prefetch_related("actions")
    return Meeting.objects.select_related("guild", "event", "approved_by").prefetch_related(
        Prefetch("items", queryset=item_qs), "attendees__member", "attachments"
    )


@login_required
def hub_meeting(request: HttpRequest, pk: int) -> HttpResponse:
    """The workspace — draft or locked, editor or read-only (§6.3)."""
    meeting = get_object_or_404(_workspace_queryset(), pk=pk)
    return render(request, "hub/meeting_workspace.html", _workspace_context(request, meeting))


# --- Create (the §6.2 modal POST + Start-the-agenda prefill contract) ---------


def _parse_prefill(request: HttpRequest) -> tuple[date | None, time | None]:
    """The optional §6.2 ``date``/``time`` prefill params.

    The time is parsed permissively (ISO, not just the half-hour grid) because a
    guild's cadence time can be an off-grid legacy value — the same tolerance
    ``_seed_time_choice`` extends. Raises ValueError on garbage (a 400).
    """
    prefill_date = _clean_date(request.POST.get("date", ""))
    raw_time = request.POST.get("time", "").strip()
    try:
        prefill_time = time.fromisoformat(raw_time) if raw_time else None
    except ValueError:
        raise ValueError("That doesn't look like a time.") from None
    return prefill_date, prefill_time


@login_required
@require_POST
def hub_meeting_create(request: HttpRequest) -> HttpResponse:
    """Create an EMPTY meeting (create-empty-then-fill) and land in its workspace.

    Carries the §6.2 Start-the-agenda prefill contract (optional ``date``/``time``)
    and its double-create guard: an existing upcoming meeting for the same
    scope + date redirects into THAT workspace instead of creating a twin.
    """
    form = MeetingCreateForm(request.POST, request=request)
    if not form.is_valid():
        # The scope choices are permission-derived, so a bad scope IS a permission failure.
        if "scope" in form.errors:
            return HttpResponse("Forbidden", status=403)
        return HttpResponse("Invalid.", status=400)
    try:
        prefill_date, prefill_time = _parse_prefill(request)
    except ValueError as exc:
        return HttpResponse(str(exc), status=400)
    guild = form.scope_guild()
    if prefill_date is not None:
        existing = Meeting.objects.for_scope(guild).upcoming().filter(scheduled_date=prefill_date).first()
        if existing is not None:
            return redirect("hub_meeting", pk=existing.pk)
    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    meeting = Meeting.objects.create(
        guild=guild,
        is_special=form.cleaned_data["kind"] == "special",
        scheduled_date=prefill_date,
        scheduled_time=prefill_time,
        created_by=user,
    )
    return redirect("hub_meeting", pk=meeting.pk)


# --- Agenda items -------------------------------------------------------------

_ITEM_FIELDS: dict[str, Callable[[str], Any]] = {
    "name": _clean_char(200),
    "description": _clean_text,
    "minutes": sanitize_rich_html,
}


def _apply_child_save(
    instance: MeetingAgendaItem | MeetingActionItem | MeetingAttendee, field: str, value: Any
) -> None:
    setattr(instance, field, value)
    instance.save(update_fields=[field])


def _get_item(pk: int) -> MeetingAgendaItem:
    return get_object_or_404(MeetingAgendaItem.objects.select_related("meeting__guild"), pk=pk)


def _items_response(request: HttpRequest, meeting: Meeting, *, new_item_pk: int | None = None) -> HttpResponse:
    """Re-render the agenda list container (the HTMX swap unit for add + move)."""
    response = render(
        request,
        "hub/partials/_meeting_items.html",
        {
            "meeting": meeting,
            "items": meeting.items.prefetch_related("actions"),
            "is_editable": True,
            "new_item_pk": new_item_pk,
        },
    )
    _set_saved_trigger(response)
    return response


@login_required
@require_POST
def hub_meeting_item_add(request: HttpRequest, pk: int) -> HttpResponse:
    """Append an agenda item with the submitted name; response swaps the full item list."""
    meeting = get_object_or_404(Meeting.objects.select_related("guild"), pk=pk)
    guard = _guard(request, meeting)
    if guard is not None:
        return guard
    name = _clean_char(200)(request.POST.get("name", ""))
    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    item = meeting.add_item(by=user, name=name)
    return _items_response(request, meeting, new_item_pk=item.pk)


@login_required
@require_POST
def hub_meeting_item_save(request: HttpRequest, pk: int) -> HttpResponse:
    item = _get_item(pk)
    guard = _guard(request, item.meeting)
    if guard is not None:
        return guard
    return _autosave(request, fields=_ITEM_FIELDS, apply=partial(_apply_child_save, item))


@login_required
@require_POST
def hub_meeting_item_move(request: HttpRequest, pk: int) -> HttpResponse:
    """Up/down reorder (§6.3) — swaps sort_order, clamped no-op at the ends."""
    item = _get_item(pk)
    guard = _guard(request, item.meeting)
    if guard is not None:
        return guard
    direction = request.POST.get("direction", "")
    if direction not in ("up", "down"):
        return HttpResponse("Unknown direction.", status=400)
    item.move(direction=direction)
    return _items_response(request, item.meeting)


@login_required
@require_POST
def hub_meeting_item_delete(request: HttpRequest, pk: int) -> HttpResponse:
    item = _get_item(pk)
    guard = _guard(request, item.meeting)
    if guard is not None:
        return guard
    item.delete()
    response = HttpResponse("")  # empty outerHTML swap removes the row
    trigger_toast(response, "Topic deleted.", "success")
    return response


# --- Action items -------------------------------------------------------------


def _get_action(pk: int) -> MeetingActionItem:
    return get_object_or_404(MeetingActionItem.objects.select_related("item__meeting__guild"), pk=pk)


def _clean_action_status(value: str) -> str:
    if value not in (MeetingActionItem.Status.OPEN, MeetingActionItem.Status.DONE):
        raise ValueError("Invalid value.")
    return value


def _apply_action_save(action: MeetingActionItem, user: User, field: str, value: Any) -> None:
    """Action saves route ``status`` through the domain transitions (complete/reopen)."""
    if field != "status":
        _apply_child_save(action, field, value)
        return
    if value == action.status:
        return  # idempotent — a debounced double-fire is not an error
    if value == MeetingActionItem.Status.DONE:
        action.complete(by=user)
    else:
        action.reopen()  # ValueError on a dismissed row → 422


@login_required
@require_POST
def hub_meeting_action_add(request: HttpRequest, pk: int) -> HttpResponse:
    """Append an empty action row under an agenda item (create-empty-then-fill)."""
    item = _get_item(pk)
    guard = _guard(request, item.meeting)
    if guard is not None:
        return guard
    action = MeetingActionItem.objects.create(item=item)
    response = render(
        request,
        "hub/partials/_meeting_action.html",
        {"action": action, "is_editable": True, "focus_new": True},
    )
    _set_saved_trigger(response)
    return response


@login_required
@require_POST
def hub_meeting_action_save(request: HttpRequest, pk: int) -> HttpResponse:
    action = _get_action(pk)
    guard = _guard(request, action.item.meeting)
    if guard is not None:
        return guard
    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    fields: dict[str, Callable[[str], Any]] = {
        "name": _clean_char(300),
        "is_flagged": _clean_bool,
        "status": _clean_action_status,
    }
    return _autosave(request, fields=fields, apply=partial(_apply_action_save, action, user))


@login_required
@require_POST
def hub_meeting_action_delete(request: HttpRequest, pk: int) -> HttpResponse:
    action = _get_action(pk)
    guard = _guard(request, action.item.meeting)
    if guard is not None:
        return guard
    action.delete()
    response = HttpResponse("")
    trigger_toast(response, "Action item deleted.", "success")
    return response


# --- Attendance ---------------------------------------------------------------


def _get_attendee(pk: int) -> MeetingAttendee:
    return get_object_or_404(MeetingAttendee.objects.select_related("meeting__guild", "member"), pk=pk)


def _apply_attendee_save(attendee: MeetingAttendee, field: str, value: Any) -> None:
    """Attendee saves keep the member-XOR-guest shape intact (§4.2)."""
    if field == "guest_name":
        if attendee.member_id is not None:
            raise ValueError("Roster names can't be edited.")
        if not value:
            raise ValueError("Guest name can't be empty.")
    _apply_child_save(attendee, field, value)


@login_required
@require_POST
def hub_meeting_attendee_add(request: HttpRequest, pk: int) -> HttpResponse:
    """Add a roster member or a guest; the response appends the row AND OOB-swaps
    the picker so the just-added member drops out of the ``<select>`` (§6.3)."""
    meeting = get_object_or_404(Meeting.objects.select_related("guild"), pk=pk)
    guard = _guard(request, meeting)
    if guard is not None:
        return guard
    member_pk = request.POST.get("member", "").strip()
    guest_name = request.POST.get("guest_name", "").strip()
    if bool(member_pk) == bool(guest_name):
        return _invalid("Pick a member or type a guest name.")
    member = None
    if member_pk:
        if not member_pk.isdigit():
            return _invalid("Pick a member from the list.")
        member = get_object_or_404(Member, pk=int(member_pk))
    try:
        attendee = meeting.add_attendee(member=member, guest_name=guest_name)
    except ValueError as exc:  # duplicate member — the stale-select backstop
        return _invalid(str(exc))
    row_html = render_to_string(
        "hub/partials/_meeting_attendee_row.html",
        {"attendee": attendee, "is_editable": True},
        request=request,
    )
    picker_html = render_to_string(
        "hub/partials/_meeting_attendee_picker.html",
        {"meeting": meeting, "oob": True, **_attendee_picker_context(meeting)},
        request=request,
    )
    response = HttpResponse(row_html + picker_html)
    _set_saved_trigger(response)
    return response


@login_required
@require_POST
def hub_meeting_attendee_save(request: HttpRequest, pk: int) -> HttpResponse:
    attendee = _get_attendee(pk)
    guard = _guard(request, attendee.meeting)
    if guard is not None:
        return guard
    fields: dict[str, Callable[[str], Any]] = {"present": _clean_bool, "guest_name": _clean_char(120)}
    return _autosave(request, fields=fields, apply=partial(_apply_attendee_save, attendee))


@login_required
@require_POST
def hub_meeting_attendee_delete(request: HttpRequest, pk: int) -> HttpResponse:
    attendee = _get_attendee(pk)
    guard = _guard(request, attendee.meeting)
    if guard is not None:
        return guard
    name = attendee.display_name
    attendee.delete()
    response = HttpResponse("")
    trigger_toast(response, f"{name} removed from attendance.", "success")
    return response


# --- Attachments --------------------------------------------------------------


@login_required
@require_POST
def hub_meeting_attachment_add(request: HttpRequest, pk: int) -> HttpResponse:
    """The '+ Attach file or link' modal POST — a conventional form (§6.3)."""
    meeting = get_object_or_404(_workspace_queryset(), pk=pk)
    guard = _guard(request, meeting)
    if guard is not None:
        return guard
    form = MeetingAttachmentForm(request.POST, request.FILES)
    if form.is_valid():
        attachment = form.save(commit=False)
        attachment.meeting = meeting
        attachment.save()
        messages.success(request, "Attachment added.")
        return redirect("hub_meeting", pk=meeting.pk)
    context = {**_workspace_context(request, meeting), "attachment_form": form, "open_attachment_modal": True}
    return render(request, "hub/meeting_workspace.html", context)


@login_required
@require_POST
def hub_meeting_attachment_delete(request: HttpRequest, pk: int) -> HttpResponse:
    attachment = get_object_or_404(MeetingAttachment.objects.select_related("meeting__guild"), pk=pk)
    guard = _guard(request, attachment.meeting)
    if guard is not None:
        return guard
    attachment.delete()  # row only — stored files are never deleted from here
    response = HttpResponse("")
    trigger_toast(response, "Attachment removed.", "success")
    return response


# --- Lifecycle: approve / unlock / delete (§5.3, §6.3 banner + footer) ---------


@login_required
@require_POST
def hub_meeting_approve(request: HttpRequest, pk: int) -> HttpResponse:
    """Approve and lock the minutes (footer confirm modal → redirect to locked mode)."""
    meeting = get_object_or_404(Meeting.objects.select_related("guild"), pk=pk)
    guard = _guard(request, meeting)
    if guard is not None:
        return guard
    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    try:
        meeting.approve(by=user)
    except ValueError as exc:  # undated — "Set the meeting date before approving."
        return _invalid(str(exc))
    messages.success(request, "Minutes approved and locked.")
    return _hx_redirect(reverse("hub_meeting", args=[meeting.pk]))


@fog_admin_required
@require_POST
def hub_meeting_unlock(request: HttpRequest, pk: int) -> HttpResponse:
    """Reopen approved minutes for editing — FOG admin only (banner confirm modal)."""
    meeting = get_object_or_404(Meeting.objects.select_related("guild"), pk=pk)
    user: User = request.user  # type: ignore[assignment]  # @fog_admin_required guarantees User
    try:
        meeting.unlock(by=user)
    except ValueError as exc:  # not currently approved
        return _invalid(str(exc))
    messages.success(request, "Minutes unlocked — the workspace is editable again.")
    return _hx_redirect(reverse("hub_meeting", args=[meeting.pk]))


@login_required
@require_POST
def hub_meeting_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete a DRAFT meeting via ``Meeting.remove`` (locked minutes 403 — unlock first).

    ``remove()`` unwinds an OWNED calendar event through the existing rails and
    leaves a merely-linked one alone (§5.3); the confirm copy names each case.
    """
    meeting = get_object_or_404(Meeting.objects.select_related("guild", "event"), pk=pk)
    guard = _guard(request, meeting)
    if guard is not None:
        return guard
    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    meeting.remove(by=user)
    messages.success(request, "Meeting deleted.")
    return _hx_redirect(reverse("hub_meetings"))


# --- Carryover panel (§5.5, §6.3) ---------------------------------------------


@login_required
@require_POST
def hub_meeting_action_carryover(request: HttpRequest, pk: int) -> HttpResponse:
    """Close (Done) or wave off (Dismiss) a carried-over action from a draft meeting.

    The guard runs against the PANEL meeting (must be an editable draft); the
    action's SOURCE meeting may be locked — that's the one deliberate write into a
    locked meeting's data (§5.5). The response re-renders the whole panel, which
    removes itself once empty.
    """
    action = _get_action(pk)
    meeting_pk = request.POST.get("meeting", "").strip()
    if not meeting_pk.isdigit():
        return HttpResponse("Unknown meeting.", status=400)
    meeting = get_object_or_404(Meeting.objects.select_related("guild"), pk=int(meeting_pk))
    guard = _guard(request, meeting)
    if guard is not None:
        return guard
    if not meeting.carryover_actions().filter(pk=action.pk).exists():
        # Wrong scope, undated panel meeting, or already closed by another editor.
        return HttpResponse("Not a carryover item for this meeting.", status=404)
    op = request.POST.get("op", "")
    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    if op == "complete":
        action.complete(by=user, in_meeting=meeting)
        toast = "Marked done."
    elif op == "dismiss":
        action.dismiss(by=user, in_meeting=meeting)
        toast = "Dismissed."
    else:
        return HttpResponse("Unknown op.", status=400)
    response = render(
        request,
        "hub/partials/_meeting_carryover.html",
        {"meeting": meeting, "carryover_actions": meeting.carryover_actions().select_related("item__meeting__guild")},
    )
    trigger_toast(response, toast, "success")
    return response


# --- Agenda-item proposals (§5.4, §6.3) ---------------------------------------


def _proposer_name(proposal: MeetingItemProposal) -> str:
    member = getattr(proposal.proposed_by, "member", None)
    return member.display_name if member is not None else proposal.proposed_by.username


@login_required
@require_POST
def hub_meeting_propose(request: HttpRequest, pk: int) -> HttpResponse:
    """A member proposes an agenda item (the propose modal POST → 204 + toast).

    ``can_propose_to_meeting`` is the whole gate: it already refuses locked and
    past meetings, so the button-absent states 403 here too.
    """
    meeting = get_object_or_404(Meeting.objects.select_related("guild"), pk=pk)
    if not can_propose_to_meeting(request, meeting):
        return HttpResponse("Forbidden", status=403)
    form = MeetingItemProposalForm(request.POST)
    if not form.is_valid():
        return _invalid(str(next(iter(form.errors.values()))[0]))
    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    meeting.propose_item(by=user, title=form.cleaned_data["title"], why=form.cleaned_data["why"])
    response = HttpResponse(status=204)
    trigger_toast(response, f"Proposed — {meeting.scope_label} leadership will review it.", "success")
    return response


@login_required
def hub_meeting_proposal_decide(request: HttpRequest, pk: int) -> HttpResponse:
    """The reviewer decision modal: GET loads it (HTMX), POST records the decision.

    Approve is edit-then-approve — the posted Title/Why (possibly tweaked) land on
    the created item, the response removes the strip row and OOB-appends the new
    item partial to the accordion. Decline carries an optional note.
    """
    proposal = get_object_or_404(
        MeetingItemProposal.objects.select_related("meeting__guild", "proposed_by__member"), pk=pk
    )
    guard = _guard(request, proposal.meeting)  # reviewer gate + locked-meeting 403
    if guard is not None:
        return guard
    if request.method == "GET":
        mode = request.GET.get("mode", "")
        if mode not in ("approve", "decline"):
            return HttpResponse("Unknown mode.", status=400)
        return render(
            request,
            "hub/partials/_meeting_proposal_decide_modal.html",
            {"proposal": proposal, "mode": mode, "proposer_name": _proposer_name(proposal)},
        )
    form = MeetingProposalDecisionForm(request.POST)
    if not form.is_valid():
        if "decision" in form.errors:
            return HttpResponse("Unknown decision.", status=400)
        return _invalid(str(next(iter(form.errors.values()))[0]))
    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    try:
        if form.cleaned_data["decision"] == "approve":
            item = proposal.approve(reviewer=user, title=form.cleaned_data["title"], why=form.cleaned_data["why"])
            item_html = render_to_string(
                "hub/partials/_meeting_item.html",
                {"item": item, "meeting": proposal.meeting, "is_editable": True, "oob_append": True},
                request=request,
            )
            response = HttpResponse(item_html)  # OOB append; the empty remainder removes the row
            trigger_toast(response, "Added to the agenda.", "success")
        else:
            proposal.decline(reviewer=user, note=form.cleaned_data["note"])
            response = HttpResponse("")
            trigger_toast(response, f"Declined — {_proposer_name(proposal)} was notified.", "success")
    except InvalidProposalTransition:
        return _invalid("This proposal was already decided.")
    return response


@login_required
@require_POST
def hub_meeting_proposal_withdraw(request: HttpRequest, pk: int) -> HttpResponse:
    """The proposer pulls back their own pending proposal — anyone else 403s,
    admins included (a reviewer who wants it gone already has Decline, §5.4)."""
    proposal = get_object_or_404(MeetingItemProposal.objects.select_related("meeting__guild"), pk=pk)
    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    if proposal.proposed_by_id != user.pk:
        return HttpResponse("Forbidden", status=403)
    try:
        proposal.withdraw(by=user)
    except InvalidProposalTransition:
        return _invalid("This proposal was already decided.")
    response = HttpResponse("")
    trigger_toast(response, "Withdrawn.", "success")
    return response


# --- The Meetings home (§6.2) --------------------------------------------------

_ARCHIVE_PAGE_SIZE = 25


def _scope_is_editable(guild_id: int | None, editable_ids: set[int], council_editable: bool) -> bool:
    """Whether one meeting scope (guild pk, or None for council) is viewer-editable."""
    if guild_id is None:
        return council_editable
    return guild_id in editable_ids


def _dash_rows(editable_ids: set[int], council_editable: bool, upcoming: list[Meeting]) -> list[dict[str, Any]]:
    """Zone 2's coordinator rows: the Council row first, then every active guild (§6.2).

    'Most recent' is the latest PAST-DATED meeting only — an approved *future* meeting
    belongs solely to 'Next scheduled'. A guild with no upcoming Meeting row falls back
    to its cadence-derived ``next_meeting_occurrence()`` (no row to link — plain text
    for members, 'Start the agenda' for that scope's editors).
    """
    latest_past: dict[int | None, Meeting] = {}
    for meeting in Meeting.objects.past().select_related("guild").order_by("scheduled_date", "pk"):
        latest_past[meeting.guild_id] = meeting  # ascending date order — the last write wins
    next_up: dict[int | None, Meeting] = {}
    for meeting in upcoming:  # already soonest-first — the first write wins
        next_up.setdefault(meeting.guild_id, meeting)
    council_next = next_up.get(None)
    rows: list[dict[str, Any]] = [
        {
            "guild": None,
            "label": "Council",
            "most_recent": latest_past.get(None),
            "next_meeting": council_next,
            "cadence": None if council_next else _council_cadence(),
            "can_edit": council_editable,
        }
    ]
    for guild in Guild.objects.filter(is_active=True).order_by("name").prefetch_related("events"):
        next_meeting = next_up.get(guild.pk)
        rows.append(
            {
                "guild": guild,
                "label": guild.name,
                "most_recent": latest_past.get(guild.pk),
                "next_meeting": next_meeting,
                "cadence": guild.next_meeting_occurrence() if next_meeting is None else None,
                "can_edit": guild.pk in editable_ids,
            }
        )
    return rows


def _archive_page(request: HttpRequest) -> dict[str, Any]:
    """Zone 3's filtered, paginated archive (§6.2): guild + year GET filters that never
    hide undated drafts (they match every year — an interrupted draft's only copy must
    stay findable), 25/page, undated rows last via the queryset's ``nulls_last``."""
    guild_filter = request.GET.get("guild", "")
    year_filter = request.GET.get("year", "")
    archive_qs = Meeting.objects.archive().select_related("guild")
    if guild_filter == "council":
        archive_qs = archive_qs.filter(guild__isnull=True)
    elif guild_filter.isdigit():
        archive_qs = archive_qs.filter(guild_id=int(guild_filter))
    if year_filter.isdigit():
        archive_qs = archive_qs.filter(Q(scheduled_date__year=int(year_filter)) | Q(scheduled_date__isnull=True))
    page = Paginator(archive_qs, _ARCHIVE_PAGE_SIZE).get_page(request.GET.get("page"))
    params = {key: value for key, value in (("guild", guild_filter), ("year", year_filter)) if value}
    return {
        "archive_page": page,
        "archive_guild_filter": guild_filter,
        "archive_year_filter": year_filter,
        "archive_years": [when.year for when in Meeting.objects.dates("scheduled_date", "year", order="DESC")],
        "archive_filtered": bool(params),
        "archive_base_params": urlencode(params),
    }


@login_required
def hub_meetings(request: HttpRequest) -> HttpResponse:
    """The Meetings home (§6.2): Upcoming, the editor 'Needs attention' strip, the
    coordinator table, the filtered archive, and the '+ New meeting' modal."""
    editable_guilds, council_editable = editable_meeting_scopes(request)
    editable_ids = {guild.pk for guild in editable_guilds}
    can_create = council_editable or bool(editable_ids)

    upcoming = list(
        Meeting.objects.upcoming()
        .select_related("guild")
        .annotate(
            topic_count=Count("items", distinct=True),
            pending_count=Count(
                "proposals", filter=Q(proposals__state=MeetingItemProposal.State.PENDING), distinct=True
            ),
        )
    )
    for meeting in upcoming:
        meeting.viewer_can_edit = _scope_is_editable(meeting.guild_id, editable_ids, council_editable)

    attention: list[Meeting] = []
    if can_create:
        # Anyone with edit rights anywhere edits the council scope too (§5.1), so
        # can_create ⇒ council rows belong in this editor's strip.
        scope_q = Q(guild_id__in=editable_ids) | Q(guild__isnull=True)
        attention = list(
            Meeting.objects.needs_attention().filter(scope_q).select_related("guild").order_by("created_at")
        )

    guild_filter = request.GET.get("guild", "")
    create_form = None
    if can_create:
        # The archive's ?guild filter doubles as the guild-tab entry point's preselect (§6.2/§6.4).
        create_form = MeetingCreateForm(request=request, initial={"scope": guild_filter} if guild_filter else None)
    return render(
        request,
        "hub/meetings_home.html",
        {
            **_get_hub_context(request),
            "upcoming_meetings": upcoming,
            "attention_meetings": attention,
            "dash_rows": _dash_rows(editable_ids, council_editable, upcoming),
            "can_create": can_create,
            "create_form": create_form,
            **_archive_page(request),
        },
    )


# --- Calendar rails: create / link / unlink (§5.3, §6.3) -----------------------


@login_required
@require_POST
def hub_meeting_event(request: HttpRequest, pk: int) -> HttpResponse:
    """The Add-to-calendar modal POST: create + own an event, or link an existing one.

    No ``event`` in the body → ``create_calendar_event`` (rides ``schedule_or_go_live``
    — announce + Google + Discord + reminders on the existing rails). An ``event`` pk →
    ``link_event`` with the posted occurrence date (defaulting to the event's own start
    date for a non-recurring event). ValueErrors surface as 422 toasts (§6.3).
    """
    meeting = get_object_or_404(Meeting.objects.select_related("guild", "event"), pk=pk)
    guard = _guard(request, meeting)
    if guard is not None:
        return guard
    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    event_pk = request.POST.get("event", "").strip()
    try:
        if event_pk:
            if not event_pk.isdigit():
                return HttpResponse("Unknown event.", status=400)
            event = get_object_or_404(_scope_events(meeting), pk=int(event_pk))
            occurrence = _clean_date(request.POST.get("occurrence", ""))
            if occurrence is None:
                occurrence = timezone.localtime(event.starts_at).date()
            meeting.link_event(event, occurrence, by=user)
            messages.success(request, "Linked to the calendar event.")
        else:
            meeting.create_calendar_event(by=user)
            messages.success(request, "On the calendar — Google and Discord will sync.")
    except ValueError as exc:
        return _invalid(str(exc))
    return _hx_redirect(reverse("hub_meeting", args=[meeting.pk]))


@login_required
@require_POST
def hub_meeting_event_unlink(request: HttpRequest, pk: int) -> HttpResponse:
    """Drop the calendar link — never deletes or edits the event (§5.3)."""
    meeting = get_object_or_404(Meeting.objects.select_related("guild", "event"), pk=pk)
    guard = _guard(request, meeting)
    if guard is not None:
        return guard
    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    try:
        meeting.unlink_event(by=user)
    except ValueError as exc:  # no event linked
        return _invalid(str(exc))
    messages.success(request, "Unlinked — the event stays on the calendar.")
    return _hx_redirect(reverse("hub_meeting", args=[meeting.pk]))
