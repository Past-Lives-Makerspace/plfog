# Auto-Navigating, Role-Based Guided Tours — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-27
**Surface:** FOG hub (`pastlives.test` / members apex) — the whole authenticated hub, plus the classes teaching and admin surfaces that render on the apex. Runtime: `static/js/pl_tour.js`, registry `core/tours.py`, state `core.models.TourState`.
**Related:** the original guided-tour work (Spec C, in-code in `core/tours.py` / `pl_tour.js`); `docs/HELP_AUTHORING.md` (Tours section); `core/help_registry.py` drift guard (`tests/hub/help_keys_spec.py`).

---

## 1. Summary

Today a guided tour is a spotlight lap of **one page**: the moment you navigate, the tour is destroyed on purpose (`Tour` docstring: "Multi-page tours are consciously not built"). This spec rebuilds the runtime so a tour can **drive the browser**: it narrates a step, then moves to the next screen for you — navigating to another page or flipping a tab on the same page — detects that the new screen has loaded, and keeps narrating there. You still fill in real content yourself (typing a class description, picking an orientation time); the tour just gets you to the right place, in order, hands-free. The payoff is a live demo the owner can *click once and let run*, and a genuinely guided onboarding for new members, instructors, guild leads, and admins.

We also add a **fourth role tour — Admin** — so the four are Member, Instructor, Guild Lead, and Admin.

### Locked decisions (from the brief + codebase scout)

| Decision | Choice |
|---|---|
| Central design change | Invert the "navigation = destroy" contract. Navigation now **persists and resumes** the tour instead of tearing it down. |
| Resume carrier | `sessionStorage` (survives full reloads within an origin) **plus** a `?tour=<key>&step=<n>` URL param (survives everything, is the authoritative seed). The existing `?tour=` autostart is extended with `&step=`. |
| Do we ever cross subdomains? | **No.** Every tour target reverses to a **relative** path on the members apex (teach + admin are member-only; catalog/detail/register also resolve on the apex). Steps must never point at an absolute `book.pastlives.test` URL — that is the one thing that would break resume (different origin, different `sessionStorage`). |
| Same-page tab switch | Flip the Alpine tab variable directly (precedent: `sidebarOpen`), then wait for the now-visible panel. `click` on the tab button is the documented fallback. |
| Between-page navigation | Prefer an **htmx boosted** GET (body swap + `pushState`, no white flash) with `?tour=&step=` on the pushed URL; fall back to `window.location.assign` if htmx is unavailable. |
| Backward compatibility | A `TourStep` with none of the new action fields set behaves **exactly** as today (highlight-only, single page). The three existing tours keep working unchanged until we script them. |
| New DB schema? | **None.** `TourStep` is an in-code frozen dataclass (no model, no migration). `TourState` is untouched and keeps recording offered / completed / dismissed. |
| Admin tour audience | `member.is_fog_admin` **or** the member holds any `AdminCapability`. Entry page: Admin Tools (`/tools/`). |
| Robustness posture | Optimized for the **specific demo paths**, not generality. Every step degrades gracefully (skip on missing target, abort on wrong page) and the tour can always be ended with Esc / the popover ✕. |

---

## 2. What already exists (reuse, don't reinvent)

The build is mostly *assembly* — the tour spine, state model, offer card, settings toggle, and Help cards are all in place. This table maps each need to the existing plumbing.

| Need | Existing thing | Location |
|---|---|---|
| Tour registry (frozen dataclasses + `TOURS` dict) | `Tour` / `TourStep`, `TOURS`, `tours_for()`, `entry_url_for()`, `help_card_rows()`, `_tour_payload()`, `tour_offer_context()` | `core/tours.py` |
| Per-user tour state (offered/completed/dismissed) | `TourState` + `TourStateManager` (`mark_offered/completed/dismissed`, `status_for`, `statuses_for`) | `core/models.py:1154-1246` |
| State-recording endpoint | `tour_state` view + `TourStateForm` | `hub/views.py:157`, `hub/forms.py:3095`, url `hub_tour_state` (`hub/urls.py:350`) |
| Runtime controller (lazy Driver.js load, offer binding, sidebar flip, focus mgmt, state POST) | `window.plTour` IIFE | `static/js/pl_tour.js` |
| Vendored Driver.js v1.3.6 + CSS (lazy-loaded) | `static/js/driver.min.js`, `static/css/driver.css` | referenced from `templates/hub/partials/_tour.html` |
| Payload + offer template | `_tour.html`, `_tour_offer.html` | `templates/hub/partials/` |
| Offer card CSS + popover theming (dark/light overlay already computed) | `.pl-tour-offer`, `.pl-tour` popover class | `static/css/hub.css` / `components.css`; theme handled in `runTour()` (`pl_tour.js:138-145`) |
| Settings toggle `guided_tours_enabled` | `Member.guided_tours_enabled` field, `TourSettingsForm`, `_tour_settings.html` | `membership/models.py:557`, `hub/forms.py:3120`, `templates/hub/_tour_settings.html` |
| Help-page "Guided tours" card rows | `help_card_rows(member)` → `hub_help` | `core/tours.py:268`, `templates/hub/help.html` |
| Body-level Alpine state for the sidebar flip precedent | `x-data="{ sidebarOpen: … }"` on `<body hx-boost="true">` | `templates/hub/base.html:72` |
| Guild-edit tab machinery (Alpine `section`, **also seeds from `?tab=`**) | `x-data="{ section: <?tab=…> || 'basic' }"` | `templates/hub/guild_edit.html:5` |
| `[data-help-key]` selector convention + drift guard | help registry + template-walk test | `core/help_registry.py`, `tests/hub/help_keys_spec.py` |
| Existing pk-resolver precedent (guild-lead entry) | `_first_staffed_guild_pk(member)` | `core/tours.py:74` |

### Genuine gaps to close (kept small)

1. **`TourStep` action fields** — `navigate`, `navigate_kwargs`, `tab_set`, `click`, `wait_for` (all optional). §4.
2. **Multi-page/segment resume in `pl_tour.js`** — the inverted contract, segment grouping, wait/retry, location assertion, graceful skip, "advancing" affordance, URL-param cleanup. §5 + §6.
3. **Site-wide payload availability** — a `core.context_processors.tour_runtime` context processor + including the bootstrap in the three base templates so any page a tour lands on can re-hydrate. §5.
4. **Payload additions** — `_tour_payload` resolves each step's `navigate` to a concrete relative href (with member-resolved kwargs), tags each step with a stable `page_id` for segment grouping + location assertion, and drops steps whose resolver raises. §5.
5. **A fourth tour (`admin`) + one new audience predicate + two pk/slug resolvers** (a class for the instructor tour, a guild/class for the member tour). §5 + §6.
6. **New `[data-help-key]` hooks** on the demo-path elements that have none, each registered in `core/help_registry.py`. §6 (hooks table).
7. **CHANGELOG + VERSION bump** (1.19.1 → 1.20.0). §8.

---

## 3. Where the code lives

```
core/
  tours.py                  # extend TourStep; add TOURS["admin"]; extend member/instructor/guild-lead
                            #   step lists; audience predicate _admin_audience; resolvers
                            #   (_demo_guild_slug, _demo_class_slug, _instructor_class_pk);
                            #   _tour_payload gains navigate-href resolution + page_id + skip-on-raise
  context_processors.py     # NEW: tour_runtime(request) -> {tour_json, show_tour_offer, tour_autostart}
  help_registry.py          # register every NEW data-help-key (and stamp the already-registered-but-unused ones)
plfog/settings.py           # add core.context_processors.tour_runtime to context_processors list
static/js/pl_tour.js        # the runtime rewrite (segments, resume, wait/retry, location guard, advancing UI)
static/css/hub.css          # .pl-tour-advancing overlay/spinner (theme tokens)
templates/hub/base.html            # include hub/partials/_tour.html in extra_js (already there on some pages -> move to base)
templates/classes/teach/base.html  # include the tour bootstrap (teaching portal is a tour surface)
templates/classes/base_public.html # include the tour bootstrap (catalog/detail/register are member-tour stops)
templates/**/*.html         # add the new data-help-key attributes (hooks table in §6)
plfog/version.py            # VERSION 1.20.0 + member-facing CHANGELOG entry
core/spec/tours_spec.py             # registry + payload + audience + resolver tests
core/spec/context_processors_spec.py# tour_runtime gating tests
tests/hub/help_keys_spec.py         # extend expected-keys set for the new hooks
tests/e2e/                          # NEW auto-nav Playwright spec (the real runtime guard)
```

No new app. `core.py`/`hub` stay inside the existing coverage + mypy scope.

---

## 4. Data model

**No database change. No migration.** `TourState` (offered/completed/dismissed, one row per `(user, tour_key)`) is exactly right for multi-page tours: a tour is one lifecycle regardless of how many pages it spans. Completion is recorded once, on the true final step.

### `TourStep` — extended in-code dataclass (`core/tours.py`)

Frozen dataclass, all new fields optional with safe defaults, so today's highlight-only steps are unchanged.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `target` | `str \| None` | *(required)* | CSS selector to spotlight; `None` = centered popover. (unchanged) |
| `title` | `str` | *(required)* | Title Case step title. (unchanged) |
| `body` | `str` | *(required)* | ELI14 narration, 1-2 sentences. **No dashes** in new copy (arrow `->` allowed). (unchanged) |
| `navigate` | `str \| None` | `None` | URL **name** (preferred) or literal path to load **before** this step. Resolved to a concrete **relative** href at payload-build time. `None` = stay on the current page. |
| `navigate_kwargs` | `Callable[[Member], dict] \| dict \| None` | `None` | kwargs for `reverse(navigate, kwargs=…)` (e.g. a guild pk, a class pk). A callable is resolved with the member at payload-build time. If it raises `ValueError`, the step is **dropped** (graceful shorten). |
| `query` | `dict[str, str] \| None` | `None` | query string appended to the resolved href (e.g. `{"tab": "payments"}`, `{"audience": "class:42", "lock": "1"}`). |
| `tab_set` | `tuple[str, str] \| None` | `None` | `(alpine_var, value)` to flip an Alpine tab on the current page **before** showing (e.g. `("section", "orientations")`). No navigation. |
| `click` | `str \| None` | `None` | selector to click before this step — documented fallback for a link/tab button when `tab_set`/`navigate` do not fit. |
| `wait_for` | `str \| None` | `None` | selector that must be present **and** visible before the popover shows; defaults to `target`. Backs the async-content / Alpine-hydration wait. |

Rules kept from CLAUDE.md: frozen dataclass, `from __future__ import annotations`, full type hints, meaningful behavior documented in the docstring. No `help_text`/`__str__` — this is not a Django model.

### `Tour` — one small addition

`Tour` keeps `key`, `title`, `entry_url_name`, `entry_url_kwargs`, `audience`, `steps`, `opens_sidebar`. A multi-page tour's `entry_url_name` is simply **the first step's page** (where the offer/autostart lives); every later page is reached by a step's `navigate`. No structural change needed — the step list carries the itinerary.

### Client payload (JSON, what `pl_tour.js` consumes)

`_tour_payload` already emits `{key, title, steps[], state_url, autostart, opens_sidebar}`. Each step object gains:

```jsonc
{
  "target": "[data-help-key=\"voting.rank-guilds\"]",
  "title": "Guild Voting",
  "body": "Each month you rank your top three guilds ...",
  "navigate": "/guilds/voting/",        // concrete RELATIVE href, or null
  "query": null,                          // already folded into navigate at build time
  "tab_set": ["section", "orientations"], // or null
  "click": null,                          // or null
  "wait_for": "[data-help-key=\"voting.rank-guilds\"]", // defaults to target
  "page_id": "hub_guild_voting"           // stable id: last navigate target carried forward; used for
                                          //   segment grouping + location assertion
}
```

`page_id` is computed server-side: it is the URL name of the most recent `navigate` at or before this step (the entry page for steps before any navigate). Consecutive steps with the same `page_id` **and** compatible `tab_set` form one **segment** (one Driver.js instance). A change in `page_id` = a navigation hop; a change in `tab_set` on the same `page_id` = a tab hop.

---

## 5. Business logic (the runtime + the registry)

### 5.1 `core/tours.py` — payload resolution (thin, fail-loud, graceful-skip)

`_tour_payload(tour, member, *, autostart)` (now takes `member`):

- For each step: resolve `navigate` via `reverse(name, kwargs=resolve(navigate_kwargs, member))`, append `query`, and set `page_id`. If `navigate_kwargs` (or a `target`-less resolver) raises `ValueError`, **drop the step** and continue (mirrors the runtime's "missing target → drop" philosophy: a demo with no eligible class simply skips the register step rather than 500ing). Log at `warning`.
- Never emit an absolute cross-origin href. If a resolved href is absolute and its host differs from the members apex, raise a **coding-error** `ValueError` at build time (fail loud in tests, never on stage) — steps must be relative.

New resolvers (frozen-callable style, like `_first_staffed_guild_pk`):

- `_admin_audience(member) -> bool` — `member.is_fog_admin or member.has_admin_capability()` (or `AdminCapability.objects.filter(member=member).exists()` if no helper exists). Fail loud only inside `_validate` paths; audience predicates return bool.
- `_demo_guild_slug(member) -> dict` — a guild for the member tour's orientation stop: first **active** guild that currently exposes open orientation slots; else first active guild; raise `ValueError` if none (step drops).
- `_demo_class_slug(member) -> dict` — a class for the member tour's register stop: first `ClassOffering.objects.bookable()` (future, published, seats); raise `ValueError` if none (step drops).
- `_instructor_class_pk(member) -> dict` — the instructor's most-recent class for the roster + announcement stops: `member`'s newest `ClassOffering`; raise `ValueError` if none (both steps drop, tour shortens to create+submit).

`tours_for`, `entry_url_for`, `help_card_rows`, `tour_offer_context` keep their signatures. `entry_url_for` still returns `entry_url + "?tour=<key>"` (step 0 implied).

### 5.2 `core/context_processors.py::tour_runtime(request)` — site-wide payload

The single choke-point that decides, on **every** request, whether this page should emit a tour payload:

1. Anonymous / no member → `{}` (nothing).
2. `?tour=<key>` present, member eligible → **autostart/resume** payload with `resume_step = int(request.GET.get("step", 0))`, clamped to a valid index. (No `TourState` write — a driven hop is not an offer.) Works on **any** page, which is what makes resume possible.
3. Otherwise, on a tour's **entry page** (`request.resolver_match.url_name == tour.entry_url_name`) → reuse the existing offer/auto-offer guard logic from `tour_offer_context` (toggle on, audience passes, welcome modal not showing, `TourState` absent or still `OFFERED`; first eligible GET writes the `offered` row). Emits `show_tour_offer`.
4. Else `{}`.

This **replaces** the three per-view `tour_offer_context` calls (`hub_home`, `hub_guild_edit`, `classes:teach_overview`) with one processor — the offer still appears only on entry pages (guard 3), and resume now works everywhere (guard 2). The processor returns `{tour, tour_json, show_tour_offer, tour_autostart, tour_resume_step}`.

`_tour.html` (payload `json_script` + offer + `pl_tour.js`) moves from per-page includes into the **three base templates** (`hub/base.html`, `classes/teach/base.html`, `classes/base_public.html`) inside their `extra_js` block, gated on `{% if tour_json %}` exactly as today. Driver.js itself stays lazy (loaded only when a tour actually runs), so ordinary pageviews pay only ~2 KB for `pl_tour.js`.

### 5.3 `pl_tour.js` — the inverted contract (the heart of this spec)

The controller becomes a **segment player over a persisted itinerary**. Prose first, then the loop.

**Persisted running state** (`sessionStorage["plTourRun"]`): `{ key, index, total }` where `index` is the **global** step index into the full itinerary. Written on start and before every hop; cleared on completion, dismissal, or user-initiated navigation away.

**Segment building.** From the payload's step list, group into segments by `(page_id, tab_set)`. `buildSegment(globalIndex)` returns the maximal run `[a..b]` containing `globalIndex` whose steps share the current page and tab context. Driver.js is built with only those steps; it starts at local offset `globalIndex - a`.

**Custom Next/Back (Driver.js `onNextClick` / `onPrevClick`).** Providing these hooks stops Driver from auto-advancing, so we own the boundaries:
- `onNextClick`: not last-in-segment → `driverObj.moveNext()`. Last-in-segment with more steps globally → `advance(+1)`. Last-in-segment and globally last → `driverObj.destroy()` → `onDestroyStarted` records **completed**.
- `onPrevClick`: symmetric with `advance(-1)`.

**`advance(delta)`** — the hop:
1. `index += delta`; write `plTourRun`.
2. Read `nextStep = steps[index]`.
3. If `nextStep.page_id` differs from the current page → **navigate hop**: set `navigatingForTour = true`, then boosted GET to `nextStep.navigate` (or the current path if `navigate` is null but page_id changed — shouldn't happen) with `?tour=<key>&step=<index>` on the pushed URL. Tear down the Driver overlay silently (keep `plTourRun`). Show the **advancing** affordance. Resume runs on the new page's `init()`.
4. Else (same page) → **tab hop**: destroy the current Driver silently, run `applyTabOrClick(nextStep)`, `await waitFor(nextStep)`, then `driveSegment(index)` on the same page.

**`applyTabOrClick(step)`** — reach the panel:
- `tab_set` → find the nearest element whose Alpine scope owns `alpine_var` (walk up from `target`, else `document.body`), set `Alpine.$data(el)[var] = value`; `requestAnimationFrame` once so the `x-show` panel paints.
- else `click` → `document.querySelector(step.click)?.click()`.
- Prefer `tab_set` for guild-edit (var `section`) and user-settings (var `tab`); `click` is the fallback for a link or a server `?tab=` surface that is actually a navigation.

**`waitFor(step)`** — the async guard (this is what makes "detect the new page loaded" real): poll (rAF loop, ~50 ms, cap ~4 s) until `document.querySelector(step.wait_for || step.target)` exists **and** is visible (`offsetParent !== null`). Centered steps (`target == null`, no `wait_for`) resolve immediately. On timeout → **graceful skip**: `console.warn`, then `advance(+1)` (or end if last). Never hangs.

**Resume on load (`init()`)** — the gate:
1. Read `?tour` / `?step` from the URL and the payload. If a payload is present and `autostart`/resume-step applies → this is the authoritative seed: write `plTourRun = {key, index: resumeStep, total}`, load assets, `await waitFor(steps[resumeStep])`, **assert location** (`location.pathname` starts with the expected page's path; if not, clear run + stop silently — a 302 sent us somewhere unexpected), then `driveSegment(resumeStep)`.
2. Else read `plTourRun` from `sessionStorage`. If present and the current location matches `steps[index].page_id`'s path → resume (covers an accidental same-page reload). If location does **not** match → the user navigated away on their own → clear `plTourRun`, do nothing.

**Teardown split** (replaces the single `teardown()` bound to htmx events):
- `teardownForNav()` (we set `navigatingForTour` right before our own boosted GET) → destroy the Driver overlay silently, **keep** `plTourRun`. Bound so `htmx:beforeHistorySave` / `htmx:beforeSwap` do not serialize the overlay into history.
- User-initiated swap (`navigatingForTour` false) → destroy overlay, **clear** `plTourRun` (they abandoned), record nothing.

**End-of-tour hygiene.** On completed **or** dismissed: `postState(...)` as today, clear `plTourRun`, and `history.replaceState` to strip `?tour=&step=` from the URL so a later refresh does not silently restart the tour. Esc / ✕ / overlay click still route through `onDestroyStarted` (dismissed unless truly last step) — unchanged contract, now also clears the run.

**Progress text.** `showProgress` with `progressText: "{{current}} of {{total}}"` where the controller offsets local→global (`progressText` set per segment from `index`/`total`) so the popover reads "6 of 12" across the whole tour, not per page.

**Reduced motion / focus / lazy assets / double-start guards** — all preserved from today (`prefers-reduced-motion`, popover focus in `onHighlighted`, `restoreFocus` in `cleanup`, cached `assetsPromise`, `window.plTour` re-declare guard, `data-pl-tour-init` bind guard).

### 5.4 Failure modes and how each degrades (demo-safety)

| Failure | Guard | Result on stage |
|---|---|---|
| A target never loads / was renamed | `waitFor` timeout (~4 s) → `advance(+1)` | Tour skips that stop and continues; `console.warn` for the operator. Never freezes. |
| A tab panel animates (x-transition) | `waitFor` polls until the in-panel target is *visible*, not just present | Popover appears only once the panel is really there. |
| A navigate 302-redirects (e.g. a locked area) | Location assertion after `waitFor` | Tour stops silently instead of spotlighting the wrong element (won't happen for the eligible demo user, but safe). |
| Cross-subdomain (absolute `book.` URL) | Build-time `ValueError` in `_tour_payload` | Caught in tests, never ships; runtime never sees it. |
| Instructor has no class / no bookable class / no active guild | Resolver raises `ValueError` → step dropped at build time | Tour is shorter, still coherent; no 500. |
| Driver.js asset fails to load | Existing `assetsPromise` reject path | Sidebar flip reverted, tour silently does not start; retry next time. |
| User clicks a sidebar link mid-tour (full reload, `hx-boost="false"`) | Resume gate step 2 location mismatch | `plTourRun` cleared, tour ends cleanly — no ghost popover on the new page. |
| Lost `postState` on dismiss | Existing keepalive + self-heal | Row stays `OFFERED`, offer reappears next visit — honest. |

**Pre-flight (operator) checklist** for a clean demo lives in §10 (seed a bookable class, an active guild with orientation slots, and — for the instructor tour — the demo account owning a class with a waitlisted registration).

---

## 6. UI / UX  (completeness checklist applied per screen)

This feature adds **no new forms** — it is runtime behavior over existing screens, plus one small "advancing" affordance, a fourth tour's content, and attribute-only `data-help-key` hooks. The screens below are walked against the checklist; the list-editor items (Add/Delete/Save) do not apply (no formsets), but states, dark/light, mobile, and "no dead end" all do.

### 6.1 The tour popover (Driver.js, `.pl-tour`)

- **Layout & container:** existing Driver.js popover, spotlight overlay, Next / Back / Done / ✕. Progress reads global "N of M" (§5.3).
- **States:** *loading* — while Driver.js assets load and between hops, the **advancing affordance** (below) covers the gap so the screen never flashes bare. *success* — final Done records completed and strips the URL param. *error* — a missing target skips; a wrong page ends cleanly. *empty* — a tour that resolves to <2 steps for this member does not autostart (existing guard), and a manual start shows what remains.
- **No dead end:** Esc / ✕ / overlay click always end the tour; the URL param is stripped so refresh does not restart it.
- **Dark + light:** overlay color already switches on `data-theme` (`pl_tour.js:138-145`); popover uses `.pl-tour` tokens. No new colors. Verify both themes.
- **Mobile:** Driver.js repositions the popover; centered steps have no anchor so they always fit. `opens_sidebar` continues to open the collapsed sidebar before sidebar targets are spotlighted.

### 6.2 The "advancing" affordance (NEW, tiny)

- **Screen / partial:** a single element `#pl-tour-advancing` (added to `_tour.html`), toggled by `pl_tour.js`.
- **Layout:** a low, non-blocking overlay with a small spinner and the label "Taking you there..." (no dashes), shown from the instant a hop is triggered until `waitFor` resolves on the destination. Prevents the "bare page for a beat" flash during a boosted swap or reload.
- **States:** visible only during a hop; removed on arrival or on skip/timeout.
- **Dark + light:** `static/css/hub.css`, class `.pl-tour-advancing`, theme tokens only (`--hub-bg` scrim at low opacity, `--hub-text` label, `--color-tuscan-yellow` spinner accent). No hardcoded colors; `--surface` is not a token (do not use it). Verify both themes.
- **Mobile:** centered, `max-width:100%`, 8px-grid padding; tap-through disabled while shown (it is brief).
- **Reduced motion:** spinner respects `prefers-reduced-motion` (static dot instead of spin).

### 6.3 The offer card (`_tour_offer.html`) — unchanged behavior

- Compact fixed bottom-right card, `role="status"`, "Show me around" / "No thanks". Still rendered only on entry pages (context-processor guard 3). "Show me around" starts at step 0 and the tour drives from there. No change to markup or CSS.

### 6.4 Settings toggle (`_tour_settings.html`) — unchanged

- `Member.guided_tours_enabled` via `components/toggle.html`. Off → no auto-offers (manual "Show me around" and the Help cards still work). Verified by existing tests; no change.

### 6.5 Help-page "Guided tours" card (`help.html`) — grows to four rows

- `help_card_rows(member)` already renders one row per eligible tour with a "completed" tick and a manual-start URL (`entry_url_for` → `?tour=<key>`). Adding the `admin` tour makes it appear automatically for admins. Each row's link now kicks off a full auto-nav lap.
- **Empty state:** a member eligible for zero tours (rare) sees the card's existing empty copy. **Success:** completed tours show the existing tick. No new UI.

### 6.6 New `data-help-key` hooks (attribute-only additions)

Every new hook is registered in `core/help_registry.py` (the drift test `tests/hub/help_keys_spec.py` fails otherwise) and pointed at the relevant Help anchor. Keys marked **(reuse)** already exist in the registry but were never stamped on a template — stamping them is free.

| Tour / stop | Template + element | `data-help-key` | New or reuse |
|---|---|---|---|
| Member: catalog | `classes/public/list.html` — `.cp-filter` / `#cp-filter-form` | `catalog.filter` | new |
| Member: catalog | `classes/public/partials/_list_results.html` — first `.cls-card` | `catalog.class-card` | new |
| Member: spaces | `hub/spaces.html` — `.pl-map` container | `spaces.map` | new |
| Member: register | `classes/public/detail.html` — `.cp-detail__cta` | `class.register-cta` | reuse (`class.register`) |
| Instructor: title/desc | `classes/teach/class_form.html` — wrapper around `#id_title`/`#id_description` | `teach.class-basics` | new |
| Instructor: gallery | `classes/teach/class_form.html` — `#gallery-manager` | `teach.class-gallery` | new |
| Instructor: roster | `classes/teach/class_registrations.html` — registrations table container | `teach.roster-table` | new |
| Instructor: waitlist | `classes/teach/class_registrations.html` — waitlist section | `teach.roster-waitlist` | new |
| Instructor + Admin: composer | `hub/announcement_compose.html` — `[x-ref="wizardForm"]` wrapper | `compose.wizard` | reuse (`announcements.compose`) |
| Guild Lead: welcome msg | `hub/guild_edit.html` — `form.discord_welcome_message` wrapper (Meetings panel) | `guild.welcome-message` | new |
| Guild Lead: meetings | `hub/guild_edit.html` — meeting-schedule block (Meetings panel) | `guild.meetings` | reuse (`guild.meeting-notes`) or new |
| Guild Lead: studio hours | `hub/guild_edit.html` — Studio Hours panel card | `guild.studio-hours` | reuse |
| Guild Lead: events | `hub/guild_edit.html` — Events panel card | `guild.events` | reuse |
| Guild Lead: wishlist | `hub/guild_edit.html` — `form.wishlist` wrapper (Basic panel) | `guild.wishlist` | new |
| Admin: review queue | `classes/admin/overview.html` — `.overview-title` section | `admin.review-queue` | reuse |
| Admin: refunds | `billing/partials/payments_table.html` — table container | `admin.refunds` | new |
| Admin: discount codes | `classes/admin/discount_codes.html` — `.admin-toolbar` | `admin.discount-codes` | new |
| Admin: reconciliation | `billing/admin_reports.html` — `payout_summary` block | `admin.reconciliation` | new |

Reused-but-existing panel hooks the guild-lead tour rides after a `tab_set`: `guild.edit-page` (basic), `guild.run-orientations` (orientations tab btn), `guild.manage-staff` (staff), `guild.announcements` (announcements tab btn), `guild.photo-gallery` (images), `guild.manage-faq` (content). Note `document.querySelector` is **first-match-wins** and `base.html` renders the sidebar twice — sidebar keys (`nav.calendar`, `voting.rank-guilds`, `nav.help`) resolve to the first-rendered copy, which is fine for spotlighting.

**Single-line `{# #}` only** in any template edits (multi-line comments render as visible text — `tests/template_comment_lint_spec.py` guards this).

### 6.7 The four tours — concrete step lists

Narration is member-facing: **no dashes** (arrow `->` allowed), ELI14, 1-2 sentences. "Page" is the URL name that `navigate` reverses to (all relative, members apex). "Action" is how the runtime reaches the step.

#### Member (`member-welcome`) — extends today's 7 hub-home steps into a lap

| # | Page | Action | Target | Narration |
|---|---|---|---|---|
| 1 | hub_home | (start; `opens_sidebar`) | centered | Welcome to your Past Lives member hub. Take a quick lap and I will drive. Use Next to move, or press Esc anytime to stop. |
| 2 | hub_home | — | `nav.sidebar` | The sidebar reaches everything: guilds, classes, the calendar, your settings. On a phone the menu button opens it. |
| 3 | hub_home | — | `home.get-started` | Your Get Started checklist. Finish these and it tidies itself away. |
| 4 | classes:public_list | navigate | `catalog.filter` | Every class and workshop lives here. Filter by guild or date, then open one to see the details. |
| 5 | classes:public_list | — | `catalog.class-card` | Each card is a class. Click it to read what you will make and to sign up. |
| 6 | hub_community_calendar | navigate | `calendar.filter` | Classes, guild meetups, and events all land on the calendar. Filter it or open any event. |
| 7 | hub_community_calendar | — | `calendar.subscribe` | Tap Subscribe to add the whole calendar to your own phone or laptop. |
| 8 | hub_guild_voting | navigate | `voting.rank-guilds` | Each month you rank your top three guilds to help decide funding. Your picks save on their own and you can change them anytime. |
| 9 | hub_spaces | navigate | `spaces.map` | Studios, storage, parking, and desks live on this map. Click any open space to ask about renting it and the team gets your request. |
| 10 | hub_guild_detail (`_demo_guild_slug`) | navigate | `orientation.book-slot` | Before you work in a studio you book an orientation. Pick an open time here and the guild confirms it. |
| 11 | classes:public_class_detail (`_demo_class_slug`) | navigate | `class.register-cta` | Ready to join a class? Hit Register, pick your date, and you are in. If it is full you can grab a waitlist spot. |
| 12 | hub_help | navigate | `nav.help` | Stuck later? The Help page has short guides for everything you just saw. That is the lap. Go explore. |

Steps 10 and 11 drop cleanly if no eligible guild/class exists (resolver raises → step removed at build time).

#### Instructor (`instructor`) — extends today's 5 steps

| # | Page | Action | Target | Narration |
|---|---|---|---|---|
| 1 | classes:teach_overview | (start) | centered | You are cleared to teach. I will walk you from a blank draft to a published class and its roster. |
| 2 | classes:teach_overview | — | `teach.create-class` | Start here. This opens a private draft that nobody sees until it is reviewed. |
| 3 | classes:teach_class_create | navigate | `teach.class-basics` | Give it a clear title and describe what members will make and learn. Type your own here; I will wait. |
| 4 | classes:teach_class_create | — | `teach.class-gallery` | Drag in photos of the finished project and the space. Good images fill seats. |
| 5 | classes:teach_class_create | — | `teach.class-schedule` | Set the dates and times. A series just adds more sessions. |
| 6 | classes:teach_class_create | — | `teach.class-pricing` | Set the price, the number of seats, and the waitlist. This is what members see. |
| 7 | classes:teach_class_create | — | `teach.submit-for-review` | When it is ready, submit for review. It goes to the guild lead and then an admin before it publishes. |
| 8 | classes:teach_class_registrations (`_instructor_class_pk`) | navigate | `teach.roster-table` | Here is who signed up. Open a person's menu to remove them and a seat frees up. |
| 9 | classes:teach_class_registrations | — | `teach.roster-waitlist` | Someone waiting? Promote them from the waitlist and they take the open seat and get a confirmation. |
| 10 | hub_compose (`query: audience=class:<pk>, lock=1`) | navigate | `compose.wizard` | Need to reach everyone in this class? Write an announcement here. It is already aimed at just this class. |
| 11 | classes:teach_overview | navigate | centered | That is the teaching lap. The Instructor Quickstart on the Help page covers welcome emails and more. |

Steps 8 to 10 drop if the demo instructor owns no class (resolver raises); the tour ends after step 7 + a centered close.

#### Guild Lead (`guild-lead`) — extends today's tab-button lap into a full same-page lap

All steps are on `hub_guild_edit` (entry kwargs = guild pk via existing `_first_staffed_guild_pk`). Each stop `tab_set`s the Alpine `section` var, then spotlights the now-visible panel element. No navigation between steps -> very robust.

| # | `tab_set` | Target | Narration |
|---|---|---|---|
| 1 | (start) | centered | This page runs your guild. Every tab is one job and each saves on its own. I will flip through them. |
| 2 | `section=basic` | `guild.edit-page` | Basic Information is your public page: banner, overview, meeting times. |
| 3 | `section=orientations` | `guild.run-orientations` (tab btn) | Set your orientation hours and open slots here. Bookings show up on the Orientations dashboard to confirm. |
| 4 | `section=staff` | `guild.manage-staff` | Anyone you add here gets full guild authority. Add people you trust. |
| 5 | `section=announcements` | `guild.announcements` (tab btn) | Write to your members here: draft, preview, send. Member proposals wait in your review queue. |
| 6 | `section=meetings` | `guild.welcome-message` | This is the welcome new members get when they join your guild. Make it warm. |
| 7 | `section=studio_hours` | `guild.studio-hours` | Post when your studio is open so members know when they can drop in. |
| 8 | `section=meetings` | `guild.meetings` | Set your regular meeting day and time so they show on your page and the calendar. |
| 9 | `section=events` | `guild.events` | Post one-off events like a demo night or a field trip. |
| 10 | `section=images` | `guild.photo-gallery` | Add photos of your space and members' work to the gallery. |
| 11 | `section=content` | `guild.manage-faq` | Answer common questions in the FAQ and link out to guides and sign-up sheets. |
| 12 | `section=basic` | `guild.wishlist` | List the tools and supplies your guild wants. Members and donors can see it. |
| 13 | — | centered | That is the lap. The Guild Lead Quickstart on the Help page details every tool, and the Cartographers Guild is a full example to borrow from. |

Note: "welcome email" in the brief maps to the guild's **welcome message** (`discord_welcome_message`) — there is no per-guild welcome *email*; the narration says "welcome message" to stay honest. Flagged in §10.

#### Admin (`admin`) — NEW tour, audience `_admin_audience`, entry `hub_admin_tools`

Server `?tab=` surfaces are plain navigations (relative paths), not Alpine — the runtime just hops between them.

| # | Page | Action | Target | Narration |
|---|---|---|---|---|
| 1 | hub_admin_tools | (start) | centered | These are the admin controls. I will show the five you will reach for most. |
| 2 | hub_compose | navigate | `compose.wizard` | Send a site wide announcement here. Pick who gets it, write once, and it can go to email, push, and Discord. |
| 3 | billing_admin_dashboard (`query: tab=payments`) | navigate | `admin.refunds` | Every charge is here. Open one to issue a refund; the member gets it back and a receipt. |
| 4 | classes:admin_overview | navigate | `admin.review-queue` | Classes waiting on you sit at the top. Review the details, then approve or send it back with a note. |
| 5 | classes:admin_discount_codes | navigate | `admin.discount-codes` | Create discount codes for a class or a promotion here, with limits and an expiry date. |
| 6 | billing_admin_reports | navigate | `admin.reconciliation` | This report breaks down what each guild is owed so you can reconcile payouts. |
| 7 | hub_admin_tools | navigate | centered | That is the admin lap. Each tool has a full guide on the Help page. |

---

## 7. Notifications / emails / activity

None. Tours are client-side + a `TourState` row. The only server write is the existing `hub_tour_state` POST (offered/completed/dismissed). No emails, no `SiteActivity`, no `core/triggers.py` changes.

---

## 8. Build order (each phase ships green: full suite + `ruff` + `mypy` + `manage.py check`)

1. **Registry schema + resolvers (no behavior change yet).** Extend `TourStep` with the action fields; add `_admin_audience`, `_demo_guild_slug`, `_demo_class_slug`, `_instructor_class_pk`; extend `_tour_payload` (navigate resolution, `page_id`, skip-on-raise, cross-origin build-time guard). Keep the three tours highlight-only for now. Tests in `core/spec/tours_spec.py`. Green.
2. **Site-wide payload + context processor.** Add `core.context_processors.tour_runtime`; wire it in settings; move `_tour.html` into the three base templates; remove the three per-view `tour_offer_context` calls. Behavior identical to today (offer on entry pages, no resume yet since no tour uses `navigate`). Tests in `core/spec/context_processors_spec.py`. Green.
3. **Runtime rewrite in `pl_tour.js`.** Segment player, `advance`, `waitFor`, resume gate, teardown split, URL-param cleanup, global progress, `.pl-tour-advancing`. No Python change. Guarded by the new e2e (phase 6). Green (Python suite unaffected).
4. **New `data-help-key` hooks + help registry.** Add every attribute in the §6.6 table; register new keys + Help anchors in `core/help_registry.py`; extend `tests/hub/help_keys_spec.py` expected set. Green.
5. **Script the four tours.** Fill in the §6.7 step lists (extend member/instructor/guild-lead, add `admin`). This is where `navigate`/`tab_set` first appear. Green.
6. **e2e (the real runtime guard).** Playwright spec in `tests/e2e/`: member tour auto-advances across >=2 pages; resume after a manual reload via `?tour=&step=`; a removed target is skipped; completion writes `TourState.completed`; Esc mid-tour strips the URL param. Green.
7. **Housekeeping.** Bump `plfog/version.py` VERSION 1.19.1 -> **1.20.0**; add ONE member-facing CHANGELOG entry stamped `1.20.0` (curate per the changelog rule):
   > **Guided tours that drive themselves.** Pick a tour and it walks you through the app hands free, moving from screen to screen while you follow along. There are tours for members, instructors, guild leads, and admins. Turn them on or off anytime in Settings.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*`, factory-boy, 100% branch on touched Python, run in the `plfog-web` Docker image.

**Python (`core/spec/tours_spec.py`):**
- `TourStep` defaults: a step with no action fields serializes to today's payload shape (backward compat).
- `_tour_payload`: `navigate` resolves to the correct relative href; `query` folds in; `navigate_kwargs` callable resolved with the member; `page_id` carries forward correctly across steps; a resolver raising `ValueError` drops exactly that step (others intact); an absolute cross-origin href raises at build time.
- Resolvers: `_admin_audience` true for `is_fog_admin` and for a capability-holder, false otherwise; `_demo_guild_slug` / `_demo_class_slug` / `_instructor_class_pk` pick the expected object and raise `ValueError` when none exists.
- `tours_for` includes `admin` only for admins; `help_card_rows` renders four rows for an admin, fewer for others.

**Python (`core/spec/context_processors_spec.py`):**
- Anonymous / no member -> empty. `?tour=<key>` eligible -> autostart payload with clamped `resume_step`, no `TourState` write. Entry page + toggle on + no row -> offer + `mark_offered`. Toggle off / welcome modal showing / row `dismissed` -> no offer. `?tour=` for an ineligible member -> empty (param ignored). Non-entry page without `?tour=` -> empty.

**Drift guard (`tests/hub/help_keys_spec.py`):** extended expected set; every new `data-help-key` maps to a registry entry (test fails if a hook is added without registering it).

**e2e (`tests/e2e/`, Playwright):** the runtime behaviors in §8 phase 6. This is the guard that the auto-nav actually works — the Python tests cannot exercise `pl_tour.js`.

**Gotchas:** `_demo_class_slug` depends on a *future* bookable class (past-dated example class is not bookable) — the e2e/factory must create a future offering. Guild-lead tab hops need Alpine hydrated before `tab_set`; the e2e waits on the panel, matching `waitFor`.

## 10. Open / deferred

- **"Welcome email" (guild lead)** is mapped to the guild **welcome message** (`discord_welcome_message`); there is no per-guild welcome email today. If one is wanted, that is a separate feature — out of scope here.
- **Spaces request is modal-gated** — the request form only exists inside a hotspot modal (`_space_request_form.html`), and the top-level request entry was intentionally removed. The member tour spotlights the **map** and narrates the flow rather than auto-opening a specific hotspot (which would depend on a particular space existing). Auto-opening a hotspot is deferred.
- **Cross-subdomain tours are explicitly out of scope.** Every step stays on the members apex. If a future tour must show the *real* `book.` public site, it needs the `?tour=&step=` param carried across origins **and** the payload re-rendered on the book origin (the context processor already can, since `TOURS` is shared) — deferred until needed.
- **Global progress text** is a nice-to-have polish (§5.3); if it proves fiddly across skipped steps, fall back to per-segment progress without blocking the release.
- **Operator pre-flight for a clean live demo** (not code): sign in as the demo owner; ensure at least one active guild with open orientation slots and one future bookable class exist; for the instructor lap, the demo account should own a class that has a confirmed registration and a waitlisted registration so the remove/promote stops have something real to act on.
- **Deep-link start from the Help card** already works (`?tour=<key>`); a future enhancement could let a step deep-link mid-tour (`?tour=&step=`) from a Loom or email, which this design supports for free.
```
