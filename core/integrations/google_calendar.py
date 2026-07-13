"""Google Calendar API client + push service (FOG → Google, one-way).

Mirrors the deferred-integration pattern of :mod:`core.integrations.mailchimp` /
:mod:`core.integrations.simplybook`: build via
:meth:`GoogleCalendarClient.from_settings`, check ``.enabled``, and **never raise to
the caller** — a Google outage records a ``sync_error`` on the event and the FOG save
proceeds regardless.

Credentials come from ``settings.GOOGLE_SERVICE_ACCOUNT_JSON`` (raw service-account JSON
or its base64 encoding) behind the ``settings.GOOGLE_CALENDAR_SYNC_ENABLED`` master
flag. The admin runtime toggle (``SiteConfiguration.google_calendar_sync_enabled``) is
the second gate, checked in :func:`push_community_event`. **Both** must be on, plus a
target Calendar ID, before any event is pushed.

The model (:class:`membership.models.CommunityEvent`) delegates here via a lazy import
(``push_to_google`` / ``remove_from_google``), keeping the ``membership → core`` layering
clean — exactly as ``CommunityEvent.announce`` lazily imports ``emit``.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import TYPE_CHECKING, Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from membership.models import CommunityEvent

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar"]
_EVENT_TIMEZONE = "America/Los_Angeles"
_SYNC_ERROR_MAX = 500


class GoogleCalendarError(Exception):
    """A Google Calendar API call failed (wrapped so callers can record ``sync_error``)."""


class GoogleCalendarConfig:
    """Sentinel names for the two "why we're not pushing" reasons, so tests + the sync
    badge share one source of truth for the copy."""

    SYNC_OFF = "Google Calendar sync is turned off."
    NO_CALENDAR = "No Google Calendar linked for this event yet."


class GoogleCalendarClient:
    """Minimal Google Calendar v3 client. Disabled when the flag is off or creds blank."""

    def __init__(self, service: Any | None) -> None:
        # Typed ``Any`` so the API methods (only reached when ``enabled``) don't trip
        # union-attr; ``enabled`` reflects whether it was actually built.
        self._service: Any = service

    @classmethod
    def from_settings(cls) -> GoogleCalendarClient:
        """Build a client from ``settings``.

        Returns a disabled client (``enabled is False``) when the master flag is off, the
        service-account JSON is blank/unparseable, or credential/discovery construction
        fails — never raises, so a mis-set env var degrades to "sync off", not a 500.
        """
        from django.conf import settings

        raw = (getattr(settings, "GOOGLE_SERVICE_ACCOUNT_JSON", "") or "").strip()
        if not getattr(settings, "GOOGLE_CALENDAR_SYNC_ENABLED", False) or not raw:
            return cls(service=None)
        info = _parse_service_account_info(raw)
        if info is None:
            return cls(service=None)
        try:
            credentials = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
            service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        except Exception as exc:  # credential/discovery failure → stay disabled, never raise
            logger.warning("Google Calendar client init failed: %s", exc)
            return cls(service=None)
        return cls(service=service)

    @property
    def enabled(self) -> bool:
        return self._service is not None

    def insert_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Create an event; return the Google event resource (with ``id`` + ``iCalUID``)."""
        return self._execute(self._service.events().insert(calendarId=calendar_id, body=body))

    def update_event(self, calendar_id: str, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Replace an existing event; return the updated Google event resource."""
        return self._execute(self._service.events().update(calendarId=calendar_id, eventId=event_id, body=body))

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        """Delete an event. No-op-safe from the caller's view (wraps API errors)."""
        self._execute(self._service.events().delete(calendarId=calendar_id, eventId=event_id))

    @staticmethod
    def _execute(request: Any) -> Any:
        """Run a built request, translating any failure into our own
        :class:`GoogleCalendarError` so callers catch one exception type.

        Catches broadly (like :meth:`from_settings`): the service-account token is minted
        lazily on the first ``execute()``, so a transport/auth failure surfaces here as a
        ``google.auth`` ``RefreshError``/``TransportError`` or a socket ``OSError`` — NOT a
        ``googleapiclient`` ``HttpError``. Wrapping only ``HttpError`` would let those escape
        the ``GoogleCalendarError`` guards in :func:`push_community_event` /
        :func:`remove_community_event` and 500 the event request path, breaking this module's
        "never raise to the caller" contract.
        """
        try:
            return request.execute()
        except Exception as exc:
            # Broad by design: HttpError + google.auth transport/refresh + socket errors must
            # all degrade to a recorded sync_error, never 500 the caller (see the docstring).
            raise GoogleCalendarError(str(exc)) from exc


def _parse_service_account_info(raw: str) -> dict[str, Any] | None:
    """Parse the service-account key from raw JSON or base64-of-JSON. ``None`` on failure."""
    for candidate in (raw, _maybe_b64_decode(raw)):
        if candidate is None:
            continue
        try:
            info = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(info, dict):
            return info
    logger.warning("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON or base64-encoded JSON.")
    return None


def _maybe_b64_decode(raw: str) -> str | None:
    try:
        return base64.b64decode(raw, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


def _display_name(user: User | None) -> str:
    """A friendly attribution name — full name, then the member's display name, then the
    username, then a graceful generic when nothing is set."""
    if user is None:
        return "a Past Lives member"
    full = (user.get_full_name() or "").strip()
    if full:
        return full
    member = getattr(user, "member", None)
    display = getattr(member, "display_name", "") if member is not None else ""
    if display:
        return display
    return user.get_username()


def _build_event_body(event: CommunityEvent, *, actor: User | None) -> dict[str, Any]:
    """Build the Google event body from a :class:`CommunityEvent`.

    Attribution ("Added by <name> via FOG") is appended to the description. Events are
    always timed (``dateTime`` + Portland timezone) — ``CommunityEvent`` has no
    ``all_day`` field, so Google's all-day ``date`` form is reserved for a future field.
    A recurring event carries its ``ical_rrule()`` as a single ``RRULE`` line.
    """
    who = _display_name(event.created_by or event.submitted_by or actor)
    description = f"{event.description}\n\n" if event.description else ""
    description += f"Added by {who} via FOG"
    body: dict[str, Any] = {
        "summary": event.title,
        "location": event.location,
        "description": description,
        "start": {"dateTime": event.starts_at.isoformat(), "timeZone": _EVENT_TIMEZONE},
        "end": {"dateTime": event.ends_at.isoformat(), "timeZone": _EVENT_TIMEZONE},
    }
    rrule = event.ical_rrule()
    if rrule:
        body["recurrence"] = [f"RRULE:{rrule}"]
    return body


def _mark(event: CommunityEvent, state: str, error: str) -> None:
    """Set the in-memory sync bookkeeping fields; the caller (the model method) saves."""
    from django.utils import timezone

    from membership.models import CommunityEvent as CE

    event.sync_state = state
    event.sync_error = error
    if state == CE.SyncState.SYNCED:
        event.synced_at = timezone.now()


def push_community_event(event: CommunityEvent, *, actor: User | None = None) -> None:
    """Insert or update the event on its target Google calendar; set the sync fields.

    NEVER raises — records ``PENDING`` (sync off / no calendar) or ``FAILED`` (API error)
    instead, so the FOG save is never rolled back. Does not itself save; the model's
    ``push_to_google`` persists the mutated fields.
    """
    from core.models import SiteConfiguration
    from membership.models import CommunityEvent as CE

    client = GoogleCalendarClient.from_settings()
    config = SiteConfiguration.load()
    if event.google_calendar_target == CE.GoogleCalendarTarget.PUBLIC:
        target = config.public_google_calendar_id
    else:
        target = config.member_google_calendar_id

    if not (client.enabled and config.google_calendar_sync_enabled):
        _mark(event, CE.SyncState.PENDING, GoogleCalendarConfig.SYNC_OFF)
        return
    if not target:
        _mark(event, CE.SyncState.PENDING, GoogleCalendarConfig.NO_CALENDAR)
        return

    body = _build_event_body(event, actor=actor)
    try:
        if event.google_event_id:
            g = client.update_event(target, event.google_event_id, body)
        else:
            g = client.insert_event(target, body)
        event.google_event_id = g["id"]
        event.google_ical_uid = g.get("iCalUID") or f"{g['id']}@google.com"
        event.google_calendar_id = target
        _mark(event, CE.SyncState.SYNCED, "")
    except GoogleCalendarError as exc:
        _mark(event, CE.SyncState.FAILED, str(exc)[:_SYNC_ERROR_MAX])


def remove_community_event(event: CommunityEvent) -> None:
    """Delete the event from Google (best-effort) BEFORE the FOG row is deleted.

    A stale remote event on a delete failure is a minor, loggable residue — the FOG
    delete still proceeds.
    """
    client = GoogleCalendarClient.from_settings()
    if client.enabled and event.google_event_id and event.google_calendar_id:
        try:
            client.delete_event(event.google_calendar_id, event.google_event_id)
        except GoogleCalendarError:
            logger.warning("Google Calendar delete failed for event %s; leaving a stale remote event.", event.pk)
