# `/guide` Discord command — Spec & Implementation Plan

**Status:** Spec only — approved to build (v23).
**Date:** 2026-07-17
**Surface:** Discord (interactions platform).
**Related:** `2026-07-17-join-guild-command.md`, `2026-07-13-discord-interactions-foundation.md`.

---

## 1. Summary

A member types `/guide` in the Past Lives Discord and gets a tidy, ephemeral list of **every FOG slash command and what it does** — a built-in "what can I do here?" so members don't have to remember the command set. It's generated straight from the command registry, so it lists whatever is registered (today `/fog-ping`, `/link`, `/join-guild`, `/whats-on`, `/info`, `/schedule-orientation`, `/balance`, and `/guide` itself) and **stays correct automatically** as commands are added or removed — no separate list to maintain.

### Locked decisions

| Decision | Choice |
|---|---|
| Source of the list | **The command registry** (`all_commands()`) — never a hand-maintained list. |
| Visibility | **Ephemeral** (`flags:64`) — a personal reference, no channel clutter. |
| Link required | **No** (`requires_link=False`) — anyone can read the guide. |
| Content per command | `**/name** — description`, plus a one-line note that some commands need a connected account (link via `/link`). |

## 2. What already exists (reuse, don't reinvent)

| Need | Existing thing | Location |
|---|---|---|
| The registry to read (name/description/`requires_link` per command) | `all_commands()`, `SlashCommand` | `core/events/discord_commands.py:37-79` |
| Command handler + registration pattern (no-option, `requires_link=False`) | `/fog-ping` / `/link` | `core/events/discord_commands.py`; `hub/discord_commands.py:58-93` |
| Ephemeral reply builder | `reply(text, ephemeral=True, embeds=…)` | `core/events/discord_interactions.py:88-105` |
| Auto-registration (add to registry → registered) | `register_discord_commands` reads `all_commands()` | `core/management/commands/register_discord_commands.py:57,88` |

No new model, migration, form, or UI. One handler + one `register()`.

## 3. Where the code lives

```
core/events/discord_commands.py   # + GUIDE SlashCommand + _guide handler (natural home — it reads the registry it lives in)
tests/core/events/discord_commands_spec.py   # handler cases
```

## 5. Business logic

`_guide(interaction, member) -> dict` (`requires_link=False`, `ephemeral=True`, `scope="guild"`):
- Read `all_commands()`, build one line per command: `f"**/{c.name}** — {c.description}"` (registry order). Optionally group nothing — a flat list is clearest for ~8 commands.
- Return an **embed** (title "Past Lives commands", the lines as the description) via `reply(..., ephemeral=True, embeds=[embed])`, with a footer/last line: *"Some commands need your account connected — run `/link` first."*
- Pure function of the registry; no side effects, no DB.

Register at module scope: `GUIDE = SlashCommand(name="guide", description="List the Past Lives Discord commands and what they do.", handler=_guide, requires_link=False, ephemeral=True, scope="guild"); register(GUIDE)`.

## 6. UI / UX

The only "screen" is the ephemeral Discord reply.
- **Content:** an embed titled "Past Lives commands"; each registered command as `**/name** — description`; a closing line pointing unlinked users at `/link`.
- **States:** the registry is always non-empty (at least `/guide` itself), so there's no empty state; a registry read can't fail. Ephemeral, so no clutter and no "back" needed.
- Not a web UI — no theme/mobile/list-editor concerns.

## 8. Build order

1. Add the `GUIDE` command + `_guide` handler + tests. Green.
2. **Version + changelog** — fold into the v23 line; the member-facing entry can be a bullet on the Discord-commands story or a short line: *"New `/guide` command in Discord lists every Past Lives command and what it does."* (Handled centrally.)

> Post-merge: `register_discord_commands` (already required for `/join-guild`) registers `/guide` in the same run.

## 9. Testing

- `_guide` returns an ephemeral embed whose text contains each registered command's `/name` and description (assert `/join-guild` and `/guide` both appear), and the `/link` hint.
- The command carries `requires_link=False` and `ephemeral=True`.
- Adding a command to the registry makes it appear in the guide (drive via a temporarily-registered fake command, or assert the list is derived from `all_commands()` — no hard-coded names).

## 10. Open / deferred

- **Grouping / categories** (member vs lead commands) — deferred; a flat list is clear at this scale.
- **Per-command usage examples** — deferred; the description is enough.

> Spec only — build under v23 (independent of the other specs; touches only `core/events/discord_commands.py`).
