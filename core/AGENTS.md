# core app

Auth infrastructure, site configuration, and Web Push.

## Models

| Model | Key fields | Notes |
|-------|-----------|-------|
| `SiteConfiguration` | registration_mode, spotlight_meeting_event, spotlight_first_line, spotlight_second_line, spotlight_text_changed_at, door_code, door_code_changed_at, door_code_changed_by | Singleton (pk=1); load via `SiteConfiguration.load()`. The `spotlight_*` fields (#708) are edited at Admin Tools > Spotlight; `hub.spotlight.Spotlight.load(member, now)` reads them, the meeting and the open poll in one query (#709). The `door_code*` fields (#813) are set from the Door Code card on Home by an admin through `set_door_code`; the repo is public, so no real code appears in code, migrations or tests. |
| `AutomationAction` | task_key, run FK (nullable), kind, result, subject, member FK, actor FK, detail, created_at | What an automation did to one person (#898), next to `ScheduledTaskRun`, which only says a run happened. Generic: any job writes through `AutomationAction.record(key, ...)`, which attaches the run `record_run` has open for that key (`core.scheduled_jobs.current_run`, a context variable, because `call_command` hands the command no run). An admin's click has `actor` and no run. Rows outlive their pruned run. Shown on each job's history page, `/manage/site-settings/automations/<key>/` (`hub.views.admin_automation_history`), linked from every Automations card. |
| `Invite` | email, invited_by FK, member 1:1, accepted_at | Email invite flow for invite-only registration |
| `PushSubscription` | user FK, endpoint, p256dh, auth | Web Push subscription per user |
| `FeedbackRequest` | user FK, category, subject, message, status, staff_note, github_issue_url, status_changed_at, live_notified_at | A bug report, feature request or feedback sent from the Feedback page (#693). The sender is a `User`, not a `Member`. `apply_admin_update` / `mark_live` move it and emit `feedback.request_updated` to the sender, once per real change (Received never notifies; Not planned needs a note). `live_notified_at` keeps the automatic live notice from repeating one: `announce_live` (#693 part 2), run by the `announce_live_requests` job for each request a shipped changelog fragment lists in `requests`, once `/health/` on the web service reports a version at least the job's own. Photos are `FeedbackRequestPhoto` rows on the default storage. |

## Registration Modes

`SiteConfiguration.RegistrationMode.OPEN` — anyone can sign up.
`SiteConfiguration.RegistrationMode.INVITE_ONLY` — only invited emails can register.

The allauth adapter (`plfog/adapters.py`) checks this before allowing new signups.

## Invite Flow

1. Admin calls `Invite.create_and_send(email, invited_by)` — creates `Member` placeholder with status=INVITED and sends email
2. Invitee clicks link → `allauth` signup pre-fills email
3. Adapter calls `invite.mark_accepted()` on successful signup

## Admin Actions

`plfog/admin_views.py`:
- `invite_member` — POST view at `/admin/membership/member/invite/`; calls `Invite.create_and_send()`
- `take_snapshot` — POST view at `/admin/take-snapshot/`; calls `FundingSnapshot.take()`

## URLs (core.urls)

- `/` — home / dashboard
- `/health/` — health check endpoint
- `/push/subscribe/` — register Web Push subscription
- `/push/test/` — send test push notification
- `/manifest.json` — PWA manifest
- `/restart-login/` — force re-login (clear session)
- `/site-migration/` — migration landing page
- `/find-account/` — find account by email (admin tool)

## Member Lockout (#409)

`core/member_lockout.py` holds the one rule: `lockout_reason(user)` returns `former` or `guest` (always, staff included; a guest is an account made from a class booking, #654) or `suspended` (while `SiteConfiguration.suspended_members_locked_out` is on), else None. It reads `Member.status`, never `User.is_active`, and knows nothing about surfaces; the three gates decide that:

- `AdminRedirectAccountAdapter.pre_login` refuses sign-in on the members surface only, so a former member can still sign in on book for their class receipts. A guest is the exception (#691): while `kiln_tickets_open` is on, it signs in on members to file kiln tickets.
- `biometric_unlock` always refuses (the app is the members site) and revokes the credentials.
- `MemberLockoutMiddleware` never logs out, because the session cookie is shared with book. On the members surface it redirects everything to the lockout page except that page, logout, `/static/` and `/health/`. On book it serves only `settings.LOCKED_OUT_BOOK_PATH_PREFIXES` (`/classes/`, `/account/`, `/accounts/`, `/static/`, `/media/`, `/health/`), because `MEMBER_ONLY_PATH_PREFIXES` is a blocklist that would otherwise open hub pages, `/api/` and the KB sign-in (`/o/`). Guilds and signage are untouched. The one exception (#691): while `kiln_tickets_open` is on, a guest on the members surface is served `/kiln/`, and every other members page sends it to `/kiln/` instead of the lockout page. A guest is also served `/hq/` (the Holiday Bizarre HQ) and, by resolved URL name (`GUEST_EVENT_URL_NAMES`), one event's public page, its flyer, its `.ics` and the guest RSVP post (#887), which the HQ links to; the page renders for a guest as for a signed-out visitor.

All send the member to `/accounts/locked/?reason=`, which renders on either host and shows the admin-editable message (`former_member_signin_message` / `guest_member_signin_message` / `suspended_member_signin_message`, General tab of Site Settings) and the support email. A signed-in viewer also gets a link to their bookings on book (`BOOK_BASE_URL`) and a log out link; an anonymous one gets "Back to login".

## Deferred work (`core/deferred.py`, #845)

`defer(task, name=...)` runs slow work after the current transaction commits, on a non-daemon background thread named `deferred:<name>`, so a request never holds its response for it. It is a thread, not a response close hook, because in a gunicorn sync worker the close hook still runs on the worker's main loop and WORKER TIMEOUT kills it at 30 seconds as before. The thread closes its own database connections when it ends and logs a failure instead of raising. A thread does not survive a redeploy or a kill, so every caller needs a database stamp and a cron backstop that finishes the work, and the work must be safe to run twice (the event announcement: `CommunityEvent.announcement_owed_since` and `finish_event_announcements`). Under test the root `conftest.py` swaps `deferred._schedule` for `run_inline`; a spec that needs the real thread sets it back to `after_commit_thread` and joins the `deferred:` threads, and `tests/core/events/conftest.py`'s `held_deferred` holds the work so a spec can check what the request left behind first.
