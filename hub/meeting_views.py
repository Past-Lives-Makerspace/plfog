"""Meeting page views: scheduling only (#1113).

A meeting is a scope, a date, start and end times and a video link. The page autosaves
those fields one at a time (``hub_meeting_save``), carries the calendar card (create, link
and unlink the event) and Delete. The views are thin: parse the request, check the
permission (``can_edit_meeting``), clean a whitelisted field, save, and answer 204 with
``HX-Trigger: meeting-saved`` (or a redirect for the confirm posts). An unlisted field is a
400; an invalid value is a 422 with an error toast. A past meeting opens read only.

The Meetings home lists upcoming meetings, meetings by guild and the archive.
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
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from hub.forms import MeetingCreateForm, half_hour_time_choices
from hub.toast import trigger_toast
from hub.views import _get_hub_context
from membership.models import CommunityEvent, Guild, Meeting, NextMeeting, NextMeetingSources
from membership.permissions import can_edit_meeting, editable_meeting_scopes

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

    The Delete and calendar confirm modals POST via HTMX, so a plain 302 would be swapped
    inline; the header redirect (the ``hub_admin_member_add`` idiom) navigates the
    whole page and the queued Django message renders on the destination.
    """
    response = HttpResponse(status=204)
    response["HX-Redirect"] = url
    return response


def _guard(request: HttpRequest, meeting: Meeting) -> HttpResponse | None:
    """The permission gate for every mutating route: 403, or None to proceed."""
    if not can_edit_meeting(request, meeting):
        return HttpResponse("Forbidden", status=403)
    return None


def _guard_current(request: HttpRequest, meeting: Meeting) -> HttpResponse | None:
    """:func:`_guard`, and a past meeting is read only (#1113): 403 for Delete and the calendar card.

    The field autosave uses :func:`_guard` alone, because the date input saves while a year
    is typed and passes through past years (0002, 0020, 0202) on the way to 2026.
    """
    guard = _guard(request, meeting)
    if guard is not None:
        return guard
    if meeting.is_past:
        return HttpResponse("This meeting has already happened.", status=403)
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
    "scheduled_end_time": _clean_half_hour,
    "video_call_url": _clean_url,
}

# Saves that must reach an OWNED calendar event (§5.2) — sync_event() itself
# no-ops without an owned link, so calling it unconditionally here is safe.
_EVENT_SYNC_FIELDS = frozenset(
    {"scheduled_date", "scheduled_time", "scheduled_end_time", "is_special", "special_title", "video_call_url"}
)


def _apply_meeting_save(meeting: Meeting, field: str, value: Any, *, by: User) -> None:
    """One meeting-level field save, with the §5.2 coupled-field rule.

    Turning ``is_special`` off clears ``special_title`` in the SAME save, so the
    §4.1 check constraint can never trip from a lone-field autosave; symmetrically,
    ``special_title`` can't be set on a Monthly meeting. The save that makes the
    meeting :attr:`Meeting.is_calendar_ready` (a time and a date today or later) also gives
    it its calendar event (#596).
    """
    if field == "special_title" and not meeting.is_special and value:
        raise ValueError("Turn on Special meeting before naming it.")
    # End must be after start — checked against the other field's current value (autosave
    # writes one field at a time), for a change to either the start or the end.
    if (
        field == "scheduled_end_time"
        and value is not None
        and meeting.scheduled_time is not None
        and value <= meeting.scheduled_time
    ):
        raise ValueError("End time must be after the start time.")
    if (
        field == "scheduled_time"
        and value is not None
        and meeting.scheduled_end_time is not None
        and value >= meeting.scheduled_end_time
    ):
        raise ValueError("Start time must be before the end time.")
    was_ready = meeting.is_calendar_ready
    setattr(meeting, field, value)
    update_fields = [field, "updated_at"]
    if field == "is_special" and value is False:
        meeting.special_title = ""
        update_fields.insert(1, "special_title")
    meeting.save(update_fields=update_fields)
    if field in _EVENT_SYNC_FIELDS:
        meeting.sync_event()
    if not was_ready:
        meeting.add_to_calendar_if_scheduled(by=by)


@login_required
@require_POST
def hub_meeting_save(request: HttpRequest, pk: int) -> HttpResponse:
    """Autosave one meeting-level field (§6.0 ``/meetings/<pk>/save/``)."""
    meeting = get_object_or_404(Meeting.objects.select_related("guild", "event"), pk=pk)
    guard = _guard(request, meeting)
    if guard is not None:
        return guard
    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    return _autosave(request, fields=_MEETING_FIELDS, apply=partial(_apply_meeting_save, meeting, by=user))


# --- Meeting page GET ---------------------------------------------------------


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
    """The meeting page context: editable for its editors until the meeting is past (#1113)."""
    is_editable = can_edit_meeting(request, meeting) and not meeting.is_past
    return {
        **_get_hub_context(request),
        "meeting": meeting,
        "is_editable": is_editable,
        "time_choices": half_hour_time_choices(required=False),
        # The link-existing candidates are only worth computing for an editable, unlinked meeting.
        "linkable_events": _linkable_events(meeting) if is_editable and meeting.event_id is None else [],
        "can_add_to_calendar": meeting.scheduled_date is not None and meeting.scheduled_time is not None,
    }


@login_required
def hub_meeting(request: HttpRequest, pk: int) -> HttpResponse:
    """The meeting page: editable for its editors, read only for everyone else and once past."""
    meeting = get_object_or_404(Meeting.objects.select_related("guild", "event"), pk=pk)
    return render(request, "hub/meeting_workspace.html", _workspace_context(request, meeting))


# --- Create (the New meeting modal and the Schedule it prefill) ---------------


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
    """Create a meeting and land on its page.

    Carries the Schedule it prefill (optional ``date``/``time``) and its double-create
    guard: an existing upcoming meeting for the same scope + date redirects into THAT
    meeting instead of creating a twin. A future date and time give it its calendar event.
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
    meeting.add_to_calendar_if_scheduled(by=user)
    return redirect("hub_meeting", pk=meeting.pk)


# --- Delete --------------------------------------------------------------------


@login_required
@require_POST
def hub_meeting_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete a meeting via ``Meeting.remove`` (a past meeting is read only: 403).

    ``remove()`` unwinds an OWNED calendar event through the existing rails and
    leaves a merely-linked one alone (§5.3); the confirm copy names each case.
    """
    meeting = get_object_or_404(Meeting.objects.select_related("guild", "event"), pk=pk)
    guard = _guard_current(request, meeting)
    if guard is not None:
        return guard
    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    meeting.remove(by=user)
    messages.success(request, "Meeting deleted.")
    return _hx_redirect(reverse("hub_meetings"))


# --- The Meetings home ---------------------------------------------------------

_ARCHIVE_PAGE_SIZE = 25


def _dash_rows(editable_ids: set[int], council_editable: bool, upcoming: list[Meeting]) -> list[dict[str, Any]]:
    """The Meetings by guild rows: the Council row first, then every active guild.

    'Most recent' is the latest PAST-DATED meeting only. A scope with no upcoming Meeting row
    falls back to its schedule (``cadence``: the soonest of anything, as the guild page card
    shows, #953) and, for that scope's editors, a Schedule it suggestion (``suggested``: the
    soonest meeting only, so studio hours never get one).
    """
    latest_past: dict[int | None, Meeting] = {}
    for meeting in Meeting.objects.past().select_related("guild").order_by("scheduled_date", "pk"):
        latest_past[meeting.guild_id] = meeting  # ascending date order — the last write wins
    next_up: dict[int | None, Meeting] = {}
    for meeting in upcoming:  # already soonest-first — the first write wins
        next_up.setdefault(meeting.guild_id, meeting)
    council_next = next_up.get(None)
    council_cadence = None if council_next else _council_cadence()
    rows: list[dict[str, Any]] = [
        {
            "guild": None,
            "label": "Council",
            "most_recent": latest_past.get(None),
            "next_meeting": council_next,
            "cadence": council_cadence,
            "suggested": council_cadence,
            "can_edit": council_editable,
        }
    ]
    guilds = list(Guild.objects.filter(is_active=True).order_by("name").prefetch_related("events"))
    # The guild page's Next Meeting card reads the same sources, loaded once for every row (#953).
    sources = NextMeetingSources.load(guilds)
    for guild in guilds:
        next_meeting = next_up.get(guild.pk)
        rows.append(
            {
                "guild": guild,
                "label": guild.name,
                "most_recent": latest_past.get(guild.pk),
                "next_meeting": next_meeting,
                "cadence": guild.next_meeting_occurrence(sources) if next_meeting is None else None,
                "suggested": (
                    guild.next_meeting_occurrence(sources, meetings_only=True) if next_meeting is None else None
                ),
                "can_edit": guild.pk in editable_ids,
            }
        )
    return rows


def _schedule_rows(dash_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Editable scopes with a schedule but nothing upcoming yet, for the Needs attention strip's
    Schedule it buttons. Filters the already-computed ``dash_rows``, no new query."""
    return [
        row for row in dash_rows if row["can_edit"] and row["next_meeting"] is None and row["suggested"] is not None
    ]


def _archive_page(request: HttpRequest) -> dict[str, Any]:
    """The filtered, paginated archive: guild + year GET filters that never hide undated
    meetings (they match every year, so an interrupted create stays findable), 25 a page,
    undated rows last via the queryset's ``nulls_last``."""
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
    """The Meetings home: Upcoming, the editor's Needs attention strip (Schedule it, and
    meetings with no date), Meetings by guild, the archive, and the New meeting modal."""
    editable_guilds, council_editable = editable_meeting_scopes(request)
    editable_ids = {guild.pk for guild in editable_guilds}
    can_create = council_editable or bool(editable_ids)
    upcoming = list(Meeting.objects.upcoming().select_related("guild"))

    undated: list[Meeting] = []
    if can_create:
        # Anyone with edit rights anywhere edits the council scope too, so can_create means
        # council rows belong in this editor's strip.
        scope_q = Q(guild_id__in=editable_ids) | Q(guild__isnull=True)
        undated = list(Meeting.objects.undated().filter(scope_q).select_related("guild"))

    guild_filter = request.GET.get("guild", "")
    create_form = None
    if can_create:
        # The archive's ?guild filter doubles as the guild tab's preselect for the New meeting modal.
        create_form = MeetingCreateForm(request=request, initial={"scope": guild_filter} if guild_filter else None)
    dash_rows = _dash_rows(editable_ids, council_editable, upcoming)
    return render(
        request,
        "hub/meetings_home.html",
        {
            **_get_hub_context(request),
            "upcoming_meetings": upcoming,
            "undated_meetings": undated,
            "dash_rows": dash_rows,
            "schedule_rows": _schedule_rows(dash_rows),
            "can_create": can_create,
            "create_form": create_form,
            **_archive_page(request),
        },
    )


# --- Calendar card: create / link / unlink -------------------------------------


@login_required
@require_POST
def hub_meeting_event(request: HttpRequest, pk: int) -> HttpResponse:
    """The Add-to-calendar modal POST: create + own an event, or link an existing one.

    No ``event`` in the body → ``create_calendar_event`` (Google + Discord + reminders on
    the existing rails, and a draft announcement instead of an announcement). An ``event`` pk →
    ``link_event`` with the posted occurrence date (defaulting to the event's own start
    date for a non-recurring event). ValueErrors surface as 422 toasts (§6.3).
    """
    meeting = get_object_or_404(Meeting.objects.select_related("guild", "event"), pk=pk)
    guard = _guard_current(request, meeting)
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
            messages.success(request, "On the calendar. Its announcement is waiting in your Announcements drafts.")
    except ValueError as exc:
        return _invalid(str(exc))
    return _hx_redirect(reverse("hub_meeting", args=[meeting.pk]))


@login_required
@require_POST
def hub_meeting_event_unlink(request: HttpRequest, pk: int) -> HttpResponse:
    """Drop the calendar link — never deletes or edits the event (§5.3)."""
    meeting = get_object_or_404(Meeting.objects.select_related("guild", "event"), pk=pk)
    guard = _guard_current(request, meeting)
    if guard is not None:
        return guard
    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    try:
        meeting.unlink_event(by=user)
    except ValueError as exc:  # no event linked
        return _invalid(str(exc))
    messages.success(request, "Unlinked — the event stays on the calendar.")
    return _hx_redirect(reverse("hub_meeting", args=[meeting.pk]))
