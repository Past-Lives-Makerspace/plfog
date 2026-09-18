# #383 — Back then sidebar click throws; the rich editor stacks listeners

**Issue:** https://github.com/Past-Lives-Makerspace/plfog/issues/383
**Origin:** two pre-existing findings from the PR #381 review. Both reproduce on main before that
PR. Two unrelated defects that happen to share a trigger, so keep them as two commits.

---

## Item 1 — Back then a sidebar click throws `_x_dataStack` of null

### The sequence

A boosted navigation, browser Back (an htmx history restore), then a click on any sidebar link:

```
pageerror: Cannot read properties of null (reading '_x_dataStack')
```

### Where it comes from

`templates/hub/base.html:120` is the sidebar nav:

```html
<nav class="hub-sidebar__nav" ... hx-boost="false"
     @click="if ($event.target.closest('a') && window.innerWidth <= 768) sidebarOpen = false">
```

Two things are true at once. `hx-boost="false"` means the click itself is a full page load, which
is why the navigation still works and the error is cosmetic today. And `@click` is an Alpine
directive whose expression reads `sidebarOpen` off an ancestor `x-data` scope.

htmx restores history from a cached HTML snapshot. The restored markup carries the `@click`
attribute, but Alpine's runtime state was never re-established on that tree, so the click handler
evaluates an expression and walks up for a data stack that is not there. The error fires from the
restored snapshot's Alpine tree during the unload.

**This is a hypothesis, not a finding.** It is consistent with the symptom and the markup, but it
has not been observed under a debugger. **Write the failing Playwright scenario first and confirm
the mechanism before writing a fix**; if the real cause turns out to be different, fix the real one
and say so in the report rather than making this document true.

### Direction

`static/js/hub_boot.js` is the home for one-time wiring under boost and already owns the
`htmx:beforeSwap` and `alpine:init` rules; its header comment explains why it is loaded once from
the head. The two candidate shapes are re-initialising Alpine on `htmx:historyRestore`, or
cleaning Alpine's internals at `htmx:beforeHistorySave` so the snapshot is inert markup. Pick
whichever the repro supports and explain the choice.

An uncaught error on a common path is worth removing even when it is currently harmless: it is
noise in front of every future real error on that path.

---

## Item 2 — `rich-editor-init.js` accumulates an `htmx:afterSettle` listener per boosted arrival

`static/js/rich-editor-init.js:134` registers at file top level:

```js
document.addEventListener("htmx:afterSettle", window.plRteInitAll);
```

`templates/_components/rich_editor_assets.html:9` loads that file from the **body**, on six hub
pages. `document` survives a boosted swap, so every boosted arrival at one of those pages adds
another listener, and `plRteInitAll` then runs once per accumulated listener on every subsequent
settle. `plRteInitAll` is idempotent (keyed on `data-rte-ready`), so this is waste rather than
breakage.

### The fix is the guard, NOT the relocation

The issue offers two options: "load it once from the head, or guard the registration." **Take the
guard.** The head option is unsafe as written, and the reason is in the template's own comment
(`templates/_components/rich_editor_assets.html:2-5`):

> the widget's own init script assumes Quill is already loaded above it

`rich-editor-init.js` is loaded immediately after `quill.min.js` in the same body include and
depends on that ordering. Moving only the init script to the head puts it above its own
dependency. Moving Quill to the head as well would load a large vendored library on every hub page
to serve six of them, and would mean editing `HEAD_ORDER` in `tests/hub/base_scripts_spec.py`.
Neither is warranted by a listener leak.

Guard the registration so it binds once per document, in the shape the rest of the file already
uses for `window.plRteInitAll`.

**Not with a module-scope boolean.** `rich-editor-init.js` is an IIFE, opening `(function () {` at
line 16 and closing `})();` at line 135. The body script re-executes on every boosted arrival, so
the whole IIFE body runs again in a fresh scope and a `var bound = false;` inside it is rebuilt as
`false` each time. It would guard nothing while looking correct on a single visit.

Put the flag where it survives re-execution: a property on `window` (the pattern this very file
already relies on for `window.plRteInitAll`), or a `data-*` key on `document.documentElement`.
#382 ships first and establishes the shape; reuse it rather than inventing a second one.

Whatever you write, the test must arrive at the page **twice** through real boosted navigation. A
single-visit assertion cannot distinguish a working guard from a broken one.

---

## What done looks like

1. Back then a sidebar click logs no uncaught page error. Navigation still works exactly as now.
2. Repeated boosted arrivals at a rich-editor page leave exactly one `htmx:afterSettle` listener
   bound from this file.
3. The rich text editor still initialises on: a hard load, a boosted arrival, and an htmx swap
   that brings a new editor into an already loaded page. All three, verified, because the guard
   is one `if` away from disabling the third.
4. Coverage for both, at the level each admits. Item 1 needs a Playwright scenario in
   `tests/e2e/boosted_navigation_spec.py` using its `_watch_for_errors(page)` helper
   (`tests/e2e/boosted_navigation_spec.py:58`) and a real Back. Item 2 can be asserted by counting
   bindings in the browser, or by a file-level spec in the manner of
   `tests/hub/base_scripts_spec.py` if the runtime count proves awkward. Say which you chose.

## Corrected note: #382 fixed the map editor's Back path; do not inherit the earlier claim

An earlier draft of this document said the map editor was dead after Back on both pre-PR main
and the #382 branch, and concluded from that identical symptom that the cause lay in htmx's
restore path rather than in either file's boot. **That conclusion was wrong, and it was wrong in
a way worth naming**, because it is the reasoning error `CLAUDE.md` warns about under "Retest the
premise before you inherit a conclusion from it."

Two causes can each be sufficient for the same symptom. Pre-PR main died after Back because
`DOMContentLoaded` never fires again. The #382 branch removed that cause and, in the same commit,
installed a second one: the per-node ready keys were `data-` attributes, htmx serializes the body's
innerHTML into its history snapshot and re-executes the restored scripts, so `boot()` ran, found
every node already claimed, and bound nothing. Identical symptom, different cause, and "identical
on both sides" proved nothing about either.

Caught in adversarial review of PR #437 and fixed there: the ready key is now a property on the
element rather than an attribute, so it never serializes and dies with the node. Measured on the
real page, not a harness: after Back the restored nodes are claimed fresh and a dragged marker
actually moves in the database. `tests/e2e/boosted_navigation_spec.py` carries
`it_survives_the_browser_back_button`, which fails against the attribute version.

**What this means for item 1 below.** The map editor is no longer evidence for anything about the
restore path, so do not start from it. The sidebar `_x_dataStack` error is still a hypothesis and
still needs its own repro before any fix. If you find yourself concluding "the cause must be the
restore path because X is also broken there", stop and measure X.

## Out of scope

- `space_map_editor.js`. Its sibling bug is #382 and ships earlier in this round; rebase onto it
  and reuse the once-per-document shape that PR establishes rather than inventing a second one.
- Moving anything into `<head>`, or any edit to `HEAD_ORDER`.
- The sidebar's mobile close behaviour itself. Item 1 removes an error; it does not change what
  the `@click` does.
