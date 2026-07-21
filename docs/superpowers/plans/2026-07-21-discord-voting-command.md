# `/voting` Discord command — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-21
**Surface:** Discord (interactions platform); read-only companion to the FOG hub voting page (`/guilds/voting/`).
**Related:** `2026-07-13-discord-interactions-foundation.md`, `2026-07-13-discord-member-commands.md`, `2026-07-17-guide-command.md`.

---

## 1. Summary

A linked member types `/voting` in the Past Lives Discord and gets an ephemeral mini-dashboard: this month's **live guild-funding standings** drawn as a unicode bar graph, **their own three ranked choices** (or a "you haven't voted yet" nudge), and a **button to the FOG voting page**. It's the voting page's at-a-glance twin — check the race and confirm your ballot without leaving Discord, then click through to change it.

The one piece of plumbing work: the live tally currently lives as a private helper in `hub/views.py`. It gets **lifted into `membership`** (where the voting models live) so both the hub page and the Discord handler call one shared implementation — the Discord handler never imports `hub.views`.

### Locked decisions

| Decision | Choice |
|---|---|
| Dispatch policy | `requires_link=True`, `ephemeral=True`, `defer=False`, `scope="guild"` — mirrors `/fog-ping` and `/whats-on`. Two cheap aggregate queries; no defer needed. |
| Graph rendering | **Unicode block bars** (`█`/`░`, fixed width 12 chars scaled from `bar_pct`) inside a single embed description. **No image attachment, no new plumbing.** |
| Standings content | Top guilds by points, 🥇🥈🥉 for ranks 1–3 (mirrors `templates/hub/_vote_bar.html`), points per guild, cycle label + closes-on date (`get_cycle_context()`), weighting note (1st=5, 2nd=3, 3rd=2 pts). |
| Member's ballot | From `member.vote_preference` (OneToOne). `None` → "you haven't voted yet" nudge. Either way, a link-style button to `hub_url("hub_guild_voting")`. |
| Tally source | **Reuse `_compute_live_standings()`** (`hub/views.py:224`) — lifted into `membership/vote_calculator.py` with its `VoteStanding` TypedDict and the `distinct=True` Count gotcha (`hub/views.py:237-240`) preserved verbatim. Behavior identical; hub page keeps working. |
| Handler home | `membership/discord_commands.py` (voting models live in `membership`); registered via the `SlashCommand` dataclass + `register()` pattern. |
| Length safety | Standings are small (~a dozen guilds; ~40 chars/row ≈ 500 chars), well under the 4096 embed-description cap — but cap rows defensively and `truncate()` the final description. |
| Go-live | Re-run `register_discord_commands` after deploy (guild-scoped commands register instantly). |

## 2. What already exists (reuse, don't reinvent)

| Need | Existing thing | Location |
|---|---|---|
| Live tally (points + `bar_pct` per guild) | `_compute_live_standings()` → `list[VoteStanding]` | `hub/views.py:224` (to be lifted — see §5.1) |
| `VoteStanding` shape (`guild_name`, `total_points`, `bar_pct`) | `VoteStanding` TypedDict | `hub/views.py:69` (moves with the lift) |
| The cross-join fix the tally depends on | `distinct=True` on the three reverse-FK `Count`s, with the 6/6/6-instead-of-1/2/3 comment | `hub/views.py:237-240` (comment moves verbatim) |
| Vote weights (5/3/2) | `WEIGHTS` | `membership/vote_calculator.py:17` |
| Signed-up-voters-only rule | `VotePreferenceQuerySet.from_signed_up_members()` (the tally applies the same `member__user__isnull=False` filter inline) | `membership/models.py:4313` |
| Member's ballot | `member.vote_preference` OneToOne (`guild_1st/2nd/3rd`, `updated_at`); `Member._has_voting_preference` | `membership/models.py:4338`, `:591` |
| Cycle label + closes date | `get_cycle_context()` → `current_cycle_label`, `cycle_closes_on` | `membership/cycle.py:11` |
| Command registry + dispatch (unlinked gate, error wrap) | `SlashCommand`, `register()`, `dispatch()`, `resolve_member()` | `core/events/discord_commands.py:37,82,198,116` |
| Reply builders | `reply(content, ephemeral=True, embeds=…, components=…)`; `unlinked_reply()` (automatic via `requires_link`) | `core/events/discord_interactions.py:88,117` |
| Absolute hub URL + safe truncation + local time | `hub_url("hub_guild_voting")`, `truncate()`, `format_local()` | `core/events/discord_replies.py:41,57,49`; URL name at `hub/urls.py:7` |
| Link-button row pattern (`style: 5`) | `_fog_ping`'s `button_row` | `core/events/discord_commands.py:243-248` |
| Reference handlers (no-option, embed, requires_link) | `/whats-on` `membership/discord_commands.py:78-109`; `/info` embed fields `:156-247`; `/fog-ping` `core/events/discord_commands.py:257` | — |
| Dashboard copy to mirror | Intro + weighting sentence `templates/hub/guild_voting.html:9-10`; ballot table `:76-129`; medal bars `templates/hub/_vote_bar.html` | — |
| Test fixtures | `linked_member` fixture (`tests/core/events/conftest.py:24`); `MemberFactory`, `GuildFactory`, `VotePreferenceFactory` (`tests/membership/factories.py:304`) | — |

No new model, migration, form, or web UI. Gaps to close: (a) the lift in §5.1, (b) one handler + `register()`, (c) a tiny `_bar()` string helper.

## 3. Where the code lives

```
membership/vote_calculator.py        # + VoteStanding TypedDict, compute_live_standings(), compute_new_votes_since()
hub/views.py                         # − the two private helpers + local VoteStanding; imports from membership.vote_calculator
plfog/dashboard.py                   # comment at :43 references _compute_live_standings — point it at the new home
membership/discord_commands.py       # + _voting handler, _bar() helper, VOTING SlashCommand + register()
tests/hub/guild_voting_spec.py       # import (:14) + rename ~12 call sites/describe blocks to the public names; assertions unchanged
tests/core/events/voting_command_spec.py   # new handler specs (siblings: whats_on_command_spec.py, balance_command_spec.py)
```

## 4. Data model

None. Read-only feature over existing `VotePreference` / `Guild` rows. No migration.

## 5. Business logic

### 5.1 The refactor — lift the live tally into `membership`

Move, verbatim in behavior, from `hub/views.py` into `membership/vote_calculator.py` (already the guild-funding math home — it owns `WEIGHTS`):

- `VoteStanding` TypedDict (from `hub/views.py:69`).
- `_compute_live_standings()` → public **`compute_live_standings()`**.
- `_compute_new_votes_since(since)` → public **`compute_new_votes_since(since)`** — it shares the `VoteStanding` type and the same `distinct=True` comment, so it moves too rather than leaving half the pair stranded in the view module.

Rules for the lift:

- The `distinct=True` comment block (`hub/views.py:237-240` — the 6/6/6-instead-of-1/2/3 cross-join trap) moves **verbatim**. It's load-bearing documentation of a real regression class.
- The hardcoded `* 5 / * 3 / * 2` become `WEIGHTS["1st"] / WEIGHTS["2nd"] / WEIGHTS["3rd"]` (same file, same values — one source of truth, zero behavior change).
- `Guild` is imported **inside the functions** (mirroring the lazy-import house style in the Discord modules): `membership/models.py:4530` already imports `vote_calculator` lazily, and a module-level `from membership.models import Guild` would couple the pure-math module to the ORM at import time.
- `hub/views.py` deletes its copies and its local `VoteStanding`, and calls `compute_live_standings()` / `compute_new_votes_since()` — `guild_voting`, `snapshot_history`/`snapshot_detail` context, and the hub page render byte-for-byte the same.
- `tests/hub/guild_voting_spec.py` updates the import at `:14` AND renames every call site to the public (underscore-free) names — ~12 call sites (:429–:614), two `describe_*` block names (:425, :570), and two comments; **assertions themselves stay untouched and green** (including the distinct-regression case around `:531`).
- `plfog/dashboard.py:43` comment updated to name the new home.

### 5.2 The handler — `_voting(interaction, member) -> dict`

Thin, per the house pattern (resolve → call domain code → format):

1. `member = cast("Member", member)` — `requires_link=True` guarantees dispatch resolved a linked member (mirror `/schedule-orientation`'s cast + comment).
2. `standings = compute_live_standings()`; `cycle = get_cycle_context()`; `preference = getattr(member, "vote_preference", None)` (exactly how `hub/views.py:158` reads it).
3. Build the embed (§6) and the button row (`style: 5`, `url=hub_url("hub_guild_voting")` — copy `_fog_ping`'s `button_row` shape).
4. `return reply("", ephemeral=True, embeds=[embed], components=[button_row])`.

Helpers (module-level in `membership/discord_commands.py`, ~5 lines total):

- `_BAR_WIDTH = 12`; `_bar(bar_pct: float) -> str` — `filled = max(1, round(bar_pct / 100 * _BAR_WIDTH))`, return `"█" * filled + "░" * (_BAR_WIDTH - filled)`. `max(1, …)` because standings only contain guilds with points > 0, mirroring the `min-width: 2px` sliver in `_vote_bar.html:22`. The leader (`bar_pct == 100.0`) renders 12 full blocks. Bars are **relative to the leader** (that's what `bar_pct` means), exactly like the hub page.
- `_STANDINGS_CAP = 15` — rows beyond it collapse to `…and N more on the voting page` (mirrors `_SECTION_CAP` overflow at `:73-75`). Final belt-and-braces: `truncate(description, 4096)` before building the embed.
- Rank prefix: `🥇 🥈 🥉` for rows 1–3 (same medals as `_vote_bar.html:5`), then `` `4.` `` `` `5.` `` … plain numbers. Guild name is **bold** for ranks 1–3, plain for 4+ (exactly as the §6.1 mock shows). Note: width-12 quantization can render a ~95% guild with the same 12 full blocks as the leader — the `— N pts` suffix disambiguates; accepted.

No side effects: no writes, no notifications, no activity log. Pure read.

### 5.3 Registration

```python
VOTING = SlashCommand(
    name="voting",
    description="See this month's live guild-funding standings and your ballot.",
    handler=_voting,
    requires_link=True,
    ephemeral=True,
    defer=False,
    scope="guild",
)
register(VOTING)
```

Autodiscovered via the existing `membership/discord_commands.py` module; `/guide` lists it automatically (it reads the registry).

## 6. UI / UX — the ephemeral Discord reply, every state

The only "screen" is one ephemeral embed + one button row. Not a web UI — no theme/mobile/form concerns — but **every state is specified** below. Copy mirrors the voting page (`templates/hub/guild_voting.html:9-10`).

### 6.1 Happy path (standings exist, member has voted)

Embed `title`: `Guild funding — July 2026` (from `current_cycle_label`). Embed `description` (literal example; bars in inline code so the fixed width holds in Discord's proportional font):

```
Your votes decide how the monthly funding pool is split. This cycle closes **July 31, 2026**.

🥇 `████████████` **Fiber Arts** — 42 pts
🥈 `████████░░░░` **Woodshop** — 28 pts
🥉 `█████░░░░░░░` **Ceramics** — 18 pts
`4.` `███░░░░░░░░░` Metals — 11 pts
`5.` `█░░░░░░░░░░░` Print — 4 pts

**Your ballot**
1st — Fiber Arts · 5 pts
2nd — Ceramics · 3 pts
3rd — Woodshop · 2 pts
_Last updated Mon Jul 14, 2:00 PM_
```

- Closes-on date: `cycle_closes_on` from `get_cycle_context()`.
- Ballot rows mirror the hub's "Your Current Votes" table (`guild_voting.html:76-129`): rank, guild name, that rank's points; "Last updated" from `preference.updated_at` via `format_local()`.
- Embed `footer.text`: `Weighting: 1st = 5 pts · 2nd = 3 pts · 3rd = 2 pts` (values from `WEIGHTS`, never re-hardcoded).
- Components: one action row with one **link button** (`style: 5`) — label **"Open the voting page"**, `url = hub_url("hub_guild_voting")`. This is the change-your-vote path; the command itself never mutates anything.

### 6.2 Member hasn't voted (`preference is None`)

Standings render as above; the ballot block is replaced by the nudge:

```
**Your ballot**
You haven't voted yet — your three ranked choices help decide where this month's funding pool goes. It takes 30 seconds on the voting page below.
```

Same button. No dead end: the nudge names the action and the button performs it.

### 6.3 No votes cast at all this cycle (`standings == []`)

The standings block is replaced with:

```
No votes yet this cycle — the standings are wide open. Be the first!
```

…followed by the member's ballot block (§6.1) or the nudge (§6.2 — with an empty tally this is the usual pairing). Title, closes-on line, footer, and button all still render, so the reply is never a bare stub.

### 6.4 Unlinked member

Handled **before the handler runs**: `requires_link=True` means `dispatch()` (`core/events/discord_commands.py:217`) returns the standard `unlinked_reply()` connect prompt with the one-click link button. Nothing to build; one test asserts the flag.

### 6.5 Error state

Any handler exception is caught by `dispatch()` and becomes the standard friendly `error_reply()` — never a 500 back to Discord. Nothing to build.

### 6.6 Overflow / truncation

More than `_STANDINGS_CAP` (15) guilds with points → rows 16+ collapse into `…and N more on the voting page`. The assembled description additionally passes through `truncate(…, 4096)` so a pathological guild name can never breach Discord's embed cap. Nothing is ever silently dropped — the overflow line and the button both point at the full page.

### UX-completeness check (Discord surface)

- Every state specified: happy (6.1), no-ballot (6.2), empty-standings (6.3), unlinked (6.4), error (6.5), overflow (6.6). ✔
- Primary action obvious: the one link button, present in every handler-built state. ✔
- No dead ends; ephemeral so no channel clutter and nothing to clean up. ✔
- Nothing half-built: read here, change on the page — the page already owns submit/update. ✔
- Copy is member-plain and mirrors the hub page's own words. ✔

## 8. Build order (each phase ships green: full suite + lint + mypy)

1. **The lift.** Move `VoteStanding` + both compute functions into `membership/vote_calculator.py` (WEIGHTS-backed multipliers, lazy `Guild` import, comments intact); update `hub/views.py`, `tests/hub/guild_voting_spec.py` (import + all call-site renames per §5.1), and the `plfog/dashboard.py:43` comment. Pure refactor — zero behavior change, existing specs prove it.
2. **The command.** `_bar()` + `_voting` + `VOTING`/`register()` in `membership/discord_commands.py`; new `tests/core/events/voting_command_spec.py`.
3. **Housekeeping.** Bump `plfog/version.py` VERSION + member-facing changelog entry, e.g. *"Ask the Fog Bot: `/voting` on Discord shows this month's live guild-funding standings and your own ranked ballot, with a jump to the voting page."*

> Post-deploy: re-run `register_discord_commands` (guild-scoped → registers instantly). `/guide` picks the command up automatically.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, **`describe_*` / `it_*` only** (`context_*` is silently uncollected), factory-boy, run in the `plfog-web` image, ≥98% coverage gate.

**Refactor (phase 1)** — `tests/hub/guild_voting_spec.py` keeps all existing cases (points math, signed-up-only filter, sort order, `bar_pct`, and the `distinct=True` cross-join regression case) with only the import changed. That suite staying green *is* the refactor's proof.

**Handler (phase 2)** — `tests/core/events/voting_command_spec.py`, structured like `whats_on_command_spec.py` / `balance_command_spec.py` (same dir, `linked_member` fixture, `VotePreferenceFactory`/`GuildFactory`):

- `describe_voting_command_definition` — `it_is_linked_only_ephemeral_and_immediate`: `name == "voting"`, `(requires_link, ephemeral, defer, scope) == (True, True, False, "guild")`.
- `describe_voting` (happy): with 3 voters across guilds, the embed description contains 🥇 on the top guild, correct `pts` values, the cycle label in the title, the closes-on date, and the footer weighting line; the components carry a `style: 5` button whose `url == hub_url("hub_guild_voting")`; reply `flags` mark it ephemeral.
- Bar scaling: leader renders 12 `█` and 0 `░`; a guild at 50% renders 6/6; a tiny-but-nonzero guild renders at least 1 `█` (never an all-`░` bar).
- Ballot block: renders the member's three guilds with 5/3/2 pts and the `updated_at` line; another member's ballot never leaks in.
- No ballot: nudge copy shown, no "1st —" rows, button still present.
- Empty standings: "No votes yet this cycle" copy; title/closes-on/footer/button still present.
- Overflow: 16+ guilds with points → 15 rows + "…and more" line; description length ≤ 4096.
- Tz/date note: cycle label/closes-on come from `get_cycle_context()` (site tz via `timezone.now()`); freeze time in cases that assert the label so month boundaries can't flake.

Unlinked dispatch is already covered by `tests/core/events/discord_commands_spec.py`; the definition-flags case above is the contract.

## 10. Open / deferred (out of scope)

- **Voting from Discord** (casting/updating the ballot via command options or buttons) — deferred; the page owns the form and its validation. This command is read-only by design.
- **"New votes since last snapshot" section** (`compute_new_votes_since`) — moved in the refactor but not surfaced in the embed; one graph keeps the reply scannable.
- **Funding-dollar projections** (`calculate_results` pools) — the hub admin tabs own money views; the member dashboard shows points only, matching the member-facing page.
- **Guild logo emojis / custom Discord emojis** per guild — no plumbing exists; medals + names are enough.
- **Refactoring `plfog/dashboard.py`'s own annotate to reuse the lifted function** — only its comment is touched; consolidating it is a separate cleanup.
