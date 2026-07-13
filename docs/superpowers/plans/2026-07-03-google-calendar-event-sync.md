# Google Calendar event sync (FOG → Google, with a member approval queue) — Spec & Implementation Plan

**Status:** 📋 IMPLEMENTATION PLAN — **NOT YET IMPLEMENTED.** Planning document only; nothing here is built. Do not treat any field, method, view, setting, or behavior below as existing in the codebase until it ships.
**Date:** 2026-07-03
**Surface:** FOG hub (`pastlives.test:8000`) — the **Community Calendar** (`templates/hub/community_calendar.html`) and its Events tab, the **guild edit** Events + calendar-ID surfaces (`templates/hub/guild_edit.html`), the shared **add/edit event** page (`templates/hub/community_event_edit.html`), a new **member "Propose an event"** path + **reviewer queue**, and the **Site Settings** screen (member-event policy + general Google calendar ID). Admin/ops: a new management command wired into the existing Render cron.
**Related:**
- **Builds directly on** `docs/superpowers/plans/2026-06-25-community-events-and-meetings.md` — the spec that produced `CommunityEvent`, its CRUD, `announce()`, `occurrences_in`/`ical_rrule`, and the calendar-render wiring. This spec **extends** that model and those views; it does **not** introduce a new event model. (Note: the shipped `CommunityEvent` has drifted *richer* than that spec — 7-option `Recurrence`, `ical_rrule()`, `for_member()`, and a live `_require_admin` helper — this plan reflects the **shipped** code, verified below.)
- Mirrors (a lighter, single-stage version of) the class-offering **approval** workflow: `classes/models.py` `ClassOffering.Status` / `submit_for_review` / `on_review_decision_recorded` / `ClassApproval`.
- Reuses the notification spine (`emit()`, resolvers, `core/events/copy.py`) and the deferred-integration-client pattern (`core/integrations/mailchimp.py`, `simplybook.py`).

---

## 1. Summary

Today members can only *view* the Community Calendar; guild leads/staff and admins can create FOG-native events, but nothing those events do ever reaches the **Google Calendars** the makerspace and its guilds actually publish to the public. This feature closes that loop in **one direction, cleanly**: an event created inside FOG — by a member, a guild lead/staffer, or an admin — is **pushed out to the linked Google Calendar** via the Google Calendar API when it publishes, and edits/deletes in FOG propagate to Google as update/delete. Ordinary members get a real way to **propose an event**, which (by default) lands in an **approval queue** that the relevant guild's leadership or any admin clears with one decision; guild leads/staff and admins keep creating directly. Every pushed Google event records **who added it** ("Added by <Full Name> via FOG") in its description, so the public calendar stays accountable. FOG remains the source of truth: the push is best-effort and never blocks a save, sync state is visible on each event, and a retry job re-pushes anything that failed — while the existing daily iCal *read* of Google-authored events keeps working, de-duplicated against what FOG itself pushed.

### Locked decisions (from the brainstorm)

| Decision | Choice (+ why) |
|---|---|
| **Who may create, and does it need approval** | A **Site-Settings policy** field `MemberEventPolicy` (`TextChoices`) governs *member* creation: **`APPROVAL`** (default — member submissions enter a review queue before publishing/pushing), **`OPEN`** (member events publish + push immediately), **`DISABLED`** (members can't create events; only leads/staff/admins). *Why:* the makerspace wants a safety valve without hard-coding it — a toggle lets them loosen/tighten as the community grows. **Guild leads/staff and admins ALWAYS create directly**, regardless of this setting. |
| **Approval routing** | **Single-stage, guild-leadership-or-admin.** A member's *guild* event routes to that guild's `Guild.leadership_members()` (resolver `GUILD_LEADERSHIP`) **or any admin**; a member's *site-wide* event routes to admins. **ONE approval publishes it.** *Why:* the two-stage lead→admin escalation `ClassApproval` uses (with tokens) is heavier than events need — a lightweight status + reviewer fields on `CommunityEvent` is enough. (Trade-off noted in §4.) |
| **Two writable Google targets** | **Per-guild + general.** Guild events push to that guild's Google Calendar (`Guild.google_calendar_id`, new); site-wide/community events push to a general makerspace calendar (`SiteConfiguration.general_google_calendar_id`, new). Both are the Google **Calendar ID** (e.g. `abc123@group.calendar.google.com`), **distinct from** the existing read-only `Guild.calendar_url` iCal field (an iCal URL cannot be written through the API). *Why:* one calendar per audience keeps the public view organized and matches how the space already shares calendars. |
| **Auth model** | **One Google service account, shared into each calendar.** Its JSON key is loaded from a Render env var (`GOOGLE_SERVICE_ACCOUNT_JSON`), gated behind a `GOOGLE_CALENDAR_SYNC_ENABLED` flag. The admin shares each Google Calendar with the service account's email, granting **"Make changes to events."** **No per-user OAuth, no domain-wide delegation.** *Why:* the robot writes; **attribution** ("Added by <name> via FOG") lives in the event description — simplest thing that is accountable. |
| **Direction of truth** | **FOG is the source of truth; one-way push FOG→Google** for FOG-authored events. The existing daily iCal *read* of Google-authored events (`hub/calendar_service.py`) is untouched. **No two-way conflict resolution in v1.** *Why:* two-way sync is a large, error-prone feature; one-way covers the actual need (publish FOG events to the public calendar). |
| **Echo de-dup** | When FOG pushes, it stores the returned Google event ID **and** its iCal UID (`<id>@google.com`) on the `CommunityEvent`. The daily iCal read would otherwise re-import the pushed event as a `CalendarEvent`, duplicating it on the display — so the read/render path **filters out** any `CalendarEvent` whose UID matches a FOG-pushed event's stored `google_ical_uid`. *Why:* without it every FOG event shows twice. |
| **Push trigger** | **Synchronous on save** (create/edit/delete of a **PUBLISHED** event), wrapped so a Google API failure **never blocks or rolls back the FOG save**. Records `sync_state` (`PENDING`/`SYNCED`/`FAILED`) + `sync_error` + `google_event_id`/`google_calendar_id`/`synced_at`. A `retry_calendar_pushes` command re-pushes `PENDING`/`FAILED`, wired into the existing 15-min `run_scheduled_tasks` cron. **No Celery/task queue.** *Why:* the space runs on Render with a simple cron; a queue is overkill. |
| **Only PUBLISHED events push** | Pending-approval events are **FOG-only** until approved; the push fires **on approval**. Declined events never push. A published+pushed event that is later declined/deleted has its Google event **deleted**. *Why:* the public calendar should only ever show approved content. |
| **Reuse `CommunityEvent`** | Do **NOT** add a new event model. Extend `CommunityEvent` with approval-status + sync fields; reuse the existing CRUD views/forms; add a member "propose" path and a reviewer queue. *Why:* one event model, one calendar pipeline — no divergence. |
| **Recurrence mapping** | Map `CommunityEvent.ical_rrule()` into the Google event `recurrence` field. All-day events use Google's `date` form; timed events use `dateTime` + the Portland timezone (`America/Los_Angeles`). *Why:* the RRULE logic already exists; recurrence is the trickiest mapping (§5) and gets explicit test coverage (§9). |

> This spec covers **CRUD** sync: create (insert), edit (update), delete (delete), plus the member-approval lifecycle that gates *when* an event becomes eligible to push. It does **not** build two-way sync, per-occurrence exceptions, or editing a FOG event directly inside Google (§10).

---

## 2. What already exists (reuse, don't reinvent)

All confirmed in the shipped code (line numbers verified 2026-07-03 — re-verify before building; the community-events feature has already drifted past its own spec). The build is mostly assembly around one genuinely new capability: **the Google write path.**

| Need | Existing thing | Location |
|---|---|---|
| FOG-native event model to **extend** (add approval + sync fields) | `CommunityEvent` (+ `CommunityEventQuerySet`) | `membership/models.py:1977-2212` (QS `:1939-1974`) |
| The create-only, idempotent side-effect hook the push trigger sits **beside** | `CommunityEvent.announce()` (`emit`, `period="event:{pk}:published"`) | `membership/models.py:2189-2212` |
| Recurrence → RRULE (already emits `FREQ=…;BYDAY=…`, handles semi/every-N/yearly) | `CommunityEvent.ical_rrule()`, `occurrences_in()`, `_occurrence_ordinal()` | `:2134-2155`, `:2096-2132`, `:2091-2094` |
| Absolute Community-Calendar URL for notifications/attribution | `CommunityEvent.absolute_url` (`MEMBER_BASE_URL` + `reverse`) | `:2180-2185` |
| Existing event CRUD (guild-scoped + admin) to **thread the lifecycle/push through** | `guild_event_edit` / `guild_event_delete` / `event_edit` / `event_delete` | `hub/views.py:2099-2208` |
| The shared add/edit form (`as_admin` toggles `event_type`/`guild`) | `CommunityEventForm` | `hub/forms.py:965-1013` |
| Guild-scoped edit gate (view_as-aware) | `_require_can_edit_guild(request, guild)` → `can_edit_guild` | `hub/views.py:2092-2096`; `membership/permissions.py:51` |
| Admin gate (already a real helper — NOT inline anymore) | `_require_admin(request)` | used at `hub/views.py:2168, 2203` |
| Who leads a guild / who's admin / view_as | `Guild.leadership_members()`, `Member.can_edit_guild`, `Member.fog_role`, `view_as` | `membership/models.py`; `hub/view_as.py`; `membership/permissions.py` |
| **Approval workflow to mirror (single-stage, lighter version)** | `ClassOffering.Status` + `submit_for_review` + `on_review_decision_recorded` | `classes/models.py:195-199, 421-451, 563-646` |
| Heavier reference we deliberately **do NOT copy** (separate row-per-gate model + tokens + two-stage escalation) | `ClassApproval` (`role`/`decision`/`decided_by`/`notes`/`token`, `decide()`) | `classes/models.py:905-998` |
| Tokenized email-link reviewer page — **reference only, OUT of scope v1** (reviewers act from the in-hub queue) | `class_review(token)`; in-hub twin `admin_class_review(pk)` | `classes/views.py:2069-2083, 2057-2066`; shared `_class_review_view` `:2086-2133` |
| Reviewer-decision email builder to mirror (subject-noun link, edit/detail CTAs, per-outcome branches) | `send_class_review_decision(offering, row)` | `classes/emails.py:445-508` |
| Notification spine — single emission point (logs, resolves recipients, fans out, dedupes by `period`) | `emit(event_key, *, actor, target, context, url, period, …)` | `core/events/emit.py:43` |
| Resolvers to reuse — guild leadership + single user + admins | `GUILD_LEADERSHIP` (reads `context["guild"]`), `SINGLE_USER`, `fog_admins` | `core/events/resolvers.py` *(exact lines in §7, pending scout)* |
| Curated copy structure (`placeholders` ↔ `sample_context` ↔ per-`Channel` copy) + seed cmd | `_CURATED` in `copy.py`; `seed_notification_templates` | `core/events/copy.py`; `core/management/commands/` |
| Existing `event.*_published` announce events to mirror for the new lifecycle events | `event.guild_published` / `event.community_published` / `event.lead_meeting_published` | `core/events/registry.py` |
| **iCal READ-sync to leave as-is + de-dup against** | `sync_all_sources()`, `_upsert_events()` (upsert key `(guild, feed, uid)`; `uid` = raw VEVENT UID) | `hub/calendar_service.py:220-249, 78-107` (`uid` set `:39`) |
| The DB uniqueness that pins the echo (`CalendarEvent.uid`) | `UniqueConstraint(guild, feed, uid)` = `uq_calendarevent_guild_feed_uid`; `CalendarEvent.uid` `CharField(500)` | `membership/models.py:2958`, field `:2940` |
| Calendar render pipeline + **the exact echo-dedup insertion point** | `_get_calendar_context` — `CalendarEvent` qs built `:2351`, materialized `:2356`, synthetic merge `:2362-2369` | `hub/views.py:2310-2369` |
| `.ics` export — the CalendarEvent VEVENT loop that also needs the echo exclusion | `calendar_export_ics` (CalendarEvent loop `:2577-2597`; CommunityEvent loop `:2602-2617`) | `hub/views.py:2556-2624` |
| Calendar synthetic-entry adapter (source of the FOG-native rows) | `community_event_entries()`, `CalendarEntry`, `EVENT_PK_OFFSET=3e9`/`_OCC_STRIDE=100` | `hub/calendar_entries.py:108-141, 31-55, 25-28` |
| Per-guild read-only iCal field to sit `google_calendar_id` **beside** | `Guild.calendar_url` | `membership/models.py:954-969` |
| Site config singleton to add `MemberEventPolicy` + `general_google_calendar_id` + flag to | `SiteConfiguration.load()` | `core/models.py:100-221` |
| Deferred-integration client pattern to mirror (`from_settings`/`enabled`/blank=disabled/never-raises) | `MailchimpClient`, `SimplybookClient` | `core/integrations/mailchimp.py`, `simplybook.py` |
| Cron entry point (every 15 min) to wire the retry command into | `run_scheduled_tasks` | `render.yaml`; `core/management/commands/` *(exact lines in §3/§5, pending scout)* |
| The existing "+ Add event" UI to keep (now also pushing) | community-calendar Events tab; guild-edit Events section | `templates/hub/community_calendar.html:231-270`; `templates/hub/guild_edit.html:195-231` |
| The shared add/edit page to extend (member "propose" variant + sync badge) | `templates/hub/community_event_edit.html` | whole file |

### Genuinely NEW work (no reuse — call it out plainly)

The entire **Google *write* path** and the **member-submission lifecycle** are new:

1. **Google API client + credentials** — `google-api-python-client` + `google-auth` (confirmed absent from `requirements.txt`/`requirements-dev.txt` and the whole tree — a repo-wide grep for `googleapiclient`/`google.oauth2`/`from_service_account` returned nothing). Service-account JSON loaded from a Render env var behind a feature flag.
2. **The push service** — build a Google event body from a `CommunityEvent` (title/description-with-attribution/location/start-end + timezone + recurrence→`recurrence:["RRULE:…"]`), resolve the target calendar (guild vs general), and call `insert`/`update`/`delete`.
3. **Stored Google identifiers** — `google_event_id`, `google_calendar_id`, `google_ical_uid` on `CommunityEvent` (all absent today).
4. **Echo de-dup** — exclude any `CalendarEvent` whose `uid` matches a pushed event's stored `google_ical_uid`, in the render path and the `.ics` export.
5. **Sync state + retry** — `sync_state`/`sync_error`/`synced_at` + a `retry_calendar_pushes` command on the 15-min cron.
6. **Member-submission approval lifecycle** — `moderation_state`/`submitted_by`/`reviewed_by`/`reviewed_at`/`review_notes` on `CommunityEvent`, a member "propose" path, a reviewer queue, and the policy toggle. (`CommunityEvent` has **none** of these 11 fields today — all new.)

---

## 3. Where the code lives

Mirror the existing architecture: **model + business logic in `membership`** (extend `CommunityEvent`), **CRUD/queue views + templates in `hub`**, **the Google client/push service + events/copy/resolvers + the policy setting in `core`**, **a new management command**. No new Django app — everything stays inside the current coverage/mypy scope.

```
core/
  integrations/
    google_calendar.py          NEW  GoogleCalendarClient (from_settings/enabled/never-raises, mirrors mailchimp.py)
                                      + push_community_event(event) / remove_community_event(event)
                                      + _build_event_body(event) (attribution, tz, recurrence→RRULE)
  models.py                     ~    SiteConfiguration: + MemberEventPolicy TextChoices
                                      + member_event_policy, general_google_calendar_id, google_calendar_sync_enabled
  migrations/
    0040_siteconfig_event_policy_google.py            NEW  (next core number — 0039 is current head)
  events/registry.py            ~    + event.submitted / event.approved / event.changes_requested / event.declined
  events/resolvers.py           ~    (reuse GUILD_LEADERSHIP + SINGLE_USER + fog_admins; add a small
                                      "guild-leadership-OR-admins" resolver for event.submitted — see §7)
  events/copy.py                ~    + 4 _CURATED entries (placeholders == sample_context; IN_APP/EMAIL[/DISCORD off])
  management/commands/
    retry_calendar_pushes.py    NEW  re-push moderation=PUBLISHED & sync_state in (PENDING, FAILED)
  # run_scheduled_tasks         ~    add retry_calendar_pushes to the 15-min task list (§5.6)
membership/
  models.py                     ~    CommunityEvent: + moderation_state (+ ModerationState TextChoices),
                                      submitted_by, reviewed_by, reviewed_at, review_notes,
                                      google_event_id, google_calendar_id, google_ical_uid,
                                      sync_state (+ SyncState TextChoices), sync_error, synced_at
                                      + methods submit_for_review/approve/decline/publish/push_to_google/remove_from_google
                                      + QS .published() / .awaiting_review() / .pushed()
                                      Guild: + google_calendar_id CharField (beside calendar_url)
  migrations/
    0072_communityevent_moderation_sync_guild_gcal.py NEW  (next membership number — 0071 is current head)
hub/
  forms.py                      ~    CommunityEventForm: member "propose" variant (as_member flag);
                                      + SiteEventPolicyForm fields folded into the existing Site-Settings form
  views.py                      ~    + propose_event (member submit) ; + event_review_queue ;
                                      + event_review_decision (approve/changes/decline)
                                      ~ guild_event_edit / event_edit — set moderation=PUBLISHED, call push on publish/edit
                                      ~ guild_event_delete / event_delete — call remove_from_google before delete
                                      ~ community_calendar — add queue link + sync badges to Events tab
                                      ~ _get_calendar_context (:2351-2356) — echo-dedup .exclude(uid__in=…) + .published()
                                      ~ calendar_export_ics (:2577-2597) — same echo exclusion
  urls.py                       ~    + /events/propose/ , /events/propose/<pk>/edit/ , /events/<pk>/withdraw/ ,
                                      + /events/review/ , /events/review/<pk>/decision/ , /events/<pk>/retry-sync/
templates/hub/
  propose_event.html            NEW  member "Propose an event" page (reuses form_field.html; pending-review confirm)
  event_review_queue.html       NEW  reviewer queue (leads + admins): pending list + approve/changes/decline
  community_event_edit.html     ~    + sync-state badge; conditional publish/pending help line
  community_calendar.html       ~    Events tab: sync badges, "Review queue (N)" link, propose entry point
  guild_edit.html               ~    Events section: sync badges; + google_calendar_id field on the guild form
  # NOTE: the 4 workflow emails are NOT separate template files. Spine copy is authored inline as
  # body_text + body_html on the EMAIL ChannelCopy in core/events/copy.py _CURATED, wrapped by the
  # branded membership/emails/notification_shell.html (verified). Do NOT create templates/hub/emails/*.
static/css/
  hub.css                       ~    + .pl-sync-badge / --synced/--pending/--failed modifiers (theme tokens)
requirements.txt                ~    + google-api-python-client, google-auth, google-auth-httplib2
plfog/settings.py               ~    + GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_CALENDAR_SYNC_ENABLED (env-driven)
render.yaml                     ~    (env vars documented in the go-live checklist §10; no schedule change)
tests/                          NEW  spec files listed in §9
plfog/version.py                ~    VERSION bump + member-friendly CHANGELOG (final phase, at build time)
```

> **Why the Google code lives in `core/integrations/`, not `hub/`** (the choice the template asks me to justify): the read-sync (`hub/calendar_service.py`) is a *hub* concern because it feeds the hub calendar display and is driven by a command. The **write** trigger, by contrast, lives on the `CommunityEvent` **model** (`membership`), which must not import `hub` (that inverts the dependency layering — `hub` depends on `membership`, never the reverse). `core` is the lowest layer, `membership` already imports from it (`from core.events.emit import emit`), and the existing best-effort integration clients (`MailchimpClient`, `SimplybookClient`) already live in `core/integrations/`. So the push client sits there as a sibling — same `from_settings`/`enabled`/never-raises shape — and the model calls it via a lazy import, exactly as `announce()` lazily imports `emit`.

---

## 4. Data model

Additive, reversible migrations only. Every new field carries `help_text`; choice fields are `TextChoices` (CLAUDE.md model patterns).

### 4.1 `CommunityEvent` — new fields (`membership/models.py`, migration `0072`)

Two new `TextChoices` on the model, plus the eleven new fields.

```python
class ModerationState(models.TextChoices):
    PUBLISHED = "published", "Published"                    # live on the calendar; eligible to push
    PENDING = "pending", "Pending review"                   # member proposal awaiting a decision; FOG-only
    CHANGES_REQUESTED = "changes_requested", "Changes requested"  # sent back to the proposer to edit + resubmit
    DECLINED = "declined", "Declined"                       # rejected; never pushes (removed from Google if it was)

class SyncState(models.TextChoices):
    IDLE = "idle", "Not synced"          # nothing to push yet (unpublished, or a pre-existing/unmanaged row)
    PENDING = "pending", "Pending"       # published and awaiting its push / re-push
    SYNCED = "synced", "Synced"          # pushed to Google successfully
    FAILED = "failed", "Failed"          # last push errored (retry_calendar_pushes will re-try)
```

| Field | Type | Notes |
|---|---|---|
| `moderation_state` | `CharField(max_length=20, choices=ModerationState.choices, default=PUBLISHED)` | `help_text="Where this event is in the review flow. Leads/staff/admins create events already Published; member proposals start Pending."` **Default PUBLISHED** so every *existing* row and every direct-create stays live. |
| `submitted_by` | `ForeignKey(AUTH_USER_MODEL, null=True, blank=True, SET_NULL, related_name="+")` | `help_text="The member who proposed this event (for events that went through review)."` |
| `reviewed_by` | `ForeignKey(AUTH_USER_MODEL, null=True, blank=True, SET_NULL, related_name="+")` | `help_text="The lead or admin who approved, declined, or requested changes."` |
| `reviewed_at` | `DateTimeField(null=True, blank=True)` | `help_text="When the review decision was recorded."` |
| `review_notes` | `TextField(blank=True, default="")` | `help_text="The reviewer's note to the proposer (shown on a decline or a changes-requested)."` |
| `google_event_id` | `CharField(max_length=1024, blank=True, default="")` | `help_text="The Google Calendar event id returned when FOG pushed this event. Blank until pushed."` |
| `google_calendar_id` | `CharField(max_length=255, blank=True, default="")` | `help_text="Which Google calendar this event was pushed to (kept so a later edit/delete targets the right one)."` |
| `google_ical_uid` | `CharField(max_length=500, blank=True, default="")` | `help_text="The pushed event's iCal UID (<id>@google.com), used to hide the echoed copy when the daily iCal read re-imports it."` |
| `sync_state` | `CharField(max_length=12, choices=SyncState.choices, default=IDLE)` | `help_text="Google Calendar sync status for this event."` |
| `sync_error` | `TextField(blank=True, default="")` | `help_text="Why the last Google push failed (or why it's still pending, e.g. no calendar linked). Blank when synced."` |
| `synced_at` | `DateTimeField(null=True, blank=True)` | `help_text="When this event last synced to Google."` |

**Trade-off flagged (per the locked decision):** this **collapses `ClassApproval`'s separate row-per-gate + token model onto the event itself** — `moderation_state` + `submitted_by`/`reviewed_by`/`reviewed_at`/`review_notes`, one decision, no token. Lighter and correct for single-stage routing; the cost is that we don't keep a *history* of multiple review rounds (only the latest decision) and there's no no-login email-link approval (reviewers act in the hub queue — §10). Both are acceptable for v1.

**New QuerySet methods** (extend `CommunityEventQuerySet`):
```python
def published(self):        return self.filter(moderation_state=CommunityEvent.ModerationState.PUBLISHED)
def awaiting_review(self):  return self.filter(moderation_state=CommunityEvent.ModerationState.PENDING)
def pushed(self):           return self.exclude(google_ical_uid="")   # rows whose Google echo must be de-duped
def needs_push(self):       return self.published().filter(sync_state__in=[SyncState.PENDING, SyncState.FAILED])
```

> **Render pipeline must now gate on `.published()`.** `community_event_entries` (`hub/calendar_entries.py:108-141`), the `upcoming_events` list on the Events tab, and the `.ics` `CommunityEvent` loop currently render **all** rows — they must filter to `.published()` so PENDING/CHANGES_REQUESTED/DECLINED never surface on the public calendar. `upcoming()` stays as-is for the "keeps recurring" logic but is composed with `.published()` where public visibility matters. (Spec'd in §5.4.)

> **`IDLE` is a 4th `sync_state` beyond the three the brief named — flagged.** It exists so the retry job never mass-**backfills** pre-existing events (deferred, §10): the data migration sets every existing row to `sync_state=IDLE`, and `needs_push()` only selects `PENDING`/`FAILED`. A row flips `IDLE → PENDING` the moment it's *published or edited* after go-live (opt-in-by-touch). `IDLE` also renders **no badge** (§6). If you'd rather not add it, the alternative is a data migration marking existing rows `SYNCED` with a blank id — but that shows a false green check, so `IDLE` is the honest choice.

### 4.2 `Guild` — `google_calendar_id` (`membership/models.py`, same migration `0072`)

Add **beside** `calendar_url` (`:954-969`). **Refinement to the brief:** `calendar_url` is *not* read-only in code — it's an editable `URLField` surfaced in `GuildEditForm` (`hub/forms.py:55-83`). The distinction that matters is still real: `calendar_url` is the **iCal read** URL (can't be written through the API); `google_calendar_id` is the **API write** target.

```python
google_calendar_id = models.CharField(
    max_length=255, blank=True, default="",
    help_text=(
        "This guild's Google Calendar ID for pushing FOG-created events out to Google "
        "(e.g. abc123@group.calendar.google.com). Find it in Google Calendar → Settings "
        "for that calendar → 'Integrate calendar' → Calendar ID. This is NOT the iCal URL "
        "above — leave blank to keep this guild's events in FOG only."
    ),
)
```

### 4.3 `SiteConfiguration` — policy + general calendar + admin toggle (`core/models.py`, migration `0040`)

Mirror the existing `registration_mode` nested-`TextChoices` pattern (`:103-113`) and the GA-id CharField (`:180-185`).

```python
class MemberEventPolicy(models.TextChoices):
    APPROVAL = "approval", "Members can propose (needs review)"   # default
    OPEN = "open", "Members can post directly"
    DISABLED = "disabled", "Only leads and admins can post"
```

| Field | Type | Notes |
|---|---|---|
| `member_event_policy` | `CharField(max_length=20, choices=MemberEventPolicy.choices, default=APPROVAL)` | `help_text="Who can create Community Calendar events, and whether a member's event needs review before it's published. Leads, staff, and admins always post directly."` |
| `general_google_calendar_id` | `CharField(max_length=255, blank=True, default="", verbose_name="General Google Calendar ID")` | `help_text="Google Calendar ID for site-wide community events (Calendar Settings → Integrate calendar → Calendar ID — NOT the iCal URL). Blank keeps site-wide events in FOG only."` |
| `google_calendar_sync_enabled` | `BooleanField(default=False, verbose_name="Push events to Google Calendar")` | `help_text="When on (and the Google service account is configured), publishing/editing/deleting an event updates the linked Google Calendar."` |

> **Two gates, deliberately.** `settings.GOOGLE_CALENDAR_SYNC_ENABLED` (env) is the **infrastructure** master switch — it means "the service-account credentials are present and loadable." `SiteConfiguration.google_calendar_sync_enabled` (this field) is the **admin runtime** switch they flip from Site Settings without a redeploy. The push service acts only when **both** are true **and** a target calendar ID is set. This matches how the space already runs many admin-facing feature booleans (`sync_classes_enabled`, `tab_payments_enabled`).

### 4.4 Migrations

- `membership/0072_*`: `AddField` × (11 on `CommunityEvent` + 1 on `Guild`) + a `RunPython` **data migration** that sets every existing `CommunityEvent` to `moderation_state=PUBLISHED` (matches the default — so it's belt-and-suspenders) and `sync_state=IDLE` (prevents backfill). **The reverse function** sets fields back / is a no-op that's explicitly written (never `RunPython.noop` without approval — CLAUDE.md); since these are additive columns the reverse is the auto `RemoveField`, and the data step's reverse is a documented no-op (the columns vanish on reverse anyway — call this out in the migration and get sign-off, or make the reverse restore nothing since the column is being dropped).
- `core/0040_*`: `AddField` × 3 on `SiteConfiguration`. Reverse = auto `RemoveField`.
- `ruff format` both generated migrations and `git add` them in the same commit (per the migrations-need-ruff-format note).

---

## 5. Business logic (fat models + a best-effort service; views stay thin)

### 5.1 `CommunityEvent` lifecycle methods

All guards raise a domain exception (`InvalidEventTransition(ValueError)`, new, in `membership/models.py`) rather than silently no-op'ing, so a mis-wired view fails loudly in tests.

```python
def submit_for_review(self, *, submitted_by: User) -> None:
    """Member proposal path (policy == APPROVAL), used for BOTH the first submit and a
    resubmit after a changes-requested edit. Sets PENDING, records the proposer, clears
    any prior review verdict, and notifies reviewers. Does NOT announce or push (not public)."""
    # guard: a brand-new (unsaved) event OR moderation_state in {PENDING, CHANGES_REQUESTED}; else raise
    self.moderation_state = self.ModerationState.PENDING
    self.submitted_by = submitted_by
    self.reviewed_by = None; self.reviewed_at = None; self.review_notes = ""   # a resubmit clears the old verdict
    self.save(update_fields=[...])
    # A fresh period per submit round so a resubmit re-notifies reviewers (see §7 note on the period).
    self._emit_submitted()          # → event.submitted, GUILD_LEADERSHIP_OR_ADMINS

def withdraw(self, *, by: User) -> None:
    """The proposer pulls back their own not-yet-published proposal. Deletes the row —
    it was never published, so there is no Google event or announcement to unwind."""
    # guard: moderation_state in {PENDING, CHANGES_REQUESTED} AND not self.google_event_id; else raise
    self.delete()

def approve(self, *, reviewer: User) -> None:
    """Single decision → live. Records the reviewer, publishes (announce + push),
    and tells the proposer."""
    # guard: moderation_state in {PENDING, CHANGES_REQUESTED}; else raise InvalidEventTransition
    self.reviewed_by = reviewer; self.reviewed_at = timezone.now()
    self.moderation_state = self.ModerationState.PUBLISHED
    self.save(update_fields=[...])
    self.publish(actor=reviewer)                                  # announce() + push_to_google()
    self._emit_decision("event.approved", period=f"event:{self.pk}:approved")

def request_changes(self, *, reviewer: User, notes: str) -> None:
    # guard: moderation_state == PENDING; sets CHANGES_REQUESTED + notes; notifies proposer
    ...
    self._emit_decision("event.changes_requested", period=f"event:{self.pk}:changes:{int(self.reviewed_at.timestamp())}")

def decline(self, *, reviewer: User, notes: str) -> None:
    """Reject a proposal (it was never published — no Google/announce to unwind)."""
    # guard: moderation_state in {PENDING, CHANGES_REQUESTED}; sets DECLINED + reviewer + notes
    # NOTE: `notes` is required (non-empty) — enforced by the form (§6 Screen B), asserted here.
    ...
    self._emit_decision("event.declined", period=f"event:{self.pk}:declined")

def publish(self, *, actor: User | None = None) -> None:
    """Make a PUBLISHED event live everywhere: the one-shot announce (existing, idempotent
    via period) AND queue the Google push. Called by approve() and by the direct-create views."""
    self.announce(actor=actor)                     # unchanged; period="event:{pk}:published"
    if self.sync_state == self.SyncState.IDLE:
        self.sync_state = self.SyncState.PENDING
        self.save(update_fields=["sync_state"])
    self.push_to_google(actor=actor)               # best-effort; never raises
```

- **The direct-create views stop calling `announce()` directly and call `publish()` instead** (so the push is wired for leads/admins too). `publish()` is the single "it's live now" choke point. `announce()`'s existing `period` keeps it announce-once.
- `_emit_submitted()` / `_emit_decision(key, period)` are thin wrappers over `emit()` (context built in §7). `submit_for_review`/decisions pass the **submitter's `User`** as `context["user"]` for `SINGLE_USER`, and the `guild` for the union resolver.

### 5.2 The push service (`core/integrations/google_calendar.py`, NEW)

Mirrors `MailchimpClient`/`SimplybookClient`: `from_settings()`, an `enabled` property, blank/absent config = disabled, and **never raises to the caller**.

```python
class GoogleCalendarError(Exception):
    """A Google Calendar API call failed (wrapped so callers can record sync_error)."""

class GoogleCalendarClient:
    @classmethod
    def from_settings(cls) -> "GoogleCalendarClient":
        # reads settings.GOOGLE_SERVICE_ACCOUNT_JSON (raw JSON or base64) + GOOGLE_CALENDAR_SYNC_ENABLED
        # builds google.oauth2.service_account.Credentials(scopes=["https://www.googleapis.com/auth/calendar"])
        # + googleapiclient.discovery.build("calendar", "v3", credentials=..., cache_discovery=False)
    @property
    def enabled(self) -> bool: ...          # flag on AND credentials parsed OK
    def insert_event(self, calendar_id: str, body: dict) -> dict:   # returns Google event (id, iCalUID)
    def update_event(self, calendar_id: str, event_id: str, body: dict) -> dict:
    def delete_event(self, calendar_id: str, event_id: str) -> None:
    # each wraps googleapiclient HttpError → GoogleCalendarError
```

Module-level orchestration (what the model delegates to):

```python
def push_community_event(event: "CommunityEvent", *, actor: "User | None" = None) -> None:
    """Insert or update the event on its target Google calendar; update sync fields.
    NEVER raises — records FAILED/PENDING instead so the FOG save is never rolled back."""
    client = GoogleCalendarClient.from_settings()
    config = SiteConfiguration.load()
    target = event.guild.google_calendar_id if event.guild_id else config.general_google_calendar_id
    if not (client.enabled and config.google_calendar_sync_enabled):
        _mark(event, SyncState.PENDING, "Google Calendar sync is turned off."); return
    if not target:
        _mark(event, SyncState.PENDING, "No Google Calendar linked for this event yet."); return
    body = _build_event_body(event, actor=actor)
    try:
        if event.google_event_id:
            g = client.update_event(target, event.google_event_id, body)
        else:
            g = client.insert_event(target, body)
        event.google_event_id = g["id"]
        event.google_ical_uid = g.get("iCalUID", f"{g['id']}@google.com")
        event.google_calendar_id = target
        _mark(event, SyncState.SYNCED, "")
    except GoogleCalendarError as exc:
        _mark(event, SyncState.FAILED, str(exc)[:500])

def remove_community_event(event: "CommunityEvent") -> None:
    """Delete the event from Google (best-effort) BEFORE the FOG row is deleted."""
    client = GoogleCalendarClient.from_settings()
    if client.enabled and event.google_event_id and event.google_calendar_id:
        try:
            client.delete_event(event.google_calendar_id, event.google_event_id)
        except GoogleCalendarError:
            pass   # the FOG delete still proceeds; a stale Google event is a minor, loggable residue

def _build_event_body(event, *, actor) -> dict:
    who = _display_name(event.created_by or event.submitted_by or actor)
    description = (event.description + "\n\n" if event.description else "") + f"Added by {who} via FOG"
    body = {"summary": event.title, "location": event.location, "description": description,
            "start": {"dateTime": event.starts_at.isoformat(), "timeZone": "America/Los_Angeles"},
            "end":   {"dateTime": event.ends_at.isoformat(),   "timeZone": "America/Los_Angeles"}}
    rrule = event.ical_rrule()
    if rrule:
        body["recurrence"] = [f"RRULE:{rrule}"]     # reuses the model's existing RRULE builder
    return body
```

- **`CommunityEvent.push_to_google(actor=None)` / `remove_from_google()`** are thin model methods that lazy-import and delegate to the two service functions, then `save()` the sync fields (`_mark` sets them in-memory; the model saves `update_fields=[...]`). Keeps `membership → core` layering clean.
- **Attribution** (`Added by <Full Name> via FOG`) uses `created_by`'s full name (the direct author or the member proposer), falling back to the reviewer/actor. `_display_name` handles a missing name gracefully ("a Past Lives member").
- **Timezone / recurrence (the trickiest mapping — flagged).** `CommunityEvent` has **no `all_day` field** today (its events are always timed — the `.ics` export treats them as timed), so the push always uses `dateTime` + `America/Los_Angeles`; the brief's "all-day → `date`" branch is **reserved for a future `all_day` field** and is a no-op now (noted, not built). Recurrence maps straight through `ical_rrule()` — which already emits `FREQ=MONTHLY;BYDAY=2SA`, `INTERVAL=` for every-N-months, `FREQ=YEARLY;BYMONTH=…`, and the twice-a-month two-BYDAY form — into Google's `recurrence: ["RRULE:…"]`. §9 asserts each recurrence flavor round-trips.
- **Best-effort, never blocks the save:** every push is wrapped; a Google outage records `FAILED` + `sync_error` and the FOG event is saved/edited/deleted regardless. The 15-min retry job (§5.6) re-pushes.

### 5.3 Policy enforcement (validation in the form/view, not scattered)

`SiteConfiguration.load().member_event_policy` decides the member path — resolved in `propose_event` (§5.5):
- `DISABLED` → members get **403** (the propose entry point isn't shown, and the view guards it).
- `APPROVAL` → `event.submit_for_review(submitted_by=user)` (PENDING).
- `OPEN` → create as `moderation_state=PUBLISHED` and `event.publish(actor=user)` immediately.

Leads/staff/admins bypass the policy entirely (their existing create views set `PUBLISHED` + call `publish()`).

> **The two member/lead entry points are deliberately different surfaces — make this split explicit (should-fix).** `events_can_manage` on the Community Calendar Events tab is **admin-only** (`hub/views.py:2505`), so a **guild lead is a non-manager there** and sees **"+ Propose an event"** — and that is *correct*: the Community Calendar Events tab is for **site-wide** events (nullable guild), so a lead proposing there is proposing a **community** event, which routes to **admins** for review (a lead shouldn't self-approve a makerspace-wide event). A lead's **direct, no-review** path for **their own guild's** events stays where it already is: the **guild-editor Events tab** (`guild_event_edit`, gated `_require_can_edit_guild`), which publishes immediately. So: *guild's own event → guild-editor tab (direct); anything site-wide → propose (reviewed).* Leads are never forced to review-request their own guild's events.

### 5.4 Views (`hub/views.py`, all `@login_required`, thin) — new + changed

| View | Job | Gate |
|---|---|---|
| `propose_event(request, pk=None)` | **Create OR edit a member's own proposal.** No `pk` → new proposal via `CommunityEventForm(as_member=True)` (title/when/location/description/recurrence + optional guild picker); on valid POST branch on `member_event_policy` (§5.3). **With `pk` → edit** — load `get_object_or_404(CommunityEvent, pk=pk, submitted_by=request.user, moderation_state__in=[PENDING, CHANGES_REQUESTED])` (404 for anyone else, or for a published/declined event), pre-fill the same `as_member` form, and on save **re-call `submit_for_review()`** (CHANGES_REQUESTED → PENDING). For a `CHANGES_REQUESTED` event, the page shows the reviewer's `review_notes` up top so the member knows what to fix. This is the target of `edit_url` in `event.changes_requested`. Redirect to the Events tab with a Django message. | `@login_required`; new: **`policy != DISABLED`** else 403; edit: ownership+state via the scoped `get_object_or_404` |
| `my_proposals` *(context, not a view)* | `community_calendar` adds `my_proposals = CommunityEvent.objects.filter(submitted_by=request.user).exclude(moderation_state=PUBLISHED).order_by("-updated_at")` so the member sees their own Pending / Changes-requested / Declined proposals (Screen A′). | n/a |
| `event_withdraw(request, pk)` | `@require_POST`. Loads `get_object_or_404(CommunityEvent, pk=pk, submitted_by=request.user, moderation_state__in=[PENDING, CHANGES_REQUESTED])`; calls `event.withdraw(by=request.user)`; message; redirect back to the Events tab. | `@login_required`; ownership+state via scoped fetch |
| `event_review_queue(request)` | The reviewer queue. Admins see all `CommunityEvent.objects.awaiting_review()`; a non-admin lead/staffer sees only `awaiting_review().filter(guild__in=<guilds they can edit>)`. Renders the list + per-row decision controls. | reviewer = admin **or** any guild leadership; else 403 |
| `event_review_decision(request, pk)` | `@require_POST`. Loads the pending event **scoped to the reviewer's authority** (admin: any; lead: `guild__in=<their guilds>` → 404 otherwise). Binds a small `EventDecisionForm` (`decision` ∈ {approve, changes, decline}; `notes` **required when decision ∈ {changes, decline}**). **On invalid notes → re-render the queue with the error and the offending row's modal re-opened** (pass `open_decision_for=pk` + `decision_errors` to the template), **not** a silent redirect. On valid → `event.approve/request_changes/decline(reviewer=request.user, notes=...)`; Django message; redirect to the queue. A decision on an already-handled event → `InvalidEventTransition` caught → friendly "already handled" message. | same as queue, **scoped** |
| `event_retry_sync(request, pk)` | `@require_POST`. Loads any event; calls `event.push_to_google()` (best-effort) and redirects back with a message reflecting the new `sync_state`. Lets an admin re-push a `FAILED` event immediately instead of waiting for the 15-min cron. | `_require_admin` |
| `guild_event_edit` *(changed)* | On **create**, keep setting `GUILD_MEETING`/`guild`/`created_by`, then call **`event.publish(actor=request.user)`** (was `announce()`), so leads' events push. On **edit** of a published event, re-push (`event.push_to_google()` — flips `SYNCED`→`PENDING`→re-sync via `_build_event_body`, or fixes a `FAILED`). | unchanged (`_require_can_edit_guild`, guild-scoped fetch) |
| `event_edit` *(changed)* | Same: create → `publish()`; edit of a published event → re-push. | unchanged (`_require_admin`) |
| `guild_event_delete` / `event_delete` *(changed)* | Call **`event.remove_from_google()` before `event.delete()`** (needs the stored ids). `remove_from_google` never raises, so delete always proceeds. | unchanged |
| `_get_calendar_context` *(changed, `:2351-2356`)* | **(a)** echo-dedup: `events_qs = events_qs.exclude(uid__in=CommunityEvent.objects.pushed().values_list("google_ical_uid", flat=True))` before materializing at `:2356`; **(b)** ensure `community_event_entries` only contributes `.published()` rows. | n/a |
| `calendar_export_ics` *(changed, `:2577-2597 / :2602-2617`)* | Apply the **same `.exclude(uid__in=…pushed…)`** to the `CalendarEvent` loop, and `.published()` to the `CommunityEvent` loop. | n/a |
| `community_calendar` *(changed)* | Add to context: `can_review` + `review_pending_count` (queue link/badge), `member_can_propose` (policy-driven), **`my_proposals`** (Screen A′), and `google_sync_enabled` (env master AND SiteConfig toggle — drives the sync badges). Keep `events_can_manage`. `upcoming_events` → `.published()`. | n/a |

**Echo-dedup lives in exactly one query spot per read path** (the `_get_calendar_context` `events_qs` at `:2351` and the export's `CalendarEvent` loop at `:2577`), so there's no scattered filtering. The `pushed()` set is small (only FOG-pushed events) and indexed-lookup-friendly on `uid`.

### 5.5 Forms (`hub/forms.py`)

- **`CommunityEventForm` gains an `as_member` variant** (parallel to `as_admin`): shows `title/starts_at/ends_at/location/description/recurrence` **plus an optional `guild` picker** (a member proposing their guild's meeting), but **not** `event_type` (derived: guild picked → `GUILD_MEETING`; blank → `COMMUNITY`). Reuses the existing `datetime-local` widgets + `clean()` (end-after-start). Same friendly validation. The **same variant serves both create and the CHANGES_REQUESTED edit** (`propose_event(pk)`).
- **`EventDecisionForm`** (NEW, small): `decision = ChoiceField(choices=[approve, changes, decline])`, `notes = CharField(widget=Textarea, required=False)`, with a `clean()` that makes **`notes` required when `decision` ∈ {changes, decline}** ("Add a note so the proposer knows why."). This is what makes reviewer notes a real validation error (fix 6), not a silent redirect.
- **`SiteSettingsForm`** (`:518-543`) adds all three of `member_event_policy`, `general_google_calendar_id`, `google_calendar_sync_enabled` to `fields`. **All three render on ONE tab — the Calendar tab, under a "Member events & Google sync" grouping** (fix 7). *Why one tab:* the Site-Settings save is a **per-tab partial POST**, so splitting a select onto the General tab and the calendar-ID/toggle onto the Calendar tab risks a Calendar-tab save blanking `member_event_policy` (or vice-versa) unless the handler carefully merges partial POSTs. One tab sidesteps that entirely. (The select + text render default-themed; the boolean via `form_field.html`'s toggle path.)
- **`GuildEditForm`** (`:55-83`) adds `google_calendar_id` to `fields`, rendered near `calendar_url`.

### 5.6 Retry command + cron (`core/management/commands/retry_calendar_pushes.py`, NEW)

```python
class Command(BaseCommand):
    help = "Re-push community events whose Google Calendar sync is pending or failed."
    def handle(self, *args, **opts) -> None:
        if not GoogleCalendarClient.from_settings().enabled or not SiteConfiguration.load().google_calendar_sync_enabled:
            return   # self-gating — safe to run every 15 min when sync is off
        for event in CommunityEvent.objects.needs_push().select_related("guild")[:200]:
            event.push_to_google()   # best-effort; updates sync_state per event
```

- **Idempotent + self-gating** (does nothing when sync is off; only touches `PENDING`/`FAILED` published rows), so it slots straight into `run_scheduled_tasks`' **always-run tuple** (`core/management/commands/run_scheduled_tasks.py:36-43`) beside `bill_tabs` — **no `render.yaml` change** (the header comment says add tasks in the command only). Bounded slice (`[:200]`) keeps a single tick cheap.
- Never mass-backfills pre-existing events (they're `IDLE`, excluded by `needs_push()`).

---

## 6. UI / UX — completeness checklist applied per screen

Five surfaces. All use `<div class="hub-card">` sections, `pl-`/`hub-` classes, **theme tokens only**, and the component library. **No form control is ever inline-styled with `background`/`color`** (FRONTEND Rule 13) — every field renders through `form_field.html` (`.pl-form-group`), which carries input tokens and the `color-scheme` rules that fix the native datetime picker in both themes. **Verify both themes on every screen.**

> **Note on the "list editor" rubric.** The repeated thing here is a list of **top-level events, each on its own dedicated add/edit page** (exactly like Guild Meeting Notes / the existing Events tab), **not** a Django inline formset. So the famous three controls map as: **"+ Add / + Propose event"** = the primary button on the list; **per-row Delete** = the existing real `pl-btn pl-btn--danger pl-btn--sm` button → `confirm_modal.html`; **Save/Submit** = the primary button on the edit/propose page. The `extra=0` + clone-`empty_form` sub-row pattern is **N/A** (no child collection) — stated so a reviewer doesn't read it as a missing control.

---

### Screen A — Member "Propose / edit a proposal" (`templates/hub/propose_event.html`, NEW — serves create AND edit)

- **Entry point:** on the Community Calendar **Events tab**, a **`+ Propose an event`** primary button (`<a class="pl-btn pl-btn--primary">` → `hub_propose_event`) shown to any logged-in member **when `member_can_propose`** (policy ≠ `DISABLED`) and the viewer isn't already a manager (managers use the existing `+ Add event`). Note (per §5.3): a **guild lead** is a non-manager here and legitimately sees this — proposing on the *Community* tab is proposing a **site-wide** event that routes to admins; their own guild's direct-publish path is the guild-editor Events tab. When policy is `DISABLED`, the button is absent and the view 403s a hand-crafted URL.
- **Layout:** a dedicated page (5–7 fields incl. two datetime pickers → FRONTEND interaction table = dedicated page), mirroring `community_event_edit.html`: a "← Back" ghost button, `<h1 class="hub-page-title">Propose an event</h1>` (or **"Edit your proposal"** in edit mode), one `hub-card` of fields, then the Save row.
- **Changes-requested banner (edit mode):** when editing a `CHANGES_REQUESTED` event, a `hub-card` **callout at the top** shows the reviewer's `review_notes` — **"A reviewer asked for changes: {{ review_notes }}"** — so the member knows exactly what to fix before resubmitting. Guarded `{% if event.review_notes %}`.
- **Components:** `form_field.html` for **every** field (`title`, optional `guild` select, `starts_at`, `ends_at`, `location`, `description`, `recurrence`).
- **The controls, named:**
  - **Submit:** a single `pl-btn pl-btn--primary` whose label is **context-aware** — **"Submit for review"** (new under `APPROVAL`), **"Publish event"** (new under `OPEN`), **"Resubmit for review"** (edit of a `CHANGES_REQUESTED`/`PENDING` proposal) — beside a `pl-btn pl-btn--secondary` **"Cancel"** `<a href="{{ cancel_url }}">`, in a `display:flex; gap:1rem` row.
  - A muted **help line** above Save states what happens: `APPROVAL` → "Your event goes to your guild's leads (or an admin) for a quick review before it appears on the calendar."; `OPEN` → "Your event will be published to the Community Calendar right away."
- **States:**
  - **Empty (initial):** blank fields; the page *is* the form.
  - **Success (`APPROVAL` / resubmit):** redirect to the Events tab + green Django message **"Thanks — your event was submitted for review. You'll get a note when a lead or admin responds."**
  - **Success (`OPEN`):** redirect + **"Your event is live on the Community Calendar."**
  - **Error:** re-render 200 with bound values + inline `form_field.html` errors (missing title / end-before-start / start-in-past if we keep that guard) — no lost input, no 500. A non-owner or wrong-state pk → 404.
  - **Loading:** none (synchronous full-page POST).
- **Dark + light:** all through `.pl-form-group`; the `guild`/`recurrence` `<select>` popups need `select option { background; color }` — **reuse the existing rule** the community-events feature already added (`components.css`); if absent, add it (FRONTEND Rule 13, required, not optional). Datetime pickers inherit the working `color-scheme` handling.
- **Mobile:** single-column, full-width inputs; Save/Cancel stack; real button tap targets; 8px-grid spacing.

---

### Screen A′ — "My proposed events" surface (on the Community Calendar Events tab + the propose page, NEW)

**Closes the loop (blocker):** without this, a member submits a proposal and it vanishes from every surface they can see (`upcoming_events` is `.published()`-only), with nowhere to edit/resubmit a changes-requested one. This block gives them a home for their own in-flight proposals.

- **Where:** a `hub-card` section titled **"Your proposed events"** rendered from `my_proposals` (§5.4) — directly under the `+ Propose an event` button on the **Community Calendar Events tab**, and mirrored at the top of the propose page. Shown only when the member has ≥1 non-published proposal.
- **Row content:** bold `{{ event.title }}` + a muted sub-line with the date, and a **status label** — a small `.pl-status-pill` reading **"Pending review"** (`PENDING`), **"Changes requested"** (`CHANGES_REQUESTED`), or **"Declined"** (`DECLINED`), colored from theme tokens (flag the pill class if new; reuse an existing status pill if one exists). For `DECLINED`/`CHANGES_REQUESTED`, show the reviewer's `review_notes` beneath (guarded) so the member sees *why*.
- **The controls, named (per row):**
  - **Edit** — shown for `PENDING` **and** `CHANGES_REQUESTED`: `<a class="hub-btn hub-btn--sm">` → `hub_propose_event` with the pk (Screen A edit mode). The primary next step for a changes-requested proposal.
  - **Withdraw** — for `PENDING`/`CHANGES_REQUESTED`: a real `pl-btn pl-btn--danger pl-btn--sm` button → **`confirm_modal.html`** ("Withdraw this proposal? It'll be removed and won't be reviewed.") → `event_withdraw`. Each row inlines its own `confirm_modal` with a **unique `confirm_id`** (mirror the per-row pattern in `community_calendar.html:243`).
  - `DECLINED` rows are read-only (no Edit/Withdraw) — the member can start fresh with `+ Propose an event`.
- **States:**
  - **Empty:** the section is simply absent when the member has no in-flight proposals (its natural empty state); on the propose page itself, a muted line **"You haven't proposed any events yet."** under the form header when `my_proposals` is empty.
  - **Success:** withdraw → Django message "Proposal withdrawn." + redirect back to the Events tab (row gone).
  - **Error:** withdraw of a non-owned/wrong-state pk → 404; withdraw of an already-published event → not offered (guarded by state).
- **Dark + light:** `hub-card` + theme-token pills; **verify both themes.**
- **Mobile:** rows `flex-wrap` (title `flex:1; min-width:220px`; Edit/Withdraw wrap below); modal full-width.

---

### Screen B — Reviewer queue (`templates/hub/event_review_queue.html`, NEW)

Where **guild leadership + admins** clear proposals. One page, list of pending events the viewer may act on.

- **Entry point:** a **"Review queue"** link on the Community Calendar Events tab, shown when `can_review`, with a count pill when `review_pending_count > 0` (`Review queue <span class="pl-badge">3</span>`). (The `.pl-badge` count chip reuses existing hub badge styling; flag if a new class is needed.)
- **Layout:** `hub-page-title` "Events awaiting review", a one-line muted description, then a `hub-card` with one row per pending event (same `flex-wrap` row shape as the existing Events-tab rows).
- **Row content:** bold `{{ event.title }}`; a muted sub-line `Proposed by {{ event.submitted_by.get_full_name }} · {{ event.guild.name|default:"Site-wide" }} · {{ event.starts_at|date:"D, M j · g:i A" }}`; the `description` shown (guarded `{% if event.description %}`) so reviewers judge with full context — **surface the human content** (checklist §9 analog).
- **Per-row modals (nit):** each pending row inlines its **own** `modal.html` instances (request-changes + decline) with **unique ids** — `changes-modal-{{ event.pk }}` / `decline-modal-{{ event.pk }}` — so N rows don't collide on one modal id (mirror the per-row `confirm_modal` inlining at `community_calendar.html:243`). The row's buttons `$dispatch('open-modal', 'decline-modal-{{ event.pk }}')`.
- **The controls, named (per row):**
  - **Approve** — `pl-btn pl-btn--primary pl-btn--sm`, a `POST` to `event_review_decision` with `decision=approve`. Because one click here fires the Discord broadcast **and** the Google push and is not casually reversible, it is **spaced apart from the destructive controls** (its own left-aligned group, a `1rem` gap from Request-changes/Decline) **and** wrapped in a light **`confirm_modal.html`** ("Approve & publish this event? It goes live on the calendar and posts to Discord.") so it isn't fat-fingered next to Decline (nit 9). On confirm → message "Event approved and published." and the row leaves the queue.
  - **Request changes** — `pl-btn pl-btn--sm` (secondary) that opens `changes-modal-{{ event.pk }}` (`modal.html`) containing a `.pl-form-group`-wrapped **notes `<textarea>`** + a `pl-btn pl-btn--primary` "Send back for changes". Notes are **required** — enforced by `EventDecisionForm` (§5.5).
  - **Decline** — `pl-btn pl-btn--danger pl-btn--sm` that opens `decline-modal-{{ event.pk }}` (`modal.html`, not `confirm_modal.html`, because a decline carries a notes field) with a `.pl-form-group` notes `<textarea>` + a `pl-btn pl-btn--danger` "Decline event". The modal message names the consequence ("This removes the proposal; the proposer will be notified."). *(Rationale for `modal.html` over `confirm_modal.html`: the latter takes no free-text input; a decline should explain why.)*
  - All three post to `event_review_decision`; every `<textarea>` sits inside `.pl-form-group` so it is **not** a bare white box on dark (FRONTEND Rule 13).
- **States:**
  - **Empty:** `Nothing awaiting review. 🎉` (a real friendly empty state, not a blank card).
  - **Missing-notes error (fix 6):** submitting Request-changes/Decline with an empty note is a **real validation error**, not a silent redirect — the view re-renders the queue (HTTP 200) with the `EventDecisionForm` error shown **inside that row's modal, re-opened** (`open_decision_for={{ pk }}` + `decision_errors` drive an `x-init`/`x-data` that re-dispatches `open-modal` for the right id), so the reviewer sees "Add a note so the proposer knows why." with their text preserved. Client-side `required` on the `<textarea>` is the first line; server-side is authoritative.
  - **Already-handled error:** a decision on an already-decided/removed event → the guard raises `InvalidEventTransition`; the view catches it and redirects back with a message "That event was already handled." (no 500). A lead acting on another guild's event → 404 (scoped fetch).
  - **Success:** full-page POST → Django message + redirect back to the queue (now one shorter). *(Full-page action → messages, per the interaction table; the modals submit real forms, not HTMX.)*
- **Dark + light:** `hub-card` + `pl-btn` tokens; the modal textareas via `.pl-form-group`. **Verify both themes.**
- **Mobile:** rows `flex-wrap` (title `flex:1; min-width:220px`, buttons wrap below); modals are full-width and usable one-handed.

---

### Screen C — Sync-state badges on the Events surfaces (`community_calendar.html` Events tab + `guild_edit.html` Events section, changed)

The existing `+ Add event` / Edit / Delete stay; they now also push (a **view-layer** behavior change — no markup change beyond the badge). What's new on screen is a **sync badge** per row, visible to the people who manage events (staff/admins) and only when Google sync is globally on.

- **The badge:** a new `.pl-sync-badge` span with `--synced` / `--pending` / `--failed` modifiers, placed in the row's muted sub-line:
  - `SYNCED` → **"Synced to Google ✓"** (uses `--color-tuscan-yellow` or a success token — theme tokens only, flag the exact token).
  - `PENDING` → **"Sync pending"** (muted `--hub-text-muted`); when `sync_error` explains a config gap ("No Google Calendar linked…"), show that reason as **visible inline text**, not a tooltip.
  - `FAILED` → **"Sync failed"** (a red/danger token) **with the `sync_error` reason rendered as visible inline text beneath the badge** — e.g. a small `.pl-sync-badge__reason` line "Calendar not shared with the service account" — **never only a `title` tooltip** (tooltips are dead on touch, and a persistent config failure re-fails every 15 min with the cause otherwise hidden) (fix 5). If the reason is long, an Alpine `x-show` "why?" disclosure is acceptable — layout in a CSS class, never inline `display` (Rule 12).
  - `IDLE` → **no badge** (unmanaged/not-yet-published).
- **Retry sync now (admin, fix 5):** on a `FAILED` row, an admin-only `pl-btn pl-btn--sm` **"Retry sync now"** posting to `event_retry_sync` (§5.4) — a manual re-push so an admin who just fixed the Calendar ID / sharing doesn't wait up to 15 min for the cron. Lower urgency than surfacing the reason (the cron auto-retries), but paired with it. Full-page POST → message reflecting the new state.
- **Guard:** the badge block (and the Retry button) is wrapped `{% if google_sync_enabled %}` (context flag = env master AND SiteConfig toggle) so it's invisible when the feature is off — no confusing "Pending forever" for spaces not using Google.
- **States:** the list's existing empty/populated states are unchanged. A `FAILED` badge + its inline reason is the error surface; the retry job (or the Retry-now button) clears it to `SYNCED` on the next successful push.
- **Dark + light:** `.pl-sync-badge` colors come from theme tokens (define `--synced`/`--pending`/`--failed` values in `hub.css` for both `:root` and `[data-theme="light"]`). **No hardcoded hex, no `--surface` fallback.**
- **Mobile:** the badge + its inline reason live in the already-`flex-wrap` sub-line — reflow naturally, no tooltip dependency, no new width constraints; the Retry button is a real tap target.

---

### Screen D — Config forms (Site Settings + Guild edit, changed)

Two **forms**; each already has a Save button — we add fields, we don't add a new save flow.

**D1 · Site Settings (`templates/hub/admin/site_settings.html`, view `admin_site_settings` `hub/views.py:3544`)** — **all three fields on ONE tab (the Calendar tab), under a "Member events & Google sync" grouping** (fix 7). *Why one tab:* the save is a per-tab **partial POST**, so splitting the policy select onto General and the calendar fields onto Calendar risks one tab's save blanking the other tab's field unless the handler merges partials. One tab removes that failure mode entirely. (No new tab, so no change to the allowed-tabs set at `hub/views.py:3557`.)
- **`member_event_policy`** → a themed `<select>` via `form_field.html` (three options). `field_hint`: "Leads, staff, and admins always post directly. This controls what ordinary members can do."
- **`general_google_calendar_id`** → text input via `form_field.html`. `field_hint`: "Site-wide events push here. Find the Calendar ID in Google Calendar → Settings for that calendar → Integrate calendar → Calendar ID (e.g. abc123@group.calendar.google.com). This is NOT the iCal URL."
- **`google_calendar_sync_enabled`** → `toggle.html` (via `form_field.html`'s checkbox auto-detect), rendered like the Features-tab toggles. `field_hint`: "Also requires the Google service account to be configured on the server."
- **Save:** the existing per-tab **"Save"** button (`_save_site_settings`) — full-page POST → Django message. Naming unchanged.

**D2 · Guild edit (`templates/hub/guild_edit.html`, `GuildEditForm`)**
- **`google_calendar_id`** → text input via `form_field.html`, rendered **right after `calendar_url`** in the guild's calendar/settings section, so leads see the iCal-read URL and the API-write ID side by side. `field_hint`: "This guild's events push to this Google calendar. It's the Calendar ID (…@group.calendar.google.com) from Calendar Settings → Integrate calendar — not the iCal link above. Leave blank to keep this guild's events in FOG only."
- **Save:** the guild form's existing **"Save"** button — full-page POST → message. Unchanged.
- **States (both forms):** empty (blank field is valid = "not linked"); error (none beyond standard field errors — a malformed ID just fails silently at push time and shows `sync_error`, so no format validation needed in v1); success (Django message).
- **Dark + light:** both are `form_field.html` fields → theme-correct inputs and `select option` popups. **Verify both themes.**
- **Mobile:** single-column, full-width; the long help text wraps.

---

### Screen E — Shared add/edit event page (`templates/hub/community_event_edit.html`, changed)

- **Change:** for a **published** event that's been pushed, show the **sync badge** (Screen C) near the top of the form (under the title), so an editor sees "Synced to Google ✓ / Sync pending / Sync failed (reason)" and knows that saving will re-push. Guarded `{% if google_sync_enabled %}`.
- The existing publish help line stays; add one clause when sync is on: **"Saving also updates this event on the linked Google Calendar."**
- Save/Cancel, states, dark+light, mobile: unchanged from today's page (already compliant).

> **Cross-cutting dark-mode reminder (FRONTEND Rules 12–14):** every new `<textarea>`/`<select>`/`<input>` in Screens A/B/D goes through `.pl-form-group`; `select option { background; color }` is styled; no inline `display` on any `x-show` element (put layout in a class); datetime pickers keep the working `color-scheme` handling. New `.pl-sync-badge` / `.pl-badge` colors are theme tokens in `hub.css`, defined for both themes.

---

## 7. Notifications

Four new spine events, all `category="Events"`, `activity_kind=None` (workflow replies, not activity-log entries). All authored in-code: key constants + `EventType`s in `core/events/registry.py` (`_NEW_EVENTS`, `_assemble_events()` picks them up), curated copy in `core/events/copy.py` `_CURATED`, one new resolver in `core/events/resolvers.py`. The existing `announce()` on publish is **unchanged** — these are the *approval-workflow* notifications, separate from the *published* broadcast.

| Event key | Fired when | Recipient (resolver) | Context | Channels | Period |
|---|---|---|---|---|---|
| `event.submitted` | member proposal enters review (first submit **and** each resubmit) | **`GUILD_LEADERSHIP_OR_ADMINS`** (NEW union resolver) | `{"guild": event.guild, "event_title", "when", "proposer_name", "review_url"}` | `(_IN_APP_ON, _EMAIL_ON)` | `event:{pk}:submitted:{ts}` |
| `event.approved` | reviewer approves | `SINGLE_USER` (submitter) | `{"user": submitter_user, "event_title", "when", "event_url"}` | `(_IN_APP_ON, _EMAIL_ON)` | `event:{pk}:approved` |
| `event.changes_requested` | reviewer requests changes | `SINGLE_USER` (submitter) | `{"user": submitter_user, "event_title", "reviewer_notes", "edit_url"}` | `(_IN_APP_ON, _EMAIL_ON)` | `event:{pk}:changes:{ts}` |
| `event.declined` | reviewer declines | `SINGLE_USER` (submitter) | `{"user": submitter_user, "event_title", "reviewer_notes", "propose_url"}` | `(_IN_APP_ON, _EMAIL_ON)` | `event:{pk}:declined` |

- **`review_url` deep-links to the row (nit 8):** `event.submitted`'s `review_url` is `{MEMBER_BASE_URL}{reverse("hub_event_review_queue")}#event-{pk}`, not just the queue top, so a reviewer lands on the exact proposal (each queue row carries `id="event-{{ event.pk }}"`).
- **`event.submitted` period includes a timestamp (`:{ts}`) so a resubmit re-notifies reviewers.** A fixed `event:{pk}:submitted` would let `emit()`'s dedup swallow every resubmit after the first, and reviewers would never learn a changes-requested proposal came back. `submit_for_review` stamps the period with `int(timezone.now().timestamp())` (matching the changes-requested pattern). `event.approved`/`event.declined` are terminal (one-shot), so their periods stay stable.
- **`edit_url` targets the member proposal-edit route** `propose_event(pk)` (Screen A edit mode), which is member-reachable — `event_edit`/`guild_event_edit` are NOT (they're admin/lead-gated), so `changes_requested` MUST point here or the member dead-ends.

- **No Discord channel** on these four — they're per-person workflow replies, not broadcasts (contrast the `_DISCORD_ON` on the three `event.*_published` events). In-app bell + email only.
- **The `GUILD_LEADERSHIP_OR_ADMINS` union resolver (NEW, flagged).** There is no existing "leadership ∪ admins" audience; the class workflow instead uses *two separate events* (`class_review_requested → GUILD_LEADERSHIP`, `class_validation_requested → FOG_ADMINS`). For single-stage routing, **one** union resolver is cleaner. Compose the two existing resolvers and dedupe — exactly how `release_audience` composes today (`resolvers.py:409-419`):
  ```python
  def guild_leadership_or_admins(context):
      # guild set → that guild's leadership PLUS all admins; guild None (site-wide) → admins only
      return _dedupe([*guild_leadership(context), *fog_admins(context)])
  ```
  Add `Recipients.GUILD_LEADERSHIP_OR_ADMINS` + a `_RESOLVERS` map entry. For a **site-wide** proposal `context["guild"]` is `None`, so `guild_leadership` returns `[]` and only admins are notified — the exact routing the locked decision calls for, with no branching in the model. `guild_leadership`/`single_user` are **not** activation-gated (they reach any linked User with an email), so a never-signed-in lead still gets the review request — correct for a workflow reply (ref: the activation-gate note).
- **Curated copy** (`_CURATED`, mirror the `event.guild_published` shape): `placeholders` must **equal** `set(sample_context.keys())` (a test + the seed command assert this). Author bare `<p>`/`<a>`/`<strong>` HTML — the branded shell (`notification_shell.html`) + `_style_copy_fragment` inject cream-on-navy + gold links centrally, so **don't** hand-color. Each entry supplies IN_APP (`subject` + `body_text`) and EMAIL (`subject` + `body_text` **+** `body_html`, kept in sync).
  - **Link the subject noun** (checklist §9 / FRONTEND Rule 15): `event.submitted`'s CTA is **`review_url`** → the reviewer queue (absolute); `event.approved`'s is `event_url` → the Community Calendar; `changes_requested`/`declined` link `edit_url`/`propose_url` so the proposer's next step is one click. **Surface the human content:** `reviewer_notes` is shown (guarded `{% if reviewer_notes %}`) so a decline/changes email carries the reviewer's actual reason, not a bare scaffold.
  - **Absolute URLs** are the caller's job (no auto-absolutizer) — build them in the model's emit wrappers from `settings.MEMBER_BASE_URL` + `reverse(...)` (as `absolute_url` already does), never a bare `/path`.
- **Seeding:** `seed_notification_templates` iterates all registered events, so the four new ones get `NotificationTemplate` rows automatically. **Run it post-deploy** (go-live checklist, §10).

---

## 8. Build order (phased; each phase ships green)

Each phase lands green — full suite + `ruff format`/`ruff check` + `mypy`, run in the `plfog-web` Docker image (per the run-tests-in-Docker note; `--no-cov` for subsets during dev, full-cov before the phase closes). Ordered so no phase leaves a half-wired push.

1. **Model fields + migrations + policy setting.** `CommunityEvent` +11 fields (`ModerationState`/`SyncState`) + QS methods; `Guild.google_calendar_id`; `SiteConfiguration` +3 (`MemberEventPolicy`); `membership/0072` (incl. the `IDLE`/`PUBLISHED` data migration) + `core/0040`. **No behavior change yet** — existing rows default `PUBLISHED`/`IDLE`, calendars render exactly as today. *Specs: field defaults, QS `.published()/.awaiting_review()/.pushed()/.needs_push()`, constraint still holds.*
2. **Approval lifecycle + member proposal loop + reviewer queue + notifications (100% FOG-side, no Google).** `submit_for_review`/`approve`/`request_changes`/`decline`/`withdraw`/`publish` (**`publish()` in this phase = `announce()` + set `sync_state=PENDING`; it does NOT yet call `push_to_google` — that method doesn't exist**); `InvalidEventTransition`; the **full member loop** — `propose_event(pk=None)` (create + owner-scoped edit) + `my_proposals` context + `event_withdraw` + the "Your proposed events" surface (Screen A′) — plus `event_review_queue` + `event_review_decision` (with `EventDecisionForm`'s required-notes validation + re-open-modal error state); the four events + copy + the union resolver + `review_url` anchor; wire the create paths to `member_event_policy`; direct-create views call `publish()` instead of `announce()`. *(Members propose, edit/resubmit/withdraw, reviewers decide, everyone's notified, events publish — all with zero Google. Green because publish's push is a no-op stub.)* *Specs: policy branches, every transition + guard + side-effect, the member edit/withdraw ownership+state gating, missing-notes validation, view gating (member/lead/admin/view_as), form validation, template states, copy lock-step, resolver audiences.*
3. **Google service module + credentials + requirements (behind the flag, not yet wired).** Add `google-api-python-client`/`google-auth`/`google-auth-httplib2` to `requirements.txt`; `GOOGLE_SERVICE_ACCOUNT_JSON`/`GOOGLE_CALENDAR_SYNC_ENABLED` in `settings.py`; `core/integrations/google_calendar.py` (`GoogleCalendarClient` + `push_community_event`/`remove_community_event`/`_build_event_body`); `CommunityEvent.push_to_google`/`remove_from_google` delegators. **Unit-tested against a fake/`respx`-mocked client — never a real Google call.** **Not yet invoked from publish/edit/delete** (that wiring is Phase 4). *(The push machinery exists and is proven in isolation; the request path still behaves exactly as Phase 2 because `publish()` doesn't call `push_to_google` yet, so the suite stays green — the default env/SiteConfig state is sync-off anyway.)* *Specs: enabled/disabled gating, insert vs update, body mapping incl. attribution + tz + each recurrence flavor, never-raises, sync-field bookkeeping.*
4. **Wire push into publish/edit/delete + echo de-dup.** `publish()` calls `push_to_google`; the edit views re-push a published event; the delete views call `remove_from_google()` before `delete()`; `_get_calendar_context` (`:2351`) + `calendar_export_ics` (`:2577`) exclude `pushed()` UIDs; `community_event_entries`/`upcoming_events`/export gate on `.published()`. *Specs: create/edit/delete push calls fire (mocked), echo row hidden from context + export, published-only visibility, a Google failure doesn't roll back the FOG save.*
5. **Retry command + cron.** `retry_calendar_pushes` + add to `run_scheduled_tasks` always-run tuple. *Specs: re-pushes `PENDING`/`FAILED` published rows, skips `IDLE`/unpublished, self-gates when sync off, bounded slice.*
6. **Sync-state badges + retry-now + calendar-ID config UI.** `.pl-sync-badge` (+ theme tokens, + the inline `FAILED` reason line) on the Events surfaces; the admin-only **`event_retry_sync`** "Retry sync now" button; `member_event_policy`/`general_google_calendar_id`/`google_calendar_sync_enabled` into `SiteSettingsForm` + `site_settings.html` (**all three on the Calendar tab**); `google_calendar_id` into `GuildEditForm` + `guild_edit.html`; the propose entry point + review-queue link + `google_sync_enabled` context flags. *Specs: badge renders per sync_state + inline reason on FAILED + hidden when off/IDLE; retry-now re-pushes (admin-only, mocked); form fields save; policy gates the propose button; both themes.*
7. **Housekeeping (at build time).** `ruff format . && ruff check .`; bump `plfog/version.py` `VERSION` (a patch on `release-0.20.x`, exact number decided at build time) + **one** member-friendly `CHANGELOG` entry, e.g. *"You can now propose events for the Community Calendar right from FOG — your guild's leads or an admin give it a quick look, and once it's approved it shows up on the calendar (and on the makerspace's public Google Calendar too). Leads and admins post directly, as before."* Finalize this doc's status.

> Spec only — do not build until approved. Phases 1–2 are shippable member value on their own (proposals + review) even before any Google wiring; Phases 3–6 add the Google push incrementally.

---

## 9. Testing

BDD `*_spec.py` in each app's `spec/`-style location, `describe_*`/`it_*` only (**`context_*` is NOT collected** — use `describe_*` for nested blocks), factory-boy, full type hints, **≥98% coverage** (mirror CI's SQLite run in a throwaway `plfog-web` container). **Never hit Google** — mock `GoogleCalendarClient` with a fake or `respx`-stubbed discovery/HTTP layer; in-app/email asserted via `EventDelivery` rows / `mail.outbox`. Extend `CommunityEventFactory` with traits: `pending` (`moderation_state=PENDING`, `submitted_by` set), `declined`, `pushed` (`google_event_id`/`google_ical_uid`/`sync_state=SYNCED`).

- **Model lifecycle — `tests/membership/community_event_lifecycle_spec.py`:**
  - `submit_for_review` sets `PENDING` + `submitted_by`, clears prior review fields, emits `event.submitted`; **a resubmit re-notifies** (distinct `:{ts}` period → a 2nd `EventDelivery` per reviewer, not deduped away); raises `InvalidEventTransition` from an illegal state.
  - `approve` requires `PENDING`/`CHANGES_REQUESTED`, sets `PUBLISHED` + `reviewed_by`/`reviewed_at`, calls `publish()` (announce fires once via its period; `push_to_google` invoked — mocked), emits `event.approved` to the submitter.
  - `request_changes` sets `CHANGES_REQUESTED` + `review_notes`, emits `event.changes_requested`; a subsequent `submit_for_review` returns it to `PENDING` and clears the prior notes.
  - `decline` requires `PENDING`/`CHANGES_REQUESTED` (raises from `PUBLISHED`/`DECLINED`), sets `DECLINED` + notes, emits `event.declined`; a declined event never appears in `.published()`.
  - `withdraw` requires `PENDING`/`CHANGES_REQUESTED` and deletes the row; raises `InvalidEventTransition` from `PUBLISHED`/`DECLINED`; no Google/announce side-effect (never pushed).
  - `publish` announces once, flips `IDLE→PENDING`, calls push; a second `publish` doesn't double-announce (period).
- **Push service — `tests/core/integrations/google_calendar_spec.py`** (all mocked):
  - **Disabled** (env flag off **or** SiteConfig toggle off **or** blank target) → `push_community_event` records `sync_state=PENDING` + an explanatory `sync_error`, makes **no** API call, never raises.
  - **Insert** (no `google_event_id`) → calls `insert_event`, stores returned `id`/`iCalUID`/target `google_calendar_id`, `sync_state=SYNCED`, `synced_at` set, `sync_error=""`.
  - **Update** (existing `google_event_id`) → calls `update_event`, not `insert`.
  - **Failure** (`HttpError`→`GoogleCalendarError`) → `sync_state=FAILED`, `sync_error` truncated, no raise, FOG event unchanged otherwise.
  - **`remove_community_event`** deletes when ids present; swallows a delete error; no-ops when disabled/unpushed.
  - **`_build_event_body`**: attribution line `Added by <name> via FOG` from `created_by`/`submitted_by`; timed `dateTime` + `America/Los_Angeles`; **recurrence round-trip** — `NONE` → no `recurrence` key; `MONTHLY`/`SEMI_MONTHLY`/`EVERY_2_MONTHS`/`EVERY_3_MONTHS`/`EVERY_6_MONTHS`/`YEARLY` → the exact `["RRULE:<ical_rrule()>"]` (assert against the model's `ical_rrule()`, e.g. `FREQ=MONTHLY;BYDAY=2SA`, `INTERVAL=2`, `FREQ=YEARLY;BYMONTH=…`).
- **Echo de-dup / visibility — `tests/hub/community_calendar_sync_spec.py`:**
  - A `CalendarEvent` whose `uid` equals a `pushed()` event's `google_ical_uid` is **excluded** from `_get_calendar_context` output and from `calendar_export_ics`; a non-matching `CalendarEvent` still appears.
  - `PENDING`/`CHANGES_REQUESTED`/`DECLINED` events do **not** appear on the calendar, the Events-tab `upcoming_events`, or the `.ics`; `PUBLISHED` ones do.
  - An un-pushed published event (blank `google_ical_uid`) is **not** wrongly excluded.
- **Retry command — `tests/core/retry_calendar_pushes_spec.py`:** re-pushes `PUBLISHED` + `PENDING`/`FAILED` (mocked push called), skips `IDLE`/unpublished, self-gates to a no-op when sync off, respects the slice bound.
- **Views / gating — `tests/hub/event_review_spec.py` + `tests/hub/propose_event_spec.py`:**
  - `propose_event` (create): `DISABLED` → 403; `APPROVAL` → creates `PENDING` via `submit_for_review`; `OPEN` → creates `PUBLISHED` + publishes. Non-authenticated → login redirect.
  - `propose_event(pk)` (**member edit — the loop-closing case**): the owner editing their own `PENDING`/`CHANGES_REQUESTED` event → 200, save re-calls `submit_for_review` (back to `PENDING`); the `review_notes` banner renders for a `CHANGES_REQUESTED` event; **a different member's event → 404**; the owner's own **`PUBLISHED`/`DECLINED`** event → 404 (not editable); `event.changes_requested`'s `edit_url` resolves to this route.
  - `my_proposals` context: a member sees exactly their own non-published proposals with the right status labels; another member's proposals don't appear; empty → the "haven't proposed any" state.
  - `event_withdraw`: owner of a `PENDING`/`CHANGES_REQUESTED` event → row deleted + message; **non-owner → 404**; owner's `PUBLISHED` event → 404 (not offered). POST-only.
  - `event_review_queue`: a plain member → 403; a lead sees only their guild's pending (not another guild's); an admin sees all. `review_pending_count` correct; each row has `id="event-{pk}"` (anchor target).
  - `event_review_decision`: **cross-guild isolation** — a lead of A POSTing a decision on B's pending event → 404, B unchanged (mirror the existing event cross-guild isolation test). Approve/changes/decline call the right model method; **a missing note on changes/decline re-renders the queue (200) with the form error and `open_decision_for` set to that pk** (no state change, no redirect); a decision on an already-handled event → friendly redirect, no 500.
  - `event_retry_sync`: admin re-pushes a `FAILED` event (mocked push called, message reflects new state); a non-admin → 403; POST-only.
  - Changed create/edit/delete views: create calls `publish()` (push mocked); edit of a published event re-pushes; delete calls `remove_from_google()` before delete (mocked, ordering asserted).
  - Site-Settings + Guild forms: the three SiteConfig fields (all on the Calendar tab) + `Guild.google_calendar_id` save; a Calendar-tab save **doesn't blank `member_event_policy`** (one-tab guarantee); a member (non-admin) can't reach `admin_site_settings`.
- **Copy/audience — `tests/core/events/event_workflow_copy_spec.py`:** for each of the 4 events `placeholders == set(sample_context)`; `guild_leadership_or_admins` returns leadership + admins for a guild event and **admins only** for a site-wide (guild=None) event, deduped, excludes plain members; `event.approved/changes/declined` resolve to the submitter's User.
- **Gotchas:** freeze `timezone.now()` for review-timestamp + recurrence-window tests; seed a `MembershipPlan` before any member-gated login (the signal skips Member creation otherwise); set explicit `starts_at`/`ends_at`; override `MEMBER_BASE_URL` when asserting absolute URLs; the union-resolver test builds a lead via `GuildFactory(guild_lead=…)`/`GuildStaffMembershipFactory`, not a bare member.

---

## 10. Open / deferred / out of scope

### Flagged for confirmation (new conventions I'm introducing — call them out per the "don't invent unilaterally" rule)
1. **`SyncState.IDLE`** — a 4th value beyond the brief's `PENDING`/`SYNCED`/`FAILED`, to prevent the retry job from mass-backfilling pre-existing events and to represent unpublished/unmanaged rows. (§4.1 — recommended; alternative rejected as dishonest.)
2. **`Recipients.GUILD_LEADERSHIP_OR_ADMINS` + `guild_leadership_or_admins` resolver** — a small, necessary backend addition (composes two existing resolvers, like `release_audience`). No existing "leadership ∪ admins" audience. (§7.)
3. **New `pl-`-prefixed classes** — `.pl-sync-badge` (+ `--synced/--pending/--failed` + `.pl-sync-badge__reason`), a `.pl-badge` review-count chip, and a `.pl-status-pill` for the proposal status labels (Pending review / Changes requested / Declined). **All colors are theme tokens, no new brand color** introduced. If any duplicates an existing hub badge/pill, reuse that instead of adding a class. (§6 Screens A′/B/C.)
4. **Two sync gates** (`settings.GOOGLE_CALENDAR_SYNC_ENABLED` env master + `SiteConfiguration.google_calendar_sync_enabled` admin toggle) rather than one — confirm the space wants a runtime toggle separate from the credential gate. (§4.3.)

### Deferred (explicitly not built in v1)
- **Tokenized email-approval links.** Reviewers act from the **in-hub queue** (Screen B). The class workflow's no-login `/classes/review/<token>/` page + `ClassApproval.token` machinery are **out of scope** — a future add if reviewers want to approve straight from the email.
- **Take down / un-publish an already-live event via the review flow.** `decline` is tightened to `PENDING`/`CHANGES_REQUESTED` only (nit 10) — it's a *proposal* verdict, not a takedown. Removing an already-`PUBLISHED` event (which would need `remove_from_google()` + an un-announce) is done by **deleting** it (the delete views already call `remove_from_google()`); a dedicated "unpublish but keep the draft" state is a future add if wanted.
- **Two-way sync / editing a FOG event *inside* Google.** One-way FOG→Google only. An edit made directly in Google is **not** pulled back for FOG-authored events (the daily iCal read still imports Google-*authored* events as before, de-duped against pushes). Conflict resolution is a future project.
- **Bulk push / backfill of pre-existing events.** Existing rows are `IDLE` and never auto-push; they opt in when next published/edited. A one-shot "backfill all published events to Google" command is a future nicety.
- **All-day events.** `CommunityEvent` has no `all_day` field; the push always uses timed `dateTime`. Google's `date` (all-day) form is reserved for when/if an `all_day` field is added.
- **Per-occurrence exceptions / editing a single instance of a recurring series** — edit/delete affects the whole series (unchanged from the community-events feature). Recurrence beyond the existing 7 options is likewise unchanged.
- **A per-event detail page** — notifications still point at the Community Calendar / the review queue; a dedicated event page remains a future nicety.

### Go-live checklist (ops prerequisites — not code, but required to switch it on)
1. **Create a Google Cloud project** and enable the **Google Calendar API**.
2. **Create a service account**, generate a **JSON key**, and set it on Render as **`GOOGLE_SERVICE_ACCOUNT_JSON`** (raw JSON or base64 — the client accepts either); set **`GOOGLE_CALENDAR_SYNC_ENABLED=true`**. (Env writes need a manual redeploy on Render.)
3. **Share each Google Calendar** (the general makerspace calendar + each guild's) with the **service account's email**, granting **"Make changes to events."**
4. **Enter the Calendar IDs** in FOG: `SiteConfiguration.general_google_calendar_id` (Site Settings → Calendar) and each `Guild.google_calendar_id` (guild edit). Flip **Site Settings → "Push events to Google Calendar"** on.
5. **Post-deploy:** run `python manage.py seed_notification_templates` (registers the 4 new event templates) via a Render one-off job.
6. **Verify:** create a test event, confirm it appears on the Google Calendar with the "Added by … via FOG" description, edit it, delete it, and confirm the daily iCal read doesn't duplicate it (echo de-dup). Confirm `retry_calendar_pushes` clears a `FAILED` after a transient outage.

> Spec only — do not build until approved.
