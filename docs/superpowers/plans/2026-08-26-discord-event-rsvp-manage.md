# Discord event RSVP + Manage (sesh parity plus) — Spec & Implementation Plan

**Status:** Spec for review (v1.10.0).
**Date:** 2026-08-26
**Surface:** Discord (#public-calendar event announcements, `/poll` messages, component clicks) + one small hub page addition (event detail attendees). Touches `membership`, `hub`, `core.integrations.discord_channel`, `core/events/discord_interactions`.
**Related:** `docs/superpowers/plans/2026-08-25-discord-create-command.md` (v1.8.0), the `/poll` command (v1.9.0, `membership/discord_commands.py:1630-1901`).

---

## 1. Summary

Sesh's event messages gave members three things ours do not: a rich, scannable embed (bold Time / Duration / Location field headlines), a live **Attendees** list that updates as people RSVP with ✅, and a ⚙ affordance for the organizer. This release brings all three to our own announcements. Every published CommunityEvent's Discord channel announcement becomes a rich embed with an **✅ RSVP** toggle button and a **⚙ Manage** button; RSVPs land in a new `EventRSVP` table and render live in the embed's `Attendees (N)` field; Manage opens an ephemeral card with an Edit link and a jump into the existing `/cancel` confirm flow, authority-gated exactly like `/cancel`. The hub event page grows a read-only "Who's coming" list (hub-side RSVP is a should-have). `/poll` messages gain a small ⚙ **End poll** button for the asker and admins.

### 1.1 The webhook-vs-bot investigation (this determines feasibility — verified in code 2026-08-26)

A published CommunityEvent reaches Discord on **three separate paths today**, and only one of them is the "event announcement message" this feature targets:

1. **The #public-calendar announcer (bot-token channel post — the target surface).** The 15-minute cron `announce_calendar_events` calls `announce_new_events()` (`hub/discord_calendar_posts.py:310-392`), which picks up every published, upcoming, non-studio-hours CommunityEvent with `channel_announced_at IS NULL` (`membership/models.py:4723`) — **regardless of whether it was created on the web or via `/create`** — builds a compact embed (`_announcement_embed`, `hub/discord_calendar_posts.py:280-288`), and posts it via `post_channel_message` (`core/integrations/discord_channel.py:33-41`): a plain `POST {API_BASE}/channels/{channel_id}/messages` **authenticated with the bot token** (`_auth_headers` from `core/events/discord_dm.py`). This is NOT a webhook. Because the message is authored by our application (the bot), Discord allows `components` on it, component clicks arrive at our interactions endpoint (`core/views.py:123-155`, type 3 → `dispatch_component`), and a **type-7 UPDATE_MESSAGE** response legally edits it in place. **No migration of the posting mechanism is needed — the bot-post surface already exists.** The helper just needs to learn to send `components` and return the created message id (it currently sends `{"embeds": …}` only and returns `None`).
2. **The notification-spine broadcast (webhook — cannot carry buttons, left untouched).** `CommunityEvent.publish()` → `announce()` (`membership/models.py:5104, 5010`) emits `event.guild_published` / `event.community_published` (`membership/models.py:4551-4554`, registry rows `core/events/registry.py:610-636`), whose Discord channel is a **webhook** post: `core/events/discord.py` `post_embed`/`build_embed_payload` (lines 157-219) POSTs a generic title/body embed (copy from `core/events/copy.py:978-1037`) to `DISCORD_NOTIFY_WEBHOOK_URL` or a guild's own `discord_webhook_url`. These are member-pasted **incoming webhooks, not application-owned**: Discord only permits interactive components on application-authored messages (bot messages, interaction responses, app-owned webhook executions), and even if it accepted them, clicks on a non-app message would have no application to route to. So this path **cannot** carry the RSVP button and is deliberately left as-is (it is a notification fan-out, not the canonical channel card).
3. **The Discord Scheduled Event** (`push_to_discord`, `membership/models.py:5187`, `discord_event_id` at 4693) — Discord's native server-events entry with its own native "Interested" list. Unrelated surface; untouched.

**Consequence:** the rich embed + buttons land on path 1. The embed builder moves out of the announcer into model methods so the announcer, the button click handler, and the hub RSVP refresh all render one truth.

### 1.2 Locked decisions (from the request)

| Decision | Choice |
|---|---|
| Rich embed | Bold field structure (Time, Duration, Location when set, Attendees), footer "Created by <name>", title links to the event page. Applies to ALL published community events, web- or Discord-created (guaranteed by construction: the announcer keys off `channel_announced_at`, not the creation surface). |
| RSVP storage | New `EventRSVP` model: event FK, member FK, `created_at`, unique together. House rules: `help_text` everywhere, reversible migration. |
| RSVP funnel | Button click resolves the member via the component registry's `requires_link=True`; unlinked clickers get the existing connect prompt (`unlinked_reply`, `core/events/discord_commands.py:292-294`) — a deliberate funnel. |
| Attendee display | Names capped at 15, then "and N more"; count in the field name, e.g. `Attendees (7)`. |
| Manage authority | Creator, guild leads/staff of the event's guild, fog admins — reusing the `/cancel` authority logic (`_cancel_authority`, `membership/discord_commands.py:1473-1485`) plus the creator. |
| Manage → Cancel | Jumps into the EXISTING cancel confirm-card flow: the card's buttons are `cancel:confirm:<pk>` / `cancel:keep:<pk>`, handled by the existing `_cancel_component` (`membership/discord_commands.py:1575-1613`). |
| Hub surface | Read-only attendees list + count on `templates/hub/event_detail.html`; hub-side RSVP button is a should-have (cheap: one POST endpoint + one model method — included). |
| Mass-ping safety | Any content with member-controlled names carries `allowed_mentions: {"parse": []}` (the `/poll` reply's gate, `core/events/discord_interactions.py:115-117`, generalized). |
| No modals | The endpoint handles interaction types 1/2/3 only (`core/views.py:147-154`). Everything here is buttons + ephemeral replies. |
| Version | 1.10.0. |

### 1.3 This spec's own calls

| Question | Call | Why |
|---|---|---|
| RSVP toggle vs two buttons | **One "✅ RSVP" button that toggles.** Clicking adds your RSVP; clicking again removes it. | Sesh muscle memory (its ✅ was a toggle), one less button beside ⚙, and the un-RSVP path costs nothing. The embed footer says so plainly. Feedback is the in-place type-7 update: your name visibly appears in / disappears from the Attendees field. |
| Toggle response shape | Type-7 UPDATE_MESSAGE rebuilt entirely from the DB — never an increment of the rendered text. | Zero extra REST calls, and every click self-heals the message: a render that drifted (hub RSVPs, a concurrent click) is corrected by the next click because the handler re-queries. This is the same statelessness principle as `/members` paging (`membership/discord_commands.py:730-748`). |
| Double-click / race | The toggle is `get_or_create` → created means RSVP'd, existing means deleted (un-RSVP'd), with the unique constraint as the backstop (a concurrent duplicate insert loses to `IntegrityError`, treated as "already existed"). Two rapid clicks by the same member = RSVP then un-RSVP; the final type-7 render shows the true DB state either way. | A toggle cannot be made click-count-proof without a confirm step nobody wants on an RSVP; what matters is the DB and the message never disagree for longer than one click, and a lost race never 500s. |
| RSVP after the event ends | **Blocked for a non-recurring event once `ends_at` is past**: ephemeral "This event has already ended, so RSVPs are closed." A **recurring series keeps accepting RSVPs** (the row represents the ongoing series; the hub page treats it the same way — `show_past_note` is never set for recurring, `hub/views.py:4330-4332`). | Matches the existing "already taken place" semantics; blocking a live weekly series because its anchor date passed would be wrong. Listed as a known simplification in §10. |
| Where the embed builder lives | Fat-model methods on `CommunityEvent` (`discord_announcement_embed()`, `discord_announcement_components()`, `attendees_field()`) + `toggle_rsvp(member)`. | House rule (fat models); and it is the only import-cycle-safe spot all three callers (hub announcer, membership command module, hub view) can share. |
| Storing the message id | Two new nullable-blank char fields on CommunityEvent: `discord_announce_channel_id`, `discord_announce_message_id`, written by the announcer. | The click handler gets the message for free (`interaction["message"]`), but the hub RSVP refresh and the `/cancel` cleanup (strip buttons on a cancelled event) need to find the message from the web side. Cheap columns, no join table. |
| `/poll` manage: button vs `/endpoll` | **(a) A one-button ⚙ row on the poll message itself.** custom_id `poll:end:<creator_member_pk>`. | (b) would need a new `PollRecord` model *and* a way to learn the message id — and our `/poll` posts via the type-4 interaction response (`membership/discord_commands.py:1847`), which never returns a message id to the view; capturing it needs `with_response=true` callback plumbing we don't have. With (a), the click hands us `interaction["message"]["id"]` + `channel_id` for free, the creator pk rides statelessly in the custom_id (the `/members` pattern), and the affordance is discoverable on the message — sesh parity. **Can a message carry BOTH a poll and components?** Discord's Create Message / interaction-callback-data schemas list `poll` and `components` as independent optional fields with no documented mutual exclusion (knowledge as of early 2026), and `reply()` already merges extra keys freely — but because I cannot ship a live probe from a spec, the rollout runbook (§8) makes "post a `/poll` with the gear row" the first smoke test, with the named fallback: if Discord rejects the combined payload, drop the gear row and ship `/endpoll` + `PollRecord` as a fast-follow (the handler logic in §5.6 is reusable either way). |
| Ending the poll | `POST {API_BASE}/channels/{channel_id}/polls/{message_id}/expire` with the bot auth headers. Legal because the poll message is authored by our application (Discord only lets you end your own polls). | Documented Discord endpoint; one call. |
| Notifications on RSVP | **None.** No registry changes for RSVP/Manage (verdict DMs etc. all exist already). | An RSVP ping to the organizer is noise at our scale; the hub list and the embed are the ledger. Revisit with reminders (§10). |
| New component prefix | One prefix `event` (`event:rsvp:<pk>`, `event:manage:<pk>`, `event:cancelcard:<pk>`), plus `poll` (`poll:end:<member_pk>`). Registered beside the existing `members` / `create` / `cancel` prefixes. | One handler per concern; `dispatch_component` routes on the pre-colon prefix (`core/events/discord_commands.py:285-287`). |

## 2. What already exists (reuse, don't reinvent)

All verified in code 2026-08-26 on `feat/discord-poll-command` (tip `5e3aaac`).

| Need | Existing thing | Location |
|---|---|---|
| The channel announcement post (bot token, NOT webhook) | `announce_new_events()` → `post_channel_message` | `hub/discord_calendar_posts.py:310-392`, `core/integrations/discord_channel.py:33-41` |
| Editing a bot message in place from the web side | `edit_channel_message` (PATCH keeps message id + pin; PATCHing only `embeds` leaves existing components untouched) | `core/integrations/discord_channel.py:44-51` |
| Component routing by custom_id prefix, link-required funnel | `ComponentHandler`, `register_component`, `dispatch_component` (unlinked → `unlinked_reply` connect prompt) | `core/events/discord_commands.py:89-121, 270-300` |
| In-place message edit on click | `update_message` (type 7) — legal for any application-authored message, incl. public bot posts | `core/events/discord_interactions.py:121-140` |
| Slow-click plumbing | `ack_component_deferred` (type 6) + `send_followup` (PATCH `@original`) | `core/events/discord_interactions.py:215-272` |
| Cancel authority + confirm card + execution | `_cancel_authority`, `_cancel_confirm_card`, `_cancel_component`, `_confirm_cancel` | `membership/discord_commands.py:1473-1613` |
| Edit-page routing per event kind | `can_edit_event` + the detail template's exact URL branch (`hub_guild_event_edit` for guild events, `hub_event_edit` for site-wide) | `membership/permissions.py:88-101`, `templates/hub/event_detail.html:61-67`; member-level checks `member.is_fog_admin` / `member.can_edit_guild` |
| Event page + when/where copy | `event_detail` view + template, `when_display`, `public_url` | `hub/views.py:4306-4334`, `membership/models.py:4922, 4955` |
| Recurring "next occurrence" for announcements | `_next_community_start` | `hub/discord_calendar_posts.py:291-300` |
| Announcement dedupe stamp | `channel_announced_at` + `_stamp` | `membership/models.py:4723-4730`, `hub/discord_calendar_posts.py:303-307` |
| Unique-membership row precedent (constraints naming) | `MeetingAttendee` (`uq_meetingattendee_meeting_member`) | `membership/models.py:6139-6169` |
| Mass-ping gate | `reply()`'s poll branch pins `allowed_mentions: {"parse": []}` | `core/events/discord_interactions.py:109-118` |
| `/poll` message + attribution header | `_poll`, `_poll_header` (member display name in content) | `membership/discord_commands.py:1804-1847` |
| Poll-expire REST base + auth | `API_BASE`, `_auth_headers`, `bot_token` | `core/events/discord_dm.py` |
| Interactions endpoint (types 1/2/3 only) | `discord_interactions` view | `core/views.py:123-155` |
| Event reminders (future RSVP audience) | `send_event_reminders` cron | `core/management/commands/send_event_reminders.py` |

Genuine gaps to close: the `EventRSVP` model + two message-id columns, `post_channel_message` learning `components`/`content`/`allowed_mentions` + returning the message JSON, the embed/field/toggle model methods, the `event` and `poll` component handlers, the `allowed_mentions` passthrough on `update_message`/`send_followup`, the hub template block (+ optional RSVP POST), and the announcer swap-in.

## 3. Where the code lives

```
membership/
  models.py                  # EventRSVP (+ manager), CommunityEvent: 2 announce-message columns,
                             #   discord_announcement_embed(), discord_announcement_components(),
                             #   attendees_field(), toggle_rsvp(), can_manage_from_discord(),
                             #   refresh_discord_announcement(), strip_discord_announcement_buttons()
  migrations/0136_eventrsvp_announce_message_ids.py   # reversible (CreateModel + AddField x2)
  discord_commands.py        # "event" ComponentHandler (rsvp / manage / cancelcard),
                             #   "poll" ComponentHandler (end), gear row added to /poll reply
core/
  integrations/discord_channel.py   # post_channel_message: components/content/allowed_mentions + return JSON
  events/discord_interactions.py    # update_message + send_followup: optional allowed_mentions passthrough
hub/
  discord_calendar_posts.py  # announce_new_events: CommunityEvent branch uses the model embed +
                             #   components, stores channel/message ids
  views.py                   # event_detail context: attendees; NEW event_rsvp POST view (should-have)
  urls.py                    # + events/<pk>/rsvp/ (should-have)
templates/hub/event_detail.html    # attendees block (+ RSVP button, should-have)
tests/
  membership/event_rsvp_spec.py                    # NEW (model + toggle + constraints)
  core/events/event_component_spec.py              # NEW (rsvp/manage/cancelcard clicks)
  core/events/poll_end_spec.py                     # NEW (gear click, expire REST)
  hub/discord_calendar_posts_spec.py               # extended (rich embed, ids stored)
  hub/event_detail_spec.py                         # extended (attendees, RSVP POST)
```

No changes to `core/events/registry.py`, `copy.py`, or the webhook path (`core/events/discord.py`).

## 4. Data model

### 4.1 `EventRSVP` (membership/models.py, near CommunityEvent)

| Field | Type | help_text (required on every field) |
|---|---|---|
| `event` | FK `CommunityEvent`, CASCADE, `related_name="rsvps"` | The published event this RSVP is for. |
| `member` | FK `Member`, CASCADE, `related_name="event_rsvps"` | Who is coming. |
| `created_at` | DateTime `auto_now_add` | When the RSVP was made (drives the display order). |

`Meta`: `ordering = ["created_at"]`, `constraints = [UniqueConstraint(fields=["event", "member"], name="uq_eventrsvp_event_member")]` (the `MeetingAttendee` naming pattern, `membership/models.py:6169`). Meaningful `__str__`: `"<member.display_name> → <event.title>"` style. No `TextChoices` needed (the model has no choice fields — noted explicitly because the house rule demands TextChoices *where choices exist*). **Migration is auto-reversible** (CreateModel + two AddField; reverse drops them). No data migration, so no RunPython.

### 4.2 CommunityEvent additions

| Field | Type | help_text |
|---|---|---|
| `discord_announce_channel_id` | Char(64), blank, default "" | The channel the #public-calendar announcement message was posted to. Blank until announced (or for events announced before v1.10). |
| `discord_announce_message_id` | Char(64), blank, default "" | The announcement message id, so the hub can refresh its Attendees field and a cancel can strip its buttons. Blank until announced. |

### 4.3 Infrastructure signature changes (no migration)

- `post_channel_message(channel_id, embeds, *, content: str = "", components: list | None = None, allowed_mentions: dict | None = None) -> dict` — returns the created message JSON (it already parses errors; today it discards the body and returns `None`). Existing callers (digest, class announcer) pass nothing new and ignore the return.
- `update_message(..., allowed_mentions: dict | None = None)` and `send_followup(..., allowed_mentions: dict | None = None)` — included in the payload only when provided (mirrors `embeds`/`components` handling at `core/events/discord_interactions.py:135-139, 254-258`).

## 5. Business logic (fat models)

### 5.1 Embed construction — `CommunityEvent.discord_announcement_embed()`

One method, one truth, called by the announcer, the RSVP click (type-7 rebuild), and the hub refresh:

- `title` = event title, `url` = `public_url` (the link-to-event-page requirement), `color` = the calendar blue (`0x3D8BD4`, matching `hub/discord_calendar_posts.py:48`).
- `description` = `event.description` truncated to a sane cap (600 chars, "…more on the event page" suffix) when set.
- `fields` (Discord renders field names bold — that IS the bold-headline structure):
  - **Time** — next-occurrence-aware: `"Fri, Aug 29 · 6:00 PM to 8:00 PM"` (start from `_next_community_start`'s logic, promoted onto the model as `next_occurrence_start()` so the method has no hub import; "to", never an en dash — new member-facing copy carries no dashes, unlike the legacy `when_display` at `membership/models.py:4930-4933` which keeps its existing "–" elsewhere).
  - **Duration** — humanized from `ends_at - starts_at`: "2 hours", "90 minutes", "1 hour 30 minutes".
  - **Location** — only when `event.location` is set (the template's own conditional, mirrored).
  - **Repeats** — only for a recurring series: the `get_recurrence_display()` label.
  - **Attendees (N)** — from `attendees_field()` (§5.2).
- `footer.text` = `"RSVP below · Created by <name>"` where name = the creator's Member `display_name` (via `created_by`/`submitted_by` user → member), falling back to `"Past Lives"` when both are None (imported/seeded rows). Embed text can never trigger pings, so names are safe here; the message `content` stays empty.

### 5.2 `attendees_field()` and `toggle_rsvp(member) -> bool`

- `attendees_field()` returns `{"name": f"Attendees ({n})", "value": …}`: the first **15** RSVP display names (creation order), then `"and N more"`; empty state value `"No RSVPs yet. Click RSVP below to be the first."`; defensively truncated under Discord's 1024-char field cap. One query with `select_related("member")`.
- `toggle_rsvp(member)`: `get_or_create(event=self, member=member)`; created → `True` (now going); else delete the row → `False` (no longer going). A concurrent duplicate insert's `IntegrityError` is caught and treated as "already existed". Pure DB, no HTTP — callers decide how to re-render.
- `refresh_discord_announcement()`: best-effort — when both id columns are set, rebuild the embed and `edit_channel_message` (PATCHing only `embeds` leaves the buttons untouched, `core/integrations/discord_channel.py:44-51`); log and swallow `DiscordChannelError` (a hub RSVP must never 500 on a Discord hiccup).
- `strip_discord_announcement_buttons()`: called best-effort from the `/cancel` delete fan-out (`_confirm_cancel`, beside `remove_from_discord()` at `membership/discord_commands.py:1562-1566`) so a cancelled event's message stops inviting clicks; failures logged, never raised (stale clicks also degrade gracefully — §6.2).

### 5.3 `can_manage_from_discord(member) -> bool`

`member.is_fog_admin`, OR (`guild is not None and member.can_edit_guild(guild)`), OR the member's user is `created_by` / `submitted_by`. The first two are byte-for-byte the `/cancel` delete authority (`_cancel_authority`, `membership/discord_commands.py:1480-1484`); the creator clause is this feature's deliberate widening **for the manage card only** — what the creator can actually *do* inside the card is still governed by the existing per-action authorities (§6.3), so no new edit/delete power is minted.

### 5.4 The announcer change (`hub/discord_calendar_posts.py`)

In `announce_new_events()`, the `community_rows` branch (lines 364-374) **plus the shared pending/post loop** change (the pending tuple gains a components/on-posted element; feed rows pass none — the post + stamp happen in the shared loop at 336/376-381, so the loop must thread the new payload through): build `event.discord_announcement_embed()` + `event.discord_announcement_components()` instead of `_announcement_embed(...)`; post via the extended `post_channel_message`; store `discord_announce_channel_id` / `discord_announce_message_id` from the returned message JSON in the same save as the `_stamp`. Feed/class `CalendarEvent` rows keep the compact embed and carry **no** buttons (they have no CommunityEvent row to RSVP against). The cap/overflow/horizon logic is untouched; events stamped silently past the cap simply never get a message (and therefore no buttons) — exactly today's behavior.

### 5.5 The `event` component handler (membership/discord_commands.py, prefix `event`, `requires_link=True`)

Parse `event:<action>:<pk>`; malformed → `error_reply()` (the `members` pattern). Load `CommunityEvent.objects.published().filter(pk=…)` — missing/unpublished → ephemeral "gone" copy (§6.2). Actions:

- **`rsvp`** — past non-recurring event → the "RSVPs are closed" ephemeral reply. Else `toggle_rsvp(member)` then return **type-7** `update_message("", embeds=[event.discord_announcement_embed()])` (components omitted → Discord keeps the existing buttons, `core/events/discord_interactions.py:133`). Fast: two small queries, well inside the 3-second window — no deferred ack needed.
- **`manage`** — authority via `can_manage_from_discord`; unauthorized → the friendly ephemeral refusal (§6.3). Authorized → a **type-4 ephemeral** manage card (never type-7 — that would overwrite the public announcement).
- **`cancelcard`** — the manage card's "Cancel this event" click: re-resolve `_cancel_authority(member, event)` (state may have shifted); `None` → the existing `_CANCEL_NO_AUTH` copy; else return **type-7** `_cancel_confirm_card(event, branch)` (`membership/discord_commands.py:1506-1530`), which edits the ephemeral manage card into the existing confirm card whose buttons (`cancel:confirm:<pk>` / `cancel:keep:<pk>`) route to the existing `_cancel_component` → `_confirm_cancel` (`membership/discord_commands.py:1575-1613, 1544-1572`) — including its type-6 ack + Google/Discord unwind + followup. Zero new cancel logic.

### 5.6 The `poll` component handler (prefix `poll`, `requires_link=True`)

`/poll`'s reply gains one component row: `[{"type": 2, "style": 2, "label": "⚙", "custom_id": f"poll:end:{member.pk}"}]` passed alongside the existing `poll=` payload (`reply()` already accepts both, `core/events/discord_interactions.py:88-118`; the reply's `allowed_mentions` gate is already pinned).

Click handling: parse `poll:end:<creator_pk>`; authorized when `member.pk == creator_pk` or `member.is_fog_admin`; else the friendly ephemeral refusal. Authorized → `ack_deferred(..., ephemeral=True)` (**type 5, NOT type 6**: Discord refuses to edit any message that carries a poll — JSON error 520003 — so a type-6 ack whose `@original` is the poll message would make every followup PATCH fail silently; a type-5 ack makes `@original` a fresh editable ephemeral reply), then `POST {API_BASE}/channels/{interaction['channel_id']}/polls/{interaction['message']['id']}/expire` with `_auth_headers()`; on 2xx, `send_followup(token, content="Poll closed.")` (ephemeral); on non-2xx (already expired, deleted), the followup says "This poll has already ended." Never a stacktrace. **The gear button cannot be removed after the poll ends** (same edit restriction) — an accepted cosmetic cost of plan (a); stale clicks land on the already-ended branch.

## 6. UI / UX — every surface, every state

Discord renders natively (dark/light/mobile are its job). All new member-facing copy below is final and contains no dashes.

### 6.1 The announcement message (public, #public-calendar)

> **Monthly Potluck** *(title, links to the event page)*
> Bring a dish to share and meet the other guilds. *(description, when set)*
> **Time** Fri, Aug 29 · 6:00 PM to 8:00 PM
> **Duration** 2 hours
> **Location** Common Area *(only when set)*
> **Repeats** Every week *(only when recurring)*
> **Attendees (3)** Jo Plaza, Tricia M, Sam K
> *footer:* RSVP below · Created by Tricia M
>
> Buttons: **✅ RSVP** (style 3 success, `event:rsvp:<pk>`) · **⚙ Manage** (style 2 secondary, `event:manage:<pk>`)

States: zero attendees → "No RSVPs yet. Click RSVP below to be the first."; 16+ → first 15 names, "and N more"; no description/location/recurrence → those parts absent, never an empty labeled row (the `/members` card rule). Events announced before v1.10 keep their old compact message (no buttons, no backfill — §10).

### 6.2 RSVP click — all states

| State | Behavior |
|---|---|
| Happy (not yet going) | Row created; type-7 update; your name appears in Attendees, count bumps. That visible change is the confirmation. |
| Happy (already going) | Row deleted; type-7 update; name gone, count drops. Footer's "RSVP below" + the toggle behavior is taught by doing; no confirm dialog on purpose. |
| Unlinked clicker | The existing connect prompt (`unlinked_reply`), ephemeral, with the one-click link button — the deliberate funnel; the handler never runs (`core/events/discord_commands.py:292-294`). |
| Double-click | Two toggles: net un-RSVP; last type-7 render reflects true DB state. Simultaneous clicks by two members: each render re-queries, so the later response shows both changes; the unique constraint guarantees no duplicate rows. |
| Event ended (non-recurring, `ends_at` past) | Ephemeral: "This event has already ended, so RSVPs are closed." Message untouched. Recurring series: always accepted. |
| Event cancelled/gone (stale message) | Ephemeral: "This event is no longer on the calendar." (The cancel path also best-effort strips the buttons, §5.2.) |
| Handler exception | `dispatch_component` converts to `error_reply()` — a fresh ephemeral, never clobbering the public message (`core/events/discord_commands.py:296-300`). |

### 6.3 ⚙ Manage — visible to all, gated on click

| State | Behavior |
|---|---|
| Unauthorized clicker | Ephemeral: "Only the organizer or a guild lead can manage this event. You can see the details here: <event url>". Never a dead end. |
| Authorized (can edit) | Ephemeral card: "**Managing <title>** (Fri, Aug 29 · 6:00 PM)" + buttons: **Edit on the hub** (style 5 link — `hub_guild_event_edit` for a guild event, `hub_event_edit` for site-wide, resolved with `member.can_edit_guild` / `member.is_fog_admin`, the exact mapping of `templates/hub/event_detail.html:61-67`; the reverse MUST be prefixed with `settings.MEMBER_BASE_URL` into an absolute https URL — Discord link buttons reject relative paths — mirroring `public_url`'s pattern at models.py:4962) and **Cancel this event** (style 4 danger, `event:cancelcard:<pk>`, shown only when `_cancel_authority` yields a branch). |
| Authorized creator WITHOUT edit/delete authority (e.g. a plain member whose OPEN-policy event published) | The card is honest: link button **Open the event page** + "Editing and cancelling a published event is handled by a guild lead or admin. Ask a lead if this event needs a change." — the same pre-existing gap `/cancel`'s empty-state copy acknowledges (spec 2026-08-25 §10), not widened and not hidden. |
| Cancel flow | `cancelcard` → the existing confirm card in place (type-7 on the ephemeral card) → existing `cancel:confirm` / `cancel:keep` handlers with all their states (gone / no-auth / keep / type-6 deferred delete + followup). |
| Authority lost between card and click | Re-checked at `cancelcard` AND inside `_cancel_component` — lands on `_CANCEL_NO_AUTH` copy. |
| Unlinked clicker | Connect prompt, as above. |

### 6.4 `/poll` gear

| State | Behavior |
|---|---|
| Asker or fog admin clicks ⚙ | Type-5 ephemeral ack → expire call → the poll renders as ended natively; the clicker gets an ephemeral "Poll closed." (The poll message itself cannot be edited — Discord restriction — so the gear stays; clicking it again lands on "already ended".) |
| Anyone else | Ephemeral: "Only the person who started this poll or an admin can end it." |
| Already ended / poll gone / Discord error | Ephemeral-followup: "This poll has already ended." Logged, never a stacktrace. |
| Unlinked clicker | Connect prompt. |

### 6.5 Hub event detail page

Below the description block (`templates/hub/event_detail.html:45-47`), a "Who's coming" section for authenticated viewers: heading "Who's coming (N)", a simple name list (`event.rsvps` with `select_related("member")` passed from `event_detail`, `hub/views.py:4306-4334`), empty state "No RSVPs yet." Anonymous visitors (the page is public for QR scans) see the count only, no names — directory-privacy caution. **Should-have (included):** a POST form button "I'm going" / "I'm not going" (per current state) → new `event_rsvp` view (`@login_required`, `@require_POST`, `events/<pk>/rsvp/`): resolve the member, `event.toggle_rsvp(member)`, `event.refresh_discord_announcement()` (best-effort), redirect back with a Django message. Blocked for ended non-recurring events with the same honest copy the page already shows (`show_past_note`).

## 7. Notifications / emails / activity

**No notification registry changes.** No new event keys, no channel changes, no copy.py entries — RSVPs are silent by design (§1.3). The webhook broadcast rows (`event.guild_published` / `event.community_published`) are untouched. The only new outbound Discord traffic is the announcer's richer payload, the type-7 edits, the best-effort hub-side PATCH, and the poll expire call — all bot-token REST, all mocked with respx in tests. Mass-ping audit: embed text cannot ping; the two places member names enter message *content* (the poll header already gated at `core/events/discord_interactions.py:115-117`, and the poll-closed followup) both pin `allowed_mentions: {"parse": []}`.

## 8. Build order (phased; each phase ships green — full suite + lint + mypy)

1. **Model layer:** `EventRSVP` + the two id columns + migration (reversible); `toggle_rsvp`, `attendees_field`, `next_occurrence_start`, `can_manage_from_discord`, embed/components builders, `refresh_discord_announcement`, `strip_discord_announcement_buttons`. Specs first.
2. **Transport plumbing:** `post_channel_message` extensions (components/content/allowed_mentions/return JSON), `update_message`/`send_followup` `allowed_mentions` passthrough. Existing digest/class-announcer specs stay green.
3. **Announcer:** the `community_rows` branch swap + id storage in `announce_new_events`; spec the rich payload and the stored ids.
4. **`event` component handler:** rsvp/manage/cancelcard + registration; wire `strip_discord_announcement_buttons` into `_confirm_cancel`'s delete branch.
5. **`/poll` gear + `poll` handler.**
6. **Hub surface:** template block, view context, `event_rsvp` POST endpoint + URL.
7. **Housekeeping + release:** VERSION → 1.10.0, ONE CHANGELOG entry (§11).

**Rollout runbook:** deploy → smoke `/poll` **first** (verifies the poll+components combination live; on rejection, remove the gear row via the named fallback and proceed — the rest of the release is independent) → publish a throwaway event, wait one announcer tick (≤15 min), click RSVP as a linked member, un-RSVP, click ⚙ as a non-lead and as a lead, cancel it from the card → check the hub page attendees. No `register_discord_commands` run is required unless the fallback `/endpoll` ships (no slash-command set changes in plan (a)).

## 9. Testing (BDD `*_spec.py`, `describe_*`/`it_*` — never `context_*`; factory-boy; respx for all Discord HTTP; 100% coverage)

- **`event_rsvp_spec.py`:** toggle create/delete round-trip; unique constraint; `attendees_field` at 0 / 1 / 15 / 16+ (cap copy, count in name, 1024-char guard); `can_manage_from_discord` per tier (admin / lead / staff / creator via `created_by` and via `submitted_by` / plain member); embed field presence rules (location/repeats only when set); footer fallback when creator is None; recurring next-occurrence Time.
- **`event_component_spec.py`:** rsvp click type-7 rebuild (embeds present, components omitted); toggle-then-toggle; ended non-recurring refusal; recurring allowed; gone-event copy; unlinked → connect prompt (dispatch-level); manage card per authority tier incl. the creator-without-authority honest card and the correct edit URL branch; cancelcard → existing confirm card; full manage→cancel→confirm chain hits `remove_from_google`/`remove_from_discord`/`delete` + button strip (respx); double-click and lost-authority races land on friendly copy.
- **`poll_end_spec.py`:** gear row present on `/poll` reply beside the poll payload; creator and admin may end (respx asserts the expire URL + the **type-5 ephemeral** callback + the ephemeral "Poll closed." followup — never a PATCH against the poll message itself); stranger refused; expire non-2xx → "already ended"; malformed custom_id → `error_reply`.
- **`discord_calendar_posts_spec.py` (extended):** community branch posts embed + two buttons and stores both ids; feed rows keep compact embed, no components; cap/horizon behavior unchanged.
- **hub specs:** attendees in context; anonymous sees count only; RSVP POST toggles + calls the best-effort refresh (mocked, and a `DiscordChannelError` never surfaces); ended-event POST refused.
- **Transport specs:** `post_channel_message` includes new keys only when provided and returns parsed JSON; `update_message`/`send_followup` `allowed_mentions` passthrough.

## 10. Open / deferred

- **Reminders × RSVP** — the existing reminder spine (`send_event_reminders`) is untouched; RSVP'd members are noted as a future reminder audience (e.g. a day-before nudge to people who said they're coming) — one resolver + one registry row when wanted.
- **Backfilling buttons onto pre-v1.10 announcements** — not done; old messages stay compact. A one-off command could retro-edit recent ones if demand appears.
- **Per-occurrence RSVPs for recurring series** — an RSVP attaches to the series row, not a date; accepted simplification, revisit if a weekly event's list gets stale (a `starts_at`-scoped RSVP or a periodic reset are the seams).
- **RSVP feedback above the 15-name cap** — a clicker whose name lands in "and N more" only sees the count bump, which is ambiguous if someone else clicks in the same window. Accepted at makerspace scale (most events are well under 15); the seam is an ephemeral "You are on the list" reply sent only when the name will not render.
- **RSVP name privacy on the public embed** — display names are already public-facing in Discord; the hub page hides names from anonymous visitors. A directory-visibility gate on the embed list is a possible tightening.
- **Webhook spine broadcast staying generic** — by construction (not app-owned, no components possible); if the guild-channel copy should ever carry buttons, the seam is routing those events through bot channel posts instead, a separate spec.
- **`/endpoll` + `PollRecord`** — only as the named fallback if the live poll+components smoke test fails.
- **Editing from Discord** — still deferred (the manage card's hub link is the affordance, matching the `/create` spec's stance).

## 11. Versioning & changelog

Bump `plfog/version.py` `VERSION` **1.9.0 → 1.10.0** (net-new member-facing feature). ONE new CHANGELOG entry stamped `1.10.0`:

- **Title:** "RSVP to events right in Discord"
- **Body:** "Event announcements in Discord got a glow up. Each one now shows the time, how long it runs, where it is, and who is coming, with an RSVP button that updates the list live. Click RSVP again to take it back. Organizers and guild leads get a manage button for edits and cancellations, whoever starts a poll can now end it early with the little gear, and the event page on the hub shows who is coming too."

> Spec only — do not build until approved.

---
