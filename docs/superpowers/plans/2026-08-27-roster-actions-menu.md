# Roster Actions Menu (Kebab Dropdown) — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-27
**Surface:** book CMS `book.pastlives.test` — class Workspace Registrations + Waitlist tabs, **both** the teach portal and the classes admin (the four tabs share the same two row partials, so one change covers all four).
**Related:** `2026-08-26-roster-waitlist-management.md` (built the actions this reorganizes), `2026-08-26-stripe-refunds-payments-panel.md` (Refund authority model — referenced, untouched).

---

## 1. Summary

The roster Actions column currently stacks up to four inline buttons per row (Send Payment Link, Mark as Paid, Refund, Remove) — it already wraps awkwardly and every future action makes it worse. This replaces the button strip with a single "…" (kebab) button per row that opens a compact dropdown menu of actions. Staff get a tidier table, every action keeps its exact current behavior and permission gate, and new actions land as one more menu line instead of another column-widening button. Two genuinely essential actions are added while we're in here: **View Details** (the registration detail page — today reachable only via the name link, which staff routinely miss) and **Email Student** (a mailto link — staff currently copy the address by hand).

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Pattern | One new reusable component, `templates/components/row_actions.html`: kebab trigger + Alpine dropdown, modeled on the existing `.pl-profile__dropdown`. All inline roster buttons move into it. |
| Scope | Both registration rows and waitlist rows, teach + admin (shared partials — one edit, four tabs). |
| Renames | "Remove" → **"Remove Student"** (roster) and **"Remove from Waitlist"** (waitlist). Menu labels only; the confirm modals' titles/buttons already name the person and consequence and stay as they are. |
| Extra actions | Exactly two: **View Details** and **Email Student**. Nothing else — YAGNI. |
| Gates | Item visibility keeps the exact current per-button gates (`can_manage`, `viewer_has_refund_authority`, status/payment state). No gating changes. |
| Clipping | `.admin-table-wrap` has `overflow: hidden` (it exists for the rounded corners), so an absolutely positioned menu inside the table **will** clip. The menu is `position: fixed`, placed from the trigger's bounding rect on open, flipping upward near the viewport bottom (§6.2). No change to the table wrapper's CSS. |
| Empty menu | Cannot occur: View Details + Email Student render for every viewer who sees the Actions column at all, so the kebab always has at least two items. No disabled-kebab state needed (§6.6). |

## 2. What already exists (reuse, don't reinvent)

All verified against the current tree.

| Need | Existing thing | Location |
|---|---|---|
| Shared roster row (teach + admin + HTMX row-swap target) | `registration_row.html` — actions strip at lines 39–80 in `.pl-roster-actions` | `templates/classes/partials/registration_row.html` |
| Shared waitlist row | `waitlist_row.html` — actions at lines 28–41 | `templates/classes/partials/waitlist_row.html` |
| Shared table shell + Actions `<th>` gate | `class_registrations_table.html` (also the refund-done refresh partial) | `templates/classes/teach/partials/class_registrations_table.html` |
| The four consuming tabs | teach/admin `class_registrations.html` + `class_waitlist.html` — all include the shared partials; **no per-tab action markup exists**, confirming one change covers both surfaces | `templates/classes/{teach,admin}/class_registrations.html`, `…/class_waitlist.html` |
| Dropdown pattern to copy | `.pl-profile` avatar menu: `x-data="{ open: false }"`, `x-show` + `x-transition`, `@click.away`, window-level Escape that refocuses the trigger, `role="menu"`/`"menuitem"`, item + `--danger` + divider variants | `templates/hub/base.html:457-475`, `static/css/hub.css:919-1014` |
| Dropdown theme tokens | `--hub-dropdown-bg` / `--hub-dropdown-border` (defined for both themes) | `static/css/hub.css` `:root` + `[data-theme="light"]` |
| Confirm modals per row (unchanged) | `roster_modals.html` renders `remove-reg-<pk>`, `markpaid-reg-<pk>`, `promote-reg-<pk>` confirm modals + the promote-followup shell, opened via `$dispatch('open-confirm', …)` | `templates/classes/partials/roster_modals.html` |
| Refund modal (unchanged) | `modal_id="refund-modal"` shell on the registrations tabs; buttons `hx-get` the form into `#refund-modal-body` then `$dispatch('open-modal', 'refund-modal')` | `templates/classes/teach/class_registrations.html:21`, admin twin |
| Caller-supplied body inside a component | the `confirm_body_include` param pattern on `confirm_modal.html` — same trick gives `row_actions.html` its menu items | `templates/components/confirm_modal.html`, used in `roster_modals.html:16` |
| Registration detail page (View Details target) | `classes:admin_registration_detail` — gated by `classes_registrations_access_required` + `_scoped_registrations`, so instructors reach it for their own classes; the row's name link already points here unconditionally | `classes/urls.py:120`, `classes/views.py:2886` |
| Views serving these tables (untouched) | `_teach_registrations_context` :1669, `teach_class_registrations_table` :1714, `admin_class_registrations` :2289, `admin_class_registrations_table` :2304 | `classes/views.py` |
| Existing actions-cell CSS (to retire) | `.pl-roster-actions` flex strip | `static/css/components.css:1143-1148` |

**Genuine gaps to close (kept minimal):**
1. No overflow-menu component exists anywhere in `templates/components/` → build `row_actions.html` (§6.1).
2. No menu CSS → one `.pl-row-menu` block in `components.css` (§6.5).
3. No view or model changes at all — this is templates + CSS only.

## 3. Where the code lives

```
templates/components/row_actions.html            # NEW — kebab trigger + fixed-position menu shell
templates/classes/partials/
    registration_row.html                        # actions cell → one row_actions include (modified)
    registration_row_menu.html                   # NEW — the roster row's menu items
    waitlist_row.html                            # actions cell → one row_actions include (modified)
    waitlist_row_menu.html                       # NEW — the waitlist row's menu items
static/css/components.css                        # NEW .pl-row-menu block; DELETE the dead .pl-roster-actions block
classes/spec/views/roster_actions_menu_spec.py   # NEW — menu rendering per row state
classes/spec/views/roster_actions_spec.py        # existing label assertions updated (§9)
```

No `classes/views.py`, `urls.py`, or model changes. Everything stays inside existing coverage/mypy scope.

## 4. Data model

None. No migrations.

## 5. Business logic

None. Every endpoint, gate, confirm modal, and HTMX row-swap contract is reused byte-for-byte; only the element that carries the `hx-*`/`@click` attributes changes from an inline button to a menu item.

## 6. UI / UX

Design language: book CMS shell, theme tokens only, new classes under the `pl-` prefix in `components.css` (shared hub + admin file — these tabs load it already, `.pl-roster-actions` lives there today). Verify **both themes** on every screen below.

### 6.1 The component — `templates/components/row_actions.html`

**Parameters:**

| Param | Required | Meaning |
|---|---|---|
| `menu_include` | yes | Template path rendered inside the menu (inherits the caller's context, so `reg`, `can_manage`, etc. are available) — the `confirm_body_include` pattern. |
| `menu_label` | yes | Accessible name for the trigger, e.g. `"Actions for Jane Doe"`. |

**Markup sketch (structure, not final code):**

```html
<div class="pl-row-menu" x-data="plRowMenu()" @keydown.escape.window="closeAndRefocus()"
     @keydown.tab="open = false">
    <button type="button" class="pl-row-menu__trigger" x-ref="trigger"
            aria-haspopup="menu" :aria-expanded="open" aria-label="{{ menu_label }}"
            @click="toggle()">
        <svg aria-hidden="true" …>⋯ (three-dot glyph, inline SVG)</svg>
    </button>
    <div class="pl-row-menu__menu" x-ref="menu" x-show="open" x-transition.opacity
         x-cloak role="menu" @click.away="open = false"
         @scroll.window.passive="open = false" @resize.window="open = false"
         @keydown.down.prevent="focusNext()" @keydown.up.prevent="focusPrev()">
        {% include menu_include %}
    </div>
</div>
```

`plRowMenu()` is a small Alpine data factory registered once in a `<script>` inside the component guarded by a window flag (the include renders per row; the factory must define once). It owns `open`, the positioning math (§6.2), `closeAndRefocus()` (close + `$refs.trigger.focus()` — only when open, matching the `.pl-profile` pattern), and the roving arrow-key focus over `[role="menuitem"]` children.

**Menu items** are plain `<a>`/`<button>` elements with class `pl-row-menu__item` (danger ones add `pl-row-menu__item--danger`), `role="menuitem"`, written by the caller's `menu_include` partial. **Interaction contract:** every item's `@click` starts with `closeAndRefocus()` and then does its real work — `$dispatch('open-confirm', 'remove-reg-{{ reg.pk }}')`, `$dispatch('open-modal', 'refund-modal')`, or nothing extra for plain links. `closeAndRefocus()` (not a bare `open = false`) because keyboard activation (Enter on a focused item) otherwise hides the focused element and drops focus to `<body>`; returning focus to the trigger is harmless when a modal opens next (the modal takes focus itself) and correct for HTMX items and links. `hx-*` attributes sit directly on the item element and fire normally (HTMX doesn't care that the element lives in a menu); the menu having just closed via Alpine does not detach the element, so in-flight requests complete. When a response swaps the whole row (`hx-target="#reg-row-<pk>" hx-swap="outerHTML"`), the menu goes with it — correct, since the fresh row re-renders the kebab against the new state.

Rule 12 compliance: the menu's `display: block` lives in the `.pl-row-menu__menu` CSS class; the only inline styles the JS writes are `top`/`left`, never `display`.

### 6.2 Positioning — why fixed, and how

`.admin-table-wrap { overflow: hidden; border-radius: 8px; }` (`hub.css:3136-3140`) clips any absolutely positioned descendant, so the `.pl-profile` approach (absolute, `top: calc(100% + 8px)`) would cut the menu off — worst on the last row, where the whole menu falls outside the wrapper. Rather than churn the table wrapper (its `overflow: hidden` is what rounds the row corners), the menu is **`position: fixed`** and placed from the trigger:

- On open, after `$nextTick` (menu must be visible to measure): read `$refs.trigger.getBoundingClientRect()` and the menu's `offsetWidth`/`offsetHeight`.
- **Horizontal:** right-align the menu to the trigger's right edge (`left = rect.right − menuWidth`), clamped to an 8px viewport gutter (`max(8, …)`) so it never overflows a narrow screen.
- **Vertical:** open downward (`top = rect.bottom + 4`); if that would pass `window.innerHeight − 8`, flip upward (`top = rect.top − menuHeight − 4`) — the last-row case.
- **Staleness guard:** a fixed element doesn't track scroll, so any window scroll or resize closes the menu (`@scroll.window.passive` / `@resize.window`). This also covers the `.admin-table-wrap`'s own edges: the menu floats above the wrapper (z-index above the table, below modal overlays) and simply closes if the page moves.
- **Scope of the scroll guard:** window-level only. Scrolls inside a nested scrolling container do not bubble to `window`, so a future reuse of this component inside an `overflow: auto` wrapper would silently lose the guard — acceptable today because the roster tables have no inner scroll (`.admin-table-wrap` is `overflow: hidden`); the limitation and its fix are recorded in §10.
- **iOS Safari:** `position: fixed` coordinates can drift against the visual viewport there (pinch-zoom offsets, the dynamically collapsing toolbar resizing the viewport mid-gesture). The mitigation is the same scroll/resize-dismiss above — any viewport movement closes the menu rather than leaving it misplaced. The manual pass (§9) includes opening a bottom-row menu on a real iPhone.

### 6.3 Registration rows (`registration_row.html`, both Registrations tabs)

The `.pl-roster-actions` div and all four inline buttons are deleted; the cell becomes:

```django
{% include "components/row_actions.html" with menu_include="classes/partials/registration_row_menu.html" menu_label="Actions for "|add:reg.first_name|add:" "|add:reg.last_name %}
```

`registration_row_menu.html` renders, in order (dividers only between non-empty groups — template conditionals, never a stranded rule line):

| # | Item | Gate (exact current) | Behavior (exact current mechanism, moved onto the item) |
|---|---|---|---|
| 1 | View Details | always (column itself already gated `can_manage or viewer_has_refund_authority`) | `<a href="{% url 'classes:admin_registration_detail' pk=reg.pk %}">` |
| 2 | Email Student | always | `<a href="mailto:{{ reg.email }}">` |
| — | divider | any of 3–4 visible | |
| 3 | Send Payment Link | `can_manage and reg.is_unpaid` | `hx-post` `registration_send_payment_link`, `hx-target="#reg-row-{{ reg.pk }}"`, `hx-swap="outerHTML"`, `hx-disabled-elt="this"` |
| 4 | Mark as Paid | `can_manage and reg.is_unpaid` | `@click`: close, `$dispatch('open-confirm', 'markpaid-reg-{{ reg.pk }}')` |
| — | divider | any of 5–6 visible | |
| 5 | Refund **or** Retry Refund | `viewer_has_refund_authority` and (`refund_state == "failed"` → Retry Refund; elif `refundable_cents > 0` → Refund) | danger item; `hx-get` refund form → `#refund-modal-body`, `@click`: close, set "Loading…", `$dispatch('open-modal', 'refund-modal')` |
| 6 | Remove Student | `can_manage` and `status in (confirmed, pending)` | danger item; `@click`: close, `$dispatch('open-confirm', 'remove-reg-{{ reg.pk }}')` |

**Menu contents by row state** (assuming a viewer with both `can_manage` and refund authority; drop the gated rows accordingly for lesser viewers). **The gate table above is normative; this table is illustrative** — `is_unpaid` (CONFIRMED with a balance due) and `refundable_cents` (paid minus refunded) are independent booleans, so combinations compose per the gates:

| Row state | Visible items |
|---|---|
| Confirmed, paid in full, refundable | View Details · Email Student ─ Refund · Remove Student |
| Confirmed, unpaid, nothing collected yet (promoted from waitlist) | View Details · Email Student ─ Send Payment Link · Mark as Paid ─ Remove Student |
| Confirmed, **partially paid** (balance still due AND `refundable_cents > 0`) | View Details · Email Student ─ Send Payment Link · Mark as Paid ─ Refund · Remove Student (all three groups, two dividers) |
| Confirmed, refund previously failed | View Details · Email Student ─ Retry Refund · Remove Student |
| Pending | View Details · Email Student ─ Remove Student (+ Refund when `refundable_cents > 0`) |
| Cancelled / refunded (row at reduced opacity) | View Details · Email Student (+ Retry Refund only if `refund_state == "failed"`) |
| Viewer has refund authority but not `can_manage` | View Details · Email Student (± Refund / Retry Refund) |

Copy notes: all labels above are the exact member-facing strings — Title Case, no dashes, no punctuation. "Email Student" opens the visitor's own mail app; no in-app compose (out of scope, §10).

### 6.4 Waitlist rows (`waitlist_row.html`, both Waitlist tabs)

Live (`waitlisted`) rows swap their two buttons for the same include with `menu_include="classes/partials/waitlist_row_menu.html"`. Items, in order:

| # | Item | Gate | Behavior |
|---|---|---|---|
| 1 | Add to Class | `can_manage` (the column's own gate) | `@click`: close, `$dispatch('open-confirm', 'promote-reg-{{ reg.pk }}')` |
| 2 | View Details | always | link to `classes:admin_registration_detail` |
| 3 | Email Student | always | `mailto:` |
| — | divider | | |
| 4 | Remove from Waitlist | `can_manage` | danger item; `@click`: close, `$dispatch('open-confirm', 'remove-reg-{{ reg.pk }}')` |

Add to Class stays the first item — it is the tab's primary action and must be the first thing the open menu offers (and the first item receives focus on open, so keyboard flow is trigger → Enter → Enter). The promoted stub ("Added to class ✓") and removed stub rows keep their empty actions `<td>` exactly as today — no kebab on a row with nothing to do.

### 6.5 CSS — `.pl-row-menu` block in `components.css`

New classes, theme tokens only, both themes:

- `.pl-row-menu` — `display: inline-flex` anchor wrapper (no `position: relative` needed; the menu is fixed).
- `.pl-row-menu__trigger` — quiet icon button: transparent background, `color: var(--hub-text-muted)`, `border-radius: 6px`, hover/focus-visible → `color: var(--hub-text)` + the same subtle hover wash as `.pl-topbar__theme-toggle` (`rgba(255,255,255,0.06)` dark / `rgba(0,0,0,0.06)` light). Size `2.25rem` square; at `max-width: 768px` bump to `2.75rem` (44px tap target).
- `.pl-row-menu__menu` — `position: fixed; display: block; min-width: 200px; background: var(--hub-dropdown-bg); border: 1px solid var(--hub-dropdown-border); border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.3); overflow: hidden; z-index: 150;` (above the table and `.pl-profile`'s 100; modal overlays sit higher and are never open at the same time anyway — every modal-opening item closes the menu first).
- `.pl-row-menu__item` — mirrors `.pl-profile__dropdown-item` (block, full width, `0.625rem 1rem`, `--hub-text`, hover washes per theme) plus a `:focus-visible` ring for the keyboard path.
- `.pl-row-menu__item--danger` — mirrors `.pl-profile__dropdown-item--danger` (`#e05555`).
- `.pl-row-menu__divider` — 1px `var(--hub-dropdown-border)`, `0.25rem 0` margin.
- `[x-cloak] { display: none !important; }` already exists globally; the menu carries `x-cloak` so nothing flashes pre-Alpine.

Delete the now-dead `.pl-roster-actions` block (`components.css:1143-1148`) once both row partials stop using it (grep confirms these two partials are its only consumers).

### 6.6 States, per the completeness checklist

- **Empty menu:** impossible — View Details + Email Student always render when the Actions column renders at all, so the kebab never opens onto nothing and never needs hiding or disabling.
- **Empty table:** unchanged ("No registrations yet." / existing waitlist empty state).
- **Loading:** menu items that fire HTMX close the menu on click; the existing row-level `.htmx-request` opacity and `hx-disabled-elt` behavior carry over unchanged. The refund item keeps the current "Loading…" placeholder write into `#refund-modal-body` before the modal opens.
- **Error:** unchanged — action endpoints already return a fresh row partial + error toast on stale-state guards; the fresh row re-renders the kebab against reality.
- **Success:** unchanged — row swap + success toast per action; a swapped row's kebab reflects the new state (e.g. after Mark as Paid, the payment items vanish from the fresh row's menu).
- **Concurrent swap while open:** the table-refresh listener (`refund-done` → table reload) or a row `outerHTML` swap replaces the open menu's DOM; the menu disappears with its row — acceptable, the user just acted.
- **One menu at a time (intended):** each row's component owns its own `open` state, and clicking row B's trigger lands outside row A's menu, so A's `@click.away` closes it in the same interaction that opens B. No cross-row coordination needed; verified in the manual pass (§9).
- **No dead ends:** every item either navigates, opens a guarded modal (which has Cancel), or fires a reversible-feeling HTMX action with a toast; Escape and click-away always close the menu.

### 6.7 Keyboard & accessibility

- Trigger: real `<button>`, `aria-haspopup="menu"`, `:aria-expanded`, `aria-label="Actions for <name>"` (the glyph is `aria-hidden` SVG).
- Menu: `role="menu"`; items `role="menuitem"` (on both links and buttons).
- Open (click or Enter/Space on the trigger) → focus moves to the first item after positioning.
- ArrowDown / ArrowUp cycle focus through visible items (small hand-rolled rover in `plRowMenu()` — no Alpine Focus plugin exists in this stack and one function is cheaper than adding it).
- Escape closes and returns focus to the trigger (window-level listener, same as `.pl-profile`).
- Tab closes the menu (WAI-APG menu-button behavior): `@keydown.tab="open = false"` on the component wrapper — **no** `.prevent`, so focus moves on naturally while the menu closes. Without this, a Tab-out would strand the fixed-position menu floating with a stale `aria-expanded` until an unrelated click or scroll. **Do NOT implement this with `@focusout` / blur handlers** — that was the first design and it is a Safari-breaking race: Safari (macOS and all of iOS) does not focus buttons on click/tap, so a mousedown on a menu item blurs the currently focused element, `focusout` fires with `relatedTarget: null`, the containment guard fails, `open = false` runs on mousedown, `x-show` hides the menu before mouseup, and the click never lands — every menu item dead for Safari mouse users and all iOS taps. The keydown approach has zero pointer-event interference. No focus trap — it's a menu, not a modal.
- `:focus-visible` styling on items so the keyboard position is visible in both themes.

### 6.8 Mobile

- 44px trigger tap target at ≤768px (§6.5); the kebab actually *shrinks* the Actions column versus today's wrapped button strip, so the table needs less horizontal room on phones.
- The fixed-position menu with viewport clamping (§6.2) cannot overflow a narrow screen; near the screen bottom it flips upward.
- Scroll-to-dismiss (§6.2) doubles as the mobile escape hatch alongside tap-away.
- Items are full-width 44px-ish rows (padding per §6.5) — comfortably one-handed.

### 6.9 The user-lens walk

An instructor opens their class's Registrations tab: a clean table, one quiet "…" at the end of each row. Click → a menu opens under the button (or above it on the last row), View Details and Email Student on top, payment actions when the person owes, the red Remove Student at the bottom where a destructive action belongs. Click Remove Student → the same confirm modal as today, naming the person and the waitlist consequence. Nothing they could do yesterday is gone; the two things they kept wishing for (jump to the detail page, email the student) are now one click deep. On the Waitlist tab the first menu item is Add to Class — the thing they came to do.

## 7. Notifications / emails / activity

None. No email, notification, or activity changes.

## 8. Build order (phased; each phase ships green)

1. **Component + CSS.** `components/row_actions.html` with the Alpine factory, positioning, and a11y behavior; `.pl-row-menu` block in `components.css`. Verify both themes and the last-row flip by hand on a seeded roster.
2. **Roster rows.** `registration_row_menu.html`; swap the actions cell in `registration_row.html`; update the existing spec assertions that grep for the old inline button labels; new menu-state specs.
3. **Waitlist rows.** `waitlist_row_menu.html`; swap `waitlist_row.html`; specs. Delete the dead `.pl-roster-actions` CSS. Run `tests/template_comment_lint_spec.py` after all template work.

Versioning/changelog are handled at build time per repo convention — deliberately not specced here.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py` under `classes/spec/views/`, `describe_*`/`it_*` (never `context_*`), factory-boy, 100% branch coverage. All rendering is asserted through the real views (teach + admin), not template unit shims — that also proves the shared-partial claim.

**`spec/views/roster_actions_menu_spec.py`** (new)

- `describe_registration_row_menu`:
  - it renders View Details (correct `admin_registration_detail` href) and Email Student (`mailto:` the registrant email) for every row state where the Actions column renders, including cancelled/refunded rows.
  - Gate matrix: Send Payment Link + Mark as Paid appear only for `can_manage` + `is_unpaid`; Refund only for refund authority + `refundable_cents > 0`; Retry Refund only on `refund_state == "failed"`; Remove Student only for `can_manage` + confirmed/pending. Each negative case asserted (viewer with refund authority but not `can_manage` sees exactly View Details / Email Student / Refund).
  - it keeps the exact HTMX contract on the moved items (`hx-post` URL + `hx-target="#reg-row-<pk>"` + `outerHTML` on Send Payment Link; `hx-get` refund-form URL + `#refund-modal-body` target on Refund).
  - it renders the menu label "Remove Student" (and "Remove from Waitlist" on waitlist rows), not a bare "Remove" item.
  - divider logic: no divider renders when a group below it is empty (e.g. a paid row for a `can_manage`-only viewer shows no payment group and no stranded rule).

**Assertion mechanism — scope every assertion to the menu markup, never bare phrases against the whole page.** Full-page substring assertions are unimplementable here: the Paid column's help bubble (`class_registrations_table.html:20`) contains "Mark as Paid" on every render regardless of row state; `roster_modals.html` renders confirm titles and `confirm_button_text="Remove"` on the same page; "Remove Student" contains "Remove" as a substring; and per the changelog-renders-everywhere gotcha, bare-phrase negatives like "Retry Refund" are exposed to unrelated page content (one such assertion already exists at `roster_actions_spec.py:435` — fold it into this mechanism while updating). Therefore every menu spec first **extracts the row's menu region** from the response — parse the rendered row and take the `pl-row-menu` element's HTML (a tiny shared helper in the spec's conftest, e.g. BeautifulSoup/regex slice on `class="pl-row-menu`) — and asserts presence/absence inside that slice only, using tag-bounded forms (`>Mark as Paid</`, `>Remove Student</`) rather than raw substrings. Positive assertions about hrefs/`hx-*` attributes target the extracted item elements the same way.
- `describe_waitlist_row_menu`:
  - it renders Add to Class first, then View Details / Email Student, then Remove from Waitlist as a danger item.
  - promoted stub and removed stub rows render no kebab.
- `describe_component_accessibility` (asserted via rendered HTML): trigger `aria-haspopup`/`aria-label` carries the student's name; menu `role="menu"`; every item `role="menuitem"`; danger items carry the danger class.
- `describe_admin_surface`: the admin Registrations + Waitlist tabs render the identical menu markup (shared partial regression — one representative assertion per tab).

**Updates to existing specs:** `roster_actions_spec.py` (and any teach/admin tab specs) currently assert the inline button labels/classes — update those assertions to the menu items. Behavior specs for the endpoints themselves are untouched (no endpoint changes).

**Template lint:** `tests/template_comment_lint_spec.py` after template work. Mind the `Changelog renders everywhere` gotcha only at build time (no changelog work here).

**Manual pass (both themes, both surfaces):** last-row flip-up, narrow-viewport clamp, scroll-dismiss, Escape refocus, Tab-out closes the menu, opening the refund modal from the menu still loads the form, **one menu at a time** (with row A's menu open, clicking row B's trigger closes A via A's `@click.away` and opens B — intended behavior, verify no flicker or both-open state), and **a bottom-row menu on a real iPhone** (position holds or the menu dismisses cleanly as the Safari toolbar collapses — §6.2's iOS note).

## 10. Open / deferred

- **In-app "email selected students" compose:** out — Email Student is a mailto link; a bulk composer is its own feature if staff ask.
- **Arrow-key type-ahead / Home/End in the menu:** out — Down/Up/Escape/Tab covers the WAI menu-button essentials for a 2–6 item menu.
- **Kebab on other admin tables** (`admin-actions` strips elsewhere): the component is deliberately reusable, but migrating other tables is not this spec — do it when a table actually outgrows its buttons.
- **Scroll guard inside nested scrolling containers:** the dismiss listens on `window` only (§6.2); inner-container scrolls don't bubble, so the first reuse inside an `overflow: auto` wrapper must upgrade the factory to a capture-phase `document` scroll listener (`addEventListener('scroll', close, { capture: true, passive: true })`, removed on Alpine destroy). Not needed for the roster tables today — deferred until that reuse exists.
- **Live tab-count / table refresh behavior:** unchanged and untouched.
- **Confirm modal button copy** ("Remove"): unchanged — the modal title already names the person and consequence; renames live in the menu only.
