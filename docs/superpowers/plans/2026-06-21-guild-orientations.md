# Guild Orientations — Spec & Implementation Plan

**Status:** Spec / not yet built
**Date:** 2026-06-21
**Surface:** FOG hub (`pastlives.test:8000`) — guild pages, member booking, admin/lead dashboard
**Related:** Builds on the guild-pages expansion (`2026-06-21-guild-pages-expansion.md`) and the configurable-email pattern (`2026-06-21-instructor-welcome-email.md`).

---

## 1. Summary

Let each **guild** run its own **orientations**: a guild lead publishes orientation times (recurring hours and/or
one-off slots, each with a seat cap), writes an info page, and members who aren't yet oriented for that guild book a
slot from a small calendar on the guild page. A booking is a **request, not a confirmed booking**, until the lead
accepts it. Accept / decline / cancel / reschedule all fire emails (iCal-attached), in-app notifications, and site
activity, and can be driven **from the email with no login** via signed links. A members/leads-only **Orientations**
sidebar page lists upcoming + completed orientations with filters and CSV export, and auto-completes past orientations.

Two configurable follow-up emails ride along (both reuse the welcome-email editor pattern): a **"thanks for
orienting / next steps"** email and a **"welcome to the guild"** email on join. The broader "email digest for all
sorts of events" idea is split into its own future spec: `2026-06-21-email-notifications-system.md`.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Oriented-status scope | **Per guild/shop.** Tracked as a record per `(member, guild)`. No single global flag. |
| Availability model | **Both** — a weekly recurring rule that auto-generates slots **+** manually-added one-off slots. |
| Reschedule depth | **Lightweight** — accept / decline / decline-with-note; the member rebooks a different slot themselves. No counter-proposal state machine. |
| Who can book | **Members only.** Every booking ties to a `Member`; the section is gated on the logged-in member's per-guild oriented status. |

---

## 2. What already exists (reuse, don't reinvent)

Everything below was confirmed in the codebase — this feature is mostly assembly of existing plumbing.

| Need | Existing thing | Location |
|---|---|---|
| In-app + push + email notifications, **opt-out per trigger** | `notifications.dispatch()`, `Notification`, `NotificationPreference`, `core.triggers.TRIGGERS` | `core/notifications.py:18`; `core/models.py:628-673`; `core/triggers.py` |
| Site activity feed | `SiteActivity.log()` (generic FK + `email_log` link) | `core/models.py:606-625` |
| Transactional email (HTML+text, audited) | `core.email.send()` → `TransactionalEmailLog` | `core/email.py:16-70`; `core/models.py:500-527` |
| **Configurable, lead-editable email** | `ClassOffering.welcome_email_*` + `TeachWelcomeEmailForm` + `_components/welcome_email_form.html` + `send_class_welcome_email()` | `classes/models.py:305-343`; `classes/forms.py:955-1000`; `classes/emails.py:85-122` |
| **No-login token actions** | `Registration.self_serve_token` (view + cancel unauthenticated); `signing.dumps(..., salt=...)` relay | `classes/models.py:1319-1366`; `classes/views.py:686-729`; `core/views.py:37-79` |
| **iCal generation** | `icalendar>=6.0` already a dep; existing `.ics` export | `requirements.txt:18`; `hub/views.py:1273-1321` |
| **Scheduled jobs** | `run_scheduled_tasks` dispatcher (every 15 min) + `ScheduledNotificationMarker` idempotency | `render.yaml:50-73`; `core/management/commands/run_scheduled_tasks.py`; `…/send_voting_reminders.py` |
| **Sortable/filterable/paginated admin table** | `prepare_table()` + `{% sort_header %}` + `table_pagination.html` | `classes/table.py:12-60`; `classes/templatetags/classes_tags.py:57-70` |
| **CSV streaming export** | `_Echo` + `StreamingHttpResponse` | `classes/exports.py:23-111` |
| **Admin / guild-lead gating** | `classes_admin_access_required`, `classes_registrations_access_required`, `_scoped_registrations`, `view_as.has_actual(...)` | `classes/views.py:786-842`; `hub/view_as.py` |
| Guild edit authority | `Guild.guild_lead` FK; `can_edit_guild()`; `_require_can_edit_guild()` | `membership/permissions.py:51-56`; `hub/views.py:456-460` |
| Guild social/contact links | `GuildLink` (label+url+sort, `related_name="links"`), already renders | `membership/models.py:798-810`; `templates/hub/guild_detail.html:274-281` |

**Two infra gaps to close (small):**

1. **Email attachments.** `core.email.send()` uses `django.core.mail.send_mail`, which has no attachment support. Add an
   optional `attachments: list[tuple[str, bytes|str, str]]` param and switch internally to `EmailMultiAlternatives`
   when html and/or attachments are present (backward-compatible; existing callers unchanged). Needed for the `.ics`.
2. **No single `is_oriented` flag exists.** Per the locked decision we derive status per-guild from completed bookings
   (`Member.is_oriented_for(guild)`), so no global boolean is added. (If Airtable ever needs a denormalized flag, that's
   a separate follow-up.)

---

## 3. Where the code lives

Mirror the guild-pages architecture exactly: **models + business logic in `membership`** (next to `Guild`), **views +
templates in `hub`** (next to `guild_detail`, the admin pages, `member_directory`), **email builders + `.ics` in a new
`membership/orientations.py`** module. This keeps everything inside the existing coverage/mypy scope (`membership`,
`hub`) — no new Django app, no new app-config/coverage wiring.

```
membership/models.py            # 4 new models + Member.is_oriented_for()
membership/orientations.py      # email builders, .ics builder, slot-generation + auto-complete logic
membership/forms.py             # settings/availability/slot/booking/decline forms
membership/permissions.py       # orientations_access_required (admin OR is a guild lead)
membership/management/commands/generate_orientation_slots.py
membership/management/commands/auto_complete_orientations.py
core/management/commands/run_scheduled_tasks.py   # register the two new commands
core/email.py                   # + attachments support
core/triggers.py                # + orientation/guild-join triggers
hub/views.py                    # guild-page section, info page, book, signed-link actions, dashboard, export, config editor
hub/urls.py                     # new routes
templates/hub/partials/guild_orientation.html
templates/hub/orientation_info.html
templates/hub/orientation_action.html          # no-login signed-link landing
templates/hub/orientations_dashboard.html      # sidebar page (mirror admin/registrations.html)
templates/hub/orientation_settings.html        # lead/admin config editor
templates/membership/emails/orientation_*.{html,txt}   # request, lead_request, confirmed, declined, cancelled, thankyou
templates/membership/emails/guild_welcome.{html,txt}
```

---

## 4. Data model (`membership/models.py`)

### 4.1 `GuildOrientationSettings` (OneToOne → Guild)

Per-guild config + the two configurable emails. 1:1 so `Guild` isn't bloated further and is created lazily.

| Field | Type | Notes |
|---|---|---|
| `guild` | `OneToOneField(Guild, related_name="orientation_settings")` | |
| `is_enabled` | `BooleanField(default=False)` | Master switch — orientation offered on this guild's page. |
| `info` | `TextField(blank=True)` | Orientation info-page body (plain text, preserved line breaks — same convention as `welcome_email_body`). |
| `default_seats` | `PositiveSmallIntegerField(default=4)` | Default capacity for new/generated slots. |
| `default_location` | `CharField(max_length=200, blank=True)` | Where orientations happen. |
| `default_duration_minutes` | `PositiveSmallIntegerField(default=60)` | Used to compute `ends_at` for generated slots. |
| `is_closed` | `BooleanField(default=False)` | "Closed for orientation" toggle. |
| `closed_message` | `CharField(max_length=300, blank=True)` | e.g. "On vacation till 9/8." Shown on the guild page when closed. |
| `thankyou_email_enabled` / `_subject` / `_body` / `_updated_at` | mirror `welcome_email_*` | "Thanks for orienting / next steps" email, lead-editable. |
| `join_email_enabled` / `_subject` / `_body` / `_updated_at` | mirror `welcome_email_*` | "Welcome to the guild" email on join, lead-editable. |
| `created_at` / `updated_at` | timestamps | |

Properties: `thankyou_email_ready`, `join_email_ready` (mirror `welcome_email_ready`).
Helper: `Guild.orientation` property returning (and lazily creating) the settings row.

### 4.2 `OrientationAvailability` (recurring rule)

| Field | Type | Notes |
|---|---|---|
| `guild` | `FK(Guild, related_name="orientation_rules")` | |
| `weekday` | `PositiveSmallIntegerField` (0=Mon…6=Sun, TextChoices) | Weekly recurrence. (Monthly is out of scope — orientation hours are weekly in practice.) |
| `start_time` / `end_time` | `TimeField` | |
| `seats` | `PositiveSmallIntegerField` | Capacity for generated slots. |
| `location` | `CharField(max_length=200, blank=True)` | Optional per-rule override of `default_location`. |
| `is_active` | `BooleanField(default=True)` | |

A cron generates concrete `OrientationSlot` rows from active rules for a rolling window (next ~8 weeks), **idempotently**
(`UniqueConstraint(availability, starts_at)`), skipping when the guild is closed.

### 4.3 `OrientationSlot` (a concrete bookable appointment)

| Field | Type | Notes |
|---|---|---|
| `guild` | `FK(Guild, related_name="orientation_slots")` | |
| `availability` | `FK(OrientationAvailability, null=True, related_name="slots", on_delete=SET_NULL)` | Provenance for generated slots; null for one-offs. |
| `source` | `CharField` TextChoices `MANUAL` / `GENERATED` | |
| `starts_at` / `ends_at` | `DateTimeField` | Concrete datetimes (tz-aware; remember the local-date gotcha from guild calendar specs). |
| `seats` | `PositiveSmallIntegerField` | Total capacity. |
| `location` | `CharField(max_length=200, blank=True)` | |
| `is_cancelled` | `BooleanField(default=False)` + `cancelled_reason` | Cancelling a slot cancels its active bookings (each fires the cancel flow). |
| `created_at` | timestamp | |

Manager `OrientationSlotQuerySet`: `for_guild(guild)`, `upcoming()`, `bookable(guild)` (future, not cancelled, guild
enabled & not closed, seats remaining).
Properties: `seats_taken` (active bookings), `seats_remaining`, `is_full`, `is_past`, `is_bookable`.

### 4.4 `OrientationBooking` (the request **and** the orientation record)

| Field | Type | Notes |
|---|---|---|
| `slot` | `FK(OrientationSlot, related_name="bookings")` | |
| `guild` | `FK(Guild, related_name="orientation_bookings")` | Denormalized from `slot.guild` on save — keeps dashboard filters/scoping cheap and survives slot edits. |
| `member` | `FK(Member, related_name="orientation_bookings")` | Members-only. |
| `status` | `CharField` TextChoices: `REQUESTED` (default) / `CONFIRMED` / `DECLINED` / `CANCELLED` | Request lifecycle (lightweight — no counter-proposal). |
| `is_completed` | `BooleanField(default=False)` | Attendance. Auto-trues after the slot ends for confirmed bookings; lead/admin can uncheck & save. **This is what marks the member oriented for the guild.** |
| `oriented_by` | `FK(Member, null=True, related_name="orientations_given", on_delete=SET_NULL)` | "Who gave them" — defaults to the guild lead on confirm, editable. |
| `member_note` | `TextField(blank=True)` | Optional note the member adds when requesting. |
| `lead_note` | `TextField(blank=True)` | Decline/cancel reason or follow-up suggestion from the lead. |
| `requested_at` / `confirmed_at` / `declined_at` / `cancelled_at` | timestamps | |

No DB token field — email links use **signed tokens** (`django.core.signing`, salt per action, payload = booking pk),
matching the existing relay pattern, decoded in no-login views. Avoids a token column and is revocation-free-but-bounded
(max_age = until the slot ends).

Manager `OrientationBookingQuerySet`: `for_guild`, `active()` (REQUESTED|CONFIRMED, not cancelled), `upcoming()`,
`completed()`, `pending()`.

### 4.5 Derived oriented-status (no new flag)

```python
# Member
def is_oriented_for(self, guild: "Guild") -> bool:
    return self.orientation_bookings.filter(guild=guild, is_completed=True).exists()

def active_orientation_for(self, guild: "Guild") -> "OrientationBooking | None":
    return self.orientation_bookings.filter(guild=guild, status__in=[REQUESTED, CONFIRMED]).first()
```

The guild page shows the orientation **booking** section only when `member.is_oriented_for(guild)` is False **and** there's
no active booking; if there's an active booking it shows **status** instead ("Requested — awaiting confirmation" /
"Confirmed for Jun 25, 6pm"); if oriented it shows a subtle "✓ You're oriented for this guild."

---

## 5. Business logic (fat models)

All on the models; views stay thin. Each mutator does its DB change **and** fans out (activity + notification +
transactional email). Raise a domain `OrientationError(ValidationError subclass)` on invalid transitions.

- `OrientationSlot.book(member, *, note="") -> OrientationBooking`
  Guards: slot bookable; guild enabled & not closed; member not already oriented for this guild; member has no active
  booking for this guild; seats remaining. Creates `REQUESTED`. Fans out: **lead email** (with accept/decline signed
  links), **member email** ("Request received — *this is not an official booking yet*", with **TENTATIVE** `.ics`),
  `SiteActivity.log(ORIENTATION_REQUESTED)`, `dispatch("orientation_requested", [lead.user], …)` (in-app/push).
- `OrientationBooking.confirm(*, by, oriented_by=None) -> None`
  → `CONFIRMED`; default `oriented_by` to guild lead. Member email ("Confirmed!", **CONFIRMED** `.ics` update) + activity
  + member notification.
- `OrientationBooking.decline(*, by, note="") -> None`
  → `DECLINED`, store `lead_note`; member email ("Couldn't confirm — here's a note / pick another time", link back to
  guild slots) + activity + notification. Frees the seat.
- `OrientationBooking.cancel(*, by, actor_label) -> None`
  → `CANCELLED`; frees seat; fires "{actor} canceled the orientation" to **the other party** (+ lead) — email
  (**CANCELLED** `.ics`) + activity + notification. Used by member-cancel, lead-cancel, and slot-cancel.
- `OrientationBooking.mark_completed()` / `uncomplete()`
  Toggle `is_completed`. On true → member becomes oriented for the guild; optionally send the configurable
  **thank-you/next-steps** email (if `thankyou_email_ready`). Activity logged either way.
- `OrientationSlot.cancel(reason)` → cancels slot + each active booking via `booking.cancel(actor_label="the guild")`.
- Admin/lead **add member to slot**: `slot.book(member, …)` invoked by staff — identical fan-out, so the member gets the
  same emails "as if they booked themselves," then optionally auto-confirm.

Cron logic (in `membership/orientations.py`, called by management commands):
- `generate_slots(window_weeks=8)` — for each active rule, ensure slots exist across the window; idempotent; skip closed
  guilds.
- `auto_complete(now)` — confirmed, non-cancelled bookings whose `slot.ends_at < now` and not `is_completed` →
  `mark_completed()` (idempotent via the boolean), which sends thank-you emails.

---

## 6. Emails & iCal

Builders in `membership/orientations.py`; templates under `templates/membership/emails/` (dark-theme card style, paired
`.html` + `.txt`, like `templates/classes/emails/`). All sent via `core.email.send(trigger_kind="orientations.*", …)`
so they're audited in `TransactionalEmailLog`.

| Email | To | Trigger kind | iCal | Notes |
|---|---|---|---|---|
| Request received | member | `orientations.request` | TENTATIVE | Bold "**NOT an official booking yet**"; cancel/reschedule signed links. |
| New request | guild lead | `orientations.lead_request` | — | Accept / Decline / "view on FOG" signed links. |
| Confirmed | member | `orientations.confirmed` | CONFIRMED (update) | Cancel/reschedule signed links. |
| Declined | member | `orientations.declined` | CANCELLED | Lead's note + "pick another time" link. |
| Cancelled | other party (+lead) | `orientations.cancelled` | CANCELLED | "{actor} canceled the orientation." |
| Thanks / next steps | member | `orientations.thankyou` | — | **Configurable** by lead (welcome-email editor pattern). On completion. |
| Welcome to the guild | member | `guild.welcome` | — | **Configurable**; on guild join. See §9. |

**iCal**: `build_orientation_ics(booking, *, method, status) -> bytes` using the `icalendar` library (cleaner than the
hand-rolled `hub/views.py` version). Single VEVENT: stable `UID` (e.g. `orientation-{pk}@pastlives`), `DTSTART`/`DTEND`
from the slot, `SUMMARY` "Orientation — {guild}", `LOCATION`, `DESCRIPTION` with status + management link;
`METHOD:REQUEST` with `STATUS:TENTATIVE/CONFIRMED`, or `METHOD:CANCEL` + `STATUS:CANCELLED` for cancellations so Google/
Outlook update the existing event. Attached via the new `core.email.send(attachments=[(…, ics, "text/calendar")])`.

**Transactional vs opt-out:** the operational orientation emails (request/lead/confirmed/declined/cancelled) **always
send** — they're transactional. The opt-out-able ones (`guild_joined` lead notification, "welcome to the guild") go
through `NotificationPreference` via `dispatch()` / trigger `email_default`. Define the orientation triggers with
`email_default=False, force_email=False` so `dispatch()` only does in-app + push (and we send the rich email ourselves),
avoiding double emails.

---

## 7. No-login signed-link actions

Mirror `registration_self_serve` (`classes/views.py:686-729`). One landing view, action chosen by the signed payload:

- `GET /guilds/orientations/act/<token>/` → `orientation_action` decodes `signing.loads(token, salt=…, max_age=…)`
  → `{booking_id, action, role}`; renders `orientation_action.html` showing the booking + the buttons allowed for that
  role (member: **cancel**, **reschedule** [= link to guild slots]; lead: **accept**, **decline**). No login required.
- `POST` same URL performs the action (`confirm` / `decline` / `cancel`), then shows a confirmation + a "view on FOG"
  link (which *does* route through login for full management).
- Invalid/expired token → friendly error page (not a 500), matching the self-serve view's `get_object_or_404` posture.

Authenticated equivalents live on the dashboard (`§8`) and on the booking row actions, gated to admin or that guild's
lead via `_require_can_edit_guild(request, booking.guild)`.

---

## 8. Orientations dashboard (sidebar page)

Members/leads-only sidebar page. **Read is cross-guild for any lead/admin** (per the ask: "guild leads should see
upcoming orientations all guilds"); **write/CRUD on a booking** is restricted to admin or that booking's guild lead.

- **Gating:** new `orientations_access_required` decorator in `membership/permissions.py` — admin (`view_as.has_actual
  ("admin")`) **or** `Member.is_guild_lead` (leads ≥1 guild). Mirrors `classes_registrations_access_required` but the
  read scope is all-guilds rather than editable-only.
- **Sidebar link:** add to `templates/hub/base.html` nav, visible when admin or `is_guild_lead`.
- **Two sections/tabs:**
  - **Upcoming Orientations** — all guilds (REQUESTED + CONFIRMED, future). Inline accept/decline/cancel/reschedule +
    "add member to slot." Pending requests highlighted.
  - **Completed Orientations** — sortable table: date · guild · member · `oriented_by` · **completed** (editable
    checkbox, auto-trued by cron) · status. Uncheck-and-save supported.
- **Filters:** guild; "mine" (orientations I'm giving / my guild); status; **date range**. Built on `prepare_table()`
  (`classes/table.py:12-60`) + `{% sort_header %}`; paginated via `components/table_pagination.html`.
- **CSV export:** `GET …/orientations/export/` → `StreamingHttpResponse` via the `_Echo` pattern
  (`classes/exports.py:23-111`), preserving the active filters (`base_params`).
- **Template:** mirror `templates/classes/admin/registrations.html` structure (search form + selects + sort headers +
  pagination + export button).

---

## 9. Guild page: orientation section + social/contact polish

### 9.1 Orientation section (`templates/hub/partials/guild_orientation.html`)
Injected into `guild_detail` (gated on a member who isn't oriented for the guild). Shows: a **small calendar/list of
bookable slots** (next few weeks, seats-remaining badge), a **"Book"** action → `orientation_book`, a link to the
**info page**, and — when `is_closed` — the `closed_message` banner instead of slots. If the member has an active
booking, show its **status** rather than the booker.

### 9.2 Social / contact (mostly reuse `GuildLink`)
- **"Email guild lead" button** — prominent in the guild hero/contact card; `mailto:` to `guild.guild_lead.primary_email`
  (fallback `guild.contact_email`).
- **Discord channel + socials** — render `GuildLink` rows as styled social buttons (icon per recognized label:
  Discord, Instagram, website, …). `GuildLink` already exists and renders (`guild_detail.html:274-281`) — this is
  styling + an **editor UI** in the guild editor (add/remove links) if one doesn't already exist.
- **Recommendation (minor):** add first-class `discord_url` + `website_url` `URLField`s on `Guild` for clean dedicated
  buttons ("#woodworkers Discord channel"), and keep `GuildLink` for arbitrary extras. Small migration; optional — can
  ship purely on `GuildLink` if you'd rather not add fields.

---

## 10. Notifications / triggers / activity

Add to `core/triggers.py` (each opt-out-able unless noted):

| Trigger key | Audience | Email default | Purpose |
|---|---|---|---|
| `orientation_requested` | guild lead / staff | (rich email sent directly; in-app via dispatch) | Lead hears about a new request. |
| `orientation_status` | member | direct rich email | Confirmed/declined/cancelled to the member (in-app mirror). |
| `guild_joined` | guild lead | opt-out | "New member joined your guild." Also the "welcome to the guild" member email. |

Add `SiteActivity.Kind` values: `ORIENTATION_REQUESTED`, `ORIENTATION_CONFIRMED`, `ORIENTATION_DECLINED`,
`ORIENTATION_CANCELLED`, `ORIENTATION_COMPLETED`, `GUILD_JOINED`. Log via `SiteActivity.log(kind, actor=…, target=
booking, email_log=…)` so the activity row links the email it sent. The guild "pulse" (`hub/views.py:_guild_pulse`)
can optionally surface recent completed orientations and joins.

---

## 11. Scheduled jobs

Two idempotent management commands, registered in `run_scheduled_tasks.py` (runs every 15 min):
- `generate_orientation_slots` — daily window-fill (gate `if now.hour == X`), idempotent via `UniqueConstraint`.
- `auto_complete_orientations` — runs each tick (cheap), completes confirmed bookings whose slot has ended; idempotent
  via the `is_completed` boolean. Sends thank-you emails as a side effect of `mark_completed()`.

---

## 12. Build order (phased; each phase ships green)

> Spec only for now — do not build until approved. When built, follow the standing "commit each phase green, then roll
> to the next" workflow.

0. **Infra** — `core.email.send(attachments=…)`; add triggers + `SiteActivity.Kind` values.
1. **Models + logic** — 4 models, migration, managers, `Member.is_oriented_for`, all mutators (`book/confirm/decline/
   cancel/complete`), factories, model specs (seat limits, closed-guild, duplicate-prevention, lifecycle, oriented
   derivation). No UI.
2. **Config editor** — `orientation_settings.html` for leads/admins: enable, info, default seats/location/duration,
   closed toggle + message, recurring rules, one-off slots, the two configurable emails (reuse welcome-email component).
3. **Member booking** — guild-page section + info page + `book` → REQUESTED, request emails (`.ics`), activity/
   notifications.
4. **Actions** — signed-link no-login views (accept/decline/cancel/reschedule) + authenticated equivalents + confirmed/
   declined/cancelled emails + `.ics` updates.
5. **Dashboard** — sidebar page, upcoming + completed tables, filters, CSV export, "add member to slot."
6. **Cron** — `generate_orientation_slots` + `auto_complete_orientations` wired into the dispatcher; thank-you email.
7. **Guild social/contact** — email-lead button, Discord/social buttons, link editor; `guild_joined` notification +
   "welcome to the guild" join email + activity.
8. **Housekeeping** — bump `plfog/version.py` `VERSION` + member-friendly `CHANGELOG`; finalize this doc.

---

## 13. Testing (BDD `*_spec.py`, ≥98% gate, run in Docker `plfog-web`)

- **Models:** `book()` seat-limit / closed-guild / already-oriented / duplicate-active guards; `confirm/decline/cancel`
  transitions + side-effects (use factories; assert `TransactionalEmailLog` + `SiteActivity` + `Notification` rows);
  `is_oriented_for`; `mark_completed/uncomplete`; slot capacity properties.
- **iCal:** `build_orientation_ics` produces a valid VEVENT with correct DTSTART/STATUS/METHOD per state.
- **Signed links:** each action via valid token; invalid/expired token → friendly error, no mutation.
- **Crons:** `generate_orientation_slots` idempotency + closed-guild skip; `auto_complete_orientations` completes only
  past confirmed bookings and is idempotent.
- **Dashboard:** lead sees all-guild upcoming; scoping on mutations (lead can't confirm another guild's booking);
  filters; CSV headers/rows; pagination.
- **Guild page:** section shows only for un-oriented members; status rendering for active bookings; closed banner.
- **Email/notifications:** transactional emails always send; opt-out respected for `guild_joined`.
- Watch the **local-tz date-window gotcha** seen in guild-calendar specs (use `now + timedelta(days=2)` for slot
  fixtures, not `now`).

---

## 14. Open / deferred

- **Counter-proposal flow** (lead proposes a specific alternative slot the member one-click confirms) — deferred;
  lightweight decline-with-note chosen. Easy to layer on later (add a `RESCHEDULE_PROPOSED` status + a signed confirm
  link) without reworking the model.
- **Monthly orientation recurrence** — out of scope; weekly rules + one-offs cover real usage.
- **Guest/prospective-member booking** — out of scope (members-only chosen). Would need a public entry point + guest-by-
  email identity, like the class register flow.
- **Dedicated `discord_url`/`website_url` on Guild** — recommended but optional; can ship on `GuildLink` alone.
- **Broader event-digest emails** — split into `2026-06-21-email-notifications-system.md`.
