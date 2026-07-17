# Community Events → Discord Scheduled Events (+ a weekly Classes digest) — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-16
**Grilling update (2026-07-16, with Josh):** the four open product calls are resolved — see the re-stamped locked-decisions table. Most important: **the weekly Classes digest (§5.6/§6-C/§8 phase 4) is PAUSED — do not build it** — Josh doesn't want to step on marketing's toes; it needs a marketing conversation first. The Scheduled-Events sync (everything else) stands alone and is unaffected. Digest sections are retained below as the ready design for if/when marketing says yes.
**Surface:** Backend integration on the member-hub domain (`pastlives.app` / dev `pastlives.test:8000`) + the **Discord server** (where members see the native Events UI and the weekly digest post) + one Site Settings toggle. No FOG hub page beyond that toggle; no book-CMS surface.
**Related:**
- `docs/superpowers/plans/2026-07-03-google-calendar-event-sync.md` — the FOG→Google **push architecture this mirrors exactly** (`push_community_event` / `remove_community_event` / `_build_event_body`, per-event sync bookkeeping, the `retry_calendar_pushes` self-gating retry) and its **§10 go-live-checklist format**.
- `docs/superpowers/plans/2026-07-13-guild-studio-hours-and-meetings.md` — established `EventType.STUDIO_HOURS` + `Recurrence.WEEKLY` (both now shipped, v0.22.8); this spec inherits the studio-hours-are-ambient decision.
- `docs/superpowers/plans/2026-07-12-event-announcement-scheduling-and-reminders.md` — the `publish()` / `schedule_or_go_live()` / `publish_scheduled()` choke points a Discord push hangs off (all shipped).
- `docs/superpowers/plans/2026-07-13-discord-interactions-foundation.md` + `2026-07-13-discord-member-commands.md` — the built `/whats-on` pull command this positions its push-based digest against, and the go-live-checklist shape.

---

## 1. Summary

Community events (the site-wide `CommunityEvent`s and each guild's meetings/events) already live on the FOG Community Calendar, sync one-way to Google Calendar, and get a one-shot Discord **channel announcement** when they publish. They do **not** appear in Discord's native **Events** UI — the thing members actually browse and RSVP to inside Discord. This feature makes every published community event a **Discord Guild Scheduled Event**, mirroring the Google push exactly: one-way (app → Discord), best-effort (never blocks a FOG save), gated behind an admin toggle + the bot token + the server id, with a self-gating retry job.

Classes are **deliberately excluded** from Discord Scheduled Events (a hard exclusion — a bookable class is not an RSVP-in-Discord event, and dozens of them would bury the real community events). Instead, classes get an elegant **push-based discovery** surface: a **weekly "Classes This Week" digest** — one embed posted to a Discord channel every Monday listing the week's bookable sessions with clickable book-surface links. This complements the two class surfaces that already exist (the per-class `class_published` channel announcement, and the pull-based `/whats-on` slash command) without duplicating either.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| **Community events → Discord Scheduled Events** | **Yes, one-way app→Discord**, mirroring the Google push architecture (§2). New `discord_event_id` + Discord sync bookkeeping on `CommunityEvent`; push on publish/edit, delete propagation; best-effort/never-raises; master gates (admin toggle + `discord_server_id` + `DISCORD_BOT_TOKEN`); a self-gating retry job. |
| **Classes → Discord Scheduled Events** | **Never.** Hard exclusion. A class/session is not an RSVP event, and the Events UI is a scarce, prominent surface. |
| **Classes' "nice and elegant" Discord presence** | **PAUSED (grilling 2026-07-16)** — the weekly digest is deferred pending a marketing conversation ("I don't want to step on marketing toes"). Classes keep their existing Discord presence (per-publish `class_published` alert + pull-based `/whats-on`). The digest design below (§5.6/§6-C) is retained for later. |
| **Studio hours → Discord Scheduled Events** | **No — RATIFIED by Josh (grilling 2026-07-16)**, knowingly reversing the in-app "everywhere" call for this scarcer surface. `EventType.STUDIO_HOURS` rows are excluded — they are ambient standing hours (they already never announce; `announce()` no-ops for them at `membership/models.py:3623`). A dozen guilds' weekly studio hours would flood Discord's Events list; this is the one place the earlier "surface studio hours everywhere" call is reversed, because Discord's Events UI is scarcer than an in-app card. |
| **Member vs public calendar** | **Push all published events regardless of `google_calendar_target`** — CONFIRMED by Josh (grilling 2026-07-16) as an informed trade-off: a Scheduled Event is GUILD_ONLY (visible to everyone in the server, incl. non-member guests), and the server effectively is the member community. |
| **Recurrence** | Map the cadences Discord's `recurrence_rule` supports (weekly, monthly-by-weekday); for the ones it does **not** (semi-monthly, every-2/3/6-months, yearly-by-weekday) fall back to pushing the **next concrete occurrence** as a single event and **rolling it forward nightly** (§5.3). |
| **Sync direction** | **One-way forever.** A Discord-side edit of a scheduled event is never pulled back into FOG (matching the Google decision). |
| **Process model** | **No new process type / cron.** Push runs inline on the web request (like the Google push); the retry + roll-forward + digest ride the existing 15-min `run_scheduled_tasks` dispatcher and its registry (`core/scheduled_jobs.py`). |
| **Echo/dedup** | A Discord **Scheduled Event** and the existing **channel announcement embed** for the same event are two *different surfaces*, not a duplicate bug — recorded in §7 so nobody "fixes" it. |

---

## 2. What already exists (reuse, don't reinvent)

This is assembly. The Google push is the exact template; every Discord primitive already has a home from the DM channel + webhook broadcast + interactions work.

| Need | Existing thing | Location |
|---|---|---|
| **The whole one-way push service to mirror** (build client, check `.enabled`, never raise, record sync state) | `push_community_event` / `remove_community_event` / `_build_event_body` / `_mark` | `core/integrations/google_calendar.py:197`, `:235`, `:161`, `:185` |
| Per-event sync bookkeeping fields (the parallel to copy) | `google_event_id` / `google_calendar_id` / `google_ical_uid` / `sync_state` / `sync_error` / `synced_at` | `membership/models.py:3319`, `:3325`, `:3331`, `:3340`, `:3346`, `:3351` |
| `SyncState` choices (reuse verbatim for Discord) | `IDLE` / `PENDING` / `SYNCED` / `FAILED` | `membership/models.py:3201-3205` |
| The publish choke point that fires the push | `CommunityEvent.publish()` (announce → `PENDING` → `push_to_google`) | `membership/models.py:3645-3657` |
| Model delegators to mirror | `push_to_google()` / `remove_from_google()` | `membership/models.py:3691`, `:3713` |
| Every edit/delete call site to wire a Discord call beside | `push_to_google()` / `remove_from_google()` in the event + studio-hours views | `hub/views.py:899`, `:2777`, `:2843`, `:3098` (push); `:896`, `:2812`, `:2870` (remove) |
| The self-gating retry command to sibling | `retry_calendar_pushes` + `CommunityEvent.objects.needs_push()` | `core/management/commands/retry_calendar_pushes.py:18-37`; queryset `membership/models.py:3151-3155` |
| Recurrence expansion (for the roll-forward next-occurrence) | `CommunityEvent.occurrences_in(frm, to)` | `membership/models.py:3410` (WEEKLY branch `:3422`) |
| The RRULE the event already computes (informs the recurrence map) | `CommunityEvent.ical_rrule()` | `membership/models.py:3463` (WEEKLY `:3475`, monthly/semi/yearly `:3479-3488`) |
| Local weekday + nth-weekday-of-month for the recurrence map | `_occurrence_ordinal()` + `local_start.weekday()` | `membership/models.py:3405-3408`, `:3474` |
| The event's absolute public page (for the Discord description link + digest lines) | `CommunityEvent.public_url` / `absolute_url` | `membership/models.py:3572`, `:3594` |
| Local-time "when" string | `CommunityEvent.when_display` | `membership/models.py:3539` |
| Discord bot REST auth (reuse verbatim) | `bot_token()` / `_auth_headers()` / `_API_BASE` (`https://discord.com/api/v10`) | `core/events/discord_dm.py:38-40`, `:43-45`, `:34` |
| The Discord server id (guild-scoped) + "blank disables" idiom | `SiteConfiguration.discord_server_id` | `core/models.py:211` |
| The Google admin toggle to mirror | `SiteConfiguration.google_calendar_sync_enabled` (`BooleanField(default=False)`) | `core/models.py:290` |
| **Discord channel broadcast (for the digest)** — one embed, best-effort, never raises | `post_embed(webhook_url, message)` + `global_webhook()` + `webhook_for_event(key)` | `core/events/discord.py:162`, `:50`, `:79` |
| The rendered-message dataclass a broadcast posts | `core.events.channels.Message` (`title`, `body`, `url`) | `core/events/channels.py:57-77` |
| **The emit broadcast-once ledger (digest idempotency)** — posts Discord ONCE per `(event.key, channel, period)`, independent of recipients | `emit(..., period=…)` → `_broadcast_fan_out` → `_record_broadcast(key, channel, period)` | `core/events/emit.py:44`, `:199`, `:245` |
| Registry + curated copy to add the digest key (mirror `class_published`) | `class_published` `EventType` (Discord broadcast, EMAIL-copy fallback) | `core/events/registry.py:353-361`; copy `core/events/copy.py:215-237`; `copy_for` fallback `copy.py:63-77` |
| The scheduled-job registry (append two rows; DAILY self-gates to UTC 13:00) | `SCHEDULED_JOBS` + `Cadence` + `ScheduledJob` | `core/scheduled_jobs.py:54-161`, `:28-31`, `:39-51`; dispatcher `run_scheduled_tasks.py:38-54` |
| **The exact "classes this week" query the digest needs** | `ClassSession.objects.upcoming_public()` + `_class_sessions(to)` helper | `classes/models.py:1144-1157`; helper `membership/discord_commands.py:55-62` |
| Class book-surface absolute link + title | `ClassOffering.public_url` (via `BOOK_BASE_URL`) / `ClassOffering.title` | `classes/models.py:340-347`, `:209`; setting `plfog/settings.py:492` |
| The scheduler-source reminder pattern (for the digest window math) | `class_reminder_occurrences(now)` / `build_class_reminder_occurrence` | `classes/tasks.py:20-46`; `classes/emails.py:610-668` |
| Absolute-URL helper | `_absolute_url()` + `MEMBER_BASE_URL` | `membership/orientations.py:32-35`; `plfog/settings.py:74` |
| Site Settings boolean control (theme-correct toggle) | `components/toggle.html` / `form_field.html` (auto-toggle) | `templates/components/` |

### Genuine gaps to close (kept minimal)

1. `core/integrations/discord_events.py` — the push/remove service + body builder + recurrence mapper (mirrors `google_calendar.py`).
2. `CommunityEvent` Discord bookkeeping fields (`discord_event_id`, `discord_sync_state`, `discord_sync_error`, `discord_synced_at`, `discord_pushed_occurrence`) + `push_to_discord()` / `remove_from_discord()` delegators + `needs_discord_push()` / `needs_discord_rollforward()` querysets (§4).
3. `SiteConfiguration.discord_events_sync_enabled` — the new admin toggle (mirrors `google_calendar_sync_enabled`).
4. `retry_discord_event_pushes` management command (sibling of `retry_calendar_pushes`; also does roll-forward) + a `ScheduledJob` row.
5. `backfill_discord_events` one-time command (push existing future events; `--dry-run`).
6. `classes.weekly_digest` registry event key + copy + a `send_class_digest` command + a `ScheduledJob` row.
7. Migrations (additive; reverse = auto `RemoveField` + restore `choices`).

**No** new dependency, **no** new env var (reuses `DISCORD_BOT_TOKEN`), **no** new process type, **no** `render.yaml` change.

---

## 3. Where the code lives

Mirrors the Google integration's layering: the push service lives in `core/integrations/` beside `google_calendar.py`; the model delegates via a lazy import (keeping `membership → core` clean, exactly as `push_to_google` does at `membership/models.py:3698`). The digest lives in `core.events` beside the other broadcast keys.

```
core/integrations/discord_events.py            NEW  DiscordScheduledEventsClient.from_settings() (+ .enabled),
                                                    push_community_event() / remove_community_event(),
                                                    _build_scheduled_event_body(), _recurrence_rule_for(event),
                                                    _mark() — httpx, best-effort, NEVER raises (mirror google_calendar.py)
core/models.py                                  ~   SiteConfiguration.discord_events_sync_enabled (BooleanField)
core/events/registry.py                         ~   + CLASS_WEEKLY_DIGEST = "classes.weekly_digest" (Discord broadcast only)
core/events/copy.py                             ~   + curated copy for classes.weekly_digest (IN_APP + EMAIL; Discord
                                                    inherits EMAIL via copy_for) — body is {{ digest_body }} + calendar link
core/management/commands/
    retry_discord_event_pushes.py               NEW  self-gating: re-push PENDING/FAILED + roll forward unmappable-cadence
                                                    single events (sibling of retry_calendar_pushes)
    backfill_discord_events.py                  NEW  one-time: push all future published non-studio-hours events (--dry-run)
    send_class_digest.py                        NEW  Monday-gated weekly digest; emit("classes.weekly_digest", period=<week>)
core/scheduled_jobs.py                          ~   + ScheduledJob(retry_discord_event_pushes, ALWAYS)
                                                    + ScheduledJob(send_class_digest, DAILY)   ← self-gates to Monday
membership/models.py                            ~   CommunityEvent: +5 discord_* fields, +push_to_discord()/remove_from_discord(),
                                                    publish() also pushes Discord; CommunityEventQuerySet +needs_discord_push()
                                                    /needs_discord_rollforward()
membership/migrations/00XX_communityevent_discord_sync.py   NEW  AddField ×5 (+ any AlterField for choices); auto-reverse
core/migrations/00XX_siteconfig_discord_events_sync.py       NEW  AddField (auto-reverse)
hub/views.py                                    ~   add push_to_discord()/remove_from_discord() beside each push_to_google/
                                                    remove_from_google (the edit + delete branches; publish() covers creates)
templates/…/site settings integrations         ~   one toggle for discord_events_sync_enabled (mirror the Google toggle)

tests/core/integrations/discord_events_spec.py  NEW
tests/membership/community_event_discord_spec.py NEW
tests/core/management/retry_discord_event_pushes_spec.py / backfill_discord_events_spec.py / send_class_digest_spec.py  NEW
tests/core/events/class_weekly_digest_spec.py   NEW
plfog/version.py                                 ~   VERSION bump + one grouped CHANGELOG entry (final phase)
```

Home apps: **core** (push service, digest, toggle, commands) and **membership** (the `CommunityEvent` Discord fields + delegators). Both already in coverage/mypy scope.

---

## 4. Data model

### 4.1 `CommunityEvent` — Discord sync bookkeeping (mirror the Google block at `:3311-3351`)

| Field | Definition | Note |
|---|---|---|
| `discord_event_id` | `CharField(max_length=64, blank=True, default="")` | The Discord Guild Scheduled Event id (snowflake) returned on create. Blank until pushed. Presence ⇒ PATCH on next push; absence ⇒ POST. |
| `discord_sync_state` | `CharField(max_length=12, choices=SyncState.choices, default=SyncState.IDLE)` | Reuses the existing `SyncState` enum verbatim. `IDLE` (nothing to push) → `PENDING` (published, awaiting push) → `SYNCED` / `FAILED`. Independent of `sync_state` (Google), so one integration can be off/failed while the other is fine. |
| `discord_sync_error` | `TextField(blank=True, default="")` | Why the last Discord push failed / is pending ("Discord Events sync is off", "No Discord server linked", or the API error). Truncated to 500. |
| `discord_synced_at` | `DateTimeField(null=True, blank=True)` | When this event last synced to Discord. |
| `discord_pushed_occurrence` | `DateTimeField(null=True, blank=True)` | For an **unmappable-cadence** event pushed as a *single* Scheduled Event (§5.3): the start of the occurrence currently live in Discord, so the nightly roll-forward knows when it has passed. `NULL` for one-off events and for natively-recurring (mappable) events. |

`help_text` on each (per CLAUDE.md), e.g. `discord_event_id`: *"The Discord Scheduled Event id returned when FOG pushed this event to the server's Events. Blank until pushed."*

> **Flagged (new fields = new convention, per "don't invent unilaterally"):** five `discord_*` bookkeeping fields on `CommunityEvent`. They are the exact structural parallel of the six `google_*` fields already there; `discord_pushed_occurrence` is the one net-new shape and exists only for the roll-forward — an alternative that avoids it (GET the live Discord event and compare its start) is in §10.

### 4.2 `CommunityEventQuerySet` — new methods (beside `needs_push()` at `:3151`)

```python
def needs_discord_push(self) -> CommunityEventQuerySet:
    """PUBLISHED, non-studio-hours events whose Discord sync is pending/failed (the retry set)."""
    return self.published().exclude(event_type=CommunityEvent.EventType.STUDIO_HOURS).filter(
        discord_sync_state__in=[CommunityEvent.SyncState.PENDING, CommunityEvent.SyncState.FAILED]
    )

def needs_discord_rollforward(self, now: datetime) -> CommunityEventQuerySet:
    """SYNCED single-occurrence events (unmappable cadence) whose pushed occurrence has passed."""
    return self.published().filter(
        discord_sync_state=CommunityEvent.SyncState.SYNCED,
        discord_pushed_occurrence__isnull=False,
        discord_pushed_occurrence__lt=now,
    )
```

`needs_discord_push()` excludes `STUDIO_HOURS` at the query level so the exclusion is enforced in one place, not just at the push call.

### 4.3 `SiteConfiguration.discord_events_sync_enabled` (`core/models.py`, beside `:290`)

`BooleanField(default=False, verbose_name="Publish events to Discord", help_text="When on (and the Discord bot is configured with Manage Events), publishing/editing/deleting a community event creates/updates/removes it in the Discord server's Events. Studio hours and classes are never pushed.")` — the exact mirror of `google_calendar_sync_enabled`.

### 4.4 Migrations

Two additive migrations (one per app), each `AddField` only (auto-reversible `RemoveField`; no data migration — existing rows default to `discord_sync_state=IDLE`, blank ids). Choice reuse (`SyncState`) means no new enum column shape. `ruff format` + `git add` the migrations together (per the migrations-need-ruff-format note).

---

## 5. Business logic (fat models + the push service)

### 5.1 The push service — `core/integrations/discord_events.py` (mirror `google_calendar.py`)

A `DiscordScheduledEventsClient.from_settings()` that returns a **disabled** client (`.enabled is False`) when the master toggle is off, `DISCORD_BOT_TOKEN` is blank, or `SiteConfiguration.discord_server_id` is blank — never raises, so a mis-set config degrades to "sync off," not a 500. HTTP via `httpx`, auth via the reused `_auth_headers()` / `_API_BASE`.

- `insert_event(server_id, body) -> dict` → `POST /guilds/{server_id}/scheduled-events` (returns the event resource with `id`).
- `update_event(server_id, event_id, body) -> dict` → `PATCH /guilds/{server_id}/scheduled-events/{event_id}`.
- `delete_event(server_id, event_id) -> None` → `DELETE /guilds/{server_id}/scheduled-events/{event_id}`.
- All wrap failures into one `DiscordEventsError` so callers catch one type (exactly `GoogleCalendarClient._execute` at `google_calendar.py:103-121`).

**`push_community_event(event, *, actor=None)`** — the fat-model-facing entry (never raises; records `PENDING`/`FAILED`; does not save — the model delegator persists):

```
client = DiscordScheduledEventsClient.from_settings()
config = SiteConfiguration.load()
if event.event_type == STUDIO_HOURS:            # ambient hours never become Scheduled Events
    _mark(event, IDLE, "Studio hours are not pushed to Discord."); return
if not (client.enabled and config.discord_events_sync_enabled):
    _mark(event, PENDING, "Discord Events sync is off."); return
body = _build_scheduled_event_body(event)
try:
    g = client.update_event(server_id, event.discord_event_id, body) if event.discord_event_id \
        else client.insert_event(server_id, body)
    event.discord_event_id = g["id"]
    event.discord_pushed_occurrence = <the single occurrence pushed, or None>   # §5.3
    _mark(event, SYNCED, "")
except DiscordEventsError as exc:
    _mark(event, FAILED, str(exc)[:500])
```

**`remove_community_event(event)`** — best-effort `DELETE` **before** the FOG row is deleted (needs the stored `discord_event_id`); a stale remote event on failure is a loggable residue, exactly like `remove_from_google` (`google_calendar.py:235-247`).

### 5.2 `_build_scheduled_event_body(event)` — the Discord payload

Discord Guild Scheduled Events with an off-Discord (physical) location are **`entity_type = 3` (EXTERNAL)**, which *requires* `scheduled_end_time` and `entity_metadata.location`. All events here are external.

| Discord field | Value |
|---|---|
| `name` | `event.title` (truncate to 100) |
| `privacy_level` | `2` (GUILD_ONLY — the only permitted value) |
| `entity_type` | `3` (EXTERNAL) |
| `scheduled_start_time` | `event.starts_at.isoformat()` (aware) — for a recurrence, the series anchor; for the unmappable-fallback, the next occurrence's start (§5.3) |
| `scheduled_end_time` | `event.ends_at.isoformat()` (required for EXTERNAL) — fallback shifts it by the same delta |
| `entity_metadata` | `{"location": event.location or DEFAULT_LOCATION}` (1–100 chars; `DEFAULT_LOCATION = "Past Lives Makerspace"` — required, so a blank `location` must fall back) |
| `description` | `event.description` (if any) + a blank line + `event.public_url` (truncate to 1000). The URL makes it actionable — "make it act, not just inform." |
| `recurrence_rule` | `_recurrence_rule_for(event)` or **omitted** when `None` (single event / unmappable-fallback) |

Attribution ("Added by … via FOG") is optional; the `public_url` click-through is the load-bearing part.

### 5.3 `_recurrence_rule_for(event)` — the honest recurrence map

Discord's `recurrence_rule` supports only a subset of plfog's cadences. Return a `recurrence_rule` dict for the mappable ones, `None` (→ single-event fallback + roll-forward) for the rest. **All of the specific Discord limits below must be re-verified at build time** — they are from documentation knowledge as of early 2026 and Discord has iterated on this endpoint.

| plfog `Recurrence` | Discord `recurrence_rule` | Mappable? |
|---|---|---|
| `NONE` | *(omitted)* — a single event | ✅ one-off |
| `WEEKLY` | `frequency=2` (WEEKLY), `interval=1`, `by_weekday=[wd]` (single weekday) | ✅ |
| `MONTHLY` | `frequency=1` (MONTHLY), `interval=1`, `by_n_weekday=[{"n": ordinal, "day": wd}]` (single nth-weekday) | ✅ (verify) |
| `EVERY_2_MONTHS` / `EVERY_3_MONTHS` / `EVERY_6_MONTHS` | would need `frequency=1` + `interval>1` **with** `by_n_weekday` — Discord requires `interval=1` for monthly-by-weekday → **unmappable** | ❌ fallback |
| `SEMI_MONTHLY` | needs **two** `by_n_weekday` entries (nth + nth-plus-2); Discord allows only one → **unmappable** | ❌ fallback |
| `YEARLY` | plfog yearly = nth-weekday-of-a-month; Discord `frequency=0` supports only `by_month`+`by_month_day` (a fixed calendar date), not nth-weekday → **unmappable** | ❌ fallback |

- **Weekday encoding:** Discord `by_weekday`/`by_n_weekday.day` use `0=Monday … 6=Sunday`, which matches Python `datetime.weekday()` and the model's `local_start.weekday()` (`:3474`) — a direct map. **Verify Discord's Monday=0 convention at build time.**
- **`ordinal`:** `_occurrence_ordinal()` (`:3405`) returns `1–4`, or `-1` for a 5th ("last"). Discord `n` is `1–5` — map `-1 → 5`.
- **`count`:** always `null` (Discord recurs indefinitely; plfog series have no fixed count).
- **Fallback mechanics:** for an unmappable recurring event, push the **next concrete occurrence** as a *single* event — `occ = event.occurrences_in(today, today + FALLBACK_HORIZON)`; take the first `>= now`; set `scheduled_start_time = occ`, `scheduled_end_time = occ + (ends_at − starts_at)`, no `recurrence_rule`, and record `discord_pushed_occurrence = occ`. If there is no occurrence in the horizon, mark `SYNCED` with a blank `discord_event_id` (nothing to show yet) — the nightly job will pick it up when one enters the window.

**Roll-forward (nightly, `needs_discord_rollforward`):** a single-occurrence event whose `discord_pushed_occurrence < now` has completed in Discord (a past external event auto-completes and drops off the active list — you cannot PATCH a completed event's time into the future). So the roll-forward **creates a fresh** Scheduled Event for the next occurrence, updates `discord_event_id` + `discord_pushed_occurrence`, and leaves the completed one to fall off naturally. (Mappable recurrences never enter this path — Discord shows their next instance natively.)

### 5.4 Model delegators + wiring the choke points (`membership/models.py`)

Add beside `push_to_google` (`:3691`) / `remove_from_google` (`:3713`):

```python
def push_to_discord(self, *, actor: User | None = None) -> None:
    """Push this event to the Discord server's Scheduled Events and persist the sync fields.
    Best-effort (records PENDING/FAILED, never raises). Self-gating no-op for studio hours /
    when Discord Events sync is off. Lazy-imports the service (membership → core layering)."""
    from core.integrations.discord_events import push_community_event
    push_community_event(self, actor=actor)
    self.save(update_fields=["discord_event_id", "discord_sync_state", "discord_sync_error",
                             "discord_synced_at", "discord_pushed_occurrence", "updated_at"])

def remove_from_discord(self) -> None:
    from core.integrations.discord_events import remove_community_event
    remove_community_event(self)
```

**`publish()`** (`:3645`) gains a Discord push alongside the Google one — the single choke point for all create/publish/approve paths:

```python
def publish(self, *, actor=None):
    self.announce(actor=actor)
    if self.sync_state == self.SyncState.IDLE:
        self.sync_state = self.SyncState.PENDING
        self.save(update_fields=["sync_state", "updated_at"])
    self.push_to_google(actor=actor)
    if self.discord_sync_state == self.SyncState.IDLE:          # NEW
        self.discord_sync_state = self.SyncState.PENDING
        self.save(update_fields=["discord_sync_state", "updated_at"])
    self.push_to_discord(actor=actor)                           # NEW (self-gates off / studio hours)
```

**Edit / delete views** (`hub/views.py`) — add the parallel Discord call beside each existing Google call. Because `push_to_discord()` self-gates (no-op for studio hours / when off), the additions are safe everywhere, including the studio-hours save at `:899` (where it must stay a no-op):

| Site | Existing Google call | Add beside it |
|---|---|---|
| Guild event edit, PUBLISHED branch | `push_to_google` `hub/views.py:2777` | `event.push_to_discord(actor=request.user)` |
| Site-wide event edit, PUBLISHED branch | `push_to_google` `:2843` | `event.push_to_discord(actor=request.user)` |
| `:3098` push | `push_to_google` `:3098` | `event.push_to_discord()` |
| Studio-hours save | `push_to_google` `:899` | `event.push_to_discord()` (no-op — studio hours) |
| Guild event delete | `remove_from_google` `:2812` | `event.remove_from_discord()` |
| Site-wide event delete | `remove_from_google` `:2870` | `event.remove_from_discord()` |
| Studio-hours delete | `remove_from_google` `:896` | `event.remove_from_discord()` (no-op) |

> `withdraw()` / `submit_for_review()` / `decline()` are all **pre-publish only** — `decline()` is reachable only from `PENDING`/`CHANGES_REQUESTED` (`membership/models.py:3858`) and its own docstring notes "it was never published — no Google/announce to unwind" (`:3852`). A declined/pending/withdrawn proposal therefore has no `discord_event_id` and needs **no Discord cleanup** — nothing to wire there, exactly as the Google path adds nothing to those methods.

### 5.5 The retry + roll-forward command — `retry_discord_event_pushes` (sibling of `retry_calendar_pushes`)

Same shape as `retry_calendar_pushes.py:18-37`: self-gates to a no-op when `DiscordScheduledEventsClient.from_settings().enabled` is False or `discord_events_sync_enabled` is off, so it is safe on every 15-min tick. Then:
1. `for event in CommunityEvent.objects.needs_discord_push().select_related("guild")[:_MAX_PER_RUN]: event.push_to_discord()` — retry PENDING/FAILED.
2. `for event in CommunityEvent.objects.needs_discord_rollforward(now)[:_MAX_PER_RUN]: event.push_to_discord()` — roll a completed single-occurrence event forward (its push recomputes the next occurrence).

Registered as a `ScheduledJob(key="retry_discord_event_pushes", cadence=Cadence.ALWAYS, …)` in `SCHEDULED_JOBS` — no `render.yaml` change (`run-scheduled-tasks` already runs every 15 min).

### 5.6 The weekly Classes digest — `send_class_digest` command

A **DAILY** `ScheduledJob` that **self-gates to Monday** internally (fires only when `timezone.localdate().weekday() == 0`), then emits once for the week. It reuses the exact "classes this week" query the `/whats-on` command already uses.

```
if timezone.localdate().weekday() != DIGEST_WEEKDAY:   # DIGEST_WEEKDAY = 0 (Monday)
    return
frm = timezone.localdate(); to = frm + timedelta(days=7)
sessions = ClassSession.objects.upcoming_public().filter(starts_at__date__lte=to).select_related("class_offering")
if not sessions:                                       # empty week → post nothing (no dead "no classes" spam)
    return
body = "\n".join(f"• [{s.class_offering.title}]({s.class_offering.public_url}) — {when(s.starts_at)}"
                 for s in sorted(sessions, key=lambda s: s.starts_at))
emit("classes.weekly_digest", target=None,
     context={"digest_body": body, "calendar_url": _absolute_url(reverse("hub_community_calendar"))},
     url=<book catalog url>, period=f"classes:digest:{frm.isocalendar().year}-W{frm.isocalendar().week:02d}")
```

**Why `emit()` and not a bare `post_embed`:** a **DAILY** job fires on *every* 15-min tick whose UTC hour is 13 (13:00, 13:15, 13:30, 13:45 — up to 4 runs). A bare `post_embed` would post the digest up to 4 times. Routing through `emit()` with a **weekly `period`** makes `_record_broadcast(event.key, DISCORD, period)` return `True` only once per ISO week (`emit.py:245`), so the embed posts exactly once — and `_broadcast_fan_out` posts the Discord channel **independent of recipients** (`emit.py:209-213`), so an empty in-app audience is fine. This is the exact broadcast-once mechanism `class_published` already uses.

- **Registry key** `classes.weekly_digest`: `recipient=ALL_ACTIVE_MEMBERS` (harmless — the event declares **only** the Discord channel, so no per-recipient in-app/email rows are created; only the broadcast fires), `channels=(_DISCORD_ON,)` (a Discord-broadcast-only event — the per-recipient fan-out only touches channels the event declares, so omitting IN_APP/EMAIL is how "no in-app noise" is expressed; `_DISCORD_ON` already exists at `registry.py:310`, no new shorthand needed), `category="Classes"`, `activity_kind=None`. Mirrors `class_published` (`registry.py:353-361`) minus the per-recipient channels.
- **Copy** (`copy.py`, mirror `class_published` `:215-237`): a single `EventCopy` with an `EMAIL` `ChannelCopy` (Discord inherits it via `copy_for` fallback `copy.py:63-77`) — subject `"📅 Classes at Past Lives this week"`, body `"{{ digest_body }}\n\nBrowse all classes: {{ calendar_url }}"`. The body is the command-assembled markdown list; the copy template just renders `{{ digest_body }}`.
- **Channel routing:** `webhook_for_event("classes.weekly_digest")` (`discord.py:79`) → defaults to `global_webhook()` (`DISCORD_NOTIFY_WEBHOOK_URL`, already set for `class_published`), and a later admin `DiscordWebhookRoute` can point it at a dedicated `#classes` channel with zero code change.

---

## 6. UI / UX — completeness checklist through the Discord lens

Like the interactions foundation spec, the "screens" here are mostly **Discord surfaces** — the dark/light-theme + mobile rows are replaced by *Discord rendering constraints*, since Discord renders the client. The one true FOG hub control is the Site Settings toggle, which must theme correctly.

### Screen A — Site Settings: "Publish events to Discord" toggle (the one FOG control)

- **Where:** the Site Settings Integrations/Discord area, immediately beside the existing **"Push events to Google Calendar"** toggle (`google_calendar_sync_enabled`) so the two integrations read as a pair.
- **Component:** `components/toggle.html` (or `form_field.html`, which auto-renders a boolean as a toggle) — **never a raw checkbox**. Bound to `SiteConfiguration.discord_events_sync_enabled`.
- **Save:** the existing Site Settings form's Save button (full-page POST → Django `messages.success`) — no new button; this is one field on an existing settings form.
- **Hint copy:** *"When on, publishing or editing a community event also creates it in the Discord server's Events. Requires the bot's Manage Events permission and a Discord server id set above. Studio hours and classes are never published to Discord."*
- **States:** default **off** (safe until go-live); on/off is the whole interaction. A save while the bot lacks Manage Events does not error here — pushes simply record `FAILED` and the retry job re-tries (best-effort). *Empty/loading/error* are the settings form's existing behavior.
- **Dark + light:** the toggle routes through the component's theme tokens — no inline `background`/`color`. **Verify both themes** beside the Google toggle.
- **Mobile:** a single full-width toggle row on the existing responsive settings form — reflows, real tap target.

### Screen B — the Discord Scheduled Event (what a member sees in Discord's Events UI)

- **Rendered by Discord** from `_build_scheduled_event_body` (§5.2): the event **name**, its **start/end time** (Discord localizes to each viewer), the **location** (`entity_metadata.location`), and a **description** ending in the clickable `public_url` back to the FOG event page (the "make it act" link). A recurring mappable event shows Discord's native "Repeats weekly/monthly" affordance; an unmappable-cadence event shows as its next single occurrence.
- **No dead end:** the description's `public_url` is the way back to FOG (full details, add-to-calendar, QR). Never a bare title.
- **Empty / not-provisioned state:** when the toggle is off or the bot lacks Manage Events, **nothing** appears in Discord Events (the push records `PENDING`/`FAILED`) — the FOG calendar and Google sync are unaffected. There is no half-rendered event.
- **Discord rendering constraints (replaces dark/light + mobile):** `name` ≤ 100, `description` ≤ 1000, `entity_metadata.location` 1–100 (so a blank FOG `location` **must** fall back to `DEFAULT_LOCATION`, else Discord 400s the create). `privacy_level` must be `2`. `scheduled_end_time` + `entity_metadata` are **required** for EXTERNAL — omitting either is a hard API error, not a soft degrade.

### Screen C — the weekly "Classes This Week" digest (Discord channel embed)

- **Rendered** as one embed to the routed channel: a bold title "📅 Classes at Past Lives this week," then one linked, dated line per session (`• [Title](book url) — Mon, Jul 20 · 6:00 PM`), then "Browse all classes." Each title links to its book-surface page (`BOOK_BASE_URL`) — actionable, not a dead list.
- **Empty state:** a week with **no** `upcoming_public()` sessions posts **nothing** (the command returns before emit) — no "no classes this week" spam in the channel. (Deliberate: a digest that says "nothing" every quiet week trains members to ignore it.)
- **Success/idempotency:** exactly one post per ISO week via the `period` dedupe (§5.6) even though the DAILY job fires up to 4× in the 13:00-UTC hour.
- **Error state:** a Discord outage is best-effort — `post_embed` logs and returns `False`, never raises; the week's digest is simply skipped (it retries next Monday, not mid-week — a stale "this week" list is worse than none).
- **Admin control:** the digest is a `ScheduledJob`, so it appears in **Site Settings → Automations** with the standard enable/disable toggle and "Run now" — a lead can pause it or fire a test post without a code change (reuses `is_enabled` / `record_run`).

---

## 7. Notifications / emails / activity

- **Community-event Discord Scheduled Events are NOT notifications** — they are a one-way *calendar mirror* through the push service (`core/integrations/discord_events.py`), exactly like the Google push. They do **not** go through `emit()`, create no `EventDelivery` rows, and log no `SiteActivity`.
- **Echo/dedup — not a bug (record it so nobody "fixes" it):** a published guild event already fires an `event.guild_published` **channel announcement embed** (`membership/models.py:3627` → `emit` → the guild/global Discord webhook). That announcement and the new **Scheduled Event** are two *different Discord surfaces* — a one-shot "new event" post in the channel vs. a standing entry in the server's Events UI. Both are intended. (The Google-side `google_ical_uid` echo-hiding at `:3336` is a Google-iCal-reimport concern only; Discord is never read back, so no echo-hide applies.)
- **The classes digest IS a notification-spine broadcast** (`classes.weekly_digest`, §5.6) — Discord channel only (in-app + email OFF), `activity_kind=None` (a weekly roundup is not a fresh site-activity item), one grouped `period` per week. Its copy follows the email-template bar: linked subject noun (each class title → its book page), one clear "Browse all classes" secondary link, absolute URLs, no dead ends.
- **`seed_notification_templates`** must seed the new `classes.weekly_digest` key + copy at go-live (like every new registry key).

---

## 8. Build order (phased; each phase ships green)

Each phase is independently green (full suite + `ruff format` + `ruff check` + `mypy`, run in the `plfog-web` Docker image; `--no-cov` for subsets).

1. **Model + service (no member-visible change).** The 5 `CommunityEvent.discord_*` fields + `SiteConfiguration.discord_events_sync_enabled` + migrations; `push_to_discord()` / `remove_from_discord()` delegators; `needs_discord_push()` / `needs_discord_rollforward()`; `core/integrations/discord_events.py` (client + `push_community_event`/`remove_community_event`/`_build_scheduled_event_body`/`_recurrence_rule_for`, all best-effort, never-raises). Wire `publish()` + the edit/delete view call sites. Specs against a **mocked** Discord client (respx): gated-off/no-token/no-server → `PENDING`/`IDLE` no-op; studio hours → no-op; insert vs update; the recurrence-map table (mappable → correct `recurrence_rule`, unmappable → single occurrence + `discord_pushed_occurrence`); delete propagation; never-raises. *Ships green; inert on prod until the toggle + Manage Events go-live.*
2. **Retry + roll-forward + backfill commands.** `retry_discord_event_pushes` (self-gating, both querysets) + its `ScheduledJob(ALWAYS)`; `backfill_discord_events` (`--dry-run`, future published non-studio-hours events). Specs: self-gate no-op when off; retries PENDING/FAILED; rolls a passed single-occurrence forward (fresh create, updated id); backfill counts + dry-run writes nothing. *Ships green.*
3. **Site Settings toggle UI.** Screen A — the `discord_events_sync_enabled` toggle beside the Google one, via `toggle.html`. Template-render spec; **verify both themes** + mobile. *Ships green.*
4. **Weekly Classes digest — PAUSED, DO NOT BUILD (grilling 2026-07-16; awaits marketing).** When unpaused: `classes.weekly_digest` registry key + copy (`_DISCORD_ON`, in-app/email OFF); `send_class_digest` command (Monday-gated, `emit` with weekly `period`) + its `ScheduledJob(DAILY)`. Specs: Monday gate; empty week posts nothing; body assembly + book links; `period` dedupe (two runs in the 13:00 hour post once); Discord broadcast fires with empty audience. *Ships green.*
5. **Housekeeping.** Bump `plfog/version.py` `VERSION` (from `0.22.8`) and add **one grouped, member-facing CHANGELOG entry** — this is a net-new member-facing feature on the unreleased `0.22` line, so a **new entry at the top** (not folded into an existing one), re-stamped to the new `VERSION`. Suggested copy:
   > **Events in Discord** — Community events now show up right in the Past Lives Discord server's Events tab, so you can mark yourself interested and get reminders without leaving Discord.

   (When the digest un-pauses it gets folded into this entry per the changelog curation rules.)

> Spec only — do not build until approved.

---

## 9. Testing

BDD `*_spec.py` (`describe_*`/`it_*` only — `context_*` is not collected), factory-boy, **respx** for every Discord REST call (never a live call), ≥98% coverage gate, run in the `plfog-web` Docker image.

- **Push service** (`discord_events_spec.py`): `from_settings` disabled when toggle off / blank token / blank `discord_server_id`; `_build_scheduled_event_body` shape (EXTERNAL, `privacy_level=2`, end + location required, blank location → `DEFAULT_LOCATION`, description ends in `public_url`, name/description truncation); `_recurrence_rule_for` per row of the §5.3 table (WEEKLY/MONTHLY → correct dict; every-N/semi-monthly/yearly → `None`; weekday + `-1→5` ordinal mapping); `push_community_event` insert vs update, studio-hours no-op, gated-off `PENDING`, API error → `FAILED`, **never raises**; `remove_community_event` deletes before row delete, swallows failure.
- **Model** (`community_event_discord_spec.py`): `publish()` marks `discord_sync_state PENDING` + calls `push_to_discord` (mocked); `needs_discord_push()` includes PUBLISHED PENDING/FAILED, **excludes** STUDIO_HOURS and non-published; `needs_discord_rollforward()` only SYNCED single-occurrence rows with `discord_pushed_occurrence < now`; the fallback sets `discord_pushed_occurrence`, a mappable recurrence leaves it `NULL`.
- **Commands**: `retry_discord_event_pushes` self-gate no-op; retries + roll-forward (respx); `backfill_discord_events` dry-run writes nothing, real run pushes each future published non-studio-hours event once.
- **Digest** (`class_weekly_digest_spec.py`): Monday gate (Tue/Wed → no emit); empty week → no emit; body lines link `ClassOffering.public_url`; **`period` dedupe — two runs in the same ISO week post the Discord broadcast once** (assert `_record_broadcast` / `EventDelivery` broadcast row); the broadcast fires with the in-app audience empty; copy renders `{{ digest_body }}`.
- **tz/window gotchas:** freeze `now`; all math in Portland local time; the digest's `[frm, frm+7d]` window and the DAILY `hour==13` gate are UTC-vs-local sensitive — assert both. Assert on markup/dict shape, not incidental visible copy (the "what's new" widget echoes the CHANGELOG).

---

## 10. Open / deferred / out of scope

### Flagged for confirmation (new conventions / real product calls)
1. ~~Member-calendar visibility~~ **RESOLVED (grilling 2026-07-16): push all published events.** Josh confirmed the visibility trade-off; no PUBLIC-target gate. (If ever revisited, the gate is `& Q(google_calendar_target=PUBLIC)` on the push guard + `needs_discord_push` — and the Google-named field should then be renamed to something surface-neutral.)
2. ~~`discord_pushed_occurrence` field vs GET-compare~~ **RESOLVED (grilling 2026-07-16): keep the field** (cheaper, explicit).
3. ~~One command vs two~~ **RESOLVED (grilling 2026-07-16): one command** (`retry_discord_event_pushes` does retry + roll-forward, parity with `retry_calendar_pushes`).
4. ~~DEFAULT_LOCATION~~ **RESOLVED (grilling 2026-07-16): the `"Past Lives Makerspace"` constant** (YAGNI; revisit if events span venues).
5. ~~Digest weekday + channel~~ **PAUSED with the whole digest (grilling 2026-07-16)** — Josh: "let's pause on this digest, I don't want to step on marketing toes." Revisit weekday/channel with marketing when the digest is un-paused.

### Build-time verification (must confirm against live Discord, not assumed)
- **The entire `recurrence_rule` limitation table (§5.3)** — which frequency/interval/`by_*` combinations Discord accepts, whether `by_n_weekday` truly forbids `interval>1`, whether Monday=0, and whether YEARLY supports nth-weekday. These are from ~early-2026 doc knowledge; Discord iterates. Prove each with a real (or sandbox-server) call before trusting the map; any newly-supported cadence just moves a row from "fallback" to "mappable."
- **Bot Manage Events permission + external-event field requirements** — confirm `scheduled_end_time` + `entity_metadata.location` are still required for EXTERNAL and the exact 400s on omission.
- **`emit()` posts the Discord broadcast with an empty in-app audience** — verified in source (`_broadcast_fan_out` at `emit.py:199-247` is unconditional on recipients), but assert it in the digest spec so a future emit refactor can't silently break the digest.

### Deferred / out of scope
- **Two-way sync** — a Discord-side edit/delete is never pulled back. One-way forever.
- **Per-event Discord sync badge in the FOG editor** — the Google feature has `_sync_badge.html`; Discord status stays invisible plumbing for v1 (surfaced only via logs + the retry job). Add a badge later if leads ask.
- **Per-class Discord Scheduled Events** — the hard exclusion; classes get the digest, not Events entries.
- **Studio hours as Discord events** — excluded (ambient hours).
- **RSVP/interest write-back** — Discord tracks "interested" counts on a Scheduled Event; we never read them back.
- **Cover images** (`image` on the Scheduled Event) — deferred; the event has a QR/hero but the Discord create can omit the image for v1.

### Go-live checklist (ops prerequisites — not code, but required to switch it on)
1. **Deploy** the code (no new dependency, no new env var).
2. **Grant the bot the Manage Events permission** in the Past Lives server (edit the bot's role, or re-invite with the updated permission scope). Without it, every push records `FAILED`.
3. **Confirm `SiteConfiguration.discord_server_id`** is set (already required for the guild role sync — reused here) and **`DISCORD_BOT_TOKEN`** is set on Render (already present for the DM channel).
4. **Turn on `SiteConfiguration.discord_events_sync_enabled`** in Site Settings (default off).
5. **Run `python manage.py backfill_discord_events`** via a Render one-off job (`--dry-run` first) to push existing future events into Discord's Events.
6. **For the digest:** run `python manage.py seed_notification_templates` (seeds the `classes.weekly_digest` copy), confirm `DISCORD_NOTIFY_WEBHOOK_URL` is set (already is, for `class_published`) or add a `DiscordWebhookRoute` for `classes.weekly_digest` → a `#classes` channel. The `send_class_digest` job runs itself each Monday; use Site Settings → Automations "Run now" to fire a test post.
7. **Verify in Discord:** publish a test event → it appears in the server's **Events**; the event's Discord description links back to its FOG page; run the digest "Run now" → the "Classes This Week" embed posts with working book links. Confirm a member's existing `event.guild_published` channel announcement and the new Scheduled Event are both present (expected — two surfaces).
8. **Verify recurrence:** create a weekly and a monthly event (mappable → native repeat in Discord) and an every-2-months event (unmappable → single next occurrence; confirm the nightly roll-forward re-creates it after it passes).

---

## 11. Review addendum — adversarial UX/completeness pass

A skeptical read of the draft against the fogstorm rubric surfaced the following; each is folded into the body above or noted here as a build-time guard.

1. **[Resolved in body] The digest could post 4× a day.** A DAILY `ScheduledJob` runs on *every* 15-min tick where `now.hour == 13` (four ticks). A naive `post_embed` would quadruple-post. §5.6/§6-C now mandate `emit()` with a weekly `period` so `_record_broadcast` dedupes to exactly one post — and the test explicitly asserts two same-week runs post once.
2. **[Resolved in body] "Mirror Google" would silently push studio hours.** Studio hours are `CommunityEvent`s and flow through `publish()`; without a guard they'd become Discord events. The exclusion is now enforced in *three* places — `push_community_event` early-return, `needs_discord_push()` `.exclude(STUDIO_HOURS)`, and the `push_to_discord()` no-op — so no single call site can leak them. (This is the §5.3-of-the-studio-hours-spec "flood every surface" trap, applied to Discord.)
3. **[Resolved in body] External-event required fields are hard errors, not soft degrades.** A blank `event.location` (common — `location` is optional at `:3250`) would 400 the create because `entity_metadata.location` is required for EXTERNAL. §5.2 forces `DEFAULT_LOCATION`; the spec test covers the blank-location path.
4. **[Resolved in body] The recurrence claims must not be trusted as written.** The §5.3 table is explicitly flagged as build-time-verify (not assumed), and the fallback path guarantees an *unmappable* cadence still shows (its next occurrence) rather than silently vanishing — closing the "you can create it but it doesn't appear" dead end.
5. **[Resolved in body] Roll-forward can't PATCH a completed event.** A past external event auto-completes; §5.3 rolls forward by **creating a fresh** event and updating `discord_event_id`, not by PATCHing a dead one.
6. **[Guard] Edit-of-PUBLISHED and delete paths bypass `publish()`.** Verified against `hub/views.py` — the edit `elif PUBLISHED` branches (`:2777`, `:2843`, `:3098`) and the deletes (`:2812`, `:2870`, and studio-hours `:896`/`:899`) each get an explicit parallel Discord call (§5.4 table). Because `push_to_discord()` self-gates, the studio-hours sites stay no-ops without special-casing.
7. **[Confirmed clean] Decline needs no Discord cleanup.** Checked against source: `decline()` (`membership/models.py:3851`) is reachable only from `PENDING`/`CHANGES_REQUESTED` (`:3858`) — a declined proposal was never published, so it has no `discord_event_id` to remove. §5.4 states this as fact; no wiring needed (the Google path adds nothing there either).
8. **[Guard] The digest reuses a slash-command-private helper.** `_class_sessions` / `_section_block` live in `membership/discord_commands.py` (a *pull* command). The digest must **not** import those private helpers across the concern boundary — it queries `ClassSession.objects.upcoming_public()` (the real manager anchor) directly and formats its own body. Noted in §5.6 so the build doesn't create a cross-module private-helper dependency.
9. **[Confirmed clean] Dark/light + mobile.** The only FOG control is the Site Settings toggle via `toggle.html` (theme tokens, no inline color, real tap target) — Screen A verifies both themes. Everything else renders in Discord's own client, so the theme/mobile rows are correctly replaced by Discord rendering constraints (Screens B/C).

> Spec only — do not build until approved.
