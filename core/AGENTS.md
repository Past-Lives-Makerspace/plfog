# core app

Auth infrastructure, site configuration, and Web Push.

## Models

| Model | Key fields | Notes |
|-------|-----------|-------|
| `SiteConfiguration` | registration_mode | Singleton (pk=1); load via `SiteConfiguration.load()` |
| `Invite` | email, invited_by FK, member 1:1, accepted_at | Email invite flow for invite-only registration |
| `PushSubscription` | user FK, endpoint, p256dh, auth | Web Push subscription per user |

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
