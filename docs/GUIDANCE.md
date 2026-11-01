# Guidance: how a change teaches itself

Every PR says how much teaching it ships, once in its changelog fragment and once in its PR
body, and the portal does the rest: What's New lists the change by role, a New! badge marks the
thing itself, and the feature's button opens the entry. Nobody writes an announcement by hand
and no PR adds a Help Center article (#1009). The issue is #1007; clips are #1008.

## The four levels

| level | what ships | when |
|---|---|---|
| `none` | The changelog entry. Nothing to open. | A fix, a copy change, a tweak across the portal, anything internal. |
| `link` | The entry with **Open it**, which goes where the feature lives. | The default for a feature members can find once they know it exists. |
| `clip` | `link`, plus a short recorded walkthrough played from the entry. | A feature with a few steps that are easier watched than read: a new tab, a flow with a modal, a tool with settings. |
| `tour` | `clip`, plus a Guided Tour (`core/tours.py`) started from the entry. | A whole new surface with several stops, for a role that has not seen it before. |

A `tour` is a tour plus a clip. The `fragment` check requires the tour key today and will
require the clip from #1008, when a feature can register one. No level adds a Help Center
article; the Help Center is frozen (`docs/HELP_AUTHORING.md`).

## Where it is written

**The registry**, `core/guidance.py`, holds what never changes between releases, once per
feature key: `title`, where it lives (`url_name`, with `url_kwargs` for a member's own guild
and `query` for a page's tab), `steps` (2 to 5 plain lines a member follows), `clip` (a slug,
#1008), `tour` (a key in `TOURS`) and `help_key` (the matching Info View key, when one exists).
Keys are `<area>.<slug>` under `core.help_registry.KEY_PATTERN`; an area key (`guild`) covers
everything under it. `tests/core/guidance_spec.py` holds every key, tour, help key and URL to
its registry.

**The fragment**, in `changelog.d/`, holds what the release changed, and names who it is for
and what it belongs to (`changelog.d/README.md`):

```toml
audience = ["members", "guild-staff"]
feature = "guild.cubbies"
guidance = "link"
```

`audience` names the What's New tabs the change is for, `members`, `instructors`, `guild-staff`
and `admins`, and any finer guild staff key (`guild-leads`, `orientors`, `treasurers`,
`class-reviewers`, `equipment-managers`), which lists under For Guild Staff with a chip;
`changelog.d/README.md` has the table.

**The PR body** carries one line under `**Area:**`:

```
**Guidance:** link
```

CI holds the three together: `check_pr_description.py` fails a body without the line, with a
value outside the four, or with a value that differs from the fragment the PR adds;
`check_changelog_fragment.py` fails `clip` on a feature with no clip registered and `tour` on
one with no tour. The review bot blocks a members fragment that plainly belongs to a feature
and names none.

## Picking the level

1. Is there a feature? A change to one thing members use names its key. A fix spread across
   the portal, a sign in change, a copy change: `none`, with no `feature`.
2. New feature, or an existing one with a new tab, button or flow: `link`. Register the key if
   it is new, with where it lives and its steps.
3. Would a member need to be shown it? Record a clip (below) and declare `clip`.
4. A new surface with several stops for a role: write the tour too and declare `tour`.

The level is a promise the PR keeps: nothing in it is written later.

## Registering a feature

Add a `Guidance(...)` to `_GUIDANCE_LIST` in `core/guidance.py`:

- `key`: `<area>.<slug>`. Areas in use: `home`, `guild`, `wiki`, `calendar`, `classes`,
  `admin`, `feedback`, `account`.
- `title`: the name members see for it, Title Case.
- `url_name` and, when the page needs arguments, `url_kwargs` (a function taking a `Member`;
  `_guild_slug` and `_staffed_guild_pk` cover the guild page and its edit page). `query` lands
  on a tab (`{"tab": "cubbies"}`).
- `steps`: 2 to 5 lines in the house voice for members (STANDARDS.md §13), each true of merged
  code. Name buttons and tabs as they are labelled.
- `help_key`, `tour`, `clip`: only what exists.

## Recording a clip

Arrives with #1008.
