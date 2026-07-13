# Class Catalog Timeframe Filter & Count Reconciliation — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-13
**Surface:** book CMS public catalog (`book.pastlives.test` / `book.pastlives.space`) — `/classes/` (`classes:public_list`, also reached via `classes:public_category`).
**Related:** none.

---

## 1. Summary

On the public class catalog, the hero shouts **"32 Upcoming Sessions"** while the list below shows only **~5 cards** — the two numbers count different things (raw session *dates* vs. bookable, grouped *class cards*), and there is no visible sign of what timeframe is on screen. A visitor reasonably concludes classes are hidden or the page is broken.

This feature does two things:

1. **Makes the timeframe visible and adjustable** — a `<select name="within">` in the filter bar (Next 30 / 90 / 180 days / All upcoming) sitting beside the existing Guild dropdown. **Default is "All upcoming"** so nothing is hidden until the visitor chooses to narrow.
2. **Reconciles the headline count** — the prominent hero number becomes the count of **visible bookable class cards for the current filter** (`paginator.count`), labeled "Classes", so it always matches the "Showing X–Y of N classes" summary and the cards on screen.

No schema changes, no new models — this is view glue, one template select, one small CSS rule, and a count-sync mechanism.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Timeframe control type | Visible `<select name="within">` in the filter bar (not a hidden default window, not chips) — the surprise was *invisible* hiding. |
| Options | Next 30 days / Next 90 days / Next 180 days / **All upcoming**. |
| Default | **All upcoming** — nothing hidden by default. |
| Hero number meaning | Count of visible bookable class **cards** for the current filter (`paginator.count`), labeled "Classes" — not the raw session-date count. |
| Sessions stat | Drop the misleading "Upcoming Sessions" headline. A secondary "N sessions" caption is **not** added (kept minimal); see §10 if we later want it. |
| Flexible / undated classes under a timeframe | **Kept visible in every timeframe** (they mirror `bookable()`'s own "flexible always qualifies" rule). Rationale + the stricter alternative in §5 and §10. |

---

## 2. What already exists (reuse, don't reinvent)

The build is almost entirely assembly on top of the existing HTMX filter flow.

| Need | Existing thing | Location |
|---|---|---|
| HTMX-partial-aware list view | `public_list()` | `classes/views.py:182` |
| Apply all GET-param filters to the queryset | `_apply_browse_filters(qs, request)` | `classes/views.py:154` |
| Bookable set, already annotated `first_session_at` (Min of sessions) | `ClassOffering.objects.bookable()` via `_browsable_classes()` | `classes/views.py:78`, `classes/models.py:121` |
| Precedent for filtering on the `first_session_at` annotation | the `upcoming` filter → `qs.exclude(first_session_at__isnull=True)` | `classes/views.py:177` |
| Group offerings sharing `grouping_key` into one card | `_grouped_catalog()` | `classes/views.py:129` |
| Pagination (25/page) + querystring that already carries every filter | `Paginator(...)`, `filter_querystring` | `classes/views.py:223`, `views.py:235-237` |
| Filter-bar form (HTMX `hx-get`, `hx-trigger="change, submit"`, `hx-push-url`) | `#cp-filter-form` | `templates/classes/public/list.html:29-38` |
| Existing themed select to clone | Guild `<select class="cp-filter__select">` inside `.cp-filter__field--select` | `templates/classes/public/list.html:40-48` |
| Themed select styling (dark+light, custom gold arrow) | `.cp-filter__select` | `static/css/cms-public.css:1117-1144` |
| Reconciled card-count line already on screen | "Showing X–Y of {{ paginator.count }} classes" | `templates/classes/public/_list_results.html:4-12` |
| Hero stat tiles | `#hero-stats` / `.hs-n` / `.hs-l` | `templates/classes/public/list.html:10-14`, CSS `cms-public.css:406-408` |
| `timedelta`, `timezone` already imported in the view | — | `classes/views.py:6`, `views.py:28` |

**Gaps to close (all small):**

- No timeframe param exists → add a `within` branch to `_apply_browse_filters` with a **module-level `WITHIN_DAYS` whitelist** (no bare horizon constant scattered around).
- The hero number is `upcoming_session_count` (session dates) and lives **outside** the HTMX swap target `#cls-results` → it must be re-pointed to `paginator.count` **and** kept in sync on HTMX filter changes (see §6, OOB swap).
- `.cp-filter__select` has **no `option {}` rule** → native option popups can render browser-default white on dark (FRONTEND.md rule 13). Add one rule (also fixes the existing Guild select).

---

## 3. Where the code lives

```
classes/
  views.py                 # WITHIN_DAYS const; within branch in _apply_browse_filters;
                           # hero count → paginator.count; is_htmx + filter_qs_no_within context
templates/classes/public/
  list.html                # + timeframe <select>; hero tile → classes-count partial include
  _list_results.html       # OOB hero-count block (htmx only); timeframe-aware empty state
  _hero_classes_stat.html  # NEW tiny partial: the "N Classes" hero tile body (DRY across the two)
static/css/
  cms-public.css           # + .cp-filter__select option {} rule (theme tokens)
classes/spec/views/
  public_spec.py           # extend: within filter, count reconciliation, empty state
plfog/version.py           # VERSION bump + CHANGELOG entry
```

Home app: `classes`. Everything stays inside the existing coverage/mypy scope (all edits are in already-covered files). No migration.

## 4. Data model

**None.** No new or changed models, no migration. `within` is a transient GET parameter; the timeframe is computed at query time from `bookable()`'s existing `first_session_at` annotation.

## 5. Business logic (view glue — the view stays thin)

The only logic is a whitelist + one queryset filter, added to the existing filter helper.

```python
# classes/views.py (module level)
WITHIN_DAYS = {"30": 30, "90": 90, "180": 180}  # "all"/absent → no upper bound
```

Inside `_apply_browse_filters(qs, request)`, after the existing `upcoming` branch:

```python
within = request.GET.get("within", "").strip()
if within in WITHIN_DAYS:
    horizon = timezone.now() + timedelta(days=WITHIN_DAYS[within])
    # Keep flexible/undated classes in every window — they mirror bookable()'s
    # own rule that a flexible class always qualifies (it has no fixed date to
    # fall outside the window). NULL first_session_at would otherwise be dropped
    # by a bare `__lte`.
    qs = qs.filter(
        Q(first_session_at__lte=horizon) | Q(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
    )
```

Notes / guards:

- **Whitelist, not fail-loud.** A public query param is untrusted; an unknown value (`within=abc`, `within=`) is simply ignored → behaves as "All upcoming". This matches how the other browse params degrade (empty/invalid → skipped) and never 500s. `WITHIN_DAYS` keeps the three magic numbers in one named place (the "introduce a horizon constant" note from the brief).
- **`first_session_at` is already present** — `bookable()` annotates it (`Min("sessions__starts_at")`), and the sibling `upcoming` branch already filters on it, so no extra annotation/join is introduced.
- **Ordering/grouping untouched** — `bookable()` still supplies soonest-first order and `.distinct()`; `_grouped_catalog()` still collapses shared `grouping_key`, so timeframe-narrowing removes whole date-options but never double-counts a card.
- `Q` and `ClassOffering` are already imported in the view module (Q via Django models import used elsewhere; `ClassOffering` at `views.py:65`). `SchedulingModel.FLEXIBLE` is the enum used in `bookable()` itself.

Everything else (hero count, empty state) is presentation, handled in templates + context, below.

## 6. UI / UX  ← completeness checklist applied per screen

Only one screen changes: the catalog list page. It has three touch points — the **timeframe select**, the **hero count**, and the **empty state**.

### 6a. Timeframe select (filter bar)

- **Screen / partial:** `templates/classes/public/list.html`, inside `#cp-filter-form > .cp-filter__row`, immediately **after** the Guild `.cp-filter__field--select` (before the Filter popover toggle button).
- **Layout & container:** a sibling `<label class="cp-filter__field cp-filter__field--select">` — a peer of the Guild select. It is a **primary, always-visible** control (not tucked in the popover), because timeframe is the thing that was invisibly hiding classes.
- **Components used:** no new component — reuse the existing `.cp-filter__select` class (this CMS-public surface's established select). Markup mirrors the Guild select exactly:

  ```html
  <label class="cp-filter__field cp-filter__field--select">
    <span class="cp-filter__field-label">When</span>
    <select name="within" class="cp-filter__select">
      <option value="all"  {% if selected_within == "all" %}selected{% endif %}>All upcoming</option>
      <option value="30"   {% if selected_within == "30"  %}selected{% endif %}>Next 30 days</option>
      <option value="90"   {% if selected_within == "90"  %}selected{% endif %}>Next 90 days</option>
      <option value="180"  {% if selected_within == "180" %}selected{% endif %}>Next 180 days</option>
    </select>
  </label>
  ```

- **Submit / feedback:** **no new JS.** The form already carries `hx-get`, `hx-target="#cls-results"`, `hx-swap="innerHTML"`, `hx-push-url="true"`, and `hx-trigger="change, submit"` — so changing the select fires an HTMX GET that swaps the results grid in place and pushes a shareable `?within=…` URL. Selection is the feedback (the list updates). `selected_within` comes from context: `request.GET.get("within", "all")` (absent → `"all"`, matching the default).
- **`active_filter_count` / Filter badge:** `within` is a visible primary control (like Guild `category`), so it is **not** counted in the popover's "Filter (N)" badge — consistent with `category` today (`views.py:239-250` sums instructor/price/members/free/upcoming only). No change to that block.
- **Pagination:** free — `filter_querystring` (`views.py:237`) is `request.GET` minus `page`, so it already includes `within`; the existing page links carry the timeframe forward with no edit.
- **States:** the select has no empty/loading/error of its own; the results grid it drives owns those (§6c). While an HTMX request is in flight HTMX applies its default `.htmx-request` opacity to `#cls-results` (existing behavior, unchanged).
- **Dark + light:** **reuse `.cp-filter__select`** — it is already theme-correct (`background: var(--bg2); color: var(--text); border: 1px solid var(--border)`, gold arrow via `--gold`), with light tokens on `.cp-page` and dark tokens on `[data-theme="dark"] .cp-page` (`cms-public.css:336, 373`). **Do NOT introduce `.reg-field`/`--hub-input-*`** here — those belong to the registration surface (`classes-register.css`); this catalog page's correct, matching field tokens are the `.cp-filter__*` family. **One CSS gap to close** (FRONTEND.md rule 13 — native option popups don't inherit): add to `cms-public.css`, next to the `.cp-filter__select` rule —

  ```css
  .cp-filter__select option { background: var(--bg2); color: var(--text); }
  ```

  This also fixes the pre-existing Guild select's white-popup risk on dark. No inline `background`/`color` anywhere. Verify both themes.
- **Mobile:** `.cp-filter__row` is already `display:flex; flex-wrap:wrap; gap:10px`, and `.cp-filter__field` is `flex:1; min-width:200px`. Adding a second field means the two selects sit side by side on wide screens and **wrap to full-width rows** below ~480px — no horizontal scroll, no fixed overflow. The 10px gap keeps the 8px-grid rhythm. Verify the Guild + When + Filter button wrap cleanly at 360px.

### 6b. Hero count reconciliation

- **Screen / partial:** `templates/classes/public/list.html` `#hero-stats` (first tile), plus a new tiny partial and an OOB block in `_list_results.html`.
- **The change:** the first hero tile's number becomes **`paginator.count`** (grouped bookable cards for the current filter) with label **"Classes"**, replacing `upcoming_session_count` / "Upcoming Sessions". This makes the headline literally equal the "Showing … of {{ paginator.count }} classes" summary and the card count.

  ```html
  <!-- list.html hero, first tile -->
  <div id="hero-classes-stat">{% include "classes/public/_hero_classes_stat.html" %}</div>
  ```

  ```html
  <!-- NEW templates/classes/public/_hero_classes_stat.html -->
  <div class="hs-n">{{ paginator.count }}</div>
  <div class="hs-l">Class{{ paginator.count|pluralize:"es" }}</div>
  ```

- **Keeping it in sync on HTMX filter changes (the real trap):** the hero lives **outside** `#cls-results`, so a naive `paginator.count` would update on full page load but go **stale** after an HTMX filter swap — re-introducing the very contradiction we're fixing, just reversed. Fix with a small, idiomatic **out-of-band swap**: `_list_results.html` (the HTMX response body) emits the same tile with `hx-swap-oob="true"`, guarded so it only renders for HTMX requests (never as a stray duplicate on full page load):

  ```html
  <!-- top of _list_results.html -->
  {% if is_htmx %}
    <div id="hero-classes-stat" hx-swap-oob="true">{% include "classes/public/_hero_classes_stat.html" %}</div>
  {% endif %}
  ```

  The view adds `is_htmx` to context using the **same condition it already uses to choose the partial** (`views.py:275`): `is_htmx = bool(request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"))`. On a full page load `is_htmx` is False → the OOB block is not rendered inside the embedded include (no artifact); on every HTMX filter/timeframe/pagination response it is True → HTMX matches `#hero-classes-stat` by id and replaces it, so the hero number tracks `paginator.count` in lock-step with the summary line. The shared `_hero_classes_stat.html` partial keeps the two renderings from drifting.
- **The other two tiles** (Guilds = `total_categories`, Instructors = `total_instructors`) are unchanged and are **not** OOB-synced — same as today. `total_categories` is already filter-independent (built from the unfiltered universe); `total_instructors` already reflected only full-page-load filter state before this change, so no new staleness is introduced (noted in §10).
- **Context cleanup:** `upcoming_session_count` is no longer shown in the hero. Leave the manager method (`ClassSession.objects.upcoming_public_count()`) in place; removing the now-unused context key is a harmless optional tidy.
- **Dark + light:** no CSS change — reuses `.hs-n`/`.hs-l` tokens (`--gold-text`/`--text2`), already theme-aware.
- **Mobile:** unchanged — `#hero-stats` already wraps (`cms-public.css:536`); wrapping the tile in an id'd `<div>` keeps it a single flex item.

### 6c. Empty state (timeframe-aware)

- **Screen / partial:** `_list_results.html`, the `{% else %}` branch of `{% if paginator.count %}` (`_list_results.html:8-11`).
- **Today:** a generic "No classes match your filters." + Reset-all link. That reads as a dead end when the cause is simply a narrow timeframe.
- **New behavior — two-tier:**
  - **When a bounded timeframe is active** (`selected_within` in `30/90/180`) and `paginator.count == 0`:
    > **No classes scheduled in the next {{ N }} days.** Try a wider timeframe.
    plus a real action **"Show all upcoming"** that widens without losing the visitor's other filters. It is an HTMX link mirroring the pagination links, pointed at the current filters **minus** `within` (dropping `within` = the "All upcoming" default):

    ```html
    <a class="cp-results__reset-inline"
       hx-get="{% url 'classes:public_list' %}{% if filter_querystring_no_within %}?{{ filter_querystring_no_within }}{% endif %}"
       hx-target="#cls-results" hx-swap="innerHTML" hx-push-url="true">Show all upcoming</a>
    ```

    `N` is rendered from `WITHIN_DAYS[selected_within]` — pass it as `selected_within_days` in context (int or `None`) so the template shows the exact number.
  - **Otherwise** (All upcoming, but other filters exclude everything): keep the existing "No classes match your filters." + the existing **Reset all filters** inline link (`_list_results.html:10`).
- **Context additions:** `selected_within` (str, default `"all"`), `selected_within_days` (int|None), and `filter_querystring_no_within` — built exactly like `filter_querystring` but also popping `within`:

  ```python
  no_within = filter_qs.copy()      # filter_qs already has 'page' popped (views.py:235-236)
  no_within.pop("within", None)
  filter_querystring_no_within = no_within.urlencode()
  ```

- **Feedback / no dead end:** the "Show all upcoming" action is a real, tappable link that returns the widened grid via the same HTMX flow (and OOB-updates the hero to the new count). No 500 on any `within` value (whitelist guard, §5).
- **Dark + light / mobile:** reuses `.cp-results__summary` / `.cp-results__reset-inline` (already themed and mobile-safe, `cms-public.css:1282-1297`). No new CSS.

### Checklist sign-off for this screen

- Primary control obvious? Yes — a labeled "When" select beside "Guild", default "All upcoming".
- Complete the task without a dead end? Yes — narrowing shows a clear reason + a one-tap "Show all upcoming".
- Half-built anything? No — the select drives the same round-trip the other filters use; nothing to "edit but not clear".
- Non-technical member understands it? Yes — plain option labels, "N Classes" headline that matches the list.
- Simple? One select, one relabeled number, one smarter empty message — no new screens.

## 7. Notifications / emails / activity

None. This is a read-only public browse change.

## 8. Build order (phased; each phase ships green)

1. **View logic + context.** Add `WITHIN_DAYS`, the `within` branch in `_apply_browse_filters`, and the new context keys (`selected_within`, `selected_within_days`, `is_htmx`, `filter_querystring_no_within`); point the hero at `paginator.count`. (Model/logic-equivalent layer first.) Full suite + lint + mypy green.
2. **Templates + CSS.** Add the timeframe `<select>`; create `_hero_classes_stat.html`; wire the OOB hero block and the timeframe-aware empty state; add the `.cp-filter__select option {}` rule. Verify HTMX swap keeps hero == summary in **both** dark and light, and mobile reflow at 360px.
3. **Tests** (§9), then **housekeeping:** bump `plfog/version.py` `VERSION` and add/curate the CHANGELOG entry (member-facing — see §9 note).

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*`, factory-boy, run in the `plfog-web` Docker image against the 98% gate. Extend `classes/spec/views/public_spec.py` (has the `published_class` fixture, `CategoryFactory`/`InstructorFactory`/`ClassOfferingFactory`/`ClassSessionFactory`, and a `describe_public_list()` block). Seed offerings at **now+10d**, **now+60d**, **now+200d**, plus one **flexible** (no sessions) to exercise the three windows and the flexible rule.

- **`describe__apply_browse_filters` / within filter**
  - `it_limits_to_the_next_30_days` — `?within=30` returns only the now+10d class; the 60d/200d classes are absent.
  - `it_widens_to_90_and_180_days` — `?within=90` includes 10d+60d; `?within=180` still excludes 200d; `?within=all` (and no param) includes all three.
  - `it_keeps_flexible_classes_in_every_window` — the flexible offering appears at `within=30` (guards the `Q(...FLEXIBLE)` clause).
  - `it_ignores_an_unknown_within_value` — `?within=abc` behaves as All upcoming (no crash, full set).
- **Count reconciliation**
  - `it_shows_the_grouped_card_count_as_the_hero_number` — hero `.hs-n` equals `paginator.count` equals the number of rendered `.cls-card`s, and the label reads "Class(es)" not "Upcoming Session(s)". Assert on the full-page (`list.html`) response.
  - `it_returns_an_oob_hero_count_on_htmx_requests` — GET with `HX-Request: true` header returns the `_list_results.html` partial containing `id="hero-classes-stat"` + `hx-swap-oob="true"` and the same count; a plain full-page GET does **not** contain a stray second `hero-classes-stat` inside the results grid.
  - `it_counts_a_grouped_class_once` — two offerings sharing a `grouping_key` (both within window) yield `paginator.count == 1` (grouping preserved under `within`).
- **Empty state**
  - `it_offers_a_wider_range_when_the_timeframe_is_empty` — all sessions 200d out, `?within=30` → 200, body contains "next 30 days" and a "Show all upcoming" link whose `hx-get` has no `within` param; **not** a 500.
  - `it_preserves_other_filters_in_show_all_upcoming` — with `?within=30&category=ceramics` and an empty result, the "Show all upcoming" link keeps `category=ceramics` but drops `within`.
- **Pagination carry-through**
  - `it_keeps_within_across_pages` — with >25 cards in-window, the page-2 link's querystring contains `within=90`.
- **Update the existing test:** `it_renders_the_upcoming_session_count_in_the_hero` (`public_spec.py:66`) asserts `"Upcoming Session"` is in the hero — that copy is being removed. Retarget it to assert the new "Classes" headline (or fold it into `it_shows_the_grouped_card_count_as_the_hero_number`).

**tz/date-window gotcha:** windows are computed from `timezone.now()` at request time, and `bookable()` also gates on `now`. Seed sessions with explicit `now + timedelta(days=…)` offsets (as the fixture already does) so a class at exactly the boundary isn't flaky; avoid dates within a day of a window edge.

**Changelog note:** the "32 vs 5" confusion is **live on production**, so the fix is member-facing and earns a plain-language entry (e.g. *"The class catalog now lets you choose a timeframe — next 30/90/180 days or all upcoming — and the count at the top matches the classes you actually see."*). Group under one feature entry stamped at the new `VERSION`.

## 10. Open / deferred

- **Flexible classes under a timeframe.** Recommended (§5): always show them, mirroring `bookable()`. The stricter alternative — a bare `first_session_at__lte=horizon` that drops undated/flexible classes from timed windows — is simpler but surprises members ("why did the flexible class vanish at 30 days?"). Deferred unless product prefers the strict reading.
- **Secondary "N sessions" caption.** The locked decision allows keeping a muted sessions figure beneath the headline. Left out to keep three clean tiles; `upcoming_public_count()` still exists if we want to add it later.
- **Instructors tile staleness on HTMX.** `total_instructors` reflects full-page-load filter state and is not OOB-synced (pre-existing). If we later want every hero tile to track filters, extend the OOB block to cover all three tiles — out of scope here.
- **Persisting the visitor's timeframe** across visits (cookie/localStorage) — not requested; the shareable `?within=` URL is enough.

## 11. Review addendum — fold in before building

An adversarial UX review confirmed the tricky parts are sound (dark-mode `<select>`, the OOB hero-sync + `is_htmx` gating, the flexible-class NULL guard, the empty-state escape, count == shown). Three gaps to fix:

1. **The "Instructors" hero tile goes stale after HTMX filtering — a *new* visible mismatch (supersedes the "out of scope" note in §10).** `total_instructors` (`classes/views.py:269`) is computed from the *filtered* qs, so once "Classes" live-syncs via OOB, narrowing to a guild drops "Classes" to e.g. 2 while "Instructors" stays frozen at 15 right beside it — reintroducing the "numbers that don't agree" bug, reversed. Fix: OOB-sync `total_instructors` too, or make it filter-independent like `total_categories` (which is already safe).
2. **Three hero tests break, not one, plus a silent semantic flip.** Beyond `it_renders_the_upcoming_session_count_in_the_hero`, both `it_counts_sessions_not_cards` and `it_shows_zero_gracefully_when_no_upcoming_sessions` (`classes/spec/views/public_spec.py:84-121`) assert the old session-count semantics — and the zero-case seeds a session-less *flexible* class, which is now a card, so the hero renders "1 Class," not "0." Rewrite all three and call out the flexible-counts-as-1 change explicitly.
3. **The claimed loading state doesn't exist on this surface.** §6a says the swap reuses HTMX's default `.htmx-request` dim, but there is NO `.htmx-request` rule in `cms-public.css` (the hub-scoped ones don't reach `.cp-page`). Either add a real `.cp-page #cls-results.htmx-request { opacity: … }` rule (test both themes) or drop the claim.
