# Event Announcement Scheduling & Reminders — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-12
**Surface:** FOG hub `pastlives.test` — the community-event create/edit form (`templates/hub/community_event_edit.html`), reached from a guild's **Events** tab and the Community Calendar **Events** tab. No book-CMS surface.
**Related:**
- `docs/superpowers/plans/2026-07-12-event-pages-and-qr-codes.md` (Feature B — per-event detail pages; this feature links its reminder/announcement copy at `CommunityEvent.absolute_url`, which Feature B repoints from the calendar to the event page).
- `docs/superpowers/plans/2026-04-15-community-calendar.md` and `docs/superpowers/plans/2026-06-25-community-events-and-meetings.md` (the `CommunityEvent` model + calendar this rides on).
- `docs/superpowers/plans/2026-07-03-google-calendar-event-sync.md` (the `publish()` → `push_to_google()` sync path a scheduled publish defers).
- The **announcements wizard** sibling (pure member-email/announcement composer). Boundary is explicit: the wizard unifies *pure announcements*; **event** announce/reminder options stay inline on the event form, described here.

---

## 1. Summary

Today, creating a community event fires its "new event" announcement the instant you save it — and that's the only knob. This feature gives the event's creator three opt-in controls, all inline on the event form:

1. **Schedule the announcement** — announce now (default, as today) *or* pick a future date/time and the event quietly waits until then to go live (announce + push to Google).
2. **Reminder pings** — turn on a nudge **7 / 3 / 1 days before** the event starts (each an independent toggle, all off by default).
3. **A "happening now" ping** — a single ping when the event starts (off by default).

A guild lead planning next month's Forge Night can line up the announcement for Monday morning and a 1-day reminder, and never touch it again. Everything rides the existing 15-minute scheduler and its dedupe — no new cron, no new delivery plumbing.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Where the controls live | **Inline on the event create/edit form**, not the announcements wizard (the wizard is for pure announcements). |
| Scheduled-publish state | A **new `SCHEDULED` moderation state**. The event exists but is not announced/pushed until `publish_at` arrives — cleaner and more visible than a hidden `published=False` flag, and it slots into the existing `ModerationState` machine. |
| Reminder shape | **Four booleans** — `remind_7d` / `remind_3d` / `remind_1d` / `notify_happening_now`. Simplest thing that renders as four toggles; no offsets table to manage. |
| Defaults | Announce **now** (blank `publish_at`); all reminders **off**; happening-now **off**. Opt-in, quiet by default. |
| Approval vs scheduling | A member proposal is **approved first, then scheduled**. `approve()` routes to `SCHEDULED` if `publish_at` is future, else publishes now — a scheduled publish can never bypass review. |
| Reminder anchor | **`event.starts_at`** (the single/first concrete start). Per-occurrence reminders for a recurring series are **out of scope for v1** (§10) — recurring events still get their launch announcement. |
| Reminder audience | **Same audience the launch announcement reached**, via one new switching resolver (guild event → guild members; leadership meeting → all guild leads; other site-wide → all active members). One `event.reminder` key, one `event.happening_now` key — not three of each. |
| Reminder channels | `event.reminder`: in-app **on**, email **off**, Discord **off** (bell is enough; per-offset Discord posts would clutter the guild channel). `event.happening_now`: in-app **on**, email **off**, Discord **on** (a one-shot "starting now" in the channel is genuinely useful). Copy is authored for `IN_APP` + `EMAIL` (Discord inherits the `EMAIL` body via `copy_for()`, per the `event.*_published` precedent — §7), so a channel default can flip on later with no new copy. |

---

## 2. What already exists (reuse, don't reinvent)

This is the strongest-reuse feature in the area — almost the entire delivery path already exists.

| Need | Existing thing | Location |
|---|---|---|
| Scheduled-send walker (window + dedupe + fan-out) | `run_sources(sources, now=)` / `run_due(occurrences, now=, window=)` | `core/events/scheduler.py:134` / `:111` |
| The occurrence dataclass a source yields | `ScheduledOccurrence(event_key, anchor, offset, context, period, …)` + `.is_due()` + `.fire()` | `core/events/scheduler.py:52-102` |
| Due-window time math (15-min tick) | `is_due(anchor, offset, now=, window=)`, `DEFAULT_WINDOW = 15min` | `core/events/scheduling.py:94` / `:26` |
| **Worked example: an N-before reminder source** | `class_reminder_occurrences(now)` (24h-before class reminder) | `classes/tasks.py:20-46` |
| **Worked example: per-member N-days-before broadcast sources** | `closing_soon_occurrences` / `vote_soon_occurrences` (gated, windowed, per-`period` dedupe) | `membership/voting.py:103` / `:161` |
| The command that wires a source into the cron | `send_voting_reminders` → `run_sources([...])` | `core/management/commands/send_voting_reminders.py:29-35` |
| The 15-min cron dispatcher (always-run tuple) | `run_scheduled_tasks` | `core/management/commands/run_scheduled_tasks.py:36-44` |
| The Render cron service that fires it | `run-scheduled-tasks`, every 15 min | `render.yaml:66-74` |
| The event model + its publish choke point | `CommunityEvent`; `publish()` (announce → `PENDING` → `push_to_google()`) | `membership/models.py:2473` / `:2792` |
| Where publish is currently called | direct-create views + `approve()` | `hub/views.py:2423`, `:2483`; `membership/models.py:2899` |
| The launch-announcement emit (idempotent `period`) | `announce()` + `_ANNOUNCE_EVENT` map | `membership/models.py:2765`, `:2523` |
| Event key registry + curated copy (mirror these) | `event.guild_published` / `.community_published` / `.lead_meeting_published` | `core/events/registry.py:480-511`; `core/events/copy.py:636-723` |
| Recipient resolvers to compose | `guild_members`, `all_active_members`, `all_guild_leads` | `core/events/resolvers.py:161`, `:332`, `:355`; `_RESOLVERS` dict at `:447-468` |
| The absolute event URL for copy links | `CommunityEvent.absolute_url` (Feature B repoints it to the event page) | `membership/models.py:2757` |
| Themed datetime-local field (dark/light picker) | `.pl-form-group input[type="datetime-local"] { color-scheme }` — already solved centrally | `static/css/components.css:510-511`, `:974-975` |
| The two Events-tab list surfaces a scheduled row must remain findable on | guild edit → `guild.events.upcoming()`; calendar → `CommunityEvent.objects.published().upcoming()` | `hub/views.py:681` + `templates/hub/guild_edit.html:206-234`; `hub/views.py:3051` + `templates/hub/community_calendar.html` |

### Genuine gaps to close (kept minimal)

1. **Five columns + one enum value** on `CommunityEvent`: `publish_at`, `remind_7d`, `remind_3d`, `remind_1d`, `notify_happening_now`, and `ModerationState.SCHEDULED` (§4).
2. **Two new event keys + copy**: `event.reminder`, `event.happening_now` (§7). No `event.reminder`/`.happening_now` exists today (registry stops at `event.declined`). **Plus** a one-line, schedule-aware tweak to the *existing* `event.approved` copy: an approved-but-`SCHEDULED` proposal must not be announced to the proposer as "now on the Community Calendar" (`copy.py:825`, `:830`) — it isn't yet (§5, §7).
3. **One new recipient resolver**: `event_audience` — mirrors `_ANNOUNCE_EVENT`'s per-scope audience so one reminder key serves all three event types. This is the only *new* resolver; it composes the three existing ones exactly like `guild_leadership_or_admins` composes (`resolvers.py:140`). **Flag:** new `Recipients` enum member + resolver entry — additive, follows the established composition pattern.
4. **Two thin sources + a deferred-publish command**, and three `QuerySet` methods (`due_to_publish`, `scheduled` for the admin management list, plus reuse of the existing `published()`).
5. **Form + template**: extend `CommunityEventForm` + the event-edit template with the new controls.
6. **Keep scheduled events findable/manageable on the two Events tabs** (§6). A `SCHEDULED` guild event already renders on the guild Events tab (via `upcoming()`, which has no state filter) with its Edit/Delete controls — it just needs a "Scheduled" indicator. A `SCHEDULED` **site-wide** event, however, is invisible on the calendar Events tab today: `upcoming_events` is `published().upcoming()` and `my_proposals` filters `submitted_by=request.user` (admin direct-creates set `created_by`, *not* `submitted_by`), so it falls through both — the admin could never find it to edit or cancel. Add a small admin-only "Scheduled — not yet announced" section there.

No new cron infra, no new delivery/dedupe model, no new email shell.

## 3. Where the code lives

```
membership/
  models.py
    CommunityEvent                      + 5 fields, + SCHEDULED state,
                                        + schedule_or_go_live(), publish_scheduled()
                                        (approve() rerouted through schedule_or_go_live)
    CommunityEventQuerySet              + due_to_publish(now), + scheduled()
  events.py  (NEW, small)               event_reminder_occurrences(now),
                                        event_happening_now_occurrences(now)  ← scheduler sources
  spec/models/community_event_scheduling_spec.py   (NEW)
  spec/events_spec.py                   (NEW — the two sources)

core/
  events/
    registry.py                         + EVENT_REMINDER / EVENT_HAPPENING_NOW keys,
                                        + Recipients.EVENT_AUDIENCE
    resolvers.py                        + event_audience() + _RESOLVERS entry
    copy.py                             + curated copy for both keys (IN_APP + EMAIL; Discord
                                        inherits EMAIL via copy_for()), + schedule-aware event.approved
  management/commands/
    send_event_reminders.py             (NEW — run_sources([...]) driver)
    publish_due_events.py               (NEW — transition SCHEDULED→PUBLISHED for due rows)
    run_scheduled_tasks.py              + both commands in the always-run tuple

hub/
  forms.py                              CommunityEventForm + the 5 fields + clean_publish_at
  views.py                              guild_event_edit / event_edit: route is_new OR SCHEDULED
                                        through schedule_or_go_live(); community_calendar: admin
                                        scheduled_events context (+ publish_at to event.approved emit)
  spec/forms/community_event_form_spec.py   (extend)
  spec/views/…                          (extend — scheduled-edit publishes now; admin scheduled list)

templates/hub/
  community_event_edit.html             + "Announcements & reminders" card
  guild_edit.html                       events-tab rows: + "Scheduled for <date>" badge
  community_calendar.html               + admin-only "Scheduled — not yet announced" section

plfog/version.py                        VERSION bump + CHANGELOG entry (final phase)
```

Home apps: `membership` (model + sources), `core.events` (keys/copy/resolver), `hub` (form/template). All within existing coverage/mypy scope.

## 4. Data model

### `CommunityEvent` — new fields

| Field | Type | Notes |
|---|---|---|
| `publish_at` | `DateTimeField(null=True, blank=True)` | help_text: "When to announce this event. Leave blank to announce as soon as it's saved." Blank/past ⇒ announce immediately (today's behavior). |
| `remind_7d` | `BooleanField(default=False)` | help_text: "Send members a reminder 7 days before it starts." |
| `remind_3d` | `BooleanField(default=False)` | help_text: "Send members a reminder 3 days before it starts." |
| `remind_1d` | `BooleanField(default=False)` | help_text: "Send members a reminder 1 day before it starts." |
| `notify_happening_now` | `BooleanField(default=False)` | help_text: "Ping members when it starts." |

### `ModerationState` — new value

Add to the existing `TextChoices` (`models.py:2499`):

```
SCHEDULED = "scheduled", "Scheduled"  # approved/authored; auto-publishes at publish_at (not yet announced or pushed)
```

`SCHEDULED` sits between "authored/approved" and `PUBLISHED`. It is **not** live on the calendar and **not** pushed to Google until the cron promotes it. `sync_state` stays `IDLE` while `SCHEDULED` (publish hasn't run).

**Reminder-offset helper** (keeps the source DRY, one place to add an offset later):

```python
REMINDER_OFFSETS: list[tuple[str, int]] = [("remind_7d", 7), ("remind_3d", 3), ("remind_1d", 1)]

def enabled_reminder_offsets(self) -> list[int]:
    """Days-before values whose toggle is on (e.g. [7, 1])."""
    return [days for attr, days in self.REMINDER_OFFSETS if getattr(self, attr)]
```

### `CommunityEventQuerySet` — new method

```python
def due_to_publish(self, now: datetime) -> CommunityEventQuerySet:
    """SCHEDULED events whose publish_at has arrived (the deferred-publish set)."""
    return self.filter(moderation_state=CommunityEvent.ModerationState.SCHEDULED, publish_at__lte=now)

def scheduled(self) -> CommunityEventQuerySet:
    """Parked-but-not-yet-announced events (the admin management list, §6)."""
    return self.filter(moderation_state=CommunityEvent.ModerationState.SCHEDULED)
```

`scheduled()` mirrors `published()`/`awaiting_review()` (`models.py:2449`, `:2453`) — it feeds the admin-only "Scheduled — not yet announced" section on the calendar Events tab so a site-wide scheduled event stays findable (§6). Reminder sources reuse the existing `published()` plus a `starts_at` band (§5).

### Migration

One additive migration: `AddField ×5` + `AlterField(moderation_state)` (the enlarged `choices` set — data-preserving, no row rewrite). **Reverse** = `RemoveField ×5` + restore the prior `choices`. No data migration (every existing row keeps `publish_at=NULL`, all booleans `False`, unchanged `moderation_state`). Constraints on the model are unaffected (they key off `event_type`/`guild`/`starts_at`/`ends_at`).

## 5. Business logic (fat models)

### The create/approve choke point

Replace the two direct `publish()` calls (`hub/views.py:2423`, `:2483`) and the tail of `approve()` with a single branch method:

```python
def schedule_or_go_live(self, *, actor: User | None = None) -> None:
    """Publish now, or park until publish_at. The single create/approve entry point.

    Future publish_at ⇒ moderation_state=SCHEDULED, no announce/push (the cron promotes it).
    Blank/past publish_at ⇒ moderation_state=PUBLISHED + publish() (announce + Google push), as today.
    """
    if self.publish_at is not None and self.publish_at > timezone.now():
        self.moderation_state = self.ModerationState.SCHEDULED
        self.save(update_fields=["moderation_state", "updated_at"])
        return
    self.moderation_state = self.ModerationState.PUBLISHED
    self.save(update_fields=["moderation_state", "updated_at"])
    self.publish(actor=actor)
```

- **Direct-create views** (lead form, admin form — `hub/views.py:2419-2425`, `:2480-2486`): route both *create* **and** *editing a still-`SCHEDULED` row* through `schedule_or_go_live()`. The branch becomes:
  ```python
  if is_new or event.moderation_state == CommunityEvent.ModerationState.SCHEDULED:
      event.schedule_or_go_live(actor=request.user)
  elif event.moderation_state == CommunityEvent.ModerationState.PUBLISHED:
      event.push_to_google(actor=request.user)
  ```
  Editing a live (`PUBLISHED`) event is unchanged — it just re-pushes to Google and never re-announces (`announce()` is `period`-deduped anyway).
  > **Why `SCHEDULED` must re-route — the dead-end it prevents.** If the edit path left a `SCHEDULED` row untouched (as "re-saves fields but does not announce" would), a lead who edits it and turns the schedule toggle **off** (clearing `publish_at`) would leave `moderation_state=SCHEDULED` with `publish_at=NULL`. `due_to_publish` filters `publish_at__lte=now`, which a `NULL` never satisfies — so the event would be **stranded in `SCHEDULED` forever: never announced, never on the calendar, invisible**. Re-routing means clearing (or back-dating) `publish_at` publishes it now, a still-future `publish_at` simply re-schedules (idempotent — `schedule_or_go_live` re-sets `SCHEDULED` and does not announce), and moving the time merely re-times the pending publish. This is also the "publish it early / go now" escape hatch — no separate button.
- **`approve()`** (`models.py:2887`): record reviewer + `reviewed_at` as today (keep the reviewer-fields save), but instead of setting `PUBLISHED` and calling `publish()` directly, call `self.schedule_or_go_live(actor=reviewer)`. This is what enforces *approve-before-schedule*: a proposal with a future `publish_at` becomes `SCHEDULED` only **after** a reviewer approves it — a member can never self-publish at their chosen time (they don't hold `approve()`; the reviewer does). The `event.approved` proposer notification (`_emit_decision`, `models.py:2900`) still fires immediately, **but its copy must become schedule-aware.** The current text (`copy.py:825`, `:830`) reads *"your proposed event was approved and is now on the Community Calendar"* — false for a `SCHEDULED` event, which is *not* yet on the calendar. Add `publish_at` (and a `scheduled` boolean) to the `_emit_decision` context and guard the copy: *"approved — it'll be announced and added to the calendar on {{ publish_at }}"* when scheduled, the existing "now on the Community Calendar" line otherwise (§7).

### Deferred-publish (the cron promotion)

```python
def publish_scheduled(self, *, actor: User | None = None) -> None:
    """Promote a due SCHEDULED event to live (announce + Google push). Cron-facing.

    Raises:
        InvalidEventTransition: If not currently SCHEDULED.
    """
    if self.moderation_state != self.ModerationState.SCHEDULED:
        raise InvalidEventTransition(f"Cannot publish an event in state '{self.moderation_state}'.")
    self.moderation_state = self.ModerationState.PUBLISHED
    self.save(update_fields=["moderation_state", "updated_at"])
    self.publish(actor=actor)
```

`publish()` itself is unchanged — still the idempotent choke point (`announce()` deduped on `event:{pk}:published`; `IDLE→PENDING`; `push_to_google()`). Command `publish_due_events` iterates `CommunityEvent.objects.due_to_publish(now)` and calls `publish_scheduled(actor=event.created_by)` in a per-row try/except (one bad Google push must not stall the batch — `publish()` already swallows Google failures into `FAILED`, so this is belt-and-suspenders).

### Reminder + happening-now sources (`membership/events.py`)

Both mirror `class_reminder_occurrences` / the voting sources: window the query, yield one `ScheduledOccurrence` per (event × offset), let `run_due` due-check and `emit` dedupe.

```python
def _event_context(event: CommunityEvent, *, days_before: int | None) -> dict:
    return {
        "guild": event.guild,                     # drives event_audience + Discord guild routing
        "event_type": event.event_type,           # drives event_audience scope switch
        "guild_name": event.guild.name if event.guild else "",
        "event_title": event.title,
        "when": event.when_display,
        "location": event.location,
        "days_before": days_before,               # None for happening-now
        "event_url": event.absolute_url,          # Feature B repoints this to the event page
    }

def event_reminder_occurrences(now: datetime) -> Iterable[ScheduledOccurrence]:
    """One occurrence per enabled offset on each published, upcoming event (a scheduler source)."""
    window = DEFAULT_WINDOW
    upcoming = (
        CommunityEvent.objects.published()
        .filter(starts_at__gte=now, starts_at__lte=now + timedelta(days=7) + window)
        .select_related("guild")
    )
    for event in upcoming:
        for days in event.enabled_reminder_offsets():
            yield ScheduledOccurrence(
                event_key=EVENT_REMINDER,
                anchor=event.starts_at,
                offset=timedelta(days=-days),
                target=event,
                context=_event_context(event, days_before=days),
                url=event.absolute_url,
                period=f"event:{event.pk}:reminder:{days}d",
            )

def event_happening_now_occurrences(now: datetime) -> Iterable[ScheduledOccurrence]:
    """One 'starting now' occurrence per published, upcoming event that opted in (a scheduler source)."""
    window = DEFAULT_WINDOW
    starting = (
        CommunityEvent.objects.published()
        .filter(notify_happening_now=True, starts_at__gte=now, starts_at__lte=now + window)
        .select_related("guild")
    )
    for event in starting:
        yield ScheduledOccurrence(
            event_key=EVENT_HAPPENING_NOW,
            anchor=event.starts_at,
            offset=timedelta(0),
            target=event,
            context=_event_context(event, days_before=None),
            url=event.absolute_url,
            period=f"event:{event.pk}:happening_now",
        )
```

Notes carried by the design:
- **`starts_at__gte=now`** ⇒ a past-anchor recurring series contributes nothing (its `starts_at` is behind us) — recurring events get the launch announcement but no ongoing reminders in v1 (§10). It also means a reminder offset already in the past (event <7 days out) is simply never yielded as due — no negative/awkward fire.
- **`period` is per-(event, offset)**, not per-member. Combined with `emit`'s per-recipient `EventDelivery` row (`user:<pk> × period`), each member gets each occurrence at most once — exactly the voting-source contract (`membership/voting.py:17-19`). Editing the event never changes the `period`, so an already-delivered reminder never re-fires; moving `starts_at` merely re-times the *not-yet-sent* offsets.

### The commands

`send_event_reminders` mirrors `send_voting_reminders` verbatim:

```python
fired = run_sources([event_reminder_occurrences, event_happening_now_occurrences], now=timezone.now())
```

`publish_due_events` iterates `due_to_publish(now)` and calls `publish_scheduled`. Both are added to the `run_scheduled_tasks` always-run tuple (`run_scheduled_tasks.py:36`); both are idempotent (period dedupe / state-guard) so every-tick execution is safe. No `render.yaml` change — the single 15-min dispatcher already runs.

## 6. UI / UX ← completeness checklist applied

**Screen:** `templates/hub/community_event_edit.html` — the one dedicated page for both add and edit, served to guild leads (`event_edit`/`guild_event_edit`), admins (`event_edit`), and members proposing (`propose_event`). It's already a 6+ field inline form → the new controls are a **second card on the same page**, not a modal (FRONTEND.md interaction table: 4+ fields = inline).

**New card — "Announcements & reminders"** (a `<div class="hub-card">`, placed after the existing "Event details" card, before the muted save-hint paragraph). `x-data="{ scheduleLater: {{ form.publish_at.value|yesno:'true,false' }} }"` so a re-render with a validation error keeps the picker open.

**Components used:** `components/form_field.html` for every field (auto-renders the four booleans as toggles), the existing themed `datetime-local` widget for `publish_at`.

**The controls, named explicitly:**

- **When should this go out?** A single toggle **"Schedule the announcement for later"** (`components/form_field.html` on a *presentational* Alpine-bound checkbox, or `components/toggle.html`) bound to `scheduleLater`, **off by default**. Off ⇒ `publish_at` stays blank ⇒ announce on save (today's behavior). On ⇒ reveal the `publish_at` field:
  ```html
  <div x-show="scheduleLater" class="pl-reveal">
    {% include "components/form_field.html" with field=form.publish_at field_hint="We'll announce it — Discord, the bell, and the calendar — at this time. Until then it stays hidden." %}
  </div>
  ```
  `publish_at` renders through `form_field.html` inside `.pl-form-group`, which already applies `color-scheme: dark`/`light` to `input[type="datetime-local"]` (`components.css:510`, `:974`) — the picker icon is theme-correct with **no new CSS** (rule 14 is solved centrally here; no `filter: invert` needed). The widget carries `onclick="this.showPicker?.()"` to match the existing `starts_at`/`ends_at` widgets (`hub/forms.py:1128`), opening the picker from the whole field.
  > `.pl-reveal` is a plain `pl-` class (`display:block`) — **not** an inline `style="display:…"` — per FRONTEND.md rule 12 (Alpine strips inline `display` on reveal). It lives in `hub.css`.
- **Reminders** — three toggles via `form_field.html`: `remind_7d`, `remind_3d`, `remind_1d`, each **off** by default, under a small muted lead-in "Remind members before it starts:". Their `help_text` is the per-toggle description.
- **Happening now** — `notify_happening_now` toggle via `form_field.html`, **off** by default, hint "A single ping to members when the event begins."
- **Save/submit:** the existing single `Save event` button at the bottom of the form (`community_event_edit.html:36`) — unchanged. It's a full-page POST → Django `messages.success` + redirect (the existing pattern; **not** HTMX, so no toast). The success message becomes state-aware: "Event scheduled for <publish_at>." when `SCHEDULED`, else "Event saved." (today's copy).

**States:**
- *Default / empty:* schedule-toggle off, three reminder toggles off, happening-now off — the card reads as "announce now, no reminders," matching current behavior for anyone who ignores it.
- *Editing a live (`PUBLISHED`) event:* all fields reflect stored values; editing does not re-announce (the `elif PUBLISHED` branch only re-pushes to Google).
- *Editing a still-`SCHEDULED` event:* the picker opens on the stored `publish_at`. Turning the toggle **off** and saving clears `publish_at` and **publishes it now** (the edit path re-routes `SCHEDULED` rows through `schedule_or_go_live()` — §5); keeping a future time re-schedules without announcing. Success message reflects the outcome ("Event saved." when it went live, "Event scheduled for <publish_at>." when still parked).
- *Error:* `publish_at` in the past (see §clean below) → inline `.pl-field-error` under the field via `form_field.html`, picker stays open (`scheduleLater` seeded from the submitted value). Standard full-page re-render with `messages`-free error display — no 500.
- *Scheduled confirmation & where it lives afterward (no can't-find-it dead end):* the success message names the date, and the event stays visibly findable on the Events tab it belongs to:
  - **Guild event →** already renders on the guild Events tab (`guild.events.upcoming()` has no state filter), with its existing **Edit** / **Delete** controls. Add a small **"Scheduled for {{ event.publish_at }}"** pill next to the existing `_sync_badge`, reusing the existing `hub-badge` class (`guild_edit.html:561` — do **not** invent a `pl-badge` token) so a lead can tell at a glance it's parked, not live.
  - **Site-wide event →** does **not** appear in the calendar Events tab's public `upcoming_events` (that's `published()` only, and correctly so). Surface it in a new **admin-only "Scheduled — not yet announced"** section (mirror the existing `my_proposals` block), fed by `scheduled_events` (§5 `scheduled()` queryset, admins only), each row showing the publish date + **Edit** / **Delete**. Without this section an admin-scheduled event is invisible after save.
- *Cancelling a scheduled announcement:* use the existing **Delete** button on the row (`guild_event_delete` / `event_delete`). A `SCHEDULED` row was never announced or pushed (no `google_event_id`), so `remove_from_google()` is a no-op and nothing needs unwinding. `withdraw()` is intentionally **not** extended to `SCHEDULED` — that path is the *member's* pre-approval pull-back; once approved-and-scheduled the event is the lead's/admin's to delete.

**Form validation (`CommunityEventForm.clean_publish_at`, not the view):**
- `publish_at` must be in the future *and* strictly before `starts_at` (announcing after the event started is a mistake): "Pick a time in the future." / "The announcement time must be before the event starts." Blank is valid (announce now).
- No cross-field requirement between reminders and `starts_at` — a `remind_7d` on an event 2 days out is *allowed*; it simply never fires (§5). No error, no warning needed, but the hint copy sets the expectation.

**Dark + light:** Every control routes through `form_field.html` → `.pl-form-group` / `components/toggle.html`, which already carry both themes. The datetime picker's `color-scheme` is theme-driven (verified `components.css:510-511`, `:974-975`). No hardcoded colors, no `--surface` fallback, no inline `background`/`color` on any control. **Verify both themes** on the new card (spec requirement).

**Mobile:** The card is a single vertical stack of full-width fields + toggles — reflows with the existing `.hub-card` / `.pl-form-group` (already responsive). Toggles are real tap targets. 8px-grid spacing (`margin-bottom:1rem` between groups) matches the existing "Event details" card. No table, no fixed widths.

## 7. Notifications / emails / activity

Two new registry events (`core/events/registry.py`), curated copy (`core/events/copy.py`), one new resolver (`core/events/resolvers.py`). Both use the new `EVENT_AUDIENCE` resolver so a single key serves all three scopes.

### New resolver — `event_audience`

```python
def event_audience(context: dict[str, Any]) -> list[Recipient]:
    """The launch-announcement audience for a community event, by scope.

    Mirrors CommunityEvent._ANNOUNCE_EVENT: a guild event → the guild's members;
    a leadership meeting → all guild leads; any other site-wide event → all active
    members. Composes the three existing resolvers (like guild_leadership_or_admins).
    """
    guild = _require(context, "guild")  # value may be None (site-wide); missing key is a bug
    if guild is not None:
        return guild_members(context)
    if context["event_type"] == CommunityEvent.EventType.LEAD_MEETING:
        return all_guild_leads(context)
    return all_active_members(context)
```

Add `Recipients.EVENT_AUDIENCE = "event_audience"` (`registry.py:64` enum) and the dict entry (`resolvers.py:452-463`). Lazy-import `CommunityEvent` inside the function (keep `core → membership` layering clean, as the other resolvers do).

### Registry entries (mirror `event.guild_published`, `registry.py:480`)

| Key | Label | Recipient | Channels (default) |
|---|---|---|---|
| `event.reminder` | "Event reminder" | `EVENT_AUDIENCE` | in-app ON, email OFF, Discord OFF |
| `event.happening_now` | "Event starting now" | `EVENT_AUDIENCE` | in-app ON, email OFF, Discord ON |

Category `"Events"`, `activity_kind=None` (a reminder is not a fresh site-activity item — the event's creation already logged one).

### §7 notifications table

| Event | Audience | Channels | Copy essentials | Period (unique) |
|---|---|---|---|---|
| `event.reminder` | Same as the launch announcement, **by scope**: guild event → guild members; lead meeting → all guild leads; community → all active members (see gate note below) | In-app (on), Email (off), Discord (off) | Subject links `{{ event_title }}` → `{{ event_url }}` (the event page via Feature B); "{{ event_title }} is {{ days_before }} day(s) away — {{ when }}"; where + "See it on the calendar / add to your calendar"; guild name when set | `event:{pk}:reminder:{days}d` |
| `event.happening_now` | Same launch audience, by scope | In-app (on), Email (off), Discord (on) | "{{ event_title }} is starting now — {{ when }}"; linked title → event page; location; Discord post to the guild channel when `guild` set | `event:{pk}:happening_now` |

> **Activation gate — precisely (corrects an earlier over-claim).** The reminder reuses the launch audience exactly, so its gating *matches the launch announcement's* — it is **not** a blanket "logged-in members only." Verified in source: `all_active_members` (community) and `all_guild_leads` (lead meeting) **do** filter `last_login__isnull=False` (`resolvers.py:347`, `:373`), so those two audiences reach only members who've signed in at least once. **`guild_members` does NOT** filter on `last_login` (`resolvers.py:174-181`) — a provisioned-but-never-logged-in guild member *is* a recipient. That is deliberate parity: `event.guild_published` already reaches those members, and a reminder that reached *fewer* people than the launch it echoes would be surprising. (If we ever want guild reminders to be logged-in-only, that is a change to `guild_members` itself — out of scope, since it would also change every existing guild broadcast. Flagged for Josh in §10.)

**Copy authoring rules (FRONTEND.md → Email Templates / rule 15):** author an `EventCopy` for each key exactly like the `event.*_published` entries (`copy.py:636-723`) — a `ChannelCopy` (fields: `subject`, `body_text`, `body_html`) for `Channel.IN_APP` (subject + body_text) and `Channel.EMAIL` (subject + body_text + `body_html`, `.txt`/`.html` parity). **Discord is not authored as a separate channel in this codebase:** `EventCopy.copy_for()` (`copy.py:63`) falls a `Channel.DISCORD` request back to the `EMAIL` copy, exactly as the three `*_published` events do (they define only `IN_APP` + `EMAIL`). So `event.happening_now` (Discord default **on**) will post its **EMAIL body** to the guild channel — write that body so it reads well *both* as a one-line Discord post and as an email (short lead sentence, link first). Only add an explicit `Channel.DISCORD` `ChannelCopy` if you want a distinct, terser channel post — not required for v1. The email HTML is styled centrally by `notification_shell.html` + `_style_copy_fragment` (cream-on-dark, gold links) — verify it renders cream/gold, not black-on-dark. Link the **subject noun** (`{{ event_title }}`) to `{{ event_url }}`; absolute URL via `event.absolute_url`; one primary CTA (view the event) plus the calendar link; no "BETA"; subject/body both in Portland time (`when_display` is already localtime). Add both keys' `sample_context` for the settings-matrix preview.

**`event.approved` (existing key) — make it schedule-aware.** When `approve()` routes a proposal to `SCHEDULED` (future `publish_at`), the proposer's `event.approved` copy must not claim the event is "now on the Community Calendar" (`copy.py:825`, `:830`). Add `publish_at` + a `scheduled` flag to the `_emit_decision` context (`models.py:2900`) and branch the `body_text`/`body_html` (both channels, both files in sync): *"approved — it'll be announced and added to the calendar on {{ publish_at }}"* when `scheduled`, the existing copy otherwise. Update its `sample_context` to include `publish_at`.

### Activity

No new `SiteActivity` kind. The scheduled-publish path still runs `publish()` → `announce()`, which logs/announces exactly as an immediate publish does (just later). Reminders/happening-now do not write activity rows (they're transient pings), matching the `event.*_published` precedent (`activity_kind=None`).

## 8. Build order (phased; each phase ships green)

1. **Model + migration.** Add the 5 fields + `SCHEDULED` state + `enabled_reminder_offsets()` + `due_to_publish()` + `scheduled()`. Add `schedule_or_go_live()` / `publish_scheduled()`; reroute `approve()` through `schedule_or_go_live()`. Point the two direct-create views at `schedule_or_go_live()` on **`is_new` *or* `SCHEDULED`** (the no-strand fix, §5). Migration (additive; reverse drops columns + restores choices). Specs for the state machine + queryset (incl. the scheduled-edit-publishes-now path). *Green: full suite + `ruff` + `mypy`.*
2. **Event keys, copy, resolver.** `EVENT_REMINDER` / `EVENT_HAPPENING_NOW` registry entries, `Recipients.EVENT_AUDIENCE` + `event_audience()` resolver, curated `IN_APP` + `EMAIL` copy (Discord inherits `EMAIL`), and the schedule-aware `event.approved` tweak (§7). Resolver + copy specs (audience-by-scope, per-scope activation gate). *Green.*
3. **Sources + commands + cron wiring.** `membership/events.py` (both sources), `send_event_reminders` + `publish_due_events` commands, add both to `run_scheduled_tasks` always-run tuple. Source specs (window math, dedupe, past-offset skip, recurring-series no-fire, scheduled-publish fires once). *Green.*
4. **UI.** Extend `CommunityEventForm` (5 fields, `clean_publish_at`), the "Announcements & reminders" card, `.pl-reveal` in `hub.css`, state-aware success message. **Keep scheduled events findable (§6):** add the "Scheduled for <date>" `hub-badge` to the guild Events-tab rows (`guild_edit.html`), and the admin-only "Scheduled — not yet announced" section + `scheduled_events` context on the calendar Events tab (`community_calendar.html` / `hub/views.py:3051`). Form + template-render specs. Manually verify **both themes** + mobile + the datetime picker. *Green.*
5. **Housekeeping.** Bump `plfog/version.py` `VERSION` (currently `0.21.7`) and the `CHANGELOG`. This is a **net-new member-facing feature** on the unreleased `0.21` line → **add a new grouped entry** at the top (do not fold into an existing one), plain language, e.g. *"Schedule when a community event's announcement goes out, and turn on reminders 7/3/1 days before or a ping when it starts."* Re-stamp `version`/`date` to the new `VERSION`. *(Build-time note only — do not bump during specing.)*

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py` under each app's `spec/`, `describe_*`/`it_*` (never `context_*` — not collected), factory-boy, ≥98% gate, run in the `plfog-web` Docker image (`--no-cov` for subsets).

**Model / state machine** (`membership/spec/models/community_event_scheduling_spec.py`):
- `schedule_or_go_live`: blank `publish_at` → `PUBLISHED` + `announce`/`push` called (mock `emit` + `push_to_google`); future `publish_at` → `SCHEDULED`, **no** announce/push; past `publish_at` → publishes now.
- `approve` with future `publish_at` → `SCHEDULED` (records reviewer, fires `event.approved`, does **not** announce the launch); with blank `publish_at` → `PUBLISHED` + `publish` (today's behavior preserved).
- `publish_scheduled`: `SCHEDULED` → `PUBLISHED` + `publish`; raises `InvalidEventTransition` from any other state.
- `due_to_publish(now)`: includes `SCHEDULED` with `publish_at <= now`; excludes future, non-`SCHEDULED`, and `PENDING` rows; **and never returns a `SCHEDULED` row whose `publish_at` is `NULL`** (the strand-guard rationale — proves a cleared schedule can't sit in `due_to_publish`).
- **Editing a `SCHEDULED` row (no strand):** the edit view routing `is_new or SCHEDULED` through `schedule_or_go_live` — clearing `publish_at` on a `SCHEDULED` event publishes it now (`PUBLISHED` + `announce`/`push`); a still-future `publish_at` keeps it `SCHEDULED` without announcing; editing a `PUBLISHED` event still only re-pushes (no re-announce).
- `enabled_reminder_offsets`: correct subset for each toggle combination; `[]` when all off.

**Sources** (`membership/spec/events_spec.py`) — the tz/window-sensitive core, use a fixed `now`:
- `event_reminder_occurrences`: yields one occurrence per enabled offset; `is_due` true only for the offset whose `starts_at − days` lands in `[now, now+15min)`; **event <1 day out** yields 7d/3d occurrences but none are due (past fire time) — assert `run_due` fires only the due one(s).
- Dedupe: two ticks in the same window deliver once (assert second `run_due` returns 0 / `EventDelivery` not duplicated); distinct offsets have distinct `period`s and both deliver.
- `event_happening_now_occurrences`: only `notify_happening_now=True`, only within `[now, now+window]`; offset 0; period `event:{pk}:happening_now`.
- **Recurring series:** a monthly event with `starts_at` in the past yields **no** reminder occurrence (band starts at `now`); a *future* first `starts_at` does. Documents the v1 scope.
- **Audience (`event_audience`):** guild event → guild members only; `LEAD_MEETING` → all guild leads; `COMMUNITY` (guild None) → all active members. **Activation gate, per scope (both directions — pin the parity so it's not accidental):** a never-logged-in *community/lead-meeting* recipient is **dropped** (mirrors `all_active_members`/`all_guild_leads`, `resolvers.py:347`/`:373`); a never-logged-in *guild member* is **kept** (mirrors `guild_members`, `resolvers.py:174-181`, and the `event.guild_published` launch it echoes).
- **Scheduled publish fires once:** a `due_to_publish` event, run through `publish_due_events`, transitions to `PUBLISHED` and announces exactly once even across two cron passes (`event:{pk}:published` dedupe).

**Form** (`hub/spec/forms/community_event_form_spec.py`): `clean_publish_at` rejects past and rejects `publish_at >= starts_at`, accepts blank and accepts a valid future-before-start; the 4 booleans round-trip.

**Template/view:** the edit view saves the new fields; success message is state-aware; a proposal with future `publish_at` lands `SCHEDULED` after `approve`, and its `event.approved` notification carries the schedule-aware copy (`publish_at` in context, not "now on the calendar"). **Findability (no dead end):** a `SCHEDULED` **site-wide** event appears in the admin's `scheduled_events` section on the calendar Events tab (and is absent from the public `upcoming_events`); a non-admin does **not** get `scheduled_events`. A `SCHEDULED` **guild** event still appears on the guild Events tab with its Edit/Delete controls and the "Scheduled for …" badge. (HTML-structure smoke: the reveal `x-show` uses a CSS class, not inline `display` — assert the markup, since a test-client POST can't catch that rule-12 bug.)

**tz/window gotchas to guard:** all anchors are aware datetimes (`starts_at`, `publish_at`); `when_display` is `localtime`; the 7-day prefilter band must include `+ DEFAULT_WINDOW` slack (a `starts_at` exactly `now+7d` must still be caught). Freeze `now` in every source spec; never rely on wall-clock.

## 10. Open / deferred

### Decisions to confirm with Josh (currently locked as below)

- **One switching resolver vs. three reminder keys — the flagged fork.** This spec picks **one** `event.reminder` (and one `event.happening_now`) key backed by a new `event_audience` resolver that branches on scope inside itself (guild → `guild_members`, lead meeting → `all_guild_leads`, community → `all_active_members`) — mirroring `_ANNOUNCE_EVENT` and composing existing resolvers like `guild_leadership_or_admins` does. The **alternative** is three keys each (`event.reminder.guild` / `.lead` / `.community`) wired to the three existing resolvers, no new resolver — matching the *existing* `event.{guild,community,lead_meeting}_published` split one-for-one. Trade-off: one key = one registry/copy/settings-matrix row and a single member preference toggle, but a resolver that reads `event_type`/`guild` from context; three keys = zero new resolver and exact symmetry with the launch keys, but 3× the copy/registry entries and three preference rows for what members experience as "event reminders." Spec assumes **one key**; confirm before Phase 2.
- **Do guild-event reminders reach never-logged-in guild members?** As specced, **yes** — the reminder mirrors the launch audience, and `guild_members` is not activation-gated (§7 gate note). That's consistent with today's `event.guild_published`. If Josh wants reminders (or all guild broadcasts) to be logged-in-only, that's a separate change to `guild_members` and out of this spec's scope.

### Deferred (YAGNI for v1)

- **Per-occurrence reminders for a recurring series.** v1 anchors reminders/happening-now on `starts_at` (the first/next concrete start), so a monthly series gets its launch announcement but not a reminder before *every* occurrence. Doing it right means expanding the next occurrence via `occurrences_in` and a per-occurrence `period` (`event:{pk}:reminder:{days}d:{occ_date}`). Deferred — YAGNI until members ask, and the task explicitly scoped `anchor=starts_at`.
- **Editing `publish_at` after it fired.** Once an event is `PUBLISHED`, re-scheduling is out of scope — the announcement already went out; the field is effectively read-only post-publish (the form still shows it, but the state guards prevent re-announce). No "un-publish" path.
- **A dedicated cross-surface "Scheduled events" management view** (one combined list across all guilds + site-wide with cancel/edit shortcuts). *v1 does surface scheduled events inline* — badged on the guild Events tab, and in the admin "Scheduled — not yet announced" section on the calendar Events tab (§6), each editable/deletable — so leads/admins can already find and manage them. A unified dashboard on top of that is deferred.
- **Per-recipient reminder opt-out beyond the existing notification preferences.** Reminders honor the member's existing in-app/email/Discord preferences via `emit`; no event-specific mute is added.
- **Discord copy for `event.reminder`** is authored but the channel default is OFF — flip to ON later if leads want per-offset channel posts. Cheap to change (one `ChannelDefault`).
