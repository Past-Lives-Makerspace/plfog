# Discord new-joiner DM nudge — Spec & Implementation Plan

**Status:** Approved — building via the fog-quick-feature pipeline (2026-08-12).
**Date:** 2026-08-12
**Surface:** Discord (Fog Bot DM) + FOG hub admin (`templates/hub/admin/site_settings.html`, Discord tab).
**Related:** `2026-07-13-discord-member-commands.md` (the `/link` command this DM points at), the reaction-sync nudge in `membership/discord_sync.py`.

---

## 1. Summary

Someone joins the Past Lives Discord server and, within 15 minutes, Fog Bot DMs them once: welcome, here's where to create your member portal account, type `/link` to connect. Today a new joiner gets nothing until they happen to react in #choose-your-guild — this closes that gap at the front door. Plus a one-off management command to sweep all *existing* unlinked server members through the same once-only ledger, and a site-settings kill switch.

### Locked decisions (from Jo)

| Decision | Choice |
|---|---|
| Delivery mechanism | Piggyback on the existing 15-minute reconcile cron (`reconcile_reactions`) — no render.yaml change. |
| Channel | **DM only.** Explicitly no welcome-channel mention fallback. |
| New-joiner detection | **Window-based, no persisted tick state**: `joined_at` within the last 48 h AND unlinked AND not in the ledger. Old members are never touched by the cron. |
| Bots | Skipped (`user.bot`). |
| Backfill | On-demand management command only, never scheduled; same ledger, ignores the 48 h window. |
| Kill switch | `SiteConfiguration` BooleanField, default **True**, on the site-settings Discord tab. |
| Config gate *(decided here)* | The cron step requires the **full reconcile config** (bot token + server + channel + message ids), because it runs inside `reconcile_reactions()` which already no-ops without it — one gate, one idiom, and prod always has all three ids set whenever the Discord integration is on at all. |
| Command vs toggle *(decided here)* | The sweep command **also requires the toggle ON** — one switch kills all joiner nudging, cron and sweep alike. |

## 2. What already exists (reuse, don't reinvent)

Verified on `main` 2026-08-12.

| Need | Existing thing | Location |
|---|---|---|
| Once-only DM ledger pattern | `DiscordLinkNudge` (discord_user_id + created_at, UniqueConstraint; row written even on undeliverable-403) | `membership/models.py:3798-3825` |
| Send orchestration to mirror | `_nudge_unlinked_reactors()` — set minus linked minus ledgered, sorted loop, DM, ledger row | `membership/discord_sync.py:230-253` |
| DM sender contract | `send_dm_text()` — `False` on any 403 (undeliverable, never retry); raises on 401/429/5xx/network | `core/events/discord_dm.py:159-192` |
| Cron entry + stats | `reconcile_reactions()` → `ReconcileStats`; `_server_id` already unpacked and **unused** at `:169` — the members fetch needs exactly it | `membership/discord_sync.py:155-210`, stats `:65-73` |
| Config gate | `_reconcile_config()` — bot token + `discord_server_id`/channel/message ids or `None` | `membership/discord_sync.py:96-112` |
| Paginated best-effort Discord reader to model on | `fetch_reactors()` — `after` pagination, 429 Retry-After (cap 5 s, 2 retries), `complete` flag, bot filtering | `core/events/discord_reactions.py:51-121` |
| Cron management command template | `sync_discord_guild_roles` — thin BaseCommand, lazy import, "skipped (not configured)" branch, SUCCESS summary | `core/management/commands/sync_discord_guild_roles.py` |
| Settings toggle pattern | `discord_events_sync_enabled` BooleanField (verbose_name + help_text) | `core/models.py:339-347` (singleton `SiteConfiguration` at `:127`) |
| Settings form | `SiteSettingsForm.Meta.fields` (discord ids at `:601-603`) | `hub/forms.py:588-625` |
| Settings template | Discord tab `x-show="tab === 'discord'"` at `:547`; "Guild-role sync connection" card `:563-574`; General-tab exclusion `{% if %}` at `:159`; single "Save settings" submit at `:710` | `templates/hub/admin/site_settings.html` |
| "Linked" definition | `Member.discord_is_linked` = `bool(discord_user_id)` | `membership/models.py:553` |
| DM copy tone + URL | `_link_nudge_content()` (the "halfway there" DM), `_absolute_url()` + `reverse("hub_discord_link_start")` | `membership/discord_sync.py:213-227`, `:45-48` |
| Test conventions | `sync_config` fixture `:37`, `_mock_dm` helper `:67`, full nudge battery `describe_reconcile_nudges_unlinked_reactors` `:220` | `tests/membership/discord_sync_spec.py` |

### Genuine gaps (kept minimal)

1. **No guild-members reader exists** — `GET /guilds/{id}/members` is new code (`core/events/discord_members.py`), modeled exactly on `fetch_reactors`.
2. **`DiscordJoinWelcome`** ledger model + migration.
3. **`discord_joiner_nudge_enabled`** on `SiteConfiguration` + migration, form field, template card.
4. **`nudge_unlinked_discord_members`** management command.

> **Ops note:** `GET /guilds/{id}/members` requires the bot's **Server Members Intent** (Discord developer portal → Bot → Privileged Gateway Intents). **Verified live 2026-08-12: the intent is already enabled — Fog Bot lists members successfully in production.** If it were ever revoked, Discord returns 403, the reader reports `complete=False` with zero members, and the feature quietly welcomes nobody — which is why the incomplete flag is surfaced in cron stdout (§6). Payload gotcha: Discord **omits** `user.bot` for humans (it is not `false`, it is absent) — the reader must treat a missing flag as "not a bot".

## 3. Where the code lives

```
core/events/discord_members.py                          # NEW: fetch_guild_members()
core/models.py                                          # + discord_joiner_nudge_enabled
core/migrations/00XX_*.py                               # AddField (auto-reversible)
core/management/commands/nudge_unlinked_discord_members.py  # NEW: on-demand sweep
membership/models.py                                    # + DiscordJoinWelcome (next to DiscordLinkNudge)
membership/migrations/01XX_*.py                         # CreateModel (auto-reversible; number from makemigrations)
membership/discord_sync.py                              # + _welcome_new_joiners(), JoinWelcomeStats,
                                                        #   ReconcileStats.welcomed, hook in reconcile_reactions()
hub/forms.py                                            # + field in SiteSettingsForm.Meta.fields
templates/hub/admin/site_settings.html                  # + card on Discord tab, + General-tab exclusion
tests/core/events/discord_members_spec.py               # NEW
tests/core/management/nudge_unlinked_discord_members_spec.py  # NEW
tests/membership/discord_sync_spec.py                   # + welcome battery
tests/hub/site_settings_discord_spec.py                 # + toggle cases
```

## 4. Data model

**`DiscordJoinWelcome`** (`membership/models.py`, sibling of `DiscordLinkNudge` — clone it):

| Field | Type | Notes |
|---|---|---|
| `discord_user_id` | `CharField(max_length=32)` | `help_text="The Discord account id (snowflake) of the server joiner who was sent the one-time welcome DM. One welcome ever per Discord user."` |
| `created_at` | `DateTimeField(auto_now_add=True)` | `help_text="When the welcome DM was sent (or skipped because the user's DMs are closed)."` |

- `Meta.constraints`: `UniqueConstraint(fields=["discord_user_id"], name="uq_discordjoinwelcome_discord_user_id")`.
- `__str__` → `f"Join welcome → {self.discord_user_id}"`.
- Docstring states the ledger law: a row means *handled forever* — sent, or undeliverable (403) — never retried, kept even if they later link/unlink. Written only by the reconcile cron and the sweep command.
- **Leave-and-rejoin is deliberately covered:** someone who leaves the server and rejoins later gets a fresh `joined_at` inside the 48 h window, but their ledger row still blocks a second DM — one welcome per Discord user, ever. This is the desired behavior (never re-spam a returner), not an accident.
- **Ledger writes use `get_or_create`, not `create`:** the cron and a manually-run sweep can overlap on the same user; `get_or_create` means the loser of that race skips silently instead of raising `IntegrityError` and aborting the rest of the loop. The rare double-DM inside that overlap window is accepted (same person, same minute, same message).
- Migration: plain `CreateModel`, auto-reversible.

**`SiteConfiguration.discord_joiner_nudge_enabled`** (`core/models.py`, next to the other discord fields):

```python
discord_joiner_nudge_enabled = models.BooleanField(
    default=True,
    verbose_name="DM new Discord joiners",
    help_text=(
        "When on (and the guild-role sync connection above is configured), Fog Bot sends each NEW "
        "server joiner one welcome DM prompting them to create their member portal account and link "
        "Discord. Each person is ever DMed once. Turning this off also disables the manual sweep command."
    ),
)
```

Migration: plain `AddField`, auto-reversible.

## 5. Business logic (fat models / service layer)

### `core/events/discord_members.py` — the member-listing helper (NEW)

Modeled line-for-line on `fetch_reactors` (`discord_reactions.py:51`): same `API_BASE`, timeout, retry caps.

```python
@dataclass(frozen=True)
class GuildMemberInfo:
    user_id: str
    bot: bool
    joined_at: datetime | None   # parsed from ISO8601; None if absent/unparseable

@dataclass(frozen=True)
class GuildMemberPage:
    members: list[GuildMemberInfo]
    complete: bool

def fetch_guild_members(server_id: str) -> GuildMemberPage: ...
```

Contract:
- Pages `GET /guilds/{server_id}/members?limit=1000&after={last_user_id}` until a page returns fewer than 1000.
- Response items carry `user.id`, `user.bot`, `joined_at` (ISO8601). Parse `joined_at` with `datetime.fromisoformat`; a missing/garbled value → `None` (caller skips it — no window match, no crash).
- 429: honor `Retry-After` capped at 5 s, max 2 retries, then bail incomplete. Any non-2xx / network error / missing last-id: stop paging, return `complete=False` with what was gathered.
- Blank token or server id → `complete=False`, empty.
- **An incomplete fetch is SAFE to act on**: everyone seen genuinely is a server member, and the ledger dedupes — so we welcome whoever we saw and the next tick (or the sweep) catches the rest. The caller logs a warning on incomplete.

### `membership/discord_sync.py` — the welcome step

```python
@dataclass(frozen=True)
class JoinWelcomeStats:
    welcomed: int = 0          # DMs delivered (2xx)
    undeliverable: int = 0     # 403s — ledgered anyway, never retried
    skipped_linked: int = 0
    skipped_ledgered: int = 0
```

- `_join_welcome_content() -> str` — the DM body (§ DM copy below), built with `_absolute_url(reverse("hub_discord_link_start"))` like `_link_nudge_content`.
- `_send_join_welcomes(candidates: list[GuildMemberInfo]) -> JoinWelcomeStats` — the shared core both callers use. Mirrors `_nudge_unlinked_reactors`: drop bots, compute id set, subtract `Member.objects.filter(discord_user_id__in=...)` (linked) and `DiscordJoinWelcome.objects.filter(discord_user_id__in=...)` (ledgered), loop **sorted**, `discord_dm.send_dm_text(...)`, **`get_or_create`** the ledger row even when the send returns `False` (403 = undeliverable forever ≠ retry forever, `logger.info`; `get_or_create` per §4 so a cron/sweep overlap can't `IntegrityError`-abort the loop). Any other DM error raises — no ledger row, the cron/command surfaces it loudly, the rest retry next run.
- `_welcome_new_joiners(server_id: str) -> tuple[int, bool]` — the cron step; returns `(rows_written, fetch_complete)`. Returns `(0, True)` immediately when `SiteConfiguration.load().discord_joiner_nudge_enabled` is off. Otherwise `page = fetch_guild_members(server_id)` (warn on `not page.complete`), filter to `joined_at is not None and joined_at >= timezone.now() - timedelta(hours=48)`, delegate to `_send_join_welcomes`, return `(stats.welcomed + stats.undeliverable, page.complete)` (rows written — same semantics as `nudged`).
- **Hook**: in `reconcile_reactions()`, immediately after the `_nudge_unlinked_reactors(...)` call at `:208` — finally using the `_server_id` unpacked at `:169`. Placed last for the same reason the nudge is: a DM failure can't block the membership reconcile.
- `ReconcileStats` gains `welcomed: int = 0` and `welcome_fetch_complete: bool = True` — the incomplete flag must reach cron stdout (§6), because a persistent 403 (revoked intent) would otherwise look like a healthy "0 welcomed" forever.

### `nudge_unlinked_discord_members` management command (NEW)

Clone the `sync_discord_guild_roles` shape (thin, lazy import). `handle()`:
1. Bot token + `discord_server_id` blank → `self.stdout.write("Skipped (Discord not configured).")` and return. (The sweep needs no channel/message ids — it never reads reactions.)
2. Toggle off → `self.stdout.write("Skipped (new-joiner DMs are turned off in Site Settings).")` and return.
3. `page = fetch_guild_members(server_id)`; **no window filter** — every human server member is a candidate. If `not page.complete`, write a WARNING line ("member list incomplete — a truncated fetch is caught by re-running; zero members almost always means the bot's Server Members Intent is off in the Discord developer portal") and continue with who we saw.
4. `stats = _send_join_welcomes(page.members)`; print the SUCCESS summary:
   `Join-welcome sweep: {welcomed} welcomed, {skipped_linked} skipped (already linked), {skipped_ledgered} skipped (already welcomed), {undeliverable} undeliverable (DMs closed — marked welcomed, never retried).`

Never added to `core/scheduled_jobs.py` — run by hand (Render one-off job / local shell) when Jo wants the backfill.

### DM copy (verbatim)

Same voice as the halfway-there nudge (`discord_sync.py:222-227`) — short, plain, one emoji, ends on reassurance:

```
Welcome to Past Lives! 👋 Your member portal account is how you sign up for classes, join guilds, and vote on funding — and it takes about a minute to set up.

Get started here: {link_url}

That link walks you through creating your account and connecting your Discord (or type /link in the server anytime). Once you're linked, swing by #choose-your-guild to pick your guilds. No rush — everything will still be here when you're ready.
```

`{link_url}` = `_absolute_url(reverse("hub_discord_link_start"))` — the same one-click flow the slash commands send unlinked users to; it handles both "no account yet" (NEEDS_LOGIN landing → sign in/up, then link) and "account exists, just link".

## 6. UI / UX — site settings Discord tab (the one screen touched)

- **Screen / partial:** `templates/hub/admin/site_settings.html`, Discord tab (`x-show="tab === 'discord'"`, `:547`).
- **Layout & container:** one new `hub-card` titled **"New-joiner welcome DM"**, placed directly after the "Guild-role sync connection" card (`:563-574`) since it rides that connection. Card header = `<h2>` title + muted `<p>` hint, cloned from the sibling cards: *"When someone new joins the Discord server, Fog Bot DMs them once with a link to create their portal account and connect Discord. Rides the guild-role sync connection above — each person is only ever messaged once."*
- **Components used:** `{% include "components/form_field.html" with field=form.discord_joiner_nudge_enabled field_hint="Each joiner gets exactly one DM, ever — turning this off and on again never re-sends. Also gates the manual backfill sweep." %}`. BooleanField → `form_field.html` auto-renders the `pl-toggle` (FRONTEND.md rule 3) — no raw checkbox, no custom HTML.
- **Save:** the field joins the page's single main settings `<form>`; the existing **"Save settings"** submit button at the bottom of the form (`site_settings.html:710`) covers it. On save: existing Django-messages success + redirect, no new endpoint.
- **Form wiring:** add `"discord_joiner_nudge_enabled"` to `SiteSettingsForm.Meta.fields` (`hub/forms.py:590-625`, next to the discord ids at `:601-603`). No `clean_*` needed — a bare boolean.
- **CRITICAL template gotcha:** add `field.name != 'discord_joiner_nudge_enabled'` to the General-tab exclusion `{% if %}` at `site_settings.html:159`, or the toggle renders twice (once on General, once on Discord). The existing spec `it_renders_each_field_only_once_not_doubled_onto_general` (`tests/hub/site_settings_discord_spec.py:241`) is the regression net — extend it.
- **States:** no new empty/loading/error states — a single always-present toggle with a stored value; form errors (none expected for a boolean) render via `form_field.html`'s error slot like every sibling field.
- **Dark + light:** `form_field.html` + the `pl-toggle` component are theme-token driven; the card copies the sibling cards' `var(--hub-text)` / `var(--hub-text-muted)` markup. Verify both themes.
- **Mobile:** one toggle row inside an existing single-column card stack — already reflows, nothing new.
- **Admin-facing feedback (observability):**
  - *Cron:* `sync_discord_guild_roles` stdout gains the count — `"… {stats.nudged} unlinked reactor(s) nudged, {stats.welcomed} new joiner(s) welcomed."`, with the suffix `" (member fetch incomplete — check Server Members Intent)"` whenever `welcome_fetch_complete` is False. Visible in Render cron logs; also a `logger.warning`.
  - *Sweep command:* prints the four counts (welcomed / skipped-linked / skipped-ledgered / undeliverable) per §5.
- No list editor, no destructive action, no email → the Add/Delete and confirm-modal rubrics don't apply.

## 7. Notifications / emails / activity

None. The DM is a direct bot send (like the existing nudge), not a spine broadcast — no `emit()`, no `period`, no `SiteActivity`, no email templates.

## 8. Build order (one phase — this is small)

1. Everything in §§3-6 lands as **one PR**: models + migrations, members reader, sync step + stats, command, form + template, full test battery. Ships green (suite + ruff + mypy).
2. Bump `plfog/version.py` VERSION; changelog: fold into / add one member-facing entry — *"New to our Discord? Fog Bot now sends first-time joiners a quick DM with everything you need to set up your member account and link Discord."*
3. Go-live ops (with the deploy): enable the bot's **Server Members Intent** in the Discord developer portal; optionally run `nudge_unlinked_discord_members` once (Render one-off job) to backfill existing unlinked members.

> Approved for build under fog-quick-feature.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*` only (**no `context_*` — it is silently not collected**), factory-boy, respx, 100% coverage.

- **`tests/core/events/discord_members_spec.py`** (mirror `fetch_reactors`' spec): single page complete; multi-page `after` pagination; 429 honored then success; 429 exhausted → incomplete with partials; non-2xx / network error → incomplete; blank token/server id → incomplete empty; `user.bot` and `joined_at` parsed (missing/garbled `joined_at` → `None`).
- **`tests/membership/discord_sync_spec.py`** — new `describe_reconcile_welcomes_new_joiners`, reusing `sync_config` + `_mock_dm` and mocking `GET /guilds/srv/members`. Mirror the existing nudge battery:
  - welcomes an unlinked human who joined 1 h ago — DM body contains the link URL, `/link`, and `#choose-your-guild`; ledger row created; `stats.welcomed == 1`
  - never twice — existing `DiscordJoinWelcome` row → no DM route call
  - `it_never_rewelcomes_a_rejoiner` — ledger row exists AND `joined_at` is fresh (inside the 48 h window, as after a leave-and-rejoin) → still no DM
  - skips linked members, skips bots, skips `joined_at` older than 48 h and `joined_at=None`
  - 403 DM → no raise, ledger row still written (never retried), logged
  - 500 DM → raises, **no** ledger row (retried next tick)
  - toggle off → members endpoint never called, `welcomed == 0`
  - incomplete member fetch → still welcomes who was seen + warning logged + `welcome_fetch_complete is False`
  - reconcile without full config → whole thing no-ops (`ran=False`, welcome step never runs)
  - the existing `sync_discord_guild_roles` command spec gains: stdout contains `"N new joiner(s) welcomed"`, and the incomplete-fetch suffix appears when `welcome_fetch_complete` is False
- **`tests/core/management/nudge_unlinked_discord_members_spec.py`** (pattern: the existing management specs in that dir): skips when unconfigured; skips when toggle off; welcomes a member who joined **months ago** (no window); prints all four counts; warns on truncated fetch.
- **`tests/hub/site_settings_discord_spec.py`**: toggle persists via the settings form; renders on the Discord tab exactly once (extend `it_renders_each_field_only_once_not_doubled_onto_general` and `it_places_the_fields_inside_the_main_settings_form`).
- Tz gotcha: build `joined_at` fixtures with `timezone.now() - timedelta(...)` serialized to ISO8601 — never naive datetimes.

## 10. Out of scope / done criteria

**Out of scope:** welcome-channel mentions (locked: DM only), a gateway bot / real-time join events (the 15-min window is the design), backfill-on-deploy (sweep is manual), i18n.

**Done when:**
- A fresh unlinked human joiner gets exactly one DM within ~15 min of joining; re-running ticks, toggling, or sweeping never re-sends.
- Linked members, bots, and anyone who joined > 48 h ago are untouched by the cron; the sweep reaches everyone unlinked+unledgered regardless of join date.
- The toggle on the Discord tab (default on) kills both cron step and sweep; it renders once, as a `pl-toggle`, saved by the existing Save settings button, correct in both themes.
- Cron stdout shows the welcomed count; the sweep prints welcomed / skipped-linked / skipped-ledgered / undeliverable.
- Suite green at 100% coverage, ruff + mypy clean; migrations reversible.
