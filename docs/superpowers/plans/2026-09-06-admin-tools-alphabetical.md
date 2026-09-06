# Admin Tools: alphabetical order, no Quickstart tiles

**Status:** ready to build
**Surface:** `templates/hub/admin_tools.html` (`/manage/tools/`, view `hub.views.hub_admin_tools`)

## What the user asked for

1. Order the Admin Tools cards alphabetically.
2. Remove the two Quickstart tiles.

## Today

The grid is hand ordered by rough importance, with the two role guides pinned last:

| # | Title | Gate |
|---|---|---|
| 1 | Announcements | `tool_announcements` |
| 2 | Orientations | `tool_orientations` |
| 3 | Manage Members | `tool_manage_members` |
| 4 | Manage Classes | `tool_manage_classes` |
| 5 | Payments | `tool_payments` |
| 6 | Reports | `tool_reports` |
| 7 | Activity | `tool_activity` |
| 8 | Notification Settings | `tool_notifications` |
| 9 | Site Settings | `tool_site_settings` |
| 10 | Push Notification Test | `tool_push_test` |
| 11 | Guild Lead Quickstart | `guide_guild_lead` |
| 12 | Instructor Quickstart | `guide_instructor` |

## Target order

Cards 11 and 12 are deleted. The remaining ten sort by their visible title:

Activity, Announcements, Manage Classes, Manage Members, Notification Settings,
Orientations, Payments, Push Notification Test, Reports, Site Settings.

Each card keeps its own `{% if %}` gate, its icon, its href, and its description
verbatim. Only the order of the blocks changes. Sorting is done in the template by
hand, not computed: the gates differ per card, so a data-driven list would have to
carry ten booleans to buy nothing.

## The three things the removal drags with it

Deleting the tiles is four lines of template. These are the parts that rot if missed.

### 1. Dead view context — `hub/views.py`

`hub_admin_tools` computes two context values that nothing will read:

```python
"guide_guild_lead": can_orient,
"guide_instructor": is_admin
or (member is not None and (member.is_instructor or member.can_create_classes)),
```

Both keys and their comment block go. `can_orient` STAYS — `tool_orientations` still
uses it. `is_admin` stays for the same reason.

### 2. The tour step lies — `core/tours.py`

The admin tour's final step targets the grid and sells the guides as the payoff:

> "Every tool lives on this page, and the Guild Lead and Instructor Quickstart guides at
> the bottom spell out each role. That is the lap."

After this change there are no guides at the bottom. The body is rewritten to describe
the page that will actually be there. The step, its title, and its `navigate` stay.

### 3. The help key is misnamed — `core/help_registry.py`

`data-help-key="admin.quickstart-guides"` sits on the **grid**, not on the tiles, so the
element survives — but both its id and its copy describe content that is leaving:

> "Quickstart guides — Short role guides for guild leads and instructors."

Rename the key to `admin.tools-grid` and rewrite the entry to describe the tools grid.
Three touch points must move together: the registry key, the `data-help-key` in the
template, and the tour step's `target=`. `tests/hub/help_keys_spec.py` walks every
template and fails on a key that is not in `HELP_KEYS`, so a half-done rename is caught.

## Explicitly NOT in scope

**The Quickstart help pages stay.** Only the Admin Tools tiles are removed. Both articles
remain published, seeded, and reachable:

- the Help Center itself (`membership/help_content.py`, slugs `instructor-quickstart` and
  `guild-lead-quickstart`),
- the example guild page (`membership/example_guild.py`),
- the guild-lead and instructor onboarding announcements
  (pinned by `tests/hub/announcement_compose_spec.py`).

So removing the tiles costs one discovery path, not the guides.

## UI/UX completeness

- **Empty state:** unchanged and already correct. Every card is independently gated, and a
  member who reaches this page passes `_can_use_admin_tools`, so at least one card renders.
  Removing two cards cannot empty the grid for anyone: both gates that are leaving are
  strictly weaker than gates that remain (`guide_guild_lead` is `can_orient`, which
  `tool_orientations` also uses; `guide_instructor` includes `is_admin`, which nine other
  cards use). The one population that could have seen ONLY a Quickstart tile is a
  non-admin instructor with no orienting role, and that member is bounced home by
  `_can_use_admin_tools` before the grid renders.
- **No new controls**, no forms, no destructive actions, so no Save, Add, or Delete
  affordances are in play.
- **Responsive/dark mode:** untouched. `.pl-tools-grid` and `.pl-tool-card` are unchanged
  and the cards keep their existing classes.

## Tests

- `tests/hub/admin_tools_spec.py` gains a block asserting the rendered card titles come
  back in alphabetical order for a fog admin (who sees the most cards), and that neither
  Quickstart tile renders for any role that previously got one.
- The alphabetical assertion reads the titles out of the response in document order and
  compares against `sorted(...)`, so it fails on a future card inserted in the wrong place
  rather than pinning a hardcoded list that has to be edited every time a tool is added.
- `tests/hub/help_keys_spec.py` covers the key rename with no edit needed.
- Re-run `tests/core/tours_spec.py` and `tests/hub/tours_spec.py` for the tour step edit.

## Versioning

`VERSION` bumps. No `CHANGELOG` entry: this is the staff tools page, members do not see
it, and a reordered admin grid is not release-note material. An automatic Discord run on a
`VERSION` with no entry posts nothing, which is the correct outcome.
