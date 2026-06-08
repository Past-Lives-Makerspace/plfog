# Design: Notifications, Guild Pages Redesign, Admin Activity — v2.4.0

**Date:** 2026-06-08
**Branch:** `legacy-cms-import` (targeting `main`)
**Version bump:** 2.3.2 → 2.4.0

---

## Overview

Three features shipping together as v2.4.0:

1. **Notification System** — in-app bell feed, browser push opt-in, email opt-in, 25 triggers
2. **Guild Pages Redesign** — hero image, image gallery, YouTube embed, FAQ, announcements, meetings, links, opt-in members roster (`GuildMembership`), full edit page
3. **Admin Activity Page** — site-wide event log at `/manage/activity/`, email audit log

---

## Feature 1: Notification System

### Architecture

Fan-out model: when a trigger fires, one `Notification` DB row is created per affected user immediately. At ~200 members, even a broadcast event produces 200 rows — fine at this scale. Simpler to query than a dynamic fan-out approach.

In-app notifications (the bell feed) are always-on and cannot be disabled. "Web" and "email" toggles in settings control browser push and email delivery only.

### Models (`core/`)

#### `Notification`
Append-only feed row per user per event.

```
user          FK → User (CASCADE)
trigger       CharField(40) — TextChoices key
title         CharField(200)
body          CharField(500)
url           CharField(500, blank) — where clicking navigates
read_at       DateTimeField(null, blank)
created_at    DateTimeField(auto_now_add, db_index)
```

Indexes: `(user, read_at)` for badge count, `(user, -created_at)` for feed.

#### `NotificationPreference`
One row per (user, trigger). Created lazily on first explicit save. If no row exists for a trigger, the trigger's `push_default` / `email_default` apply.

```
user          FK → User (CASCADE)
trigger       CharField(40) — TextChoices key
push_enabled  BooleanField(default=False)
email_enabled BooleanField(default=False)

Meta: constraints = [UniqueConstraint(fields=["user", "trigger"], name="uq_notificationpreference_user_trigger")]
```

**This is the first real preference storage in the app.** The existing Emails tab (`EmailPreferencesForm.voting_results` in `hub/forms.py`) is a **non-persisting stub** — the view validates it, flashes "updated," and discards the value; nothing is saved to any model. So there is no migration of existing data to do. The new system *supersedes* it:

- The old `voting_results` toggle maps onto the new `funding_results_published` email preference.
- The standalone Emails tab's notification toggle is removed; the new Notifications tab is the single source of truth for what reaches a member by push or email. (The allauth email-address management that also lives under `/settings/?tab=emails` is unrelated and stays.)

### Trigger Catalogue

Defined in `core/notifications.py` as a `Trigger` dataclass list. Each trigger has:
- `key` — matches the TextChoices value stored in DB
- `label` — shown in settings UI
- `description` — shown below label in settings
- `category` — groups rows in settings tab
- `audience` — `"all_members"` | `"instructors_only"` | `"staff_only"`. This governs **who sees the toggle row in settings**, not who receives a given event. The actual recipients of any one dispatch are chosen by the caller (e.g. `guild_announcement` is configurable by all members, but a specific announcement only dispatches to *that guild's* `GuildMembership` members).
- `force_email` — bool; if True, always sends email regardless of preference (no opt-out)
- `push_default` / `email_default` — bool defaults for new users

#### Full trigger list (25 triggers + 1 forced)

**Classes — member-side**
| Key | Label | Audience |
|-----|-------|----------|
| `class_published` | New class published | all_members |
| `class_reminder` | Class reminder (24h before) | all_members *(scheduled — see below)* |
| `registration_confirmed` | Registration confirmed | all_members |
| `class_cancelled` | Class cancelled | all_members |
| `class_details_changed` | Class details changed | all_members |
| `waitlist_spot_available` | Waitlist spot available | all_members |
| `waitlist_confirmed` | Added to waitlist | all_members |
| `refund_issued` | Refund issued | all_members |

**Classes — instructor-side** (only shown in settings for members with instructor role)
| Key | Label | Audience |
|-----|-------|----------|
| `instructor_class_approved` | Your class was approved | instructors_only |
| `instructor_changes_requested` | Changes requested on your class | instructors_only |
| `instructor_new_registration` | New registration for your class | instructors_only |
| `instructor_class_at_capacity` | Your class filled up | instructors_only |

**Guild Voting**
| Key | Label | Audience |
|-----|-------|----------|
| `voting_cycle_open` | Voting cycle open | all_members |
| `voting_closing_soon` | Voting closing soon (3 days) | all_members *(scheduled — see below)* |
| `funding_results_published` | Funding results published | all_members |

**Guild Activity**
| Key | Label | Audience |
|-----|-------|----------|
| `guild_announcement` | Guild announcement | all_members |

**Billing / Tab**
| Key | Label | Audience |
|-----|-------|----------|
| `tab_charged` | Tab charged | all_members |
| `tab_charge_failed` | Tab charge failed | all_members |
| `tab_entry_added` | Tab entry added by admin | all_members |
| `tab_approaching_limit` | Tab approaching limit (≥80% of tab limit) | all_members |

**Membership**
| Key | Label | Audience |
|-----|-------|----------|
| `invite_accepted` | Invite accepted | all_members |
| `new_member_joined` | New member joined | staff_only |

**Spaces / Leases**
| Key | Label | Audience |
|-----|-------|----------|
| `lease_expiring` | Space lease expiring soon (30 days) | all_members *(scheduled — see below)* |
| `lease_activated` | New lease activated | all_members |

**Admin Broadcasts**
| Key | Label | Audience |
|-----|-------|----------|
| `site_announcement` | Makerspace-wide announcement | all_members |

**Security (forced — no preference row, always email)**
| Key | Label | Audience |
|-----|-------|----------|
| `new_login` | New login detected | all_members (force_email=True) |

> **`new_login` mechanism.** allauth has no built-in new-device detection. Implement minimally: on `user_logged_in`, hash a `(user, user-agent, IP)` signature against a small `KnownLoginSignature` table; if unseen, record it and fire the email. This is best-effort security hygiene, not airtight device fingerprinting. If this proves fiddly during implementation, it's the lowest-value trigger here and can be deferred without affecting the other 24.

### Scheduled (time-based) Triggers

Three triggers are not event-driven — they fire ahead of a future moment and need a periodic job. The app has **no Celery/task queue**; the established pattern is a **management command invoked by a Render cron** (see `render.yaml`'s `airtable-pull` job and `billing`'s `bill_tabs`). Each command must be **idempotent** so a daily run never double-sends.

| Trigger | Job | Idempotency |
|---------|-----|-------------|
| `class_reminder` (24h before session) | **Reuse the existing machinery** — `classes/tasks.py::send_due_class_reminders()`, the `send_class_reminders` management command, and the `RegistrationReminder` tracking table already exist and already send the reminder *email*. Extend that path to also call `notifications.dispatch("class_reminder", ...)` instead of building a new job. | Existing `RegistrationReminder` rows. |
| `voting_closing_soon` (3 days before month end) | New `send_voting_reminders` management command + daily Render cron. Fires on the 3rd-to-last day of the month. | A `sent_for_cycle` marker (cycle label string) so re-runs in the same window no-op. |
| `lease_expiring` (30 days before `end_date`) | New `send_lease_expiry_reminders` management command + daily Render cron. | A per-lease "expiry reminder sent" marker. |

Each new command gets a corresponding entry added to `render.yaml`. All other triggers fire inline at their event's workflow point.

### Delivery Function

`core/notifications.py` — `dispatch(trigger_key, users, *, title, body, url="", payload=None)`:

1. Bulk-creates `Notification` rows for all `users` (always — in-app is non-optional)
2. Reads `NotificationPreference` for each user; sends browser push to those with `push_enabled=True`
3. Sends email (via `core.email.send()` — see Feature 3 — so it lands in the email audit log) to those with `email_enabled=True`
4. For `force_email=True` triggers, sends email to all users regardless of preference

Callers pass a queryset or list of User objects. Each app calls `dispatch()` at the appropriate workflow point — no signals for notification dispatch (direct calls only, to keep the dependency graph clear). Members with no linked `User` (Airtable-imported, pre-signup) are skipped — they can't receive in-app/push and have no preference rows.

### Browser Push Delivery — Build, Not Reuse

**Reality check:** the `core/` app today only *stores* push subscriptions (`PushSubscription` model; `/webpush/vapid-key/`, `/webpush/subscribe/`, `/webpush/unsubscribe/` endpoints in `core/views.py`). There is **no send code and `pywebpush` is not installed**. VAPID keys are already wired (`settings.WEBPUSH_SETTINGS`, env vars in `render.yaml`).

This feature must build push *sending* from scratch:

1. Add `pywebpush` to `requirements.txt` / lockfile.
2. New `core/push.py` — `send_web_push(subscription, *, title, body, url)`:
   - Calls `pywebpush.webpush(subscription_info, data=json.dumps({...}), vapid_private_key=..., vapid_claims={"sub": "mailto:" + VAPID_ADMIN_EMAIL})`.
   - On `WebPushException` with response status `404`/`410` (Gone), delete the dead `PushSubscription` row — endpoints expire and must be reaped or every dispatch retries dead endpoints forever.
   - Other failures are logged, not raised (push is best-effort; a failed push must never break the workflow that triggered it).
3. A user may have multiple `PushSubscription` rows (multiple devices/browsers) — push to all of them.
4. The service worker (`static/` PWA) must handle the `push` event to display the notification and the `notificationclick` event to open `url`. Verify the existing service worker handles these; add handlers if missing.

### Hub: Topbar Bell

Located top-right in `hub/base.html`. Alpine.js component:
- Badge shows unread count (HTMX poll or SSE not needed — load on page, refresh on click)
- On click: HTMX loads `/notifications/` partial into a dropdown panel
- Panel shows ~10 most recent notifications with read/unread state (unread = highlighted)
- "Mark all read" button POSTs to `/notifications/read-all/`
- Clicking a notification item POSTs to `/notifications/<pk>/read/` and navigates to `notification.url`

New URLs in `core/urls.py`:
```
/notifications/                      GET  — partial feed (HTMX only)
/notifications/unread-count/         GET  — badge count (HTMX poll)
/notifications/<pk>/read/            POST — mark one read, return redirect
/notifications/read-all/             POST — mark all read
```

### Hub: Notifications Settings Tab

New tab in `/settings/` (alongside Profile and Emails). Renders a table of triggers grouped by category. Each row:
- Trigger label + description
- Push toggle (checkbox styled as existing toggle component)
- Email toggle

Instructor-only triggers only appear if `request.user` has instructor role. Staff-only triggers only appear for staff.

On save: POST to `/settings/notifications/` — upserts `NotificationPreference` rows.

---

## Feature 2: Guild Pages Redesign

### New Models (`membership/`)

#### `GuildImage`
Mirrors `ClassImage` pattern exactly.

```
guild         FK → Guild (CASCADE), related_name="gallery_images"
image         ImageField(upload_to="guilds/images/")
alt_text      CharField(255, blank)
sort_order    PositiveIntegerField(default=0)
created_at    DateTimeField(auto_now_add)

Meta: ordering = ["sort_order", "created_at"]
```

Max 10 images enforced in form validation (not DB constraint).

#### `GuildFAQItem`
```
guild         FK → Guild (CASCADE), related_name="faq_items"
question      CharField(500)
answer        TextField
sort_order    PositiveIntegerField(default=0)

Meta: ordering = ["sort_order"]
```

#### `GuildLink`
```
guild         FK → Guild (CASCADE), related_name="links"
label         CharField(100)   — e.g. "Discord", "Wiki", "Website"
url           URLField
sort_order    PositiveIntegerField(default=0)

Meta: ordering = ["sort_order"]
```

#### `GuildAnnouncement`
```
guild         FK → Guild (CASCADE), related_name="announcements"
author        FK → User (SET_NULL, null, blank)
title         CharField(300)
body          TextField
published_at  DateTimeField(auto_now_add)

Meta: ordering = ["-published_at"]
```

Publishing a `GuildAnnouncement` fires `dispatch("guild_announcement", ...)`. Audience = the guild's members (see `GuildMembership` below), not the whole site.

#### `GuildMembership`
Explicit, opt-in guild affiliation. **Replaces the original spec's idea of deriving a roster from `VotePreference`**, which would have leaked private voting behavior — votes are admin-only today and never shown member-to-member. There is no other affiliation source in the system besides `guild_lead`, so this is a new first-class relationship.

```
guild         FK → Guild (CASCADE), related_name="memberships"
member        FK → Member (CASCADE), related_name="guild_memberships"
joined_at     DateTimeField(auto_now_add)

Meta: constraints = [UniqueConstraint(fields=["guild", "member"], name="uq_guildmembership_guild_member")]
```

- Members join/leave a guild themselves from the guild page ("Join this guild" / "Leave" button, `@login_required`, member-scoped POST).
- Guild leads/admins can also add or remove members from the edit page.
- The public roster respects each member's existing directory privacy: a member with `show_in_directory=False` (or whose `directory_visibility` hides them) is counted but **not displayed** by name on the public page. This reuses the controls already on `Member` rather than inventing new ones.

### New Fields on `Guild`

```
youtube_url        URLField(blank, default="")
meeting_schedule   TextField(blank, default="")   — plain text: "Tuesdays 6pm, Studio B"
contact_email      EmailField(blank, default="")
show_members       BooleanField(default=False)    — show the GuildMembership roster on the public page
```

Existing `banner_image` becomes the hero. No rename (backwards-compat with existing uploads).

**Access note:** `hub_guild_detail` currently has **no `@login_required`** — the guild page is publicly reachable. The "Join this guild" / "Leave" actions must be `@login_required` and member-scoped regardless. The displayed roster honors directory privacy (above), so a public visitor sees only members who already chose to be listed in the directory.

### Page Layout (`hub/guild_detail.html`)

Modelled on `classes/public/detail.html`:

```
┌─────────────────────────────────────────────────────┐
│  HERO — full-width banner_image with dark overlay   │
│  Guild name (large) + Guild Lead name               │
└─────────────────────────────────────────────────────┘

┌──────────────────────────┬──────────────────────────┐
│  MAIN (left)             │  SIDEBAR (right)          │
│                          │                           │
│  About                   │  Guild Lead card          │
│  YouTube embed           │  Members roster           │
│  Image gallery           │   (if show_members)       │
│  Meeting schedule        │  Links                    │
│  Announcements (recent)  │  Products / Store         │
│  FAQ (collapsible)       │                           │
└──────────────────────────┴──────────────────────────┘
```

Each section only renders if data exists (no empty section headers).

Gallery uses the same `classes/_components/gallery.html` component pattern (lightbox + hover zoom).

YouTube embed uses the same `youtube_embed_id` template filter from `classes/templatetags/`.

### Edit Page (`/guilds/<pk>/edit/`)

Full-page form replacing the current modal. Only accessible to guild lead of this guild or admins (`_can_edit_guild` check, already exists).

Sections on the edit page:
1. **Basic info** — name (admin only), about, meeting schedule, contact email, youtube_url
2. **Hero image** — upload/replace/delete banner_image (existing logic)
3. **Gallery** — image formset (add up to 10, reorder by sort_order, delete individual)
4. **Links** — inline formset for GuildLink (add/edit/delete)
5. **FAQ** — inline formset for GuildFAQItem (add/edit/delete)
6. **Announcements** — list of recent GuildAnnouncement with a "Post Announcement" form at top
7. **Members roster** — `show_members` toggle plus an add/remove member list backed by `GuildMembership`. When enabled, the public page shows joined members (filtered by directory privacy). Members can also self-join/leave from the public page.

The image gallery and FAQ use the same inline formset pattern as `classes/_components/image_formset.html`.

---

## Feature 3: Admin Activity Page

### Models (`core/`)

#### `SiteActivity`
Append-only event log for everything that happens site-wide.

```
actor         FK → User (SET_NULL, null, blank) — null for system events
kind          CharField(50) — TextChoices (see below)
target_ct     FK → ContentType (null, blank)
target_id     PositiveIntegerField(null, blank)
target        GenericForeignKey("target_ct", "target_id")
payload       JSONField(default=dict)
email_log     FK → TransactionalEmailLog (SET_NULL, null, blank) — set when this event sent an email
created_at    DateTimeField(auto_now_add, db_index)

Meta: ordering = ["-created_at"]
      indexes: [("-created_at",), ("kind", "-created_at"), ("actor", "-created_at")]
```

**Why an FK, not duplicated `email_status`:** an earlier draft stored `email_kind` + `email_status` directly on the activity row, which meant the sent/failed verdict lived in two places and had to be kept in sync after the send returned. Instead, `core.email.send()` (below) returns the `TransactionalEmailLog` row it wrote; the workflow point attaches it to the activity via this FK. One source of truth for delivery status; the Activity feed reads `activity.email_log.status` to render its ✉ badge.

#### `SiteActivity.Kind` TextChoices

```
LOGIN                   login
LOGOUT                  logout
PROFILE_UPDATED         profile_updated
VOTE_SUBMITTED          vote_submitted
VOTE_CHANGED            vote_changed
TAB_CHARGED             tab_charged
TAB_CHARGE_FAILED       tab_charge_failed
TAB_ENTRY_ADDED         tab_entry_added
CLASS_REGISTERED        class_registered
CLASS_REGISTRATION_CANCELLED  class_registration_cancelled
CLASS_WAITLIST_JOINED   class_waitlist_joined
CLASS_PUBLISHED         class_published
CLASS_SUBMITTED         class_submitted
CLASS_APPROVED          class_approved
CLASS_CANCELLED         class_cancelled
REFUND_ISSUED           refund_issued
FUNDING_SNAPSHOT_TAKEN  funding_snapshot_taken
MEMBER_INVITED          member_invited
INVITE_ACCEPTED         invite_accepted
MEMBER_SIGNUP           member_signup
GUILD_ANNOUNCEMENT      guild_announcement
LEASE_ACTIVATED         lease_activated
SITE_ANNOUNCEMENT       site_announcement
```

#### `TransactionalEmailLog`
One row per email attempted (sent or failed).

```
to_email      CharField(254)
subject       CharField(500)
trigger_kind  CharField(100) — which workflow sent it (e.g. "billing.receipt")
status        CharField(10, choices: "sent"/"failed")
error_message TextField(blank)
created_at    DateTimeField(auto_now_add, db_index)

Meta: ordering = ["-created_at"]
      indexes: [("-created_at",), ("status", "-created_at")]
```

### Email Wrapper

`core/email.py` — `send(*, to, subject, trigger_kind, text_body, html_body=None) -> TransactionalEmailLog`:
- Calls Django's `send_mail()`
- On success: creates and returns `TransactionalEmailLog(status="sent")`
- On exception: creates `TransactionalEmailLog(status="failed", error_message=...)`, then re-raises *unless* the caller is best-effort (notification emails swallow; transactional receipts re-raise). The returned log row lets a workflow point attach it to a `SiteActivity` via `email_log` FK.

**Transactional sends to convert** (verified locations — 14 `send_mail` calls + 2 `EmailMessage`):
- `billing/notifications.py:37,61` — `send_receipt`, `notify_admin_charge_failed`
- `classes/emails.py:60,84,107,153,172,226,253,286,310` — confirmation, instructor/admin registration notify, review requests (loop), review decision, waitlist joined, waitlist spot opened, session reminder
- `core/models.py:254` — invite email
- `core/forms.py:66` — find-account login link (transactional — include)
- `classes/forms.py:822,889` — instructor/admin bulk emails to registrants use `EmailMessage().send()` directly (BCC). Either route through `core.email.send()` or log a single summary row per blast; do **not** log one row per BCC recipient.

**Explicitly excluded** (not transactional, leave alone): `hub/forms.py:146` (`BetaFeedbackForm.send`), and the allauth login-code path in `plfog/adapters.py:142` (allauth owns it; out of scope).

### Instrumentation Points

Login/logout: allauth signals `user_logged_in` / `user_logged_out` → `SiteActivity.log()`. The login signal is also where `new_login` detection hooks in.

Profile updated: log at the **workflow point**, not a signal — in `user_settings` view after `ProfileSettingsForm.save()` succeeds. A `post_save` signal can't tell which fields changed without snapshotting old values, and would also fire for every programmatic/admin save; logging in the view captures exactly the member-initiated edit we want.

Vote submitted/changed: `VotePreference` `post_save` signal, using the `created` flag to pick `VOTE_SUBMITTED` vs `VOTE_CHANGED`.

Tab charged/failed: existing `billing/notifications.py` and `billing/webhook_handlers.py` → add `SiteActivity.log()` calls

Tab entry added: `Tab.add_entry()` → add `SiteActivity.log()`

Class events: existing `classes/activity.py` calls → also log to `SiteActivity` (both logs coexist; CmsActivity stays for class-specific detail view)

Funding snapshot: `FundingSnapshot.take()` → add `SiteActivity.log()`

Member invited / invite accepted: `Invite.create_and_send()` / `invite.mark_accepted()` → `SiteActivity.log()`

Member signup: allauth `user_signed_up` signal → `SiteActivity.log()`

Guild announcement: `GuildAnnouncement` post-save → `SiteActivity.log()`

### Page at `/manage/activity/`

**URL:** `core/urls.py` → `path("manage/activity/", views.site_activity, name="manage_activity")`
**Access:** `@staff_member_required`
**Sidebar:** Added to `UNFOLD["SIDEBAR"]["navigation"]` above "Manage Classes"

```python
{
    "title": "Site Activity",
    "icon": "monitoring",
    "link": reverse_lazy("manage_activity"),
    "permission": lambda request: request.user.is_staff,
},
```

**Layout:** Two tabs — **Activity Feed** and **Email Log**

**Activity Feed tab:**
- Paginated (50/page), descending by `created_at`
- Filter bar: Kind (dropdown), Actor (text search), Date range (from/to)
- Each row:
  - Actor avatar (initials circle) or "System" for null actor
  - Human-readable event description (rendered from kind + payload)
  - Object link (if target exists — e.g., link to class, member, guild)
  - Timestamp (relative + absolute on hover)
  - Email badge (✉ sent / ✉ failed) if `email_log` is set, reading `email_log.status`

**Email Log tab:**
- Paginated (50/page), descending
- Filter: status (sent/failed), trigger_kind, date range
- Each row: to_email, subject (truncated), trigger_kind, status badge, timestamp
- Failed rows highlighted in red
- Clicking a row expands to show full subject, error_message if failed

---

## Cross-Cutting: Shared Instrumentation Layer

`SiteActivity.log()` and `notifications.dispatch()` are often called together at the same workflow point. Pattern:

```python
# In billing/notifications.py, after a successful charge:
email_log = core.email.send(
    to=member.primary_email, subject=f"Receipt for ${charge.amount}",
    trigger_kind="billing.receipt", text_body=..., html_body=...,
)
SiteActivity.log(kind=SiteActivity.Kind.TAB_CHARGED, actor=member.user, target=charge, email_log=email_log)
notifications.dispatch("tab_charged", users=[member.user], title="Tab charged", body=f"${charge.amount} charged", url="/tab/")
```

The email send returns the log row, which the activity attaches via FK — one source of truth for delivery status. Neither `SiteActivity.log()` nor `notifications.dispatch()` is a signal receiver; they're direct calls at workflow points, keeping the call stack legible and avoiding Django signal-ordering surprises.

> **Two ways to reach a member, kept distinct.** `notifications.dispatch()` is the member-facing, preference-gated channel (bell + opt-in push/email). `SiteActivity.log()` is the admin-facing audit trail. They frequently fire at the same workflow point but answer different questions ("did the member get told?" vs "what happened on the site?"), so they stay separate rather than one calling the other.

---

## URL Changes

```
# core/urls.py additions:
/manage/activity/                    GET  — site activity page (staff)
/notifications/                      GET  — notification feed partial (HTMX)
/notifications/unread-count/         GET  — badge count
/notifications/<pk>/read/            POST — mark one read
/notifications/read-all/             POST — mark all read
/settings/notifications/             POST — save notification preferences

# hub/urls.py additions:
/guilds/<pk>/edit/images/            POST — image gallery formset (new URL)
/guilds/<pk>/announcements/          POST — post new announcement
/guilds/<pk>/announcements/<ann_pk>/delete/  POST — delete announcement
/guilds/<pk>/join/                   POST — current member joins guild (login required)
/guilds/<pk>/leave/                  POST — current member leaves guild (login required)
```

### New Dependencies & Infra

- **`pywebpush`** added to `requirements.txt` / lockfile (push sending — not currently installed).
- **Render cron entries** in `render.yaml` for `send_voting_reminders` and `send_lease_expiry_reminders` (daily). `class_reminder` reuses the existing `send_class_reminders` cron.
- Service worker (`static/`) push + notificationclick handlers verified/added.

---

## Settings Tab Order

`/settings/` tabs after this PR:
1. Profile
2. Emails *(existing)*
3. Notifications *(new)*

---

## Version & Changelog

Bump `plfog/version.py` to `2.4.0`. Changelog entry written for member-facing audience (Discord announcement).

---

## Out of Scope

- Real-time notification delivery (WebSocket / SSE) — HTMX polling on bell open is sufficient
- Notification grouping / digest emails — not needed at current member count
- Push notification action buttons — basic push only (title + body + click URL)
- Airtable round-trip for `GuildMembership` — affiliation lives in Django only; not synced to Airtable in this PR
- Adding `@login_required` to the (currently public) guild detail page — out of scope; instead the roster respects per-member directory privacy so the public view is safe
