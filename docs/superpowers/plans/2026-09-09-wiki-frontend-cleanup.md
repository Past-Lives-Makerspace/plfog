# Wiki frontend cleanup

**Date:** 2026-09-09
**Status:** spec, for the fog-quick-feature pipeline
**Trigger:** the guild Wiki tab is not showable. Sections collide, buttons touch, the scope
toggle renders as a white system button on the dark theme.

The wiki shipped to production this morning at v1.47.0 and launched empty, so every screen
below is what a member sees on their first visit. Nothing here changes behaviour, permissions,
models' stored data, or the schema. It is layout, theming and one display method.

---

## 1. Root cause of the reported screenshot

`static/css/hub.css:9482` opened `@media (max-width: 768px) {` and never closed it.

- PR #342 (spec D, wiki moderation) added the block. It was the last thing in the file, so
  browsers auto-closed it at EOF: spec D's own rule worked and **nothing looked wrong**.
- PR #343 (spec B, the guild Wiki tab) appended 160 lines of `pl-wp-tab__*` CSS after it.
  All of it fell *inside* the media query, so above 768px none of it applied: no grid, no
  chip styling, no panel padding, no search-row flex. That is the entire reported screenshot.
- PR #344 (class CMS, v1.46.0, already live) then appended `.pl-review-reassure`, which is
  therefore also dead above 768px on the class review screen.

A brace-depth scan of all 23 files in `static/css/` finds exactly one unbalanced file, and
`hub.css` ends at depth +1.

**Fix:** close the block. Then add a lint spec so this cannot recur silently, because the
failure mode is invisible to every existing check — the file parses, the page renders, and
only the viewport width reveals it.

## 2. Browser-default controls on the dark theme

`.pl-btn` (`components.css:867`) and `.hub-btn` (`hub.css:1269`) both set box model, border
and typography but **no `background` and no `color`**. Any usage without a colour variant
therefore falls through to Chrome's default button chrome — measured
`background: rgb(239,239,239)`, `color: rgb(0,0,0)` — which is white-on-dark.

The stylesheet already documents this exact trap for `--ghost`:

> "Nine wiki-moderation controls claimed this modifier while nothing defined it, so every
> 'ghost' control rendered as a bare `.pl-btn` […] indistinguishable from the solid buttons
> beside it"

That fixed the symptom for one modifier and left the trap. Measured across the wiki in dark
mode, **34 controls render as raw browser chrome**:

| Screen | Count | Controls |
|---|---|---|
| `/wiki/p/<slug>/` | 4 | `+ Add A Photo`, `+ Add A Tip`, `Still accurate` |
| `/wiki/new/<kind>/` | 10 | fact/attachment `↑` `↓` reorder, `+ Add A Quick Answer`, `+ Add A File Or Link` |
| `/wiki/p/<slug>/edit/` | 10 | same as above |
| `/wiki/wanted/` | 2 | `Go`, `+ Add A Wanted Page` |
| guild Wiki tab | 4 | `Verify` (one per row) |

Two of those are the exact actions the launch changelog told 200 members to use ("Add a photo
from your phone in about thirty seconds, drop in a tip"), and `Verify` is what spec B is for.

**Rejected fix: changing the base rules.** Giving `.pl-btn` a background looks like the
one-line root fix and is not safe. `.pl-btn--primary` sets no border of its own, so it relies
on the base's `border: 1px solid transparent`; and the light theme would need
`[data-theme="light"] .pl-btn`, whose specificity (0,2,0) outranks every `--variant` (0,1,0)
and would repaint primary, danger and success buttons in light mode. Measured scope also
argues against it: there are 93 bare `hub-btn` usages repo-wide, nearly all outside the wiki
(admin, classes, org map). A base change is a cross-cutting refactor wearing a one-liner's
clothes.

**Fix:** name the right variant at each of the wiki call sites, which is how every other call
site in the repo already works, and make the omission fail the build on this surface (§7.2).
Zero blast radius outside the wiki. 510 controls sampled across 12 non-wiki screens are
re-measured after the change and must be byte-identical.

The two bare non-wiki controls found while sampling (`/meetings/` "Start the agenda",
`/members/` "Apply") are left alone and reported, not silently swept in.

## 3. Empty pages advertise their own scaffold

`WikiPage.lead_text` (`membership/models.py:12760`) flattens the whole body to text:

```python
def lead_text(self, limit: int = 200) -> str:
    text = _source_to_text(self.body)
```

A page created from the scaffold has body
`<h2>What It Does</h2><p></p><h2>How To Use It</h2><p></p>…`, so its card excerpt is the
literal string **"What It Does How To Use It What Goes Wrong Tips From Members"** — the four
section headings run together with no punctuation. This renders on every card on the wiki
home, in search results, in Related Pages and in the guild tab.

The sibling `WikiArticle.lead_text` (`:3511`) already handles this correctly: it splits the
body into blocks and skips heading-only ones, returning `""` when there is no prose.

Because the wiki launched empty, this is the *default* appearance of every page a member
starts, until they type prose into a section.

**Fix:** make `WikiPage.lead_text` skip headings the same way, returning `""` when the page
has no prose yet. `_wiki_card.html:37` already guards on `{% elif page.lead_text %}`, so the
card simply drops the line. Display-only; no migration, no stored-data change.

## 4. Touch targets stop at the guild tab boundary

The ≤900px block raises controls to 48px, but only within `.pl-wp-tab`, `.pl-wp-tab__editor`,
`.pl-wp-tab__switcher` and `.pl-wp-search__empty`. Its own comment states the reasoning:

> "Gloves, dust, bad light. `.pl-btn--sm` sets `min-height: 0` (about 27px)"

`/wiki/p/<slug>/` — the screen a QR sticker at a machine opens, and the most likely phone
surface in the building — is not in that list. Its whole toolbar measures 27px high.

**Fix:** extend the same floor to the wiki page, editor, history and search surfaces.

## 5. Smaller items

1. **Duplicate label and placeholder.** `_guild_wiki_tab.html:19-20` sets the label and the
   placeholder to the identical string ("Search the Events Guild wiki"). Screen readers
   announce it twice and the placeholder does no work. Give the placeholder an example.
2. **Two competing primaries.** `_guild_wiki_tab.html:23` and `:28` put a yellow
   `pl-btn--primary` Search submit and a yellow `pl-btn--primary` "+ Start A Page" in the
   same row. Search is the form's action; starting a page is the surface's action. Demote
   Search to secondary.
3. **Destructive action inline.** `wiki_page.html` renders `Archive` (`pl-btn--danger`,
   measured `rgb(220,38,38)`) between "Add an Official Note" and "Report a Problem" in a row
   of nine equally weighted controls. Separate the destructive action from the routine ones.
4. **Duplicated sentence.** `official_block.access_line` renders twice on
   `/wiki/p/<slug>/` under the *identical* `{% if official_block %}` guard — once bare under
   the page metadata (`wiki_page.html:46`) and again as the labelled `You` row of the
   official facts table (`_wiki_official.html:28`). Drop the bare one.
5. ~~**Orphan line.** "1 version saved." sits outside any card.~~ **Withdrawn — not a
   defect.** `_wiki_byline.html` is a deliberate footer line that normally reads "Started by
   … Last edited by … 1 version saved." It looked orphaned only because the seeded test page
   has no `created_by` or `updated_by`. Left alone.

## 6. Out of scope, recorded not fixed

- The guild page's own tab strip clips at 390px ("Meeti"). It belongs to the guild page
  chrome, not the wiki, and touching it moves five other tabs.
- The six non-wiki templates that also use a bare `.pl-btn` are fixed incidentally by §2's
  base rule; their screens are not otherwise reviewed here.

## 7. Tests

Every guard must fail when the fix is reverted. Vacuous tests are the failure mode this round
already produced once.

1. **`static/css/` brace balance** — parse every stylesheet, strip comments, assert depth
   returns to 0. Proven by reverting §1 and watching it fail.
2. **No bare button on a wiki template** — walk `templates/hub/wiki_*.html`,
   `templates/hub/partials/_wiki_*.html` and `_guild_wiki_tab.html`, and fail on any
   `pl-btn`/`hub-btn` class list carrying no colour variant. Scoped to the wiki on purpose:
   93 bare `hub-btn` usages exist elsewhere in the repo and are not this PR's to fix.
3. **`WikiPage.lead_text`** — a scaffold-only body returns `""`; a body with prose returns the
   prose and not the heading text; a heading followed by prose returns the prose.
4. The blast-radius diff in §2 is a build step, not a committed test: 508 non-wiki controls
   byte-identical before and after.

## 8. Changelog

**No new entry.** The wiki's entry shipped this morning at v1.47.0 and is on production. This
round is same-day polish on that release; a second entry would re-announce the wiki to the
Discord channel hours after the first post. Per the root `CLAUDE.md`, a release that carries
nothing newly member-facing legitimately has no entry at its `VERSION` and announces nothing.
`VERSION` still bumps.
