# Event-Driven Notifications — Design Spike

> **DESIGN SPIKE — exploratory. Not an approved implementation plan.**
> **Purpose: decide the architecture before building automated class emails.**
>
> Date: 2026-06-24 · Author: design exploration · Status: for decision

---

## 0. The question, in one breath

You asked for **automated class email sequences** — a `welcome`, a `reminder_48h`, and a
`follow_up_48h`, each with a per-class on/off toggle. Then you stepped back and asked the
bigger question:

> *"I'm not actually sure if this means we need to overhaul a lot of email stuff, or will we
> need `ClassEmailTemplate` models for every email?"*

**Short answer: No — not a template model for every email.** Most of the emails this app sends
are *events* with fixed, app-authored content; they already belong (or should belong) in the
event/trigger/preference system that `core` already has. Only the handful of emails whose
**content varies per class and is written by the instructor** — welcome, reminder, follow-up —
need stored, per-class template content. That is the *only* place a `ClassEmailTemplate`-shaped
model is justified, and even there it's one row per class per trigger, not "every email."

The rest of this doc backs that up with the current architecture, then proposes a target and a
pragmatic phased path to reach it without a big-bang overhaul.

---

## 1. Current-state map — two subsystems that barely talk

There are **two independent email/notification subsystems** in the codebase today. They overlap
conceptually (both "tell a person something happened") but share almost no plumbing.

### Subsystem A — the event / trigger / preference system (`core`)

This is *already event-driven*. It is the foundation we want to build on.

| Piece | Where | What it does |
|---|---|---|
| **Trigger catalogue** | `core/triggers.py:34-129` | A frozen Python list of `Trigger` dataclasses — one per notifiable event. Each has a stable `key`, `label`, `description`, `category`, an `Audience` enum (`core/triggers.py:16-19`: `ALL_MEMBERS` / `INSTRUCTORS_ONLY` / `STAFF_ONLY`), and `force_email` / `push_default` / `email_default` flags. |
| **Dispatch fan-out** | `core/notifications.py:18-63` | `dispatch(trigger_key, users, *, title, body, url, ...)`. Always bulk-creates in-app `Notification` rows (`:39-41`); then sends browser push and email **only** to users who opted in. |
| **Preference model** | `core/models.py:674-688` | `NotificationPreference(user, trigger, push_enabled, email_enabled)` — one row per user per trigger, unique-constrained (`:683-685`). Absent row ⇒ trigger defaults apply. |
| **In-app feed model** | `core/models.py:643-671` | `Notification(user, trigger, title, body, url, read_at, ...)` — the bell. Always written, regardless of channel prefs. |
| **Push channel** | `core/models.py:82` (`PushSubscription`) + `core/push.send_web_push` | Browser Web Push per subscription. |
| **Settings UI** | `hub/views.py:1252-1267` (the `user_settings` view), template `templates/hub/_notifications_settings.html` | Renders a per-trigger push/email toggle grid (grouped by `triggers.by_category(...)`, `core/triggers.py:156-164`) and saves `NotificationPreference` rows via `update_or_create`. URL: `/settings/?tab=notifications` (`hub/urls.py:109`). |
| **Scheduled-job idempotency** | `core/models.py:691-703` (`ScheduledNotificationMarker`) | A unique-`key` "already fired" guard for time-based dispatches (e.g. `"voting_closing:2026-06"`). |

**How `dispatch` routes a channel** (`core/notifications.py:43-63`):

```
for each user:
    in-app  → always (a Notification row was already bulk-created)
    push    → only if NotificationPreference.push_enabled
    email   → if trigger.force_email  OR  NotificationPreference.email_enabled
              (and the user has a usable .email)
```

So the routing primitive you described — *"an event occurs → notifications fire, routed by
each user's preferences and channel"* — **already exists.** `force_email` (`core/triggers.py:29`,
e.g. `new_login` at `:126-128`) is the existing "transactional must-send, ignore prefs" escape
hatch.

The trigger catalogue *already* contains class/instructor events:
`class_published`, `class_reminder`, `registration_confirmed`, `class_cancelled`,
`waitlist_spot_available`, `instructor_class_approved`, `instructor_new_registration`,
`class_review_requested`, `class_validation_requested` (`core/triggers.py:36-87`). These are the
*declared* events. The catch (see §1.3): the actual class emails don't go through `dispatch` —
they go straight to email.

### Subsystem B — transactional + scheduled class emails (`classes`)

These do **not** route through triggers/preferences. They call the email choke-point directly.

| Piece | Where | What it does |
|---|---|---|
| **Email choke-point** | `core/email.py:52-110` | `send(*, to, subject, trigger_kind, text_body, html_body=None, ..., best_effort=False, attachments=None)`. The single send point. Writes a `TransactionalEmailLog` row (`core/models.py:509`) on **every** attempt, success or fail. **Synchronous** — no queue. Console backend in dev, Resend in prod. `trigger_kind` here is a free-text audit label (e.g. `"classes.reminder"`), **not** a `core.triggers` key. |
| **The class senders** | `classes/emails.py` | ~14 sender functions, listed below. |

The senders in `classes/emails.py`:

| Function | Line | Recipient | Content source |
|---|---|---|---|
| `send_registration_confirmation` | `:64` | the registrant | app template `confirmation.{txt,html}` |
| `send_class_welcome_email` | `:114` | the registrant | **instructor-authored** (`ClassOffering.welcome_email_*`) |
| `send_class_welcome_email_test` | `:139` | the editor | instructor-authored (test render) |
| `send_instructor_registration_notification` | `:154` | the instructor | inline app string |
| `send_admin_registration_notification` | `:177` | admins | inline app string |
| `_send_review_request_email` | `:200` | reviewers | app template `review_request.*` |
| `_send_instructor_review_explainer` | `:229` | the instructor | app template `review_submitted_instructor.*` |
| `send_guild_lead_review_request` | `:251` | guild lead + staff | app templates |
| `send_admin_review_request` | `:265` | admins | app templates |
| `send_admin_validation_request` | `:276` | admins | app template `admin_validation_request.*` |
| `send_class_review_decision` | `:308` | the instructor | app template `review_decision.*` |
| `send_waitlist_joined_confirmation` | `:362` | the registrant | app template `waitlist_joined.*` |
| `send_waitlist_spot_opened` | `:388` | the registrant | app template `waitlist_spot_opened.*` |
| `send_reminder_email` | `:420` | the registrant | app template `reminder.*` |

Templates live in `templates/classes/emails/` (18 files; all the `.txt`/`.html` pairs above).

### 1.3 Where they overlap and where they diverge

**Overlap (the seam):** Exactly one of these senders *also* fires a `core` notification. The
reminder task (`classes/tasks.py:46-60`) sends the reminder email **and** calls
`notifications.dispatch("class_reminder", ...)`. So `class_reminder` is sent twice through two
different systems — once as a raw email bypassing prefs, once as a prefs-routed in-app/push/email
notification. Everything else in Subsystem B bypasses Subsystem A entirely.

**Divergence:** Subsystem B emails ignore `NotificationPreference`. A member who turned off
"Registration confirmed" email in `/settings/?tab=notifications` still gets
`send_registration_confirmation`, because that sender never consults the preference table. The
trigger `registration_confirmed` is *declared* (`core/triggers.py:38`) but *unused* by the actual
send path. The toggle in the settings UI is, today, decorative for that event.

```
                 EVENT HAPPENS
                      │
      ┌───────────────┴────────────────┐
      ▼                                 ▼
 Subsystem A (core)               Subsystem B (classes)
 notifications.dispatch()         classes/emails.send_*()
      │                                 │
  Notification row (always)        core.email.send()  ──► TransactionalEmailLog
      │                                 │
  push?  ◄─ NotificationPreference  (no preference check at all)
  email? ◄─ pref OR force_email          │
      │                                 ▼
   core.email.send() ──► TransactionalEmailLog   raw email out
```

**Reading of the seam:** the *plumbing* for "event → preference-routed channels" is built and
working (voting, leases, orientations, guild activity all use it). The class emails simply predate
it / were written as direct transactional sends. The opportunity is to **converge B onto A** where
it makes sense — not to build anything new at the foundation.

---

## 2. The key question, answered directly

> **Do we need a `ClassEmailTemplate` model for every email?** — **No.**

The distinction that resolves it is **who authors the content**:

### 2a. System/app-authored emails → these are EVENTS (no per-email model)

Content is fixed in app templates or inline strings; it does not vary per class beyond merge
fields the template already fills. Examples: registration confirmation, the new-registration
notices to instructor/admin, all the review-request / review-decision / validation emails,
waitlist joined/opened, and the *future* "new class published in your guild" notice for guild
leads.

These belong in the **event/trigger/preference system**. Each is "an event happened; tell the
right audience on their chosen channels." They need:
- a `Trigger` entry (most already exist in `core/triggers.py:34-129`), and
- for the ones that must always reach the person (confirmations, payment receipts), the existing
  `force_email=True` carve-out so prefs can't suppress them.

**No per-email model. No per-class storage.** The content is the app's, not the instructor's.

### 2b. Instructor-authored, per-class, scheduled emails → these need stored content

`welcome`, `reminder_48h`, `follow_up_48h`. Here the **instructor writes the subject and body**,
and it is **specific to one class**. That content has to live somewhere keyed to the class. This
is the *only* category that justifies a "`ClassEmailTemplate`"-shaped model.

The app already has this exact shape three times, hand-rolled:

| Existing precedent | Fields | Where |
|---|---|---|
| Class welcome email | `welcome_email_enabled / _subject / _body / _updated_at` + `welcome_email_ready` property | `classes/models.py:310-348` |
| Guild "thank-you" email | `thankyou_email_enabled / _subject / _body / _updated_at` + `thankyou_email_ready` | `membership/models.py:1586-1623` |
| Guild "join/welcome" email | `join_email_enabled / _subject / _body / _updated_at` + `join_email_ready` | `membership/models.py:1598-1628` |

Same quadruple of fields, copied onto three different models. Reminder (`reminder_48h`) and
follow-up (`follow_up_48h`) would make it five copies on `ClassOffering` alone if we keep spreading
fields.

#### Option for 2b: per-class fields vs. a small per-(class, trigger) model

**Field-spread (status quo, extended):** add `reminder_email_enabled/subject/body/updated_at` and
`follow_up_email_enabled/subject/body/updated_at` to `ClassOffering`.
- *Pro:* zero new tables; matches the existing welcome pattern exactly; trivial to read.
- *Con:* 12 email columns on `ClassOffering` for 3 emails; adding a 4th email = another migration +
  4 columns + another `*_ready` property; no shared rendering/scheduling path; the same shape stays
  duplicated across `ClassOffering` and the two guild emails.

**One small model — `ClassEmailTemplate(offering, trigger_type, is_active, subject, body, updated_at)`,
one row per class per trigger:**
- *Pro:* `trigger_type` (a `TextChoices`: `WELCOME` / `REMINDER_48H` / `FOLLOW_UP_48H`) collapses
  N emails into N *rows*, not N×4 *columns*; one shared render path; one place to add a new
  instructor email (a new choice + a template, no schema change); a clean `UniqueConstraint(offering,
  trigger_type)` enforces "one of each per class"; the welcome fields migrate into it cleanly.
- *Con:* a join to read; a data migration to move the existing welcome rows; slightly more upfront
  work than two more columns.

**Recommendation: the small model**, reached by migrating the existing welcome fields into it.
Three reasons: (1) you are about to add *two more* of these, which is exactly the moment field-spread
stops paying off; (2) `trigger_type` + `is_active` map 1:1 onto the enum you already described
(§5); (3) it gives one render+schedule path the scheduler in §4 can iterate generically instead of
special-casing welcome vs. reminder vs. follow-up. (Whether to *also* fold the two guild emails onto
the same model later is an open question — §8 — not required now.)

> **State it plainly:** Most emails are events and need no template model. Only the
> instructor-authored, per-class, scheduled emails need stored templates — and one small
> `ClassEmailTemplate` (one row per class per trigger) serves all of them.

---

## 3. Target architecture — extend `core`, don't replace it

The model you described composes from **three concerns**, all built on the existing `core` system.

```
                              ┌── (a) EVENTS ──────────────────────────────┐
  login / class created /     │  emit → triggers.get(key)                  │
  registration / approval /   │      → notifications.dispatch(key, users)  │
  "new class in my guild" ────┤      → Notification row (always)           │
                              │      → push/email per NotificationPreference│
                              └────────────────────────────────────────────┘

                              ┌── (b) TRANSACTIONAL MUST-SEND ─────────────┐
  registration confirmed /    │  same dispatch path, but the Trigger has   │
  payment receipt ────────────┤  force_email=True → email ignores prefs    │
                              │  (in-app row still created)                 │
                              └────────────────────────────────────────────┘

                              ┌── (c) INSTRUCTOR-AUTHORED, SCHEDULED ──────┐
  welcome (on confirm) /      │  ClassEmailTemplate(offering, trigger_type,│
  reminder (T-48h) /          │  is_active, subject, body)                 │
  follow-up (T+48h) ──────────┤  rendered + sent by a scheduler (§4),      │
                              │  honoring the recipient's channel prefs    │
                              │  where appropriate                         │
                              └────────────────────────────────────────────┘
```

**(a) Events → triggers → preference routing → channels.** Unchanged from today's `core`. New
events get a `Trigger` entry and a `dispatch()` call at the point the event occurs (fat-model
method or service, per the coding standards). "New class in your guild for the lead" is a textbook
example: a `class_published`-style trigger with `Audience.STAFF_ONLY`/guild-scoped recipients,
dispatched to `guild.leadership_members()` (the same recipient set the review emails already use,
`classes/emails.py:42-55`).

**(b) Transactional must-send.** Confirmations and receipts must reach the registrant even if they
muted everything. These keep `dispatch()` but their `Trigger` sets `force_email=True` (exactly how
`new_login` works today, `core/triggers.py:126-128`). They still drop an in-app row; they just can't
be suppressed for email. This replaces the *raw* `core.email.send()` calls in `classes/emails.py`
with prefs-aware-but-forced dispatches — same deliverability, but now audited as notifications and
visible in the bell.

**(c) Instructor-authored scheduled layer.** A distinct layer because its *content* is stored
(`ClassEmailTemplate`) and its *timing* is relative to session times, not "now." It still flows out
through `core.email.send` (one choke-point, one audit log) and can still honor channel prefs for the
recipient. The scheduler (§4) is what makes it "scheduled" rather than "on event."

The win: **one event vocabulary, one preference table, one in-app feed, one email choke-point with
one audit log.** Nothing is replaced; Subsystem B's senders get *re-pointed* through Subsystem A's
dispatch (for events) or kept as choke-point sends driven by the scheduler (for instructor content).

---

## 4. The scheduling mechanism

Timed sends (reminder at `starts_at − 48h`, follow-up at `ends_at + 48h`) reuse the **proven
window + dedupe pattern** already written for reminders.

### The proven pattern (already in the tree, just dead — see §4.1)

`send_due_class_reminders(window_minutes=15)` (`classes/tasks.py:13-62`):
1. Compute the send threshold relative to session time: `target_start = now + reminder_hours_before`,
   then a `window_minutes`-wide band (`:25-28`). A 15-minute cron means a 15-minute band catches
   each session exactly once.
2. Find sessions whose anchor falls in the band (`ClassSession.objects.filter(...)`, `:30-33`).
3. For each confirmed registration, `RegistrationReminder.objects.get_or_create(registration,
   session)` (`classes/models.py:1245-1264`, unique on `(registration, session)`) — the **dedupe
   audit row**. `created=False` ⇒ already sent ⇒ skip (`:43-44`).
4. Send, optionally dispatch the in-app notification (`:46-60`).

This is the template for *every* scheduled instructor email.

### Generalizing it

- **Time anchors** come from `ClassSession` (`classes/models.py:1083-1100`): `starts_at` and
  `ends_at`, both tz-aware.
  - `reminder_48h` window: `starts_at` is in `[now+48h, now+48h+15m)`.
  - `follow_up_48h` window: `ends_at` is in `[now−48h−15m, now−48h)` (i.e. the session ended ~48h ago).
- **Dedupe row:** generalize `RegistrationReminder` into a `(registration, trigger_type[, session])`
  audit row — one per send so the same email never fires twice. `welcome` keys on
  `(registration, WELCOME)` with no session; reminder/follow-up key on
  `(registration, trigger_type, session)`. (`ScheduledNotificationMarker`, `core/models.py:691-703`,
  is the cross-app analogue if we want a single idempotency table instead.)
- **Per-trigger "due" task:** one function per scheduled trigger (or one parameterized by
  `trigger_type`), each iterating its band → due registrations → `get_or_create` dedupe → render the
  `ClassEmailTemplate` → `core.email.send`.

### Registration → session relationship (a real subtlety)

`Registration` has **no session FK**; a confirmed registration covers *all* of its offering's
sessions. So:
- A multi-session class produces **one reminder per session** (the existing dedupe key is
  `(registration, session)`, which is correct for reminders).
- **Follow-up** wants to fire once after the *whole* class ends, not after every session. Decision
  needed (§8): dedupe follow-up on `(registration, FOLLOW_UP_48H)` keyed off the *last* session's
  `ends_at`, not per-session. This is the main place the "no session FK" fact bites.

### 4.1 Step 0 — wire the dead command into cron

**The reminder is fully built but never runs.** `send_due_class_reminders` (`classes/tasks.py`),
its command `classes/management/commands/send_class_reminders.py`, the `RegistrationReminder` dedupe
model, and the `ClassSettings.reminder_hours_before` config (`classes/models.py:1786-1788`) all
exist. But `send_class_reminders` is **not** in the task list of the cron dispatcher
`core/management/commands/run_scheduled_tasks.py:28` — which runs only
`send_voting_reminders, send_lease_expiry_reminders, auto_complete_orientations` always and
`sync_all_sources, generate_orientation_slots` at 13:xx UTC. The dispatcher is the single cron
service in `render.yaml:65-73` (`run_scheduled_tasks`, every 15 min). **Result: class reminders
never auto-send today.** Confirmed.

So the literal first step of the scheduling work is a one-line addition to the always-run tuple in
`run_scheduled_tasks.py`. Zero schema change, near-zero risk, immediate value. The 15-minute cron
granularity already matches the 15-minute default window — no `render.yaml` change needed (the file
even says "add new tasks in `run_scheduled_tasks.py` only," `render.yaml:63-64`).

---

## 5. `trigger_type` + `is_active`

Your requested enum and toggle map cleanly onto the §2b model:

```
ClassEmailTemplate.TriggerType (TextChoices):
    WELCOME        = "welcome",        "Welcome (on registration)"
    REMINDER_48H   = "reminder_48h",   "Reminder (48h before)"
    FOLLOW_UP_48H  = "follow_up_48h",  "Follow-up (48h after)"

ClassEmailTemplate:
    offering      FK → ClassOffering
    trigger_type  CharField(choices=TriggerType.choices)
    is_active     BooleanField(default=False)     # the per-class on/off toggle
    subject       CharField
    body          TextField
    updated_at    DateTimeField
    UniqueConstraint(offering, trigger_type)
    @property is_ready  → is_active and subject.strip() and body.strip()
```

- `is_active` **is** the per-class toggle you asked for, one per `trigger_type`. It maps directly
  onto today's `welcome_email_enabled` (`classes/models.py:310`); the scheduler/send path gates on
  `is_ready` exactly like the current `welcome_email_ready` check (`classes/emails.py:123`).
- `trigger_type` here is the *instructor-content* key. Keep it distinct from `core.triggers` keys
  (the cross-user event vocabulary) and from `core.email.send`'s `trigger_kind` audit label — three
  different "trigger" words that already coexist in this codebase; don't conflate them.

**Where the instructor toggles each:** the existing teach **"Emails"** subtab is the natural home.
`teach_class_emails` (`classes/views.py:1385`) already edits the welcome email and sends test
renders (`send_class_welcome_email_test`, `classes/views.py:1393`). It grows from one email editor
to three (an editable formset / tabbed panel per `trigger_type`), each with its `is_active` switch
and a "send test" button. (Per FRONTEND.md formset rules if rendered as a list.)

---

## 6. Three architecture options + recommendation

### Option A — Minimal: fields + wire reminder + add follow-up; leave system emails alone
Add `reminder_*` and `follow_up_*` field quadruples to `ClassOffering`; wire the reminder command
into cron; add a follow-up task. Leave Subsystem B's system emails as direct sends.
- **Pro:** smallest diff; no new model; ships the user's literal ask fastest.
- **Con:** 5th/6th copy of the email-field quadruple; instructor emails stay outside any unified
  path; system emails stay invisible to the bell and unaffected by prefs (the decorative-toggle
  problem persists); every future instructor email is another 4-column migration.

### Option B — Per-class `ClassEmailTemplate` for instructor emails; migrate system emails onto triggers/preferences incrementally  ★ recommended
Introduce the small `ClassEmailTemplate` model (§2b/§5) for welcome/reminder/follow-up; generalize
the dedupe row; wire it all into the cron dispatcher. **Separately and over time**, re-point the
system emails in `classes/emails.py` through `notifications.dispatch` (using `force_email=True` for
must-sends), so they gain in-app rows + preference routing.
- **Pro:** matches the natural grain of the two distinct email kinds; one render+schedule path for
  instructor content; system events converge onto the one preference table and one feed; no big
  bang — each re-point is independent and individually shippable.
- **Con:** a data migration (welcome fields → model) and per-event work to migrate system emails.

### Option C — Full event-bus standardization
A generic event bus / signal layer that everything (logins, registrations, scheduled sends) emits
into, with a unified delivery pipeline.
- **Pro:** the cleanest end-state on paper; one path for truly everything.
- **Con:** large up-front design + migration; `core` already gives ~80% of the value with
  `dispatch` + triggers + prefs; the coding standards explicitly prefer model methods over signals
  except for cross-app decoupling (CLAUDE.md §2). YAGNI at current volume. Not recommended now.

**Recommendation: Option B, reached incrementally.** It delivers the user's exact feature (welcome
/ reminder_48h / follow_up_48h with per-class toggles) on a clean per-class model, and it lets the
broader "make the whole app event-driven" goal happen *gradually* by re-pointing one system email
at a time onto the trigger/preference system that already exists. Explicitly **not** a big-bang
overhaul of "email stuff."

---

## 7. Phased incremental roadmap

Each phase is independently shippable and green.

| Phase | Work | Unblocks / value | Risk |
|---|---|---|---|
| **0 — wire the dead reminder** | Add `send_class_reminders` to the always-run tuple in `run_scheduled_tasks.py:28`. | Reminders actually send — a built-and-paid-for feature goes live. | Near-zero (one line; idempotent; dedupe already exists). |
| **1 — `ClassEmailTemplate` model + migrate welcome** | New model (§5); data migration moving `ClassOffering.welcome_email_*` rows in; repoint `send_class_welcome_email` and the teach "Emails" tab to read it. | One render path; the foundation for reminder/follow-up as data, not columns. | Low-med (data migration; reversible per CLAUDE.md). |
| **2 — reminder + follow-up as templates + toggles** | Reminder content becomes a `REMINDER_48H` row (instructor-editable, 48h default); add `FOLLOW_UP_48H` row, its `ends_at + 48h` task, and the generalized dedupe row; expose all three `is_active` toggles + test-send in the teach "Emails" tab. | The user's full requested sequence, instructor-controlled per class. | Med (new task + follow-up semantics, §4.3 decision). |
| **3 — converge system emails onto triggers/prefs** *(optional, incremental)* | Re-point `classes/emails.py` system senders through `notifications.dispatch`; add `force_email=True` to the must-send triggers; add the "new class in your guild" lead trigger. One sender per PR. | The "whole app event-driven" goal; in-app bell + push for class events; honest preference toggles. | Med, but spread thin — each sender is its own small, isolated change. |

Phase 0 stands alone and could ship this week. Phases 1–2 deliver the feature. Phase 3 is the
"standardize everything" ambition, done safely one event at a time rather than as an overhaul.

---

## 8. Open questions for the user

1. **Follow-up opt-out:** Do registrants get a *preference* to opt out of instructor follow-ups
   (route it through a `Trigger`), or is the follow-up purely transactional/instructor-driven (no
   member toggle)? Same question, lower stakes, for the welcome email.
2. **Confirmation = force_email?** Should registration confirmation / payment receipt become
   `force_email=True` triggers (always email + now also an in-app row), or stay as today's raw
   transactional sends? (Recommend the former in Phase 3.)
3. **"New class in my guild" for leads — now or later?** Move it onto a trigger in Phase 3, or ship
   it sooner as a direct guild-lead email matching the existing review-email recipient set
   (`guild.leadership_members()`)?
4. **Per-class model vs. fields — final call.** Confirm Option B's `ClassEmailTemplate` over
   extending `ClassOffering` with more field quadruples. (And: do we *also* fold the guild
   `thankyou`/`join` emails, `membership/models.py:1586-1609`, onto the same model later, or leave
   them as-is?)
5. **Follow-up timing anchor for multi-session classes:** fire once after the *last* session's
   `ends_at`, deduped on `(registration, FOLLOW_UP_48H)`? (Recommended — `Registration` has no
   session FK, so per-session follow-up would over-send.)
6. **Async queue vs. sync-in-cron:** `core.email.send` is synchronous (`core/email.py`). At current
   makerspace volume, sending inside the 15-minute cron is fine. Do we need a real async queue
   (Celery/RQ/Render worker) yet, or defer until volume demands it? (Recommend defer — YAGNI.)

---

## Appendix — file:line index

- Trigger catalogue & audiences: `core/triggers.py:16-19`, `:22-31`, `:34-129`, `:142-164`
- Dispatch / channel routing: `core/notifications.py:18-63`, `active_member_users` `:66-70`
- Preference model: `core/models.py:674-688`; in-app feed: `:643-671`; push sub: `:82`
- Scheduled-job idempotency marker: `core/models.py:691-703`
- Settings UI (saves prefs): `hub/views.py:1252-1267`; URL `hub/urls.py:109` (`/settings/?tab=notifications`)
- Email choke-point + audit log: `core/email.py:52-110`; `TransactionalEmailLog` `core/models.py:509`
- Class senders: `classes/emails.py` (table in §1.2)
- Welcome email fields: `classes/models.py:310-348`
- Guild thank-you / join email fields (same shape): `membership/models.py:1586-1628`
- Reminder task (dead): `classes/tasks.py:13-62`; command `classes/management/commands/send_class_reminders.py`
- Dedupe row: `RegistrationReminder` `classes/models.py:1245-1267`
- Reminder config: `ClassSettings.reminder_hours_before` `classes/models.py:1786-1788`
- Session time anchors: `ClassSession.starts_at/ends_at` `classes/models.py:1083-1100`
- Cron dispatcher (does NOT include reminders): `core/management/commands/run_scheduled_tasks.py:28`
- Cron service config: `render.yaml:65-73`
- Send sites: free-class path `classes/views.py:559-560`; Stripe webhook `classes/webhook_handlers.py:93-94`
- Teach "Emails" subtab: `teach_class_emails` `classes/views.py:1385`, test send `:1393`
