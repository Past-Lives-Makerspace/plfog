# `/members` Discord command — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-21
**Surface:** Discord (interactions platform); read-only companion to the FOG hub member directory (`/members/`).
**Related:** `2026-07-13-discord-interactions-foundation.md`, `2026-07-13-discord-member-commands.md`, `2026-07-17-guide-command.md`, `2026-07-21-discord-voting-command.md`.
**Dependency:** the app member directory is being **login-gated in a separate PR**. This command's privacy story leans on that: an ephemeral reply to a *linked member* is equivalent to a logged-in member viewing the directory page. Ship (or land) that PR first.

---

## 1. Summary

A linked member types `/members` in the Past Lives Discord and gets an ephemeral, **page-by-page visual browse of the member directory** — one embed card per member with their photo, pronouns, member type, guilds, skills, "open for commissions" note, and public contact info, plus Prev/Next buttons to flip pages and a button to the full directory in the hub. Optional `guild` and `search` options narrow the roster. It shows exactly what the app directory shows — the same visibility filter and the same per-field privacy toggles — and editing stays in the app.

This is also the codebase's **first interactive component** (buttons with `custom_id`s), so it ships a small, generic component layer — routing, a prefix-keyed handler registry, and an UPDATE_MESSAGE builder — that future commands reuse.

### Locked decisions

| Decision | Choice |
|---|---|
| Dispatch policy | `requires_link=True`, `ephemeral=True`, `defer=False`, `scope="guild"`. Contact info is member-only data; ephemeral-to-a-linked-member ≡ the (login-gated) app directory. Query is a handful of prefetches over ≤5 rows — no defer. |
| Privacy parity | **Exactly** the app directory's filter: `status=ACTIVE` AND (`show_in_directory=True` OR the must-show roles Q — admin / guild officer / guild lead / instructor), `hub/views.py:314-322`. **No admin bypass** (the app's `is_admin` sees-everyone branch is a web-only affordance; the command always applies the member filter). Per-field `is_public()` gates (`membership/models.py:700`) applied per card. |
| Contact info | Included when public — email / phone / Discord handle / custom `MemberContact` rows flagged `show_in_directory` — same rows the app card shows (`templates/hub/member_directory.html:122-150`). |
| Options | `guild` (optional; **static choices** built by an `options_builder` mirroring `_guild_choices` `hub/discord_commands.py:113` — value = guild `slug`, ≤25 choices, no-choices key when zero guilds) and `search` (optional string; feeds `MemberQuerySet.search_skills`, `membership/models.py:223` — matches names and approved skill names). |
| No channel auto-detect | Deliberate divergence from `/info`: omitting `guild` means **the whole directory**, even when run inside a guild channel. Browsing everyone must never require leaving the channel. |
| Page shape | **One embed per member card, 5 per page**, + 1 footer embed = 6 embeds (10-embed cap gives headroom). Ordered by `full_legal_name`, same as the app (`hub/views.py:351`). |
| Thumbnail | Profile photo when `is_public("profile_photo")` AND the URL is absolute. Prod R2 URLs are public, unsigned, absolute (`plfog/settings.py:360-386`: `querystring_auth: False` + `custom_domain`); local `FileSystemStorage` yields relative `/media/…` URLs Discord can't fetch → skip the thumbnail. No-photo cards simply have no thumbnail (`Member.initials` `:771` is a web-CSS affordance, not reused). |
| Pagination | Net-new **generic component infra** (§5.2): type-3 routing in `core/views.py`, a `custom_id`-prefix registry in `core/events/discord_commands.py`, a type-7 `update_message()` builder in `core/events/discord_interactions.py`. Stateless: every click **re-runs the same queryset fresh** from the `custom_id`; data may shift between clicks (acceptable — page is clamped). |
| `custom_id` scheme | `members:<page>:<guild_slug or ->:<search>` — ≤100 chars, search truncated (§5.3). |
| Empty state | Friendly copy + suggest clearing the search; never a bare stub. Errors → the standard `error_reply()`. |
| Handler home | `membership/discord_commands.py` (embed style mirrors `/info` `:156-247`; truncation via `truncate()` `core/events/discord_replies.py:57`). |
| Scale / limits | ~610 members, ~hundreds active/listed, 21 guilds. Discord caps: 4096/embed description, 25 fields, 10 embeds, **6000 chars total per message** — per-card cap keeps 5 cards + footer under it (§6.1). |
| Go-live | Re-run `register_discord_commands` post-deploy (guild scope → instant). `/guide` lists it automatically. |

## 2. What already exists (reuse, don't reinvent)

| Need | Existing thing | Location |
|---|---|---|
| The directory queryset to mirror (filter + ordering) | `member_directory` view — ACTIVE + (`show_in_directory` OR must-show Q), `order_by("full_legal_name")` | `hub/views.py:302-352` |
| The same must-show Q, already duplicated once | `Guild.roster_members()` ("mirrors member_directory") | `membership/models.py:1475` |
| Per-field privacy gate | `Member.is_public(field)` (default-public on missing key) + `DIRECTORY_TOGGLEABLE_FIELDS` | `membership/models.py:700,502` |
| Search | `MemberQuerySet.search_skills(text)` (name or approved-skill icontains, `.distinct()`) | `membership/models.py:223` |
| N+1-free card data | the view's `select_related("membership_plan","user")` + prefetches: primary `EmailAddress` → `_primary_emailaddresses`, `guild_memberships__guild`, `skills__skill__category`, `contacts(show_in_directory=True)` → `visible_contacts` | `hub/views.py:335-351` |
| Card content + gates to mirror | directory card template — pronouns gate (incl. the `"prefer not to share"` skip), contact rows, guild badges, skills, commissions block | `templates/hub/member_directory.html:58-177` |
| Approved skills accessor | `Member.approved_skills` | `membership/models.py:709` |
| Custom contact rows | `MemberContact` (`label`, `value`, `show_in_directory`, ordered by `sort_order`) | `membership/models.py:1043` (`as_link` `:1076` is HTML — **not** reused; Discord gets plain text, it auto-links URLs) |
| Command registry + dispatch (unlinked gate, error wrap) | `SlashCommand`, `register()`, `dispatch()`, `resolve_member()`, `_link_url()` | `core/events/discord_commands.py:37,82,198,116,174` |
| Reply builders | `reply(content, ephemeral=True, embeds=…, components=…)` (components already pass through `:88`); `unlinked_reply()` `:117`; `error_reply()` `:138` | `core/events/discord_interactions.py` |
| Interactions view (signature check covers ALL types; only types 1/2 routed today) | `discord_interactions` — type 3 currently falls into the empty-200 ack | `core/views.py:125-151` |
| Guild-choices `options_builder` pattern (value=slug, 25-cap, zero-guild edge) | `_guild_choices` / `_guild_dropdown_option` | `hub/discord_commands.py:113`; `membership/discord_commands.py:206` |
| Formatting helpers | `option_value`, `hub_url`, `truncate` | `core/events/discord_replies.py:28,41,57` |
| "Which guild?" reply for an unresolvable explicit guild | `guild_not_specified_reply()` | `core/events/discord_replies.py:91` |
| Link-button row (`style: 5`) | `_fog_ping`'s `button_row` | `core/events/discord_commands.py:243-248` |
| Test fixtures | `linked_member` (`tests/core/events/conftest.py:24`); `MemberFactory`, `GuildFactory`, skill/contact factories (`tests/membership/factories.py`) | — |
| Directory URL | `hub_url("hub_member_directory")` | `hub/urls.py:10` |

Gaps to close: (a) one shared queryset method so the visibility filter stops being copy-pasted (§5.1), (b) the generic component layer (§5.2), (c) the handler + card builder (§5.4).

## 3. Where the code lives

```
membership/models.py                  # + MemberQuerySet.directory_visible() (lifts the must-show Q); Guild.roster_members + hub view call it
hub/views.py                          # member_directory uses directory_visible() (behavior identical)
core/views.py                         # discord_interactions: route type 3 → dispatch_component
core/events/discord_commands.py       # + ComponentHandler, register_component(), dispatch_component()
core/events/discord_interactions.py   # + update_message() (type-7 builder)
membership/discord_commands.py        # + /members handler, card/footer builders, component handler, options builder
tests/core/events/members_command_spec.py         # new — handler + pagination + component cases
tests/core/events/discord_commands_spec.py        # + describe_dispatch_component (generic infra)
tests/core/events/discord_interactions_spec.py    # + update_message builder case
tests/core/views/discord_interactions_view_spec.py # + type-3 routing case
tests/membership/… (existing member model spec)   # + directory_visible() cases
```

## 4. Data model

None. Read-only feature over existing rows. No migration.

## 5. Business logic

### 5.1 One source of truth for "who is listed" — `MemberQuerySet.directory_visible()`

The ACTIVE + (`show_in_directory` OR must-show) filter currently exists twice (`hub/views.py:314-322`, `Guild.roster_members` `membership/models.py:1475`); the command would be a third copy. Lift it:

```python
def directory_visible(self) -> MemberQuerySet:
    """Active members listed in the directory: opted in, or holding a must-show role."""
    must_show = (
        models.Q(fog_role=Member.FogRole.ADMIN)
        | models.Q(fog_role=Member.FogRole.GUILD_OFFICER)
        | models.Q(led_guilds__isnull=False)
        | models.Q(instructor_slug__gt="")
    )
    return self.filter(status=Member.Status.ACTIVE).filter(models.Q(show_in_directory=True) | must_show).distinct()
```

- `hub/views.py` keeps its admin-sees-everyone branch (`is_admin` → plain ACTIVE filter) but the non-admin path calls `directory_visible()`; `Guild.roster_members()` becomes `Member.objects.directory_visible().filter(guild_memberships__guild=self)`. Behavior identical; existing hub/roster specs prove it.
- The Discord command calls `directory_visible()` unconditionally — **parity by construction**, and no admin bypass ever leaks into Discord.

### 5.2 Generic component infra (net-new; first `custom_id` buttons in the codebase)

**(a) Routing — `core/views.py:147-151`.** After the type-2 branch:

```python
if interaction["type"] == 3:  # MESSAGE_COMPONENT (button/select click)
    return JsonResponse(dispatch_component(interaction, request))
```

Signature verification already runs first for every type; nothing else in the view changes (unknown future types still fall through to the empty 200).

**(b) Registry — `core/events/discord_commands.py`** (next to `SlashCommand`; same autodiscovery — handlers live in the already-imported `<app>/discord_commands.py` modules):

```python
@dataclass(frozen=True)
class ComponentHandler:
    """One custom_id namespace: everything before the first ':' routes to `handler`."""
    prefix: str                       # e.g. "members" — no colon
    handler: Handler                  # (interaction, member) -> reply dict (usually type 7)
    requires_link: bool = True

_COMPONENT_REGISTRY: dict[str, ComponentHandler] = {}

def register_component(handler: ComponentHandler) -> None: ...   # loud on duplicate prefix, mirrors register() :82
```

**(c) Dispatch — `dispatch_component(interaction, request) -> dict`** (mirrors `dispatch()` `:198` step for step):

1. `custom_id = interaction["data"]["custom_id"]`; `prefix = custom_id.split(":", 1)[0]`.
2. Unknown prefix → log warning + `error_reply()` (registered-message drift, e.g. a stale message after a rename).
3. `member = resolve_member(interaction)`; `requires_link` and `member is None` → `unlinked_reply(_link_url(request))`. The clicker of an **ephemeral** message is always the original invoker, who was linked at invoke time — but they may have **unlinked between clicks**, so the re-check is real, not ceremony.
4. Handler wrapped in try/except → `error_reply()` (never a 5xx to Discord).

`error_reply()`/`unlinked_reply()` are **type 4** — a fresh ephemeral message. That's valid as a component response and deliberately does *not* clobber the browsable message with an error.

**(d) Type-7 builder — `core/events/discord_interactions.py`** (peer of `reply()` `:88`):

```python
def update_message(content: str, *, embeds: list[dict] | None = None, components: list[dict] | None = None) -> dict:
    """A type-7 (UPDATE_MESSAGE) response — edits the message the clicked component sits on in place."""
    data: dict = {"content": content}
    if embeds is not None:
        data["embeds"] = embeds
    if components is not None:
        data["components"] = components
    return {"type": 7, "data": data}
```

No `flags`: an ephemeral message stays ephemeral on update; flags are immutable. Note: `send_followup()` (`:169`) doesn't pass `components` through — irrelevant here (`defer=False`, we respond inline), flagged so the next deferred-command author knows.

### 5.3 The `custom_id` scheme (≤100 chars, stateless)

```
members:<page>:<guild_slug or ->:<search>
e.g.  members:2:woodshop:joinery      members:3:-:
```

- `page` — 1-based int. `guild` — the slug, or `-` for "all". `search` — last segment, so it may itself contain colons; **parse with `custom_id.split(":", 3)`** and require exactly 4 parts + a positive int page. Anything else (wrong arity, non-int, page < 1) → `error_reply()`.
- **Budgeted truncation, computed once per invocation:** the slash handler truncates the search term to `min(40, 100 - len("members:9999::") - len(slug))` before *querying or encoding* — so page 1 and every subsequent page run the **same** query, and every generated id fits 100 chars by construction (slug + 4-digit page budget). The component handler re-uses whatever search survives in the id verbatim.
- Stateless by design: each click re-parses, re-counts, re-queries. Membership may shift between clicks; the requested page is clamped to `[1, page_count]` (§6.7). No server-side pagination state, ever.

### 5.4 The `/members` handler — `membership/discord_commands.py`

**Options builder `_members_options()`** — two options: the guild dropdown (same shape as `_guild_dropdown_option()` `:206` — value = slug, `required=False`, 25-cap, no `choices` key when zero active guilds; description *"Filter to one guild — omit to browse everyone."*) and `{"name": "search", "description": "Match a name or skill.", "type": 3, "required": False}`.

**Shared page builder `_members_page(guild, search, page) -> dict`** (the one function both entry points call; returns `{"embeds": …, "components": …, "total": N}`):

1. `qs = Member.objects.directory_visible()`; `+ .filter(guild_memberships__guild=guild)` when filtered; `+ .search_skills(search)` when searching.
2. Apply the view's exact efficiency block (`hub/views.py:335-351`): `select_related("membership_plan", "user")` + prefetches for primary `EmailAddress` (`_primary_emailaddresses`), `guild_memberships__guild`, `skills__skill__category`, and `contacts(show_in_directory=True) → visible_contacts`. `order_by("full_legal_name")`. **Zero queries per card** beyond the page fetch.
3. `total = qs.count()`; `page_count = max(1, ceil(total / 5))`; `page = min(max(1, page), page_count)`; slice `[(page-1)*5 : page*5]`.
4. Build 5 card embeds (§6.1) + the footer embed (§6.2) + the component rows (§6.3).

**Slash handler `_members(interaction, member)`** — `cast("Member", member)` (requires_link guarantees non-None, mirror `/schedule-orientation`'s comment); read + truncate options (`option_value`, §5.3 budget); explicit `guild` slug resolved via `Guild.objects.filter(is_active=True, slug=slug).first()` — an unresolvable value (only possible via the zero-guild free-text edge) → `guild_not_specified_reply()`; `total == 0` → the empty reply (§6.4); else `reply("", ephemeral=True, embeds=…, components=…)`.

**Component handler `_members_component(interaction, member)`** — parse the id (§5.3; malformed → `error_reply()`); re-resolve the guild slug (`-` → None; a slug that no longer resolves to an active guild → `error_reply()` — a vanishing guild mid-browse is a genuine edge, logged); rebuild via `_members_page`; `total == 0` → type-7 update to the empty-state embed (§6.5); else `update_message("", embeds=…, components=…)`.

**Registration** (module scope, after `/schedule-orientation`):

```python
MEMBERS = SlashCommand(
    name="members",
    description="Browse the member directory — profiles, skills, and contact info.",
    handler=_members,
    options_builder=_members_options,
    requires_link=True,
    ephemeral=True,
    defer=False,
    scope="guild",
)
register(MEMBERS)
register_component(ComponentHandler(prefix="members", handler=_members_component, requires_link=True))
```

No side effects anywhere: no writes, no notifications, no activity log. Pure read.

## 6. UI / UX — the ephemeral browse, every state

The only "screen" is one ephemeral message: up to 5 member-card embeds + 1 footer embed + button rows. Not a web UI — no theme/mobile/form concerns — but **every state is specified** below. Card content and gates mirror `templates/hub/member_directory.html` line for line.

### 6.1 A member card (literal mockup — one embed)

```
┌──────────────────────────────────────────────┐
│ Maya Okafor                        [photo ▣] │  ← title = display_name; thumbnail = profile photo
│                                              │
│ Standard Member · she/her · Joined Mar 2024  │
│ 🛠️ Guilds: Woodshop, Fiber Arts              │
│ 🎨 Skills: Joinery, Weaving, Natural dyes,   │
│    Bookbinding, +3 more                      │
│ 💼 Open for commissions! — Custom furniture, │
│    small batches                             │
│ ✉️ maya@example.com                          │
│ 📞 (415) 555-0100                            │
│ 💬 maya_makes                                │
│ 🔗 Website — https://mayamakes.example       │
│ 🔗 Instagram — @maya.makes                   │
└──────────────────────────────────────────────┘
```

Line-by-line rules (each line **omitted entirely** when its data is empty or private — a card never shows an empty labeled row):

| Line | Source | Gate |
|---|---|---|
| Title | `truncate(display_name, 80)` (a 255-char legal name would blow the 6000 combined cap) | always |
| Thumbnail | `profile_photo.url` | photo set AND `is_public("profile_photo")` AND `url.startswith("http")` (§6.9/§6.10) |
| Type · pronouns · joined | `get_member_type_display()`, `pronouns`, `join_date` as `"Joined %b %Y"` | pronouns: set, ≠ `"prefer not to share"`, `is_public("pronouns")` (template `:91`); joined: `join_date` set |
| 🛠️ Guilds | prefetched `guild_memberships` → guild names | any memberships |
| 🎨 Skills | `approved_skills` names, first 6 + `+N more` | `is_public("skills")` and any approved |
| 💼 Commissions | `"Open for commissions!"` + `commission_note` when set | `is_public("skills")` AND `open_for_commissions` — on the web card the commissions block is **nested inside the skills gate** (`member_directory.html:164-180`); there is no independent commissions visibility key, so skills-hidden must hide this too |
| ✉️ Email | `primary_email` (O(1) via the prefetch) | non-blank and `is_public("email")` |
| 📞 Phone | `phone` | set and `is_public("phone")` |
| 💬 Discord | `discord_handle` | set and `is_public("discord_handle")` |
| 🔗 Contacts | each prefetched `visible_contacts` row as `Label — value` (plain text; Discord auto-links URLs; `as_link` is HTML and not reused) | rows exist (already filtered `show_in_directory=True`) |

`about_me` is **deliberately omitted** (5 bios would blow the 6000-char budget; the full card is one click away via the footer button). Char budget: each card title passes `truncate(display_name, 80)` and each description `truncate(description, 950)`; 5 × (950 + 80) + footer ≈ 5.3k < 6000 total even with 255-char legal names, and every line is far under the 4096 description cap. No `fields` are used (description lines render tighter at card size).

### 6.2 The footer embed (always the last embed)

```
┌──────────────────────────────────────────────┐
│ Page 2 of 7 · 34 members                     │
│ Edit what you share from the app —           │
│ Settings → Directory.                        │
└──────────────────────────────────────────────┘
```

First line reflects the **filtered** total (`34 members` = matches, not the whole roster). When a guild or search filter is active, append it so the state is legible: `Page 2 of 7 · 34 members · Woodshop · “joinery”`.

### 6.3 The buttons (one action row)

```
[ ◀ Prev ]  [ Next ▶ ]  [ Open the full directory ]
```

- **Prev / Next**: `style: 2` (secondary), `custom_id`s per §5.3 targeting `page-1` / `page+1`, **`disabled: true` at the bounds** (Prev on page 1, Next on the last page). Distinct ids always (clamped targets never collide).
- **Open the full directory**: `style: 5` link button, `url = hub_url("hub_member_directory")` — present in **every** state, including empty. This is also the "see everything / edit" path; the command never mutates anything.
- **Single page** (`page_count == 1`): Prev/Next are **omitted entirely** (no dead disabled pair); the link button row remains.

### 6.4 Empty state — slash invocation (`total == 0`)

Type-4 ephemeral reply, no embeds, link button only:

> No members match{ " in **Woodshop**" }{ " for **“joinery”**" }. Try a broader search, or run `/members` without the filters to browse everyone.

Never a dead end: the copy names the fix and the button opens the full directory.

### 6.5 Empty state — component click (roster shifted under the browser)

Same copy, but as a **type-7 update** replacing the cards (embeds → one embed carrying the copy above; components → link button row only). The stale page never lingers.

### 6.6 Unlinked

- **Slash:** `requires_link=True` → `dispatch()` returns the standard `unlinked_reply()` connect prompt; handler never runs. Nothing to build.
- **Component click:** the clicker is the invoker (ephemeral), but if they unlinked since, `dispatch_component` step 3 returns `unlinked_reply()` (a fresh ephemeral message; the old browse message is left as-is).

### 6.7 Page drift (stateless re-query)

Clicks re-run the queryset fresh; counts may have changed. The requested page is clamped to `[1, page_count]` — clicking "Next → page 7" after the roster shrank to 6 pages lands on page 6, never an error, never an empty slice with members remaining. Acceptable-by-decision: adjacent clicks can show a shifted window.

### 6.8 Errors

- Handler exception (slash or component) → caught by the dispatcher → standard `error_reply()`. Never a 5xx to Discord.
- **Malformed `custom_id`** (wrong arity, non-int or < 1 page) → `error_reply()` + a logged warning.
- Unknown component prefix (stale message after a rename) → `error_reply()` + warning.
- Unresolvable guild: slash option → `guild_not_specified_reply()` (lists the guilds); component slug that stopped resolving → `error_reply()` (logged; genuine edge).

### 6.9 Photo missing / private

No thumbnail key on the embed — the card renders text-only (Discord collapses the space cleanly). No initials avatar: `Member.initials` (`membership/models.py:771`) is a CSS-circle affordance with no image form. Every other line renders normally.

### 6.10 Local dev (relative media URLs)

`FileSystemStorage` yields `/media/…` — not fetchable by Discord and invalid in an embed. The `url.startswith("http")` guard (§6.1) skips the thumbnail, so local testing degrades to text-only cards instead of broken embeds. Prod R2 URLs (`settings.py:360-386` — `custom_domain`, `querystring_auth: False`) are absolute, public, unsigned: no expiry problem inside an old message.

### State matrix (summary)

| State | Response | Cards | Pager | Link btn |
|---|---|---|---|---|
| Happy, multi-page (mid) | type 4 / type 7 | 5 + footer | Prev+Next enabled | ✔ |
| First page | 〃 | ≤5 + footer | Prev disabled | ✔ |
| Last page | 〃 | remainder + footer | Next disabled | ✔ |
| Single page | 〃 | ≤5 + footer | **omitted** | ✔ |
| Empty (slash) | type 4, copy §6.4 | — | — | ✔ |
| Empty (click, drift) | type 7, copy §6.5 | 1 message embed | — | ✔ |
| Unlinked (slash / click) | `unlinked_reply()` | — | — | connect btn |
| Handler error / malformed id / unknown prefix | `error_reply()` | — | — | — |
| Photo missing / private / relative URL | normal card, no thumbnail | — | — | — |
| Page > page_count after drift | clamped to last page | 〃 | 〃 | ✔ |

### UX-completeness check (Discord surface)

- Every state specified: happy, bounds, single-page, two empty flavors, unlinked ×2, error ×3, photo edge ×2, drift. ✔
- Primary action obvious: Prev/Next for browsing, one always-present link button for everything else. ✔
- No dead ends: empty states name the fix; disabled bounds can't misfire; errors are friendly and ephemeral. ✔
- Nothing half-built: browse here, **edit in the app** — the footer says so explicitly. ✔
- Privacy: filter + field gates identical to the app; no admin bypass; ephemeral + linked-only. ✔

## 8. Build order (each phase ships green: full suite + lint + mypy)

1. **The lift.** `MemberQuerySet.directory_visible()`; rewire `hub/views.py` (non-admin path) and `Guild.roster_members()`. Pure refactor — existing directory/roster specs prove zero behavior change; add direct queryset cases.
2. **Component infra.** `update_message()` builder; `ComponentHandler` + `register_component()` + `dispatch_component()`; type-3 routing in `core/views.py`. Specs for all three (generic — no `/members` yet).
3. **The command.** Options builder, card/footer/button builders, `_members`, `_members_component`, both registrations; `tests/core/events/members_command_spec.py`.
4. **Housekeeping.** Bump `plfog/version.py` VERSION + member-facing changelog entry, e.g. *"Browse the member directory right from Discord: `/members` shows profile cards — photos, skills, guilds, and contact info members have chosen to share — with Prev/Next paging and optional guild and search filters. Private, linked-members-only, and it always respects each member's directory privacy settings."*

> Post-deploy: re-run `register_discord_commands` (guild-scoped → registers instantly). `/guide` picks it up automatically.

> Spec only — do not build until approved. Land (or coordinate with) the directory login-gating PR first.

## 9. Testing

BDD `*_spec.py`, **`describe_*` / `it_*` only**, factory-boy, run in the `plfog-web` image, ≥98% coverage gate. Fixtures: `linked_member` (`tests/core/events/conftest.py:24`), `MemberFactory`/`GuildFactory` + skill/contact factories.

**Phase 1 — `directory_visible()`** (member model spec): includes ACTIVE+opted-in; excludes opted-out standard member; includes each must-show role despite `show_in_directory=False` (admin, guild officer, lead via `led_guilds`, instructor via slug); excludes non-ACTIVE regardless; `.distinct()` holds for a multi-guild lead. Hub directory + `roster_members` suites stay green untouched — that is the refactor's proof.

**Phase 2 — infra** (`discord_interactions_spec.py`, `discord_commands_spec.py`, `discord_interactions_view_spec.py`):
- `update_message` returns `{"type": 7}`, includes `embeds`/`components` only when passed, never a `flags` key.
- `register_component` raises on duplicate prefix; `dispatch_component`: unknown prefix → `error_reply`; `requires_link` + unlinked → `unlinked_reply`; handler exception → `error_reply`; happy path returns the handler's dict.
- View: signed type-3 POST returns the dispatcher's JSON; types 1/2 unchanged; unsigned still 401.

**Phase 3 — `tests/core/events/members_command_spec.py`:**
- `describe_members_command_definition` — `(requires_link, ephemeral, defer, scope) == (True, True, False, "guild")`; options builder yields the guild dropdown (slug values, 25-cap, choices key absent at zero guilds) + the search option.
- `describe_members_privacy` — opted-out member absent; must-show member present despite opt-out; each `is_public()`-off field's line absent while the rest of the card renders (email/phone/discord/skills/pronouns/photo, driven via `directory_visibility`); `"prefer not to share"` pronouns skipped; commissions line absent when `is_public("skills")` is off even with `open_for_commissions=True` (+ note) — web parity, the block nests inside the skills gate; non-visible `MemberContact` absent, visible one rendered `Label — value`; **no admin bypass** (an admin invoker still doesn't see opted-out members).
- `describe_members_cards` — ordering by `full_legal_name`; 6 embeds on a full page (5 cards + footer); footer text `Page 1 of 2 · 7 members` (+ filter suffixes); skills capped at 6 + `+N more`; commissions line; thumbnail present for an absolute photo URL, absent for a relative one and for no photo; per-card description ≤ 950; total message chars < 6000 with 5 maximal cards built on **255-char legal names** (titles truncate to 80).
- `describe_members_filters` — guild option narrows to that guild's roster; search hits preferred name / legal name / approved skill (and not unapproved); unresolvable slash guild → `guild_not_specified_reply`.
- `describe_members_pagination` — page-1 Prev disabled / last-page Next disabled with correct target `custom_id`s (`members:2:-:` shape); single page → no pager, link button kept; search truncation budget: a 200-char search yields ids ≤ 100 and page 1 + page 2 use the same truncated query.
- `describe_members_component` — click returns type 7 with the requested page's cards; page beyond `page_count` clamps to the last page; roster emptied between clicks → type-7 empty state with the link button; malformed ids (`members:x:-:`, `members:0:-:`, `members:2:-`) → `error_reply`; stale/inactive guild slug → `error_reply`; unlinked-since-invoke clicker → `unlinked_reply`.
- **Query count** — `django_assert_num_queries` around a 5-card page build: constant (count + page + prefetches), no per-card queries.

## 10. Open / deferred (out of scope)

- **Editing anything from Discord** (profile, visibility toggles, contacts) — the app owns editing; the footer points there.
- **Per-member deep-link profile pages** — none exist in the app; the link button targets the directory page.
- **Autocomplete (interaction type 4)** for the search/guild options — static choices + free text are enough at 21 guilds; autocomplete is a separate infra slice.
- **The "Listed" → "Public" label rename** — shipping in a separate PR; this spec uses the current field names only.
- **`about_me` on cards** — omitted for the char budget (§6.1); revisit only if cards feel thin in practice.
- **Jump-to-page / page-number select menu** — Prev/Next is enough at ≤ ~40 pages; the registry supports select menus later without change.
- **Reusing the component layer for `/voting` etc.** — the layer is deliberately generic; adopting it elsewhere is each command's own spec.
