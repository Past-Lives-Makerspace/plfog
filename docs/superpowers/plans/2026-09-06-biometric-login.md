# Biometric login for the mobile app

**Status:** approved 2026-09-06
**Ships as:** one PR off `main`, after the iOS push PR

## Why

Auth is allauth passwordless login codes: type your email, wait for the code,
type the code. On a phone that is the whole friction of the app. Once the Django
session cookie lapses, a member does it again.

After a member has proved who they are once with a code, the phone should be
able to say "it is still them" with Face ID or a fingerprint.

## Decisions (locked)

1. **A rotating device credential, 90 days.** The server mints a random secret
   and stores only its hash. The app keeps the secret in the Keychain/Keystore
   behind the biometric prompt. A biometric verify exchanges the secret for a
   fresh session and rotates it.
2. **`@capgo/capacitor-native-biometric` (8.6.7, peer `@capacitor/core >=8.0.0`).**
   Chosen over `@aparajita/capacitor-biometric-auth` because it stores
   credentials in the Keychain/Keystore itself. The alternative only verifies,
   which would leave the secret in Capacitor Preferences — `UserDefaults` on iOS,
   which is not secure storage.
3. **The biometric never authenticates to the server.** It gates local access to
   the secret. The server trusts the secret, nothing else. This is the honest
   framing and it decides everything below.

## Threat model, stated plainly

The secret is a bearer token. Anything holding it can become the member. So:

- It is only ever handed to an **already authenticated** session (enrollment).
- It is stored **hashed** (SHA-256). A database leak yields no usable secrets.
  A slow hash is pointless here: the input is 48 bytes of `secrets.token_urlsafe`
  entropy, not a guessable password.
- It **rotates on every use**, so a copy stolen from a backup is dead as soon as
  the real device uses its own.
- A **spent secret coming back** is caught **only while it is one rotation stale**.
  `previous_secret_hash` is a single slot, so a thief who redeems a stolen copy twice
  pushes the original out of it and the real device's next attempt reads as merely
  unknown rather than replayed: nothing is revoked and the stolen credential stays
  live. What is lost is detection, not containment. Exact detection needs a stable
  unguessable selector beside the rotating verifier, so a credential is identified
  independently of which generation is presented. Tracked as follow up work.
- Unlock attempts are **rate limited** per IP.
- It **expires** at 90 days, refreshed on each use, so an abandoned phone stops
  working rather than staying valid forever.

## Phase 1 — model

`core/models.py`, next to `FcmDevice`.

```python
class BiometricCredential(models.Model):
    """A device-bound bearer secret that exchanges a biometric verify for a session.

    The phone holds the secret in the Keychain/Keystore behind Face ID or a
    fingerprint; the server holds only its SHA-256. Redeeming it logs the member
    in and rotates the secret, so a copied secret dies the moment the real device
    uses its own.
    """
```

Fields, every one with `help_text`:

| field | notes |
|---|---|
| `user` | FK, `on_delete=CASCADE`, `related_name="biometric_credentials"` |
| `secret_hash` | `CharField(max_length=64, unique=True)` — SHA-256 hex of the live secret |
| `previous_secret_hash` | `CharField(max_length=64, blank=True, default="", db_index=True)` — the secret this one replaced; see reuse detection |
| `rotated_at` | `DateTimeField(null=True, blank=True)` — when `previous_secret_hash` was superseded |
| `device_label` | `CharField(max_length=120)` — member-visible, e.g. "iPhone 15". Client supplied, so treat as untrusted text |
| `platform` | reuse the same `ios`/`android` choices shape as `FcmDevice.Platform` |
| `created_at` | `auto_now_add` |
| `last_used_at` | `DateTimeField(null=True, blank=True)` |
| `expires_at` | `DateTimeField` — issued at +90 days, pushed forward on each redeem |
| `revoked_at` | `DateTimeField(null=True, blank=True)` |

`__str__`: label, platform, and whether it is active.

Add a `Meta.indexes` entry for `previous_secret_hash` only if you do not set
`db_index` on the field; do not do both. Watch the 30-character index-name cap
(`manage.py check` E034 has bitten this repo before).

### Manager — all the logic lives here

`BiometricCredentialManager`:

- `issue(user, *, device_label, platform) -> tuple[BiometricCredential, str]`
  Generates `secrets.token_urlsafe(48)`, stores its hash, returns the row and the
  **raw secret**. The raw secret is never stored and never logged.
- `redeem(raw_secret) -> tuple[User, str]`
  The whole state machine, below. Returns the user and the **new** raw secret.
  Raises `InvalidBiometricCredential` (a domain exception in the same module) on
  every failure — never returns `None`, per the fail-loudly standard.
- `active_for(user) -> QuerySet` — not revoked, not expired, newest first.
- `revoke(credential)` / `revoke_all(user)`.

### `redeem` state machine

Look up by `secret_hash` first, then by `previous_secret_hash`:

1. **Hit on `secret_hash`** — the normal path. Reject if revoked or past
   `expires_at`. Otherwise move the current hash into `previous_secret_hash`, set
   `rotated_at=now`, store the new secret's hash, set `last_used_at=now` and
   `expires_at=now + 90 days`. Return the new secret.

2. **Hit on `previous_secret_hash`, within 60 seconds of `rotated_at`** — a lost
   response, not an attack. The app redeemed, the server rotated, the reply never
   arrived, so the app retried with the only secret it has. Rotate again and
   return the new secret. Keep the credential alive.

   Without this branch, every dropped response on a phone network permanently
   breaks that member's biometric login and sends them back to email codes. It is
   the difference between a feature that works on a train and one that does not.

3. **Hit on `previous_secret_hash`, later than that** — a spent secret is being
   replayed. **Revoke the credential**, log a warning with the user and the
   device label (never the secret), and raise. The member re-enrols with a login
   code; whoever held the stolen copy gets nothing.

4. **No hit** — raise.

## Phase 2 — endpoints

Views in `core/views.py` beside `fcm_register`, routes in `core/urls.py`. Thin:
parse, call the manager, return JSON.

**`POST /accounts/biometric/enroll/`** — `@login_required @require_POST`.
Body `{device_label, platform}`. Validates `platform` against the choices exactly
as `fcm_register` does. Returns `{"secret": ...}`. Being logged in **is** the
security boundary here.

**`POST /accounts/biometric/unlock/`** — `@require_POST @csrf_exempt`, no login
required. Body `{secret}`. On success calls
`django.contrib.auth.login(request, user, backend="django.contrib.auth.backends.ModelBackend")`
and returns `{"ok": true, "secret": <new secret>}`.

`csrf_exempt` is deliberate, and the reason belongs in a comment on the view: the
caller has no session yet, so it may have no CSRF cookie, and the secret in the
body is itself unguessable by a cross-site attacker — it is doing the job CSRF
protection does. Every other endpoint here keeps CSRF.

Rate limit before touching the database, via
`core.abuse_limits.record_keyed_attempt(scope="biometric_unlock", key=<client ip>, ...)`.
Match the shape the Discord `/create` limiter already uses. Return 429 with a
plain message when the cap is hit. Pick limits that a real member cannot reach:
an unlock happens on app open, so roughly 20/hour and 100/day per IP.

**`POST /accounts/biometric/disable/`** — `@login_required @require_POST`.
Body `{secret}` revokes that one credential; empty body revokes all of the
caller's. Silent success when the secret is unknown, matching `fcm_unregister`.

## Phase 3 — revocation paths (do not skip)

- **`membership/services/account_deletion.py`** already deletes `FcmDevice` at
  line 77. Add `BiometricCredential` in the same place, and extend
  `tests/membership/services/account_deletion_spec.py` to assert it. A credential
  outliving its account is a live key to a deleted member.
- **App logout** clears the Keychain entry and POSTs `disable`. Do this in the JS,
  not with a `user_logged_out` signal: a member logging out on the web must not
  silently kill biometric login on their phone.
- **Settings** — the Revoke button below.

## Phase 4 — the app side

New `static/js/biometric-auth.js`, loaded from `templates/base.html` next to
`native-push.js`. No-op outside the native app, and no-op when the plugin is
absent, exactly as `native-push.js` guards itself — this file reaches installed
apps before the build that carries the plugin does.

Three jobs:

1. **Offer** — running in the app, authenticated, no stored secret, not
   previously declined: show an in-app prompt, "Use Face ID to sign in next
   time?" with Enable and Not now. Check the plugin reports an enrolled biometric
   first; never offer on a phone that has none. Remember a decline in
   `localStorage` so it asks once, and say in the copy that Settings can turn it
   on later. Enable calls `enroll`, then stores the secret with the plugin's
   credential API.
2. **Unlock** — running in the app, on the login page, with a stored secret: show
   a "Sign in with Face ID" button and trigger the flow automatically on arrival.
   Biometric verify, read the secret, POST `unlock`, store the returned secret,
   navigate to the hub. On a `401`, clear the stored secret and fall through to
   the normal login-code form rather than trapping the member on a dead button.
   **Store the new secret before navigating** — a rotation whose result is dropped
   costs the member their enrollment (branch 2 above softens this, it does not
   remove it).
3. **Logout** — clear the stored secret and POST `disable`.

Use the actual API in
`mobile/node_modules/@capgo/capacitor-native-biometric/dist/esm/definitions.d.ts`
after installing. Verify the method names, the availability check, and the
credential storage calls against what is declared there rather than a remembered
API.

`mobile/`: `npm install @capgo/capacitor-native-biometric@^8.6.7`, commit
`package.json` and `package-lock.json`. iOS needs an `NSFaceIDUsageDescription`
string in `mobile/ios/App/App/Info.plist` or the app is rejected; Android needs
`USE_BIOMETRIC` in the manifest. Bump both build numbers: unlike the push JS,
this feature genuinely needs a new release on **both** stores.

## Phase 5 — settings UI

A "Signed-in devices" card in `templates/hub/user_settings.html`, following
`FRONTEND.md`. One row per `active_for(request.user)`: device label, platform,
last used in plain words. A Revoke button per row, and a note that revoking means
that phone asks for an emailed code next time.

Escape `device_label` on render. It is client-supplied text.

If the member is in the app and has no credential, the card also carries the
"Turn on Face ID sign-in" entry point the offer prompt's Not now sends them to.

## Phase 6 — tests

Model and manager, in a spec under `tests/core/`:

- `issue` returns a secret that is not stored anywhere in the row.
- `redeem` on a good secret logs the right user in, returns a **different**
  secret, pushes `expires_at` out, and sets `last_used_at`.
- The old secret stops working after a rotation, outside the grace window.
- The grace window: a redeem of the previous secret within 60 seconds succeeds
  and keeps the credential alive; the same redeem after 61 seconds **revokes** it
  and raises. Freeze time; do not sleep.
- Expired, revoked, and unknown secrets each raise.
- `revoke_all` leaves other users untouched.

Views:

- `enroll` requires a session; rejects a bad platform.
- `unlock` with a valid secret creates a session (assert on the response's
  authenticated user, not just the status code) and returns a new secret.
- `unlock` is rate limited: past the cap it returns 429 without a session.
- `disable` with a secret revokes exactly one; with no body revokes all.
- A crafted POST with **another member's** secret must log in as that other
  member and nothing else — assert it cannot be aimed at an arbitrary user id,
  because there is no user id in the request at all. Say so in the test name.

Account deletion: extend the existing spec.

## What cannot be verified here

The Keychain/Keystore path and the biometric prompt do not exist in pytest or in
Playwright. CI proves the server: the state machine, rotation, the grace window,
revocation, rate limiting. It proves nothing about the JS.

On hardware, Jo checks: the offer appears once and not on a phone without
biometrics; Enable stores the secret; killing and reopening the app signs in with
a face or a finger; airplane-moding mid-unlock and retrying still works (the
grace window); Revoke in settings sends that phone back to a login code; logging
out clears it.

Say this in the PR body. Do not describe the feature as verified.
