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
  double-wire a node. `rich-editor-init.js` keys off a `data-rte-ready` attribute; a `data-*`
  ready key is the same idea and the house pattern.
- **Once per document:** the `close-marker-edit` listener on `document.body`, **and
  `initDropZones`**. A module-scope boolean, or a top-level block that runs a single time.

`initDropZones` is the one to look at twice. Its name reads like the others, but it binds nothing
to the swapped markup: it is three delegated listeners on `document`
(`static/js/space_map_editor.js:227-237`) that resolve `.cls-image-upload-zone` from
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
