# Discord `/create` command (sesh replacement) — Spec & Implementation Plan

**Status:** Approved to build (v1.8.0 — note: main had moved to 1.7.0 by build time, so the release is 1.8.0, not the 1.7.0 named in §8/§11).
**Date:** 2026-08-25
**Surface:** Discord (Past Lives server slash commands + bot DMs); touches the notification registry and hub-shared event plumbing. No web pages change.
**Related:** PR #162 (the never-registered `/create-event` draft), `docs/superpowers/plans/2026-08-15-event-video-link.md`, `docs/superpowers/plans/2026-07-21-discord-members-command.md`.

---

## 1. Summary

Members create Community Calendar events straight from Discord: `/create title:Potluck when:next friday 6pm`, look over a preview of exactly what will be posted, and click **Create event**. Guild leads and admins publish instantly (calendar + announcement + Google + Discord Scheduled Event, exactly like the hub); everyone else's event goes to the existing review queue, and the reviewer's verdict now comes back as a Discord DM as well as the bell and email. A companion `/cancel` command withdraws a pending proposal or cancels an upcoming event you're allowed to delete. The third-party sesh bot is retired in the same release — `/create` is its replacement, `/whats-on` already covers its listing job.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Command name | `/create` — rename the draft's `create-event`; matches sesh muscle memory |
| Natural-language dates | Yes — "next friday 6pm", "tomorrow 7pm to 9pm" style; clear ephemeral error on ambiguity |
| Verdict DMs | Yes — `event.approved` / `event.declined` / `event.changes_requested` gain the Discord DM channel |
| Preview + Confirm | Yes — persisted draft + Confirm/Cancel buttons, copying the `/create-announcement` pattern |
| `/cancel` command | Yes — withdraw/cancel from Discord |
| Recurrence | Yes — a basic recurrence option |
| Rate limit | Yes — per-member cap on Discord-originated event creation |
| Sesh removal | Same release: recreate the one live sesh event as a CommunityEvent, announce, kick sesh (ops steps in §8) |

### This spec's own calls

| Question | Call | Why |
|---|---|---|
| Option set | 10 options: `title`, `when`, `duration_minutes`, `guild`, `details`, `location`, `video_url`, `recurrence`, `calendar`, `email` (§6.A) | The draft's set, with `date`/`start_time`/`end_time` collapsed into one `when`, plus the model fields members actually need. `publish_at` stays web-only — a second NL time field doubles the ambiguity surface for a rare need. |
| NL grammar | Enumerated table in §5.1. Missing time = error, never a silent default. Past = error. Bare weekday = next upcoming. A range whose end ≤ start rolls the end to the next day (overnight events); the preview is the safety net. | Predictable > clever. Every rejection names an accepted example. |
| Draft persistence | New small `CommunityEventDraft` model, NOT a `CommunityEvent` in a new moderation state | `ModerationState` (models.py:4518) has no DRAFT value, and adding one would leak half-created rows into every queryset that filters only positively (`published()`, `upcoming()`, admin lists) and into the `ck_communityevent_guild_matches_type` constraint's blast radius. A dedicated draft table mirrors the proven `AnnouncementDraft` (models.py:3532) exactly, including the atomic claim. |
| Draft expiry | Confirm window 30 minutes; running `/create` again deletes your older unclaimed drafts; no cron | One pending preview per member at a time; a stale click gets a friendly "expired" reply. Abandoned rows are bounded without a new scheduled job. |
| Recurrence subset | `none`, `weekly`, `semi_monthly`, `monthly` (4 of the 8 choices) | The basic set sesh users expect. The every-2/3/6-months and yearly options stay on the web form — the dropdown stays scannable. |
| `/cancel` picker | A string select menu of your cancellable upcoming events (≤ 25), then a per-event confirm | No autocomplete plumbing exists (and interaction type 4 isn't handled by the endpoint); select menus route through the existing `dispatch_component` (core/views.py:152). |
| `/cancel` authority | Mirrors the hub exactly: withdraw your own PENDING/CHANGES_REQUESTED proposal (`hub_event_withdraw`, hub/views.py:3692); delete a published/scheduled event only if `is_fog_admin`, or `can_edit_guild(guild)` for that guild's events (`event_delete` hub/views.py:3562, `guild_event_delete` hub/views.py:3501) | No new authority is minted by moving to Discord. |
| Verdict-DM default | `DISCORD_DM` default **ON** for the three verdict rows (all other events keep the global OFF default) | The verdict answers a question the member asked; a Discord-originated proposer must hear back without hunting for an opt-in. The adapter already no-ops for unlinked members, and anyone can opt out on the settings page. |
| Rate limit | 4 per hour, 12 per day per member, counted at Confirm; peeked at `/create` for an early friendly error | Generous enough for a lead entering a batch, tight enough to stop spam. Global-cache pattern from `core/abuse_limits.py` reused with per-member keys. |
| Confirm-click ack | Add a type-6 (DEFERRED_UPDATE_MESSAGE) ack helper next to `ack_deferred` | The confirm runs announce + Google push + Discord push (+ optional email) — well over Discord's 3 s. `send_followup` already PATCHes `@original`, so after a type-6 ack it edits the preview in place, buttons gone. One small plumbing add, no modals (unsupported — core/views.py:125-154 handles types 1/2/3 only). |
| Sesh event recreation | Manual, via the hub admin event form | One event; a management command for a one-off is gold-plating. |

## 2. What already exists (reuse, don't reinvent)

All verified in code 2026-08-25.

| Need | Existing thing | Location |
|---|---|---|
| Command declaration + registration | `SlashCommand` (name/options_builder/defer/ephemeral/requires_link/scope), `register`, `autodiscover` | `core/events/discord_commands.py:37-145` |
| Button/select routing by `custom_id` prefix | `ComponentHandler`, `register_component`, `dispatch_component` | `core/events/discord_commands.py:89-121, 270-300` |
| Interactions endpoint (types 1/2/3 only — **no modals**) | `discord_interactions` view | `core/views.py:125-154` |
| Reply builders + REST | `reply()` (type 4), `update_message()` (type 7), `ack_deferred` (type 5), `send_followup` (PATCH `@original`), `error_reply`, `unlinked_reply` | `core/events/discord_interactions.py:88-235` |
| Discord user → Member | `resolve_member` via verified `discord_user_id`; `requires_link=True` auto-bounces unlinked callers | `core/events/discord_commands.py:151-165, 238-267` |
| The whole create pipeline (draft command) | option parsing, guild resolution (`_resolve_target_guild`), form binding (`_build_event_form`), permission tier, `_finalize_event`, `_published_reply`/`_pending_reply`, `_create_event_options` | `membership/discord_commands.py:819-1199` |
| Two-step preview + confirm + atomic claim | `/create-announcement`: persisted draft, `prefix:action:pk` custom_ids, authority re-check on click, `UPDATE … WHERE sent_at IS NULL` claim, in-place `update_message` | `hub/discord_commands.py:497-661` |
| Draft-model shape to mirror | `AnnouncementDraft` + manager | `membership/models.py:3524-3633` |
| Shared validation (naive→aware, end>start, publish_at rules) | `CommunityEventForm(as_member=True)` | `hub/forms.py:1652-1761` |
| Event lifecycle | `propose()` (5215), `publish()` (5103), `schedule_or_go_live()` (5123), `withdraw()` (5289), `approve`/`request_changes`/`decline` (5304-5365), `_emit_submitted` (5380), `remove_from_google`/`remove_from_discord` (5177, 5208) | `membership/models.py` |
| DB constraints | `ck_communityevent_end_after_start` (4758), `ck_communityevent_guild_matches_type` (4763) — `propose()` derives type from guild; never set both independently | `membership/models.py` |
| Recurrence enum (8 values) | `CommunityEvent.Recurrence` | `membership/models.py:4508-4516` |
| Verdict notification rows (in-app + email today) | `event.approved` / `event.changes_requested` / `event.declined`, `SINGLE_USER` | `core/events/registry.py:699-728` |
| Discord DM channel + adapter (no-ops when unlinked / token blank) | `Channel.DISCORD_DM` (43-46), `_DISCORD_DM_OFF` (237), `DiscordDMAdapter` | `core/events/registry.py`, `core/events/channels.py:441-467` |
| DM REST + formatting | `post_dm`, `format_dm_content` (bold title, body, url) | `core/events/discord_dm.py:88-129` |
| Rate-limit counter pattern (global today) | `_bump` / `cache.add` rolling counters | `core/abuse_limits.py:21-45` |
| Command registration (instant, bulk PUT replaces set) | `register_discord_commands` management command | `core/management/commands/register_discord_commands.py` |
| Site-local time rendering, option reading, hub URLs | `format_local`, `option_value`, `hub_url`, `truncate` | `core/events/discord_replies.py:28-67` |
| `/guide` (auto-built from the registry — no hand edit needed) | `_guide` builds from `all_commands()` | `core/events/discord_commands.py:346-370` |
| Hub cancel/withdraw semantics to mirror | `event_withdraw` (3692), `event_delete` (3562), `guild_event_delete` (3501) | `hub/views.py` |
| Upcoming-events queries | `CommunityEvent.objects.upcoming()/awaiting_review()/published()` | `membership/models.py:4388-4441` |
| Existing spec for the draft command | `tests/core/events/create_event_command_spec.py` | reworked in place |

Genuine gaps to close (all small): the NL `when` parser, the `CommunityEventDraft` model, per-member keyed rate counters, the type-6 ack helper, the `/cancel` command, and the one-line registry change per verdict row.

## 3. Where the code lives

```
membership/
  discord_commands.py        # /create (renamed + preview flow), /cancel, two ComponentHandlers
  when_text.py               # NEW — natural-language when parser (stdlib + dateutil, no new deps)
  models.py                  # NEW CommunityEventDraft (+ manager); nothing on CommunityEvent changes
  migrations/0XXX_communityeventdraft.py
core/
  abuse_limits.py            # + keyed per-member counters (peek + record)
  events/
    discord_interactions.py  # + ack_component_deferred() (type 6)
    registry.py              # + DISCORD_DM (default ON) on the three verdict EventTypes
tests/
  core/events/create_event_command_spec.py   # reworked for /create + preview/confirm
  core/events/cancel_command_spec.py         # NEW
  membership/when_text_spec.py               # NEW
  membership/community_event_draft_spec.py   # NEW
  core/abuse_limits_spec.py                  # extended
  core/events/registry_spec.py + notification delivery spec  # verdict-DM assertions
```

## 4. Data model

### 4.1 `CommunityEventDraft` (membership/models.py, next to `AnnouncementDraft`)

One row per pending `/create` preview. It is a scratch payload, never a calendar row — nothing joins to it.

| Field | Type | Note |
|---|---|---|
| `author` | FK User, CASCADE | Whose preview this is; the claim filter and the create actor. |
| `guild` | FK Guild, null/blank, CASCADE | Resolved target (explicit choice or channel match); NULL = site-wide. |
| `title` | Char(200) | Mirrors `CommunityEvent.title`. |
| `starts_at` / `ends_at` | DateTime | The form-cleaned **aware** datetimes (validated before the draft is written). |
| `location` | Char(200), blank | |
| `video_url` | URLField(500), blank | |
| `description` | Text, blank | The `details` option. |
| `recurrence` | Char(20), `CommunityEvent.Recurrence` choices, default NONE | Only the 4 exposed values arrive via the option, but the column accepts all 8. |
| `google_calendar_target` | Char, `GoogleCalendarTarget` choices, default MEMBER | |
| `email_choice` | Char(20), default `"none"` | `none` / `guild_members` / `all_active`. |
| `created_at` | DateTime auto_now_add | Drives the 30-minute expiry. |
| `confirmed_at` | DateTime, null | The claim stamp — the `UPDATE … WHERE confirmed_at IS NULL` target. |

`help_text` on every field, meaningful `__str__` (`"<title> by <author> (unconfirmed)"` style). Manager: `CommunityEventDraftManager.claimable_for(user)` → `filter(author=user, confirmed_at__isnull=True)`.

**Migration:** one `CreateModel`, auto-reversible (reverse = drop table). No data backfill.

### 4.2 Registry change (code, no migration)

In `core/events/registry.py`, the three verdict `EventType` rows (699-728) change

`channels=(_IN_APP_ON, _EMAIL_ON)` → `channels=(_IN_APP_ON, _EMAIL_ON, _DISCORD_DM_ON)`

with a new module constant `_DISCORD_DM_ON = ChannelSpec(Channel.DISCORD_DM, ChannelDefault.ON)` beside `_DISCORD_DM_OFF` (237). Preferences are computed from the registry, so no stored rows change; the settings page picks the toggle up automatically. Web proposers who happen to have linked Discord now also get verdict DMs by default — intended, and they can opt out.

## 5. Business logic (fat models / small pure modules)

### 5.1 `membership/when_text.py` — the NL parser

`parse_when(text: str, *, duration_minutes: int, now: datetime) -> WhenResult` where `WhenResult` is either `(start_naive, end_naive)` local-naive datetimes (the form makes them aware, exactly like the draft's `_parse_when`) or a typed error: `NO_TIME`, `UNPARSEABLE`, `IN_PAST`, `TOO_FAR`. Pure function of its arguments (testable with a frozen `now`). Built on stdlib + `dateutil.parser` (already a dependency) — **no new package**.

Grammar (the spec table; input → parse, `now` = Tue 2026-08-25 in America/Los_Angeles):

| Input | Parses to |
|---|---|
| `next friday 6pm` / `friday 6pm` / `this friday 6pm` | Fri 2026-08-28 18:00, end 19:00 (default duration) |
| `tomorrow 7-9pm` / `tomorrow 7pm to 9pm` | Wed 2026-08-26 19:00-21:00 |
| `today 8pm` / `tonight 8pm` | Tue 2026-08-25 20:00 |
| `2026-08-29 6pm` / `aug 29 6pm` / `august 29 18:00` / `8/29 6pm` | Sat 2026-08-29 18:00 |
| `sep 12 noon` | **UNPARSEABLE** (word times not supported; error names examples) |
| `next friday` | **NO_TIME** |
| `yesterday 6pm`, `aug 1 6pm` (with explicit past year) | **IN_PAST** |
| `friday 6pm 2031` or anything > 366 days out | **TOO_FAR** (almost always a typo'd year) |
| `saturday 9pm-1am` | Sat 21:00 → Sun 01:00 (end ≤ start rolls end +1 day; preview shows both) |

Rules, exhaustively: (1) a time of day is **required** — no silent noon/all-day default; (2) a bare weekday means the next upcoming one strictly after `now`; a month-day with no year means the next upcoming occurrence; (3) the result must be in the future; (4) an explicit range (`X-Y` or `X to Y`, where the end token's am/pm covers a bare start number, so `7-9pm` is 19:00-21:00) wins over `duration_minutes`; no range → `start + duration_minutes` (default 60, the draft's `_DEFAULT_DURATION_MINUTES`); (5) 24-hour forms (`18:00`) and the draft's `_TIME_FORMATS` (`6pm`, `6:00 PM`, …) are all accepted — the time-token matcher extends `_parse_time` (membership/discord_commands.py:852-865) rather than replacing it.

Implementation shape: normalize whitespace/case → split off a trailing time-range token → resolve the day phrase (hand-rolled `today`/`tonight`/`tomorrow`/`[next|this] <weekday>`; else `dateutil.parser.parse(date_part, default=<today at midnight local>)` with `fuzzy=False`) → combine. Any parse exception maps to `UNPARSEABLE`, never propagates.

### 5.2 `core/abuse_limits.py` — keyed counters

Add, beside the existing global functions (reusing `_bump`):

- `record_keyed_attempt(scope: str, key: str, *, hourly_limit: int, daily_limit: int) -> tuple[bool, str | None]` — cache keys `abuse:<scope>:<key>:hourly|daily`.
- `keyed_within_limits(scope, key, *, hourly_limit, daily_limit) -> bool` — read-only peek for the early check.

`/create` uses `scope="discord_create"`, `key=str(member.pk)`, limits **4/hour, 12/day**. Peeked in the slash handler (friendly early refusal before a draft is written); recorded only when a Confirm actually creates an event, so previews that get cancelled don't burn quota.

### 5.3 `/create` slash handler (fast, synchronous — no defer at this step)

Order of guards, each an immediate ephemeral reply: unlinked (dispatch handles) → `member.user is None` (`_SETUP_INCOMPLETE`, kept) → rate-limit peek → guild resolution (`_resolve_target_guild`, kept) → `parse_when` (typed errors → the §6.B copy) → bind `CommunityEventForm(as_member=True)` via `_build_event_form` **now passing the recurrence option instead of hardcoding NONE (line 951)** → form errors surface via `_form_error_reply` (kept) → DISABLED-policy gate for non-authored callers (`_NOT_PERMITTED`, kept, line 1104). All green: delete the author's older unclaimed drafts, create the `CommunityEventDraft` from `cleaned_data`, reply with the preview (§6.C). Nothing is published at this step.

### 5.4 Confirm/Cancel component (`custom_id` prefix `create`)

Copying `_create_announcement_component` step for step: parse `create:<confirm|cancel>:<draft_pk>`; reload `claimable_for(member.user)` draft; missing/foreign → "expired" `update_message`. Age > 30 min → delete + "expired". **Cancel** → delete draft, `update_message("Cancelled. Nothing was created.")`. **Confirm** → re-check authority + policy (state can shift between preview and click), re-peek the rate limit, atomically claim (`UPDATE … WHERE confirmed_at IS NULL`; lost claim → "already handled", no double event), then — because publish runs announce + Google + Discord + optional email — **ack type 6** (`ack_component_deferred`, the one new helper in `discord_interactions.py`: same callback POST as `ack_deferred` with `{"type": 6}`), rebuild the form from the draft, and run the existing `_finalize_event` (1036-1064's shape, now `send_followup(..., components=data.get("components") or [])` — the explicit empty list matters: `send_followup` omits the key when None and the PATCH would leave the live Confirm/Cancel buttons on a `_pending_reply`, while `_published_reply`'s own "Open the event" link button must survive). `record_keyed_attempt` fires on success. Any exception → the followup is `error_reply()`'s content, draft left claimed (no retry loops).

### 5.5 `/cancel`

Handler (`requires_link=True`, synchronous): first guard `member.user is None` → `_SETUP_INCOMPLETE` (same as `/create`; a None user would make the withdrawable filter match `submitted_by__isnull=True` rows). Then build the caller's cancellable set, soonest-starting-first capped at 25 —

- **Withdrawable:** `CommunityEvent.objects.filter(submitted_by=member.user, moderation_state__in=[PENDING, CHANGES_REQUESTED])`.
- **Deletable:** upcoming (`ends_at >= now`, or recurring) PUBLISHED/SCHEDULED events where `member.is_fog_admin`, or `event.guild is not None and member.can_edit_guild(event.guild)`.

Empty → the §6.E empty reply. Else an ephemeral string-select (`custom_id "cancel:pick"`, option value = pk, label = truncated title, option `description` = `format_local(starts_at)`). Component prefix `cancel` handles: `pick` → `update_message` confirm card (§6.E) with buttons `cancel:confirm:<pk>` / `cancel:keep:<pk>`; `keep` → "Kept. Nothing changed."; `confirm` → re-fetch + re-check authority and state, then branch: withdrawable → `event.withdraw(by=member.user)` (deletes the row, nothing to unwind — models.py:5289); deletable → ack type 6 (Google/Discord REST calls), `remove_from_google()` + `remove_from_discord()` + `delete()` (the exact `event_delete` sequence, hub/views.py:3570-3572), PATCH the confirm card with the result. A vanished row (double-click, or someone else acted first) → "already handled" copy, never a stacktrace. `InvalidEventTransition` is caught and rendered as the same friendly copy.

### 5.6 Verdict DMs

No new send-path code: the three `_emit_decision` emits (models.py:5319, 5340, 5365) already produce a per-recipient `Message`; adding `DISCORD_DM` to their registry rows routes it through `DiscordDMAdapter` → `post_dm`. Content = the event's existing rendered copy through `format_dm_content` (bold title, body, url on its own line) — e.g. the changes-requested DM carries the reviewer's note and the hub edit deep-link (`hub_propose_event_edit`) as its url. **Fallback:** `post_dm` is best-effort — closed DMs / blocked bot logs a warning and returns False, and the member still has the in-app bell and email (both remain ON). No retry, no user-visible error.

## 6. UI / UX — every Discord surface, every state

Discord renders these natively, so the checklist's dark/light/mobile/8px items are Discord's job; what the rubric demands here is: every "screen" (option set, preview, buttons, error replies, DMs, `/cancel` steps) has explicit happy / empty / error / ambiguity states, a primary action, and no dead ends. All replies are **ephemeral** (flag 64) unless noted. All member-facing copy below is final and contains no dashes (Jo's rule); ISO dates and range inputs like `7-9pm` are data formats, not punctuation.

### A. The `/create` option set (the "form")

Required options first (Discord requires it). Descriptions ≤ 100 chars.

| # | Option | Type | Req | Description (copy-ready) |
|---|---|---|---|---|
| 1 | `title` | 3 str | yes | The event name shown on the calendar, like Monthly Potluck. |
| 2 | `when` | 3 str | yes | When it happens, like next friday 6pm, tomorrow 7pm to 9pm, or 2026-09-12 18:00. |
| 3 | `duration_minutes` | 4 int, min 1 | no | How long in minutes when your when has no end time. Default 60. |
| 4 | `guild` | 3 choices | no | Which guild this is for. Pick General, or skip it to use this channel's guild. |
| 5 | `details` | 3 str | no | More about it. What to bring, the agenda, who it is for. |
| 6 | `location` | 3 str | no | Where it happens. A room, an address, or leave blank. |
| 7 | `video_url` | 3 str | no | A link to join online, like a Google Meet URL. |
| 8 | `recurrence` | 3 choices | no | Whether it repeats. Default is a one time event. |
| 9 | `calendar` | 3 choices | no | Which Google calendar it posts to. Default is members only. |
| 10 | `email` | 3 choices | no | Also email members about it. Off by default. |

Choice lists: `guild` = "General (whole makerspace)" + up to 24 active guilds (the existing `_create_event_options` builder, run at registration time — line 1111's pattern, unchanged). `recurrence` = Does not repeat / Every week / Twice a month / Every month (values `none`/`weekly`/`semi_monthly`/`monthly`). `calendar` = Members only (default) / Public. `email` = Don't email / This guild's members / The whole membership. All well inside the 25-option and 25-choice limits.

### B. Immediate error replies (each names the fix — no dead ends)

| State | Ephemeral copy |
|---|---|
| Rate limited (peek) | You have hit the limit for creating events from Discord (4 per hour, 12 per day). Try again in a bit, or use the hub: `<hub_url('hub_propose_event')>` |
| `when` UNPARSEABLE | I could not read that date and time. Try one of these: next friday 6pm, tomorrow 7pm to 9pm, 2026-09-12 18:00. |
| `when` NO_TIME | I got the day but not a start time. Add one, like next friday 6pm. |
| `when` IN_PAST | That time has already passed. Events need a start in the future. Check the date and try again. |
| `when` TOO_FAR | That date is more than a year away. Double check the year and try again. |
| `email` is guild_members but no guild resolved (site-wide event) | Pick a guild to email its members, or choose the whole membership. *(caught at `/create` time — `email_announcement("guild_members")` silently returns 0 when `guild is None`, models.py:5070, so it must never reach the preview)* |
| Unknown guild value | Existing `_guild_not_found_reply` (lists the active guilds) — kept as is. |
| Form invalid (end before start, long title, bad video URL) | Existing `_form_error_reply`: the form's own message + "Nothing was created" tail — kept as is. |
| Not permitted (DISABLED policy, non-lead) | Existing `_NOT_PERMITTED` — kept as is. |
| Account not set up | Existing `_SETUP_INCOMPLETE` — kept as is. |
| Unlinked | Existing `unlinked_reply` with the one-click `/link` URL (dispatch-level). |

### C. The preview (the confirm "screen")

One ephemeral message; every value the member chose is shown so nothing publishes sight-unseen. Times render **site-local** via `format_local`. Shape:

> **Here is your event. Please confirm.**
> **Monthly Potluck**
> Sat Aug 29, 6:00 PM to 8:00 PM (Pacific)
> Guild: Fiber Arts *(or "Whole makerspace")*
> Location: Main hall *(only when set)*
> Join online: <url> *(only when set)*
> Repeats: Every week *(only when repeating)*
> Calendar: Members only
> Also emails: this guild's members *(only when an email audience was chosen)*
>
> *branch line, exactly one of:*
> You can post for this guild, so this will publish right away. *(authored, guild event)*
> You can post site wide events, so this will publish right away. *(authored, no guild — e.g. an admin's General event)*
> This will publish right away. *(OPEN policy, non-lead)*
> This will go to the review queue. A lead or admin will take a look, and you will hear back when they decide. *(and, when an email audience was chosen:)* The email option only applies when an event publishes, so it will not be sent for a proposal.

Buttons (one action row): **Create event** (style 3 success, `create:confirm:<pk>`) — the obvious primary action — and **Cancel** (style 4 danger, `create:cancel:<pk>`).

States: happy = above; confirm success = preview replaced in place by `_published_reply` (with its "Open the event" link button) or `_pending_reply`; cancel = "Cancelled. Nothing was created."; expired/claimed/foreign draft = "This preview expired or was already handled. Run /create again if you still want the event."; authority lost between preview and click = the same `_NOT_PERMITTED` copy via `update_message`, draft deleted; fan-out failure after claim = "Something went wrong on our side and the event was not fully posted. Please check the calendar or try again." (logged loudly).

### D. Verdict DMs (a "screen" the proposer receives)

Format is `format_dm_content`: bold title, body, deep link on its own line. The three messages reuse the events' existing rendered copy — approved carries the schedule-aware `{{ outcome }}` line and the event URL; changes requested carries the reviewer's note and the hub **edit** deep-link (the resubmit path stays on the hub, by design); declined carries the reason and the propose-again URL. Empty state: unlinked recipient or blank bot token → adapter no-ops. Error state: closed DMs → logged, bell + email still deliver. Opt-out: the settings page row gains a Discord DM toggle automatically (registry-driven).

### E. `/cancel` flow

1. **Empty state:** "You have no upcoming events you can cancel from here. If one of your published events needs to come down, ask a lead or admin." *(honest copy: under OPEN policy a non-lead's self-published event is cancellable by nobody but staff, on either surface — see §10)*
2. **Picker (happy):** "Which event do you want to cancel?" + string select (≤ 25 soonest; if truncated, a trailing line: "Only your next 25 are listed. The rest are on the hub.").
3. **Confirm card** (via `update_message`, so no stacked messages):
   - Withdraw branch: "Withdraw your proposal **<title>** (Sat Aug 29, 6:00 PM)? It was never published, so it just comes off the review queue."
   - Delete branch: "Cancel **<title>** (Sat Aug 29, 6:00 PM)? It will be removed from the Community Calendar, Google Calendar, and Discord. Members will not be notified automatically. *(when recurring:)* This removes the whole repeating series."
   - Buttons: **Yes, cancel it** (style 4 danger, `cancel:confirm:<pk>`), **Keep it** (style 2 secondary, `cancel:keep:<pk>`).
4. **Results:** withdraw → "Proposal withdrawn."; delete → "Event cancelled and removed from the calendar, Google Calendar, and Discord."; keep → "Kept. Nothing changed."
5. **Error/ambiguity states:** event already gone or already handled (double click) → "That event was already handled. Nothing more to do."; authority lost / state changed between pick and confirm → "You can no longer cancel that event from here. Ask a lead or admin to remove it." Never a stacktrace, never a silent no-op.

Command registration: `SlashCommand(name="cancel", description="Withdraw or cancel one of your upcoming events.", requires_link=True, ephemeral=True, defer=False)`.

### F. `/guide`

Zero code: `_guide` builds from `all_commands()` (core/events/discord_commands.py:354), so `/create` and `/cancel` list themselves with their descriptions. A spec asserts both names appear.

## 7. Notifications / emails / activity

No new event types. One registry change:

| Event key | Recipient | Channels before | Channels after |
|---|---|---|---|
| `event.approved` | SINGLE_USER (proposer) | in-app ON, email ON | in-app ON, email ON, **discord_dm ON** |
| `event.changes_requested` | SINGLE_USER | in-app ON, email ON | + **discord_dm ON** |
| `event.declined` | SINGLE_USER | in-app ON, email ON | + **discord_dm ON** |

`event.submitted` (reviewers) is untouched. The publish path's announcement/email (`email_announcement`, models.py:5033) is reused as-is from the confirm step.

## 8. Build order (phased; each phase ships green — full suite + lint + mypy)

1. **Parser + limits (pure logic):** `membership/when_text.py` with the full grammar + typed errors; keyed counters in `core/abuse_limits.py`. Specs first.
2. **Draft model:** `CommunityEventDraft` + manager + migration (reversible). Specs for claim atomicity and `claimable_for`.
3. **`/create`:** rename `create-event` → `create` in `membership/discord_commands.py`; swap `date`/`start_time`/`end_time` options for `when`; add `location`/`video_url`/`recurrence` options; wire the preview + `create` ComponentHandler; add `ack_component_deferred` to `core/events/discord_interactions.py`; recurrence stops being hardcoded NONE. Rework `tests/core/events/create_event_command_spec.py`.
4. **`/cancel`:** command + `cancel` ComponentHandler + specs.
5. **Verdict DMs:** the three registry rows + `_DISCORD_DM_ON`; delivery specs (respx) incl. the closed-DM fallback.
6. **Housekeeping + release:** VERSION → **1.7.0** in `plfog/version.py` and ONE member-facing CHANGELOG entry (§11).

**Rollout / ops runbook (same release, in order):**

1. Merge + deploy (Render).
2. Run `manage.py register_discord_commands` as a Render one-off. The bulk PUT replaces the whole guild-scoped set, so `/create` and `/cancel` appear instantly; the never-registered `create-event` never existed on Discord, so there is nothing to remove.
3. Smoke-test in the server: `/guide` lists both; `/create` a throwaway event as an admin; `/cancel` it.
4. Recreate the one live sesh event **manually via the hub admin event form**. Details captured from Discord 2026-08-25: "Intellectual Property Considerations for Makers", Fri Aug 29 2026, 2:00 PM to 5:00 PM Pacific (21:00 to 00:00 UTC), location "Tech Guild", created by Patricia (tricia38) — confirm final details with her before publishing. Note: sesh RSVPs do not migrate (it had 1) — the Discord Scheduled Event created by the push gives members a new interested-list.
5. Announce: `announce_release` carries the CHANGELOG entry; additionally post a short `/create-announcement` in the general channel pointing members at `/create` and noting sesh is retiring.
6. Kick the sesh bot (Server Settings → Integrations). Doing this **after** step 4 means its event page stays visible until the replacement exists.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*` (never `context_*` — not collected), factory-boy, respx for all Discord HTTP, 100% coverage.

- **`when_text_spec.py`:** every grammar-table row; each typed error; the overnight roll; range-beats-duration; frozen `now`. **Tz gotcha:** an evening PDT start ("friday 6pm") is the next UTC day once aware — assert via `timezone.localtime`, never raw UTC dates, and pin `America/Los_Angeles`.
- **`community_event_draft_spec.py`:** claim wins exactly once under a simulated double-confirm (two calls, one 0-row update); `claimable_for` excludes claimed/foreign drafts; new-draft creation deletes the author's stale ones.
- **`create_event_command_spec.py` (rework):** guard order (rate limit peek, guild, when errors, form errors, DISABLED gate); preview content branches (authored / OPEN / APPROVAL, email note, recurrence line, site-local time string); confirm publishes via `schedule_or_go_live` for a lead and `propose` for a member; type-6 ack + PATCH `@original` with `components=[]` (respx asserts the callback and webhook URLs); cancel deletes the draft; expired/foreign/double-click replies; rate limit recorded only on success; recurrence value lands on the saved event.
- **`cancel_command_spec.py`:** cancellable-set membership per authority tier; empty state; select→confirm→withdraw (row gone, no Google/Discord calls); confirm→delete calls `remove_from_google` + `remove_from_discord` before `delete` (respx); keep; already-handled; authority-revoked-between-clicks.
- **Registry/delivery:** the three verdict events carry `DISCORD_DM` default ON; an emit to a linked proposer POSTs the DM open + message (respx); an unlinked proposer or a 403 (closed DMs) delivers bell + email and never raises.
- **`abuse_limits_spec.py`:** keyed counters isolate per member; peek does not bump; hourly/daily boundaries.
- **`/guide`:** lists `/create` and `/cancel`.

## 10. Open / deferred

- **Editing from Discord** — deferred. The published-event reply's hub link and the changes-requested DM's edit deep-link are the edit affordance (matches the draft's v1 stance).
- **`publish_at` / scheduled announcements from Discord** — web-only; a second NL time field is not worth its ambiguity.
- **Word times ("noon", "half past six")** and non-US date orders — out of scope for the v1 grammar; the error copy teaches the accepted forms.
- **Migrating sesh RSVPs** — impossible via sesh's surface; explicitly accepted loss (one event, one RSVP).
- **`event.submitted` reviewer DMs** — not in this release; reviewers keep bell + email (flippable later, one registry line).
- **Autocomplete-based `/cancel` picker** — would need interaction type 4 support in the endpoint; the select menu does the job at ≤ 25 events.
- **Self-cancel of an OPEN-policy self-published event** — a non-lead whose event published instantly under the OPEN policy cannot cancel it on Discord *or* the hub (`event_withdraw` covers only pending proposals; `event_delete` is staff-only). Known pre-existing gap on both surfaces; the `/cancel` copy is honest about it. The seam is a `submitted_by`-may-delete branch in `event_delete`, revisit if it comes up.

## 11. Versioning & changelog

Bump `plfog/version.py` `VERSION` **1.6.1 → 1.7.0** (net-new member-facing feature). ONE new CHANGELOG entry stamped `1.7.0` so `announce_release` resolves:

- **Title:** "Create community events right from Discord with /create"
- **Body:** "Type /create in Discord, describe when in plain words like next friday 6pm, check the preview, and confirm. Leads publish instantly and everyone else's idea goes to the review queue, with the decision sent back to you as a Discord DM. Use /cancel to withdraw or cancel one of your events. Our old event bot sesh has retired."

> Spec only — do not build until approved.
