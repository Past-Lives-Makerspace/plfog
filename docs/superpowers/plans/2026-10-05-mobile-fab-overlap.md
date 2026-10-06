# Floating buttons stop covering each other on phones

Round: 2026-10-05 round, PR 1 of 2.

**User story.** As a member reading a guild page on my phone, when I scroll down, I would like to read the floating "Member" pill (or tap "Join") and still reach the feedback bubble. Currently the yellow feedback bubble sits on top of the pill, so it reads "✓ Mem" and the right half of a Join pill is unreachable.

**Summary.** On phone widths, the guild page's floating Member/Join pill and the Settings page's back to top button sit just above the feedback bubble instead of under it.

## Current behavior

- `templates/hub/base.html` renders `.hub-feedback-fab` (52px circle) for signed in members; `static/css/hub.css` shows it only at `max-width: 768px`, pinned `right: safe + 1rem; bottom: safe + 1rem; z-index: 95`.
- `templates/hub/partials/_guild_join_fab.html` renders `.pl-guild-join-fab`, pinned `right: 1.25rem; bottom: safe + 1.25rem; z-index: 90`, revealed past 400px of scroll. Same corner, lower z-index, so the bubble covers it (screenshot from Felix, Pixel width, guild page).
- `.pl-scroll-top` (44px circle, `templates/hub/user_settings.html`) uses the same corner and collides the same way.

## Expected behavior

- At `max-width: 768px`, when the feedback bubble is on the page, `.pl-guild-join-fab` and `.pl-scroll-top` sit stacked above it: same right edge as the bubble, bottom = the bubble's bottom + 52px + 0.75rem gap (keep `env(safe-area-inset-bottom)` in the sum).
- When the bubble is not on the page (signed out, public view, framed preview, or wider than 768px) both keep today's position. Prefer one rule keyed on the bubble's presence (`body:has(.hub-feedback-fab)` inside the media query) over per-template classes.
- Nothing else moves: the bubble keeps its place, the pills keep their look, reveal and z-index.
- If the `.hub-content` bottom padding no longer clears the taller stack, raise it so the last button on a page is not covered.

## Acceptance criteria

- [ ] At 390px wide, a member scrolled down a guild page they belong to sees the whole "Member" pill and the whole feedback bubble, and their bounding boxes do not intersect (Playwright e2e under `tests/e2e/`, measuring `boundingBox()` of both).
- [ ] Same for a non member's "Join <guild>" pill, which is clickable (the click opens the join modal).
- [ ] At 390px on Settings, the back to top button and the bubble do not intersect.
- [ ] At 1280px wide (no bubble) the guild pill's computed `bottom` is unchanged from today.
- [ ] Screenshot at 390px of a guild page scrolled down, under `mockups/screenshots/`.

## Out of scope

Moving or restyling the feedback bubble itself; the classes portal's floating elements; any desktop layout change.

## Files

`static/css/hub.css`, a new e2e spec under `tests/e2e/`.
