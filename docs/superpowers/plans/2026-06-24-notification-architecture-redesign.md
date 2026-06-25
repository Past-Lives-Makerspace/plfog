# Notification & Event Architecture Redesign — Design Plan

**Status:** Design — **decisions resolved (see §1A)**, ready to build pending final go-ahead.
**Date:** 2026-06-24
**Goal:** Tear out the scattered, one-off email/notification/trigger code and replace it with a single
**event-driven spine**: the app emits *events*; events resolve *their own recipients*; each recipient
receives the event on the *channels they've enabled*; channels (in-app, email, scheduled email, push,
**Discord**, digest) are pluggable. Copy is **admin-editable** with a catalogue for the copy team. One
unified preferences page. **No dead/obsolete notification code left behind** — the strangler migration is
a *build technique*; the merged PR is the *finished* system.

> Builds the spine that features `2026-06-24-event-driven-notifications-spike.md` (#8, automated class
> emails) and `2026-06-21-email-notifications-system.md` (#9, digests) sit on top of (§10). Supersedes the
> spike's "Option C, not now" call — the audit (§1) shows the mess is real and growing, which is when
> standardizing first becomes correct.

---

## 1A. Resolved decisions (locked with the user, 2026-06-24)

1. **Forced (un-mutable) emails = essentials + operational.** A member can never turn off: payment
   receipts, login/security alerts, booking confirmations, password/account changes, **class reminders**,
   and **orientation updates**. Everything else is opt-out-able.
2. **Recipients are role × scope** (not one flat "admin"):
   - **`fog_admins()` — GLOBAL.** FOG admins get site-wide admin emails (all class approvals everywhere,
     payment/billing, validation, new-member alerts). = `fog_role == ADMIN` ∪ `*_NOTIFY_EMAILS`.
   - **`guild_leadership(guild)` — GUILD-SCOPED.** A guild lead **and their guild's staff** get the emails
     for **their own guild only** (e.g. a class-approval request for a class in *their* guild). A guild lead
     does **not** get global FOG-admin mail.
   - **`guild_lead(guild)` — GUILD-SCOPED, lead only.** Some events are lead-only; staff don't receive them.
   - **`guild_orienters(guild)`** — lead + members holding the `ORIENTER` role.
   Each event picks its audience by both **role** and **scope**. This replaces the 3 incompatible "admin"
   resolutions and the ad-hoc per-site resolution.
3. **`bill_tabs` is wired into cron in this work** — receipts + failed-charge retries start running
   automatically. (Billing is sensitive; it gets extra test scrutiny and a reversible touch.)
4. **Dead triggers:** **build `guild_announcement` + `site_announcement` as real broadcast features**;
   fold `voting_cycle_open` into the new voting emails (item 5); **delete** `class_details_changed`,
   `instructor_class_at_capacity`, `lease_activated` (re-add as real events if ever wanted).
5. **New events/emails to ADD in this work:**
   - **Voting closing — 48h reminder** to **all voters**, 2 days before the automated month-end close.
   - **Voting results + allocation breakdown** email after the close.
   - **Release / changelog email** ("a new version has been released!") to **all members + admins +
     everyone who has logged in**, sourced from `plfog/version.py` `CHANGELOG` (mirrors the existing
     Discord changelog post).
   - **Member invitation to FOG** email (a real, templated invite event).
   - **Guild announcement** + **site-wide announcement** broadcasts (from item 4).
6. **Copy is fully admin-editable (DB-backed).** All subject/body copy lives in the DB; the **copy team
   edits everything in an admin catalogue** (no deploy). Code only **seeds** initial values. The catalogue
   lists **every** event — including locked/forced ones — with its lock status, resolved audience, channels,
   and a **live preview**; edits are **versioned** (history + revert). Editing is via a **constrained
   merge-field** system per event (documented placeholders like `{{ member_name }}`), not raw template code.
7. **Orientation routing fixed:** in-app fan-out to **all orienters** (not just the lead) when an
   orientation needs running, **and** the staffer who actually runs it is credited (today it's always
   mis-credited to the guild lead).
8. **Allauth login/auth emails route through the audited choke-point** (`core.email.send` +
   `TransactionalEmailLog`) — closes the biggest unaudited gap; extra care near the auth flow.
9. **Discord is built now** as a real channel adapter (webhook-based), not just an interface. Discord is a
   **per-event broadcast** channel (post to a configured webhook/channel), distinct from the per-recipient
   channels — its routing is event→webhook, configured in the admin area (§2.4).

---

## 0. Source-of-truth audit

Current-state inventory (read before building):
`scratchpad/audit-A-emails.md` (26 send sites), `audit-B-core-notifications.md` (trigger/dispatch/prefs),
`audit-C-scheduled.md` (cron/dedupe/dead jobs), `audit-D-recipients.md` (recipients + guild staff +
orientation), `audit-E-activity-signals.md` (activity kinds + signals). Fold these into `docs/` before the
scratchpad clears.

---

## 1. The current mess (why we're doing this)

Two subsystems coexist and barely share plumbing: **A** = the `core` event system (partially adopted),
**B** = direct transactional sends (ignore preferences). Damage, from the audit:

| Problem | Evidence |
|---|---|
| **24 one-off email senders** ignore `NotificationPreference` | audit-A §8 |
| **~10 double-send pairs** (rich email + un-suppressed `dispatch()`) | audit-A §5; only `classes/tasks.py:57` is correct |
| **4 sends bypass the choke-point** → unaudited (incl. ALL allauth/login email) | `classes/forms.py:867,934`, `hub/forms.py:270`, `plfog/adapters.py:145` |
| **"Admin" resolved 3 incompatible ways** | `_admin_recipients()` vs `is_staff=True` vs `BILLING_ADMIN_EMAILS` — audit-D §4 |
| **Recipients resolved ad-hoc at ~25 sites**; trigger `Audience` enum is UI-only | audit-D §2 |
| **2 unreconciled "trigger" vocabularies** | audit-A §6 — can't join audit log to prefs |
| **3 dedupe patterns** | `ScheduledNotificationMarker` / `RegistrationReminder` / ad-hoc bool — audit-C |
| **Decorative prefs** | `EmailPreferencesForm`, ~11 dead toggles, 6 never-dispatched triggers — audit-A §6, audit-B |
| **Dead-but-shipped** | `bill_tabs` (receipts!) never wired; 3 `SiteActivity` kinds never written — audit-C/E |
| **Orientation asymmetry** | email→lead+staff, in-app→lead-only; runner mis-credited; no 48h reminder — audit-D §3 |

The activity logs (`SiteActivity` 29 kinds, `CmsActivity` 17 kinds) are already a de-facto event catalogue;
every workflow hand-sequences `activity.log()` then `dispatch()`. **That pairing is what the event bus
subsumes.**

---

## 2. Target architecture — one spine

```
   something happens (fat-model method / service)
              │
              ▼
   events.emit(EVENT_KEY, *, actor, target, context)
              │
   ┌──────────┼───────────────────────────────┐
   ▼          ▼                                 ▼
 activity   recipient resolver (role × scope)   channel fan-out
 log row    context → [(user, reason), ...]      per-recipient channels:
                                                  ├─ InAppChannel   (bell)
                                                  ├─ EmailChannel   (choke-point + log + DB copy)
                                                  ├─ PushChannel    (web push)
                                                  ├─ ScheduledEmailChannel (timed)   ← orientation 48h, #8
                                                  └─ DigestChannel  (buffer + batch) ← #9
                                                 per-event broadcast channel:
                                                  └─ DiscordChannel (event → webhook)  ← built now
```

### 2.1 Event registry
Generalizes `core/triggers.py`. Each `EventType` declares `key` (one vocabulary = activity kind + audit
label + preference key), `label`, `description`, `category`, `recipient_resolver` (§3), `channels` with a
default state (`on`/`off`/`forced`), and `activity_kind`. Event **definitions** stay in code (versioned);
event **copy** is DB-backed and admin-editable (§2.3).

### 2.2 `events.emit()` — single emission point
Writes the activity row, resolves recipients, fans out to each enabled channel; one idempotency hook (§2.5)
makes it safe to re-run from schedulers. Replaces every `activity.log()` + `dispatch()` pair. Signals that
do notification work (`_on_login`, `_on_signup`) re-point to `emit()`; no new business logic in signals.

### 2.3 Admin copy catalogue (DB-backed, editable, versioned) — Decision 6
- **`NotificationTemplate(event_key, channel, subject, body_text, body_html, is_overridden, updated_by,
  updated_at)`** — one row per (event, channel) needing authored copy. Seeded from code defaults by a
  management command / data migration.
- **`NotificationTemplateVersion`** (or `django-simple-history`) — every edit snapshotted; revertible.
- **Constrained merge fields:** each event documents a fixed placeholder set (`{{ member_name }}`,
  `{{ class_title }}`, …); rendering substitutes only those (no raw Django template execution from the DB —
  safety). A per-event sample context drives **live preview**.
- **Catalogue UI** (hub admin surface): lists **every** event grouped by category, showing lock status
  (forced vs opt-out-able), resolved audience description, enabled channels, current copy, and a preview —
  for copy-team review/approval and editing. This is a first-class deliverable (Phase 3).

### 2.4 Channels — pluggable adapters
Common `Channel` interface + registry. Per-recipient channels: **InApp**, **Email** (renders DB copy →
`core.email.send` choke-point → `TransactionalEmailLog`; honors forced/prefs), **Push**,
**ScheduledEmail** (timing via §2.6), **Digest** (#9). Per-event **broadcast** channel: **Discord**
(Decision 9) — posts an embed to a configured webhook; routing is **event → webhook** (e.g.
`release.published` → #announcements, `class.review_requested` → a guild's staff channel), configured in
the admin area, **not** per-user. Adding any future channel = one adapter + register + a prefs/admin column;
**zero changes to events or call sites.**

### 2.5 One idempotency model
`EventDelivery` keyed `(event_key, target_ref, channel, period)` unique. Folds in `RegistrationReminder`
and the orientation `is_completed`-as-dedupe.

### 2.6 Scheduling
The proven window+dedupe pattern (`send_due_class_reminders`) generalizes: a scheduled event declares an
anchor (`±offset` from a datetime); the existing 15-min `run_scheduled_tasks` cron walks due events,
dedupes via §2.5, and calls `emit()`. One cron home for orientation 48h reminders, class reminders, #8,
voting 48h reminders, and the digest flush.

### 2.7 Unified preferences
`NotificationPreference(user, event_key, channel, enabled)` (one row per user/event/channel) generalizes
today's push/email columns. The `/settings/?tab=notifications` page becomes an **event-category × channel**
matrix; new channels appear automatically; `forced` rows render locked-on. `EmailPreferencesForm` and
`/settings/emails/` are **deleted**.

---

## 3. Recipient resolvers — role × scope (Decision 2 + 7)

Each returns `[(User, reason)]` (`reason` = role/why, for templates + digests). One tested home replaces
~25 ad-hoc sites.

- **`fog_admins()` — GLOBAL** — site-wide admin mail (all approvals, billing, validation, new-member).
- **`guild_leadership(guild)` — GUILD-SCOPED** — lead + all staff of *that* guild; used for **email and
  in-app** (fixes the asymmetry). Guild-scoped class approvals route here, not to global admins.
- **`guild_lead(guild)`** — lead-only events.
- **`guild_orienters(guild)`** — lead + ORIENTER-role staff (orientation "needs a runner").
- **`orientation_runner(booking)`** — the staffer who claimed/ran it (requires `confirm_orientation` to
  pass the acting member instead of defaulting to `guild_lead` — Decision 7).
- **`registrant` / `instructor` / `next_waitlisted` / `tab_member` / `inviter` / `lease_tenant` /
  `all_active_members` / `all_voters` / `everyone_with_login` / `single_user`** — the rest (audit-D §2);
  `all_voters` and `everyone_with_login` are new (for voting + release emails).

### Orientation events (Decision 7)
| Event | Resolver | Channels (default) |
|---|---|---|
| `orientation.requested` | `guild_orienters(guild)` — **email AND in-app** | email forced-ish*, in_app on |
| `orientation.claimed` | runner (confirmation) + `guild_orienters` minus claimer ("Pat took this") | in_app on |
| `orientation.reminder_48h` | `registrant` (+ runner) | scheduled_email (`slot.starts_at − 48h`) |
| `orientation.confirmed/declined/cancelled` | `registrant` | email forced (orientation updates), in_app on |
| `orientation.completed` | `registrant` (thank-you) | email on |
\*orientation updates are in the forced set (Decision 1) for the member; staff request pings are opt-out-able.

---

## 4. New events to build (Decision 4 + 5)

| Event key | Trigger | Resolver | Channels |
|---|---|---|---|
| `guild.announcement` | guild lead/staff posts an announcement | guild members (scoped) | in_app, email (opt-out), discord(optional) |
| `site.announcement` | admin posts a site broadcast | `all_active_members` | in_app, email (opt-out), discord |
| `voting.closing_48h` | 2 days before month-end auto-close | `all_voters` | email (opt-out), in_app, discord(optional) |
| `voting.results_published` | after the close + allocation computed | `all_voters` (+ allocation breakdown) | email, in_app |
| `release.published` | new version in `version.py` `CHANGELOG` (deploy hook / mgmt command) | `all_active_members` ∪ `everyone_with_login` ∪ admins | email (opt-out), in_app, discord |
| `member.invited` | an invite is created | the invitee email | email forced |

Notes: `voting.closing_48h` supersedes today's `voting_closing` (currently month-end−3d, in-app only —
audit-C) and the dead `voting_cycle_open`. `release.published` mirrors the existing GitHub-Actions Discord
changelog post; trigger mechanism (deploy hook vs. cron detecting a version bump) is a small build detail,
default = a management command run post-deploy.

---

## 5. Teardown plan — strangler inside one PR, finishing clean

Each phase is independently green + locally testable (Docker + Postgres + BDD). The PR is not done until
Phase 8; **no half-migrated end state**. (This is a large PR by design — the user explicitly wants the whole
new system in one clean PR with zero obsolete code, not split shipments.)

| Phase | Work | Exit |
|---|---|---|
| **1 — spine** | `EventType` registry, `events.emit()`, resolver registry (incl. scoped resolvers §3), `EventDelivery`, generalized `NotificationPreference` + data migration, rebuilt unified prefs page. | Spine exists + tested; nothing migrated; green. |
| **2 — channels** | InApp/Email/Push/ScheduledEmail/Digest adapters + **DiscordChannel (built, webhook + event→channel routing)**. | All channels exist + tested; green. |
| **3 — copy catalogue** | `NotificationTemplate` + versioning + seed command + constrained merge-field rendering + **admin catalogue UI** (list every event, lock status, audience, copy, preview; copy-team editable). | Copy is DB-backed + editable + previewable; green. |
| **4 — migrate event emails** | Re-point the ~20 event senders onto `emit()`; **delete each `send_*`** as it lands; double-sends vanish; collapse 3 admin resolvers into role×scope; fix orientation fan-out + runner attribution. | Event emails through `emit()`; one-offs deleted; green. |
| **5 — choke-point + scheduling + billing** | Route the 4 bypasses (incl. **allauth**, Decision 8) through `EmailChannel`; generalize scheduler; fold dedupe; **wire `bill_tabs` into cron** (Decision 3). | Every email audited; one dedupe; billing automated; green. |
| **6 — new events** | Build `guild.announcement`, `site.announcement`, `voting.closing_48h`, `voting.results_published`, `release.published`, `member.invited` (Decisions 4–5). | New emails live + tested; green. |
| **7 — prune** | Delete `EmailPreferencesForm`/`/settings/emails/`; delete the 3 truly-dead triggers; unify the audit label on `event_key`. | Zero decorative/dead notification code; green. |
| **8 — verify clean** | Grep proves no `core.email.send`/`send_mail`/`EmailMessage` outside `EmailChannel`, no `dispatch()` outside `emit()`; coverage + mutation + full suite + lint + mypy green. | The finished system. Merge. |

`#8`/`#9` (§10) are follow-on PRs on top.

---

## 6. Stays as-is
`core.email.send` choke-point + `TransactionalEmailLog` (now the only email path); web push; the bell UI;
the `/settings/?tab=notifications` URL; the activity logs (now written by `emit()`); existing templates
(re-pointed as the seed for DB copy, not rewritten).

## 7. Testing
Resolver unit tests (esp. scoped guild_leadership/guild_lead/guild_orienters + orientation_runner
attribution); `emit()` (one event → one of each enabled channel; forced ignores prefs; idempotent);
per-retired-sender characterization tests (same recipients, same copy, now exactly once); copy
preview/merge-field tests; Discord webhook mocked via `respx`; 100% coverage + mutation on the spine
(Postgres = CI source of truth).

## 8. Risks
Live production app — strangler order keeps every phase shippable. Highest-risk spots: the
`NotificationPreference` data migration (P1), allauth (P5), and `bill_tabs` billing (P5) — all get reversible
migrations + extra tests. Large PR — accepted per the no-tech-debt requirement; mitigated by phase-by-phase
green checkpoints. DB copy editing — constrained merge fields (not raw templates) contain the injection/
breakage risk.

## 9. Out of scope
Async queue (stays sync-in-cron; interface leaves room). That's the only deferral — Discord, copy editing,
billing automation, and the new emails are all IN per the decisions.

## 10. #8 and #9 land on top (follow-on PRs)
**#8** = three `class.email.*` scheduled events (welcome/reminder_48h/follow_up_48h) on `ScheduledEmailChannel`
+ a `ClassEmailTemplate` for instructor-authored content. **#9** = members opt a category to `digest`; the
flush cron batches buffered `EventDelivery` rows into one email. Both are events/content, not new plumbing.

---

## Appendix — key file:line anchors
- Choke-point `core/email.py:52`; dispatch+routing `core/notifications.py:18-70`; triggers `core/triggers.py:16-129`.
- Prefs `NotificationPreference` `core/models.py:674`; settings UI `templates/hub/_notifications_settings.html`, `/settings/?tab=notifications`.
- Guild staff `GuildStaffMembership` `membership/models.py:848-893`; `leadership_members()` `:831-845`.
- Orientation orchestration + attribution bug `membership/orientations.py` (`request :151`, `confirm :217`, `cancel :256`, `complete :282`); `OrientationBooking.confirm` default `membership/models.py:2024-2029`.
- Activity logs `SiteActivity` `core/models.py:538`, `CmsActivity` `classes/models.py:1777`, `classes/activity.py:48`.
- Signals `core/signals.py` `_on_login :14`, `_on_signup :47`.
- Scheduled dispatcher `core/management/commands/run_scheduled_tasks.py:23-43`; dead `bill_tabs` `billing/management/commands/bill_tabs.py`; voting reminder `core/management/commands/send_voting_reminders.py`.
- 3 "admin" resolutions `classes/emails.py:19-39`; `classes/models.py:684-688` / `core/signals.py:62`; `billing/notifications.py:79`.
- Release source `plfog/version.py` `CHANGELOG`; existing Discord changelog post `.github/workflows/discord-notify.yml`.
