# #427 The card focus sliders say which axis can move

Closes #427. One small PR: `static/js/card_focus.js`, `templates/classes/_components/card_focus_field.html`, `static/css/hub.css`, one e2e spec.

## The diagnosis, confirmed from the code

The two card frames show the photo cover fitted: the frame's `.cls-media` is a fixed 150px tall, the laptop frame and the phone frame have different widths, and the photo is scaled so it covers the frame and cropped on one axis only. Only that axis has slack, so only that slider can move anything. For a photo that is not wider than the frame's own ratio (nearly every photo: a 16:9 photo in a card frame wider than 16:9 is width limited), every bit of slack is vertical and the Left and right slider writes a value nothing can show. The CSS is applied, exactly as QA saw; there is nothing for it to move.

Since #558 and #585 the frames have two paths, and the slack is computed the same way on both:

- **Boxed** (the host dragged a crop box, `cardFocus.box` and `cardFocus.natural` are set): `render()` already computes `s = max(frameW / box.w, frameH / box.h)`. Horizontal slack is `box.w * s - frameW`, vertical slack is `box.h * s - frameH`.
- **Plain** (`object-fit: cover` on the frame img, no box): the img's `naturalWidth` and `naturalHeight` stand in for the box: `s = max(frameW / nw, frameH / nh)`, slack `nw * s - frameW` and `nh * s - frameH`.

An axis with less than one pixel of slack in a frame cannot move in that frame.

## What changes

### `card_focus.js`

A new method `measure()` computes, for each of the two Photos step frames (`.pl-card-focus__frame--laptop .cls-media` and `.pl-card-focus__frame--phone .cls-media`), whether each axis has slack, and stores the result on the component as two reactive flags per axis:

- `xMoves`: `"both"`, `"laptop"`, `"phone"` or `"none"`
- `yMoves`: the same four values

A frame with no size (its step is off screen, `clientWidth` 0) keeps its previous answer rather than reporting none. A plain frame whose img has not loaded yet (`naturalWidth` 0) reports nothing until the img's `load` event fires, which calls `measure()` again. `measure()` runs from `init()`, from `followBanner()` (a new box or a cleared one), from `watchHeroPreview`'s sync (a new photo, including the `localSrc` path on a class with no saved hero), on `resize`, on `composer-step-shown`, and once per frame img `load`. It never changes `posX` or `posY` and never writes the hidden input.

`render()` keeps its contract; `measure()` is the only addition to the per frame maths. Factor the shared `s` and slack arithmetic into one small function both call so the two cannot drift.

### `card_focus_field.html`

Each slider row gains:

- `:disabled="xMoves === 'none'"` on the range input (and `yMoves` for Up and down), plus a `pl-card-focus__slider-row--off` class binding on the row so the label dims.
- One short line under the slider, `.pl-card-focus__slider-why`, `x-text` bound to a component method `whyX()` / `whyY()` that returns:
  - `"none"`: "This photo already fits side to side, so only up and down moves it." (and the mirror: "This photo already fits top to bottom, so only left and right moves it.")
  - `"laptop"`: "Moves the laptop card. The phone card already fits side to side." (mirror for up and down)
  - `"phone"`: "Moves the phone card. The laptop card already fits side to side." (mirror)
  - `"both"`: empty string, and the line is hidden (`x-show`).

Match the Banner is unchanged. The slider labels "Up and down" and "Left and right" are unchanged (`tests/e2e/boosted_navigation_spec.py:64` and `class_composer_cropper_spec.py:332` name them). No dashes in any copy.

### `hub.css`

`.pl-card-focus__slider-row--off` dims the label and the range to the muted token (`color: var(--hub-text-muted); opacity: .6`) and `.pl-card-focus__slider-why` is a small muted line (`font-size: .75rem; color: var(--hub-text-muted); margin: .25rem 0 0`). Tokens only; both themes.

## The spec

`tests/e2e/card_focus_slack_spec.py`, marked `e2e`, following `tests/e2e/class_composer_cropper_spec.py` (its `_png` helper, `login_via_code`, `serve_media`, and the way it uploads a hero on a saved class):

1. **Portrait** (a 600 by 900 PNG, no crop box): Left and right is disabled and its line reads "This photo already fits side to side, so only up and down moves it."; Up and down is enabled, and dragging it changes the frame img's computed `object-position` (or `--pl-boxed-top` when boxed) in both frames.
2. **Wide landscape** (a 3000 by 600 PNG, wider than both frames' ratios): both sliders are enabled, no line shows, and each slider moves the image in both frames.
3. **Switching the photo** from portrait to wide landscape with no reload (the instant upload on a saved class) flips Left and right from disabled to enabled.
4. **The `localSrc` path**: on a class with no saved hero, picking the portrait photo in create mode shows the sliders with Left and right disabled.

Judge the e2e the CI way: `DATABASE_URL="postgres://plfog:plfog@localhost:5433/plfog" .venv/bin/pytest tests/e2e/card_focus_slack_spec.py -m e2e --no-cov -o addopts="" -q`.

`tests/e2e/class_composer_cropper_spec.py` and `tests/e2e/boosted_navigation_spec.py` still pass.

## Out of scope

The hero placement tool (`static/js/hero_placement.js`): check whether it has the same shape of problem and say so in the PR body, do not change it. The card's aspect ratio, the catalog layout, and replacing the focal point picker with a cropper (the hero cropper exists for that).
