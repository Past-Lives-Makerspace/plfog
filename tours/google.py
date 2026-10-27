"""Google's OAuth and Calendar API for a Tour Guide's own calendar (#951): plain HTTP over ``httpx``.

Not :mod:`core.integrations.google_calendar`, which pushes community events through a service
account. Here each guide signs in with their own Google account (web OAuth, authorization code with
offline access) and we act on their calendars with their token. ``httpx`` rather than
``google-api-python-client``: the discovery client speaks ``httplib2``, which ``respx`` (the repo's
HTTP mock, STANDARDS section 7) cannot intercept, and five REST calls need no discovery document.

**Scopes** (checked against Calendar v3's discovery document, revision 20260708):

- ``calendar.events.owned``: create, change and delete events on calendars the guide owns, and list
  them. Listing the events, rather than asking ``freeBusy``, is what lets the busy read leave out our
  own tour events (they carry ``extendedProperties.private.plfogTour``) while still seeing someone
  else's meeting over a booked tour; ``freeBusy`` merges both into one span.
- ``calendar.calendarlist.readonly``: the list of calendars for the picker, and the account's email
  (the main calendar's id).

Narrower than ``calendar.events`` (every calendar the guide can edit, including other people's) and
far narrower than ``calendar`` or ``calendar.readonly``. Both are "sensitive" scopes: an External
OAuth app needs Google's verification, and in Testing mode its refresh tokens expire after 7 days.

Every call takes a ``timeout``. Errors are :class:`GoogleError` (try again later) or its subclass
:class:`GoogleAuthError` (the token is revoked or expired, or a permission was not granted: the
connection is broken until the guide reconnects). A 401 is :class:`GoogleUnauthorizedError`, which
the caller answers with one fresh access token before calling the connection broken.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_URL = "https://www.googleapis.com/calendar/v3"

EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
CALENDAR_LIST_SCOPE = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
SCOPES: tuple[str, ...] = (EVENTS_SCOPE, CALENDAR_LIST_SCOPE)

#: The sync job's patience for one call.
DEFAULT_TIMEOUT = 10.0
#: Most pages of events read from one calendar (250 each): a horizon with more is cut short.
MAX_EVENT_PAGES = 20
#: A call's timeout: seconds, or a callable asked before each page (a caller's shrinking deadline).
Timeout = float | Callable[[], float]
#: 403 reasons that mean the token lacks a permission rather than a passing limit.
_PERMISSION_REASONS = frozenset({"insufficientPermissions", "forbidden", "requiredAccessLevel"})


class GoogleError(Exception):
    """A Google call failed in a way worth trying again later (network, 5xx, rate limit, our setup)."""


class GoogleAuthError(GoogleError):
    """Google no longer accepts the guide's token, or a permission was not granted: reconnect."""


class GoogleUnauthorizedError(GoogleAuthError):
    """A 401: the access token was refused. Worth one fresh token before giving up on the connection."""


class GoogleMissingScopeError(GoogleAuthError):
    """The guide unticked one of the permissions on Google's consent screen."""


class GoogleNotFoundError(GoogleError):
    """The event or calendar is not there (404, or 410 for an event already deleted)."""


class GoogleConflictError(GoogleError):
    """An event with that id already exists on the calendar (409), perhaps deleted."""


def is_configured() -> bool:
    """Whether guides can connect a Google Calendar: the OAuth client is set, and this is not staging.

    Staging is a clone of production (STANDARDS section 10): it must never write to, or read, a
    guide's real calendar.
    """
    if getattr(settings, "IS_STAGING", False):
        return False
    return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)


def redirect_uri() -> str:
    """The callback Google sends the guide back to, on the members host: register exactly this one."""
    return f"{settings.MEMBER_BASE_URL.rstrip('/')}{reverse('tours:google_callback')}"


def authorize_url(state: str, *, login_hint: str = "") -> str:
    """Google's consent screen for the tour scopes, asking for a refresh token every time.

    ``prompt=consent`` with ``access_type=offline`` makes Google return a refresh token on every
    connect, including a reconnect after the old one was revoked.
    """
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "false",
        "state": state,
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


@dataclass(frozen=True)
class Tokens:
    """What Google's token endpoint answered."""

    access_token: str
    expires_at: datetime
    refresh_token: str
    scopes: frozenset[str]


def _token_call(data: dict[str, str], *, timeout: float) -> Tokens:
    """POST the token endpoint; ``invalid_grant`` (revoked or expired) is a :class:`GoogleAuthError`."""
    payload = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
        **data,
    }
    try:
        response = httpx.post(TOKEN_URL, data=payload, timeout=timeout)
    except httpx.HTTPError as exc:
        raise GoogleError(f"Google token call failed: {exc}") from exc
    body = _json(response)
    if not response.is_success:
        error = str(body.get("error", ""))
        description = str(body.get("error_description", ""))
        if error == "invalid_grant":
            raise GoogleAuthError(description or "Google says the token is expired or revoked.")
        raise GoogleError(f"Google token call returned {response.status_code} {error} {description}".strip())
    access_token = str(body.get("access_token", ""))
    if not access_token:
        raise GoogleError("Google's token answer carried no access token.")
    expires_in = int(body.get("expires_in") or 3600)
    return Tokens(
        access_token=access_token,
        expires_at=timezone.now() + timedelta(seconds=expires_in),
        refresh_token=str(body.get("refresh_token", "")),
        scopes=frozenset(str(body.get("scope", "")).split()),
    )


def exchange_code(code: str, *, timeout: float = DEFAULT_TIMEOUT) -> Tokens:
    """Trade the callback's ``code`` for tokens.

    Raises:
        GoogleMissingScopeError: the guide unticked a permission on the consent screen.
        GoogleError: anything else went wrong, including no refresh token in the answer.
    """
    tokens = _token_call(
        {"code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri()}, timeout=timeout
    )
    if not set(SCOPES) <= tokens.scopes:
        raise GoogleMissingScopeError("Not every permission was granted on Google's consent screen.")
    if not tokens.refresh_token:
        raise GoogleError("Google gave no refresh token.")
    return tokens


def refresh(refresh_token: str, *, timeout: float = DEFAULT_TIMEOUT) -> Tokens:
    """A new access token from the stored refresh token (its ``refresh_token`` is usually blank)."""
    return _token_call({"refresh_token": refresh_token, "grant_type": "refresh_token"}, timeout=timeout)


def revoke(token: str, *, timeout: float = DEFAULT_TIMEOUT) -> None:
    """Revoke the token at Google, which ends every token of that grant. A token already dead is fine."""
    try:
        response = httpx.post(REVOKE_URL, data={"token": token}, timeout=timeout)
    except httpx.HTTPError as exc:
        raise GoogleError(f"Google revoke failed: {exc}") from exc
    if response.is_success or _json(response).get("error") == "invalid_token":
        return
    raise GoogleError(f"Google revoke returned {response.status_code}.")


@dataclass(frozen=True)
class Calendar:
    """One calendar the guide owns."""

    id: str
    name: str
    primary: bool

    def as_json(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "primary": self.primary}


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _api(
    method: str,
    path: str,
    *,
    token: str,
    timeout: float,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One Calendar API call, its errors sorted into :class:`GoogleError` and its subclasses."""
    try:
        response = httpx.request(
            method,
            f"{API_URL}{path}",
            params=params,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise GoogleError(f"Google Calendar call failed: {exc}") from exc
    if response.is_success:
        return _json(response)
    error = _json(response).get("error")
    error = error if isinstance(error, dict) else {}
    message = str(error.get("message", "")) or response.reason_phrase
    reasons = {str(item.get("reason", "")) for item in error.get("errors", []) if isinstance(item, dict)}
    status = response.status_code
    if status == 401:
        raise GoogleUnauthorizedError(f"Google Calendar refused the token: {message}")
    if status == 403 and reasons & _PERMISSION_REASONS:
        raise GoogleAuthError(f"Google Calendar refused a permission: {message}")
    if status in (404, 410):
        raise GoogleNotFoundError(message)
    if status == 409:
        raise GoogleConflictError(message)
    raise GoogleError(f"Google Calendar returned {status}: {message}")


def _seconds(timeout: Timeout) -> float:
    """The next request's timeout: asked afresh before every page when ``timeout`` is a callable."""
    return timeout() if callable(timeout) else timeout


def list_calendars(token: str, *, timeout: Timeout = DEFAULT_TIMEOUT) -> list[Calendar]:
    """The calendars the guide owns (the only ones ``calendar.events.owned`` can write), main one first.

    A callable ``timeout`` is asked before each page, so a caller's deadline covers every page.
    """
    calendars: list[Calendar] = []
    page_token = ""
    for _page in range(MAX_EVENT_PAGES):
        params: dict[str, Any] = {"minAccessRole": "owner", "maxResults": 250}
        if page_token:
            params["pageToken"] = page_token
        answer = _api("GET", "/users/me/calendarList", token=token, timeout=_seconds(timeout), params=params)
        for item in answer.get("items", []):
            calendars.append(
                Calendar(
                    id=str(item["id"]),
                    name=str(item.get("summaryOverride") or item.get("summary") or item["id"]),
                    primary=bool(item.get("primary", False)),
                )
            )
        page_token = str(answer.get("nextPageToken", ""))
        if not page_token:
            break
    calendars.sort(key=lambda calendar: (not calendar.primary, calendar.name.casefold()))
    return calendars


def _events_path(calendar_id: str, event_id: str = "") -> str:
    path = f"/calendars/{quote(calendar_id, safe='')}/events"
    return f"{path}/{quote(event_id, safe='')}" if event_id else path


def list_events(
    token: str, calendar_id: str, time_min: datetime, time_max: datetime, *, timeout: Timeout = DEFAULT_TIMEOUT
) -> list[dict[str, Any]]:
    """Every event on one calendar overlapping ``time_min`` to ``time_max``, repeats expanded.

    A callable ``timeout`` is asked before each page, so a caller's deadline covers every page.
    """
    fields = (
        "nextPageToken,items(id,status,transparency,eventType,start,end,"
        "attendees(self,responseStatus),extendedProperties/private)"
    )
    events: list[dict[str, Any]] = []
    page_token = ""
    for _page in range(MAX_EVENT_PAGES):
        params: dict[str, Any] = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "singleEvents": "true",
            "maxResults": 250,
            "fields": fields,
        }
        if page_token:
            params["pageToken"] = page_token
        answer = _api("GET", _events_path(calendar_id), token=token, timeout=_seconds(timeout), params=params)
        events.extend(item for item in answer.get("items", []) if isinstance(item, dict))
        page_token = str(answer.get("nextPageToken", ""))
        if not page_token:
            break
    else:
        logger.warning(
            "Calendar %s has more than %s pages of events; the rest were not read.", calendar_id, MAX_EVENT_PAGES
        )
    return events


def insert_event(token: str, calendar_id: str, body: dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Create the event (``body`` carries our own ``id``) and return its id."""
    answer = _api(
        "POST", _events_path(calendar_id), token=token, timeout=timeout, params={"sendUpdates": "none"}, body=body
    )
    return str(answer.get("id") or body["id"])


def update_event(
    token: str, calendar_id: str, event_id: str, body: dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT
) -> None:
    """Replace the event with ``body`` (``status: confirmed`` brings back one that was deleted)."""
    _api(
        "PUT",
        _events_path(calendar_id, event_id),
        token=token,
        timeout=timeout,
        params={"sendUpdates": "none"},
        body=body,
    )


def delete_event(token: str, calendar_id: str, event_id: str, *, timeout: float = DEFAULT_TIMEOUT) -> None:
    """Delete the event; one already gone counts as deleted."""
    try:
        _api(
            "DELETE",
            _events_path(calendar_id, event_id),
            token=token,
            timeout=timeout,
            params={"sendUpdates": "none"},
        )
    except GoogleNotFoundError:
        return
