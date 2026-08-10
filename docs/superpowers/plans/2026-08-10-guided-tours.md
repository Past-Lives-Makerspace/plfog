# Guided Tours ("Show me around") — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-10
**Surface:** FOG hub `pastlives.test` — hub home, guild edit, teaching overview, user settings (Notifications tab), Help page
**Related:** `2026-08-10-help-center-knowledge-base.md` (Spec A — help-key registry, Help landing page), `2026-08-10-info-view-hover-help.md` (Spec B — `data-help-key` annotations), `2026-08-10-instructor-orientation-unlock.md` (Spec D — instructor unlock; independent build, see §10). Shared brief: help-center overhaul, 2026-08-10.

---

## 1. Summary

A member's first visit to a new area of FOG offers them a short, skippable guided tour: a spotlight walks the page's real controls with one or two plain sentences each. Three launch tours — the member welcome tour on hub home, the guild-lead tour on the guild edit page, and the instructor tour on the teaching overview. Tours are offered exactly once, never auto-start uninvited, can always be re-run from a "Show me around" button or the Help page, and can be switched off entirely in settings. Completion is recorded per user per tour.

### Locked decisions (from brainstorm Q&A + shared brief)

| Decision | Choice |
|---|---|
| Tour library | **Driver.js**, vendored (first tour lib in the repo), MIT. No npm — plain `<script>`/`<link>` per house convention. |
| Library load | On demand only — assets injected when a tour is offered or started, never in `base.html`. |
| Tour definitions | In-code registry (`core/tours.py`, sibling of Spec A's registry module). No DB model for tour content — YAGNI, content is repo-authored. |
| Step targets | `[data-help-key="…"]` selectors from Spec B's annotation pass where a key exists; plain CSS selectors otherwise. C adds any `data-help-key` attribute it needs that B hasn't landed yet (adding the same attribute twice is a no-op merge). |
| Multi-page tours | **Not built.** Every tour lives on a single page (the entry page). All three launch tours fit; cross-page navigation is complexity with no current customer. |
| Per-user state | New `TourState` model (user FK + `tour_key` + status), `UniqueConstraint(user, tour_key)` — the `NotificationPreference` shape. |
| Global on/off | A `Member.guided_tours_enabled` boolean (not a `NotificationPreference` row) — see §4 rationale. Toggle lives in the settings **Notifications** tab. |
| Auto-offer | Non-blocking offer card on first eligible visit to the entry page. Never auto-starts the tour. Never re-offers after dismiss or complete. Never renders alongside the first-login welcome modal (though it may appear on the pageview immediately after dismissal — see §5). |
| Spec D contract | `TourState.objects.mark_completed(user, tour_key)` and `Member.has_completed_tour(key)` are the public API for completion tracking. Completed is sticky — never downgraded. **`TourState.tour_key` values are exactly the registered `TOURS` keys — the manager fail-louds on anything else. Spec D (as revised) does NOT write `TourState` at all: its unlock's sole source of truth is `Member.instructor_oriented_at`. The completion contract stays available for future consumers that register a real tour key.** |

## 2. What already exists (reuse, don't reinvent)

Verified in code 2026-08-10:

| Need | Existing thing | Location |
|---|---|---|
| Per-user preference model shape | `NotificationPreference` — user FK, key fields, `UniqueConstraint`, absent row = default | `core/models.py:1068` |
| One-time-dismissal precedent | `Member.welcome_dismissed_at` + `dismiss_welcome()` (`save(update_fields=…)`) | `membership/models.py:483,584` |
| Welcome-modal gating flag | `show_welcome_modal` computed in `_get_hub_context` (dismissed_at is None **and** not `has_started_profile`) | `hub/views.py:82-91` |
| Settings tab page + `form_id` POST dispatch | `user_settings` view — `form_id` hidden field routes `profile` / `notifications` POSTs; tab whitelist `{profile, emails, notifications, guilds}` | `hub/views.py` `user_settings`, `templates/hub/user_settings.html:7-31` |
| Toggle UI | `components/toggle.html` (via `form_field.html` auto-detect) | `templates/components/toggle.html` |
| Settings save feedback | `messages.success(...)` + `redirect(f"{request.path}?tab=…")` (full-page POST, Django messages) | `hub/views.py` `user_settings` |
| Page header with one action slot | `components/page_header.html` — `action_url` / `action_label` renders a right-aligned `hub-btn` link | `templates/components/page_header.html` |
| Hub home header + onboarding checklist | `page_header` include + `hub/partials/_onboarding_checklist.html` behind `show_onboarding` | `templates/hub/home.html:9-14` |
| Guild edit tab strip (tour targets) | An **anonymous inline-styled flex `<div>`** (note: NOT `.pl-tabs` — that class exists only on `user_settings.html:8`) holding the `.vote-tab` buttons — Basic Info / … / Orientations / Announcements/Emails / Staff | `templates/hub/guild_edit.html:11-21` |
| Teach overview cards (tour targets) | Empty-state card + "+ Create your first class" (`overview.html:31` — the **only** create link on the page today; the has-classes branch has none); "Waiting on your review" (guild-lead-only, inside `{% if is_guild_lead %}`, line 5-8); "Needs your attention"; stats cards | `templates/classes/teach/overview.html` (NOT `class_overview.html`, which is the per-class detail) |
| Mobile feedback FAB (offer card must clear it) | `.hub-feedback-fab` — 52px circle fixed at `right/bottom: calc(safe-area + 1rem)`, mobile only | `templates/hub/base.html:394-398`, `static/css/hub.css:2954-2962` |
| Help page aside (tours card home) | `templates/hub/help.html` aside (Resources links) | `templates/hub/help.html` |
| Vendored-JS convention | `static/js/alpine.min.js`, `htmx.min.js`, `quill.min.js` — plain files, no build step | `templates/hub/base.html:530-586` |
| Per-page asset blocks | `{% block extra_head %}` (line 70) and `{% block extra_js %}` (line 584) in the hub base | `templates/hub/base.html` |
| Theme detection for JS | `window.__plTheme` + `data-theme` on `<html>` (inline head script) | `templates/hub/base.html:15-69` |
| CSRF for JS `fetch` | `data-csrf` attribute idiom (gallery_manager) / cookie read in the global `htmx:configRequest` | `templates/components/gallery_manager.html`, `base.html` |
| Sidebar open/close state (mobile) | Alpine `sidebarOpen` on `<body>` (readable via `Alpine.$data(document.body)`) | `templates/hub/base.html:72` |
| E2E login + welcome-modal handling | `login_via_code` fixture — real allauth code flow, then stamps `welcome_dismissed_at` | `tests/e2e/conftest.py:63-112` |
| Toasts | `trigger_toast()` / `$dispatch('show-toast', …)` — already global | `hub/toast.py`, `components/toast.html` |

**Genuine gaps (all small):** the vendored Driver.js files, the `TourState` model + one Member boolean, the tour registry module, one state-recording endpoint, one JS bootstrap file (`pl_tour.js`), the offer-card partial, the popover restyle CSS, the settings toggle form, and the Help-page card.

## 3. Where the code lives

```
static/js/driver.min.js               # vendored Driver.js 1.3.6 IIFE build (~19 KB min) — license banner kept
static/css/driver.css                 # vendored Driver.js base stylesheet (~4 KB)
static/js/pl_tour.js                  # our bootstrap: lazy-loads driver assets, builds/filters steps, records state
core/models.py                        # + TourState (+ manager) — sibling of NotificationPreference
core/tours.py                         # tour registry (dataclasses + TOURS dict) + offer/context helpers
core/migrations/00XX_tourstate.py     # new model (auto-reversible schema migration)
membership/models.py                  # + Member.guided_tours_enabled, Member.has_completed_tour()
membership/migrations/00XX_….py       # boolean field (auto-reversible)
hub/views.py                          # thin tour_state view; tour context wired into home + guild_edit
hub/forms.py                          # TourStateForm, TourSettingsForm
hub/urls.py                           # POST /tours/<key>/state/  (name: hub_tour_state)
classes/views.py                      # tour context wired into teach_overview
templates/hub/partials/_tour.html     # json_script payload + <script src="pl_tour.js"> (entry pages only)
templates/hub/partials/_tour_offer.html  # the offer card
templates/hub/help.html               # + "Guided tours" aside card
templates/hub/user_settings.html      # + Guided-tours card in the Notifications tab
templates/hub/_tour_settings.html     # the toggle card partial
static/css/hub.css                    # .pl-tour-* styles + .driver-popover.pl-tour theme overrides
core/spec/models/tour_state_spec.py
core/spec/tours_spec.py
hub/spec/views/tour_spec.py
membership/spec/models/member_tours_spec.py
tests/e2e/guided_tour_spec.py
```

`core` and `membership` are already in the coverage `source` list; `hub`/`classes` view changes are covered by their existing spec dirs.

### Vendoring Driver.js

- **Version pinned: 1.3.6** (latest 1.x as of this spec; verify latest patch at vendor time and update this line). **License: MIT © Kamran Ahmed** — keep the license banner comment at the top of both vendored files, and note the version + source URL (`https://github.com/kamranahmedse/driver.js`) in that banner.
- Files: npm `driver.js` package `dist/driver.js.iife.js` → `static/js/driver.min.js`; `dist/driver.css` → `static/css/driver.css`. The IIFE build exposes `window.driver.js.driver`.
- **Never referenced from `base.html`.** `pl_tour.js` injects the `<link>` and `<script>` tags at the moment a tour is about to run (see §6.2). The only script an entry page carries up front is `pl_tour.js` itself (~2 KB), via `{% block extra_js %}` in `_tour.html`.

## 4. Data model

### `TourState` (new, `core/models.py`)

One row per user per tour — created when the tour is first offered, upgraded when dismissed or completed. Absent row = never offered.

| Field | Type | Notes |
|---|---|---|
| `user` | `ForeignKey(settings.AUTH_USER_MODEL, on_delete=CASCADE, related_name="tour_states")` | help_text="The user this tour state belongs to." |
| `tour_key` | `CharField(max_length=60)` | help_text="Tour key from core.tours.TOURS." |
| `status` | `CharField(max_length=20, choices=Status.choices, default=Status.OFFERED)` | help_text="Where this user is with this tour." |
| `created_at` | `DateTimeField(auto_now_add=True)` | help_text="When the tour was first offered to this user." |
| `updated_at` | `DateTimeField(auto_now=True)` | help_text="When the status last changed." |

```python
class TourState(models.Model):
    class Status(models.TextChoices):
        OFFERED = "offered", "Offered"
        COMPLETED = "completed", "Completed"
        DISMISSED = "dismissed", "Dismissed"

    objects = TourStateManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "tour_key"], name="uq_tourstate_user_tour_key"),
        ]

    def __str__(self) -> str:
        return f"{self.user.email}:{self.tour_key}={self.get_status_display()}"
```

**Manager (`TourStateManager`)** — the whole lifecycle, fat-model style:

- `mark_offered(user, tour_key) -> TourState` — `get_or_create`; never changes an existing row's status.
- `mark_completed(user, tour_key) -> TourState` — `update_or_create` to `COMPLETED`. **Spec D's contract.**
- `mark_dismissed(user, tour_key) -> TourState` — `get_or_create` then set `DISMISSED` **unless** the row is `COMPLETED` (completed is sticky — a re-run Esc'd halfway must not erase completion).
- `status_for(user, tour_key) -> str | None` — `None` when no row.
- `statuses_for(user) -> dict[str, str]` — one query for the Help-page card.

All methods validate `tour_key in TOURS` and raise `ValueError` on an unknown key (fail loudly).

**Migration:** one schema migration adding `TourState`; auto-generated reverse (drops the table) — no data migration needed.

### `Member.guided_tours_enabled` (new boolean, `membership/models.py`)

```python
guided_tours_enabled = models.BooleanField(
    default=True,
    help_text="When off, tours are never auto-offered. Manual starts from the Help page still work.",
)
```

**Why a Member boolean and not a `NotificationPreference` row:** `NotificationPreference` is one row per `(user, event_key, channel)` driven by the event registry — a tours switch is neither an event nor a channel, and shoehorning a fake `event_key` into the matrix would pollute the registry-driven settings UI and the spine's semantics. `Member.welcome_dismissed_at` is the precedent for first-run UX state living on `Member`; the member row is already fetched on every hub request, so the check is free.

Also new: `Member.has_completed_tour(key: str) -> bool` — thin wrapper over `TourState.objects.status_for(self.user, key) == TourState.Status.COMPLETED`. **This is the method Spec D calls** to gate the instructor-orientation unlock; its name and semantics are frozen by this spec.

**Migration:** schema-only boolean with a default; auto-reversible.

## 5. Business logic (fat models / registry module)

### Tour registry — `core/tours.py`

Frozen dataclasses + a module-level dict, mirroring Spec A's in-code help registry (whichever module A lands in — `core/help_registry.py` today — tours sit beside it and reference its keys):

```python
@dataclass(frozen=True)
class TourStep:
    target: str | None       # CSS selector, usually '[data-help-key="…"]'; None = centered (element-less) step
    title: str
    body: str                # ELI14, 1–2 sentences

@dataclass(frozen=True)
class Tour:
    key: str                 # slug, e.g. "member-welcome"
    title: str               # shown on the Help card and offer
    entry_url_name: str      # url name of the single page the tour runs on
    audience: Callable[[Member], bool]
    steps: tuple[TourStep, ...]
    opens_sidebar: bool = False   # member tour: open the mobile sidebar before starting

TOURS: dict[str, Tour] = {…}
```

- `tours_for(member) -> list[Tour]` — eligible tours for the Help-page card.
- `tour_offer_context(request, tour_key, *, entry_url_kwargs=None) -> dict` — the one helper entry-page views call. Returns `{tour, tour_steps_json, show_tour_offer, tour_autostart}` and owns every guard, in order:
  1. Anonymous / no member → nothing.
  2. `?tour=<key>` present, key matches this page's tour, and audience passes → `tour_autostart=True` (manual start; **no** `offered` row is written, and dismissed/completed states don't block it — manual always works).
  3. Otherwise auto-offer only when **all** hold: `member.guided_tours_enabled`; audience passes; `show_welcome_modal` is `False` (see sequencing below); `TourState` row is absent **or** still `OFFERED`. On the absent-row case it calls `mark_offered` — the row records "we showed the offer," and the offer keeps rendering on later visits while untouched, but a single click on *No thanks* (or finishing the tour) ends it forever.
- Audiences: `member-welcome` → any member; `guild-lead` → `member.is_guild_lead or member.is_guild_staff` (`membership/models.py:879,884` — matches `_require_can_edit_guild`'s member-side reality); `instructor` → any active member **until Spec D ships** (mirrors today's `teaching_member_required`, which admits any active member — the tour says exactly that). **When Spec D lands, this audience becomes `member.can_create_classes`:** D's rewritten `teaching_member_required` 302s locked members to the orientation page (dropping any `?tour=` param), so both the auto-offer and manual starts must be gated on unlock or they dead-loop. D's revision also cuts its own tour-offer card from the orientation page — **this spec's auto-offer on the member's first eligible teach-overview GET is the canonical post-unlock introduction** (D references exactly this behavior).

**Welcome-modal sequencing (locked):** when `show_welcome_modal` is `True` (`hub/views.py:91` — brand-new member, nothing dismissed, nothing customized), the member tour is neither offered nor recorded — the welcome modal is deliberately blocking (no Esc/backdrop, `includes/welcome_modal.html`), and the two never render on the same pageview. Be honest about what follows, though: the modal's "Maybe later" button POSTs `next={{ request.path }}` and `welcome_dismiss` redirects straight back (`hub/views.py:111-123`, `welcome_modal.html:34-43`) — so a member who dismisses the modal **on hub home** lands back on hub home, where that very GET now passes the guard and renders the tour offer. **Accepted as designed:** modal, then offer on the immediately following pageview, is one prompt per pageview and reads naturally ("here's the intro" → "want a look around?"). The offer is non-blocking and one click to decline; a recency-suppression window would add state for no real win. ("Set up my profile" redirects to profile settings instead, so that path sees the offer on its next hub-home visit.)

### State recording — `hub_tour_state` view (thin)

`POST /tours/<slug:tour_key>/state/` (name `hub_tour_state`), `@login_required @require_POST`. Body: `status=completed|dismissed`. Validation lives in `TourStateForm(forms.Form)`:

- `clean_tour_key` (from the URL, passed into the form): unknown key → `ValidationError("Unknown tour.")` → 404.
- `status`: `ChoiceField` limited to `completed` / `dismissed` (a client can never write `offered`).

View: form valid → call `mark_completed` / `mark_dismissed`, return `HttpResponse(status=204)`. Invalid → 400 with `{"errors": …}`. No toast — tour endings are self-evident on screen; a "Tour complete!" toast is noise.

There is deliberately **no** dedicated offer-dismiss endpoint — the offer card's *No thanks* posts `status=dismissed` to the same endpoint.

## 6. UI / UX

Six surfaces. All new classes are `pl-` prefixed in `static/css/hub.css`; every color is a theme token; **verify both themes (Obsidian dark + Slate light) on every surface**; spacing on the 8px grid.

### 6.1 The offer card — `templates/hub/partials/_tour_offer.html`

- **What:** a compact fixed card, bottom-right of the viewport (`.pl-tour-offer`), rendered only when `show_tour_offer` is true. Non-blocking: the page behind stays fully usable; no backdrop, no focus trap.
- **Layout:** `position: fixed; right: 1.5rem; bottom: 1.5rem; z-index` just below modals. Background `var(--hub-elevated)`, `1px solid var(--hub-border)`, radius + shadow matching `.hub-card`. Max-width `20rem`. Content: a one-line lead ("**New here?** Take a 30-second tour of this page.") in `var(--hub-text)` with the tour title as context, then two real buttons side by side:
  - **Show me around** — `pl-btn pl-btn--primary pl-btn--sm`. Click: hide the card, call `plTour.start()` (§6.2). No server write yet — completion/dismissal is recorded when the tour ends.
  - **No thanks** — `pl-btn pl-btn--secondary pl-btn--sm`. Click: `fetch` POST `hub_tour_state` with `status=dismissed` (CSRF from the partial's `data-csrf`), remove the card immediately (don't wait on the response). Quiet — no toast.
- **States:** *default* (both buttons enabled); *dismissed* (card removed this pageview; row now `DISMISSED` so it never renders again); *accepted* (card removed, tour running); *network-failure on dismiss* (card still disappears now; the row stays `OFFERED` so the worst case is the offer reappearing next visit — honest, self-healing, no error UI needed); *ignored* (member navigates away: row stays `OFFERED`, the card re-renders on their next visit to this page — ignoring is not declining).
- **Accessibility:** `role="status"` (announced politely, doesn't steal focus), buttons are `<button>`s in tab order; the card never traps focus.
- **Mobile (≤768px):** stretches to a bottom bar — `left: 1rem; right: 1rem`, buttons full-height ≥44px tap targets, text wraps to two lines max. **It must clear the mobile feedback FAB** (`.hub-feedback-fab`, a 52px circle fixed at `right/bottom: calc(safe-area + 1rem)` — `base.html:394-398`, `hub.css:2954-2962`): the bar sits above it at `bottom: calc(env(safe-area-inset-bottom, 0px) + 1rem + 52px + 0.75rem)`, keeping both fully visible and tappable. (Chosen over hiding the FAB — feedback stays one tap away at all times.)
- **Dark + light:** tokens only (`--hub-elevated` / `--hub-text` / `--hub-text-muted` / `--hub-border`); no hardcoded colors.

### 6.2 Running a tour — `pl_tour.js` + the Driver.js popover restyle

**Bootstrap (`static/js/pl_tour.js`, loaded via `{% block extra_js %}` inside `_tour.html`, entry pages only).** `_tour.html` emits the payload with `{{ tour_json|json_script:"pl-tour-data" }}`: `{key, title, steps: [{target, title, body}], state_url, autostart, opens_sidebar}` plus `data-csrf` on its root. On DOM ready: if `autostart`, start immediately; else wire the offer card's buttons. Nothing else runs.

**Lazy asset load:** `plTour.start()` first injects `<link href="static/css/driver.css">` + our restyle is already in `hub.css`; then injects `<script src="static/js/driver.min.js">` and waits for `onload` (a cached promise so double-start is safe). Only then builds the driver. Result: the ~23 KB of library cost is paid exactly when a tour runs — never on ordinary pageviews, and not even on entry pages unless the member engages.

**Missing / hidden targets — skip gracefully (locked):** before constructing the driver, filter the step list: a step whose `target` selector matches nothing in the DOM, or matches an element that isn't visible (`offsetParent === null`, checked *after* the sidebar handling below), is dropped. Element-less steps (`target: null` → Driver.js centered popover) always survive. Progress numbering ("2 of 5") is computed from the filtered list, so there are never gaps. This is what makes the tours resilient to page states (e.g. a brand-new instructor's empty teach overview, an already-dismissed Get-started checklist) and to future template drift — a renamed element degrades to a shorter tour, never a broken one. If filtering leaves fewer than 2 steps, an auto-offered start silently aborts (and records nothing); a manual start still shows what remains.

**Driver config:** `popoverClass: "pl-tour"`, `showProgress: true`, `allowClose: true`, keyboard control on (Driver.js defaults: **→ / ← navigate, Esc closes**), and `animate: !window.matchMedia('(prefers-reduced-motion: reduce)').matches` so reduced-motion users get instant transitions. `overlayColor` chosen at start from the active theme (`document.documentElement.dataset.theme` / `window.__plTheme`): dark `rgba(4, 5, 8, 0.72)`, light `rgba(29, 30, 30, 0.45)`. Button labels: Next / Back / **Done** on the last step.

**Focus management (ours, not the library's):** Driver.js 1.x does **not** move focus into the popover, trap Tab, or restore focus on close — a known a11y gap, so `pl_tour.js` owns it: on each step render (`onHighlighted`), focus the popover element (give it `tabindex="-1"`); on destroy, restore focus to the element that started the tour (the offer/entry button, when there is one). Phase 1 must verify this against the vendored 1.3.6 file rather than trusting this description of the library.

**Ending + recording (locked semantics):** all state recording lives in **`onDestroyStarted`** — and note the Driver.js 1.x contract: **providing `onDestroyStarted` suppresses the default teardown, so the hook must call `driverObj.destroy()` itself** or ✕/Esc/overlay-click stop closing the tour at all. Exact recipe: in `onDestroyStarted`, read `driverObj.isLastStep()` (/ the active index), `fetch` POST `status=completed` if on the last step, else `status=dismissed`, then call `driverObj.destroy()`. `onDestroyed` does cleanup only (restore the sidebar state, restore focus). `mark_dismissed`'s sticky-completed guard means re-running a finished tour and bailing halfway never erases the completion. Esc, the popover ✕, and clicking the dimmed overlay all take the "otherwise" path — mid-tour exit is one keypress, always.

**hx-boost history safety:** htmx's history snapshotting can capture a mid-tour DOM (dead dimmed overlay with no way out on Back) and boosted swaps would leak driver's document-level key listeners. `pl_tour.js` therefore listens for **`htmx:beforeHistorySave` and `htmx:beforeSwap`** and destroys any active tour (without recording state — the member navigated, they didn't decide) before the snapshot/swap happens.

**Sidebar steps on mobile:** the member tour sets `opens_sidebar: true`. At start, if `window.innerWidth <= 768`, set `Alpine.$data(document.body).sidebarOpen = true` before filtering steps (so sidebar targets count as visible); restore the previous value on destroy. Desktop: sidebar is already open by default; if a member collapsed it, the same flip applies.

**Popover restyle (`hub.css`, scoped `.driver-popover.pl-tour`)** — Driver's default theme is white-on-white and off-brand; every rule below uses tokens so both themes come free:

| Driver class | Override |
|---|---|
| `.driver-popover.pl-tour` | `background: var(--hub-elevated); color: var(--hub-text); border: 1px solid var(--hub-border);` radius/shadow to match `.pl-modal`; `max-width: 20rem`; font-family Inter |
| `.pl-tour .driver-popover-title` | Lato 700, `color: var(--hub-text)` |
| `.pl-tour .driver-popover-description` | `color: var(--hub-text-muted)`, `0.9rem` |
| `.pl-tour .driver-popover-progress-text` | `color: var(--hub-text-muted)`, `0.8rem` |
| `.pl-tour .driver-popover-next-btn` | primary look: `background: var(--color-tuscan-yellow); color: var(--color-navy)`, `text-shadow: none` (driver's default text-shadow must be reset), `pl-btn--sm` metrics, min-height 44px on touch |
| `.pl-tour .driver-popover-prev-btn` | secondary look: transparent bg, `1px solid var(--hub-border)`, `color: var(--hub-text)` |
| `.pl-tour .driver-popover-close-btn` | `color: var(--hub-text-muted)`, hover `var(--hub-text)` |
| `.pl-tour .driver-popover-arrow-side-*` | arrow border-colors set to `var(--hub-elevated)` per side (driver arrows are CSS-border triangles) |

**Mobile popover:** `max-width: calc(100vw - 2rem)`; Driver.js auto-repositions the popover against the spotlighted element and falls back to bottom placement on tight screens — accept its placement, just cap the width and keep Next/Back/Done ≥44px tall. The spotlight (SVG overlay cutout) works unchanged on small screens.

### 6.3 "Show me around" entry buttons (manual start)

Manual start is a plain link to the entry page with `?tour=<key>` — no JS needed at the call site, works with `hx-boost`, and the autostart guard in `tour_offer_context` handles eligibility.

- **Hub home** (`templates/hub/home.html`): the existing `page_header` include gains `action_url="?tour=member-welcome"` `action_label="Show me around"` — the header's single action slot is currently unused here.
- **Teach overview** (`templates/classes/teach/overview.html` — note: NOT `class_overview.html`, which is the per-class detail): today the only `teach_class_create` link on the page is the empty-state "+ Create your first class" (`overview.html:31`) — **an instructor who already has classes has no create button at all**, so the tour's `teach.create-class` step would have no target for exactly the people most likely to run it. In-scope fix: the overview gains `components/page_header.html` (title "Teaching") with the **action slot holding a persistent "+ New class" button** (`action_url={% url 'classes:teach_class_create' %}`, `action_label="+ New class"`) that renders in both the empty and has-classes branches — this button carries `data-help-key="teach.create-class"` and is the tour's step-2 target. The **"Show me around"** manual entry (`?tour=instructor`) renders as a `hub-btn hub-btn--sm hub-btn--ghost` link directly beneath the header description. Also note: "Waiting on your review" (`overview.html:5-8`) renders only inside `{% if is_guild_lead %}` — it must never be a general-audience step target (and isn't; see §7.3).
- **Guild edit** (`templates/hub/guild_edit.html`): a `hub-btn hub-btn--sm` link (`?tour=guild-lead`) in the existing header flex row at `guild_edit.html:6-9` (this page doesn't use `page_header`; don't force it).

Ineligible or anonymous visitors hitting a `?tour=` URL get the page normally — the param is simply ignored (no error; the buttons only render for eligible members anyway: home always, guild edit only renders for editors, teach button gated the same as the tour's audience — which becomes `can_create_classes` once Spec D ships, at which point locked members never reach the overview anyway, per §5).

### 6.4 Settings toggle — Notifications tab of `templates/hub/user_settings.html`

- **Screen / partial:** new `templates/hub/_tour_settings.html`, included in the Notifications tab (`x-show="tab === 'notifications'"`) **below** the existing `_notifications_settings.html` card, as its own `hub-card` titled "Guided tours". Two small cards in one tab, each with its own Save — exactly the page's existing `form_id` idiom.
- **Form:** `TourSettingsForm(forms.ModelForm)` — `Meta: model = Member, fields = ["guided_tours_enabled"]`. Rendered with `{% include "components/toggle.html" with field=form.guided_tours_enabled toggle_label="Offer guided tours" toggle_description="Suggest a quick tour the first time you visit a new area. You can always start one yourself from a page's Show me around button or the Help page." %}` — never a raw checkbox.
- **Save:** `<form method="post">` with hidden `form_id="tours"`, `pl-btn pl-btn--primary` **Save** button with ≥1.5rem clearance from the toggle above it. `user_settings` grows a third `form_id` branch: bind the form to `member`, `is_valid()` → save → `messages.success(request, "Guided tour preference saved.")` → `redirect(f"{request.path}?tab=notifications")` (full-page POST → Django messages, matching its siblings). No member linked → the existing "not linked to a membership" error path.
- **States:** default on (field default `True`); off = auto-offers stop everywhere immediately (the context helper checks the live field), manual `?tour=` starts and Help-page links still work — the description says so; validation can't realistically fail (one boolean) but form errors would render via `form_field.html` as everywhere else.
- **Dark + light / mobile:** the toggle component and card are already theme- and mobile-correct; nothing bespoke.

### 6.5 "Guided tours" card on the Help page

- **Screen:** `templates/hub/help.html` aside (above the existing Resources links), authenticated members only — coordinate with Spec A: when A's redesigned Help landing ships, this card moves into A's aside slot unchanged.
- **Layout:** a `hub-card` titled "Guided tours". One row per tour from `tours_for(member)` (+ `statuses_for` in one query): tour title in `var(--hub-text)`, a status pill — `hub-pill` "✓ Taken" for completed, muted "Not taken" otherwise — and a right-aligned `hub-btn hub-btn--sm` link: **Start** (never taken / dismissed) or **Retake** (completed), href = the tour's entry URL + `?tour=<key>`. For `guild-lead`, the entry URL needs a guild pk — link to the first guild the member leads/staffs (`member` already exposes the guilds; if they lead several, any one works: the tour targets the tab strip, identical on every guild).
- **Audience gating on rows:** the card lists only tours whose audience the member passes — in particular, **once Spec D ships, the instructor tour row appears only for unlocked members** (`can_create_classes`); showing a Start link that 302s a locked member to the orientation page (shedding the `?tour=` param — a dead loop) is exactly the trap `tours_for` prevents.
- **Empty state (specced, per the rubric):** a member whose role matches no tour sees "No tours for your role yet." in `hub-text-muted` — in practice unreachable (`member-welcome` matches every member), but the template guards it rather than rendering a bare card. Anonymous visitors: the card doesn't render at all.
- **Flag dependency, stated plainly:** the Help page (sidebar link and `help_page` view) is gated on `SiteConfiguration.help_page_enabled` (`hub/views.py:2304`) — the launch assumption is that flag is **on** in production (Spec A ships the Help Center behind it). When it's off, this card and its Start/Retake links simply don't exist; the entry-page **"Show me around" buttons (§6.3) are the flag-independent manual path**, so every tour stays reachable regardless.
- **Mobile:** rows wrap (title + pill on one line, button drops below); the aside already stacks under the main column at narrow widths.

### 6.6 Cross-cutting behavior recap

- **Esc / keyboard mid-tour:** Esc closes instantly and records `dismissed` (unless on the last step / already completed). Arrows navigate. Focus lands on the popover per step and returns to the starting button on close — handled by `pl_tour.js`, since Driver.js 1.x provides none of it (§6.2). No confirmation dialog on exit — leaving a tour must cost one keypress.
- **Missing targets:** filtered before start, numbering stays contiguous, <2 surviving steps aborts an auto-start silently (§6.2).
- **`hx-boost` note:** entry pages load `pl_tour.js` via `{% block extra_js %}`; boosted navigation swaps the body and **re-executes the script**, so it is written as a **guarded `window.plTour` IIFE with no top-level `const`/`let`** (a top-level `const` redeclaration throws on boosted re-execution; the `data-pl-tour-init` flag guards event *binding*, not re-declaration — both guards are needed). The `htmx:afterSettle` Alpine re-init in `base.html` handles the offer card's markup as usual, and the history/swap teardown in §6.2 keeps stale tours out of snapshots.
- **Both themes:** every surface above is token-only; the build must eyeball all six surfaces in Obsidian and Slate.

## 7. Tour definitions (launch content)

Copy is ELI14: short sentences, second person, no filler. Targets use help keys shared with Specs A/B where the concept overlaps (`voting.rank-guilds`, `guild.manage-staff`, `teach.create-class`, …); nav/layout targets get their own `nav.*` / page-local keys, added as `data-help-key` attributes by this spec if Spec B hasn't landed them yet.

### 7.1 `member-welcome` — entry `hub_home`, audience: every member, `opens_sidebar: true`

| # | Target | Title | Body |
|---|---|---|---|
| 1 | *(centered)* | Welcome to FOG | This is the member hub for Past Lives. Want a 30-second look around? Use Next — or press Esc anytime to stop. |
| 2 | `[data-help-key="nav.sidebar"]` (`.hub-sidebar__nav`) | Everything in one place | The sidebar gets you everywhere: guilds, classes, the calendar, your settings. On a phone, the menu button opens it. |
| 3 | `[data-help-key="nav.guilds"]` (sidebar Guilds section header) | Guilds | Guilds are the craft groups that run each studio. Join as many as you like, then book an orientation to get working in the space. |
| 4 | `[data-help-key="nav.calendar"]` (Calendar sidebar link) | Community calendar | Classes, guild meetups, and events all land here. Filter it, open any event, or subscribe from your own calendar app. |
| 5 | `[data-help-key="voting.rank-guilds"]` (Guild Voting sidebar link) | Guild voting | Each month, members rank their top 3 guilds to help decide funding. Your picks save automatically and you can change them anytime. |
| 6 | `[data-help-key="home.get-started"]` (onboarding checklist card; skipped once dismissed) | Your Get started list | A short checklist to finish setting up — profile, photo, first guild. It disappears on its own when you're done. |
| 7 | `[data-help-key="nav.help"]` (Help sidebar link) | Help, whenever | Stuck later? The Help page has short guides for everything you just saw. That's the tour — go explore. |

### 7.2 `guild-lead` — entry `hub_guild_edit` (any guild the member leads/staffs), audience: `is_guild_lead or is_guild_staff`

Steps target the **tab buttons** (always visible), not the tab contents — tab panels are Alpine `x-show` and hidden content can't be spotlighted. Each step describes what's behind the tab. Note the strip itself is an **anonymous inline-styled flex `<div>`** (`guild_edit.html:11`), not `.pl-tabs` (that class exists only on `user_settings.html`) — the build adds `data-help-key="guild.edit-tabs"` **to that container div**, and per-tab keys to the `.vote-tab` buttons.

| # | Target | Title | Body |
|---|---|---|---|
| 1 | *(centered)* | Your guild's control room | This page runs your guild. Each tab below covers one job — here's the quick lap. |
| 2 | `[data-help-key="guild.edit-tabs"]` (the tab-strip container div, `guild_edit.html:11`) | One tab per job | Basic Information is your public page: banner, overview, meeting times. Every other tab saves on its own, so switching tabs never loses work. |
| 3 | `[data-help-key="guild.manage-staff"]` (Staff tab button) | Staff — heads up | Anyone you add here gets full guild authority: edit this page, run orientations, approve classes, send announcements. Every role has the same powers, so add people you trust. |
| 4 | `[data-help-key="guild.run-orientations"]` (Orientations tab button) | Orientations | Set your orientation hours and open slots here. Member bookings show up on the Orientations dashboard in the sidebar, where you confirm them and mark them complete. |
| 5 | `[data-help-key="guild.announcements"]` (Announcements/Emails tab button) | Announcements | Write to your members with the compose wizard — draft, preview, send. Members can propose announcements too; they wait in your review queue until you approve them. |

### 7.3 `instructor` — entry `classes:teach_overview`, audience: every active member until Spec D ships, then `member.can_create_classes` (§5)

Step 2 targets the **persistent "+ New class" header button this spec adds** (§6.3) — today's only create link is the empty-state CTA (`overview.html:31`), absent for anyone who already has classes. The guild-lead-only "Waiting on your review" section (`overview.html:5-8`, inside `{% if is_guild_lead %}`) is deliberately **not** a step target — this is a general-audience tour. On a brand-new instructor's empty overview, steps 3–4 have no targets and are skipped automatically; the tour still lands the one action that matters (create).

| # | Target | Title | Body |
|---|---|---|---|
| 1 | *(centered)* | The teaching portal | Any active member can create a class here. Here's the 20-second version of how it works. |
| 2 | `[data-help-key="teach.create-class"]` (the persistent "+ New class" header button, §6.3) | Start a class | This opens a draft: title, description, dates, price. Drafts are private — nothing goes public until it's been reviewed. |
| 3 | `[data-help-key="teach.review-states"]` ("Needs your attention" card) | Draft → review → published | When you submit a draft, it goes to the guild's lead (if the category has one) and then an admin. Anything waiting on you, or waiting on review, shows up right here. |
| 4 | `[data-help-key="teach.roster"]` (upcoming-classes card) | Your classes and rosters | Click any class to see who signed up, manage the waitlist, email your registrants, or export the roster as a CSV. |

Step 1's copy ("Any active member can create a class here") is revised by Spec D's build when the unlock ships — at that point only unlocked members ever see this tour, and the sentence becomes a statement about *them* ("You're cleared to create classes here").

## 8. Build order (phased; each phase ships green)

1. **Vendor + restyle.** Add `static/js/driver.min.js` + `static/css/driver.css` (v1.3.6, license banners) and the `.pl-tour` overrides in `hub.css`. Dead code until phase 4 — ships green trivially.
2. **State + preference.** `TourState` + manager + migration; `Member.guided_tours_enabled` + `has_completed_tour` + migration. Full model specs. (This phase alone unblocks Spec D's contract.)
3. **Registry.** `core/tours.py` — dataclasses, the three tours with final copy, `tours_for`, `tour_offer_context`. Registry specs (keys resolve, URLs reverse, audiences behave, welcome-modal suppression).
4. **Wiring + runtime.** `hub_tour_state` endpoint + form; context wired into `home`, `guild_edit`, `teach_overview`; `_tour.html`, `_tour_offer.html`, `pl_tour.js` (guarded `window.plTour` IIFE, `onDestroyStarted` → record + `destroy()`, focus management, htmx history/swap teardown); any missing `data-help-key` attributes on targets (incl. the guild-edit tab-strip container div); every key this phase stamps also gets an annotation-only `HELP_KEYS` entry (`article_slug=None`) in `core/help_registry.py` per Spec A §5.1(c) — Spec B's template-walk drift test fails CI on any stamped-but-unregistered key; entry buttons + the persistent "+ New class" header button on the teach overview. View specs.
5. **Settings toggle + Help card.** `_tour_settings.html` + `form_id="tours"` branch; the Help-page aside card. Specs for both.
6. **E2E + release.** `tests/e2e/guided_tour_spec.py`; bump `plfog/version.py` `VERSION` (0.23.58 at spec time — take the next free patch at merge; concurrent worktrees collide on this) and add **one** member-facing `CHANGELOG` entry, e.g. *"Guided tours — FOG can now show you around: take a 30-second tour of the hub, guild tools, or the teaching portal, from the Help page or the 'Show me around' button. New members get offered one automatically (you can turn that off in Settings → Notifications)."*

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py` with `describe_*` / `it_*` (**`context_*` is not a collected prefix — never use it; a `context_` block silently skips its tests**), factory-boy, 100% branch coverage, mutation-clean.

- `core/spec/models/tour_state_spec.py` — `describe_TourState`: `mark_offered` creates once and never overwrites; `mark_completed` upgrades from absent/offered/dismissed; `mark_dismissed` records, and **is a no-op on a completed row** (the sticky guard Spec D leans on); unknown `tour_key` raises `ValueError`; the unique constraint holds; `__str__`.
- `membership/spec/models/member_tours_spec.py` — `guided_tours_enabled` defaults on; `has_completed_tour` true only for `COMPLETED`.
- `core/spec/tours_spec.py` — every tour: key format, `entry_url_name` reverses, non-empty steps each with title + body, audience callables (lead/staff/plain member matrix); `tour_offer_context`: creates `offered` on first eligible visit; suppressed by welcome modal, by `guided_tours_enabled=False`, by dismissed/completed rows; still shown while `OFFERED`; `?tour=` autostarts for eligible members without writing a row, ignored for ineligible/unknown keys.
- `hub/spec/views/tour_spec.py` — endpoint: login required, POST only, unknown key → 404, bad status → 400 with form errors, dismiss/complete write and return 204, completed stays sticky; settings: `form_id="tours"` flips the field, message + redirect to `?tab=notifications`; templates: offer card present/absent per state, Help card rows + pills + empty state (and no instructor row for locked members once D's gate exists), entry buttons render for the right roles, the "+ New class" header button renders on the teach overview in **both** the empty and has-classes branches.
- `tests/e2e/guided_tour_spec.py` (one spec, `-m e2e`, deselected by default) — `login_via_code` (already stamps the welcome modal dismissed, so the offer path is live), goto `/home/`: offer card visible → click **Show me around** → `.driver-popover.pl-tour` appears with step 1's title → **Next** through to **Done** → assert `TourState` is `completed` in the DB and the offer card is gone after reload. A second short scenario presses **Esc** mid-tour and asserts the popover closes (the `onDestroyStarted`-must-call-`destroy()` recipe is exactly the kind of JS contract only a browser test catches) and the state lands `dismissed`. One tour end-to-end is enough; the other two are covered by registry + view specs.
- Gotchas to pin in specs: the offer-row write happens on GET (assert idempotent across refreshes); `mark_dismissed` after `mark_completed`; the `?tour=` param must survive the tab-param-style whitelist treatment (validated against `TOURS`, never echoed into JS unescaped — it flows through `json_script` only).

## 10. Open / deferred

- **Instructor-orientation auto-unlock** — Spec D (`2026-08-10-instructor-orientation-unlock.md`). As revised, D does **not** write `TourState` (its unlock is `Member.instructor_oriented_at`) and ships no tour-offer of its own — it relies on this spec's auto-offer on the member's first post-unlock teach-overview visit (§5). What D changes here: the instructor tour's audience flips to `member.can_create_classes`, and step 1's copy is retouched (§7.3) — scheduled in D's build order (its gate phase) when C is already live; if C lands after D, C ships that audience from day one. The `mark_completed` / `has_completed_tour` contract stays available for future consumers with registered `TOURS` keys.
- **book. subdomain tours** — deferred; tours are hub-only at launch.
- **Multi-page tours** — consciously not built (single-page-per-tour, §1). Revisit only when a tour genuinely can't live on one page.
- **Site-wide kill switch** (`SiteConfiguration` flag à la `help_page_enabled`) — skipped as YAGNI: the per-user toggle plus the in-code registry (deleting a tour from `TOURS` removes it everywhere) already cover it.
- **Admin tour, tour analytics, per-guild tour state, re-offer campaigns** — all out of scope; no current need.
