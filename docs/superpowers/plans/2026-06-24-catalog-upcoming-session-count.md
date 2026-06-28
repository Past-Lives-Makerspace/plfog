# Catalog "Upcoming Sessions" Count — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-06-24
**Surface:** book CMS `book.pastlives.test` — the public Classes & Workshops catalog (`classes:public_list`, hero stat).
**Related:** `docs/superpowers/plans/2026-06-24-cms-guilds-terminology-relabel.md` (sibling spec — relabels the *adjacent* hero stat; see §0 Coordination). Builds on the catalog grouping work in `docs/superpowers/plans/2026-06-18-series-vs-single-sessions.md`.

---

## 0. Coordination (read first — shared file)

`templates/classes/public/list.html`'s hero block (lines ~10–14) is edited by **two** specs at once. To keep the builds from clobbering each other, the ownership is split by stat:

| Hero stat | Template line (verify at build) | Owned by |
|---|---|---|
| `{{ total_classes }}` / "Classes" | `list.html:11` | **THIS spec** — becomes upcoming-session count + relabel |
| `{{ total_categories }}` / "Categories" | `list.html:12` | sibling `2026-06-24-cms-guilds-terminology-relabel.md` — relabels "Categories" → "Guilds" |
| `{{ total_instructors }}` / "Instructors" | `list.html:13` | neither — untouched |
| Category filter dropdown | `list.html:41` (`<span>Category</span>`) | sibling spec (relabels to "Guild") |

Whichever ships second must rebase on the first rather than overwrite the hero `<div id="hero-stats">` wholesale. This spec touches **only** the first stat (`{{ total_classes }}` → new context var + new label). It does not touch line 12, line 13, or the filter dropdown. Line numbers are approximate — both files are under active edit; **re-grep before editing.**

**Assembled-hero check (whoever ships second owns it).** Neither spec alone verifies the *finished* three-tile hero — each only owns its own tile. So whichever of this spec / the guilds-relabel sibling lands **second** must verify the fully-assembled hero — **"N Upcoming Sessions · M Guilds · K Instructors"** — reads coherently as a row in **both themes (Obsidian + Slate) and on mobile (~360px)**: no awkward wrap, the three tiles stay balanced, and the now-longer first two labels ("Upcoming Sessions", "Guilds" vs. the old "Classes", "Categories") don't crowd or overflow. This is the only place the whole hero is checked end-to-end; the second-mover must not skip it just because its own tile looks fine in isolation.

---

## 1. Summary

The catalog's hero shows three big numbers; the first currently reads e.g. "**42 Classes**". That 42 is the number of *cards* on the page — one per collapsed class type, where a single workshop repeated on six different dates counts as one. That under-sells what's actually for sale: a prospective student looking at the catalog can buy a seat in any *upcoming dated session*, and admins want a true read on how much purchasable inventory is live. This change makes the first hero stat count **individual upcoming sessions** across all publicly-visible classes — so a workshop offered on six dates contributes six — and relabels it so the number and the word agree.

Nothing else moves: the results summary ("Showing 1–25 of 42 classes") keeps counting cards, because it drives the pager and must match the cards on the page. No new buttons, forms, or filters — this is a single number and its label.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| What does the stat count? | **Upcoming sessions** — `ClassSession` rows with `starts_at >= now`, whose parent offering is `public()` (published + non-private). Mirrors catalog visibility so a member can't see a count that includes things they can't reach. |
| Visibility filter to mirror | **`public()`** (`status="published", is_private=False`), *not* `bookable()`. `bookable()` drops a series the instant its first session starts; but its *remaining* sessions are still purchasable inventory, so the count uses `public()` + `starts_at >= now` to catch every still-future session, including later sessions of a part-started series. (See §4 for the precise filter and why this differs from the card list's gate.) |
| Do flexible (session-less) classes count? | **No — they contribute 0.** The stat is explicitly about *dated* inventory ("how many sessions can I book on a date"). A flexible class has no session rows, so it naturally contributes nothing; this is correct, not a bug, and is called out in copy-neutral terms. |
| New label text | **"Upcoming Sessions"** (see §6 for the justification vs. "Available Dates"). Pluralizes correctly at 0 and 1. |
| Results summary ("Showing X–Y of N classes") | **Unchanged — keeps counting cards.** It is the pager's denominator; switching it to sessions would make "Showing 1–25 of 200" lie when only 25 cards render. Explicitly out of scope (§10). |
| Category-filter dropdown badge counts (`cat.class_count`, `list.html:45`) | **Unchanged — stays per-card (distinct grouping keys).** Minor open decision flagged in §10; recommend leaving as-is unless the user asks. |
| Migration? | **None.** Read-only aggregate over existing `ClassSession`/`ClassOffering` rows. |

---

## 2. What already exists (reuse, don't reinvent)

This is assembly, not invention — every primitive the count needs is already in the codebase.

| Need | Existing thing | Location |
|---|---|---|
| Public-visibility gate (published + non-private) | `ClassOfferingQuerySet.public()` | `classes/models.py:117-119` |
| Bookable gate (reference for why we *don't* use it) | `ClassOfferingQuerySet.bookable()` | `classes/models.py:121-140` |
| Session rows with tz-aware `starts_at` | `ClassSession` (FK `class_offering`, related_name `sessions`) | `classes/models.py:1083-1099` |
| Upcoming-filter pattern already used across the app | `sessions.filter(starts_at__gte=timezone.now())` | e.g. `bookable()` at `models.py:133-137`; `upcoming_sessions` filter at `classes/templatetags/classes_tags.py:231-244` |
| The view that builds catalog context | `public_list()` | `classes/views.py:174-269` |
| Where the current (wrong) count is set | `"total_classes": paginator.count` | `classes/views.py:260` |
| Hero stat template markup | `<div class="hs-n">{{ total_classes }}</div><div class="hs-l">Classes</div>` | `templates/classes/public/list.html:11` |
| BDD spec home for catalog view tests | `describe_public_list` (+ `describe_catalog_grouping`) | `classes/spec/views/public_spec.py:58`, `:368` |
| Existing visibility fixtures (`published_class`, draft/private builders) | reused across `public_spec.py` | `classes/spec/views/public_spec.py` |

**Gap to close (small):** there is no manager/queryset method that aggregates *upcoming public sessions*. Today the only upcoming-session logic lives inline in `bookable()` (which gates *offerings*, not *sessions*) and in a template filter (which operates on an already-fetched list). We add one small, tested queryset method (§4). That is the whole gap.

---

## 3. Where the code lives

No new files. Touches three existing ones, all already inside coverage/mypy scope:

```
classes/
  models.py                              # + ClassSessionQuerySet.upcoming_public_count() + wire manager
  views.py                               # public_list(): swap total_classes → upcoming_session_count
  spec/views/public_spec.py              # new it_* count assertions under describe_public_list
  spec/models/  (class_session_spec.py)  # new BDD spec for the queryset method (create if absent)
templates/classes/public/
  list.html                              # hero stat line 11: new var + new label
plfog/version.py                         # VERSION bump + member-friendly CHANGELOG (build time)
```

Home app: `classes`. `ClassSession` currently uses the default manager; we attach a typed `ClassSessionQuerySet` manager (same pattern `ClassOffering` already uses with `ClassOfferingQuerySet`).

---

## 4. Data model

**No schema change. No migration.** This is a read-only aggregate over existing rows — confirm at build that the diff adds no field, index, or constraint, so no `makemigrations` output appears.

### New queryset method (fat model)

Add a `ClassSessionQuerySet` to `classes/models.py` and set it as `ClassSession.objects` (mirrors the existing `ClassOfferingQuerySet` pattern; keeps the default `Meta.ordering = ["starts_at"]`).

```python
class ClassSessionQuerySet(models.QuerySet["ClassSession"]):
    def upcoming_public(self) -> "ClassSessionQuerySet":
        """Future sessions whose offering is publicly visible (published + non-private)."""
        return self.filter(
            starts_at__gte=timezone.now(),
            class_offering__status="published",
            class_offering__is_private=False,
        )

    def upcoming_public_count(self) -> int:
        """How many purchasable, dated sessions are live in the public catalog."""
        return self.upcoming_public().count()
```

Notes on the exact filter — and why it differs from the card list's gate:

- **`starts_at__gte=timezone.now()`** — tz-aware; `timezone.now()` returns an aware UTC datetime and `starts_at` is stored aware, so the comparison is correct across DST. **Boundary: `>=`** — a session starting *exactly* at `now` counts as upcoming (it hasn't ended; a student arriving at the start can still attend). This is asserted in §9.
- **Mirrors `public()`, not `bookable()`.** The catalog *cards* use `bookable()`, which drops a whole series once its first session begins (you can't join a series mid-stream). But the later sessions of that started series are still real, dated, purchasable inventory for the count's purpose, and single future workshops in any published class should all be tallied. Filtering sessions by `public()` + `starts_at >= now` counts every still-future session, which is the truthful "purchase opportunities" metric the user asked for. We inline the two `public()` predicates (`status="published"`, `is_private=False`) across the FK because `public()` lives on the *offering* queryset; keeping them literally identical is enforced by the tests in §9 (draft/pending/archived/private all excluded).
- **Flexible classes contribute 0.** They have no `ClassSession` rows, so they never appear in this queryset — correct, per the locked decision. No special-casing needed.
- **Counts sessions, not distinct dates.** Two different classes meeting on the same calendar day count as two. The metric is "bookable session slots," which is what a buyer actually purchases into.

### View wiring (skinny view)

In `public_list()` (`classes/views.py`), replace the `total_classes` context entry with the new count. Keep `paginator.count` flowing to the template via `paginator` (the results summary uses `paginator.count` directly), so the pager is untouched.

```python
# was: "total_classes": paginator.count,
"upcoming_session_count": ClassSession.objects.upcoming_public_count(),
```

The count is over the **whole public catalog**, deliberately *not* re-filtered by the active browse filters (category/instructor/price). The hero stat is a headline "here's how much is on offer" number, consistent with how `categories`/`total_instructors`/the per-category chips are already computed from the unfiltered universe (`views.py:193-204`). Confirm this intent at build; if the user wants the headline to react to filters, that's a one-line change (filter the queryset) flagged in §10.

---

## 5. Business logic (fat models)

The whole of the logic is the queryset method in §4 — view stays a one-line context assignment, template stays presentation only. No side effects, no notifications, no activity log, no domain exceptions (a count of a filtered queryset can't fail meaningfully; an empty catalog returns `0`). This satisfies fat-models/skinny-views: the visibility rule and the upcoming-window rule live in `ClassSessionQuerySet`, callable and testable in isolation, reusable anywhere a "how many upcoming public sessions" number is later wanted (see §10's wider-sweep note).

---

## 6. UI / UX

One screen, one number, one label. No new interactive controls, no forms, no modals, no list editors — so the famous list-editor checklist (Add/Delete/Save) does not apply here, and the destructive-action and formset rules are N/A by design. The completeness bar that *does* apply is states, copy, theming, and mobile.

- **Screen / partial:** `templates/classes/public/list.html` (hero block, line ~11). This is in the full-page hero, **not** the HTMX-swapped `_list_results.html`, so it renders once on full page load and does not re-render on filter changes — correct, since the stat is the unfiltered headline.
- **Layout & container:** existing `<div id="hero-stats">` grid — three stat tiles (`.hs-n` big number, `.hs-l` label). We reuse the exact same tile markup; only the value expression and the label string change. No new CSS class, no new layout.
- **Components used:** none new. Pure template-value + copy change inside existing hero chrome.
- **The control / element, named explicitly:**
  - Line 11 today: `<div><div class="hs-n">{{ total_classes }}</div><div class="hs-l">Classes</div></div>`
  - Becomes: `<div><div class="hs-n">{{ upcoming_session_count }}</div><div class="hs-l">Upcoming Session{{ upcoming_session_count|pluralize }}</div></div>`
  - **Pluralization:** `|pluralize` so it reads "1 Upcoming Session" and "0 Upcoming Sessions" / "12 Upcoming Sessions" correctly. (Django `pluralize` adds "s" by default — exactly what's needed here.)
- **Label choice (small copy decision, flagged):** **"Upcoming Sessions"** over "Available Dates." Reasons: (1) the codebase already speaks in *sessions* (`ClassSession`, the `upcoming_sessions` template filter, "sessions" in card copy), so the word matches the domain language a member already sees on detail/card; (2) "Dates" is ambiguous (a session is a date *and* a time; two sessions can share a date); (3) "Upcoming" makes the future-only scope self-evident, heading off "why doesn't this match the total number of times this class has ever run?" This is a one-word call — surface it to the user at build for a thumbs-up; the logic doesn't depend on it.
- **States — the only stat that has a meaningful empty state:**
  - **Zero upcoming sessions** (brand-new catalog, or everything's in the past): the queryset returns `0`, the tile renders `0` / "Upcoming Sessions" — a clean, honest "0 Upcoming Sessions," **never blank.** Verified in §9 by asserting `0` renders, not an empty string. (Contrast a naive `len()`-of-nothing or a missing context var, which would render blank — the `count()` always returns an int, so the tile always has a number.)
  - **Loading:** N/A — server-rendered into the full page; no HTMX swap for the hero.
  - **Error:** N/A — a `.count()` of a queryset cannot 500 here; an empty DB is the `0` case above.
  - **Success feedback:** N/A — this is a read-only display, no mutation, no toast.
  - No dead ends — the hero already has its CTAs (`#hero-cta`) untouched.
- **Headline-vs-summary divergence is intentional and legible to a user — not a bug.** After this lands, the hero headline counts *sessions* ("**N** Upcoming Sessions") while the results summary directly below still reads "Showing 1–25 of **M** classes" (a card-count) — and those two numbers will legitimately differ (e.g. "200 Upcoming Sessions" up top, "42 classes" in the summary). This reads correctly to a user, by design, because the two answer different questions in different visual registers: the hero is the big-number **headline of total live inventory** ("how much is on offer right now"), and the summary is the **pager's running tally of the filterable card list** the person is scrolling. They are never labelled as the same metric and never sit in the same control, so there is no implied contradiction. The summary must stay a card-count (it is the pagination denominator — see Locked decisions and §10); the point recorded *here in §6* is that keeping it so does **not** create user-perception confusion — it preserves a clear, intuitive division of labour between "headline inventory" and "the list you're browsing." A reviewer should read the mismatch as intended, not as a number that's out of sync.
- **Dark + light:** **no new color, token, or CSS rule** — the value and label render inside the existing `.hs-n` / `.hs-l` classes, which are already theme-correct in both Obsidian (dark) and Slate (light). Risk is essentially nil because nothing about the styling changes; only text content does. **Still verify both themes** at build to confirm the (possibly longer) "Upcoming Sessions" label doesn't wrap awkwardly in the stat tile on either theme.
- **Mobile — pinned stylesheet + wrap mitigation.** The hero stat styling lives in **`static/css/cms-public.css`**: `.cp-page #hero-stats` is a centered flex row (`display:flex; gap:48px; justify-content:center`, line ~406), `.cp-page .hs-n` is the 32px/900 number (line ~407), and `.cp-page .hs-l` is the **uppercase 9px label** with `letter-spacing:.14em` (line ~408). The mobile override is in the same file at line ~536 (`#hero-stats{gap:24px}`) and ~537 (`.hs-n{font-size:22px}`) — it shrinks the gap and number but **does not touch the label or switch the row to wrap/stack.** This is the real wrap risk: lengthening the label from one word ("CLASSES") to two ("UPCOMING SESSIONS") inside a centered flex row of three tiles, with letter-spacing widening each word, at ~360px. Mitigation, all scoped to the existing mobile `@media` block in `cms-public.css` (no markup change, no new file, no `pl-` class needed since these are existing element-scoped rules): add `white-space:nowrap` on `.cp-page .hs-l` so each tile's label sits on one line and the *tiles* wrap as whole units rather than the words breaking mid-label, and/or allow the flex row to wrap (`#hero-stats{flex-wrap:wrap; row-gap:16px}`) so a too-wide third tile drops to a second row cleanly. Pick the lighter of the two after eyeballing it at 360px in the running container; both are theme-agnostic (no color/token involved). **Verify at 360px in both themes.** No horizontal scroll introduced.

---

## 7. Notifications / emails / activity

N/A — read-only display change. No triggers, no `SiteActivity`/`CmsActivity` kinds, no emails.

---

## 8. Build order (phased; each phase ships green)

1. **Model + logic.** Add `ClassSessionQuerySet` with `upcoming_public()` / `upcoming_public_count()`; attach as `ClassSession.objects`. Add the model spec (§9 model cases). Run the `classes` suite + `ruff` + `mypy` green in the `plfog-web` image. (No migration — confirm `makemigrations --check` reports nothing.)
2. **View wiring.** Swap `total_classes` → `upcoming_session_count` in `public_list()`. Update/extend `describe_public_list` count assertions. Suite green.
3. **Template.** Change `list.html:11` value + label with `|pluralize`. Coordinate with the sibling spec (§0) — rebase, don't overwrite the hero block. Verify both themes + mobile in the running container (`book.pastlives.test:8000`). Suite green.
4. **Housekeeping.** Bump `plfog/version.py` `VERSION` (patch, per `release-0.19.x`) + a member-friendly `CHANGELOG` entry. Final full suite + lint + mypy green.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*` (no `context_*` — it isn't collected), factory-boy, run in the `plfog-web` Docker image (local python lacks deps; `--no-cov` for subsets). The local SQLite run under-reports the coverage gate vs. CI Postgres — don't chase a borderline local number for additive, covered code; CI Postgres is the source of truth.

**Model — `classes/spec/models/class_session_spec.py`** (create if absent), `describe_ClassSessionQuerySet` → `describe_upcoming_public_count`:

- `it_counts_a_single_future_session` — one published class with one future session → `1`.
- `it_counts_each_session_of_a_series` — one published class with a 3-session series, all future → **`3`** (proves it counts sessions, not cards).
- `it_excludes_past_sessions` — a future session + a past session on the same offering → counts only the future one.
- `it_excludes_draft_pending_and_archived_offerings` — future sessions under `draft` / `pending` / `archived` offerings → not counted (covers each non-published status; assert published-only).
- `it_excludes_private_offerings` — future session under a published-but-`is_private=True` offering → not counted.
- `it_excludes_flexible_classes` — a flexible (session-less) published offering → contributes `0`.
- `it_counts_a_session_starting_exactly_now` — `starts_at == timezone.now()` is included (the `>=` boundary). Freeze/inject a fixed `now` (e.g. `freezegun` if already a dep, else build the session at a captured `now` and assert inclusion) to make the edge deterministic — note the tz window so the test isn't flaky on the second-boundary.
- `it_returns_zero_for_an_empty_catalog` — no offerings → `0` (the empty-state contract behind §6).

**View — `classes/spec/views/public_spec.py`**, extend `describe_public_list` (it has visibility/filter tests but **none currently assert the count value**):

- `it_renders_the_upcoming_session_count_in_the_hero` — GET the catalog with a known set of future sessions; assert the rendered hero shows the right number *and* the relabeled text (assert "Upcoming Session" appears in the HTML and the old "Classes" stat label is gone from that tile). Guards against the label-lying regression.
- `it_counts_sessions_not_cards` — seed a single 3-date series (which collapses to **one** card) and assert the hero count is **3** while the results summary still says "1 … class" (proves the two numbers are decoupled — the headline counts sessions, the pager counts cards).
- `it_shows_zero_gracefully_when_no_upcoming_sessions` — only past/flexible content → hero reads "0 Upcoming Sessions," not blank (assert the literal "0" renders inside `.hs-n`).

Reuse existing `published_class` / draft / private fixtures already in `public_spec.py`. Aim for 100% branch coverage of the new method (every filter branch is exercised by the exclusion cases above).

## 10. Open / deferred

- **Category-filter dropdown badge counts** (`cat.class_count`, `list.html:45`; computed `views.py:197-204`): left as **per-card** (distinct grouping keys). Switching these to session counts is plausible but would make the dropdown badges and the card list disagree, which is more confusing than helpful. Minor open decision — flag to the user; recommend leaving unless asked.
- **Should the hero count react to active browse filters?** Currently specified as **whole-catalog (unfiltered)**, consistent with the other hero stats. If the user wants "show me how many sessions match my current filter," that's a small change (apply the browse filters to the session queryset). Deferred pending user preference.
- **Results-summary count** ("Showing X–Y of N classes," `_list_results.html:6`) stays **card-count** — explicitly out of scope; changing it would break the pager. Recorded here so a later reader doesn't "fix" the apparent inconsistency.
- **Wider sweep for other class-count surfaces:** the reuse scout found the count *only* on the public catalog hero. If a class/session count appears elsewhere later (a homepage tile, an admin dashboard, a guild page), the new `ClassSessionQuerySet.upcoming_public_count()` is the reusable primitive to point them at — but auditing those surfaces is out of scope for this change. Note if a wider sweep is wanted as a follow-up.
- **Label wording** ("Upcoming Sessions" vs "Available Dates"): recommended "Upcoming Sessions"; confirm with the user at build (copy-only, no logic impact).

---

**Closing note (build time):** bump `plfog/version.py` `VERSION` and add a **member-friendly** `CHANGELOG` entry — on `release-0.19.x` each feature bumps the **patch**; Discord aggregates all 0.19.x changes at merge, so **verify the next patch number at build, don't assign it now.** Suggested changelog tone (plain language, no jargon): *"The Classes & Workshops catalog now shows how many upcoming sessions you can book at a glance, instead of just how many class types we offer."*
