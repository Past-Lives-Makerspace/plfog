# `/join-guild` Discord slash command — Spec & Implementation Plan

**Status:** Spec only — approved to build (v23).
**Date:** 2026-07-17
**Surface:** Discord (interactions platform) + FOG hub guild-edit page (`pastlives.test:8000` → `templates/hub/guild_edit.html`).
**Related:** `2026-07-13-discord-interactions-foundation.md`, `2026-07-12-discord-guild-linking-sync.md`, memory `project_discord_prod_golive` (the platform + all per-guild wiring is LIVE on prod).

---

## 1. Summary

A member types `/join-guild` in the Past Lives Discord, picks their guild from a dropdown, and is joined instantly — with no trip to the hub. The command adds the app-side `GuildMembership`, assigns the guild's Discord role(s), and delivers a welcome **two ways**: an ephemeral confirmation to the member, and a **public greeting in the guild's Discord channel** written in the guild lead's voice. It rides entirely on the interactions platform that is already live on prod (the endpoint, dispatch, member-resolution, role sync, and command registration all exist and work). It **complements** the existing emoji reaction-role flow — that stays untouched.

### Locked decisions (from user)

| Decision | Choice |
|---|---|
| Instant vs cron-delayed | **Instant** — a slash command is an interaction, so it fires the moment it's run (no 15-min reaction poll, no reaction-message redesign). |
| Guild picker | **Static dropdown** built from the active guild list (15 guilds ≤ Discord's 25-choice cap). Choices are snapshotted at `register_discord_commands` time; re-run it when guilds change. |
| Welcome delivery | **Both** — an ephemeral reply to the member AND a public post in the guild's Discord channel (via the wired `Guild.discord_webhook_url`). |
| Welcome copy | A **new per-guild `discord_welcome_message`** field, editable on the guild-edit page (guild lead's voice). Blank → a generic "Welcome to {guild}!" fallback. |
| Membership source | `record_app_join` (a deliberate, durable join — upgrades a prior `discord`-sourced row to `app`). |
| Reaction flow | **Untouched.** This is additive. |
| Already a member | Idempotent — confirm "you're already in", send **no** welcome email and **no** public re-announcement. |

## 2. What already exists (reuse, don't reinvent)

Confirmed on `origin/main` (the v23 base). This is assembly, not invention.

| Need | Existing thing | Location |
|---|---|---|
| Command dataclass + registry + autodiscover | `SlashCommand`, `register()`, `all_commands()`, `autodiscover()` | `core/events/discord_commands.py:37-98`; wired at `core/apps.py:37` |
| Register-with-Discord command (reads `all_commands()` → `to_api_dict()`) | `register_discord_commands` | `core/management/commands/register_discord_commands.py:57,88` |
| A hub-app command handler to mirror (`requires_link`, builds URLs from settings, style-5 button) | `/link` | `hub/discord_commands.py:58-93` |
| Read a submitted option value | `option_value(interaction, name)` | `core/events/discord_replies.py:28-38` |
| Ephemeral reply / deferred ack / followup PATCH | `reply()`, `deferred_ack()`, `ack_deferred()`, `send_followup()` | `core/events/discord_interactions.py:88-196` |
| Deferred driver (ack type-5 → run handler → followup) — for slow handlers | `_dispatch_deferred` via `defer=True` | `core/events/discord_commands.py:169-183` |
| Member resolution + unlinked→connect prompt | `resolve_member()`, `unlinked_reply()` (auto when `requires_link=True`) | `discord_commands.py:104-118`, `discord_interactions.py:117-135` |
| Join a guild in-app (source upgrade, never downgrade) + whether it's new | `GuildMembership.objects.record_app_join()` → `(membership, created, upgraded)` | `membership/models.py:2889-2903` |
| The join side-effect fan-out (activity + lead notification + optional welcome email) | `orientations.member_joined_guild(guild, member)` | `membership/orientations.py:477-510` |
| Outbound Discord role assign (loops ALL `discord_role_ids`; add/remove; best-effort) | `discord_roles.on_membership_changed(guild, member, joined=True)` | `core/events/discord_roles.py:77-108` |
| The canonical in-app join sequence to mirror | `hub/views.py:1712-1728` (`guild_join`) | `hub/views.py` |
| Post to a guild's Discord channel webhook (rich embed, best-effort, blank-URL no-op) | `post_embed(webhook_url, Message)` + `guild_webhook(guild)` | `core/events/discord.py:162-190`, `:55-76` |
| The `Message` value object a webhook post takes | `Message(title, body, url, discord_mention)` | `core/events/channels.py:58-77` |
| Guild edit form (ModelForm) + its existing Discord fields | `GuildEditForm` (`discord_webhook_url`, `discord_post_enabled` already on it) | `hub/forms.py:93-268` (fields `:112-135`, Discord `clean` `:214-228`) |
| Guild edit template — Discord card lives in the **Meetings** tab | `templates/hub/guild_edit.html:73-77` (main `<form>` `:24`, single Save `:102-103`) | — |

### Genuine gaps to close (kept minimal)

1. **`Guild.discord_welcome_message`** — one new `TextField` (+ migration). Confirmed absent (`git grep discord_welcome` empty).
2. **Dynamic option choices at registration** — no existing command uses `choices`; options are a static module-level list, and we must NOT hit the DB at import time. Add an optional `options_builder: Callable[[], list[dict]] | None` to `SlashCommand`; `to_api_dict()` calls it when set (else uses the static `options`). `register_discord_commands` already calls `to_api_dict()`, so the guild list is queried at register time only.
3. **The `/join-guild` command + handler** — a new `SlashCommand` in `hub/discord_commands.py`.
4. **Reuse `post_embed` for the public welcome** — no plain-text webhook helper exists and none is needed; construct a `Message` (title names the member, body = the lead's welcome) and post it as an embed via `guild_webhook(guild)`. **No user @-mention** (`Message.discord_mention` only does `@here`/`@everyone`). No new helper.

## 3. Where the code lives

```
core/events/discord_commands.py   # + optional options_builder on SlashCommand; to_api_dict() honors it
hub/discord_commands.py           # + JOIN_GUILD SlashCommand + _join_guild handler + _guild_choices() builder
membership/models.py              # + Guild.discord_welcome_message field
membership/migrations/00XX_*.py   # add column (reverse = remove)
hub/forms.py                      # + discord_welcome_message on GuildEditForm (field, widget, label, help_text)
templates/hub/guild_edit.html     # render the new textarea in the Meetings→Discord card
tests/core/events/discord_commands_spec.py     # options_builder + to_api_dict
tests/hub/discord_commands_spec.py             # /join-guild handler cases
tests/membership/guild_spec.py / hub/guild_edit_spec.py  # field + form + template
```

## 4. Data model

**`Guild.discord_welcome_message`** (`membership/models.py`, next to the other `discord_*` fields ~1304):

| Field | Type | Notes |
|---|---|---|
| `discord_welcome_message` | `TextField(blank=True, default="")` | `help_text="Shown to the member in Discord (their private confirmation) and posted in your guild's Discord channel when someone joins via /join-guild. This is separate from your guild Welcome email. Write it in your voice (a lead's welcome). Blank uses a generic welcome."` |

> **Not the Welcome email.** The guild already has a "Welcome email" field (Announcements/Emails tab, `join_email_body`). This is a *Discord-only* message — the label + hint must make that distinction so a lead doesn't conflate them.

Migration: add the column; reverse = drop it (a plain `AddField`, auto-reversible).

## 5. Business logic

### `SlashCommand.options_builder` (`core/events/discord_commands.py`)

Add `options_builder: Callable[[], list[dict]] | None = None` to the frozen dataclass. In `to_api_dict()`:

```python
options = self.options_builder() if self.options_builder is not None else self.options
return {"name": self.name, "description": self.description, "options": list(options), "type": 1}
```

Handlers still read values with `option_value()` at runtime — unchanged. The builder runs only when options are serialized (i.e. inside `register_discord_commands`), so no import-time DB access.

### `/join-guild` handler (`hub/discord_commands.py`)

`_guild_choices() -> list[dict]` — builds the option, resilient at both edges:
```python
def _guild_choices():
    from membership.models import Guild
    guilds = list(Guild.objects.filter(is_active=True).order_by("name"))
    if len(guilds) > 25:                        # Discord caps choices at 25
        logger.warning("join-guild: %d active guilds > 25; %r dropped from the picker (still joinable in the hub).",
                       len(guilds), [g.name for g in guilds[25:]])
        guilds = guilds[:25]
    option = {"name": "guild", "description": "Which guild to join.", "type": 3, "required": True}
    if guilds:                                  # empty choices would 400 Discord's bulk PUT
        option["choices"] = [{"name": g.name, "value": g.slug} for g in guilds]
    return [option]
```
> If there are 0 active guilds, the option ships **without** `choices` (a free-text field) rather than an invalid empty-choices option — the handler's lenient resolution (below) still works. Overflow past 25 is logged, not silent.

`_join_guild(interaction, member) -> dict` (`requires_link=True`, `defer=True`, `ephemeral=True`, `scope="guild"`):
1. `member = cast("Member", member)` (dispatch guarantees non-None; unlinked → `unlinked_reply` automatically).
2. `slug = option_value(interaction, "guild")`; resolve `guild = Guild.objects.filter(slug=slug, is_active=True).first()`. Unknown → `reply("That guild wasn't found. Try /join-guild again.", ephemeral=True)` (no writes).
3. `membership, created, upgraded = GuildMembership.objects.record_app_join(guild, member)`.
4. **Always ensure the Discord role** (idempotent, best-effort, and it *self-heals* a member who app-joined but whose role never got assigned on an earlier partial run): `discord_roles.on_membership_changed(guild, member, joined=True)`.
5. **Only when `created`** (a brand-new membership — NOT an `upgraded` reaction-join, who is already in the guild and channel):
   - **Public welcome** (best-effort, gated): `hook = guild_webhook(guild)` (returns the stripped URL only when `discord_post_enabled` AND non-blank). If `hook`, build `Message(title=f"Welcome {member.display_name} to {guild.name}!", body=_welcome_body(guild))` and `post_embed(hook, msg)`. **No `discord_mention`** — `Message.discord_mention` only supports `@here`/`@everyone` literals (with `allowed_mentions={"parse":["everyone"]}`), so a user id would print a bare number and ping no one; the embed **title** names the member instead.
   - **Welcome fan-out** (activity + lead notification + optional guild welcome email), wrapped best-effort so an email hiccup never swallows the member's confirmation: `try: orientations.member_joined_guild(guild, member) except Exception: logger.exception(...)`.
6. **Ephemeral reply** (followup, since `defer=True`):
   - `created` or `upgraded` → `f"You're in **{guild.name}**! 🎉\n\n{_welcome_body(guild)}"`.
   - already an app member (`not created and not upgraded`) → `f"You're already in **{guild.name}** — nothing to do."`

`_welcome_body(guild)` → `guild.discord_welcome_message.strip() or f"Welcome to {guild.name}! A lead will say hi soon."`

**Ordering rationale (partial-failure safety):** role assign (step 4) runs before the email fan-out (step 5) and on every path, so a member always ends up with their role even if a later step errors; the fan-out is wrapped so it can't block the confirmation. Because `member_joined_guild` can send email, the handler MUST be `defer=True` (ack type-5, then followup PATCH) to stay inside Discord's 3-second deadline. All Discord side-effects (role PUT, webhook post, followup) are best-effort and never raise — the `GuildMembership` row is the source of truth. The ephemeral flag is guaranteed to match between the type-5 ack and the followup (`_dispatch_deferred` acks with `cmd.ephemeral`; `send_followup` inherits it).

Register at module scope:
```python
JOIN_GUILD = SlashCommand(
    name="join-guild", description="Join a Past Lives guild.",
    handler=_join_guild, options_builder=_guild_choices,
    requires_link=True, defer=True, ephemeral=True, scope="guild",
)
register(JOIN_GUILD)
```

## 6. UI / UX

Only one screen changes — the guild-edit page gains one field. (The Discord side is not a web UI; its "screens" are the ephemeral reply and the channel post, specified in §5/§7.)

- **Screen / partial:** `templates/hub/guild_edit.html`, **Meetings tab → Discord card** (the existing card at lines 73-77, after `form.discord_post_enabled`).
- **Layout & container:** inline field inside the existing card, inside the **main `<form>`** (line 24). It saves with the existing single **Save button** (lines 102-103, `type="submit"`) — **no new form, no new endpoint** (critical: do NOT wrap it in its own `<form>`; the Studio-Hours-tab separate-form pattern does not apply to a plain Guild field).
- **Components used:** `{% include "components/form_field.html" with field=form.discord_welcome_message %}` — renders label + `Textarea` + hint inside `.pl-form-group`.
- **The control, named explicitly:**
  - Field: `discord_welcome_message`, `Textarea` widget (`rows=3`), label **"Discord welcome message"**, hint **"Posted to your guild's Discord channel and sent to the member when someone joins via /join-guild. Blank = a generic welcome."**
  - Save: the existing Meetings-tab Save button submits it with the rest of the form → Django-messages success + redirect back to the tab (existing behavior).
- **States:**
  - *Empty:* blank is valid → the generic fallback is used at join time. Hint explains this.
  - *Error:* none specific (free text, optional); form-level errors render via `form_field.html`'s error slot.
  - *Success:* existing "Guild updated" Django message on save.
- **Dark + light:** the `Textarea` inherits input tokens because it's inside `.pl-form-group` (via `form_field.html`) — **no inline `background`/`color`**. Verify both themes.
- **Mobile:** single full-width textarea in the existing card; already reflows. No table, no horizontal scroll.

No list editor is involved (single field), so the "+ Add / per-row Delete" rubric does not apply.

## 7. Notifications / emails / activity

- **Reuses** `orientations.member_joined_guild` — which already emits the `GUILD_JOINED` activity, the lead in-app notification, and the optional `guild_welcome` email (only when `GuildOrientationSettings.join_email_ready`). No new triggers.
- **New Discord outputs** (not app notifications): the ephemeral command reply and the public channel embed (only on a brand-new join). Both best-effort. The public embed's **title** names the member (`Welcome {display_name} to {guild}!`) and its body carries the lead's `discord_welcome_message` — "a welcome on behalf of the guild lead." It does **not** @-ping the member (see §5 — `Message.discord_mention` can't address a user).
- No `emit()`/`period` needed for the Discord replies (they are direct interaction responses / webhook posts, not spine broadcasts).

## 8. Build order (each phase ships green)

1. **Field + migration** — `Guild.discord_welcome_message`, migration (fwd/back verified). Green.
2. **`options_builder`** on `SlashCommand` + `to_api_dict()` honoring it, with tests. Green (no behavior change to existing commands — they have no builder).
3. **`/join-guild` handler + registration** in `hub/discord_commands.py`, with handler tests (respx-mocked Discord REST). Green.
4. **Guild-edit field UI** — add to `GuildEditForm.Meta.fields` + widget/label/help_text; render in the Discord card; form + template tests. Green.
5. **Version + changelog** — bump `plfog/version.py` to the v23 line; one member-facing entry: *"Join a guild straight from Discord — type `/join-guild`, pick your guild, and you're in, with a welcome from the guild."*

> Post-merge go-live op: run `register_discord_commands` (Render one-off job) so `/join-guild` appears in the server, and re-run it whenever the guild list changes (choices are snapshotted).

## 9. Testing

BDD `*_spec.py`, factory-boy, respx for Discord REST, ≥98% coverage, Docker `plfog-web`.

- **`options_builder` / `to_api_dict`:** builder returns a guild option whose `choices` are `(name→value=slug)` for active guilds, capped at 25; `to_api_dict()` uses the builder when present, the static list otherwise (existing commands unaffected).
- **Handler:**
  - Linked member, valid guild, **brand-new** join (`created`) → membership created, role PUT issued (respx), public embed posted to the webhook (respx) with the member's name in the **title** and no `<@id>`/`parse:everyone`-user ping, `member_joined_guild` called, ephemeral followup contains the welcome body.
  - `discord_welcome_message` set vs blank → body is the lead's text vs the generic fallback.
  - **Upgrade path** (member had a `source="discord"` reaction-join row → `upgraded=True, created=False`) → role ensured, ephemeral "You're in", but **no public embed post** and **no `member_joined_guild`** (they're already in the guild/channel — no surprise re-welcome).
  - **Self-heal** (existing `source="app"` row, `created=False, upgraded=False`) → still calls `on_membership_changed(joined=True)` (idempotent role add), ephemeral "already in", **no** `member_joined_guild`, **no** webhook post.
  - `member_joined_guild` raising → handler still returns the ephemeral confirmation (best-effort wrap), role already assigned.
  - Unknown/inactive guild slug → ephemeral "not found", no writes.
  - Guild with no `discord_webhook_url` (or `discord_post_enabled=False`, via `guild_webhook()`) → join still succeeds; no public post (guarded), no error.
  - Unlinked path is covered by existing dispatch tests (`requires_link=True` → `unlinked_reply`); add one asserting `/join-guild` carries `requires_link=True`.
- **`_guild_choices`:** ≤25 active guilds → one choice per guild (`value=slug`); >25 → capped at 25 + a logged warning; 0 active → option carries no `choices` key (no invalid empty-choices payload).
- **Field/form/template:** `discord_welcome_message` saves via `GuildEditForm`; renders in the Discord card; blank allowed.
- **Migration:** forward adds the column; reverse drops it.

## 10. Open / deferred

- **Autocomplete** instead of a static dropdown (so guild changes need no re-register) — deferred; static choices are the user's pick and simpler. Note the re-register step in the runbook.
- **Leaving a guild** via `/leave-guild` — out of scope (reaction-remove + the hub already handle leaving).
- **Per-guild toggle** to disable `/join-guild` for a guild — out of scope; every active guild is joinable.

> Spec only — build under the v23 branch, one PR.
