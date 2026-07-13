# `/link` Slash Command — Connect Discord to a FOG account — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-13
**Surface:** Discord (slash command, ephemeral replies) + the existing FOG hub web OAuth landing (`pastlives.test` → `hub/discord_link_landing.html`).
**Related:**
- **Depends on** `docs/superpowers/plans/2026-07-13-discord-interactions-foundation.md` — the interactions endpoint, request-signature verification, command routing, caller→member resolution, the ephemeral-reply helper, and the deferred-response pattern all live there. This spec does **not** re-spec any of them; it assumes them and only describes what `/link` does.
- Reuses the account-linking flow shipped in `2026-07-12-discord-guild-linking-sync.md` (`core/events/discord_oauth.py`, `hub/discord_views.py`, `hub/discord_link_landing.html`).

---

## 1. Summary

A member types **`/link`** in the Past Lives Discord and, in a few seconds, connects their Discord account to their FOG member account — the thing that gates *every* other part of the Discord integration (guild sync, DMs, and every other slash command). Today only ~1 of 610 members are linked, so this is the highest-leverage command: it is the on-ramp. The command itself never links anyone; it hands the member a one-tap connect link that runs the **already-built, security-vetted** web OAuth flow, which links them only on a verified-email match. `/link` replies **ephemerally** (only the caller sees it) in four cases: already-linked, not-yet-linked (the connect link), integration-not-configured, and error.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| How the command links the account | **Path (B): reply with the one-tap OAuth connect URL.** The slash command cannot complete OAuth itself; it hands off to the existing web flow, which links on a verified-email match and imports guilds. (Path (A), linking straight from the payload, is not buildable — see §10.) |
| Which URL to hand back | The absolute URL of **`hub_discord_link_start`** (`/discord/link/`) — the anon-allowed, no-FOG-login one-tap flow that already auto-matches by verified email and renders every outcome. Not a hand-rolled `authorize_url` + new callback. |
| Does `/link` need a deferred response? | **No.** It does a single indexed DB lookup and builds a URL — no outbound HTTP — so it replies immediately, well inside the 3s ACK budget. (Contrast with commands that call Discord's API.) |
| Where "you're linked!" is confirmed | On the **existing web landing** after OAuth completes, not from the command (a stateless interaction can't observe the later OAuth callback). Re-running `/link` after linking shows the already-linked nudge. |
| Availability check | `discord_oauth.is_configured()` — the real function name (**not** `is_available()`, see §10). |

## 2. What already exists (reuse, don't reinvent)

This feature is almost entirely assembly. Every linking primitive is built and tested.

| Need | Existing thing | Location |
|---|---|---|
| Interactions endpoint, signature verify, command routing, caller→member resolution, ephemeral-reply helper, deferred pattern | The **foundation** (dependency spec) | `docs/superpowers/plans/2026-07-13-discord-interactions-foundation.md` |
| "Is Discord linking turned on?" | `discord_oauth.is_configured()` | `core/events/discord_oauth.py:79` |
| The one-tap, no-login connect flow to hand the member | `discord_link_start` view → URL name `hub_discord_link_start` (`/discord/link/`) | `hub/discord_views.py:113`, `hub/urls.py:214` |
| The OAuth flow the link runs (verified-email auto-match → link + guild import → outcome landing) | `link_and_import()` + `resolve_member_from_code()` / `link_member_from_code()` | `membership/discord_sync.py`, `core/events/discord_oauth.py:176,206` |
| The verified-email → Member match the flow enforces | `member_for_verified_email()` | `membership/selectors.py:15` |
| Record the link (called *inside* the OAuth flow, never by this command) | `member.link_discord(discord_user_id, handle="")` | `membership/models.py:645` |
| "Is this member linked?" | `member.discord_is_linked` (`bool(discord_user_id)`) | `membership/models.py:494` |
| Reverse lookup: caller's Discord id → Member (the already-linked check) | `Member.objects.filter(discord_user_id=<id>).first()` — **use the foundation's resolver**, don't reimplement | mirrors `core/events/discord_dm.py:57` |
| Success/needs-login/error web pages after OAuth | `templates/hub/discord_link_landing.html` (every state already has a button) | `hub/discord_views.py:156` |
| Absolute-URL builder for the reply link | The foundation's absolute-URL helper (mirror of `_absolute_url()`) | `classes/emails.py:59` (pattern) |

**Gaps to close (small):**
1. A `/link` command handler in the foundation's command module (the four ephemeral replies + the DB lookup).
2. Register `/link` as a Discord application command (ops / owned by the foundation's registration step — see §8 go-live).
3. *(Optional, tiny)* a one-line "back in Discord, try `/whats-on`" nudge on the landing's `linked` state.

No new models, fields, migrations, forms, or CSS.

## 3. Where the code lives

Everything rides in the foundation's app (assume `core/events/discord_interactions/` per the dependency spec; exact path is the foundation's call). `/link` adds one command handler and its spec — it does **not** create a new app or new web views.

```
core/events/discord_interactions/       # created by the foundation spec
    commands/
        link.py                         # NEW — the /link handler (this spec)
    ...                                 # (endpoint, router, reply helpers — foundation)
core/events/spec/
    discord_interactions/
        commands/
            link_spec.py                # NEW — BDD tests for /link
templates/hub/
    discord_link_landing.html           # touched only for the optional §6 nudge
plfog/version.py                        # VERSION bump + CHANGELOG (final phase)
```

## 4. Data model

None. `/link` reads `Member.discord_user_id` (via the foundation resolver) and reads settings via `discord_oauth.is_configured()`. No new fields, no migration.

## 5. Business logic

The command is thin orchestration — it makes no state change itself (the OAuth flow does). Pseudocode for the handler (`commands/link.py`), using the foundation's reply helpers:

```
def handle_link(interaction) -> EphemeralReply:
    # 1. Integration off?
    if not discord_oauth.is_configured():
        return ephemeral(NOT_CONFIGURED_TEXT)

    # 2. Already linked? (caller's Discord id already resolves to a member)
    member = foundation.member_for_discord_user_id(interaction.caller_discord_id)
    if member is not None:                       # == member.discord_is_linked by construction
        return ephemeral(ALREADY_LINKED_TEXT.format(name=member.display_name))

    # 3. Not linked → hand back the one-tap connect link (absolute URL).
    connect_url = absolute_url(reverse("hub_discord_link_start"))
    return ephemeral(CONNECT_TEXT, link_button=("Connect Discord", connect_url))
```

- The whole body is wrapped by the foundation's per-command error guard so any unexpected exception becomes the friendly **error** ephemeral (§6), **never a 500** and never a bare Discord failure — the interactions endpoint must always return a valid 200 interaction response.
- The command **never** calls `member.link_discord(...)` — a test asserts this (§9). Linking happens only inside `link_and_import()` behind the verified-email gate.

### Security reasoning (the load-bearing part)

- We link an account **only** on a verified-email match, and only through the existing OAuth flow. That flow obtains a **user access token** with the `email` scope, and links only when Discord reports the email as `verified` **and** it matches exactly one verified Past Lives `EmailAddress` (`resolve_member_from_code` → `member_for_verified_email`; `link_and_import` applies the already-linked-elsewhere / account-has-other-Discord guards). `/link` does not and cannot bypass this — it only hands the member a link to that vetted flow.
- A slash-command interaction payload is signed by Discord, so it **proves the caller controls that Discord account**. It does **not** prove they own any particular Past Lives account — there is no verified Past Lives email in it to match against. The OAuth `email`-scope step is exactly what closes that gap. So the safe design is: the command proves nothing about FOG identity and asserts nothing; it defers the actual identity match to the flow that verifies it.
- This is why Path (A) is a non-starter (§10): its blocker is **feasibility** (the payload carries no email), not a weaker security posture — if a *verified* email were present it would run the identical `member_for_verified_email` match. Since it is absent, (B) is the only buildable path, and it is fully safe.

## 6. UI / UX

There is **no new web form, list, or CSS.** The command's "screens" are its ephemeral Discord replies; the success confirmation reuses the existing web landing. The famous list-editor failure (Add/Delete/Save) does not apply here — there is no list and no form to build. Below, each reply is named with exact copy and its state.

### Screen A — Discord ephemeral replies (the command output)

- **Layout & container:** Discord ephemeral message (foundation sets the ephemeral flag; only the caller sees it). Discord themes the message natively (light/dark handled by Discord — no tokens for us). Where the foundation's reply helper supports message components, the connect reply uses a **Link-style button** (`style: LINK`, `url=<connect_url>`) for a real one-tap target; otherwise it falls back to the bare `https` URL inline in the content (Discord auto-links it). See §10.
- **Markdown:** Discord markdown; command names shown as inline code (`` `/whats-on` ``).

**State 1 — Discord integration not configured** (`is_configured()` is False):
> Connecting Discord isn't set up right now. Please try again later, or reach out to a Past Lives organizer.

**State 2 — Already linked** (caller's Discord id resolves to a member):
> You're already connected as **{display_name}** — you're all set. Try `/whats-on` to see what's coming up.

**State 3 — Not yet linked (the primary path; the connect link):**
> Let's connect your Discord to your Past Lives account. **[Connect Discord]({connect_url})**
>
> After you approve, we'll match you by your verified email, link you automatically, and set you up in your guilds. It only takes a few seconds.

(`{connect_url}` = absolute `hub_discord_link_start`. Rendered as a Link button when components are available, else the bare URL.)

**State 4 — Error** (any unexpected exception, caught by the foundation guard):
> Something went wrong starting the link. Please try again in a moment.

### Screen B — Web OAuth landing (reused as-is)

`templates/hub/discord_link_landing.html` already renders every outcome the tapped link produces and every state has a button (no dead ends):
- **Success** → `state="linked"` ("your Discord is connected", guild links, settings/notifications buttons).
- **No verified-email match** → `state="needs_login"` (offers log in / sign up, then connect) — this is how a member whose Discord email doesn't match a FOG account is handled *without the command needing to know*.
- **Already linked elsewhere / account has other Discord / OAuth failed** → their existing friendly states.

**Optional tiny addition (flag in §10):** on `state="linked"`, add one guarded line nudging the member back to Discord — e.g. "Back in Discord, try `/whats-on` or `/my-guilds`." This is copy only (no new component/CSS) and satisfies the "success → nudge to the other commands" intent; the command can't emit a live "you just linked" reply because it can't observe the OAuth callback.

### States coverage (checklist)

- **Empty:** n/a (no list).
- **Loading:** n/a — `/link` replies immediately (single DB lookup, no HTTP); note the foundation's 3s ACK budget is comfortably met, so no deferred/"thinking…" state is used.
- **Error:** friendly ephemeral (State 4) + the landing's `oauth_failed`/`cancelled` states — never a 500.
- **Success:** the landing's `linked` state (+ optional nudge); re-running `/link` after linking → State 2.
- **Dark/light:** ephemeral messages are Discord-native; the landing page already passes both themes. **No new CSS — verify the optional nudge line introduces no regression in both themes.**
- **Mobile:** ephemeral replies and the Link button are Discord-native and tap-friendly; the landing page already reflows. The connect flow is designed for exactly the phone-in-Discord case (Discord's in-app browser) — the anon flow already tolerates a dropped session cookie there.

## 7. Notifications / emails / activity

None new. When the member completes the tapped flow, the **existing** `link_and_import()` does the guild import and whatever it already emits; `/link` adds no email, no `emit()`, no `SiteActivity`. (Reason to keep it: YAGNI — the linking event is already instrumented by the flow this command reuses.)

## 8. Build order (each phase ships green)

1. **`/link` handler + tests.** Add `commands/link.py` (the four ephemeral replies, the `is_configured()` gate, the foundation member-resolver lookup, the absolute `hub_discord_link_start` URL) and register it in the foundation's command router. Full `link_spec.py`. Ships green (suite + `ruff` + `mypy`). No web/CSS changes.
2. *(Optional)* **Landing nudge.** One guarded copy line on `discord_link_landing.html` `state="linked"`. Verify both themes; no new class.
3. **Version + changelog.** Bump `plfog/version.py` `VERSION`; add/curate the member-facing CHANGELOG entry for the Discord commands (fold under the interactions-foundation feature entry if it is in the same unreleased `MAJOR.MINOR` line — do not add a second entry for the same feature). Plain language, e.g. *"Type `/link` in Discord to connect your account — no digging through settings."*

**Go-live / ops (not code — owned by the foundation's registration step):** `/link` must be **registered** as a Discord application command (guild or global command payload) before it appears in the client, and the interactions endpoint URL + public key must be configured on the Discord application. `is_configured()` must be true in the target env (client id + secret present). None of this is new to this spec — it's the foundation's registration checklist; `/link` is one entry in it.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py` in the foundation app's `spec/` dir (`describe_*` / `it_*`, factory-boy, run in the `plfog-web` Docker image, ≥98% coverage). `/link` makes **no outbound HTTP**, so its own specs need no `respx` (the foundation tests the endpoint/signature/parse; don't re-test them here). Feed the handler a parsed interaction fixture (the foundation exposes a factory/builder for this) carrying the caller's Discord id.

- `describe_handle_link`
  - `describe_when_discord_not_configured` → `it_replies_with_the_friendly_not_configured_message` (monkeypatch `is_configured()` False); `it_never_hands_out_a_connect_link`.
  - `describe_when_caller_already_linked` → seed a `Member` with `discord_user_id=<caller id>`; `it_greets_them_by_display_name`; `it_nudges_whats_on`; `it_does_not_hand_out_a_connect_link`.
  - `describe_when_caller_not_linked` → `it_replies_with_the_connect_link`; `it_uses_the_absolute_hub_discord_link_start_url` (assert the reply URL == `absolute_url(reverse("hub_discord_link_start"))`, not a raw `authorize_url`); `it_reply_is_ephemeral`.
  - `describe_security` → `it_never_calls_link_discord` (a fresh unlinked member is still unlinked after `/link` — the command performs no link); resolution is by exact `discord_user_id`, not username.
  - `describe_when_the_handler_raises` → force the resolver/URL build to raise; `it_returns_the_friendly_error_reply_not_a_500` and `it_still_returns_a_valid_200_interaction_response`.
- No tz/date-window gotchas (no time math).

## 10. Open / deferred

**The A-vs-B feasibility finding (resolve the key design decision):**

- **Verified against Discord's interaction docs:** a slash-command interaction payload includes the invoking user object (`member.user` / top-level `user`) with `id`, `username`, `global_name`, `avatar`, `discriminator`, `public_flags` — **but no `email`.** A user's email is exposed **only** through the OAuth2 `email` scope on a *user* access token (exactly what the web flow at `core/events/discord_oauth.py` already requests: `_SCOPE = "identify email"`). A bot token cannot read a user's email from any API endpoint. **Therefore Path (A) — an OAuth-less email match straight from the slash command — is NOT achievable.** State this plainly: there is no verified email in the payload to match against, so the "magic" no-redirect link can't be built from a raw slash command.
- **Recommendation: Path (B)** — reply with the one-tap connect link, which runs the fully-built web OAuth flow (verified-email auto-match → `link_and_import`). It reuses the entire linking pipeline end-to-end and preserves the verified-email security gate.
- **Path (A) as a noted future option only:** if a future Discord capability surfaced a *verified* email in the interaction context (not currently possible via slash commands — only via an OAuth'd `email` scope), (A) would become buildable and would simply call the same `member_for_verified_email()` match — identical security. It is **not** worth building speculatively (YAGNI); (B) already delivers the outcome.

**Other open items:**

- **`is_configured()` vs `is_available()`:** the feature brief referenced `discord_oauth.is_available()`, which **does not exist** — the real availability helper is `is_configured()` (`core/events/discord_oauth.py:79`). This spec uses `is_configured()`. Flagged so the build doesn't chase a phantom function.
- **Link button vs bare URL:** the connect reply should use a Discord **Link-style message component** ("Connect Discord" button) for a true one-tap target. This depends on the foundation's reply helper supporting components; if it doesn't yet, fall back to the bare `https` URL inline (Discord auto-links it) and treat "add component support" as a small foundation enhancement. Confirm which the foundation ships.
- **Landing `linked`-state nudge** (§6 optional): confirm we want the "back in Discord, try `/whats-on`" line. Copy-only; skip if it clutters.
- **Command registration** (application command payload + endpoint/public-key config) is **owned by the foundation**, not this spec — listed here only as the go-live gate.
- **Out of scope:** any change to the web OAuth flow, guild import, DMs, or the already-linked-elsewhere / account-has-other-Discord handling — all already built and reused unchanged.
