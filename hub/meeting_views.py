"""Meeting workspace views (Meetings spec §6.0 / §6.3 — phase 2).

The autosave surface for the one-page agenda-and-minutes workspace. All views are
thin per CLAUDE.md: parse request → permission guard (``can_edit_meeting``) →
``assert_editable`` (403 on locked, per §5.2) → whitelisted-field cleaner →
``save(update_fields=…)`` → 204 + ``HX-Trigger: meeting-saved`` (or a partial
render for the add/move endpoints). An unlisted field is a 400 (fail loudly); an
invalid value is a 422 + error toast.

Lifecycle (approve / unlock / delete), proposals, and the calendar-event routes
are phases 3/4 and deliberately absent here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, time
from functools import partial
from typing import TYPE_CHECKING, Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db.models import Prefetch, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from core.html_sanitize import sanitize_rich_html
from hub.forms import MeetingAttachmentForm, MeetingCreateForm, half_hour_time_choices
from hub.toast import trigger_toast
from hub.views import _get_hub_context
from membership.models import (
    Guild,
    Meeting,
    MeetingActionItem,
    MeetingAgendaItem,
    MeetingAttachment,
    MeetingAttendee,
    MeetingLockedError,
    Member,
)
from membership.permissions import can_edit_meeting

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


def _workspace_context(request: HttpRequest, meeting: Meeting) -> dict[str, Any]:
    """The full draft/locked × editor/read-only rendering context (§6.3)."""
    can_edit = can_edit_meeting(request, meeting)
    return {
        **_get_hub_context(request),
        "meeting": meeting,
        "items": meeting.items.all(),
        "attendees": meeting.attendees.all(),
        "attachments": meeting.attachments.all(),
        "can_edit": can_edit,
        "is_editable": can_edit and not meeting.is_locked,
        "time_choices": half_hour_time_choices(required=False),
        "attachment_form": MeetingAttachmentForm(),
        "open_attachment_modal": False,
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
    """Append an empty agenda item; the response arrives auto-expanded + focused."""
    meeting = get_object_or_404(Meeting.objects.select_related("guild"), pk=pk)
    guard = _guard(request, meeting)
    if guard is not None:
        return guard
    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    item = meeting.add_item(by=user)
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
