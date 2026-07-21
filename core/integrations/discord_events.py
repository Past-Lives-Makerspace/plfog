"""Discord Guild Scheduled Events API client + push service (FOG → Discord, one-way).

Mirrors :mod:`core.integrations.google_calendar` shape-for-shape: build via
:meth:`DiscordScheduledEventsClient.from_settings`, check ``.enabled``, and **never raise
to the caller** — a Discord outage records a ``discord_sync_error`` on the event and the
FOG save proceeds regardless.

The bot REST auth is reused verbatim from :mod:`core.events.discord_dm`
(``bot_token`` / ``_auth_headers`` / ``API_BASE``). Two gates make a client ``enabled``:
the admin runtime toggle (``SiteConfiguration.discord_events_sync_enabled``) and a linked
Discord server (``SiteConfiguration.discord_server_id``) plus a non-blank
``DISCORD_BOT_TOKEN``. Any blank → a disabled client, never a raise.

The model (:class:`membership.models.CommunityEvent`) delegates here via a lazy import
(``push_to_discord`` / ``remove_from_discord``), keeping the ``membership → core`` layering
clean — exactly as ``push_to_google`` does.

.. note::
    **Recurrence-map limits are build-time-verify (§5.3 of the spec).** The
    :func:`_recurrence_rule_for` mapping (which ``frequency`` / ``interval`` / ``by_*``
    combinations Discord accepts, whether ``by_n_weekday`` forbids ``interval > 1``,
    whether Monday is weekday ``0``, whether YEARLY supports an nth-weekday, and whether
    the ``recurrence_rule`` object needs its own ``start`` matching
    ``scheduled_start_time``) is implemented from early-2026 documentation knowledge and
    Discord iterates on this endpoint. Confirm each row against a real (or sandbox-server)
    call before go-live; a newly-supported cadence just moves a row from "fallback" to
    "mappable". EXTERNAL events also *require* ``scheduled_end_time`` +
    ``entity_metadata.location`` — omitting either is a hard 400, not a soft degrade.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from core.events.discord_dm import API_BASE, _auth_headers, bot_token

if TYPE_CHECKING:
    from datetime import datetime

    from django.contrib.auth.models import User

    from membership.models import CommunityEvent

logger = logging.getLogger(__name__)

# The one-and-only default venue for an event whose ``location`` is blank — Discord's
# EXTERNAL entity requires a 1–100 char ``entity_metadata.location`` (a blank 400s).
DEFAULT_LOCATION = "Past Lives Makerspace"

_TIMEOUT_SECONDS = 5.0
_SYNC_ERROR_MAX = 500
_NAME_MAX = 100
_LOCATION_MAX = 100
_DESCRIPTION_MAX = 1000
_ENTITY_TYPE_EXTERNAL = 3
_PRIVACY_GUILD_ONLY = 2
_FREQUENCY_MONTHLY = 1
_FREQUENCY_WEEKLY = 2
# How far ahead to look for the next concrete occurrence of an unmappable-cadence event.
_FALLBACK_HORIZON_DAYS = 366


class DiscordEventsError(Exception):
    """A Discord Scheduled Events API call failed (wrapped so callers record a sync error)."""


class DiscordEventsConfig:
    """Sentinel copy for the "why we're not pushing" reasons, so tests + the service share
    one source of truth."""

    SYNC_OFF = "Discord Events sync is off."
    STUDIO_HOURS = "Studio hours are not pushed to Discord."


class DiscordScheduledEventsClient:
    """Minimal Discord Guild Scheduled Events client. Disabled when the toggle is off, the
    bot token is blank, or no Discord server is linked."""

    def __init__(self, *, enabled: bool, server_id: str) -> None:
        self._enabled = enabled
        self._server_id = server_id

    @classmethod
    def from_settings(cls) -> DiscordScheduledEventsClient:
        """Build a client from ``settings`` + ``SiteConfiguration``.

        Returns a disabled client (``enabled is False``) when the admin toggle is off, the
        ``DISCORD_BOT_TOKEN`` is blank, or no ``discord_server_id`` is set — never raises,
        so a mis-set config degrades to "sync off", not a 500.
        """
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        server_id = (config.discord_server_id or "").strip()
        enabled = bool(bot_token() and server_id and config.discord_events_sync_enabled)
        return cls(enabled=enabled, server_id=server_id)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def server_id(self) -> str:
        return self._server_id

    def insert_event(self, server_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Create a Scheduled Event; return the event resource (with ``id``)."""
        return self._execute("POST", f"/guilds/{server_id}/scheduled-events", json=body)

    def update_event(self, server_id: str, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Patch an existing Scheduled Event; return the updated event resource."""
        return self._execute("PATCH", f"/guilds/{server_id}/scheduled-events/{event_id}", json=body)

    def delete_event(self, server_id: str, event_id: str) -> None:
        """Delete a Scheduled Event. Wraps API errors (caller catches one type)."""
        self._execute("DELETE", f"/guilds/{server_id}/scheduled-events/{event_id}")

    @staticmethod
    def _execute(method: str, path: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run a bot-authed REST call, translating any failure into :class:`DiscordEventsError`
        so callers catch one exception type (mirrors ``GoogleCalendarClient._execute``).

        Both a transport failure (``httpx.HTTPError``) and a non-2xx status become a
        ``DiscordEventsError``; a 2xx with an empty body (a ``DELETE`` 204) returns ``{}``.
        """
        try:
            response = httpx.request(
                method,
                f"{API_BASE}{path}",
                json=json,
                headers=_auth_headers(),
                timeout=_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise DiscordEventsError(str(exc)) from exc
        if not response.is_success:
            raise DiscordEventsError(f"Discord API {response.status_code}: {response.text[:300]}")
        return response.json() if response.content else {}


def _build_description(event: CommunityEvent) -> str:
    """The Discord event description: the event's own description (if any), a blank line,
    then the absolute public URL — so the entry is actionable, not a dead title."""
    parts: list[str] = []
    if event.description:
        parts.append(event.description)
    parts.append(event.public_url)
    return "\n\n".join(parts)


def _occurrence_n(event: CommunityEvent) -> int:
    """Discord ``by_n_weekday.n`` (1–5) for a monthly-by-weekday event — the model's
    ``-1`` ('last') maps to Discord's ``5``."""
    ordinal = event._occurrence_ordinal()
    return 5 if ordinal == -1 else ordinal


def _recurrence_rule_for(event: CommunityEvent) -> dict[str, Any] | None:
    """Map a :class:`CommunityEvent` recurrence to a Discord ``recurrence_rule`` dict.

    Returns a dict for the cadences Discord natively expresses (weekly, monthly-by-weekday)
    and ``None`` for a one-off event and for the cadences it cannot express
    (every-2/3/6-months, twice-a-month, yearly-by-weekday) — a ``None`` routes the caller
    to the single-next-occurrence fallback (§5.3). Weekday encoding is ``0=Monday … 6=Sunday``
    (Python ``weekday()`` == Discord's convention). See the module docstring: these limits
    are build-time-verify.
    """
    from membership.models import CommunityEvent as CE
    from django.utils import timezone

    if event.recurrence == CE.Recurrence.NONE:
        return None
    weekday = timezone.localtime(event.starts_at).weekday()
    if event.recurrence == CE.Recurrence.WEEKLY:
        return {"frequency": _FREQUENCY_WEEKLY, "interval": 1, "by_weekday": [weekday]}
    if event.recurrence == CE.Recurrence.MONTHLY:
        return {
            "frequency": _FREQUENCY_MONTHLY,
            "interval": 1,
            "by_n_weekday": [{"n": _occurrence_n(event), "day": weekday}],
        }
    return None


def _next_occurrence(event: CommunityEvent) -> datetime | None:
    """The next concrete start datetime (>= now) of an unmappable-cadence event, within the
    fallback horizon, or ``None`` when the series has no occurrence in that window yet."""
    from datetime import timedelta

    from django.utils import timezone

    now = timezone.now()
    today = timezone.localdate(now)
    horizon = today + timedelta(days=_FALLBACK_HORIZON_DAYS)
    for occ in event.occurrences_in(today, horizon):
        if occ >= now:
            return occ
    return None


def _build_scheduled_event_body(event: CommunityEvent, *, occurrence: datetime | None = None) -> dict[str, Any]:
    """Build the Discord Scheduled Event payload for a :class:`CommunityEvent`.

    All FOG events are off-Discord (physical), so they are ``entity_type = 3`` (EXTERNAL),
    which *requires* ``scheduled_end_time`` + ``entity_metadata.location`` (a blank location
    falls back to :data:`DEFAULT_LOCATION`). When ``occurrence`` is given (the unmappable
    fallback, §5.3) the event is pushed as a single instance at that start with no
    ``recurrence_rule``; otherwise a mappable cadence carries its ``recurrence_rule``.
    """
    start = occurrence if occurrence is not None else event.starts_at
    end = start + (event.ends_at - event.starts_at)
    body: dict[str, Any] = {
        "name": event.title[:_NAME_MAX],
        "privacy_level": _PRIVACY_GUILD_ONLY,
        "entity_type": _ENTITY_TYPE_EXTERNAL,
        "scheduled_start_time": start.isoformat(),
        "scheduled_end_time": end.isoformat(),
        "entity_metadata": {"location": (event.location or DEFAULT_LOCATION)[:_LOCATION_MAX]},
        "description": _build_description(event)[:_DESCRIPTION_MAX],
    }
    if occurrence is None:
        rule = _recurrence_rule_for(event)
        if rule is not None:
            # Discord requires the rule to carry its own start matching scheduled_start_time
            # (omitting it is a hard 400: recurrence_rule.start BASE_TYPE_REQUIRED —
            # confirmed against the live API 2026-07-21).
            rule["start"] = start.isoformat()
            body["recurrence_rule"] = rule
    return body


def _mark(event: CommunityEvent, state: str, error: str) -> None:
    """Set the in-memory Discord sync bookkeeping fields; the caller (the model method) saves."""
    from django.utils import timezone

    from membership.models import CommunityEvent as CE

    event.discord_sync_state = state
    event.discord_sync_error = error
    if state == CE.SyncState.SYNCED:
        event.discord_synced_at = timezone.now()


def push_community_event(event: CommunityEvent, *, actor: User | None = None) -> None:
    """Create or update the event as a Discord Guild Scheduled Event; set the sync fields.

    NEVER raises — records ``IDLE`` (studio hours), ``PENDING`` (sync off), or ``FAILED``
    (API error) instead, so the FOG save is never rolled back. Does not itself save; the
    model's ``push_to_discord`` persists the mutated fields.

    Studio hours are ambient standing hours and are never pushed (the first of the three
    guard sites — the ``needs_discord_push`` queryset and the ``push_to_discord`` delegator
    are the other two). An unmappable-cadence event is pushed as its single next occurrence
    and rolled forward nightly (§5.3); when it has already rolled past its stored occurrence
    the old Discord event has auto-completed and cannot be PATCHed, so a fresh one is created.
    """
    from membership.models import CommunityEvent as CE

    if event.event_type == CE.EventType.STUDIO_HOURS:
        _mark(event, CE.SyncState.IDLE, DiscordEventsConfig.STUDIO_HOURS)
        return

    # from_settings() already folds discord_events_sync_enabled into client.enabled, so this
    # guard needs no second SiteConfiguration.load() — one config query per event, not two.
    client = DiscordScheduledEventsClient.from_settings()
    if not client.enabled:
        _mark(event, CE.SyncState.PENDING, DiscordEventsConfig.SYNC_OFF)
        return

    occurrence: datetime | None = None
    if event.recurrence != CE.Recurrence.NONE and _recurrence_rule_for(event) is None:
        occurrence = _next_occurrence(event)
        if occurrence is None:
            # Nothing in the horizon yet — record SYNCED with no remote event; the nightly
            # roll-forward pushes one when an occurrence enters the window.
            event.discord_pushed_occurrence = None
            _mark(event, CE.SyncState.SYNCED, "")
            return
        if event.discord_event_id and occurrence != event.discord_pushed_occurrence:
            # The previously-pushed single occurrence has rolled forward: the old Discord
            # event auto-completed and cannot be PATCHed into the future — create a fresh one.
            event.discord_event_id = ""

    body = _build_scheduled_event_body(event, occurrence=occurrence)
    try:
        if event.discord_event_id:
            result = client.update_event(client.server_id, event.discord_event_id, body)
        else:
            result = client.insert_event(client.server_id, body)
        event.discord_event_id = str(result["id"])
        event.discord_pushed_occurrence = occurrence
        _mark(event, CE.SyncState.SYNCED, "")
    except DiscordEventsError as exc:
        _mark(event, CE.SyncState.FAILED, str(exc)[:_SYNC_ERROR_MAX])


def remove_community_event(event: CommunityEvent) -> None:
    """Delete the event from Discord (best-effort) BEFORE the FOG row is deleted.

    Needs the stored ``discord_event_id``; a stale remote event on a delete failure is a
    minor, loggable residue — the FOG delete still proceeds.
    """
    client = DiscordScheduledEventsClient.from_settings()
    if client.enabled and event.discord_event_id:
        try:
            client.delete_event(client.server_id, event.discord_event_id)
        except DiscordEventsError:
            logger.warning(
                "Discord Scheduled Event delete failed for event %s; leaving a stale remote event.", event.pk
            )
