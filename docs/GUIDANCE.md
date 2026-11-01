# Guidance: how a change teaches itself

Every PR says how much teaching it ships, once in its changelog fragment and once in its PR
body, and the portal does the rest: What's New lists the change by role, a New! badge marks the
thing itself, and the feature's button opens the entry. Nobody writes an announcement by hand
and no PR adds to or edits the Help Center (#1009). The issue is #1007; the shots are #1050, which
superseded the recorded clips of #1008.

## The four levels

| level | what ships | when |
|---|---|---|
| `none` | The changelog entry. Nothing to open. | A fix, a copy change, a tweak across the portal, anything internal. |
| `link` | The entry with **Open it**, which goes where the feature lives. | A feature members can find once they know it exists, with nothing new to be shown. |
| `shots` | `link`, plus two or three captioned screenshots the entry walks through, one per step. | The diff adds a button, field, tab or flow to a template: anything a member has to be shown where to press. |
| `tour` | `shots`, plus a Guided Tour (`core/tours.py`) started from the entry. | A whole new surface with several stops, for a role that has not seen it before. |

A `tour` is a tour plus shots. The `fragment` check requires the tour key and HEADs every shot
on the media bucket. No level adds or edits a Help Center article; the Help Center is frozen
(`docs/HELP_AUTHORING.md`). `clip` was a level until #1050 and is none now: a fragment that still
says it fails the check, which names `shots`.

## Where it is written

**The registry**, `core/guidance.py`, holds what never changes between releases, once per
feature key: `title`, where it lives (`url_name`, with `url_kwargs` for a member's own guild
and `query` for a page's tab), `steps` (2 to 5 plain lines a member follows), `tour` (a key in
`TOURS`) and `help_key` (the matching Info View key, when one exists). Keys are
`<area>.<slug>` under `core.help_registry.KEY_PATTERN`; an area key (`guild`) covers
everything under it. `tests/core/guidance_spec.py` holds every key, tour, help key and URL to
its registry.

**The fragment**, in `changelog.d/`, holds what the release changed, names who it is for and
what it belongs to, and at `shots` or `tour` lists the steps it walks through, in order
(`changelog.d/README.md`):

```toml
audience = ["members", "guild-staff"]
feature = "guild.cubbies"
guidance = "shots"

[[shots]]
caption = "Open your guild's page and press the Cubbies tab."

[[shots]]
caption = "Tap an open cubby. The sheet shows what you pay each month."
frame = "phone"
```

The files are `guidance/<fragment-slug>/step-1.png`, `step-2.png` and so on on the media bucket,
derived from the position, so a step cannot be misnumbered; a shot names no file. `frame =
"phone"` is the one other key, for phone-only UI (the section picker).

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
`check_changelog_fragment.py` fails `tour` on a feature with no tour registered, and fails a
`shots` or `tour` fragment whose picture is not on the bucket, naming the first missing URL
(it HEADs `R2_PUBLIC_URL/guidance/<slug>/step-N.png`; the URL is a repository variable, required
in CI and skipped out loud on a local run). The review bot blocks a members fragment that
plainly belongs to a feature and names none, and one at `none` or `link` when the diff adds a
button, field, tab or flow to a template: that is `shots`.

## Picking the level

1. Is there a feature? A change to one thing members use names its key. A fix spread across
   the portal, a sign in change, a copy change: `none`, with no `feature`.
2. A feature members can find once they know it exists, with nothing new to be shown: `link`.
   Register the key if it is new, with where it lives and its steps.
3. Does the diff add a button, field, tab or flow to a template? Take the shots (below) and
   declare `shots`.
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
- `help_key`, `tour`: only what exists.

## Taking the shots

**The standard frame, one for everything.** Desktop, dark theme, a 1280 by 800 viewport (never a
full page scroll), PNG, at most 500 KB, with the control the step is about ringed in the New!
gold (`--pl-new-from`, `--pl-new-to`). The phone exception is 390 wide, dark, only for UI that
exists on a phone alone, and the shot says so with `frame = "phone"`. Shots are made from seeded
data on a local server, never production and never with real member names (STANDARDS.md §11).

**Taking them.** `tests/e2e/guidance.py` gives `shoot(page, path, target=None, frame="desktop")`:
it sets the viewport and the theme, rings `[data-guidance-target="<key>"]` when a target is
given, waits for the pictures and screenshots the viewport. Drive the page to the moment in a
Playwright spec (sign in, open the modal, pick the tab), then call it once per step:

```python
from tests.e2e.guidance import shoot

shoot(page, "mockups/screenshots/1050/step-1.png", target="whats-new.next")
```

Name the files `step-1.png`, `step-2.png` and so on; the position is the step number.

**Uploading.**

```
.venv/bin/python scripts/upload_screenshot.py --guidance <fragment-slug> step-1.png step-2.png
```

writes `guidance/<slug>/step-N.png` on the media bucket, outside the 30-day rule on
`pr-screenshots/`. It requires the names consecutive from 1, refuses a file over 500 KB or not
a PNG with the measured size or type, refuses a step already there unless `--replace`, and
prints both the PR Markdown (one `### Step N` heading and image per shot) and the `[[shots]]`
skeleton with empty captions. Paste the Markdown under Verification and the skeleton into the
fragment, then write the captions: one short sentence each in the house voice (STANDARDS.md
§13), naming the control as it is labelled. The PR description and the fragment carry the same
pictures in the same order.

**The remake rule.** A screen change remakes its shots in the same PR: retake every step the
change touches at the standard frame and upload with `--replace`. An entry whose picture no
longer matches the portal teaches the wrong thing.

**What members see.** At `shots` or `tour` the entry shows a stepper in its media slot: the
picture (16:10), "Step 1 of 3" and the caption under it, Previous and Next, arrow keys, a
swipe on a phone; the steps listed beside the picture with the current one marked, a press on
one jumping to it; on a phone the picture above the list. A tap on the picture opens it full
size. A picture that fails to load hides the picture block and keeps the list, and under
`prefers-reduced-motion` nothing slides.
