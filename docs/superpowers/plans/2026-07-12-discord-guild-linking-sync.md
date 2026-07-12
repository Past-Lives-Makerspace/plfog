# Discord ↔ App Guild-Membership Linking & Two-Way Sync — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-12
**Surface:** FOG hub `pastlives.test` — a new anon-allowed link landing (`/discord/link/`), the home "Get started" onboarding card, the profile + notifications settings tabs, and the admin Site Settings → Discord tab. Plus one new cron task and two new outbound/inbound Discord service modules.
**Related:** builds directly on the shipped Discord DM linking (`core/events/discord_oauth.py`, `core/events/discord_dm.py`, `hub/discord_views.py`, v0.19.25). See also `reference_notification_activation_gate`, `reference_emit_period_required`.

---

## 1. Summary

Today only **1 of 610 members** have connected Discord, and guild membership lives in two disconnected places: the app's "My Guilds" and the reaction-role message in Discord. This feature makes connecting Discord **effortless** (a member clicks one link — if their Discord email is verified and matches their Past Lives account, they're linked and their guilds are set up instantly, no FOG login required) and then keeps guild membership **mirrored in both directions forever**: reacting to a guild emoji on Discord joins that guild in the app, and joining/leaving a guild in the app adds/removes the matching Discord role. Removals mirror too — un-react and you leave; leave in-app and the role comes off. The whole thing is designed so the two directions never fight each other.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Sync direction | **Two-way.** Discord → app AND app → Discord. |
| Removal policy | **Add AND remove** — a full mirror in both directions. |
| Emoji → guild map | Ignore unmapped emojis (🎲 Gamers, 🥳 → skipped); collapse duplicates (✍️ Glass-Stained + 🔥 Glass-Torchworkers → one "Glass Guild"; 🎨 Gallery + Visual-Arts → one "Visual Arts/Gallery Guild"); everything else 1:1. Many emojis may map to one guild. |
| Auto-link | Auto-link when the Discord email is `verified:true` **and** matches exactly one **verified** app account; otherwise a friendly "log in to confirm" fallback. |
| Provenance | New `GuildMembership.source` (`"app"` default \| `"discord"`) is the anti-oscillation key. |

---

## 2. What already exists (reuse, don't reinvent)

This is 80% assembly. The Discord OAuth link, the bot-token REST idiom, the join/leave logic, the verified-email→member lookup, the cron dispatcher, and the onboarding checklist all already exist.

| Need | Existing thing | Location |
|---|---|---|
| Bot-token REST calls (best-effort, blank token disables, never raises) | `bot_token()`, `_auth_headers()`, `httpx` + log-and-return-falsy idiom | `core/events/discord_dm.py:38-127` |
| OAuth link (authorize → exchange → identity → store) | `authorize_url` / `exchange_code` / `fetch_identity` / `link_member_from_code`; `DiscordIdentity` NamedTuple; `is_configured()` | `core/events/discord_oauth.py:32,76,94,129,163` |
| `identify` scope to extend to `identify email` | `_SCOPE = "identify"` | `core/events/discord_oauth.py:53` |
| Interactive link views + CSRF `state` handshake | `discord_connect` / `discord_callback` / `discord_disconnect`; `_STATE_SESSION_KEY` | `hub/discord_views.py:38,54,80` + `hub/urls.py:210-212` |
| Record/clear a link on the member | `Member.link_discord(id, handle)` / `unlink_discord()`; `discord_user_id`, `discord_is_linked` | `membership/models.py:635-660,494` |
| Join a guild **exactly like a click** (idempotent + side-effect) | `GuildMembership.objects.get_or_create(guild=, member=)`; on `created` → `orientations.member_joined_guild(guild, member)`; leave = `.filter().delete()` | `hub/views.py:1637-1639,1653,1677-1688` |
| The join side-effect (activity + lead notice + welcome email, one `emit`) | `orientations.member_joined_guild` | `membership/orientations.py:384-420` |
| Verified-email → Member (ACCOUNT_UNIQUE_EMAIL ⇒ no collision) | `_member_for_email` (lift into a shared selector) | `classes/views.py:425-436`; `plfog/settings.py:410` |
| Cron dispatcher (add a task to the always-run tuple, no `render.yaml` change) | `run_scheduled_tasks` always-run tuple (15-min tick) | `core/management/commands/run_scheduled_tasks.py:36-46` |
| Cron command shape (iterate + per-row try/except + counts) | `publish_due_events` | `core/management/commands/publish_due_events.py:22-36` |
| Discord env config (blank = disabled) | `DISCORD_BOT_TOKEN`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` | `plfog/settings.py:339-341` |
| Singleton config with `.load()` + existing Discord webhook fields | `SiteConfiguration` | `core/models.py:102,187-208,294` |
| Buried "Connect Discord" CTA to reuse/promote | `.pl-discord-link` block in the Notifications tab | `templates/hub/_notifications_settings.html:9-30` |
| Admin Site Settings **discord** tab + list-editor pattern to copy | `admin_site_settings` tabbed view; Slideshow Slides delegated-clone list editor | `hub/views.py:4420-4433`; `templates/hub/admin/site_settings.html` (Slideshow editor) |
| Onboarding checklist (add a step) + the `discord_handle`-OR-`discord_is_linked` loophole to close | `Member.onboarding` (3 steps), `profile_completeness` line 525 | `membership/models.py:578-623,510-537` |

### Genuine gaps to close (the scout confirmed none of these exist)

- `SiteConfiguration.discord_server_id`, `discord_role_message_channel_id`, `discord_role_message_id` (the REST paths need these ids).
- `Guild.discord_role_id` — the **canonical** outbound role per guild (blank = outbound disabled for that guild).
- `GuildMembership.source` (`"app"`\|`"discord"`) — provenance, the anti-oscillation key. **The only schema change to `GuildMembership`, which today holds only guild/member/joined_at.**
- `DiscordGuildEmoji` — a small admin-editable emoji→guild map model (seeded via data migration; edited on a Site-Settings sub-page).
- A **role helper** (`assign_role` / `remove_role`) mirroring `discord_dm.py`.
- A **reactions reader** (paged, with an explicit "fetched completely" flag for the removal guardrail).
- The **no-login link flow** (`resolve_member_from_code` + anon-allowed views).
- **Import-on-link** + the ongoing **reconcile cron**.

---

## 3. Where the code lives

```
core/
  events/
    discord_oauth.py        # EXTEND: DiscordIdentity += email/email_verified; fetch_identity reads them;
                            #         _SCOPE → "identify email"; NEW resolve_member_from_code()
    discord_roles.py        # NEW: assign_role() / remove_role() (bot-token REST, mirrors discord_dm.py) +
                            #      on_membership_changed(guild, member, joined) outbound entry point
    discord_reactions.py    # NEW: fetch_reactors(channel, message, emoji) → paged, with a `complete` flag
    registry.py             # EXTEND: register the discord_guilds_imported EventType (member/SELF resolver,
                            #         EMAIL-only) so emit() doesn't KeyError (§7); copy seeded at go-live
  models.py                 # EXTEND SiteConfiguration: 3 id fields
  management/commands/
    sync_discord_guild_roles.py   # NEW: cron command → membership.discord_sync.reconcile_reactions()
    run_scheduled_tasks.py        # EXTEND: add "sync_discord_guild_roles" to the always-run tuple

membership/
  models.py                 # EXTEND: GuildMembership.source (+ Source) + GuildMembershipManager
                            #         (record_app_join/record_discord_join, §4.5); Guild.discord_role_id;
                            #         NEW DiscordGuildEmoji model + manager .mapping(); Member onboarding step
  selectors.py              # NEW: member_for_verified_email(email) (lifted from classes/views:425-436)
  discord_sync.py           # NEW service: link_and_import(), import_member_guilds(), reconcile_reactions()
  emails.py / orientations  # (reuse emit_with_email_shell for the single import confirmation)
  migrations/00XX_*.py      # additive schema + data migration seeding the 15-guild emoji map

classes/views.py            # EXTEND: import member_for_verified_email from selectors (behavior unchanged)

hub/
  discord_views.py          # EXTEND: anon-allowed discord_link_start / discord_link_callback;
                            #         existing connect/callback now also run import
  views.py                  # EXTEND: guild_join/leave/membership_set call discord_roles.on_membership_changed;
                            #         admin_site_settings handles the emoji-map + role-id + config formsets
  forms.py                  # NEW: DiscordGuildEmojiFormSet, GuildRoleForm(Set), Discord-config fields on SiteSettingsForm
  urls.py                   # NEW: /discord/link/ + /discord/link/callback/

templates/hub/
  discord_link_landing.html            # NEW: anon-friendly outcome page (public base, style.css)
  partials/_discord_connect_cta.html   # NEW: extracted .pl-discord-link block (reused on profile + notifications)
  admin/site_settings.html             # EXTEND: the "discord" tab (config ids + emoji map + role-id table)
membership/emails/
  discord_guilds_imported.{html,txt}   # NEW: the single "we set up your N guilds" confirmation
```

Everything stays inside the current coverage/mypy scope (`core`, `membership`, `hub`), mirroring the existing Discord DM feature's layering.

---

## 4. Data model

### 4.1 `GuildMembership.source` (the anti-oscillation key)

```python
class GuildMembership(models.Model):
    class Source(models.TextChoices):
        APP = "app", "In-app join"
        DISCORD = "discord", "Discord reaction"

    # existing: guild, member, joined_at
    source = models.CharField(
        max_length=20, choices=Source.choices, default=Source.APP,
        help_text="How this membership was created: an in-app join (app) or a Discord-reaction mirror "
                  "(discord). Inbound reaction sync only ever adds/removes 'discord' rows and never touches "
                  "'app' rows — this is what keeps the two directions from fighting.",
    )
```

**Migration:** additive, one field, `default="app"`. Existing rows correctly become `"app"` (they were all in-app joins). **Reverse:** drop the column (real reverse, not `noop`).

### 4.2 `Guild.discord_role_id`

| Field | Type | Note |
|---|---|---|
| `discord_role_id` | `CharField(max_length=32, blank=True, default="")` | Canonical Discord role assigned/removed on in-app join/leave (outbound). **Blank disables outbound role sync for that guild.** For the collapsed guilds (Glass has two Discord roles), the admin picks **one** canonical role for outbound; inbound still accepts *either* Glass emoji. Document this asymmetry in `help_text`. |

### 4.3 `SiteConfiguration` — three ids (disabled-when-blank)

| Field | Type | Example | Note |
|---|---|---|---|
| `discord_server_id` | `CharField(max_length=32, blank=True, default="")` | `933589656996565023` | Server/guild id for the role REST path. |
| `discord_role_message_channel_id` | `CharField(max_length=32, blank=True, default="")` | `1121958041424773131` | Channel of the reaction-role message. |
| `discord_role_message_id` | `CharField(max_length=32, blank=True, default="")` | `1301194610277748886` | The reaction-role message. Editable so admins can update if it's reposted. |

Any blank → the whole sync no-ops (same "disabled when unconfigured" idiom as `discord_oauth.is_configured()` and `bot_token()`).

### 4.4 `DiscordGuildEmoji` (admin-editable emoji → guild map)

```python
class DiscordGuildEmojiManager(models.Manager):
    def mapping(self) -> dict[str, Guild]:
        """{emoji: guild} for every configured row, one query (select_related guild)."""

class DiscordGuildEmoji(models.Model):
    emoji = models.CharField(
        max_length=64, unique=True,
        help_text="The reaction emoji on the Discord role message: a unicode character (e.g. 🔥) or a "
                  "custom emoji as name:id (e.g. PrisonOutreach:123456789). A member who reacts with this "
                  "joins the guild below. Many emojis may point at one guild (collapsed guilds).",
    )
    guild = models.ForeignKey(
        Guild, on_delete=models.CASCADE, related_name="discord_emojis",
        help_text="The guild a reaction with this emoji joins the member to.",
    )

    class Meta:
        constraints = [models.UniqueConstraint(fields=["emoji"], name="uq_discordguildemoji_emoji")]
        ordering = ["guild__name", "emoji"]

    def __str__(self) -> str:
        return f"{self.emoji} → {self.guild.name}"
```

- **Unmapped emojis** (🎲, 🥳) simply have no row → skipped, both directions.
- **Collapse** = two rows, different emoji, same `guild` FK.
- **Data migration** seeds the ~18-emoji → 15-guild mapping. Reverse: delete the seeded rows.

### 4.5 `GuildMembership.objects` — the join/leave rule (source upgrade)

**The anti-oscillation key is not just the `source` column — it's that every writer sets it through a manager, never a bare `get_or_create`.** Because `unique(guild, member)` (`uq_guildmembership_guild_member`, verified in `membership/models.py`) means there is exactly **one** row per pair, the *ordering* of a reaction and an in-app join matters:

> **The data-loss case this closes:** member reacts 🔥 *first* → reconcile creates a `source="discord"` row. Later they click **Join Glass** in-app. A plain `get_or_create(guild, member)` finds that existing row (`created=False`) and leaves it at `source="discord"`. Now they un-react on Discord → the next complete reconcile deletes every `source="discord"` Glass row → **the member is dropped from a guild they explicitly joined in-app.** An explicit in-app join is the strongest signal and must be immune to inbound removal.

So all writers go through a manager:

```python
class GuildMembershipManager(models.Manager):
    def record_app_join(self, guild, member) -> tuple["GuildMembership", bool, bool]:
        """In-app join. Create as source=app; UPGRADE an existing source=discord row to
        source=app (an explicit join outranks a standing reaction). Returns
        (membership, created, upgraded) — ``upgraded`` is True when an existing
        discord-sourced row was promoted, so the caller can fire the join side-effect."""
        membership, created = self.get_or_create(
            guild=guild, member=member, defaults={"source": self.model.Source.APP}
        )
        upgraded = False
        if not created and membership.source != self.model.Source.APP:
            membership.source = self.model.Source.APP
            membership.save(update_fields=["source"])
            upgraded = True
        return membership, created, upgraded

    def record_discord_join(self, guild, member) -> tuple["GuildMembership", bool]:
        """Inbound reaction mirror. Create as source=discord; NEVER downgrade an existing
        source=app row (get_or_create with defaults leaves it untouched)."""
        return self.get_or_create(
            guild=guild, member=member, defaults={"source": self.model.Source.DISCORD}
        )
```

- **`record_app_join`** — the three in-app join views **and** `import_member_guilds`' "push my app guilds" step call this. Upgrade-to-app is the anti-data-loss key; `upgraded` lets the view fire `member_joined_guild` for a member who was only a reactor before (the guild lead never got a "joined" notice for the silent reaction — now they do, on the real join).
- **`record_discord_join`** — reconcile + import's reaction mirror call this. `get_or_create` with `defaults` never touches an existing `source="app"` row, so inbound can't downgrade an explicit join.
- Inbound removal stays `filter(guild=…, source=DISCORD).exclude(...).delete()` — structurally it can only ever delete discord-sourced rows.

---

## 5. Business logic (fat models / services — views stay thin)

### 5.1 OAuth extensions (`core/events/discord_oauth.py`)

- `_SCOPE = "identify email"` (was `"identify"`) — Discord returns `email` + `verified` from `/users/@me`.
- `DiscordIdentity` gains `email: str` and `email_verified: bool`; `fetch_identity` reads `payload.get("email","")` / `payload.get("verified", False)`.
- **New** `resolve_member_from_code(code, redirect_uri) -> tuple[Member | None, DiscordIdentity]` — exchanges the code, fetches identity; if `email_verified` and email matches exactly one verified account, returns that member; else `(None, identity)`. Raises `DiscordOAuthError` on OAuth failure (the view shows one friendly message). The existing `link_member_from_code` (logged-in path) stays.

### 5.2 Shared selector (`membership/selectors.py`)

`member_for_verified_email(email) -> Member | None` — lifted verbatim from `classes/views._member_for_email:425-436` (`user__emailaddress__email__iexact`, `verified=True`, `.distinct().first()`). `classes/views` imports it from here (behavior unchanged). `ACCOUNT_UNIQUE_EMAIL=True` guarantees at most one match.

### 5.3 Outbound role helper (`core/events/discord_roles.py`) — mirrors `discord_dm.py`

- `assign_role(server_id, user_id, role_id) -> bool` → `PUT /guilds/{server_id}/members/{user_id}/roles/{role_id}`.
- `remove_role(server_id, user_id, role_id) -> bool` → `DELETE …`.
- Both: blank token / blank id → no-op `False`; `httpx` best-effort; **never raise**; log-and-return-falsy. Treat **404** (member left the server) and **403** (missing Manage-Roles / role above ours) as benign, logged — not errors.
- `on_membership_changed(guild, member, *, joined: bool) -> None` — the single entry point the views call. No-ops unless `member.discord_is_linked`, `guild.discord_role_id`, and `config.discord_server_id` are all set; then `assign_role`/`remove_role`. **Only ever called from the in-app join/leave views** — this is what makes outbound disjoint from inbound.

### 5.4 Reactions reader (`core/events/discord_reactions.py`) — the removal guardrail lives here

```python
@dataclass(frozen=True)
class ReactorPage:
    user_ids: set[str]   # discord snowflakes that reacted with this emoji
    complete: bool       # True ONLY if every page fetched with a 2xx and pagination ran to the end
```

- `fetch_reactors(channel_id, message_id, emoji) -> ReactorPage` — pages `GET /channels/{ch}/messages/{msg}/reactions/{emoji}?limit=100&after=…` (emoji is the unicode char, URL-encoded, or `name:id` for custom) until a page returns `<100`.
- **On any non-2xx / network error / 429 rate-limit mid-paging: stop, return `complete=False`** with whatever was gathered. Adds from a partial page are still safe (everyone we saw genuinely reacted); **removals are gated on `complete=True`** (a missing id on an incomplete fetch could mean "un-reacted" *or* "we never fetched that page"). 429s honor `Retry-After` up to a small cap, then bail as incomplete — the next 15-min tick retries. This is the destructive-removal guardrail; it logs loudly.

### 5.5 Inbound reconcile + import (`membership/discord_sync.py`)

**`import_member_guilds(member) -> ImportResult`** (carrot, runs right after a link). `ImportResult` = `(guilds: list[Guild], complete: bool)`:
1. For each `(emoji, guild)` in `DiscordGuildEmoji.mapping()`, `fetch_reactors(...)` and check whether `member.discord_user_id` is in the result. **Track completeness:** if *any* emoji fetch returns `complete=False` (429/partial — §5.4), set `ImportResult.complete=False`. A partial fetch can only ever *under*-count (everyone we saw genuinely reacted), never over-count — safe to add, but the member may have reacted on a page we never fetched.
2. For every guild the member reacted to → `GuildMembership.objects.record_discord_join(g, member)` (§4.5) — **without** calling `member_joined_guild` (no per-join welcome-email storm). An existing `source="app"` row is left as `"app"` and no welcome fires.
3. For the member's **pre-existing `source="app"` memberships** → `discord_roles.on_membership_changed(g, member, joined=True)` (push their app guilds onto Discord as roles).
4. Return the deduped guild list **+ the `complete` flag**. When `complete=False`, the landing and the confirmation email add "a few more may appear within 15 minutes" — the cron backfills the un-fetched pages — so the low-friction promise never *silently* under-delivers. The confirmation (§7) still fires with the guilds we *did* set up (skip it entirely only when the list is empty).

**`reconcile_reactions() -> ReconcileStats`** (cron, every 15 min):
1. No-op unless `bot_token()`, `discord_server_id`, `discord_role_message_channel_id`, `discord_role_message_id` are all set.
2. Build `reacted_by_guild: dict[Guild, set[str]]` and `complete_by_guild: dict[Guild, bool]` by fetching each mapped emoji and OR-ing into its guild (a guild is `complete` only if **every** emoji mapping to it fetched completely).
3. **Add:** for each guild, `Member.objects.filter(discord_user_id__in=ids)` → `GuildMembership.objects.record_discord_join(g, member)` (§4.5 — never downgrades an existing `source="app"` row). Silent (no `member_joined_guild`; no per-reaction email/notification noise — the member already got the one confirmation at link time). A reactor with **no linked member** (`discord_user_id` unknown) simply isn't matched → ignored; inbound only ever affects members who have linked.
4. **Remove (guarded):** only if `complete_by_guild[guild]` is `True`, delete `GuildMembership.filter(guild=guild, source=DISCORD).exclude(member__discord_user_id__in=ids)`. **Inbound never touches `source="app"` rows.** On incomplete fetch: skip removals for that guild, `logger.warning(...)`, retry next tick.
5. Return counts (added / removed / skipped-guilds) for the command's stdout.

**`link_and_import(code, redirect_uri, *, member=None) -> LinkOutcome`** — orchestration the views call so they stay thin. Applies the guards **in this exact order** (each maps to one landing state in §6):
- **Resolve the target member:** `member` given (logged-in path) → that member, no email check. Else `resolve_member_from_code`: OAuth failure → `OAUTH_FAILED`; `email_verified` false, or no verified account matched (**including the pre-signup member with a verified email but no linked `User`**, whom `member_for_verified_email` can't find) → `NEEDS_LOGIN`.
- **Guard 1 — Discord already linked *elsewhere*:** if `identity.user_id` is already on a *different* member (`Member.objects.filter(discord_user_id=identity.user_id).exclude(pk=member.pk).exists()`) → `ALREADY_LINKED_ELSEWHERE`. Never steal or re-point another member's link.
- **Guard 2 — this member already has a *different* Discord:** if `member.discord_user_id` is set and ≠ `identity.user_id` → `ACCOUNT_HAS_OTHER_DISCORD`. We never *silently* replace an existing link — not on the anon path (a link click must not swap someone's connected account) and not on the logged-in path (they disconnect first from Settings). Re-linking the **same** account (`==`) falls through to `LINKED` (idempotent re-import).
- **Link + import:** `member.link_discord(...)` (idempotent; stamps `discord_linked_at` for the §7 period) → `import_member_guilds(member)` → fire the §7 confirmation only when the guild list is non-empty.
- Returns a `LinkOutcome` enum: `LINKED` (carries the guilds **+ the import `complete` flag** for the landing) | `NEEDS_LOGIN` | `ALREADY_LINKED_ELSEWHERE` | `ACCOUNT_HAS_OTHER_DISCORD` | `OAUTH_FAILED`. The view maps each to a landing state.

### 5.6 The transitions — worked, proving no oscillation

Inbound reads **reactions** and only ever writes `source="discord"` rows. Outbound reads **in-app join/leave events** and only writes Discord **roles**. The two sets are disjoint, and a role written by outbound is never re-read by inbound (inbound reads reactions, not roles). A bot cannot react *as* a user, so an app-join shows up on Discord as a **role**, never a reaction — inbound correctly ignores it.

| # | Transition | What fires | Result & why it's stable |
|---|---|---|---|
| 1 | **react-add** (Discord→app): member reacts 🔥 | Next reconcile tick sees 🔥 → `record_discord_join` creates a `source="discord"` row for Glass Guild. Outbound **not** called. | One `source="discord"` row. Next tick: still reacting → row still valid, no-op. |
| 2 | **un-react-remove** (Discord→app): member removes 🔥 (and holds no ✍️ either) | Reconcile, on a **complete** fetch, finds them absent from all Glass emojis → deletes the `source="discord"` Glass row. | Gone. If they *also* joined Glass in-app, that row is `source="app"` → inbound never deletes it, they stay in Glass. Correct. |
| 3 | **app-join, fresh** (app→Discord): member clicks Join Glass, no prior reaction | View `record_app_join` → `created=True` → `member_joined_guild` side-effect **+** `on_membership_changed(joined=True)` assigns the canonical Glass **role**. | They have the Discord role, not a reaction. Next reconcile reads *reactions*, sees no Glass reaction, but the row is `source="app"` → inbound skips it. **No oscillation.** |
| 3b | **react-then-app-join** (the ordering that used to lose data): member reacted 🔥 earlier (a `source="discord"` row exists), now clicks Join Glass in-app | View `record_app_join` → `created=False, upgraded=True` → row **promoted to `source="app"`**; `member_joined_guild` fires (the lead finally hears about the join the silent reaction never surfaced) **+** role assigned. | The explicit join now **outranks the standing reaction**. If they later un-react, inbound only deletes `source="discord"` rows → the promoted `source="app"` row survives → **they stay in Glass.** This is the data-loss case §4.5 closes. |
| 4 | **app-leave** (app→Discord): member clicks Leave Glass | View deletes the row (any source) **+** `on_membership_changed(joined=False)` removes the role. | Role gone, no row. Reconcile: no reaction, no row → no-op. (Edge: if they *still* hold the 🔥 reaction, reconcile re-creates a `source="discord"` row next tick — correct: a standing reaction is a live "I'm in Glass" signal. Noted in §10.) |

### 5.7 Thin view wiring

`guild_join` / `guild_leave` / `guild_membership_set` (`hub/views.py:1627-1689`) change minimally, **inside their existing `if member is not None:` guard** — never call the outbound helper with `member=None`, it dereferences `member.discord_is_linked`:

- **Join** (`guild_join`, and the `"joined" in POST` branch of `guild_membership_set`): swap the bare `GuildMembership.objects.get_or_create(guild, member)` for `membership, created, upgraded = GuildMembership.objects.record_app_join(guild, member)` (§4.5). Fire `orientations.member_joined_guild(guild, member)` when **`created or upgraded`** (a promoted reactor is a first-time real join, so the lead should hear about it; a fresh join behaves exactly as today). Then one line: `discord_roles.on_membership_changed(guild, member, joined=True)`.
- **Leave** (`guild_leave`, and the else branch): the existing `filter(...).delete()` is unchanged, then `discord_roles.on_membership_changed(guild, member, joined=False)`.

`on_membership_changed` is **best-effort and must never raise** (§5.3): a Discord outage, a 403/404, or even a config-load hiccup can never fail the in-app join/leave the member just performed — it runs *after* the DB write, and every path inside it logs-and-returns. No other logic moves into the view.

### 5.8 Security — why verified-email auto-link is airtight

Auto-link with **no FOG login** fires *only* when Discord returns `verified:true` **and** the email matches exactly one verified Past Lives account. That one guardrail is safe because **Discord's own `verified:true` already required inbox control**, and inbox control is exactly what our existing email-code login grants — so the anon link is *no weaker* than a login we already ship.

| Attack | Why it fails |
|---|---|
| Attacker points a Discord account's email at `victim@…` and links | To reach `verified:true`, Discord makes them click a verification link **sent to `victim@…`**. Anyone who can read that inbox can already `login-by-code` as the victim — auto-link adds no new exposure. |
| Unverified Discord email that happens to match | `email_verified` is `false` → `NEEDS_LOGIN`. An unverified email proves nothing and never auto-links. |
| Victim later changed their Past Lives email | Matching is against the member's **current** verified `EmailAddress` rows (`member_for_verified_email`); a stale Discord email no longer matches → `NEEDS_LOGIN`. |
| The Discord account is already tied to member B; attacker's email matches member A | Guard 1 (`ALREADY_LINKED_ELSEWHERE`) refuses to move an existing link, and Guard 2 (`ACCOUNT_HAS_OTHER_DISCORD`) refuses to overwrite A's link. |

**The net:** the anon flow can only ever *create* a link on an account whose email the requester provably controls; it can never *move* or *replace* one. Anything stronger — switching Discord accounts, resolving an "already linked elsewhere" — requires an authenticated session. `ACCOUNT_UNIQUE_EMAIL=True` (`plfog/settings.py:410`, verified) guarantees the "exactly one verified account" half.

---

## 6. UI / UX  ← completeness checklist applied per screen

### Screen A — Low-friction link landing (`/discord/link/` + `/discord/link/callback/`)  ★ centerpiece

- **Template:** `templates/hub/discord_link_landing.html`, rendered on the **public base** (`templates/base.html`, `style.css`) so it works for anonymous visitors (this is the link posted *in Discord*). Views are **NOT** `@login_required` (session `state` works for anon; confirmed no global login middleware — login/signup are anon).
- **Layout & container:** a single centered card (mirrors the login/signup card), one headline + one primary button per state. New `.pl-discord-landing` classes in `style.css`.
- **Flow:** `/discord/link/` stores a **`state` under its own session key `discord_link_state`** (distinct from the logged-in `/settings/discord/` flow's `discord_oauth_state`, so two in-flight flows never collide) and redirects to Discord's authorize page (`identify email`). The `redirect_uri` is `request.build_absolute_uri(reverse("hub_discord_link_callback"))` — a **second** Discord redirect URI that must be registered on the app (§10), separate from the existing `/settings/discord/callback/`. `/discord/link/callback/` verifies `state`, calls `link_and_import(...)` (with `member=_get_member(request)` when logged in, else the email-match path), and renders the outcome.
- **States (each is a real screen, not a redirect to a 500):**
  - **Success — `LINKED` with guilds:** "Discord connected — we set you up in N guilds." Lists the guild names (each linking to its guild page). Primary CTA: logged-in → redirect into **My Guilds** (`?tab=guilds`) with a success toast/message; anon → "Log in to Past Lives" button. Secondary: "See all guilds." **When the import came back `complete=False`** (a link-time rate-limit truncated the read — §5.5): append a calm line "A few more may still be syncing — check back in about 15 minutes." Never present a possibly-undercounted N as final.
  - **Success — `LINKED`, no guilds:** "Discord connected." (No import email; §7 only fires when the guild list is non-empty.) CTA: log in / go to guilds.
  - **`NEEDS_LOGIN`** (unverified Discord email, or no verified account match — e.g. a pre-signup member with no linked `User`): friendly "Almost there — **log in or sign up** to Past Lives to finish connecting your Discord," with a **Log in / Sign up** button carrying `?next=` back to the logged-in connect flow (a pre-signup member has no account yet, so *log in* alone would dead-end — offer sign-up too). Not an error tone.
  - **`ALREADY_LINKED_ELSEWHERE`:** "This Discord account is already connected to a different Past Lives account. Log in to manage it." (Never silently re-point the link.)
  - **`ACCOUNT_HAS_OTHER_DISCORD`:** "Your Past Lives account already has a different Discord connected. To switch, log in and disconnect the current one first," with a **Go to Discord settings** button → `?tab=notifications`. (We never swap a connected account from a link click.)
  - **`OAUTH_FAILED` / bad, missing, or expired `state`:** "We couldn't verify that Discord sign-in — please try again," with a **Try again** button back to `/discord/link/`. Cancelled/denied at Discord (`?error=access_denied`) → a calm "Connection cancelled" with the same retry. (A stale session — the member clicked from Discord's in-app browser and the `state` cookie didn't survive the round-trip — lands here too, on the friendly retry, never a 500. See §10 testing note.)
- **Feedback:** full-page (not HTMX) → messages/inline copy, no dead ends — every state has a button.
- **Dark + light:** theme tokens only; the card and buttons reuse existing auth/landing classes. Verify both themes.
- **Mobile:** single column, full-width button, 8px-grid spacing — trivially reflows.

### Screen B — Onboarding step "Connect your Discord"

- **Where:** `Member.onboarding` (`membership/models.py:578-623`) gains a step: `label="Connect your Discord"`, `hint="We'll set up your guilds instantly"`, `done=self.discord_is_linked`, `url=reverse("hub_discord_connect")`, **`optional=True`**. Keyed **specifically off `discord_is_linked`** (a typed `discord_handle` does **not** satisfy it — closing the `profile_completeness` line-525 loophole for this step).
- **Why optional (design choice — flag):** `is_onboarded` stays `profile-essentials + joined-guild`. Making Discord *required* would flip `is_onboarded` false for ~609 existing members and re-open the "Get started" card for everyone at once. Optional keeps it prominent (its own row, carrot copy) without that regression.
- **Renders on:** the existing home "Get started" card (`templates/hub/home.html`), no new markup pattern — the checklist already renders required + optional steps. Verify the card still reads correctly with 4 steps in both themes and on mobile (it already stacks).

### Screen C — Profile-tab Connect CTA

- **Template:** `templates/hub/user_settings.html` profile tab, beside the existing free-text `discord_handle` field (line 171). Today only Notifications has the Connect CTA.
- **Reuse:** extract the `.pl-discord-link` block (`_notifications_settings.html:9-30`) into `templates/hub/partials/_discord_connect_cta.html` and `{% include %}` it on both tabs. No new CSS — `.pl-discord-link` already exists and is theme-correct.
- **⚠ Duplicate-id guard (both tabs live in one DOM):** `user_settings.html` renders **every** tab pane at once and toggles them with Alpine `x-show` (`profile` at line 36, `notifications` at line 375 — verified). Including the same partial in both panes puts two copies in the DOM simultaneously, so any fixed element id would collide. The partial therefore takes an **`id_suffix`** param and scopes every id it emits (`disconnect-discord-{{ id_suffix }}`); the profile include passes `id_suffix="profile"`, the notifications include passes `id_suffix="notifications"`. Distinct ids, no collision.
- **⚠ Nested-form guard:** the profile tab's fields sit inside the profile `<form>`, and `confirm_modal.html` renders its **own** `<form method="post">`. A `<form>` inside a `<form>` orphans the submit button (the known nested-form save bug — see `reference_nested_form_save_bug`). So the confirm-modal `{% include %}` is placed **outside** the profile form (end of the pane); only the *trigger* button (`@click="$dispatch('open-confirm', 'disconnect-discord-profile')"`, `type="button"`) sits beside the field.
- **States:** linked → "Discord connected" + **Disconnect** (`hub_discord_disconnect`, routed through `components/confirm_modal.html` since disconnecting stops DMs *and* drops the two-way guild sync); unlinked → "Connect Discord — we'll set up your guilds instantly" primary button (`hub_discord_connect`, `hx-boost="false"`). Copy makes the guild-setup benefit explicit.
- **Dark + light + mobile:** inherited from the existing `.pl-discord-link` styles; verify the include renders identically on the profile tab in both themes.

### Screen D — Admin: Site Settings → **Discord** tab (config ids + emoji map + role-id table)

The `discord` tab already exists in `admin_site_settings` (`hub/views.py:4432` — verified in the allowed-tab set). Extend it with three stacked `hub-card` sections; all save via the tab's existing single form-post (`submitted_tab` hidden input) so no section blanks another. The config form (D1) and the two formsets (D2 emoji, D3 role) each take a **distinct formset prefix** (e.g. `emoji` / `guildroles`) so their management-form fields don't collide inside the one `<form>`; on any validation error the view re-renders the **bound** form + formsets so no typed value is lost.

**D1 — Discord connection (config ids):** three text fields (`discord_server_id`, `discord_role_message_channel_id`, `discord_role_message_id`) added to `SiteSettingsForm`, rendered via `components/form_field.html` with hints ("The reaction-role message id — update if it's reposted"). Plain inline form + the tab's Save. Empty is valid (disables sync) — hint says so.

**D2 — Emoji → guild map (list editor — the famous-failure rubric):**
- **Component:** a `modelformset_factory(DiscordGuildEmoji, extra=0)` — `DiscordGuildEmojiFormSet`.
- **"+ Add mapping" button:** clones a hidden `<template>` of `formset.empty_form`, swaps `__prefix__`, bumps `TOTAL_FORMS` — the plfog way, copied from the Slideshow Slides editor in `admin/site_settings.html`.
- **Per-row Delete:** a real `pl-btn pl-btn--danger pl-btn--sm` button, `margin-top:0.75rem`. Saved rows: flip the hidden `{{ form.DELETE }}` and `requestSubmit()` (preserves the rest of the tab). Cloned unsaved rows: a "Remove" button that drops the DOM node. **Never a DELETE toggle.**
- **Row fields:** `emoji` (text input, hint: "unicode char like 🔥, or name:id for a custom emoji") + `guild` (select — scoped under `.hub-form-group` so the `<select>` and its `option`s inherit `--hub-input-*` tokens, not a white box on dark; per FRONTEND rule 13).
- **Save:** the tab's primary Save; success → toast/redirect back to `?tab=discord`.
- **Empty state:** "No emoji mappings yet. Add one so reacting on Discord joins a guild." (not a blank region).
- **Error state:** duplicate `emoji` → form error rendered on the offending row ("This emoji is already mapped."); a blank `emoji` on a saved row → inline required error.

**D3 — Canonical outbound role per guild:** a compact table of active guilds (a `modelformset` over `Guild`, **no Add/Delete** — the guild set is fixed), one text input per guild for `discord_role_id`, scoped under `.hub-form-group`. Hint on the Glass/Visual-Arts rows: "Collapsed guild — pick one canonical role for outbound; inbound accepts either emoji." Blank = outbound disabled for that guild.

- **Dark + light:** every `<select>`/`<input>` lives inside `.hub-form-group`; `select option { background; color }` covered by the existing hub scope. Verify both themes.
- **Mobile:** the D3 table degrades to stacked "guild → role id" rows (or scrolls within a contained region); the D2 list is already single-column rows.

### Screen E — the existing Notifications-tab CTA

Now backed by the extracted `_discord_connect_cta.html` (Screen C), included with `id_suffix="notifications"`. Its copy gains the guild-setup benefit line, and — consistent with the shared partial — its **Disconnect** now routes through the same `confirm_modal.html` (a small, deliberate upgrade from today's bare button, since disconnect is destructive). No behavior change to `hub_discord_connect`. Because the notifications CTA already sits *outside* the notifications `<form>` (it's a sibling `div` above it — verified at `_notifications_settings.html:10-30`), the confirm-modal include has no nested-form issue here.

---

## 7. Notifications / emails / activity

**One** confirmation — the "we set up your N guilds" email, never a per-guild storm.

- **Trigger:** `import_member_guilds` returns a non-empty guild list → `emit_with_email_shell("discord_guilds_imported", …)` (same helper `member_joined_guild` uses), addressed with an explicit `email_to=member.primary_email` so it's **transactional** (sends regardless of broadcast preferences and independent of the activation gate — the member just proved control of the account via Discord OAuth).
- **⚠ Register the event key first (else `emit` raises `KeyError`).** `emit()` resolves `event_key` through `core.events.registry.get_event`, which **raises `KeyError` for an unknown key** (verified — `core/events/registry.py:645`). So `discord_guilds_imported` must be added to `_NEW_EVENTS` in `core/events/registry.py`: recipient resolver = the member themselves (a member/`SELF`-scoped resolver; because the email is `email_to`-addressed, the resolver only governs the in-app/push fan-out — keep those **off**: EMAIL-only channels, `in_app_title`/`in_app_body` left blank so there's no bell row), and its DB copy seeded via `seed_notification_templates` at go-live (§10). The email body is the rich structural shell (`discord_guilds_imported.html`, *not* copy-mode), so the black-on-dark copy-mode pitfall doesn't apply — but the event must still exist or the send throws. *(Alternative, if a spine event feels heavy for a one-shot member-addressed email: send it directly through `core/email.py` like the `classes/emails.py` transactional builders and skip the registry entirely — pick one explicitly at build.)*
- **When the import was `complete=False`** (§5.5): the email adds the same "a few more may appear within about 15 minutes" line as the landing, so its guild list is never presented as final when a rate-limit truncated the read.
- **Period (dedup):** `f"discord_import:{member.pk}:{member.discord_linked_at.isoformat()}"` — unique per link event, so retries dedupe but a genuine re-link re-sends (per `reference_emit_period_required`).
- **Email content (FRONTEND *Email Templates* rubric):**
  - Subject: "Your Past Lives guilds are set up" — noun links into **My Guilds** in the body.
  - Body lists each guild **as a link to its guild page** (`_absolute_url(reverse("hub_guild_detail", args=[g.slug]))`), one primary CTA **"Manage your guilds"** → `?tab=guilds`, and a one-liner "You'll now get these guilds' announcements — leave any anytime."
  - Branded shell (`templates/membership/emails/_base.html`), **no "BETA"**, absolute URLs, subject/body in Portland tz.
  - Both `discord_guilds_imported.html` **and** `.txt`, kept in sync.
- **Empty import** (linked, no reacted/app guilds): **no** import email — just the plain "Discord connected" landing/message. Don't send a hollow "we set up 0 guilds."
- **Reconcile (cron) adds/removes are silent** — no per-reaction email or in-app notification (avoids 610× noise). The single link-time confirmation is the only member-facing message. (An optional lightweight `SiteActivity` "joined via Discord" row is **deferred** — §10.)

---

## 8. Build order (phased; each phase ships green)

1. **Schema + provenance + config.** `GuildMembership.source` (+ `Source`) **and the `GuildMembershipManager` with `record_app_join` / `record_discord_join` (§4.5)**, `Guild.discord_role_id`, `SiteConfiguration` 3 ids, `DiscordGuildEmoji` + manager, and the data migration seeding the 15-guild map. Additive migrations with real reverses. (No behavior yet — pure model layer.)
2. **Shared email resolver + email scope + no-login link flow.** Lift `member_for_verified_email` into `membership/selectors.py` (repoint `classes/views`); extend `DiscordIdentity`/`fetch_identity`/`_SCOPE`; add `resolve_member_from_code`; the anon `/discord/link/` + callback views + `discord_link_landing.html` (link-only, import stubbed).
3. **Import-on-link.** `import_member_guilds` (returns guilds + `complete`) + `link_and_import` (the ordered guards, incl. `ACCOUNT_HAS_OTHER_DISCORD`) + the §7 confirmation email — **register the `discord_guilds_imported` event in `core/events/registry.py` and seed its copy** so `emit` doesn't `KeyError`; wire both the logged-in and anon callbacks through `link_and_import`; suppress the welcome storm.
4. **Outbound role helper + hook.** `discord_roles.py` (`assign_role`/`remove_role`/`on_membership_changed`); route the three in-app joins through `record_app_join` (fire `member_joined_guild` on `created or upgraded`) and add the one-line `on_membership_changed` hook to each join/leave view, inside the existing member guard.
5. **Inbound cron + guardrails.** `discord_reactions.py` (paged, `complete` flag), `reconcile_reactions`, the `sync_discord_guild_roles` command, and its entry in the `run_scheduled_tasks` always-run tuple.
6. **Admin emoji-map editor + onboarding/profile CTAs.** Site Settings Discord tab (D1/D2/D3), the extracted `_discord_connect_cta.html` include on profile + notifications, the optional onboarding step.
7. **Version + changelog** (at BUILD, last): bump `plfog/version.py` (`0.21.13` → next patch on the release line) and a **new** member-facing CHANGELOG entry (net-new feature) — e.g. *"Connect Discord in one click — we set up your guilds instantly, and your guild membership now stays in sync both ways."*

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py` under each app's `spec/`, `describe_*`/`it_*`, factory-boy, `respx` for the Discord HTTP calls, run in the `plfog-web` Docker image, ≥98% coverage.

- **Email-match link (`resolve_member_from_code` / `link_and_import`):** verified email + one match → `LINKED`; `verified:false` → `NEEDS_LOGIN`; no matching verified account → `NEEDS_LOGIN`; `discord_user_id` already on another member → `ALREADY_LINKED_ELSEWHERE`; target member already has a **different** `discord_user_id` → `ACCOUNT_HAS_OTHER_DISCORD`; re-linking the **same** `discord_user_id` → `LINKED` (idempotent, not blocked); OAuth failure → `OAUTH_FAILED`. Logged-in path links to `request.user`'s member without the email match (and still honours both guards).
- **Import-on-link:** reacted guilds create `source="discord"` rows (via `record_discord_join`) and **no** `member_joined_guild`/welcome email fires; pre-existing `source="app"` guilds get an outbound `assign_role`; exactly **one** `discord_guilds_imported` email with all guilds; empty import → no email; a link-time `complete=False` fetch (`respx` 429 mid-page) → `ImportResult.complete` is `False` and the landing/email carry the "still syncing" note (never an over-reported final count).
- **Inbound reconcile — add:** a reactor resolves to a member → `record_discord_join` creates a `source="discord"` row; unmapped emoji (🎲/🥳) → no row; unresolvable reactor (no linked member) → ignored.
- **Inbound reconcile — remove (guarded):** un-reacted `source="discord"` row deleted on a **complete** fetch; a `source="app"` row for the same guild is **never** deleted; on `complete=False` (simulate 429/500 mid-page via `respx`) **no** removals happen and it logs — the row survives.
- **Outbound:** join fires `assign_role`; leave fires `remove_role`; blank `discord_role_id` or unlinked member or blank `discord_server_id` → no call; 404/403 responses don't raise.
- **No-oscillation worked cases (§5.6):** after an app-join (`source="app"` + role assigned), a reconcile tick that sees no reaction leaves the row intact (does not delete an `app` row); after a react-add then reconcile, a second reconcile is a no-op.
- **Source-upgrade / data-loss guard (§4.5, case 3b):** a `source="discord"` row + a later in-app join via `record_app_join` → row promoted to `source="app"`, `upgraded=True`, `member_joined_guild` fires once; a following **complete** reconcile where the member no longer reacts does **not** delete the promoted `app` row (they stay in the guild). `record_discord_join` never downgrades an existing `app` row.
- **Outbound never blocks the join:** `on_membership_changed` raising nothing even when `SiteConfiguration.load()` / the Discord call fails — the `get_or_create`/`delete` still commits and the view returns success (assert the membership row state regardless of a simulated Discord 500).
- **Emoji map:** `DiscordGuildEmoji.mapping()` returns `{emoji: guild}`; two emojis → one guild both add to that guild; duplicate-emoji save raises the unique error.
- **Admin editor (view/template):** the Discord tab saves config ids + emoji rows + role ids together; add/delete a mapping row preserves the rest; empty state copy present.
- **Onboarding:** the Discord step is `done` only when `discord_is_linked` (a typed `discord_handle` alone does not satisfy it); it's optional and does not change `is_onboarded`.
- **Pre-signup edge:** a member with no linked `User` isn't found by `member_for_verified_email` → `NEEDS_LOGIN` (covered explicitly).
- **Gotchas:** no real network (all Discord calls mocked with `respx`); the confirmation email asserts on href/markup, not visible copy (the "what's new" widget echoes the changelog — `reference_changelog_whatsnew_widget_pollutes_tests`); `context_*` is not collected — use `describe_*` for nested blocks.

## 10. Open / deferred

- **Go-live ops (not code):** the bot needs **Manage Roles**, and **its own role must sit above every guild role** in the Discord hierarchy or `assign_role`/`remove_role` 403s (handled gracefully, but sync silently won't apply). Enter `discord_server_id`, each guild's `discord_role_id`, and the reaction-role channel/message ids in Site Settings. Confirm `DISCORD_BOT_TOKEN`/`CLIENT_ID`/`CLIENT_SECRET` in Render, register the **second** `/discord/link/callback/` redirect URI on the Discord app (the existing `/settings/discord/callback/` stays), and run `seed_notification_templates` so the `discord_guilds_imported` copy exists before the first link (§7).
- **Reaching the existing ~609 members (adoption — flag for Josh).** The onboarding step (Screen B) shows only on the "Get started" card, which only appears for members who *aren't* onboarded yet — nearly every current member already is, so it will never surface for them; it drives *future* members. The vector for the existing base is the **low-friction link itself**: post `https://<MEMBER_HOST>/discord/link/` in the Discord server and/or a one-time member email blast — that is the whole point of the no-login flow. Decide at go-live whether to queue that announcement/email.
- **Discord in-app browser & the `state` cookie.** The link is clicked *inside Discord*, often its mobile in-app browser. The anon `state` handshake relies on the session cookie surviving the top-level redirect to Discord and back (`SameSite=Lax` permits this for a top-level navigation). Verify the round-trip in Discord's iOS/Android in-app browser before launch: a dropped cookie fails *safe* (friendly `OAUTH_FAILED` retry, never a 500) but would block the link, so confirm it actually completes, not just that it degrades gracefully.
- **Outbound role drift (mods).** Inbound reads *reactions*, never *roles* — a role a moderator adds/removes by hand in Discord is not reconciled by the app (the app writes a role only on an in-app join/leave). Roles can therefore drift from app membership if mods edit them directly; acceptable (Discord roles aren't a source of truth here), noted so it isn't mistaken for a sync bug.
- **Collapsed-guild canonical-role asymmetry:** inbound accepts *either* Glass (or Visual-Arts) emoji, but outbound assigns only the **one** canonical role the admin picks. A member who joins Glass in-app gets the canonical role, not both — acceptable, documented in the field help and the D3 hint.
- **Ambiguous "leave in-app while still reacting":** honoring the standing reaction, reconcile re-creates a `source="discord"` row next tick (§5.6 #4). We treat a live reaction as a live membership signal; not a bug. Could later suppress with a short "recently left in-app" tombstone if members complain — deferred (YAGNI).
- **Silent reconcile adds:** ongoing Discord-reaction joins send no lead notice / activity row (only the link-time confirmation). An optional lightweight `SiteActivity` "joined via Discord" row is deferred to keep noise and scope down.
- **Per-member reaction read at link time** is O(#emojis) fetches (~18) — fine for a single member on link; not batched. If Discord rate-limits become an issue at link time, fall back to "your guilds will sync within 15 minutes" and let the cron do it — deferred.
