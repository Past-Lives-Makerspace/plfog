# #382 — The org map editor is dead on a boosted arrival

**Issue:** https://github.com/Past-Lives-Makerspace/plfog/issues/382
**Origin:** found while building #378's boosted navigation fix (PR #381). Pre-existing; PR #381
only changed how the shared head scripts load.

## The bug

`static/js/space_map_editor.js:251` does all of its wiring inside:

```js
document.addEventListener('DOMContentLoaded', function () { ... });
```

`templates/hub/org_map_edit.html:175` loads that file `defer` from the **body**. Under `hx-boost`
the body script does re-execute on a boosted arrival — that part is fine — but `DOMContentLoaded`
fired once, on the original document, long before. The listener is registered against an event
that will never fire again, so nothing initialises and the editor is inert: no drag, no add
marker, no drop zones. Hard load the page and it works, which is why this survived.

## The fix

**Do not move the file to `<head>`.** It serves exactly one page. `tests/hub/base_scripts_spec.py`
pins `HEAD_ORDER` for the scripts every hub page needs, and this is not one of them. Leave that
tuple alone.

The in-repo precedent for a body script that must survive boost is
`static/js/rich-editor-init.js:129-131`:

```js
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", window.plRteInitAll);
} else {
    window.plRteInitAll();
}
```

Boot the editor the same way: run now when the document is already parsed, wait for
`DOMContentLoaded` only when it genuinely has not fired yet.

## The trap: one of those listeners must NOT re-run

The boot block ends with:

```js
document.body.addEventListener('close-marker-edit', function () {
    window.dispatchEvent(new CustomEvent('close-modal', { detail: 'marker-edit' }));
});
```

`hx-boost` swaps the body's **contents**. The `<body>` element itself persists across a boosted
navigation, so a listener bound to `document.body` survives it. Re-running the whole boot block on
every arrival therefore stacks one more `close-marker-edit` handler per visit, and a single saved
marker fires `close-modal` once per accumulated listener.

That is precisely the defect #383 item 2 is filed about, in an adjacent file. Do not introduce it
here while fixing the other half.

Split the two concerns:

- **Per-arrival, idempotent:** everything that queries the swapped markup and binds to the
  elements it finds — `initStage`, `initAddMarker`, `initAddButtons`. Those elements are replaced
  by the swap, so these must re-run; guard them so a double run against the *same* markup cannot
  double-wire a node. `rich-editor-init.js` keys off a `data-rte-ready` attribute, and **do not
  copy that part** — see "the per-node key must not be an attribute" below. The idea is right; the
  storage is not.
- **Once per document:** the `close-marker-edit` listener on `document.body`, **and
  `initDropZones`**. See the guard note below: the obvious mechanism does not work here.

### The once-per-document guard cannot be a module-scope boolean

`static/js/space_map_editor.js` is an IIFE, opening `(function () {` at line 18 and closing
`})();` at line 263. Under `hx-boost` the body script re-executes on every boosted arrival, so the
whole IIFE body runs again in a **fresh scope**. A `var bound = false;` inside it is reconstructed
as `false` on each arrival and guards nothing; the listener stacks exactly as if there were no
guard at all. A test that visits the page only once cannot tell the two implementations apart,
which is how this would ship.

The flag has to live somewhere that outlives a script re-execution:

- **A property on `window`.** This is already the house pattern: `rich-editor-init.js` assigns
  `window.plRteInitAll` precisely so the reference survives re-execution.
- **A `data-*` key on a persistent element** — `document.documentElement` or `document.body`.
  `hx-boost` replaces the body's contents, not the `<body>` element itself.

### The per-node key must not be an attribute either

**This section replaces an earlier claim in this document that a `data-*` ready key on the swapped
elements "is correct and does not need to survive anything", and that the `data-rte-ready` pattern
"still stands". That was measurably false and it shipped as a review blocker on PR #437.**

htmx builds its history snapshot by serializing the body's innerHTML, and it re-executes the
scripts it restores. An attribute is markup, so it is captured in that snapshot: press Back and the
restored nodes arrive **already claimed**, `boot()` runs, finds every node taken, and binds
nothing. The editor comes back dead, which is this issue's own bug one navigation later.

Use a property on the element instead — `element[key] = true`. A property is not markup, so it
never serializes, and it dies with the node it belongs to. Restored nodes come back unclaimed and
wire up normally. Measured on the real page: after Back the restored nodes are claimed fresh and a
dragged marker moves in the database.

A property is also strictly safer under cloning, which the attribute version was not:
`cloneNode()` copies attributes but not properties, so an attribute key could hand a clone a false
"already wired" stamp.

The trap this document originally fell into is worth naming, because it is the one `CLAUDE.md`
warns about. The pre-fix editor was dead after Back too, for a different reason
(`DOMContentLoaded`). Seeing the same symptom on both sides looked like proof the cause lay
elsewhere. Two causes can each be sufficient for one symptom; identical symptoms prove nothing
about cause. Measure the mechanism, not the outcome.

`initDropZones` is the one to look at twice. Its name reads like the others, but it binds nothing
to the swapped markup: it is three delegated listeners on `document`
(`static/js/space_map_editor.js:227-248`) that resolve `.cls-image-upload-zone` from
`event.target` at drop time. `document` outlives a boosted swap exactly as `document.body` does.
Re-running it stacks three more listeners per arrival, and because the `drop` handler assigns
`input.files` and then dispatches a `change` event, one dropped file fires that `change` once per
accumulated listener. Put it with `close-marker-edit`, not with the markup-querying inits.

One more thing worth seeing while you are in there, not a bug to fix in this PR unless it falls
out for free: `initAddMarker(root)` takes a `root` but queries `[data-add-marker]` across the
whole document (`static/js/space_map_editor.js:194`), so with two `.pl-map-editor` roots on a page
it would bind each button twice. There is one root in practice. If your per-arrival guard keys off
the button rather than the root, this stops being reachable at no extra cost.

## What done looks like

1. Arriving at the org map edit page through a boosted sidebar link leaves the editor fully
   interactive, with no hard refresh.
2. A hard load of the same page is unchanged.
3. Arriving twice does not stack `close-marker-edit` listeners: saving a marker closes the modal
   once, not once per visit.
4. A Playwright scenario in `tests/e2e/boosted_navigation_spec.py` that arrives at the map editor
   **through a real boosted click** and asserts it is interactive. `page.goto()` does a full load
   and cannot see this bug, which is the whole reason the existing specs missed it. Use the
   file's `_watch_for_errors(page)` helper (`tests/e2e/boosted_navigation_spec.py:58`) to assert
   no uncaught page errors and no Alpine warnings, the way
   `it_leaves_the_card_focus_and_cropper_alive_with_no_alpine_errors` does.

## Out of scope

- `rich-editor-init.js`'s accumulating listener. That is #383 item 2 and ships in the next PR of
  this round. Fix the shape here; do not reach into that file.
- Any change to `HEAD_ORDER` or to which scripts `hub/base.html` loads.
- Any redesign of the map editor's behaviour. This is a boot bug.
