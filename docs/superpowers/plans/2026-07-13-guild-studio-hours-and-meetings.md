# Guild Studio Hours & Meetings (app-owned, one-way to Google) — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-13
**Surface:** FOG hub `pastlives.test` — the guild page (`/guilds/<slug>/`) and the guild editor (`/guilds/<pk>/edit/`); plus the guilds guest surface where the guild page is shared. One new management command (admin/ops).
**Related:**
- `docs/superpowers/plans/2026-07-03-google-calendar-event-sync.md` — the FOG→Google push service this reuses, and its **go-live gate** (service account + calendar IDs on prod). Parts B and C are inert until that gate is satisfied.
- Branch of record for all reuse anchors below: **`fix/calendar-feeds-and-picker`**.

> **This is likely its own release.** It is large, it spans model + editor + page + a new command + the Google push path, and Parts B/C cannot be exercised on prod until the Google service account is configured (see §10 go-live gate). Ship Part A alone first if the Google account slips.

---

## 1. Summary

Today a guild lead keeps their guild's studio hours and meetings in a **Google Calendar** and pastes an iCal URL into FOG; the guild page shows a freetext blurb and a monthly "Next Meeting" date. This feature makes **the app the source of truth**: a lead sets their weekly **Studio Hours** and their **Meetings** directly in FOG, on the guild editor, with a proper list editor (add a row, delete a row, save). The guild page grows a new **"Studio Hours"** card — "Come chat with the Guild Lead during these times" — next to "Next Meeting."

Google Calendar becomes strictly **downstream**, touched two ways only:

- **Part B — a one-time seed import** (a manual, re-runnable command) reads the Public Calendar *once* to pre-fill each guild's existing hours/meetings so leads don't start from a blank page.
- **Part C — a one-way publish-out**: when a lead edits hours/meetings in FOG, the change is pushed to the **Public** Google Calendar so it stays a public mirror.

There is **no ongoing Google→app read** for guild hours/meetings after the seed. App → Google, forever after.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| **How to model hours & meetings** | **`CommunityEvent` rows tied to the guild** (see §2.1 tradeoff). Studio Hours = a **new `EventType.STUDIO_HOURS`**; Meetings = the **existing `EventType.GUILD_MEETING`**. *Why:* the guild FK, recurrence, `google_calendar_target`, the whole Google push/sync bookkeeping, calendar display, `.ics`, QR, and echo-dedup already exist on `CommunityEvent` — Parts A/B/C become "wire up existing plumbing," not "build a parallel system." |
| **Weekly cadence** | Net-new. Add `Recurrence.WEEKLY` to **`CommunityEvent.Recurrence` once**; it powers studio hours *and* weekly meetings. Do **not** also add weekly to `Guild.MeetingCadence` — that would be a second, duplicate weekly implementation (the exact trap the brief warns about). |
| **Direction of Google sync** | One-way, app→Google, to the **Public** calendar. Studio Hours are created with `google_calendar_target=PUBLIC` and the lead does not pick a target for them. After the seed, Google is never read again for guild hours/meetings. |
| **Acronym / alias store** ("FIG" has no field today) | A **static alias map** in `membership/calendar_seed.py` (mirrors `membership/logos.py`'s `_NAME_TO_PREFIX`). No new model field, no migration, no UI — it exists only for the one-time importer. `Guild.acronym` as a real field is the alternative, flagged in §10. |
| **Idempotent seed** | New nullable `CommunityEvent.import_source_uid` field, set to the source VEVENT's iCal UID. The command does `update_or_create` keyed on it, so a re-run updates rather than duplicates. |
| **Seed vs cron** | The seed is a **standalone command, never wired into `sync_all_sources`** (the nightly sweep). It is manual and re-runnable. |
| **Announcements/reminders for studio hours** | **Suppressed.** Standing hours are not an event to ping members about; only meetings keep the existing announce + reminder behaviour. |

---

## 2. What already exists (reuse, don't reinvent)

Everything below was confirmed on `fix/calendar-feeds-and-picker`.

| Need | Existing thing | Location |
|---|---|---|
| Event tied to a guild, with recurrence | `CommunityEvent` (guild FK, `EventType`, `Recurrence`) | `membership/models.py:2909` (guild FK `:2984`, `EventType` `:2921`, `Recurrence` `:2926`) |
| Virtual occurrence expansion in a date window | `CommunityEvent.occurrences_in(frm, to)` | `membership/models.py:3138` |
| Emit one RRULE per series for `.ics` + Google | `CommunityEvent.ical_rrule()` | `membership/models.py:3176` |
| Which Google calendar an event targets | `google_calendar_target` (`MEMBER`/`PUBLIC`) | `membership/models.py:3056` (`GoogleCalendarTarget` `:2951`) |
| Push an event to Google (insert/update), never raises, gated | `push_community_event()` → `CommunityEvent.push_to_google()` | `core/integrations/google_calendar.py:197`; model delegator `membership/models.py:3393` |
| Delete an event from Google (best-effort) | `remove_community_event()` → `CommunityEvent.remove_from_google()` | `core/integrations/google_calendar.py:235`; delegator `:3415` |
| Build the Google event body incl. one RRULE + attribution + tz | `_build_event_body()` | `core/integrations/google_calendar.py:161` |
| Both sync gates (env master + admin toggle) + target-id lookup | `push_community_event` reads `SiteConfiguration.public_google_calendar_id` / `google_calendar_sync_enabled` | `core/integrations/google_calendar.py:207-219`; config `core/models.py:277,288` |
| Retry pending/failed pushes (every ~15 min, self-gating) | `retry_calendar_pushes` command + `CommunityEvent.objects.needs_push()` | `core/management/commands/retry_calendar_pushes.py` |
| Reading an iCal feed's RRULE `FREQ` off the master VEVENT | `component.get("RRULE").get("FREQ")` → `['WEEKLY']`/`['MONTHLY']` | `hub/calendar_service.py:88-94` |
| iCal fetch + parse scaffolding | `_fetch_and_parse` / `icalendar.Calendar.from_ical` | `hub/calendar_service.py:124`, `:86` |
| Name→guild fuzzy matching pattern (case-insensitive substring map) | `_NAME_TO_PREFIX` + `logo_prefix_for()` | `membership/logos.py:11`, `:32` |
| Per-guild meeting fields + "Next Meeting" computation | `Guild.meeting_*` + `_compute_next_meeting` + `Guild.next_meeting_at` | `membership/models.py:1139-1170`, `:59-95`, `:1325` |
| Guild page "Meetings"/"Next Meeting" render | freetext section `:205-210`, aside card `:243-253`, stat chip `:114` | `templates/hub/guild_detail.html` |
| Guild editor: Meetings tab + Events tab + per-event add/edit/delete | Meetings `:45-75`, Events `:196-241` | `templates/hub/guild_edit.html`; views `hub/views.py:2658`, `:2716` |
| **Canonical inline list-editor** (extra=0, +Add clone, per-row Delete that saves) | Recurring-hours editor `:280-346`; FAQ `:349-417`; Links `:419-478` | `templates/hub/guild_edit.html` |
| The full-event form (title/time/recurrence/target/reminders) | `CommunityEventForm` | `hub/forms.py:1191` |
| Google sync-state badge partial | `_sync_badge.html` | `templates/hub/partials/_sync_badge.html` |
| Help/`?` tooltip component | `pl-help` / `pl-help__icon` / `pl-help__bubble` | markup `templates/hub/tab_detail.html:8-13`; CSS `static/css/hub.css:1549-1593` |
| Absolute-URL helper for any link | `_absolute_url()` + `MEMBER_BASE_URL` | `membership/orientations.py:32`; `plfog/settings.py:74` |
| Form field / toggle / confirm components | `form_field.html`, `toggle.html`, `confirm_modal.html` | `templates/components/` |

### 2.1 The key architecture decision — `CommunityEvent` rows vs `Guild.studio_hours_*` fields

| | **(A) `CommunityEvent` rows — RECOMMENDED** | **(B) New `Guild.studio_hours_*` fields** |
|---|---|---|
| Google publish-out | **Free** — `push_to_google()`/`remove_from_google()` already target PUBLIC and are gated + best-effort | Bespoke — a whole new push path, or duplicate the service |
| Recurrence engine | Reuse `occurrences_in` + `ical_rrule` (add one WEEKLY branch) | Reimplement weekly expansion + RRULE from scratch |
| Calendar display / `.ics` / QR / echo-dedup | Reuse — studio hours land on the Community Calendar like any event | None of it; a second display path |
| Seed import target | Create rows through the same model the editor writes | Map iCal into ad-hoc fields; meetings still need `CommunityEvent` → two importers |
| Idempotency handle | Add `import_source_uid` to one model | Split across fields; fragile |
| Guild-page rendering | Slightly more work: filter rows by `event_type`, render sections | Simpler render (read a few fields) |
| Gap it introduces | **Weekly recurrence is net-new**; a new `STUDIO_HOURS` type; suppress announce for it | Weekly logic duplicated; no calendar/Google reuse |

**Recommendation: (A).** The only thing (B) buys is marginally simpler page rendering, and it pays for it with a bespoke Google path plus duplicated recurrence logic — both explicitly discouraged. Under (A), **weekly cadence is the one genuinely new thing**, and it is added once, in the recurrence engine both hours and meetings already share.

### Genuine gaps to close (kept small)

1. **`Recurrence.WEEKLY`** — new enum value + a WEEKLY branch in `occurrences_in` and in `ical_rrule` (`FREQ=WEEKLY;BYDAY=<2-letter>`). This is also the "weekly RRULE" the push-out (Part C) needs — `_build_event_body` already emits whatever `ical_rrule()` returns, so **no change to the push service itself**.
2. **`EventType.STUDIO_HOURS`** — new type; the `ck_communityevent_guild_matches_type` constraint must be widened so `studio_hours` (like `guild_meeting`) requires a guild.
3. **`CommunityEvent.import_source_uid`** — nullable field for idempotent seeding.
4. **A weekday+time list editor** — studio hours are lighter than a full event, so a small translating formset (weekday + start/end time + optional location/note ⇄ a WEEKLY `CommunityEvent`) rather than the heavy per-event page.
5. **A guild alias/acronym map** — `membership/calendar_seed.py`, so "FIG Guild Meeting" resolves to Food Independence Guild.
6. **The seed command** — its own VEVENT/RRULE parse (the existing `_parse_ical_events` *expands* recurrence and discards the master RRULE, which the seed needs to detect WEEKLY vs MONTHLY).

---

## 3. Where the code lives

```
membership/
  models.py                     ~  CommunityEvent: +Recurrence.WEEKLY, +EventType.STUDIO_HOURS,
                                     +import_source_uid, widen guild-matches-type constraint,
                                     WEEKLY branch in occurrences_in + ical_rrule,
                                     suppress announce for STUDIO_HOURS;
                                     +Guild.studio_hours() / studio_hours_display() / next_meeting_occurrence()
  calendar_seed.py              +  NEW: alias/acronym map + matcher + classifier + RRULE→Recurrence
                                     mapping used by the seed command (pure functions, unit-testable)
  managers.py (or models.py)    ~  CommunityEventQuerySet: +studio_hours() / meetings() filters
  migrations/00XX_*.py          +  AddField import_source_uid + AlterConstraint (reversible)
  management/commands/
    seed_guild_calendar_events.py +  NEW: the one-time importer (dry-run + report); NOT in cron

hub/
  forms.py                      ~  +StudioHoursForm + StudioHoursFormSet (translating modelformset);
                                     CommunityEventForm recurrence help_text mentions weekly
  views.py                      ~  +guild_studio_hours_save (editor-gated, full-page POST + message)
  urls.py                       ~  +hub_guild_studio_hours_save
  views.py (guild_detail ctx)   ~  pass studio_hours / meetings occurrences to the guild page

templates/hub/
  guild_detail.html             ~  NEW "Studio Hours" aside card (+ pl-help tooltip); Meetings section
                                     surfaces CommunityEvent meetings alongside the freetext
  guild_edit.html               ~  NEW "Studio Hours" list-editor card on the Meetings tab;
                                     Events tab: replace hardcoded "Repeats monthly" with the real label

static/css/hub.css              ~  only if a new layout class is needed (prefer reusing existing tokens)

tests/membership/               +  studio_hours_recurrence_spec.py, studio_hours_model_spec.py,
                                     calendar_seed_spec.py, seed_command_spec.py
tests/hub/                      +  studio_hours_editor_spec.py, guild_page_studio_hours_spec.py
```

Home apps: **`membership`** (model, seed logic, command) and **`hub`** (editor form/view/templates). Both are already in coverage/mypy scope.

---

## 4. Data model

### 4.1 `CommunityEvent` — additions (`membership/models.py`, migration `00XX`)

**New `EventType` value** (append to `EventType`, `:2921`):

| Value | Label |
|---|---|
| `STUDIO_HOURS = "studio_hours"` | "Studio hours" |

**New `Recurrence` value** (append to `Recurrence`, `:2926`):

| Value | Label |
|---|---|
| `WEEKLY = "weekly"` | "Every week" |

`WEEKLY` is deliberately **not** added to `_MONTH_INTERVALS` (`:2957`) — it is not month-anchored. It is branched *before* that dict is consulted in both `occurrences_in` and `ical_rrule` (see §5.1), exactly as `SEMI_MONTHLY`/`YEARLY` are special-cased today.

**New field:**

| Field | Definition | Note |
|---|---|---|
| `import_source_uid` | `CharField(max_length=500, blank=True, default="", help_text="The iCal UID of the Public-Calendar VEVENT this row was seeded from. Set only by the one-time seed importer; used to re-run it without duplicating. Blank for app-authored rows.")` | Only the seed command writes it. |

**Widen the guild↔type constraint** (`ck_communityevent_guild_matches_type`, `:3118`). Today: `guild_meeting ⟺ guild not null`. New: `studio_hours` also requires a guild.

```
condition = (
    (Q(event_type__in=["guild_meeting", "studio_hours"]) & Q(guild__isnull=False))
    | (~Q(event_type__in=["guild_meeting", "studio_hours"]) & Q(guild__isnull=True))
)
```

`ck_communityevent_end_after_start` (`:3113`) is unchanged and still applies to studio-hours anchors.

**Migration:** one migration —
- `AddField` `import_source_uid` (auto-reversible),
- `AddIndex`/`AlterField` for the `EventType`/`Recurrence` choice additions (choice-only, no column change, still recorded),
- `RemoveConstraint` + `AddConstraint` for `ck_communityevent_guild_matches_type` (both operations reversible; the reverse re-adds the old two-branch form).

No `RunPython` and no data migration — studio hours are net-new, existing rows keep `import_source_uid=""` by default. (If any backfill is ever added, it must ship a real reverse function per CLAUDE.md — not `RunPython.noop`.)

### 4.2 `Guild` — no schema change

No `Guild.acronym` field (the alias map lives in code — §5.3). No weekly cadence added to `Guild.MeetingCadence`. The only additions are **fat-model read helpers** (§5.2).

### 4.3 Manager filters (`CommunityEventQuerySet`)

| Method | Returns |
|---|---|
| `studio_hours()` | `.filter(event_type=CommunityEvent.EventType.STUDIO_HOURS)` |
| `meetings()` | `.filter(event_type=CommunityEvent.EventType.GUILD_MEETING)` |

Used by the editor formset queryset and the guild-page context.

---

## 5. Business logic (fat models)

### 5.1 Recurrence engine — the WEEKLY branch

**`occurrences_in(frm, to)`** (`membership/models.py:3138`) — add a branch immediately after the `NONE` early-return, before the monthly machinery:

- Take the anchor's localtime weekday and time-of-day.
- Walk from `max(anchor_date, frm)`, advancing 1 week at a time, appending each occurrence whose date is in `[frm, to]` and `>= anchor_date`, preserving the anchor's time-of-day (so duration is preserved by the caller, exactly like the monthly path).
- DST is handled by rebuilding each occurrence via `local_start.replace(year, month, day)` in local time (mirrors the existing monthly code at `:3172`).

**`ical_rrule()`** (`membership/models.py:3176`) — add, before the `_MONTH_INTERVALS` lookup:

```
if self.recurrence == self.Recurrence.WEEKLY:
    return f"FREQ=WEEKLY;BYDAY={weekday}"   # weekday = MO/TU/…/SU from local_start
```

That single line is the "**weekly RRULE addition**" Part C needs. `_build_event_body` (`core/integrations/google_calendar.py:179`) already does `body["recurrence"] = ["RRULE:" + ical_rrule()]`, so once `ical_rrule()` returns `FREQ=WEEKLY;…`, the push, the `.ics` export, and the calendar expansion all speak weekly with **no further push-service change**.

> Scope guard: only `FREQ=WEEKLY` (every week). Biweekly/`INTERVAL=2` studio hours are deferred (§10).

### 5.2 `Guild` read helpers (for the page)

| Method | Behaviour | Side effects |
|---|---|---|
| `studio_hours()` | `self.events.studio_hours()` ordered by weekday-of-anchor then time — the STUDIO_HOURS rows for this guild. | none |
| `studio_hours_display()` | Returns a list of small dicts `{weekday_label, time_range, location, note}` derived from each STUDIO_HOURS row's anchor (`starts_at`/`ends_at` local time) — what the page card renders. Empty list ⇒ card shows the empty state. | none |
| `next_meeting_occurrence()` | The soonest upcoming meeting datetime across **(next GUILD_MEETING `CommunityEvent` occurrence)** and the legacy **`self.next_meeting_at`**, so weekly meetings entered as events surface in the "Next Meeting" card without duplicating recurrence logic. Falls back to `next_meeting_at` when there are no meeting events. | none |

All three are cheap, read-only `@property`/method reads — no queries in a loop on the page (the view prefetches `events`).

### 5.3 Seed matching/classification (`membership/calendar_seed.py`, NEW — pure functions)

Mirrors `logos.py`: a module of constants + small pure functions, unit-tested without a DB.

```
# Case-insensitive alias/acronym → the substring that identifies a guild.
# Supplements a plain name-substring match; "FIG" is the motivating case.
_GUILD_ALIASES: dict[str, str] = {
    "fig": "food independence",     # "FIG Guild Meeting" → Food Independence Guild
    # … add as the real Public Calendar reveals them during a dry-run
}

# Title keywords → event type. Checked in order; first hit wins.
_STUDIO_HOURS_KEYWORDS = ("studio hours", "open studio", "shop hours")
_MEETING_KEYWORDS = ("meeting", "guild meeting")
```

| Function | Signature | Behaviour |
|---|---|---|
| `guild_for_title(title, guilds)` | `(str, Iterable[Guild]) -> Guild \| None \| AmbiguousMatch` | Lowercase the title; a guild matches if **its name** (or one of its `_GUILD_ALIASES` fragments) appears as a substring. Exactly one match → that guild; zero → `None`; more than one → an `AmbiguousMatch` sentinel so the command reports it and skips (never guesses). |
| `classify_type(title)` | `(str) -> EventType \| None` | `_MEETING_KEYWORDS` → `GUILD_MEETING`; else `_STUDIO_HOURS_KEYWORDS` → `STUDIO_HOURS`; else `None` (reported "unclassified", skipped). |
| `recurrence_for_rrule(rrule)` | `(dict \| None) -> Recurrence` | Read `FREQ` (+ `INTERVAL`) off the master VEVENT's RRULE (pattern from `hub/calendar_service.py:88-94`): `WEEKLY`→`WEEKLY`; `MONTHLY` + INTERVAL 1/2/3 → `MONTHLY`/`EVERY_2_MONTHS`/`EVERY_3_MONTHS`; `YEARLY`→`YEARLY`; no RRULE → `NONE`. Unmapped FREQ → `NONE` (one-off), reported. |

These are deliberately dependency-light so the command is thin.

### 5.4 The seed command (`membership/management/commands/seed_guild_calendar_events.py`, NEW)

`BaseCommand`, lazy imports, self-contained — the shape of `retry_calendar_pushes.py`.

**Args:** `--ical-url <url>` **or** `--feed <CalendarFeed name>` (pull its `ical_url`) — one is required; fail loudly if neither (per "fail loudly, no magic defaults"). `--dry-run` prints the report and writes nothing.

**Flow:**
1. Fetch + parse the feed with the command's **own VEVENT walk** (`icalendar.Calendar.from_ical`), reading each master VEVENT's `SUMMARY`, `DTSTART`, `DTEND`, `LOCATION`, `DESCRIPTION`, `UID`, and `RRULE`. (It must NOT reuse `_parse_ical_events`, which expands occurrences and drops the RRULE the seed needs.)
2. For each VEVENT:
   - `guild = guild_for_title(summary, guilds)`. `None` → skip ("no guild match"); `AmbiguousMatch` → skip ("ambiguous: G1/G2"); else continue.
   - `event_type = classify_type(summary)`. `None` → skip ("unclassified").
   - `recurrence = recurrence_for_rrule(rrule)`.
   - Build the `CommunityEvent` defaults: `guild`, `event_type`, `title`, `starts_at`/`ends_at` (from DTSTART/DTEND, tz-normalised to Portland), `location`, `description`, `recurrence`, `google_calendar_target=PUBLIC`, `moderation_state=PUBLISHED`, and `import_source_uid=uid`.
   - **Idempotency:** `CommunityEvent.objects.update_or_create(import_source_uid=uid, defaults=...)`. Re-running matches the same UID and updates in place.
   - Record an action: `create` / `update` / `skip(reason)`.
3. Print a table: `source title → matched guild → type → recurrence → action`, then counts (`N created, N updated, N skipped`).
4. **Never pushes to Google itself** — seeding only writes FOG rows. Publish-out happens when a lead next saves (Part C), or via the existing `retry_calendar_pushes` tick once the row is `PENDING`. (Open question §10: whether the seed should also mark rows `PENDING` to auto-mirror, or leave them FOG-only until a lead touches them. Default: leave `IDLE`/unpushed so seeding is a pure read of Google, never an immediate write back.)

**Not wired into `sync_all_sources`** (`hub/calendar_service.py:277`). It is invoked by hand.

### 5.5 Studio-hours save (`hub/views.py` — thin)

`guild_studio_hours_save(request, pk)` — editor-gated via the existing `_require_can_edit_guild` (same guard the FAQ/Links/hours saves use). POST only.
- Bind `StudioHoursFormSet(request.POST, queryset=guild.events.studio_hours())`.
- On valid: for each deleted row, call `instance.remove_from_google()` **before** delete (Part C); for each saved row, the form's `save()` materialises the WEEKLY `CommunityEvent` (§5.6) and then calls `instance.push_to_google()` (best-effort, gated). Add a Django `messages.success` ("Studio hours saved."), redirect back to `?tab=meetings`.
- Business logic (anchor computation, PUBLIC target, announce suppression) lives on the form/model — the view only orchestrates.

### 5.6 `StudioHoursForm` translation + announce suppression (fat model/form)

- **Announce suppression:** `CommunityEvent.announce` (`:3320`) is driven by `_ANNOUNCE_EVENT` (`:2967`). `STUDIO_HOURS` is simply absent from that map, and `announce`/the new-event announce path must **no-op for STUDIO_HOURS** (guard: if `event_type == STUDIO_HOURS`, skip announcing and skip reminder scheduling). Standing hours never ping members.
- **`StudioHoursForm` (a `ModelForm` on `CommunityEvent`)** with *declared* friendly fields `weekday` (Mon–Sun select), `start_time`, `end_time`, `location`, `note`, plus hidden `id`/`DELETE`. Its `save()` translates:
  - `starts_at` = the next occurrence of `weekday` at `start_time`, in Portland local time, made aware (if this week's slot already passed, anchor to next week).
  - `ends_at` = same date at `end_time`.
  - `recurrence = WEEKLY`, `event_type = STUDIO_HOURS`, `guild = <fixed>`, `google_calendar_target = PUBLIC`, `title = f"{guild.name} Studio Hours"`, `description = note`, `location = location`.
  - On **edit**, initial `weekday`/`start_time`/`end_time`/`location`/`note` are derived back from the instance's anchor.
  - `clean()`: `end_time` strictly after `start_time` ("End time must be after start time."); `weekday`/`start_time`/`end_time` required.

---

## 6. UI / UX — completeness checklist applied per screen

### Screen A — Guild page "Studio Hours" card (`templates/hub/guild_detail.html`, changed)

- **Container:** a new `hub-card` in the right-hand `<aside>`, immediately **before** the existing "Next Meeting" card (`:243`), so the two read as a pair.
- **Header + tooltip:** the card heading is "Studio Hours" followed by the `?` help affordance, reusing the existing component verbatim:
  ```
  <span class="pl-help" tabindex="0" aria-label="What are studio hours?">
    <span class="pl-help__icon" aria-hidden="true">?</span>
    <span class="pl-help__bubble" role="tooltip">Come chat with the Guild Lead during these times.</span>
  </span>
  ```
  The bubble text is **exactly**: `Come chat with the Guild Lead during these times.`
- **Body:** iterate `guild.studio_hours_display()` — one line per block: `Tuesdays · 6:00–9:00 PM` then, when set, `· Studio B` and the note on a muted second line.
- **Meetings section (existing):** the main-column "Meetings" section (`:205`) keeps the freetext `meeting_schedule` when present, and the "Next Meeting" card now reads `guild.next_meeting_occurrence()` so a **weekly meeting** entered as an event shows here too. (Consolidating/deprecating `Guild.meeting_*` entirely is deferred — §10.)
- **States:**
  - *Empty:* "No studio hours set yet." (muted) — never a bare blank card. (Mirrors the guild page's existing "Nothing here yet." / "TBA" placeholders.)
  - *Success/loading/error:* this is read-only render; no interaction here.
- **Dark + light:** theme tokens only (`--hub-card-bg`, `--hub-text-muted`, `--hub-border`); the `pl-help` bubble already themes in `hub.css`. **Verify both themes.**
- **Mobile:** the aside stacks under the main column at narrow width (existing `pl-guild-grid` behaviour); each hours line wraps, no fixed widths.
- **Guest surface:** the card renders on the shared guilds guest page as read-only text (no editor), same as the other guild-page sections.

### Screen B — Studio-hours **list editor** (`templates/hub/guild_edit.html`, Meetings tab, changed) — the UX-gate centerpiece

A new `hub-card` on the **Meetings** tab (`x-show="section === 'meetings'"`), its **own `<form>`** posting to `hub_guild_studio_hours_save` (never nested inside the main form), built exactly like the recurring-hours editor at `:280-346`.

- **Components:** `form_field.html` for every field (weekday select, start/end time, location, note); `{{ formset.management_form }}`; per-row `<template>` clone for +Add.
- **Fields per row:** `weekday` (select) · `start_time` · `end_time` · `location` (optional) · `note` (optional), laid out in a `flex-wrap` row with min-widths (copy `:291-296`).
- **The three required controls (per checklist §1):**
  - **"+ Add studio hours" button** — clones the hidden `studio-hours-empty-template`, swaps `__prefix__`, bumps `id_studio_hours-TOTAL_FORMS` (verbatim JS from `:400-411`). `extra=0` so no perpetual blank row blocks Save.
  - **Per-row Delete** — a real `pl-btn pl-btn--danger pl-btn--sm` button, `margin-top:0.75rem`, that for a **saved** row flips the hidden `{{ f.DELETE }}` and `this.form.requestSubmit()` (preserving other edits), and for an **unsaved cloned** row just `this.closest('.hub-card').remove()`. **Never a toggle.** (Copy `:298-305`.) No confirm modal — editable-list deletes auto-save (per FRONTEND.md *Editable Lists*).
  - **Save** — a primary `pl-btn pl-btn--primary` "Save Studio Hours" beside the +Add button (FAQ/hours footer rhythm, `:329-344`). Full-page POST → Django `messages.success` + redirect to `?tab=meetings`.
- **States:**
  - *Empty:* row-level `{% empty %}` → "No studio hours yet — add your first window."
  - *Success:* green Django message on redirect ("Studio hours saved.").
  - *Error:* form validation ("End time must be after start time.") renders inline via `form_field.html`; a failed Google push is **not** an error here — it degrades silently to a recorded `sync_error` and the save still succeeds (best-effort, §Part C).
  - *Sync badge:* when `google_sync_enabled`, render `_sync_badge.html` per row (as the Events tab does at `:220-224`) so a lead can see PUBLIC-mirror status.
- **Dark + light:** all controls go through `form_field.html`, which scopes them under the hub field wrapper — **no inline `background`/`color` on any input**. The `start_time`/`end_time` `type="time"` inputs inherit the existing hub time-input theming used by the recurring-hours editor (picker-icon invert on dark, reset on light). **Verify both themes.**
- **Mobile:** the row `flex-wrap`s; delete button clears the field above via its `margin-top`. 8px-grid spacing throughout.
- **No `display` in inline style on any `x-show` element** — the tab wrapper follows the existing `x-show="section === 'meetings'"` pattern which uses `x-cloak`, not inline display.

### Screen C — Meetings gain weekly (`hub/forms.py` + `templates/hub/guild_edit.html`, Events tab, changed)

- `CommunityEventForm.recurrence` (`hub/forms.py:1212`) automatically shows the new **"Every week"** option once the model enum has it (ModelForm reads model choices). Only the help_text copy is updated to mention weekly.
- The Events tab list currently hardcodes **"Repeats monthly"** (`templates/hub/guild_edit.html:213`). Replace with the real label so a weekly meeting reads "Repeats weekly": `{% if event.recurrence != 'none' %} · {{ event.get_recurrence_display }}{% endif %}`.
- Everything else on the Events tab (Add/Edit per-event page, delete confirm modal, sync badge) is unchanged — meetings keep the heavier per-event flow because a full meeting has 12+ fields (title/time/reminders/announce), which per the FRONTEND.md interaction table belongs on a dedicated page, not an inline formset.

---

## 7. Notifications / emails / activity

- **Studio hours:** **no notifications, no emails, no reminders** — announce and reminder scheduling are suppressed for `STUDIO_HOURS` (§5.6). Standing hours are ambient, not events to ping.
- **Meetings:** unchanged — they keep the existing `event.guild_published` announcement and the opt-in 7/3/1-day reminders (`CommunityEvent.announce` / `REMINDER_OFFSETS`).
- **Seed command:** silent to members — it writes rows and prints a console report only. No `emit()`, no Discord.
- No new `NotificationTemplate` rows, so `seed_notification_templates` is not implicated by this feature (the earlier Google-sync feature already seeds the event templates).

---

## 8. Build order (phased; each phase ships green)

Each phase is independently green (full suite + `ruff format` + `ruff check` + `mypy`, run in the `plfog-web` Docker image).

1. **Model + recurrence engine (no UI).** `Recurrence.WEEKLY` (+ `occurrences_in`/`ical_rrule` branches), `EventType.STUDIO_HOURS`, `import_source_uid`, widen the guild-matches-type constraint, migration (reversible), announce-suppression for `STUDIO_HOURS`, `CommunityEventQuerySet.studio_hours()/meetings()`, `Guild.studio_hours()/studio_hours_display()/next_meeting_occurrence()`. Specs for every branch. *Ships green; nothing visible yet.*
2. **Part A — in-app management UI.** `StudioHoursForm`/`FormSet`, `guild_studio_hours_save` view + URL, the Meetings-tab list editor (Screen B), the guild-page Studio Hours card + tooltip (Screen A), the Events-tab weekly label + form help_text (Screen C). Specs for form validation, view gating, template states. *Ships green; hours are app-owned and editable — no Google yet.*
3. **Part C — publish-out.** Force `google_calendar_target=PUBLIC` on studio-hours rows; wire save→`push_to_google()` and delete→`remove_from_google()`; per-row sync badge. (No push-service change — the WEEKLY RRULE came from Phase 1.) Specs against a mocked Google client (never a live call), covering gated/off, insert vs update, weekly RRULE in the body, delete propagation, never-raises. *Ships green; inert on prod until the Google go-live gate.*
4. **Part B — seed importer.** `membership/calendar_seed.py` (matcher/classifier/RRULE map) + `seed_guild_calendar_events` command (dry-run + report + idempotent `update_or_create` on `import_source_uid`). NOT in cron. Specs for matching (name/alias/ambiguous/none), classification, WEEKLY-vs-MONTHLY detection, idempotency, dry-run writes nothing, report output — all against fixture iCal bytes (no network). *Ships green.*
5. **Housekeeping.** Bump `plfog/version.py` `VERSION` (from `0.21.15`); add one member-facing `CHANGELOG` entry (below). Confirm both themes on Screens A/B. Cross-link the go-live gate.

**CHANGELOG (member-facing, one grouped entry — new feature):**
> **Studio Hours on guild pages** — Guild leads can now set their weekly studio hours and meetings right in the app, and every guild page shows a "Studio Hours" card so you know when to drop by and chat with the lead. Meetings can now repeat weekly, not just monthly. Your guild's public Google Calendar stays in sync automatically.

> Spec only — do not build until approved.

---

## 9. Testing

BDD `*_spec.py` in `tests/membership/` and `tests/hub/` (matching the existing `community_event_*_spec.py` / `guild_next_meeting_spec.py` layout — this repo keeps specs in top-level `tests/<app>/`, not app-local `spec/`), `describe_*`/`it_*` only (`context_*` is not collected), factory-boy for data, ≥98% branch coverage, run in the `plfog-web` Docker image (`--no-cov` for a subset).

**Model — recurrence (`studio_hours_recurrence_spec.py`):**
- `occurrences_in` WEEKLY: yields every 7 days within the window; respects `anchor_date` lower bound and both window edges; preserves time-of-day; a **DST-crossing** week keeps wall-clock time.
- `ical_rrule` WEEKLY → `FREQ=WEEKLY;BYDAY=<correct 2-letter>` for each weekday; monthly/yearly/semi-monthly outputs unchanged (regression).

**Model — studio hours (`studio_hours_model_spec.py`):**
- The widened constraint: a `STUDIO_HOURS` row with `guild=None` raises; with a guild saves; a `community` row with a guild still raises.
- Creating a `STUDIO_HOURS` row does **not** announce and does **not** schedule reminders; a `GUILD_MEETING` row still does.
- `Guild.studio_hours_display()` shape + ordering; empty list when none.
- `Guild.next_meeting_occurrence()` prefers the sooner of a weekly meeting event vs the legacy cadence; falls back to `next_meeting_at`.

**Publish-out (`studio_hours_sync_spec.py`, mocked client):**
- Save of a studio-hours row calls `push_to_google` with `target=PUBLIC` and a body whose `recurrence` is `["RRULE:FREQ=WEEKLY;BYDAY=…"]`.
- Off/gated → records `PENDING`, save still succeeds; API error → `FAILED`, save still succeeds (never raises).
- Deleting a saved row calls `remove_from_google` before delete.

**Editor (`studio_hours_editor_spec.py`):**
- Form maps weekday+start/end → correct anchor `starts_at`/`ends_at` (incl. "slot already passed this week → next week"); round-trips back to initial on edit.
- `end_time <= start_time` → validation error; missing weekday/time → required errors.
- View: non-editor → 403; editor POST saves + redirects to `?tab=meetings` with a message; a flipped `DELETE` removes the row (and, mocked, calls `remove_from_google`).

**Guild page (`guild_page_studio_hours_spec.py`):**
- Section renders each hours line; empty state text; the tooltip bubble contains the exact copy; weekly meeting appears in "Next Meeting."

**Seed (`calendar_seed_spec.py` pure + `seed_command_spec.py` DB, fixture iCal):**
- `guild_for_title`: name match, alias/acronym ("FIG" → Food Independence), ambiguous → sentinel, none → `None`.
- `classify_type`: meeting vs studio-hours keywords; unclassified → `None`.
- `recurrence_for_rrule`: WEEKLY/MONTHLY/every-2/every-3/yearly/none; unmapped FREQ → NONE.
- Command: creates rows for matched+classified VEVENTs; skips + reports ambiguous/no-match/unclassified; **idempotent** (second run updates, count of rows unchanged); `--dry-run` writes nothing; requires `--ical-url`/`--feed` (errors without); report lists actions. All against fixture bytes — **no network**.

**Gotchas to assert around:** all anchor/`occurrences_in` math in Portland local time; weekly DST; the "what's new" widget echoes the CHANGELOG on every page, so assert on markup (the tooltip's `role="tooltip"` text, button hrefs) not on incidental visible copy.

---

## 10. Open / deferred / out of scope

**Open questions (resolve before/while building):**
1. **Alias store — map vs field.** Recommended: static `_GUILD_ALIASES` map (YAGNI for a one-time importer, zero migration, matches `logos.py`). Add a real `Guild.acronym` field instead **only if** acronyms are also wanted on the guild page/badge — flag if so.
2. **Meeting-vs-studio-hours keyword source.** Recommended: module constants in `calendar_seed.py`. A Site-Settings-configurable keyword list is heavier than a one-time seed warrants.
3. **A seeded row a lead later deletes.** With `update_or_create` on `import_source_uid`, re-running the seed **resurrects** a deleted row. Recommendation: treat the seed as run-once-early (before leads curate); document that clearly, or (deferred) track deleted UIDs to skip them on re-run.
4. **Multiple studio-hours blocks per guild / multiple ranges per day.** Supported as **multiple rows** in the formset (Tue 6–9 *and* Sat 10–2 = two rows). Multiple ranges packed into one row is out of scope.
5. **Should seeded rows auto-mirror to Google?** Default: no — the seed only reads Google and writes FOG rows; the PUBLIC mirror is (re)written when a lead next saves, or by `retry_calendar_pushes` if the seed marks them `PENDING`. Confirm whether to mark them `PENDING` at seed time.
6. **Public-calendar iCal source for the seed.** `--ical-url`/`--feed` arg (recommended, explicit) vs deriving an iCal URL from `SiteConfiguration.public_google_calendar_id`. The stored config value is a **Calendar ID for the API**, not an iCal URL — hence the explicit arg.
7. **Default Google target for guild *meetings*.** Studio hours are forced PUBLIC. Meetings keep the lead's picker (model default `MEMBER`). Given Google is now a public mirror, consider defaulting new guild meetings to PUBLIC — confirm.

**Deferred / out of scope:**
- **Biweekly / every-N-weeks studio hours** (`INTERVAL=2`). WEEKLY only for now.
- **Deprecating `Guild.meeting_*` / `MeetingCadence` and fully consolidating meetings onto `CommunityEvent`.** This spec keeps the legacy cadence fields and only *surfaces* event-based meetings alongside them; a full migration of existing cadence data into meeting events is a separate project.
- **Two-way sync / editing hours inside Google.** One-way, app→Google, forever. An edit made directly in Google is not pulled back.
- **Per-occurrence exceptions** (skipping one week of studio hours) — edits affect the whole weekly series, consistent with the rest of `CommunityEvent`.
- **All-day studio hours.** `CommunityEvent` is always timed; hours carry a start/end time.

**Prod dependency (Parts B & C):** the Google **service account must be configured on prod** — `GOOGLE_SERVICE_ACCOUNT_JSON` + `GOOGLE_CALENDAR_SYNC_ENABLED=true` on Render, the Public calendar shared with the service account ("Make changes to events"), and `SiteConfiguration.public_google_calendar_id` set with "Push events to Google Calendar" on. See the **go-live checklist** in `docs/superpowers/plans/2026-07-03-google-calendar-event-sync.md` (§10, lines 647-654). Until then, Part A is fully usable; Parts B/C are built and green but inert.

## 11. Review addendum — fold in before building

An adversarial UX review confirmed the engine-level `Recurrence.WEEKLY` / `EventType.STUDIO_HOURS` coverage is sound (WEEKLY branches before `_MONTH_INTERVALS`; STUDIO_HOURS correctly kept off `publish()`/`announce()`). But eight gaps in the editor wiring, page coherence, and display blast radius:

1. **[SEV1] The Studio Hours editor will nest inside the main `<form>` → "Save does nothing."** The Meetings block (`guild_edit.html:46-75`) is *inside* the main form (`:20`→`:103`); the recurring-hours editor the spec copies (`:280-346`) is on the Orientations tab *after* `</form>`. The spec must place the Studio Hours card in a SECOND `<div x-show="section === 'meetings'">` AFTER `:103` (the pattern Basic-tab Share/Danger-Zone already use at `:106`/`:141`), or it hits [[reference_nested_form_save_bug]].
2. **[SEV2] Two conflicting "Next Meeting" values on the guild page.** Screen A switches only the aside card (`guild_detail.html:243-253`) to `next_meeting_occurrence()`, leaving the stat chip (`:114`) on legacy `next_meeting_at` → two different dates. Also: the card's detail line still reads legacy `meeting_time`/`meeting_location` (won't match an event date), and `next_meeting_occurrence()` returns a **datetime** while the templates assume a **date**. Reconcile both surfaces.
3. **[SEV3] "Repeats monthly" is hardcoded in 3 places; spec fixes 1.** Besides `guild_edit.html:213`, the same literal is at `community_calendar.html:274` and `:300` — a WEEKLY row renders "Repeats monthly" there. Use `get_recurrence_display` in all three.
4. **[SEV4] Studio hours flood every event surface, mislabeled, with no opt-out.** `community_event_entries` (`calendar_entries.py:108-143`) has no event_type filter → every WEEKLY studio-hours row expands weekly onto the community + guild calendars AND into every subscriber's `.ics` (`calendar_export_ics`, `views.py:3443`); the home "Upcoming" widget labels them `kind="Meeting"` (`home.py:117`). Decide filtering/labeling — this is a real product call the spec waved through as "free reuse."
5. **[SEV5] Migration reverse fails once a studio-hours row exists.** The old `ck_communityevent_guild_matches_type` branch requires `~guild_meeting ⟹ guild IS NULL`; a `studio_hours` row has a guild, so the down-path `ADD CONSTRAINT` is rejected on a populated table. Note the caveat / guard the reverse.
6. **[SEV6] "Like the recurring-hours editor" hides two steps.** `StudioHoursForm`'s `weekday/start_time/end_time/note` are declared NON-model fields, so (a) the time inputs won't render `type="time"` (no picker, no dark-mode invert) unless the widgets are declared explicitly, and (b) as a *modelformset* (not inlineformset) the guild FK needs `form_kwargs={"guild": guild}`. Spec both.
7. **[SEV7] Seed re-run stomps lead edits.** `update_or_create(import_source_uid=uid, defaults=<all fields>)` overwrites every field on a still-existing seeded row → a lead's edits are silently lost on the next run. And `import_source_uid` has no unique constraint, so the key can `MultipleObjectsReturned`. Add the unique constraint + don't overwrite lead-editable fields on re-seed (or only create-if-absent).
8. **[SEV8 minor]** `guild_detail` prefetch (`views.py:477-480`) doesn't include `events` → N+1 on `studio_hours_display()`/`next_meeting_occurrence()`. And the "skip reminder scheduling" guard describes code that doesn't exist (reminders are pull-based from the cron keyed on `remind_*` toggles) — instead spec a TEST that a `STUDIO_HOURS` row yields zero reminder/happening-now occurrences.

> Spec only — do not build until approved.
