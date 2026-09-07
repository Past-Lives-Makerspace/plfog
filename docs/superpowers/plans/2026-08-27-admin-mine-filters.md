# "My Classes" Filter on the Admin Classes & Registrations Lists — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-27
**Surface:** Classes admin — `/classes/admin/classes/` (`templates/classes/admin/classes_list.html`) and `/classes/admin/registrations/` (`templates/classes/admin/registrations.html`)
**Related:** none

---

## 1. Summary

An admin who also teaches (or authors) classes has no quick way to see *their own* slice of the two big admin lists. On the Classes tab there is no mine-style affordance at all — just the Instructor dropdown, which requires knowing to pick yourself. On the Registrations tab a "Mine Only" link exists but is invisible unless the acting admin happens to have an `instructor_slug` set, and it only matches classes where you are the listed *instructor*, missing classes you authored but someone else teaches. This adds an always-visible **My Classes** toggle to both lists: one click filters to classes where you are the instructor **or** the author, composing cleanly with every existing filter, search, sort, and the CSV export.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Semantics of "mine" | `Q(instructor=me) \| Q(created_by=me)` — classes I teach or authored. **Not** `editable_by` (admins can edit everything, so it filters nothing). Stated in a `.pl-help` tooltip on the control. |
| Visibility | Always visible to everyone who can reach these pages. Never gated on `instructor_slug` — that gate is exactly the bug that hid the old toggle. |
| Who is "me" | The real logged-in user's member (`request.user.member`), even under view-as preview — matches the old Mine Only behavior. |
| Composition | ANDs with everything already there: status pills, search, Instructor dropdown, Class/Status selects, sort, pagination, CSV export. |
| Registrations back-compat | The old `instructor=<own pk>` trick is **not migrated** — `instructor=` keeps its unchanged meaning (plain instructor filter), so old bookmarks still work. The new toggle uses its own `mine=1` param. |
| Empty state | Friendly message plus a one-click link that clears only the mine filter. Plain language, no dashes. |

## 2. What already exists (reuse, don't reinvent)

| Need | Existing thing | Location |
|---|---|---|
| Classes list view (status pills w/ counts, Instructor select, search, sort) | `admin_classes` | `classes/views.py:2046` |
| Registrations list view | `admin_registrations` | `classes/views.py:2828` |
| Shared GET-filter helper (status/class/instructor) that the **CSV export reuses** | `_filter_registrations` | `classes/views.py:1080` |
| Role scoping (admins see all; instructors/guild leads see `editable_by`) | `_scoped_registrations` | `classes/views.py:1065` |
| Search/sort/pagination + `base_params` that **carries any extra GET param** (so `mine=1` survives sort headers, pagination, and the export link with zero new plumbing) | `prepare_table` | `classes/table.py:12` |
| Sort headers / pagination that consume `base_params` | `{% sort_header %}` / `table_pagination.html` | `classes/templatetags/`, `templates/components/` |
| Pill styling with a muted count | Status pills | `templates/classes/admin/classes_list.html:9-14` |
| Toggle-link pill pattern (`✓` + `hub-btn--primary` when active) | Old "Mine Only" link | `templates/classes/admin/registrations.html:30-36` |
| Help tooltip | `.pl-help` bubble (CSS-only) | `static/css/hub.css`, FRONTEND.md §Help tooltip |
| The two FKs behind "mine" | `ClassOffering.instructor` (`:404`, nullable), `ClassOffering.created_by` (`:492`, nullable, `SET_NULL`) | `classes/models.py` |
| Queryset home for the new predicate | `ClassOfferingQuerySet` (`for_instructor`, `editable_by`) | `classes/models.py:205-221` |
| Filter rows that already flex-wrap on mobile | `.admin-toolbar` / `.admin-filters` | `static/css/hub.css:3116-3134` |

Gaps to close (all small):

1. No `hosted_by` predicate on `ClassOfferingQuerySet`.
2. `admin_classes` has no `mine` handling, and its status-pill hrefs are hardcoded `?status=X` / `.` — they currently drop `q` and `instructor` too, so a mine flag would not survive a status click. The pill URLs must be view-computed and param-preserving.
3. **The shared search component drops every sibling param.** `components/table_search.html` is its own GET form submitting only `q`, and its clear link is a bare `href="?"` that nukes everything. Sequence today-plus-mine: click My Classes → type a search → mine (and status, and instructor) silently vanish. The component needs an opt-in way to carry sibling params. It is shared by five templates (`classes_list.html`, `categories.html`, `discount_codes.html`, `registration_questions.html`, `hub/help_search.html`), so the mechanism must be backward compatible — callers that pass nothing behave exactly as today.
4. `_filter_registrations` has no `mine` handling.
5. The registrations template's Mine Only block is gated and narrow; it gets replaced.

## 3. Where the code lives

```
classes/models.py                                  # ClassOfferingQuerySet.hosted_by()
classes/views.py                                   # admin_classes, _filter_registrations, admin_registrations
templates/components/table_search.html             # opt-in preserved-params + clear-URL support (backward compatible)
templates/classes/admin/classes_list.html          # My Classes pill, param-preserving status pills + search, empty state
templates/classes/admin/registrations.html         # My Classes toggle (replaces Mine Only), empty state
static/css/hub.css                                 # .pl-filter-divider (one tiny rule + mobile hide)
classes/spec/views/admin_classes_spec.py           # extend
classes/spec/views/admin_registrations_filters_spec.py  # extend
classes/spec/views/registration_export_spec.py     # extend (export respects mine)
```

No new files, no migrations.

## 4. Data model

None. No schema changes; both FKs exist.

## 5. Business logic (fat models)

One queryset method on `ClassOfferingQuerySet` (next to `for_instructor` / `editable_by`):

```python
def hosted_by(self, member: "Member") -> "ClassOfferingQuerySet":
    """Classes this member teaches or authored (instructor OR created_by)."""
    return self.filter(Q(instructor=member) | Q(created_by=member))
```

- No `.distinct()` — both are direct single-valued FK comparisons on the row; no join can multiply rows.
- `member` must be a real Member. **Callers guard `None`** (passing `None` would match every class with a NULL instructor/author — the opposite of intended). Views do `qs.none()` when the user has no linked member.

### View changes

**`admin_classes`** (`classes/views.py:2046`):

- Parse `mine = request.GET.get("mine", "") == "1"` (any other value silently off, matching how the sibling filters ignore bogus values).
- `own_member = getattr(request.user, "member", None)`.
- After the existing status/instructor filtering: `if mine: qs = qs.hosted_by(own_member) if own_member else qs.none()`.
- Pill count: `mine_count = base.hosted_by(own_member).count() if own_member else 0` (on `base`, i.e. all statuses **and ignoring `q` and the Instructor dropdown** — the same global convention as the status-pill counts, which ignore the search box and each other; a filter-aware count would disagree with its sibling pills).
- Compute param-preserving URLs in the view (the same `QueryDict.copy()` / pop pattern the old `mine_only_params` used). All computed URLs start from a **normalized** copy of the GET params: a bogus `mine` value (anything other than `1`) is stripped, not echoed, so cruft never rides along on every subsequent link.
  - `mine_toggle_url` — normalized GET minus `page`, with `mine=1` added (or removed, when already on).
  - Status pill URLs — for each pill, normalized GET minus `page` and `status`, plus its own `status=<value>` (nothing extra for "All"). This is what makes mine (and, as a side benefit, `q` and `instructor` — dropped today) survive a status click. Pass the pills as `(url, label, count, is_selected)` tuples so the template stays dumb.
  - `search_preserved_fields` — a list of `(name, value)` pairs for the search form's hidden inputs: normalized GET minus `q` and `page` (so `status`, `instructor`, `mine`, `sort`, `dir` all survive a search).
  - `search_clear_url` — normalized GET minus `q` and `page` (the clear-search ✕ drops only the search, not the other filters).
- Context additions: `mine_active`, `mine_count`, `mine_toggle_url`, `search_preserved_fields`, `search_clear_url`; `status_filters` becomes the tuple list above.

**`_filter_registrations`** (`classes/views.py:1080`) — the mine filter lives here so the **CSV export inherits it for free**, exactly as the docstring promises for the other filters. The predicate is **defined once** — `hosted_by` is the single source of truth; this call site reuses it rather than hand-inlining a second copy of the `Q(...)`:

```python
if request.GET.get("mine", "") == "1":
    own_member = getattr(request.user, "member", None)
    qs = (
        qs.filter(class_offering__in=ClassOffering.objects.hosted_by(own_member))
        if own_member
        else qs.none()
    )
```

Same `None`-guard as the classes view: a memberless user under `mine=1` gets `qs.none()`, never a `hosted_by(None)` call (which would match NULL-instructor/NULL-author classes and leak their registrations — including through the export).

**`admin_registrations`** (`classes/views.py:2828`):

- Delete the `mine_only_pk` / `mine_only_params` machinery (and its instructor_slug gate).
- Add `mine_active` plus a precomputed `mine_toggle_url` (current GET minus `page`, `mine` toggled).
- The Instructor dropdown keeps its current admins-only visibility and unchanged `instructor=` semantics.
- For non-admins the list is already scoped to `editable_by` (own classes + led guilds); mine **narrows further** to strictly teach-or-authored — genuinely useful for a guild lead who also teaches, so it renders for them too.

### Querystring contract

| Param | Values | Meaning |
|---|---|---|
| `mine` | `1` = on; absent/anything else = off | Filter to classes (or registrations of classes) where the real logged-in user's member is `instructor` or `created_by` |

- Composes by AND with `status`, `instructor`, `class`, `q`, `sort`, `dir` on both pages.
- Carried automatically by `prepare_table`'s `base_params` (any non-reserved, non-empty GET param is copied through), so sort headers, pagination links, and the Export CSV href all preserve it with no template surgery.
- Toggle links always drop `page` (a filter change resets to page 1 — same convention as the old Mine Only link).
- **Normalization:** a bogus `mine` value in the incoming URL is treated as off *and* stripped from every view-computed URL (pills, toggles, clear links, search hidden inputs) — `?mine=yes` degrades to a clean state instead of propagating forever.
- Registrations `instructor=<pk>` is untouched: still the plain instructor filter, so pre-change bookmarks behave identically.

## 6. UI / UX

### Screen 1: Classes list (`templates/classes/admin/classes_list.html`)

- **Layout:** the My Classes pill joins the existing `.admin-filters` status-pill row, after the last status pill, separated by a thin vertical divider so it reads as a different *kind* of filter (status pills are exclusive; this one is orthogonal). Divider is a new class in `hub.css`: `.pl-filter-divider { width:1px; height:1.25rem; background:var(--hub-border); }` — theme token, no inline style. Because `.admin-filters` flex-wraps, the divider can end up orphaned at a line start/end on narrow screens; hide it under the page's existing mobile breakpoint (`display:none` in the media query) — the wrap itself provides the visual separation there. The `{{ pending_count }} pending review` badge stays at the end of the row.
- **The control, markup pattern (matches the status pills exactly):**

  ```html
  <span class="pl-filter-divider"></span>
  <a class="hub-btn hub-btn--sm {% if mine_active %}hub-btn--primary{% else %}hub-btn--ghost{% endif %}"
     href="?{{ mine_toggle_url }}">
      My Classes <span style="opacity:0.6; font-weight:400;">{{ mine_count }}</span>
  </a>
  <span class="pl-help">
    <span class="pl-help__icon" tabindex="0" role="img"
          aria-label="My Classes: shows only classes where you are the instructor or the class author.">?</span>
    <span class="pl-help__bubble">Shows only classes where you are the instructor or the class author.</span>
  </span>
  ```

  (The count `<span>` copies the status pills' existing inline opacity style verbatim — consistency beats introducing a class for one attribute here.)
- **Count on the pill: yes.** Every other pill in this row carries a count; a bare pill would read as broken. It costs one indexed `COUNT` on `base`, alongside the seven the row already runs. It is **global** (all statuses, ignoring the Instructor dropdown), matching how the status counts ignore each other.
- **Status pills** switch from hardcoded `?status=X` hrefs to the view-computed URLs (§5), so clicking a status keeps mine (and search/instructor) applied. Their rendered look is unchanged.
- **Search form** — the shared `components/table_search.html` gets two **optional, backward-compatible** parameters; callers that pass neither render byte-for-byte what they do today (so `categories.html`, `discount_codes.html`, `registration_questions.html`, and `hub/help_search.html` are untouched):
  - `preserved_fields` — a list of `(name, value)` pairs rendered as hidden inputs inside the form, guarded so nothing renders when absent:

    ```html
    {% if preserved_fields %}{% for name, value in preserved_fields %}
        <input type="hidden" name="{{ name }}" value="{{ value }}">
    {% endfor %}{% endif %}
    ```

  - `clear_url` — the clear-✕ link becomes `href="?{{ clear_url }}"` when provided, falling back to today's bare `href="?"` via `{% if clear_url %}…{% else %}?{% endif %}` (or `|default:` on the querystring).

  `classes_list.html` passes `preserved_fields=search_preserved_fields clear_url=search_clear_url` (§5). Net effect: searching keeps mine, status, instructor, and sort; clearing the search drops only `q`. This closes the worst leak — today the search form silently discards even the existing status and instructor filters.
- **Instructor dropdown form** (`classes_list.html:23-32`) gains hidden inputs so submitting it stops discarding sibling state: `mine` (when active), `q` (when set), and `sort`/`dir` (when present) — the same four the registrations toolbar already carries; it already carries `status`. Its "✕ Clear" link becomes a view-computed URL preserving everything except `instructor` and `page`. Mine + Instructor=someone-else is a legal (if odd) intersection; it just returns the overlap — no special casing.
- **States:**
  - *Active:* pill renders `hub-btn--primary` (same as a selected status pill). No other on-state indicator needed.
  - *Empty:* the existing empty `<td colspan="6">` row gets a `mine_active` branch, checked **first** (it is the most specific explanation):
    > No classes match. You are not listed as instructor or author on any classes that match the current filters. <a href="?{{ mine_clear_url }}">Show all classes</a>
    where `mine_clear_url` is current GET minus `mine` and `page` (one click clears *only* the mine filter; search/status stay). Link inherits the table's link styling; no dashes anywhere in the copy.
  - *Loading/error:* full-page GET navigation, same as every other filter on this page — nothing new.
- **Dark + light:** no new colors — `hub-btn` variants, `.pl-help`, and `--hub-border` are all existing themed pieces. Verify both themes on the rendered row (pill active/inactive, divider visibility, bubble). The Instructor `<select>`'s pre-existing inline `background`/`color` (a Rule 13 violation) is not touched here — noted in §10.
- **Mobile:** `.admin-filters` already `flex-wrap`s; the pill, divider, and `?` icon wrap as units in the same flow. The pill is a real `hub-btn--sm` tap target; the `.pl-help` bubble auto-pins to viewport gutters on phones per the component. No new fixed widths.

### Screen 2: Registrations list (`templates/classes/admin/registrations.html`)

- **Layout:** the new toggle **replaces the old Mine Only block** (`registrations.html:30-36`) in the same spot — inside the GET toolbar form, after the Apply button, before Export CSV. The toolbar already wraps (`flex-wrap` inline on the form).
- **The control, markup pattern (upgraded old Mine Only, same pill style):**

  ```html
  {% if mine_active %}
      <input type="hidden" name="mine" value="1">
      <a href="?{{ mine_toggle_url }}" class="hub-btn hub-btn--sm hub-btn--primary">My Classes ✓</a>
  {% else %}
      <a href="?{{ mine_toggle_url }}" class="hub-btn hub-btn--sm">My Classes</a>
  {% endif %}
  <span class="pl-help">
    <span class="pl-help__icon" tabindex="0" role="img"
          aria-label="My Classes: shows only registrations for classes where you are the instructor or the class author.">?</span>
    <span class="pl-help__bubble">Shows only registrations for classes where you are the instructor or the class author.</span>
  </span>
  ```

  - The toggle is a **link** (instant, no Apply needed — same as before). The hidden `mine` input is what keeps the filter alive when the user changes a `<select>` and hits **Apply** (the form already preserves `sort`/`dir` the same way).
  - Renamed from "Mine Only" to **My Classes** so both pages use one name for one concept.
  - Always rendered — no `instructor_slug` gate, no admin gate. Non-admins (already scoped) can still narrow to strictly their own.
- **Count on the pill: no.** This toolbar shows counts nowhere (selects and buttons only); a lone counted pill would be inconsistent, and it would add a `COUNT` over the largest table in the CMS on every load for little value. The result count is immediately visible in the table itself.
- **Export CSV** link already uses `base_params`, and the filter lives in `_filter_registrations` — so the export honors mine with zero changes. Say so in the PR; test it (§9).
- **States:**
  - *Active:* `hub-btn--primary` + `✓`, exactly the old visual language.
  - *Empty:* the empty `<td colspan="7">` row gets a `mine_active` branch, checked before the `q` branch:
    > No registrations match. None of these belong to a class where you are the instructor or author. <a href="?{{ mine_clear_url }}">Show all registrations</a>
    (`mine_clear_url` = current GET minus `mine`/`page`; other filters survive. For a non-admin, "all" means their scoped view — the copy stays honest since it just says "show all registrations".)
  - *Loading/error:* full-page GET, unchanged.
- **Dark + light:** all existing themed components; verify both themes (active pill, bubble). The toolbar's pre-existing inline-styled search input is untouched (§10).
- **Mobile:** the toolbar form already wraps; pill + `?` icon flow with it. Real tap target, no new widths.

### User-lens pass (both screens)

- Primary action obvious: one clearly labeled pill per page, in the filter row where filters live.
- Whole task completable: click pill → see mine → sort/paginate/search/export without losing it → click again (or the empty-state link) to clear. No dead ends.
- Nothing half-built: the toggle turns on, off, composes, exports, and explains itself (`?` bubble covers the one non-obvious thing: instructor OR author).
- Labels a non-technical admin understands: "My Classes", not "hosted_by" or "created_by".

## 7. Notifications / emails / activity

None — a read-only filter sends nothing.

## 8. Build order (phased; each phase ships green)

1. **Queryset + views:** `hosted_by` on `ClassOfferingQuerySet`; `mine` handling + normalized URL precomputation (incl. `search_preserved_fields`/`search_clear_url`) in `admin_classes`; `mine` via `hosted_by` in `_filter_registrations`; context rewiring (and old Mine Only context removal) in `admin_registrations`. Specs for all of it (§9). Suite + `ruff` + `mypy` + `manage.py check` green.
2. **Templates + CSS:** the opt-in `preserved_fields`/`clear_url` extension to `components/table_search.html` (other callers byte-identical), both admin templates per §6, `.pl-filter-divider` in `hub.css` (with the mobile hide), empty-state branches, hidden inputs, view-computed status-pill hrefs. Template/response-content specs incl. the component regression. Verify both themes and mobile wrap on the rendered pages. Suite green.

> Spec only — do not build until approved. No VERSION bump or changelog work in this plan (handled at release time, outside this spec).

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*`, factory-boy, extending the existing files.

**`classes/spec/views/admin_classes_spec.py` — `describe_mine_filter`:**

- `it_filters_to_classes_taught_by_me` — instructor=me kept, others dropped.
- `it_includes_classes_i_authored_but_do_not_teach` — created_by=me, instructor=someone else → kept.
- `it_excludes_classes_where_i_am_neither` — including a class with NULL instructor and NULL author (the `None`-guard regression: a memberless "mine" must never match NULL FKs).
- `it_composes_with_status_and_search` — mine + status + q AND together.
- `it_shows_the_pill_without_an_instructor_slug` — admin with no `instructor_slug` still gets the control (the original bug).
- `it_returns_empty_for_a_user_with_no_member` — `qs.none()`, count 0, page renders.
- `it_ignores_bogus_mine_values` — `mine=yes` → filter off.
- `it_counts_my_classes_across_all_statuses` — `mine_count` ignores the active status pill.
- `it_counts_my_classes_ignoring_the_search_box` — `mine_count` unaffected by `q`.
- `it_preserves_mine_in_status_pill_urls` — pill URLs contain `mine=1` (and keep `q`).
- `it_preserves_mine_in_base_params` — sort/pagination links carry it.
- `it_preserves_mine_when_searching` — the search form's markup contains hidden inputs for `mine` (and `status`/`instructor` when set), and its clear-✕ href keeps those params while dropping `q`.
- `it_strips_bogus_mine_from_computed_urls` — `?mine=yes` in → no `mine` in any pill/toggle/clear URL out.
- `it_renders_the_mine_empty_state_with_a_clear_link` — message text + link that drops only `mine`.
- `it_uses_the_real_user_under_view_as_preview` — mine follows `request.user.member`, not the previewed role.

**Component regression (same file or a small template spec):**

- `it_leaves_other_table_search_callers_unchanged` — a page passing no `preserved_fields`/`clear_url` (e.g. the categories admin) renders no hidden inputs and keeps the bare `href="?"` clear link.

**`classes/spec/views/admin_registrations_filters_spec.py` — `describe_mine_filter`:**

- `it_filters_to_registrations_for_classes_i_teach_or_authored`.
- `it_composes_with_status_and_class_filters`.
- `it_keeps_plain_instructor_param_working` — `instructor=<pk>` unchanged (back-compat).
- `it_shows_the_toggle_without_an_instructor_slug` / `it_shows_the_toggle_to_a_non_admin_instructor`.
- `it_narrows_a_guild_leads_scoped_view` — lead of a guild with others' classes + one own class → mine shows only the own class's registrations.
- `it_returns_empty_for_a_user_with_no_member` — memberless admin + `mine=1` → zero rows, even when NULL-instructor/NULL-author classes have registrations (the `None`-guard leak regression).
- `it_ignores_bogus_mine_values` — `mine=yes` → filter off.
- `it_renders_a_hidden_mine_input_when_active` — Apply keeps the filter.
- `it_renders_the_mine_empty_state_with_a_clear_link`.

**`classes/spec/views/registration_export_spec.py`:**

- `it_respects_the_mine_filter` — CSV rows limited the same way the list is.
- `it_exports_nothing_for_a_memberless_user_with_mine` — the `None`-guard asserted through the export path too: a dropped guard here would leak every registration of NULL-instructor/NULL-author classes into a downloadable file.

Also run `tests/template_comment_lint_spec.py` after the template edits (house rule).

## 10. Open / deferred

- **Grouped-class edge:** the classes list shows one representative row (lowest pk) per `grouping_key` group. If a multi-date group somehow mixed instructors and the representative row is not mine, my date stays hidden under mine. Grouping is same-title-same-category, so mixed instructors within a group are not an expected state; accepted, not handled.
- **Pre-existing Rule 13 violations** (inline `background`/`color` on the Instructor `<select>` and the registrations search input) — out of scope; note for a styling sweep.
- **Teach portal:** instructors' own portal already scopes to their classes; no mine toggle needed there.
- **Persisting the toggle as a preference** (sticky mine across visits) — YAGNI until someone asks; the querystring is bookmarkable.
