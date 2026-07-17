# Discord Interactions Foundation — Slash-Command Platform — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-13
**Surface:** Backend HTTP endpoint on the member-hub domain (`pastlives.app` / dev `pastlives.test:8000`) + the Discord client (where members see replies) + admin (Guild edit, for channel→guild mapping). No FOG hub page.
**Related:** `2026-07-12-discord-guild-linking-sync.md` (the two-way link this reuses), the per-member Discord DM channel (`core/events/discord_dm.py`), and `2026-07-03-google-calendar-event-sync.md` (the §10 go-live-checklist format this mirrors).

---

## 1. Summary

Today a member can *receive* things from Past Lives inside Discord (broadcast embeds, a per-member DM), but they can't *do* anything from Discord — every action means leaving for the hub. This spec builds the **foundation** that lets a member run a slash command like `/schedule-orientation` right inside Discord and get an immediate, actionable reply. It is the shared platform every future command rides on; the commands themselves are separate specs. Because the app is REST/webhook-only (no persistent bot, no worker process — `render.yaml` is one gunicorn web service plus two crons), the platform is a **Discord Interactions Endpoint URL**: a single new Django POST view that Discord calls for every interaction, exactly like the existing Stripe-webhook and Discord-OAuth-callback views. It verifies the request signature, resolves the Discord user to a `Member`, dispatches to a registered handler, and returns JSON — no new process type.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Bot vs. HTTP endpoint | **HTTP Interactions Endpoint URL** (a Django POST view). Scout-verified: no ASGI, no worker, no gateway bot — a persistent bot has nowhere to run. |
| Signature verification | **Mandatory ed25519 via PyNaCl.** Invalid/missing signature → `401`. Fail closed (blank public key also rejects). Verified *before* JSON parse, member lookup, or any side effect. |
| Command source of truth | **One declarative registry** (`core/events/discord_commands.py`) read by *both* the registration command and the dispatcher. No second list to drift. |
| Registration scope | **Guild-scoped** to `SiteConfiguration.discord_server_id` by default (commands appear instantly, scoped to the Past Lives server). Global registration is available but propagates for up to an hour. |
| Member resolution | `Member.objects.filter(discord_user_id=<snowflake>).first()` — the verified, unique link (same lookup as `membership/discord_sync.py:185`). |
| Unlinked members | **Ephemeral prompt** to connect, linking to the existing anon `hub_discord_link_start` flow. This is the common case (most members aren't linked) and must never be a dead end. |
| Reply visibility | **Ephemeral by default** (`flags: 64`) — commands surface personal data. Public replies are per-command opt-in. |
| Slow handlers | **Deferred** (type-5 ack) so the 3s deadline is always met; the followup is `PATCH …/messages/@original`, well inside Discord's 15-min window. See §5.4 for the no-worker mechanism (the one real open technical decision, §10). |
| Reference command | Ship one tiny built-in `/fog-ping` so registration, dispatch, member-resolution, and the unlinked prompt are exercisable end-to-end and verifiable at go-live. No other commands in this spec. |

---

## 2. What already exists (reuse, don't reinvent)

This is assembly, not invention. Every Discord primitive already has a home.

| Need | Existing thing | Location |
|---|---|---|
| Signature-verifying webhook view pattern (csrf-exempt, verify → dispatch → return, never 500 on handler error) | `stripe_webhook` | `billing/views.py:117` |
| Bot auth header (`Authorization: Bot <token>`) + Discord REST base + `httpx` best-effort idiom (logs, never raises) | `bot_token()` / `_auth_headers()` / `_API_BASE` | `core/events/discord_dm.py:38-45`, `:34` |
| "Disabled when the credential is blank" idiom (`is_configured()`) | `discord_oauth.is_configured()` / `client_id()` | `core/events/discord_oauth.py` |
| Discord app credentials (blank default, `.strip()`) | `DISCORD_BOT_TOKEN`, `DISCORD_CLIENT_ID` (= application id), `DISCORD_CLIENT_SECRET` | `plfog/settings.py:339-341` |
| The Discord server id for guild-scoped registration | `SiteConfiguration.discord_server_id` | `core/models.py:209` |
| Member ↔ Discord join key (verified, unique) + linked check | `Member.discord_user_id`, `Member.discord_is_linked` | `membership/models.py:341`, `:494` |
| The "resolve members from Discord ids" query | `Member.objects.filter(discord_user_id__in=…)` | `membership/discord_sync.py:185` |
| Where an unlinked member should be sent to connect (anon, one-click, no FOG login) | `hub_discord_link_start` (`/discord/link/`) | `hub/discord_views.py:113`, `hub/urls.py:214` |
| A guild's own Discord channel plumbing (sibling for the new `discord_channel_id`) | `Guild.discord_webhook_url` / `discord_post_enabled` | `membership/models.py:1177-1192` |
| Management-command shape (idempotent, `--quiet`, post-deploy) | `seed_notification_templates` | `core/management/commands/seed_notification_templates.py` |
| Outbound-HTTP test stack | `respx` (already a dep), `httpx` (already a dep) | `requirements.txt` |

### Genuine gaps to close (kept minimal)

1. `PyNaCl` — the only new dependency (ed25519 verify).
2. `DISCORD_INTERACTIONS_PUBLIC_KEY` — one new setting.
3. `core/events/discord_interactions.py` — verify + reply-builders + followup helpers (mirrors `discord_dm.py`).
4. `core/events/discord_commands.py` — the declarative registry + autodiscovery + the `/fog-ping` reference command.
5. `core.views.discord_interactions` + one URL.
6. `register_discord_commands` management command.
7. `Guild.discord_channel_id` field + `Guild.objects.for_discord_channel()` + one additive migration.

---

## 3. Where the code lives

Mirrors the existing Discord layering. Infra (verify + REST) lives in `core/events/` beside `discord_dm.py` / `discord_oauth.py`; the view lives in `core` (lowest layer — it must import `membership` to resolve members, and `core` → `membership` is a legal edge for a *view*, the same direction `hub` already takes; the model layer is never imported the other way). Individual command handlers ship in their own apps' `discord_commands.py` and self-register — this foundation only provides the registry and the one reference command.

```
core/events/discord_interactions.py            NEW  verify_signature(), is_configured(), reply builders
                                                    (pong/reply/deferred_ack/unlinked_reply/error_reply),
                                                    ack_deferred(), send_followup() — httpx, best-effort
core/events/discord_commands.py                NEW  SlashCommand dataclass + registry (register/all_commands),
                                                    autodiscover(), and the built-in /fog-ping reference command
core/views.py                                   ~   discord_interactions view: csrf-exempt POST,
                                                    verify → PING/PONG → APPLICATION_COMMAND dispatch
core/urls.py                                    ~   path("discord/interactions/", views.discord_interactions,
                                                    name="discord_interactions")
core/apps.py                                    ~   ready(): call discord_commands.autodiscover()
core/management/commands/register_discord_commands.py
                                                NEW  PUT all_commands() to Discord (guild-scoped default)
membership/models.py                            ~   Guild.discord_channel_id + GuildManager.for_discord_channel()
membership/migrations/00xx_guild_discord_channel_id.py
                                                NEW  AddField (additive; reverse = auto RemoveField)
plfog/settings.py                               ~   DISCORD_INTERACTIONS_PUBLIC_KEY (blank default, .strip())
requirements.txt                                ~   PyNaCl>=1.5
tests/core/events/discord_interactions_spec.py  NEW  (see §9)
tests/core/events/discord_commands_spec.py       NEW
tests/core/views/discord_interactions_view_spec.py NEW
tests/core/management/register_discord_commands_spec.py NEW
tests/membership/models/guild_discord_channel_spec.py  NEW
plfog/version.py                                 ~   VERSION bump (no CHANGELOG entry — plumbing; see §8)
```

Home apps: **core** (endpoint, registry, verify, command) and **membership** (the `Guild.discord_channel_id` field).

---

## 4. Data model

Only one small additive change; the rest of the platform is stateless (Discord holds the interaction; we never persist it).

### 4.1 `Guild.discord_channel_id` (`membership/models.py`, beside `discord_webhook_url:1177`)

| Field | Type | Note |
|---|---|---|
| `discord_channel_id` | `CharField(max_length=32, blank=True, default="")` | The numeric Discord **channel** id of this guild's channel. When a guild slash command is run *in* that channel, the platform auto-detects the guild. Blank = no auto-detect (the command falls back to an explicit `guild` option). Mirrors `SiteConfiguration.discord_role_message_channel_id` (`core/models.py:217`). |

`help_text`: *"This guild's Discord channel id (right-click the channel → Copy Channel ID with Developer Mode on). When a member runs a guild slash command in this channel, we know which guild they mean. This is NOT the webhook URL above — leave blank if you don't use channel auto-detection."*

> **Flagged (new field = new convention, per "don't invent unilaterally"):** storing `discord_channel_id` explicitly rather than deriving it from `discord_webhook_url`. A webhook URL does not expose its channel id without an extra API round-trip, and channel auto-detect wants a cheap local lookup. The field is minimal and matches the existing `*_channel_id` fields on `SiteConfiguration`. Alternative (deferred): resolve the webhook's channel once and cache it — rejected as more moving parts.

### 4.2 Manager method

```python
# GuildManager
def for_discord_channel(self, channel_id: str) -> Guild | None:
    """The active guild whose Discord channel is channel_id, or None if unmapped."""
    if not channel_id:
        return None
    return self.filter(is_active=True, discord_channel_id=channel_id).first()
```

`.first()` (not `.get()`) so an accidental duplicate mapping degrades to the disambiguation fallback (§6 Reply E) instead of raising.

### 4.3 Migration

`membership/00xx_guild_discord_channel_id`: a single `AddField`. Additive → reverse is the auto `RemoveField` (no data migration, no reverse to hand-write). `ruff format` + `git add` the migration in the same commit (per the migrations-need-ruff-format note).

---

## 5. Business logic (the platform machinery)

All of this is fat-model / service code; the view is thin (verify, branch on interaction type, call the dispatcher, return the dict as JSON).

### 5.1 Signature verification — `verify_signature()` (security-critical)

Discord signs every request. This is the gate; get it exactly right.

- Read the **raw** body (`request.body`) *before* any parsing, and the two headers `X-Signature-Ed25519` (hex signature) and `X-Signature-Timestamp`.
- The signed message is **`timestamp_bytes + raw_body_bytes`** (concatenation, timestamp first).
- Verify against the application's **public key** with PyNaCl:

```python
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

def verify_signature(public_key_hex: str, signature_hex: str, timestamp: str, body: bytes) -> bool:
    if not public_key_hex or not signature_hex or not timestamp:
        return False
    try:
        VerifyKey(bytes.fromhex(public_key_hex)).verify(timestamp.encode() + body, bytes.fromhex(signature_hex))
    except (BadSignatureError, ValueError):   # ValueError = malformed hex
        return False
    return True
```

- Rules the view enforces:
  - The view is **`@csrf_exempt`** (Discord cannot send a CSRF token) and **`@require_POST`**.
  - Verification runs **first** — before JSON parse, before member lookup, before any handler.
  - Any failure → **`return HttpResponse(status=401)`** (Discord's spec *requires* 401 for an invalid signature; it sends a deliberately bad-signature request when you save the Endpoint URL and again periodically, and expects 401).
  - `is_configured()` is `False` when `DISCORD_INTERACTIONS_PUBLIC_KEY` is blank → verification returns `False` → `401`. **Fail closed** — an unconfigured endpoint rejects everything rather than trusting an unsigned body.
  - Never log the raw body or the public key at anything above debug.

### 5.2 The dispatch view — `core.views.discord_interactions`

```python
@csrf_exempt
@require_POST
def discord_interactions(request):
    if not verify_signature(settings.DISCORD_INTERACTIONS_PUBLIC_KEY,
                            request.headers.get("X-Signature-Ed25519", ""),
                            request.headers.get("X-Signature-Timestamp", ""),
                            request.body):
        return HttpResponse(status=401)
    interaction = json.loads(request.body)
    if interaction["type"] == 1:                       # PING
        return JsonResponse(pong())                    # {"type": 1}
    if interaction["type"] == 2:                       # APPLICATION_COMMAND
        return JsonResponse(dispatch(interaction, request))
    return HttpResponse(status=200)                    # future interaction types: ack, do nothing
```

- **PING (type 1) → PONG (type 1).** Discord's liveness probe. Must still pass signature verification (it is signed).
- **APPLICATION_COMMAND (type 2) → `dispatch()`.** Returns a reply dict serialized as the HTTP body.
- **Never 500 back to Discord.** `dispatch()` wraps the handler in a try/except; any handler exception is logged and converted to `error_reply()` (an ephemeral "something went wrong") with a `200`. Repeated 5xx makes Discord auto-disable the Endpoint URL — the only non-2xx we ever send is the intentional `401` for a bad signature.

### 5.3 Dispatch + the command registry — `core/events/discord_commands.py`

One declarative source of truth, read by both registration and dispatch (mirrors the event `registry.py` autodiscovery).

```python
@dataclass(frozen=True)
class SlashCommand:
    name: str                       # e.g. "fog-ping"
    description: str                # shown in Discord's command picker
    handler: Callable[[Interaction, Member | None], dict]   # returns a reply dict
    options: list[dict] = field(default_factory=list)       # Discord option objects
    defer: bool = False             # slow handler → ack deferred first (§5.4)
    ephemeral: bool = True          # personal data by default
    requires_link: bool = True      # unlinked → the connect prompt, handler never runs
    scope: Literal["guild", "global"] = "guild"

_REGISTRY: dict[str, SlashCommand] = {}
def register(cmd: SlashCommand) -> None: ...      # fail loudly on a duplicate name
def all_commands() -> list[SlashCommand]: ...
def autodiscover() -> None: ...                   # import each installed app's discord_commands module
```

`dispatch(interaction, request)`:

1. Look up `_REGISTRY[interaction["data"]["name"]]` — unknown name → `error_reply()` (defensive; means the registry and Discord's registered set drifted).
2. Resolve the member (§5.5). If `cmd.requires_link` and no member → **return `unlinked_reply()`** (the handler never runs).
3. If `cmd.defer` → run the deferred path (§5.4).
4. Else → call `cmd.handler(interaction, member)` and return its reply dict (the handler chooses `reply(..., ephemeral=cmd.ephemeral)`).

The **reference command** `/fog-ping` (`requires_link=True`, `ephemeral=True`, `defer=False`): a linked member gets `reply("**Past Lives** — you're connected as {member.display_name}. Everything's wired up ✅", ephemeral=True)` with a link button to the hub home; an unlinked member gets the connect prompt (handled by step 2, so `/fog-ping` also proves the unlinked gate live). It exists only to make the platform verifiable end-to-end; it is explicitly the only command in this spec.

### 5.4 The 3-second deadline & deferred responses (no worker)

Discord requires the *initial* response within 3 seconds.

- **Fast path (default, `defer=False`):** the handler returns a **type-4** (`CHANNEL_MESSAGE_WITH_SOURCE`) reply dict, which the view returns as the HTTP body. A single transactional action (one Resend email, a couple of DB reads) reliably completes well under 3s, so most commands never defer.
- **Deferred path (`defer=True`), for handlers that can't guarantee <3s:**
  1. `ack_deferred(interaction_id, interaction_token, ephemeral=cmd.ephemeral)` → **`POST /interactions/{id}/{token}/callback`** with `{"type": 5, "data": {"flags": 64 if ephemeral}}`. This one fast REST call satisfies the 3s clock; Discord shows the native "thinking…" indicator.
  2. Run the handler **synchronously in the same gunicorn request** (now bounded only by the 15-min followup window and gunicorn's request `--timeout`, not the 3s clock).
  3. `send_followup(interaction_token, message)` → **`PATCH /webhooks/{DISCORD_CLIENT_ID}/{token}/messages/@original`** to replace the "thinking…" placeholder with the real reply.
  4. The view returns an empty `HttpResponse(status=200)` to close the original POST (Discord ignores the body once the interaction was acked via the callback endpoint).

  > This keeps the locked "no new process type" — the work runs in the web worker, not a background process. The **one open technical decision (§10)** is exactly this mechanism: the REST-callback-ack-then-work-in-request approach above vs. the classic HTTP-webhook alternative (return type-5 *as the HTTP body*, then finish the work in a short-lived daemon thread in the same worker). Both avoid a new process; the REST-callback form matches the "same request" wording and doesn't depend on a thread outliving the response, at the cost of one extra Discord call. Verify current Discord acceptance of a REST-callback ack for HTTP-interaction apps at build time, and confirm gunicorn `--timeout` comfortably exceeds the slowest deferred handler. If measurement shows every planned command finishes <3s, **drop the deferred path from v1** (YAGNI) and keep only the fast type-4 path plus the helper stubs.

### 5.5 Member resolution — `resolve_member(interaction)`

```python
def resolve_member(interaction: dict) -> Member | None:
    user = interaction.get("member", {}).get("user") or interaction.get("user")  # guild vs DM context
    discord_user_id = (user or {}).get("id", "")
    if not discord_user_id:
        return None
    return Member.objects.filter(discord_user_id=discord_user_id).first()
```

- `interaction.member.user.id` in a guild channel; `interaction.user.id` in a DM. Handle both.
- The lookup is on the **verified, unique** `discord_user_id` (never the free-text `discord_handle`).
- `None` → the unlinked path (§6 Reply B). This is the expected common case, not an error.

### 5.6 Channel → guild mapping — `resolve_guild(interaction, member)`

For guild-scoped commands. The payload carries `interaction.channel_id`.

1. If the command declares an explicit `guild` option and it's set → use it (explicit beats inferred).
2. Else `Guild.objects.for_discord_channel(interaction["channel_id"])` → the mapped guild.
3. Unmapped/ambiguous → **the disambiguation fallback** (§6 Reply E): an ephemeral reply asking the member to run the command in their guild's channel or pass the `guild` option, listing the guilds they're a member of. Never a silent failure.

The foundation provides `resolve_guild()` + the fallback reply; each guild command opts into the `guild` option and calls the helper.

### 5.7 Reply builders & REST helpers — `core/events/discord_interactions.py`

All best-effort (log + return falsy on failure, never raise — same contract as `discord_dm.post_dm`). Reuses `bot_token()` / `_auth_headers()` / `_API_BASE` from `discord_dm.py`.

| Helper | Returns / does |
|---|---|
| `pong()` | `{"type": 1}` |
| `reply(content, *, ephemeral=True, embeds=None, components=None)` | `{"type": 4, "data": {"content": …, "flags": 64 if ephemeral else 0, …}}` |
| `deferred_ack(*, ephemeral=True)` | `{"type": 5, "data": {"flags": 64 if ephemeral else 0}}` |
| `unlinked_reply(link_url)` | ephemeral `reply` with the connect copy + a **link-style button** (`components: [{type:1, components:[{type:2, style:5, label:"Connect my Past Lives account", url:link_url}]}]`) and a markdown-link fallback in `content` |
| `error_reply()` | ephemeral `reply("Something went wrong on our end — please try again in a minute.")` |
| `ack_deferred(interaction_id, token, *, ephemeral)` | `POST /interactions/{id}/{token}/callback` (§5.4) |
| `send_followup(token, *, content, embeds=None)` | `PATCH /webhooks/{DISCORD_CLIENT_ID}/{token}/messages/@original` |

Consistent format across every reply: a **bold title** (or an embed whose title links to the relevant hub page), the body, and a trailing **link/button back into the hub** — the same "make it act, not just inform" bar the email templates hold. Mirror `discord_dm.format_dm_content` for text shape.

### 5.8 Registration — `register_discord_commands` management command

Idempotent PUT (Discord's bulk-overwrite is the whole set at once).

- `all_commands()` → each serialized to Discord's application-command JSON (`name`, `description`, `options`, `type: 1`).
- Split by `scope`: `guild` commands → **`PUT /applications/{DISCORD_CLIENT_ID}/guilds/{SiteConfiguration.load().discord_server_id}/commands`** (instant, scoped to the Past Lives server); `global` → **`PUT /applications/{DISCORD_CLIENT_ID}/commands`** (up to ~1h propagation).
- Auth header `Authorization: Bot {DISCORD_BOT_TOKEN}` (reuse `_auth_headers()`).
- Guards: if `DISCORD_CLIENT_ID` or `DISCORD_BOT_TOKEN` is blank, **fail loudly** with a clear message (don't silently no-op — registration is an explicit go-live step, not runtime). If `scope=guild` but `discord_server_id` is blank, error and tell the operator to set it.
- `--global-only` / `--guild-only` / `--dry-run` flags; prints the command names it registered. Run via a Render one-off job at go-live and whenever the command set changes.

---

## 6. Message UX — the Discord reply experience  ← completeness checklist, adapted to Discord

The "screens" here are the **replies a member sees in Discord**. The UX-completeness bar is re-read through the Discord lens: *ephemeral vs public*, *clear actionable replies*, *the unlinked prompt*, *empty/error states*, and *no dead ends*. Every reply is described below, not just the happy path. (The dark/light-theme and mobile rows of the checklist don't apply — Discord renders the client — so they're replaced by *Discord rendering constraints*.)

### Global rules

- **Ephemeral by default.** Every reply that carries personal data (the member's name, their guilds, their tab, "you're not linked") sets `flags: 64` so only the invoking member sees it — no leaking one member's data into a public channel. A command is public **only** when it explicitly opts in (e.g. a future "post an announcement"); the foundation default is ephemeral.
- **Never a dead end.** Every reply ends with a next step — a link button into the relevant hub page, or a clear instruction. No reply is a bare acknowledgement.
- **Never a raw error to Discord.** Handler exceptions and Discord-API hiccups become a friendly ephemeral reply, not a 500 (which would get the endpoint disabled).

### Reply A — Linked member, fast command (type-4, ephemeral) — the success state

- **What:** the `/fog-ping` (and every future fast command) result. `reply("**Past Lives** — you're connected as {display_name}. …", ephemeral=True, components=[link button → hub home])`.
- **Format:** bold title, one-line body, a link button (or embed with a linked title) back to the hub. This *is* the success feedback — the equivalent of the hub's `trigger_toast()`.
- **Visible to:** only the member.

### Reply B — Unlinked member (the critical gate) — the empty/not-provisioned state

- **When:** `resolve_member()` returns `None` and the command `requires_link` (the common case — most members aren't linked).
- **What:** `unlinked_reply(absolute hub_discord_link_start URL)` — ephemeral. Copy: *"You need to connect your Discord to your Past Lives account first. It's one click — if your Discord email matches your membership, you'll be linked instantly."* Plus a **link-style button "Connect my Past Lives account"** → the absolute `hub_discord_link_start` URL, and a plain markdown-link fallback in the content.
- **Why this flow:** `hub_discord_link_start` is the anon, no-FOG-login, one-click link posted-in-Discord path already built for exactly this. The URL is absolute (`request.build_absolute_uri` or the spine's `_absolute_url()`), never a bare path.
- **Visible to:** only the member (so an unlinked member isn't publicly called out).

### Reply C — Deferred command (type-5) — the loading state

- **When:** `cmd.defer` (slow handler). Discord shows its native **"{app} is thinking…"** indicator immediately (the type-5 ack); the followup `PATCH @original` swaps in the real reply within seconds.
- **Ephemeral** if the trigger was ephemeral (the deferred ack carries `flags: 64`). If the followup PATCH fails (network), it's logged best-effort — the member sees the thinking indicator resolve to Discord's own "interaction failed" rather than a wrong answer; we never send a misleading success.

### Reply D — Error state

- **When:** any handler raises, or a required integration is down.
- **What:** `error_reply()` — ephemeral, *"Something went wrong on our end — please try again in a minute."* Logged server-side with the interaction id (never the token/body above debug). Returned with HTTP `200` so Discord doesn't disable the endpoint.

### Reply E — Guild command in an unmapped/ambiguous channel — the disambiguation state

- **When:** a guild-scoped command where `resolve_guild()` can't infer the guild (channel unmapped, or the member passed no `guild` option).
- **What:** ephemeral reply, *"I couldn't tell which guild you mean. Run this in your guild's Discord channel, or add the guild option."* — listing the guilds the member belongs to (from their `GuildMembership` rows). Never a silent no-op.

### Discord rendering constraints (replaces dark/light + mobile)

- **`content` ≤ 2000 chars; embed limits** (title 256, description 4096, ≤25 fields) — keep replies short; long output links to the hub rather than dumping.
- **Ephemeral flag** is `64` and must be set on *both* the deferred ack and the followup for a deferred ephemeral reply, or the "thinking…" is ephemeral but the result leaks public.
- **Markdown** renders in Discord (`**bold**`, links) — reuse `format_dm_content`'s conventions.
- **Link buttons** (`style: 5`) require a valid absolute `https://` URL — a relative path silently fails to render.

---

## 7. Notifications / emails / activity

None from the foundation itself. The platform is a *receiver*; it does not emit through the notification spine. Individual commands that send email/DMs will wire `emit()` (with a unique `period`) in their own specs. No new `SiteActivity` kind here — though a follow-up may log command usage; deferred (§10).

---

## 8. Build order (phased; each phase ships green)

1. **Dependency + setting + verify core.** Add `PyNaCl>=1.5` to `requirements.txt`; add `DISCORD_INTERACTIONS_PUBLIC_KEY` to `settings.py` (beside `:339-341`). Build `discord_interactions.verify_signature()` + `is_configured()` + reply builders + REST helpers. Tests: signature pass/fail/tamper/missing/unconfigured; reply-builder shapes. (Ships green — no wiring yet.)
2. **Registry + view + URL + reference command.** `discord_commands.py` (registry, autodiscover, `/fog-ping`); the `discord_interactions` view (PING/PONG + dispatch, never-500); the URL; `core/apps.py` ready-hook autodiscover. Tests: PING/PONG, dispatch → reply, unknown command → error_reply, member resolution, unlinked path, deferred flow (respx). 
3. **Channel → guild mapping.** `Guild.discord_channel_id` + `for_discord_channel()` + migration + `resolve_guild()` fallback. Tests: mapped / unmapped / ambiguous. (`ruff format` + `git add` the migration together.)
4. **Registration command.** `register_discord_commands` (guild-scoped default, guards, flags). Tests: PUT URL correct per scope, bot auth header, body = serialized `all_commands()`, blank-credential guard (respx).
5. **Housekeeping.** Bump `plfog/version.py` VERSION. **No CHANGELOG entry** — this is invisible plumbing (the only member-visible surface is the `/fog-ping` diagnostic); per the versioning rules an invisible-to-members change gets no entry, and the *first real slash-command spec* that rides on this platform carries the member-facing announcement. Do **not** run the Discord announce workflow for this release.

> Spec only — do not build until approved.

Each phase runs the full suite + `ruff format .` + `ruff check .` + `mypy .` green before the next.

---

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*` (never `context_*` — not collected), factory-boy, respx for all Discord REST, ≥98% coverage gate, run in the `plfog-web` Docker image (`--no-cov` for subsets). Tests generate a throwaway ed25519 keypair (`nacl.signing.SigningKey`), sign payloads with the private key, and set the matching public key via `override_settings(DISCORD_INTERACTIONS_PUBLIC_KEY=…)`.

- **Signature verify** (`discord_interactions_spec.py`): valid sig → `True`; wrong sig → `False`; tampered body → `False`; wrong timestamp → `False`; missing header(s) → `False`; malformed hex → `False`; blank public key → `False`.
- **View** (`discord_interactions_view_spec.py`):
  - valid signature + PING (type 1) → `200`, body `{"type": 1}`.
  - bad/missing signature → `401`, handler never reached.
  - blank public key → `401` (fail closed).
  - APPLICATION_COMMAND → correct handler invoked, type-4 reply body, ephemeral flag set.
  - handler raises → `error_reply()` body + `200` (never 500 to Discord).
  - unknown command name → `error_reply()`, `200`.
- **Member resolution**: `member.user.id` (guild) and `user.id` (DM) both resolve; known `discord_user_id` → `Member`; unknown → `None` → `unlinked_reply` (assert `flags: 64` + the absolute `hub_discord_link_start` URL in the button).
- **Channel → guild** (`guild_discord_channel_spec.py`): mapped channel → guild; unmapped → `None` → disambiguation reply; duplicate mapping → `.first()`, no raise; explicit `guild` option beats channel inference.
- **Deferred flow** (`discord_commands_spec.py`): `defer=True` → `ack_deferred` POSTs type-5 to `/interactions/{id}/{token}/callback` (respx), handler runs, `send_followup` PATCHes `…/messages/@original` (respx), ephemeral flag carried on both; followup network error → logged, no raise.
- **Registration** (`register_discord_commands_spec.py`): guild scope → PUT `…/guilds/{server_id}/commands`; global → PUT `…/commands`; bot auth header present; body equals serialized `all_commands()`; blank `DISCORD_CLIENT_ID`/`DISCORD_BOT_TOKEN`/`discord_server_id` → fails loudly (raises `CommandError`), no PUT.
- **Best-effort contract**: every REST helper returns falsy + logs on `httpx.HTTPError` / non-2xx, never raises.

No tz/date-window gotchas (stateless). Watch the note that a test-client POST can't catch HTML-structure bugs — irrelevant here (JSON in/out), but assert on the *dict shape and `flags`*, not just a 200.

---

## 10. Open / deferred / out of scope

### Flagged for confirmation (new conventions — per "don't invent unilaterally")
1. **`Guild.discord_channel_id`** — new field for channel auto-detect (§4.1). Minimal, mirrors `SiteConfiguration.*_channel_id`.
2. **`DISCORD_INTERACTIONS_PUBLIC_KEY`** — new setting (blank default, `.strip()`, like `:339-341`).
3. **`PyNaCl>=1.5`** — new dependency (ed25519 verify; `cryptography` is present but PyNaCl is the standard, documented Discord-interactions verifier).
4. **`/discord/interactions/` route** on the hub domain — confirm SurfaceMiddleware allowlists it (like the Stripe webhook / health check), csrf-exempt, no login, reachable publicly over HTTPS.
5. **`/fog-ping` reference command** shipping in the foundation — a diagnostic smoke command, or should the platform ship with an empty registry? (Recommended: ship it; it's the only way to verify the whole platform live at go-live.)
6. **Command-registry module + `autodiscover()`** — the plug-in pattern each future command self-registers through.

### The one real technical decision (§5.4)
7. **Deferred-execution mechanism under sync gunicorn, no worker:** REST-callback ack (`POST …/callback` type-5) → work in the same request → empty 200, **vs.** return type-5 as the HTTP body → finish in a short-lived worker thread. Both keep "no new process type." Recommendation: REST-callback form (matches "same request," no thread-lifetime risk), pending build-time verification that Discord accepts a REST-callback ack for HTTP-interaction apps, and that gunicorn `--timeout` exceeds the slowest deferred handler. **If every planned command reliably finishes <3s, drop the deferred path from v1 (YAGNI)** and keep only type-4 + helper stubs.

### Deferred (explicitly not built here)
- **Any actual command** (`/schedule-orientation`, etc.) — separate specs riding on this platform.
- **Message components beyond link buttons** — select menus, action buttons that fire follow-up interactions (`MESSAGE_COMPONENT` / type-3), autocomplete (type-4 interactions), and modals (type-5 interactions) are a later platform increment; v1 handles PING + APPLICATION_COMMAND only (other types → ack + no-op).
- **Command-usage `SiteActivity` logging / analytics** — a later add if leads want to see command usage.
- **Global (DM-able) commands** — the default is guild-scoped to the Past Lives server; global registration is supported by the command but off by default.
- **Rate-limiting / abuse controls** — every request is ed25519-signed by Discord, so only Discord can reach the endpoint; extra throttling is unnecessary until a real command warrants it.
- **CHANGELOG entry** — none (invisible plumbing, §8); the first real command spec carries the member-facing announcement.

### Go-live checklist (ops prerequisites — not code, but required to switch it on)
1. **Deploy** the code with `PyNaCl` in `requirements.txt`.
2. **Set `DISCORD_INTERACTIONS_PUBLIC_KEY`** on Render (+ local `.env`, Hetzner) — copy it from the **Discord Developer Portal → your application → General Information → Public Key**. Env writes need a **manual redeploy** on Render. Do this *before* step 3 (the endpoint must verify + PONG the moment Discord probes it).
3. **Set the Interactions Endpoint URL** in the portal to `https://pastlives.app/discord/interactions/`. Discord immediately sends a signed PING and a bad-signature probe; saving only succeeds if the live endpoint PONGs the good one and `401`s the bad one.
4. **Confirm the app has the `applications.commands` scope** and the bot is in the Past Lives server (the bot was already invited for the DM channel — verify the scope, re-invite if missing).
5. **Confirm `DISCORD_BOT_TOKEN` + `DISCORD_CLIENT_ID`** are set on Render (already present for the DM channel) and **`SiteConfiguration.discord_server_id`** holds the Past Lives server id.
6. **Run `python manage.py register_discord_commands`** via a Render one-off job (guild-scoped → commands appear in the server instantly).
7. **(Optional) Set each guild's `discord_channel_id`** in guild edit / admin for channel auto-detection — commands still work without it via the `guild` option.
8. **Verify in Discord:** run `/fog-ping` as a **linked** member → ephemeral "you're connected" reply; run it as an **unlinked** account → the ephemeral connect prompt with the working link button. Confirm neither reply is visible to other members.
9. **Confirm gunicorn `--timeout`** comfortably exceeds the slowest deferred handler (default 30s covers a single email send).
