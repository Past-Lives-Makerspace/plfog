# Discord `/poll` command (sesh parity) — Spec & Implementation Plan

**Status:** Approved to build (fog-quick-feature run, v1.9.0).
**Date:** 2026-08-26
**Size:** Small — one slash command, no models, no migrations, no UI.
**Related:** `docs/superpowers/plans/2026-08-25-discord-create-command.md` (the sesh-replacement release this completes), PR #226.

## Why

Sesh is being removed from the Discord server, and the hard requirement is **no lost functionality for sesh users**. `/create` shipped in v1.8.0; `/poll` is the last sesh command members actually use (Tech Guild movie-night vote, 2026-08-20). Fog Bot gets its own `/poll` so the muscle memory keeps working; sesh is not removed until this is live.

## What we're building

`/poll` posts a **native Discord poll** in the channel where it's invoked, visible to everyone there. The invoker types the question and answers as options; the bot posts the poll as its interaction response, credited to the asker in the message content.

### Locked decisions

| Decision | Choice |
|---|---|
| Post mechanism | The interaction response itself carries the poll (`type: 4` with `data.poll`) — no extra REST call, no per-channel permission dance. Public (flags 0), unlike every other Fog Bot reply. |
| Answers input | One `answers` string option. Split on `;` when the input contains any `;`, else split on `\|` — so an answer may contain a literal pipe as long as semicolons separate the list. Trimmed, empties dropped. 2–10 answers required, no exact duplicates (after trim + emoji extraction). |
| Duration | **Type 4 (INTEGER)** choice option in hours (a string option with int values fails command registration): 1 hour / 4 hours / 8 hours / 1 day (default) / 3 days / 1 week / 2 weeks / 32 days → values `1,4,8,24,72,168,336,768` (768 is Discord's max). |
| Multi-select | Boolean option (Discord type 5), default false. |
| Attribution / visual header | Styled content line above the native poll widget: `📊 **Poll from <member display name>**  ·  open for <duration label>  ·  <pick one / pick any>` — plain text, no @mention, no ping. The poll itself renders in Discord's native widget (live progress bars, counts, voter avatars, countdown, View Votes) — visually richer than sesh's static reaction embeds. |
| Answer emoji | If an answer begins with a unicode emoji or a custom emoji token (`<:name:id>` / `<a:name:id>`), strip it from the text and set it as the answer's `poll_media.emoji` (`{"name": <emoji>}` for unicode, `{"id": <id>}` for custom) so it renders as the answer's icon. **Empty-remainder rules (Discord rejects empty answer text):** a unicode-emoji-only answer (incl. trailing whitespace) keeps the original string as its text with NO emoji field; a custom-token-only answer uses the token's `name` part as text with `{"id": <id>}` as emoji. |
| Ephemeral mechanics | `SlashCommand.ephemeral` is declarative (used only by the deferred ack path) — actual visibility is per-reply. Every validation reply passes `ephemeral=True` explicitly; the happy-path payload sets flags 0. `defer` must stay `False`: `send_followup` cannot carry a poll, so a deferred `/poll` would silently drop it (note this in the `reply(poll=...)` docstring). |
| Confirm step | None — posts immediately, matching sesh. Polls don't ping anyone; a mistake can be ended/deleted by mods. Discord polls cannot be edited after posting, so the validation errors must be airtight instead. |
| Gating | `requires_link=True` like every other posting command (`/create-announcement`, `/create`). |
| Version | `VERSION` 1.8.0 → **1.9.0**, one new member-facing CHANGELOG entry. |

## What already exists (reuse, don't reinvent)

| Need | Existing thing | Location |
|---|---|---|
| Command declaration/registration/dispatch | `SlashCommand`, `register`, autodiscover | `core/events/discord_commands.py:37-145` |
| Reply builder to extend | `reply()` (type 4; content/embeds/components) | `core/events/discord_interactions.py:88-105` |
| Member resolution + display name | `resolve_member` (dispatch-level via `requires_link`), `Member.__str__` / display-name property (check what `/members` uses) | `core/events/discord_commands.py:151-165`, `membership/discord_commands.py` |
| `/guide` auto-listing | `_guide` builds from `all_commands()` | `core/events/discord_commands.py:346-370` |
| Registration to Discord | `register_discord_commands` mgmt command (bulk PUT) | `core/management/commands/register_discord_commands.py` |
| Bot permission | `send_polls` already granted on the Fog Bot role (verified 2026-07-28) | Discord server settings |

Genuine gaps (net-new): the `/poll` command module section, an answers-splitting helper, and a `poll=` kwarg on `reply()`.

## Where the code lives

```
membership/discord_commands.py     # /poll section (or hub/ — builder's call; membership hosts the other member commands)
core/events/discord_interactions.py  # reply() gains an optional poll= kwarg (included only when provided)
tests/core/events/poll_command_spec.py  # NEW
plfog/version.py                   # 1.9.0 + changelog entry
```

## Behavior

### Options (required first, per Discord)

| # | Option | Type | Req | Description (copy-ready, ≤100 chars, no dashes) |
|---|---|---|---|---|
| 1 | `question` | 3 str | yes | What you are asking, like Which movie should we watch? |
| 2 | `answers` | 3 str | yes | The choices, separated by semicolons, like Alien; Clue; The Thing. Between 2 and 10. |
| 3 | `duration` | 4 int choices | no | How long voting stays open. Default is 1 day. |
| 4 | `multiselect` | 5 bool | no | Let people pick more than one answer. Off by default. |

Duration choices: `1 hour`/`4 hours`/`8 hours`/`1 day (default)`/`3 days`/`1 week`/`2 weeks`/`32 days`.

### Validation (each an immediate **ephemeral** reply naming the fix; nothing posts)

| State | Copy |
|---|---|
| < 2 answers after splitting/trimming | Give me at least 2 answers, separated by semicolons. Like: Alien; Clue; The Thing |
| > 10 answers | Discord polls allow at most 10 answers. Trim the list and try again. |
| An answer longer than 55 chars | Each answer has to fit in 55 characters. Shorten "<first offender, truncated>" and try again. |
| Question longer than 300 chars | The question has to fit in 300 characters. Shorten it and try again. |
| Duplicate answers (exact match after trim + emoji extraction) | You have the same answer twice. Make each one different and try again. |

(Discord's poll limits: question text ≤300, answer text ≤55, ≤10 answers, duration ≤768h — the builder must verify these against the current API docs constants and encode them as module constants, not magic numbers.)

### Happy path

Returns `{"type": 4, "data": {"content": "📊 Poll from <name>", "poll": {"question": {"text": q}, "answers": [{"poll_media": {"text": a}}, ...], "duration": <hours int>, "allow_multiselect": <bool>}}}` with **flags 0** (public — a poll must be visible/votable by the channel). `SlashCommand(..., ephemeral=False, requires_link=True, defer=False, scope="guild")`.

### States checklist (UX completeness for a chat surface)

- Happy: public poll message, native Discord voting UI, auto-expires at duration. ✓
- Error states: the four ephemeral validation replies above; unlinked → dispatch's existing connect prompt; malformed/empty question is impossible (Discord requires the option). ✓
- Empty state: n/a (no listing surface). `/guide` lists the command automatically. ✓
- No dead ends: every rejection names the accepted format with an example. ✓

## Testing (BDD `*_spec.py`, 100% coverage)

`tests/core/events/poll_command_spec.py`:
- command definition: name, `ephemeral=False`, `requires_link=True`, option set, required-first ordering, duration choice values, multiselect is type 5.
- answers helper: semicolons, pipes, mixed, trimming, empty segments dropped.
- emoji extraction: leading unicode emoji → `poll_media.emoji` ({"name": <emoji>}), leading custom emoji token → ({"id": <id>}, token name becomes the text), unicode-emoji-only answer (incl. trailing whitespace) keeps the original string as text with no emoji field, custom-token-only answer gets the token name as text, no-emoji answer untouched; the 55-char limit applies to the text AFTER emoji extraction.
- separator: input with `;` splits only on `;` (pipes survive inside answers); input with no `;` splits on `|`; duplicate detection runs after trim + emoji extraction.
- header line: contains the display name, the duration label, and "pick one" vs "pick any" matching multiselect.
- each validation error (1 answer, 11 answers, 56-char answer, 301-char question) is ephemeral and posts nothing.
- happy path: type 4, flags 0, poll payload shape (question/answers/duration default 24/multiselect false), explicit duration and multiselect honored, attribution line contains the member's display name.
- `reply(poll=...)` includes the key only when provided (existing replies unaffected).
- `/guide` lists `poll`.
- dispatch integration: unlinked member gets the connect prompt.

## Out of scope

- Recurring polls, role-restricted polls (sesh features never used on this server).
- Answers containing the active separator character (a `;` inside an answer can't be expressed; a `|` can, when semicolons separate the list). Documented limitation, revisit only if someone hits it.
- A results/end subcommand (native Discord UI handles both).
- Web/hub surface for polls.

## Versioning & changelog

`VERSION` → **1.9.0**. One member-facing entry: title "Post polls with /poll", body: "Ask the room anything with /poll in Discord. Type your question and answers, pick how long voting stays open, and the bot posts a native Discord poll right in the channel. This replaces the old sesh bot's /poll."

## Post-deploy

`python manage.py register_discord_commands` as a Render one-off (quote-free startCommand), then verify `/poll` appears in the guild command list via the Discord API.
