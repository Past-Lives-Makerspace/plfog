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
import time
from typing import TYPE_CHECKING, Any, cast

import httpx

from core.events.discord_dm import API_BASE, _auth_headers, bot_disabled, bot_token

if TYPE_CHECKING:
    from datetime import datetime

    from django.contrib.auth.models import User

    from membership.models import CommunityEvent

logger = logging.getLogger(__name__)

# The one-and-only default venue for an event whose ``location`` is blank — Discord's
# EXTERNAL entity requires a 1–100 char ``entity_metadata.location`` (a blank 400s).
DEFAULT_LOCATION = "Past Lives Makerspace"

_TIMEOUT_SECONDS = 5.0
_RATE_LIMIT_MAX_WAIT_SECONDS = 15.0
_RATE_LIMIT_MAX_ATTEMPTS = 3  # the initial send + up to 2 retries
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
# Ceiling on the photo we will read back and send as a cover image. Uploads are already
# capped (``MAX_UPLOAD_IMAGE_BYTES``) and downscaled to the hero long edge on save, so a file
# above this is an old or odd row rather than the norm; it gets no cover instead of a slow
# push. Discord takes the cover as base64, which inflates the payload by about a third.
_COVER_IMAGE_MAX_BYTES = 6 * 1024 * 1024


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

    def insert_event(
        self, server_id: str, body: dict[str, Any], *, retry_on_rate_limit: bool = False
    ) -> dict[str, Any]:
        """Create a Scheduled Event; return the event resource (with ``id``)."""
        return self._execute(
            "POST", f"/guilds/{server_id}/scheduled-events", json=body, retry_on_rate_limit=retry_on_rate_limit
        )

    def update_event(
        self, server_id: str, event_id: str, body: dict[str, Any], *, retry_on_rate_limit: bool = False
    ) -> dict[str, Any]:
        """Patch an existing Scheduled Event; return the updated event resource."""
        return self._execute(
            "PATCH",
            f"/guilds/{server_id}/scheduled-events/{event_id}",
            json=body,
            retry_on_rate_limit=retry_on_rate_limit,
        )

    def list_interested_user_ids(self, server_id: str, event_id: str) -> "list[str]":
        """Every Discord user id marked Interested on a Scheduled Event, paginated.

        Walks ``GET /guilds/{id}/scheduled-events/{id}/users`` in pages of 100 (Discord's
        cap) using the ``after`` cursor until a short page arrives. Raises
        :class:`DiscordEventsError` like every other call — the sync skips that event and
        moves on.
        """
        ids: list[str] = []
        after = ""
        while True:
            path = f"/guilds/{server_id}/scheduled-events/{event_id}/users?limit=100&with_member=false"
            if after:
                path += f"&after={after}"
            # This endpoint returns a JSON array; _execute is annotated for the dict-shaped
            # calls, so narrow the actual shape here.
            page = cast("list[dict[str, Any]]", self._execute("GET", path))
            ids.extend(row["user"]["id"] for row in page)
            if len(page) < 100:
                return ids
            after = ids[-1]

    def delete_event(self, server_id: str, event_id: str, *, retry_on_rate_limit: bool = False) -> None:
        """Delete a Scheduled Event. Wraps API errors (caller catches one type)."""
        self._execute(
            "DELETE", f"/guilds/{server_id}/scheduled-events/{event_id}", retry_on_rate_limit=retry_on_rate_limit
        )

    @staticmethod
    def _execute(
        method: str, path: str, *, json: dict[str, Any] | None = None, retry_on_rate_limit: bool = False
    ) -> dict[str, Any]:
        """Run a bot-authed REST call, translating any failure into :class:`DiscordEventsError`
        so callers catch one exception type (mirrors ``GoogleCalendarClient._execute``).

        Both a transport failure (``httpx.HTTPError``) and a non-2xx status become a
        ``DiscordEventsError``; a 2xx with an empty body (a ``DELETE`` 204) returns ``{}``.
        With ``retry_on_rate_limit`` a 429 is retried up to ``_RATE_LIMIT_MAX_ATTEMPTS - 1``
        more times, each after Discord's ``Retry-After`` — the scheduled-events bucket is
        tiny, so the daily mirror's burst of ~a-dozen calls reliably trips it mid-run, at
        times on consecutive calls; waiting the advertised second or two each time clears it.
        Only batch paths opt in: an interactive FOG save should fail fast into the retry
        cron, not hang the request sleeping. A 429 without the flag, or with no usable or
        too-long ``Retry-After`` on any attempt, raises immediately.
        """
        # A blank token (unset, or ENVIRONMENT=staging) never builds a request: callers
        # already check ``enabled``, and this keeps a client built any other way honest.
        if bot_disabled("scheduled events call"):
            raise DiscordEventsError("Discord bot token is blank; nothing was sent.")
        response = DiscordScheduledEventsClient._send(method, path, json=json)
        attempts = 1
        while response.status_code == 429 and retry_on_rate_limit and attempts < _RATE_LIMIT_MAX_ATTEMPTS:
            retry_after = _retry_after_seconds(response)
            if retry_after is None or retry_after > _RATE_LIMIT_MAX_WAIT_SECONDS:
                break
            time.sleep(retry_after)
            response = DiscordScheduledEventsClient._send(method, path, json=json)
            attempts += 1
        if not response.is_success:
            raise DiscordEventsError(f"Discord API {response.status_code}: {response.text[:300]}")
        return response.json() if response.content else {}

    @staticmethod
    def _send(method: str, path: str, *, json: dict[str, Any] | None = None) -> httpx.Response:
        """One raw REST call; only transport failures raise (as :class:`DiscordEventsError`)."""
        try:
            return httpx.request(
                method,
                f"{API_BASE}{path}",
                json=json,
                headers=_auth_headers(),
                timeout=_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise DiscordEventsError(str(exc)) from exc


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Seconds Discord asks us to wait on a 429, or ``None`` when absent/unparseable."""
    header = response.headers.get("Retry-After")
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _build_description(event: CommunityEvent) -> str:
    """The Discord event description: a "Join online" line first (when set) so it's visible
    above the fold, then the event's own description (if any), a blank line, then the
    absolute public URL — so the entry is actionable, not a dead title."""
    parts: list[str] = []
    if event.video_url:
        parts.append(f"Join online: {event.video_url}")
    if event.description:
        parts.append(event.description)
    parts.append(event.public_url)
    return "\n\n".join(parts)


def _recurrence_rule_for(event: CommunityEvent) -> dict[str, Any] | None:
    """Map a :class:`CommunityEvent` recurrence to a Discord ``recurrence_rule`` dict.

    Returns a dict for the cadences Discord natively expresses (weekly, and monthly-by-weekday
    when the start sits on the same calendar day in UTC as in Portland) and ``None`` for a
    one-off event and for the cadences it cannot express (every-2/3/6-months, twice-a-month,
    yearly-by-weekday, and a monthly evening series that crosses the UTC date line) — a
    ``None`` routes the caller to the single-next-occurrence fallback (§5.3). Weekday encoding
    is ``0=Monday … 6=Sunday`` (Python ``weekday()`` == Discord's convention). See the module
    docstring: these limits are build-time-verify.

    Both halves of the rule are computed from the **UTC** start, never local time: Discord
    expands the rule on the UTC calendar from the ``scheduled_start_time`` it is sent (dateutil
    rrule semantics, reproduced in the spec's ``_discord_series`` and checked against a live
    series 2026-10-09), and a Portland evening event from 5 PM PDT (4 PM PST) onward crosses
    the UTC date line, so its UTC weekday is one day later than its local weekday. Sending the
    local weekday made Discord snap the whole series a day early (a Thursday 5–8 PM meeting
    displayed as Wednesday 5–8 PM, 2026-07-28).

    A **monthly** series that crosses the date line has no rule at all. Discord counts the nth
    weekday on the UTC calendar, and "the nth Saturday in UTC" is not "the nth Friday in
    Portland": the first Friday of August 2026 was the 7th, so its UTC instant was the *second*
    Saturday, and the rule ``{"n": 2, "day": 5}`` put the First Friday Art Walk on the second
    Friday of every later month (the live bug, 2026-10-09). Keeping the local ordinal with the
    UTC weekday fails the other way, in every month that starts on a Saturday (July 31 for
    August 2026, April 30 for May 2027). So such a series is unmappable and takes the fallback,
    whose dates come from FOG's own local-calendar
    :meth:`~membership.models.CommunityEvent.occurrences_in`.
    """
    from datetime import UTC

    from django.utils import timezone

    from membership.models import CommunityEvent as CE

    if event.recurrence == CE.Recurrence.NONE:
        return None
    utc_start = event.starts_at.astimezone(UTC)
    weekday = utc_start.weekday()
    if event.recurrence == CE.Recurrence.WEEKLY:
        return {"frequency": _FREQUENCY_WEEKLY, "interval": 1, "by_weekday": [weekday]}
    if event.recurrence == CE.Recurrence.MONTHLY:
        if utc_start.date() != timezone.localdate(event.starts_at):
            return None
        return {
            "frequency": _FREQUENCY_MONTHLY,
            "interval": 1,
            "by_n_weekday": [{"n": (utc_start.day - 1) // 7 + 1, "day": weekday}],
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


def _cover_image_data_uri(event: CommunityEvent) -> str:
    """The event's photo as a ``data:`` URI for the Scheduled Event cover, or ``""``.

    Discord takes a cover image as base64 bytes in the body, not as a URL, so unlike the
    #calendar card (which hands Discord a link and lets it fetch) this has to read the object
    back out of storage while the push is in flight. That makes it the one part of a push
    that depends on the bucket answering, so **every** failure returns ``""`` and is logged:
    a missing object, an unreadable one, a bucket that is slow or down, or a file above
    :data:`_COVER_IMAGE_MAX_BYTES`. A picture must never be the reason a calendar sync fails.
    """
    import base64
    import mimetypes

    if not event.photo:
        return ""
    try:
        size = event.photo.size
        if size > _COVER_IMAGE_MAX_BYTES:
            logger.info("Discord cover skipped for event %s: %d bytes is over the cap", event.pk, size)
            return ""
        with event.photo.open("rb") as handle:
            raw = handle.read()
    except Exception:  # storage is a network call: any failure degrades to no cover
        logger.warning("Discord cover skipped for event %s: the photo could not be read", event.pk, exc_info=True)
        return ""
    mime = mimetypes.guess_type(event.photo.name or "")[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _build_scheduled_event_body(event: CommunityEvent, *, occurrence: datetime | None = None) -> dict[str, Any]:
    """Build the Discord Scheduled Event payload for a :class:`CommunityEvent`.

    All FOG events are off-Discord (physical), so they are ``entity_type = 3`` (EXTERNAL),
    which *requires* ``scheduled_end_time`` + ``entity_metadata.location`` (a blank location
    falls back to the video link when set, then :data:`DEFAULT_LOCATION`) — a purely-online
    event should not read as happening at the makerspace, while a physical location always
    wins (the join link still shows in the description). When ``occurrence`` is given (the
    unmappable fallback, §5.3) the event is pushed as a single instance at that start with no
    ``recurrence_rule``; otherwise a mappable cadence carries its ``recurrence_rule``.

    An event with a photo also carries it as the cover ``image``, which is what Discord shows
    as the banner in its Events tab. That read can fail where nothing else here can (it goes
    to object storage), so :func:`_cover_image_data_uri` swallows its own failures and the
    key is simply absent — the event still syncs, without a banner.
    """
    start = occurrence if occurrence is not None else event.starts_at
    end = start + (event.ends_at - event.starts_at)
    location = event.location or event.video_url or DEFAULT_LOCATION
    body: dict[str, Any] = {
        "name": event.title[:_NAME_MAX],
        "privacy_level": _PRIVACY_GUILD_ONLY,
        "entity_type": _ENTITY_TYPE_EXTERNAL,
        "scheduled_start_time": start.isoformat(),
        "scheduled_end_time": end.isoformat(),
        "entity_metadata": {"location": location[:_LOCATION_MAX]},
        "description": _build_description(event)[:_DESCRIPTION_MAX],
    }
    cover = _cover_image_data_uri(event)
    if cover:
        body["image"] = cover
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
            if event.discord_pushed_occurrence is None:
                # A native series became unmappable (an edit moved a monthly event past
                # 5 PM, or the mapping tightened). Left alone, the old series keeps showing
                # its wrong dates beside the new single instance, so it goes first. Best
                # effort: a failed delete is a stale remote series, not a failed push.
                try:
                    client.delete_event(client.server_id, event.discord_event_id)
                except DiscordEventsError:
                    logger.warning(
                        "Discord series delete failed for event %s; leaving a stale remote series.", event.pk
                    )
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
