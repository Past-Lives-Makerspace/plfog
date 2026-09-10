# The wiki's first-run screens are too busy

**Date:** 2026-09-09
**Status:** spec, built alongside
**Trigger:** "what even are these sections? this entire page /wiki/new/howto/ is just too busy
and overcomplicated... if I don't know what these really are, then no one else is."

The wiki shipped this morning at v1.47.0 and launched empty. v1.49.0 fixed how it *looked*
(an unclosed media query, browser-default buttons, scaffold headings as excerpts). This round
is about what is *on* the screens: both the guild Wiki tab and the create form open by showing
a member machinery built for a wiki with a lot of content, on a wiki with none.

Nothing here changes permissions, stored data, or the schema.

---

## 1. Three cards explaining features nobody can use yet

The guild Wiki tab's right-hand column renders "Overdue For Review", "Searches That Found
Nothing" and "Wanted Pages" unconditionally, each with a paragraph explaining what it is and
then a sentence saying it is empty:

> Nothing overdue. Everything here has been checked recently.
> No failed searches in the last 30 days. Either everything is findable, or nobody looked.
> No requests yet.

On a guild with no pages that column *is* the tab. It teaches a review clock to somebody with
nothing to review, and it sits above the one thing a member came to do.

**Fix:** every card is gated on having rows, decided in `membership/wiki_guild.py` where the
rows are already counted, as `wiki_tab_show_overdue` / `_misses` / `_wanted`. When none of them
renders, the whole column goes and the grid drops to one column (`--solo`) rather than reserving
a third of the page for whitespace.

Two things this must not break, both now covered by tests:

- **The out-of-band swap target.** "Add To Wanted" in the failed-search panel swaps its new row
  into `#wiki-tab-wanted-rows`. So the wanted card also renders whenever the misses panel does,
  even with nothing on it yet — otherwise the target is missing exactly when a lead first uses
  the button, htmx drops the swap, and the request lands in the database with nothing on screen
  to say so.
- **The lead's entry point.** With the wanted card gone, a lead on a guild that has pages gets
  one line instead: "Ask for a page someone should write →". A line, not a card. On a guild with
  no pages at all the empty-state card already carries "+ Add A Wanted Page", so the line is
  gated off there and the two never both appear.

**Two more found by screenshotting the result**, both only visible once the column of empty
cards stopped hiding them:

- **The search row on a guild wiki with no pages.** "Search the Gardeners Guild wiki" over zero
  pages is a control that can only disappoint, and it put a second "+ Start A Page" a couple of
  inches above the empty-state card's own. The whole row is now gated on `wiki_tab_has_pages`,
  and the card gains "Browse the whole wiki →" so the cross-guild "Everything" scope that row
  also offered does not disappear with it.
- **`align-items: stretch` on the search row below 900px.** It made the "+ Start A Page" anchor
  as tall as the entire search form beside it — a solid yellow block the size of a card, and on
  an empty guild the largest thing on the screen. The row is a column at that width now, with
  the action underneath at its natural size.

The same defect exists one level down and is fixed the same way: `/wiki/p/<slug>/` rendered a
whole Quick Answers card reading "No quick answers yet" to a reader who cannot edit. With §2
making Quick Answers optional this becomes the *normal* state of a perfectly good page, so the
card is now skipped entirely for a reader and kept, with its invitation, for an editor.

## 2. The create form asks for a FAQ before it lets you write

`/wiki/new/<kind>/` rendered its cards in this order: **The Basics, Quick Answers, The Page,
Files And Photos** — and Quick Answers arrived pre-seeded with one row per `fact_prompts` entry
from the starter catalogue. For a how-to that is three rows; for a machine, four. Each row is a
`hub-card` carrying a drag grip, two fields with help text, two reorder arrows and a Remove
button. So a member who clicked "Start A Page" met roughly eighteen controls they had not asked
for, standing between the title field and the box they came to type in, under a header that
promises "two fields and whatever you know".

And each row was labelled **"Question: Tools needed"**. "Tools needed" is not a question.

**Fix, three parts:**

1. **Order:** The Basics, **The Page**, Quick Answers, Files And Photos. The writing box is
   second because writing is what the button promised.
2. **Opt-in:** `fact_prompts` is deleted from the starter catalogue entirely and the create form
   opens Quick Answers with zero rows and its own "+ Add A Quick Answer" button. Both optional
   sections open with "Optional." as the first word of their own intro line, which also absorbs
   the empty-list sentence each of them used to print underneath as well.
3. **Honest labels:** the row's key field is **Topic**, not Question, with help text that admits
   both shapes: "Two or three words, or a real question. 'Blade', 'Max width'."

The machine seeder is the one place starter prompts still make sense — it creates a stub nobody
chose to make, where the prompts *are* the content and the ask. Its list moves into that command
as `MACHINE_FACT_PROMPTS` rather than staying in a catalogue field that nothing else reads.

## 3. There was no way to start from nothing

Every card on `/wiki/new/` handed the member a body of headings to delete first. Added an eighth,
last on the grid: **Blank page** — "No sections and no prompts. Start from nothing and write it
your way." Empty body, no prompts, filed under `howto` (the chooser's own "not sure?" hint already
points there and the Kind select is on the form).

Last on purpose: the seven guided cards are the recommendation, and this is the escape hatch.

## 4. Not done

- The starter *bodies* stay. Nobody complained about opening a how-to on three headings, and
  the Blank page card is the answer for somebody who does not want them.
- The Quick Answers feature itself stays. It is genuinely the most useful block on a page that
  has one; it just should not be homework.

## 5. Tests

Every guard fails when its fix is reverted. Two absence tests in the overdue panel specs were
**already vacuous before this round** and are repaired here: they asserted a page was not in a
panel that, once panels are gated on rows, would not render at all — so they would have passed
against an empty string. Each now seeds a second, genuinely overdue page so the panel is on the
screen and the absence means something.

## 6. Changelog

**No new entry.** Same reasoning as v1.49.0 this morning: the wiki's entry shipped at v1.47.0
and is on production, and this is same-day polish on that release. A third Discord post about
the wiki in one day is worse than none. `VERSION` still bumps; a version with no matching entry
announces nothing, which is correct.
