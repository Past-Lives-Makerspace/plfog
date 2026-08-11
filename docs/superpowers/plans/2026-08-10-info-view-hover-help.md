# Info View Hover Help — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-10
**Surface:** FOG hub `pastlives.test` — every authenticated hub page (phase 1 annotates: guild voting, guild page, guild edit, orientation booking, teach overview, class create, community calendar, member directory, user settings)
**Related:** `2026-08-10-help-center-knowledge-base.md` (Spec A — owns the help-key registry this feature consumes), `2026-08-10-guided-tours.md` (Spec C — consumes the same `data-help-key` targets), shared brief in the fogstorm session notes.

---

## 1. Summary

Ableton Live's "Info View" for the FOG hub: a member toggles **help mode** (a `?` button in the topbar, or `Shift+/`), annotated parts of the page light up with a soft outline, and pointing at (or keyboard-focusing) any highlighted thing shows a short plain-language explanation in a small docked panel — with a "Read more →" link deep into the Help Center article for that exact feature. Click pins an explanation while you go read it; Esc gets you out. It answers "what does this button do?" *in place*, without leaving the page.

No accounts, no database, no server state: help mode is a per-device preference in `localStorage`, and the content comes from the in-code help-key registry (Spec A) served once as JSON.

### Locked decisions (from brainstorm Q&A / shared brief)

| Decision | Choice |
|---|---|
| Content source | The in-code registry module chosen by Spec A (`2026-08-10-help-center-knowledge-base.md`): `HELP_KEYS` in `core/help_registry.py`, entries `{key: {title, short_text, article_slug, anchor}}` with `short_text` per A's contract (1–2 plain sentences, ≤200 chars). No DB model; B adds no schema. |
| Key format | `<area>.<action-slug>`, lowercase, dots + hyphens only (brief contract). Templates carry them as `data-help-key="<key>"` — the same attributes Spec C's tours target. |
| Toggle state | Client-only `localStorage` (`plHelpMode`), per-device, default **off** — same precedent as `hubSidebarOpen` / calendar filters. No per-user model (YAGNI). |
| Data delivery | One JSON endpoint (`/help/topics.json`), gated on `help_page_enabled`, HTTP-cached, fetched lazily the first time help mode turns on — never on ordinary page loads. |
| JS shape | One plain vendored-free module `static/js/info_view.js` (~150 lines), **no Alpine component** — document-level event delegation survives hx-boost body swaps, where Alpine trees get re-inited per swap. Singleton on `window.__plInfoView`. |
| Click behavior in help mode | Clicking an annotated element **pins its topic and suppresses the default action** (capture-phase `preventDefault`). Help mode is a safe "inspect" mode, like Ableton's — you can poke anything without triggering it. A capture-phase `submit` guard extends the same promise to Enter-key form submission (§5.1). Non-annotated elements behave normally, so you can still navigate. |
| Scope | Hub surface only in phase 1. The `book.` subdomain (public classes, account pages) is explicitly deferred — see §10. |
| Failure posture | If the topics fetch fails, nothing on the page breaks: panel shows a quiet retry state, one `console.warn`, no outlines. The page is never held hostage by help. |

## 2. What already exists (reuse, don't reinvent)

Verified in the codebase 2026-08-10:

| Need | Existing thing | Location |
|---|---|---|
| Feature gate | `help_page_enabled` on `SiteConfiguration`, already exposed to every template via the `feature_flags` context processor and already gating `help_page` | `core/context_processors.py`, `hub/views.py:2296` |
| Topbar to host the `?` button | `.pl-topbar` with theme toggle (`.pl-topbar__theme-toggle`, hub.css:870) — the new button sits beside it and copies its styling | `templates/hub/base.html:450`, `static/css/hub.css:778` |
| Keyboard-in-modal convention | Alpine `@keydown.escape.window` on `components/modal.html:20` — our global Esc handler must yield to a visible modal (see §6.4) | `templates/components/modal.html` |
| Per-device client state precedent | `localStorage`: `hubSidebarOpen`, `hubGuildsOpen`, `theme` in base.html; calendar filter "saved on this device" | `templates/hub/base.html:72,339` |
| Re-init across hx-boost navigations | `htmx:afterSettle` listener already re-runs `Alpine.initTree` — our module hooks the same event | `templates/hub/base.html:554` |
| Theme tokens | `--hub-blue` / `--hub-blue-soft` (focus/info accents), `--color-tuscan-yellow` (pinned accent), `--hub-card-bg` / `--hub-border-strong` / `--hub-text` / `--hub-text-muted`, all overridden under `[data-theme="light"]` | `static/css/hub.css:46,113` |
| Existing `?` micro-help | `.pl-help` / `.pl-help__icon` / `.pl-help__bubble` hover bubble (hub.css:1797) — stays as-is for inline field hints; the topbar button borrows its `?`-in-a-circle icon language so the two read as one family | `static/css/hub.css:1797` |
| z-index ladder to slot into | topbar 50, mobile feedback FAB 95, profile dropdowns 100, `.pl-feedback-btn` 200, `.pl-modal-backdrop` **500** (components.css:14), changelog 9999 | `static/css/hub.css`, `static/css/components.css` |
| Mobile FAB whose spot we dock into | `.hub-feedback-fab` — **mobile-only** (`display:none`, shown ≤768px), fixed bottom-right, z-95. Desktop bottom-right is free; on mobile we suppress the FAB while help mode is on (§6.6) | `static/css/hub.css:2954`, `templates/hub/base.html:395` |
| JSON-ish thin views + URL home | `help_page` / `help_edit` cluster in `hub/views.py`, URLs at `hub/urls.py:183-188` — the topics endpoint lives beside them | `hub/urls.py` |
| Server test home | `tests/hub/help_spec.py` already tests the help page — new specs sit next to it | `tests/hub/` |
| e2e harness | `tests/e2e/conftest.py` `login_via_code` fixture, `e2e` marker deselected by default | `tests/e2e/` |

**Gaps (net-new, kept small):** the topics JSON view, `static/js/info_view.js`, the panel partial, one CSS block in hub.css, the `data-help-key` attributes themselves, and — flagged by the reuse map — a global keyboard shortcut listener, which does not exist anywhere yet. This spec introduces exactly one, guarded (§6.4), inside the module.

## 3. Where the code lives

```
hub/
    views.py                        # + help_topics_json (thin view, ~20 lines)
    urls.py                         # + path("help/topics.json", ...)
static/
    js/info_view.js                 # NEW — the whole client (~150 lines, plain module)
    css/hub.css                     # + one .pl-infoview-* block (~80 lines)
templates/
    hub/base.html                   # + topbar ? button, + panel include, + script tag
    hub/_info_view_panel.html       # NEW — panel markup (server-rendered, hidden by default)
    hub/guild_voting.html           # + data-help-key attributes   ┐
    hub/guild_detail.html           #                              │
    hub/guild_edit.html             #                              │
    hub/orientation_info.html       #                              │  phase-1
    hub/community_calendar.html     #                              │  annotation
    hub/member_directory.html       #                              │  pass (§6.8)
    hub/user_settings.html          #                              │
    classes/teach/overview.html     #                              │
    classes/teach/class_form.html   #                              ┘
tests/
    hub/help_topics_spec.py         # NEW — endpoint spec
    hub/help_keys_spec.py           # NEW — template-key ↔ registry integrity spec
    e2e/info_view_spec.py           # NEW — Playwright toggle/panel spec
```

The registry itself is **Spec A's module** — this spec imports it, never defines it. Build order across the overhaul is A → B (this) — the endpoint cannot land before A's registry exists.

## 4. Data model

None. No models, no migrations. State lives in:

- `localStorage["plHelpMode"]` — `"1"`/`"0"`, per-device, default off.
- `window.__plInfoView` — in-memory singleton: fetched topics dict, current/pinned key, bound-flag. Browser HTTP cache (endpoint sends `Cache-Control` + ETag, §5) makes cross-pageload refetches cheap; no localStorage copy of content (stale-copy risk for zero win).

## 5. Business logic (thin view — there is almost none)

One view, skinny by nature (it serializes a constant):

```python
@require_GET
def help_topics_json(request: HttpRequest) -> JsonResponse:
    """Serve the in-code help-key registry for the Info View overlay.

    Public-read for the same reason help_page is: org-wide reference content, no PII.
    """
    if not SiteConfiguration.load().help_page_enabled:
        return JsonResponse({"detail": "Not found."}, status=404)
    topics = {
        key: {"title": t["title"], "short_text": t["short_text"], "url": url_for(key)}
        for key, t in HELP_KEYS.items()   # core/help_registry.py — Spec A's module
    }
    response = JsonResponse({"version": VERSION, "topics": topics})
    patch_cache_control(response, public=True, max_age=3600)
    return response
```

- `HELP_KEYS` and `url_for(key)` are Spec A's exports (`core/help_registry.py` §5.1 of that spec): `url_for` resolves `article_slug` + `anchor` into the per-article Help Center URL — the client gets a finished `url` and needs zero URL logic, and B invents no resolver of its own.
- ETag: wrap with `@condition(etag_func=...)` hashing `(VERSION, registry)` so deploys bust the cache and unchanged deploys 304. `VERSION` from `plfog/version.py` — already bumped every PR, a free cache key.
- URL: `path("help/topics.json", views.help_topics_json, name="hub_help_topics_json")` next to the other `help/` routes in `hub/urls.py`.
- Full type annotations; no business logic anywhere else server-side. Fat-models doesn't apply — there is no model.

### 5.1 Client module contract (`static/js/info_view.js`)

Plain script, no dependencies, one IIFE that installs `window.__plInfoView` exactly once (`if (window.__plInfoView) { window.__plInfoView.init(); return; }` — hx-boost body swaps re-execute body scripts, so re-execution must be a cheap re-init, not a double-bind).

**Public surface (what Spec C's tours and tests can rely on):**

| Member / event | Behavior |
|---|---|
| `__plInfoView.toggle()` / `.on()` / `.off()` | Enter/leave help mode. `.on()` triggers the lazy fetch on first use. |
| `__plInfoView.active` | Boolean, current mode. |
| `document` event `pl-help:on` / `pl-help:off` | Dispatched on mode change (CustomEvent). |
| `document` event `pl-help:topic` | Dispatched when the panel's topic changes, `detail: {key, pinned}`. |

**Bindings (all on `document`, bound once — they survive every hx-boost swap):**

- `click` on `[data-help-toggle]` (the topbar button) → `toggle()`.
- `keydown` — the one global shortcut listener, guarded per §6.4.
- `mouseover` + `focusin` — `e.target.closest("[data-help-key]")`; if help mode is on and the key resolves in the fetched registry, show its topic (hover/focus never pins).
- `click` (capture phase) — in help mode, on a resolvable `[data-help-key]` element: `preventDefault()` + `stopPropagation()`, pin (or unpin if already pinned to that key).
- `submit` (capture phase) — in help mode, if the submitting form is, or is inside, a marked target (`e.target.closest(".pl-infoview-target")`), `preventDefault()` and pin that form's topic. Without this, focusing an input inside an annotated form and pressing Enter fires a **real** submit that the click listener never sees — e.g. the voting ballot form (`templates/hub/guild_voting.html:39`) or the directory search form. The inspect-mode promise must hold for the keyboard too.
- `click` (bubble phase, bound on the panel node each `init()`) — `stopPropagation()` for any click originating inside `[data-infoview-panel]`. `components/modal.html:26` puts `@click.outside="open = false"` on `.pl-modal`; the panel lives outside the modal, so without this containment, clicking Read more / Unpin / Try again / × while a modal is open would dismiss the modal and lose its form state (see §6.7).
- `htmx:afterSettle` → `init()`: re-find the panel node in the fresh body, re-apply mode class + outlines-ready marking, restore panel state (mode persists; a pin does **not** survive navigation — the pinned element is gone).

**Init and mode application:**

- `init()` first looks for `[data-infoview-panel]`; if absent (the script somehow runs on a page whose base didn't render the panel — e.g. `help_page_enabled` off, or a non-hub surface), it **no-ops entirely**: no mode class, no shortcut effect, no fetch. Belt to the template guard's suspenders (§6.1).
- Otherwise `init()` reads `localStorage.plHelpMode`; if on, sets `pl-help-mode` class on `<html>` (the html element survives boosted swaps; class on it means one CSS hook styles everything), syncs the toggle button's `aria-pressed`, shows the panel, and starts the fetch if topics aren't loaded yet.
- After topics load, `init()` (and every afterSettle re-init) walks `document.querySelectorAll("[data-help-key]")` and adds `pl-infoview-target` to elements whose key exists in the registry. **Only marked elements get outlines and interactions** — an unknown or unregistered key is inert, exactly like an unannotated element. In addition, unresolved keys produce one grouped `console.warn` listing them (dev-facing typo catcher; invisible to members).
- **Keyboard reachability:** while help mode is on, every marked target that contains no natively focusable descendant (no link, button, input — checked with one `querySelector` over the usual focusable selector) gets `tabindex="0"` so `focusin` can reach it; the module records which elements it touched and removes the attribute on `.off()`. Without this, pure-container targets (`guild.staff-roster`, `guild.faq`, `voting.live-standings`, `directory.your-visibility`, `settings.skills`) would be invisible to keyboard-only members.

**Fetch lifecycle:** on first `.on()` per page-lifetime, the panel shows the **Loading** state (§6.3) and `fetch("/help/topics.json")` starts. Success → store on singleton, mark targets (outlines appear only now), dispatch `pl-help:on`, panel moves to hint (or empty) state. Failure (network error or non-200) → `console.warn("Info View: help topics failed to load")`, panel shows the failed state (§6.3), no outlines, mode stays on so "Try again" can refetch. The failure path must never throw past the module — everything wrapped, page JS unaffected.

## 6. UI / UX

Walked screen-by-screen and state-by-state; this is the section the adversarial review grades.

### 6.1 The toggle button (topbar)

- **Where:** `templates/hub/base.html`, inside `.pl-topbar`, immediately **before** the theme toggle (help ? · theme · avatar reading left→right at the bar's right end). Rendered only when `help_page_enabled` and the authenticated-hub branch of the topbar renders (same condition as the sidebar; guest/public views never see it).
- **Markup:** `<button class="pl-topbar__help-toggle" data-help-toggle aria-pressed="false" aria-label="Toggle help mode" title="What does this do? (?)">` containing a `?`-in-a-circle SVG (same 18px stroke style as the theme toggle's sun/moon; visually kin to `.pl-help__icon`).
- **Styling:** copies `.pl-topbar__theme-toggle` (muted icon button, hover backgrounds per theme, hub.css:870-890). **Active (help mode on):** `[aria-pressed="true"]` gets `color: var(--color-tuscan-yellow); background: rgba(238,180,75,0.15);` — the same active-gold treatment as `.pl-badge--version`, so "this mode is ON" is visible at a glance in both themes.
- **No Alpine** on this button — the module owns it via `data-help-toggle` delegation, so it keeps working after boosted swaps without re-init races.
- **Script gating:** the `<script src="…info_view.js">` include in base.html sits inside the **same guard as the panel include** (`help_page_enabled` + authenticated hub branch) — the module never even loads where the feature is off — and `init()` additionally no-ops when `[data-infoview-panel]` is absent (§5.1), so `Shift+/` can never set `pl-help-mode` on a page with no panel and no endpoint.
- **Mobile:** the topbar renders on mobile; the button stays (44px effective tap target with its padding).

### 6.2 Help-mode page treatment (outlines)

All styling under the single `html.pl-help-mode` hook, tokens only:

```css
.pl-help-mode .pl-infoview-target {
    outline: 2px dashed var(--hub-blue);
    outline-offset: 2px;
    border-radius: 4px;
    cursor: help;
}
.pl-help-mode .pl-infoview-target:hover,
.pl-help-mode .pl-infoview-target:focus-visible {
    outline-style: solid;
    box-shadow: 0 0 0 4px var(--hub-blue-soft);
}
.pl-help-mode .pl-infoview-target.pl-infoview-pinned {
    outline: 2px solid var(--color-tuscan-yellow);
    box-shadow: 0 0 0 4px rgba(238, 180, 75, 0.18);
}
```

- `--hub-blue` is the established "info/focus" accent and is theme-overridden (`#3d8bd4` dark / `#2f6fb0` light); gold marks the pin, matching the app's "active" language. No hardcoded colors anywhere except the gold soft-glow alpha, which mirrors the existing `rgba(238,180,75,…)` fills used throughout hub.css for gold washes.
- `outline` (not `border`) so no layout shift when mode toggles; `outline-offset` keeps it off the element's own borders.
- Elements with `data-help-key` but no registry entry, and elements with no annotation at all: **no outline, no cursor change, no interaction** — they are indistinguishable from plain page content.
- Help mode off: `pl-help-mode` absent, zero CSS applies, zero visual cost.

### 6.3 The docked panel — `templates/hub/_info_view_panel.html`

Server-rendered once in `base.html` (auth'd branch, `help_page_enabled` guard), hidden by default; the module fills and shows it. Not `components/modal.html` — this is deliberately a **non-modal, non-focus-trapping** complementary panel (a modal would defeat "hover things while reading").

```html
<aside class="pl-infoview-panel" data-infoview-panel role="complementary"
       aria-label="Contextual help" hidden>
    <div class="pl-infoview-panel__bar">
        <span class="pl-infoview-panel__mode">Help mode</span>
        <button type="button" class="pl-infoview-panel__close" data-infoview-close
                aria-label="Exit help mode">&times;</button>
    </div>
    <div class="pl-infoview-panel__body" aria-live="polite"><!-- module writes here --></div>
</aside>
```

- **Placement (desktop >768px):** `position: fixed; right: 1.25rem; bottom: 1.25rem; width: 340px; max-height: 40vh; overflow-y: auto;` on `--hub-card-bg`, `1px solid var(--hub-border-strong)`, radius 12px, the house `0 8px 24px rgba(0,0,0,0.3)` dropdown shadow. Verified collisions: the feedback FAB is **mobile-only** (hub.css:2986), so desktop bottom-right is free; the fixed `.pl-feedback-btn` sits bottom-*left*; toasts render top-level and are transient.
- **z-index: 600** — above `.pl-modal-backdrop` (500) so help mode still works on annotated content *inside an open modal* (hover a modal field, panel updates, panel visibly floats at the corner over the backdrop); above dropdowns (100/200) and the FAB (95); below the changelog overlay (9999). Corner-docked vs centered modals means overlap is rare — but not impossible: at mid-width viewports (~1366px) a `lg` (720px) modal plus the 340px panel can collide. Accepted: the panel is dismissible (×) and the overlap obscures modal edge, not its controls; the §9 manual modal-overlap check covers mid-widths. Documented as a comment next to the rule so the ladder stays legible.
- **Accessibility:** `aria-live="polite"` on the body announces topic changes to screen readers without stealing focus. The panel **never takes focus** on show or update — a member tabbing through the page keeps their place; the only focusable things inside are the × and "Read more" link, reachable by tabbing to them normally. `hidden` attribute (not just CSS) when help mode is off, so it's gone from the accessibility tree entirely.
- **Dismissal:** the × exits help mode (same as the topbar button / `?` / final Esc). No focus trap — but exiting must not strand focus on a node that's about to be hidden: when help mode exits via the × (or via `Shift+/`/Esc while focus is inside the panel), the module moves focus to the topbar `?` toggle, so keyboard and screen-reader users land somewhere real and announced instead of `<body>`.

**Panel states (all seven):**

| State | Body content |
|---|---|
| **Off** | Panel `hidden`. Nothing rendered, nothing announced. |
| **Loading** | Shown from `.on()` until the fetch resolves: "Loading help topics…" (`--hub-text-muted`). No outlines yet — targets are marked only on fetch success (§5.1). Usually sub-second; still a named state, not a blank flash. |
| **On, nothing hovered (hint)** | Muted one-liner: "Hover or tap anything highlighted to learn what it does. Press Esc to exit." (`--hub-text-muted`, 0.8125rem). |
| **On, page has no annotations (empty)** | After target-marking, if `querySelectorAll(".pl-infoview-target").length === 0`: "Nothing on this page has help notes yet — browse the Help Center →" (link to `{% url 'hub_help' %}`). Matters from phase 2 on, when help mode works hub-wide but most pages aren't yet annotated — a bare hint promising highlights that never appear would read as broken. |
| **On, topic showing** | Topic `title` (Lato 700, 0.9375rem, `--hub-text`) · `short_text` (1–2 plain sentences per Spec A's contract, Inter 0.8125rem, 1.5 line-height) · "Read more →" (gold `--hub-link`, `href` = topic `url`, normal navigation — it leaves for the Help Center article anchor, which is the point). Reverts to hint when the pointer/focus leaves all targets. |
| **Pinned** | Same as topic, plus a small gold pin glyph beside the title and an "Unpin" text button (`hub-btn--ghost`-styled link). Hover elsewhere does **not** replace a pinned topic — that's the pin's job. |
| **Fetch failed** | "Help topics couldn't load." + a "Try again" `pl-btn pl-btn--secondary pl-btn--sm` button that refetches. One `console.warn` already emitted; no toast (this is ambient, not a task the member initiated failing). |

There are **no forms** in this feature — the checklist's list-editor/form/destructive sections don't bite; states, feedback, mobile, and theme sections above and below apply in full.

### 6.4 Keyboard interaction — the one global listener

No global shortcut registry exists (reuse map §10); this introduces exactly one `document`-level `keydown` listener inside the module, guarded so it can never eat typing:

```js
document.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t.closest?.("input, textarea, select") || t.isContentEditable) return;  // never fire while typing
    if (e.key === "?" && !e.ctrlKey && !e.metaKey && !e.altKey && !e.repeat) {   // Shift+/ on US layouts
        e.preventDefault();
        InfoView.toggle();
    } else if (e.key === "Escape" && InfoView.active) {
        if (anyVisibleModal()) return;               // yield: modal's own Escape handler wins
        InfoView.pinnedKey ? InfoView.unpin() : InfoView.off();
    }
});
```

- **`?` guard details:** matching `e.key === "?"` (the produced character, so it's layout-correct) with no ctrl/meta/alt keeps browser and OS chords intact; the `closest("input, textarea, select")` + `isContentEditable` check covers every text-entry surface in the app including Quill (contenteditable). Typing "?" in a search box, a Markdown textarea, or a Quill email body never toggles help mode.
- **Esc ladder:** (1) if any `.pl-modal-backdrop` is visible (`getComputedStyle(el).display !== "none"` over `querySelectorAll(".pl-modal-backdrop")`), do nothing — Alpine's `@keydown.escape.window` in `components/modal.html` closes the modal, and help mode survives; (2) else if pinned, Esc unpins (back to hover mode); (3) else Esc exits help mode. Predictable, and never fights the modal convention. **Dropdowns are a documented, accepted double-fire:** the profile menu and view-as popover also close on `@keydown.escape.window` (base.html:418,463), so Esc with one of those open closes the dropdown *and* advances our ladder one step. Accepted deliberately — closing a dropdown loses nothing (unlike a modal's form state), and sniffing Alpine's internal `open` state for every popover would be brittle coupling for a cosmetic win.
- **Focus = hover:** `focusin` delegation means keyboard members Tab through the page and the panel narrates each annotated element they land on — no pointer required. Native `:focus-visible` rings remain (our outline styles add to them, never suppress them).

### 6.5 Both themes

- Every color in §6.2/§6.3 is a token (`--hub-blue`, `--hub-blue-soft`, `--color-tuscan-yellow`, `--hub-card-bg`, `--hub-border-strong`, `--hub-text`, `--hub-text-muted`, `--hub-link`) — all overridden under `[data-theme="light"]`. **No hardcoded colors; `--surface` is not a token and appears nowhere.** Shadows use the house rgba-black values already used by `.pl-profile__dropdown`.
- Dark: blue dashed outlines on Obsidian cards, gold pin, panel on `#181b24` card. Light: the darker `#2f6fb0` blue keeps ≥3:1 against Slate's `#eef0f3`/white; gold pin unchanged (it's theme-stable by design).
- Toggling the theme while help mode is on restyles live (pure token swap, no JS involvement). **Build-time check: verify both themes by hand on the voting page and inside an open modal.**

### 6.6 Mobile (≤768px) — no hover exists

- **Entering:** the topbar `?` button (hardware-keyboard `Shift+/` also works on tablets; not relied on).
- **Panel becomes a bottom bar/sheet:** `left: 0; right: 0; bottom: 0; width: auto; border-radius: 12px 12px 0 0; padding-bottom: calc(env(safe-area-inset-bottom, 0px) + 0.75rem); max-height: 45vh;`. With nothing selected it's a slim one-line bar (the hint + ×); with a topic it grows into a sheet (title, short_text, Read more), scrolling internally past 45vh. Page content stays visible above it.
- **"Mobile" detection:** `window.matchMedia("(max-width: 768px)")` — the same breakpoint the CSS uses, checked at event time (not cached), so a rotated tablet behaves consistently with what's painted.
- **Touch behavior:** tap an annotated element → the §5.1 capture-click path fires → pins the topic and opens the sheet (the element's own action is suppressed, same as desktop — one consistent rule). On mobile pin, the module also runs `scrollIntoView({block: "center", behavior: "smooth"})` on the target so the sheet (up to 45vh) never sits on top of the very element it's explaining — the existing mobile `.hub-content` padding (hub.css:2990, sized for a slim bar, not a 45vh sheet) is not relied on for this. Tap a **different** annotated element → re-pins to it (and re-centers). Tap anywhere non-annotated → the tap's default action is **suppressed** and it only unpins/collapses the sheet to the slim hint bar — a "safe close" tap, consistent with inspect mode; the *next* tap acts normally (mobile-only relaxation of the desktop "pin holds" rule, because there's no hover to fall back to). The sheet's × exits help mode entirely, same as desktop.
- **Feedback FAB collision:** the FAB owns mobile bottom-right at z-95. While help mode is on: `.pl-help-mode .hub-feedback-fab { display: none; }` (higher specificity than the media-query show rule, no `!important` needed). It returns the instant help mode exits.
- Tap targets: ×, Unpin, Try again, Read more are all real buttons/links ≥40px effective; spacing on the 8px grid throughout.

### 6.7 Coexistence with modals, dropdowns, hx-boost

- **Modals:** panel at z-600 over backdrop z-500 (§6.3); Esc yields to open modals (§6.4); annotated modal content is fully inspectable. **Panel clicks never close a modal:** `.pl-modal` carries `@click.outside="open = false"` (`components/modal.html:26`) and the panel is outside the modal, so the module stops propagation of every click originating inside `[data-infoview-panel]` (§5.1) — Alpine's outside-listener never sees them, and clicking Read more / Unpin / Try again / × can't nuke an open modal's form state.
- **Dropdowns/popovers** (profile, view-as, notification bell, z-100/200): sit under the panel, but occupy the top of the viewport — no visual overlap in practice. Hovering an annotated item *inside* a dropdown updates the panel without closing the dropdown (mouseover doesn't trigger Alpine's `@click.away`).
- **hx-boost navigation:** mode class lives on `<html>` (never swapped); listeners live on `document` (never swapped); the panel node and `data-help-key` elements are re-found in `init()` on `htmx:afterSettle` (the hook base.html already uses for Alpine). Result: help mode persists seamlessly across boosted page moves; the pin clears (its element is gone); topics are fetched once per page-lifetime, not per navigation.
- **Toasts:** top-level and transient; no interaction.

### 6.8 Phase-1 annotation coverage — the concrete keys

`data-help-key` goes on the **semantic container** (the card, form, or primary button — not icons), one attribute per element, keys from the brief's `<area>.<action-slug>` contract. The same key may legitimately appear on more than one page (e.g. `orientation.book-slot` on both the guild page CTA and the orientation page). **Nesting rule:** when annotated elements nest, the **innermost wins** — every lookup uses `closest("[data-help-key]")` from the event target, which naturally resolves to the nearest ancestor. Still, prefer **non-overlapping regions**: annotate a heading row or button, not a whole pane that contains other annotated things, so members aren't surprised by which outline they're inside (the user-settings rows below apply this). Every key below must exist in Spec A's `HELP_KEYS`; the build coordinates with A's owner and adds any missing entries **following A's contract** (title, 1–2 plain-sentence ≤200-char ELI14 short_text, article_slug + anchor into the approved IA). The integrity spec in §9 makes drift a test failure.

| Page (template) | `data-help-key` | On | IA article |
|---|---|---|---|
| Guild voting — `templates/hub/guild_voting.html` | `voting.rank-guilds` | the 1st/2nd/3rd ballot form card | 6 |
| | `voting.live-standings` | the standings tab card (a separate `hub-card` from the ballot form — no overlap; verified guild_voting.html:33 vs :133) | 6 |
| | `voting.results-history` | the history tab/link | 6 |
| Guild page — `templates/hub/guild_detail.html` | `guild.join-leave` | the Join/Leave button | 4 |
| | `orientation.book-slot` | the orientation CTA card | 5 |
| | `guild.staff-roster` | the staff/leads list card | 17 |
| | `guild.faq` | the FAQ section | 16 |
| Guild edit — `templates/hub/guild_edit.html` | `guild.edit-overview` | the overview/banner form section | 16 |
| | `guild.manage-gallery` | the gallery manager | 16 |
| | `guild.manage-faq` | the FAQ editor | 16 |
| | `guild.manage-links` | the links editor | 16 |
| | `guild.contact-emails` | the contact-emails editor | 16 |
| | `guild.manage-staff` | the staff editor (the "every role = full authority" warning lives here) | 17 |
| Orientation booking — `templates/hub/orientation_info.html` | `orientation.book-slot` | the slot list/booking area | 5 |
| | `orientation.request-custom` | the request-custom-time control | 5 |
| | `orientation.cancel-booking` | the cancel-my-booking control | 5 |
| Teach overview — `templates/classes/teach/overview.html` | `teach.create-class` | the "New class" button | 12 |
| | `teach.review-pipeline` | the awaiting-review card | 12 |
| | `teach.waitlists` | the waitlists card | 13 |
| | `teach.guild-approvals` | the guild-lead approval queue (renders for leads only — key is simply absent for others) | 21 |
| Class create — `templates/classes/teach/class_form.html` | `teach.class-schedule` | the sessions/date-set section | 12 |
| | `teach.class-pricing` | the pricing section | 12 |
| | `teach.submit-for-review` | the submit button | 12 |
| Community calendar — `templates/hub/community_calendar.html` | `calendar.browse-filter` | the filter bar | 8 |
| | `calendar.subscribe-ics` | the .ics subscribe control | 8 |
| | `calendar.propose-event` | the propose-event button | 9 |
| Member directory — `templates/hub/member_directory.html` | `directory.search-filter` | the search/filter form | 11 |
| | `directory.your-visibility` | the visibility note/callout (short_text points at Settings → Contacts) | 11 |
| User settings — `templates/hub/user_settings.html` | `settings.profile` | the Member-profile **heading/intro row** of the Profile tab — deliberately *not* the whole pane, because `settings.contact-methods` and `settings.skills` live inside that pane (the §6.8 non-overlap rule) | 2 |
| | `settings.contact-methods` | the contact-methods section | 2 |
| | `settings.skills` | the skills section | 2 |
| | `settings.your-guilds` | the Guilds tab pane (contains no other annotated element — pane-level is fine here) | 4 |
| | `settings.email-addresses` | the Emails tab pane | 2 |
| | `settings.notifications` | the Notifications tab pane | 3 |

(IA numbers = the approved article list in the shared brief; anchors are Spec A's per-section ids derived from these keys.)

### 6.9 Reverse deep-link — `?highlight=<key>` (small, included)

The KB (Spec A) can link back into the app "show me where": any hub URL plus `?highlight=voting.rank-guilds`. On init, the module reads the param; if an element with that `data-help-key` exists, it `scrollIntoView({block: "center"})` and plays a ~2s flash (CSS keyframe pulsing the gold outline twice, then removed). No help mode required, no fetch required (it only needs the DOM attribute), silent no-op if the key isn't on the page. **Runs once per full page load:** the module consumes the param on first flash (a singleton `highlightDone` flag), because `init()` re-runs on every `htmx:afterSettle` — including non-boost partial swaps like a standings refresh — and the URL still carries the param; without the flag the flash would replay on every swap. ~18 lines of JS + one keyframe; ships as Phase 5. `prefers-reduced-motion: reduce` → no animation, just the scroll + a static 2s outline.

## 7. Notifications / emails / activity

None. This feature sends nothing and logs nothing.

## 8. Build order (phased; each phase ships green — full suite + ruff + mypy)

Hard dependency: **Spec A's registry module must exist first** (build order A → B).

1. **Endpoint.** `help_topics_json` + URL + ETag/cache headers, importing Spec A's registry; `tests/hub/help_topics_spec.py` green. No UI yet — the endpoint is inert and gated.
2. **Help mode core (desktop).** `static/js/info_view.js`, `_info_view_panel.html`, topbar button, the `.pl-infoview-*` CSS block. Toggle (button + `Shift+/`), lazy fetch, outlines, hover/focus → panel, pin/unpin, Esc ladder, fail-silent fetch, hx-boost persistence. Ships usable even before annotations exist (panel shows the hint; nothing highlights).
3. **Mobile.** Bottom bar/sheet styles, touch pin/unpin behavior, FAB suppression.
4. **Phase-1 annotations.** `data-help-key` across the §6.8 pages, registry entries coordinated with Spec A, `tests/hub/help_keys_spec.py` integrity spec, `tests/e2e/info_view_spec.py`.
5. **Deep-link flash + release.** `?highlight=` (§6.9). Bump `plfog/version.py` VERSION and add **one** member-facing CHANGELOG entry for the whole feature, e.g. — *"Press ? for instant help — Turn on help mode (the ? button up top, or press Shift+/) and hover or tap anything highlighted to see what it does, with a link to the full guide. Esc turns it off."* (Per changelog policy: later intra-line polish folds into this entry, no second entry.)

> Spec only — do not build until approved.

## 9. Testing

House style: BDD `*_spec.py`, `describe_*`/`it_*` (remember `context_*` is **not** a collected prefix — nested blocks must be `describe_*` or they silently never run), factory-boy where data is needed, coverage gate on all new Python (the view is small; 100% of it).

**Server-side — `tests/hub/help_topics_spec.py`:**

- `describe_help_topics_json`
  - `it_returns_404_when_help_page_disabled(db)` — flag off → 404 JSON.
  - `it_serves_every_registry_key_with_title_short_text_and_url(db)` — 200, JSON shape matches §5, key count == registry.
  - `it_resolves_urls_to_the_kb_article_anchor(db)` — each `url` reverses to Spec A's article route + `#anchor`.
  - `it_is_public_read(db)` — anonymous client gets 200 (parity with `help_page`).
  - `it_sends_cache_headers_and_honors_etag(db)` — `Cache-Control: public, max-age=3600`; second request with `If-None-Match` → 304.

**Server-side — `tests/hub/help_keys_spec.py` (the drift guard):**

- `describe_template_help_keys`
  - `it_only_references_registered_keys()` — walk `templates/` (all app templates live under this one root — there is no `classes/templates/`; the teach pages are `templates/classes/teach/…`) for `data-help-key="…"` (regex), assert every value is a key in `HELP_KEYS`. A typo'd or orphaned key fails CI, not silently no-ops in production.
  - `it_uses_the_key_format_contract()` — every referenced key matches Spec A's `KEY_PATTERN`, **imported from `core/help_registry`** — never a restated regex here, so the contract (including A's exactly-one-dot rule) has a single home and can't drift.

**e2e — `tests/e2e/info_view_spec.py`** (`e2e` marker, deselected by default; `login_via_code` fixture):

- `describe_info_view`
  - `it_toggles_help_mode_and_shows_the_hint` — click the topbar `?`, assert `html.pl-help-mode` and the panel hint text; press Escape, assert both gone.
  - `it_shows_a_topic_on_hover_and_pins_on_click` — on `/guilds/voting/`, enable help mode, hover `[data-help-key="voting.rank-guilds"]`, assert the panel shows the topic title and a "Read more" href into `/help/…#…`; click the element, assert the pinned outline class and that the ballot did **not** submit; Escape unpins, second Escape exits.
  - `it_survives_a_boosted_navigation` — enable help mode, click a sidebar link, assert `pl-help-mode` still set and the panel present on the new page.
  - `it_fails_silent_when_topics_are_unreachable` — `page.route("**/help/topics.json", abort)`, enable help mode, assert the panel shows "couldn't load" + Try again, no outlines, and the page still navigates normally.
  - `it_does_not_toggle_while_typing` — focus the directory search input, type `?`, assert help mode stays off.

Not automatable cheaply (manual pass at build time): both-themes visual check, modal-overlap check, mobile sheet feel.

## 10. Open / deferred

- **`book.` subdomain surfaces** — explicitly deferred. Phase 1 is the hub only; the public classes/account pages have different base templates and CSS scopes, and their audience (prospective students) needs different content anyway. Revisit after the hub proves the pattern.
- **Guided tours** — Spec C (`2026-08-10-guided-tours.md`). This spec's only obligations to it: stable `data-help-key` attributes and the `pl-help:*` events.
- **Per-user (cross-device) help-mode state** — not built. localStorage matches the sidebar/theme precedent; syncing a binary preference isn't worth a model.
- **Annotating admin-only pages** (voting admin, site settings, members) — deferred; phase 1 targets member/lead/instructor surfaces where confusion is common and support cost is real. The mechanism needs zero changes to extend, just attributes + registry entries.
- **Search inside the panel** — not built; "Read more" lands in the KB, which owns search (Spec A).
- **`?` on non-US keyboard layouts** — matching `e.key === "?"` is layout-correct wherever a `?` is typeable; no chord fallback added until someone actually asks.
