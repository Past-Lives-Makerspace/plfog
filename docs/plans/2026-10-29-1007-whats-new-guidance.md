# 1007: What's New by role, New! badges, and a Guidance level on every PR

Issue: https://github.com/Past-Lives-Makerspace/plfog/issues/1007 (parts 1 to 3). Clips are #1008, the Help
Center freeze and retirement #1009, the tab redesign #1010.
Branch: fog/guidance-record (part 1); parts 2 and 3 branch from main after each merge.
Brief (Felix, 2026-10-29): tell Guild Staff, Members and Admins what is new for them, on the thing itself and
in one What's New place by role; a per-feature button that opens the same thing filtered; short moving clips
for major features; a level of how much teaching a PR owes, declared in the PR; the current changelog stays.

## Words

- **What's New**: the modal at `/whats-new/`, three tabs: For Members, For Guild Staff, For Admins.
- **New! badge**: the small mark after a label that says this thing shipped recently and you have not looked.
- **entry**: one member-facing changelog entry (a fragment or a `history.json` row), as today.
- **feature key**: `<area>.<slug>` matching `core.help_registry.KEY_PATTERN`, registered in `core/guidance.py`.
- **guidance**: how much teaching a PR ships: `none`, `link`, `clip`, `tour`. In the fragment and in the PR body.
- **audience**: who an entry is for: `members`, `guild-staff`, `admins`, or a finer key that rolls up to one of
  those three (`guild-leads`, `orientors`, `treasurers`, `class-reviewers`, `equipment-managers` to Guild
  Staff; `instructors` to Members). `internal` stays what it is: no entry.
- **clip**: a short recorded walkthrough (#1008). **tour**: a Guided Tour in `core/tours.py`.
- **seen**: a member has looked at an entry (opened What's New while it was listed, or clicked its badge).

## Decisions (locked)

- D1. Fragments and `changelog/history.json` are the only source of entries. No `Release` model.
- D2. `plfog/changelog.py` accepts `audience` as a string (`members`, `internal`, unchanged) or a list of audience
  keys; `internal` cannot sit in a list. `feature` must be a registered key; `guidance` defaults to `none` and
  requires `feature` when set to anything else. Unknown keys still fail. `as_entry` emits `feature`,
  `guidance` and the normalised audience list.
- D3. `core/guidance.py` is import-light (dataclasses, no Django at import), so the two CI scripts can import
  it the way they import `plfog/changelog.py`. Per key: `title`, `url_name` plus optional `url_kwargs`
  resolver (the `Tour.entry_url_name` shape), `steps` (tuple of short lines), `clip` (slug or None),
  `tour` (key or None), `help_key` (or None). A spec checks every key against `KEY_PATTERN`, every `tour`
  against `TOURS`, every `help_key` against `HELP_KEYS`.
- D4. The PR body line is `**Guidance:** <level>` under `**Area:**`. `check_pr_description.py` requires it,
  limits it to the four levels, and fails when it differs from the `guidance` of the fragment the PR adds
  (the one `A` file under `changelog.d/`). `check_changelog_fragment.py` fails `clip` without a registered
  clip slug and `tour` without a registered tour key.
- D5. Audiences of a viewer come from `request.view_as`: `members` is `sees_spotlight`; `guild-staff` is
  `staffed_guilds` non-empty or `is_guild_officer`; `admins` is `is_fog_admin` or any `AdminCapability`;
  finer keys from `GuildStaffMembership.role`, `can_review_classes`, `EquipmentStaffMembership`,
  `Member.is_instructor`. One function, `core/whats_new.py: audiences_for(request)`.
- D6. Seen state is `Member.whats_new_seen` (`JSONField`, list of entry slugs, default list), pruned on
  every write to slugs inside the window. The window is `core/whats_new.py: NEW_DAYS = 30`.
- D7. What's New loads on demand: `GET /whats-new/` (members only, guests excluded) returns the modal body;
  `?tab=` picks the tab, `?feature=<key>` limits to the key and its children (`guild` covers `guild.*`).
  The body is htmx-swapped into `components/modal.html`'s body, as FRONTEND.md shows. Rendering the body
  writes every listed slug as seen. `POST /whats-new/seen/<key>/` marks one key's slugs seen (badge click).
- D8. All three tabs always render for every member; Open it shows only when the viewer is in the entry's
  audiences. Entries newest first, grouped by date; a finer audience shows as a chip on the entry.
- D9. `{% new_badge "<key>" %}` and `{% whats_new_button "<key>" %}` live in `core/templatetags/guidance.py`
  and read one per-request object (`request.whats_new`, built lazily by a context processor) so a page costs
  one query however many badges it holds.
- D10. The badge rides inside the tab label; `core/templatetags/tab_picker.py` carries `.pl-new` into the
  phone picker's item label and shows a dot on its summary when any item has one. No second tab component.
- D11. Animations (Felix, 2026-10-29: "once every 10s is enough"): the badge pops in once and sweeps its light once per 10 s; the button pulses and wiggles once per 10 s, only while unseen; everything is off under `prefers-reduced-motion`. Look approved from the mockup on #1007: gold pill tilted left-up, 88% opaque, sparkles button labelled on Home and an icon elsewhere.
- D12. The current changelog modal, the version card and the Discord announcement are untouched.
- D13. No new Help Center articles at any level (#1009). `tour` means tour plus clip.
- D14. Backfill: fragments still in `changelog.d/` dated 2026-10-15 or later (56 files) get `feature`, `audience` and
  `guidance`; `release_plan.py` diffs added files only, so the edits announce nothing.

## Parts

### Part 1 of 3: the record (this branch, internal fragment)

1. `plfog/changelog.py`: audience list, `feature`, `guidance`; `as_entry`; specs in `tests/plfog/`.
2. `core/guidance.py` registry with the first keys (at least: `guild.cubbies`, `guild.voting`,
   `guild.orientations`, `guild.reservations`, `guild.wiki`, `admin.tools`, `admin.payment-report`,
   `admin.email-members`, `classes.catalog`, `classes.teach`, `home.door-code`), each with where it lives
   and steps; `tests/core/guidance_spec.py`.
3. `.github/scripts/check_changelog_fragment.py` and `check_pr_description.py`, with their specs.
4. `.github/pull_request_template.md`: the Guidance line.
5. Docs: `changelog.d/README.md`, `CONTRIBUTING.md`, `STANDARDS.md` §12, `.github/bot-review-prompt.md`
   (one blocker: a members fragment names its feature and guidance, the body line matches, clip and tour
   materials exist, no Help article added), `CONTEXT.md` words, `FRONTEND.md` (badge and button rules,
   written now so part 3 builds to them), `docs/HELP_AUTHORING.md` (frozen: no new articles, see #1009),
   `docs/GUIDANCE.md` (levels and what each ships; recording details arrive with #1008),
   `.claude/skills/drive/` (the builder writes the level), `CODEBASE_INDEX.md`.
6. Backfill (D14).

### Part 2 of 3: What's New (migration: `whats_new_seen`)

`core/whats_new.py` (entries for a viewer, by tab, by feature; seen writes and pruning), `hub` view and
URLs, `templates/hub/whats_new/` (modal body, entry partial with the clip block stubbed as text), the Home
button in `_home_header_actions.html` with the unseen dot, the context processor flag, CSS. Mockup approved
before this part opens.

### Part 3 of 3: the badge and the button

Template tags, CSS and a 30-line script (badge click posts seen, removes the badge; button pulse),
`tab_picker` support, placements: guild section tabs, Admin Tools cards and header, guild statbar, Home
quicklinks, sidebar feature links.
