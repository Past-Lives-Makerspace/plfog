# core app

Auth infrastructure, site configuration, and Web Push.

## Models

| Model | Key fields | Notes |
|-------|-----------|-------|
| `SiteConfiguration` | registration_mode | Singleton (pk=1); load via `SiteConfiguration.load()` |
| `Invite` | email, invited_by FK, member 1:1, accepted_at | Email invite flow for invite-only registration |
| `PushSubscription` | user FK, endpoint, p256dh, auth | Web Push subscription per user |
| `FeedbackRequest` | user FK, category, subject, message, status, staff_note, github_issue_url, status_changed_at, live_notified_at | A bug report, feature request or feedback sent from the Feedback page (#693). The sender is a `User`, not a `Member`. `apply_admin_update` / `mark_live` move it and emit `feedback.request_updated` to the sender, once per real change (Received never notifies; Not planned needs a note). `live_notified_at` keeps the automatic live notice from repeating one. Photos are `FeedbackRequestPhoto` rows on the default storage. |

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

- `AdminRedirectAccountAdapter.pre_login` refuses sign-in on the members surface only, so a former member can still sign in on book for their class receipts.
- `biometric_unlock` always refuses (the app is the members site) and revokes the credentials.
- `MemberLockoutMiddleware` never logs out, because the session cookie is shared with book. On the members surface it redirects everything to the lockout page except that page, logout, `/static/` and `/health/`. On book it serves only `settings.LOCKED_OUT_BOOK_PATH_PREFIXES` (`/classes/`, `/account/`, `/accounts/`, `/static/`, `/media/`, `/health/`), because `MEMBER_ONLY_PATH_PREFIXES` is a blocklist that would otherwise open hub pages, `/api/` and the KB sign-in (`/o/`). Guilds and signage are untouched.

All send the member to `/accounts/locked/?reason=`, which renders on either host and shows the admin-editable message (`former_member_signin_message` / `guest_member_signin_message` / `suspended_member_signin_message`, General tab of Site Settings) and the support email. A signed-in viewer also gets a link to their bookings on book (`BOOK_BASE_URL`) and a log out link; an anonymous one gets "Back to login".
