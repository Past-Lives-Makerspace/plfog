# CMS "Categories → Guilds" Relabel + Glass→Lamp Demo Fix — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-06-24
**Surface:** book CMS `book.pastlives.test` — public catalog (`/classes/`) + admin CMS (Settings → Categories tab, Classes list/detail) + teach CMS (instructor class list/overview). Also the Django admin label and demo seed data.
**Related:**
- `docs/superpowers/plans/2026-06-24-catalog-upcoming-session-count.md` — **overlapping file** (`templates/classes/public/list.html` hero block). See §10 coordination note.

---

## 1. Summary

This is a **copy-only relabel**: everywhere the CMS shows the word "Category"/"Categories" to a user, it will read "Guild"/"Guilds" instead, because the org thinks of class groupings as *guilds* and the generic word "Categories" clashes with their lexicon. Nothing functional changes — the same dropdowns, columns, buttons, and pages work exactly as before, just with new wording. Separately, the demo seed data's one "Glassblowing" example becomes "Lamp Working" (the studio does lamp working, not glass blowing); this only affects throwaway demo content. No database, model, URL, or behavior changes — purely the words on screen.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Rename the model / DB / identifiers? | **No.** Keep the `Category` model, every `category`/`categories` identifier, related_names, URL names, view/form names. Display copy only. (User-locked.) |
| What changes? | Only **user-facing display strings** ("Category"/"Categories" → "Guild"/"Guilds") plus two Python display strings (`Meta` label, the FK `help_text`). |
| Django admin label | Add `verbose_name="Guild"` (+ keep/adjust `verbose_name_plural`) so the admin model list reads "Guilds" too, since admins are users. |
| "Glassblowing" → "Lamp Working" | Factual fix; occurs only in `demo_data.py`. **Change the slug too** (`[prefix]glassblowing` → `[prefix]lamp-working`) — demo data is disposable and re-seeded, no migration risk. |
| i18n | None. No `locale/`, no `{% trans %}` in this project. These are plain hardcoded-English string edits. |

---

## 2. What already exists (the precise change-list)

All locations below were **re-verified against the working tree on 2026-06-24** (the reuse-scout line numbers were stale in places — the corrected lines are below). Every change is a string swap; no logic moves.

### 2a. User-facing "Category" copy to CHANGE

| Location (verified) | Old string | New string |
|---|---|---|
| `templates/classes/admin/settings_base.html:6` | nav subtab `>Categories<` | `>Guilds<` |
| `templates/classes/admin/settings_hub.html:6` | hub card heading `Categories` | `Guilds` |
| `templates/classes/admin/settings_hub.html:7` | subtitle `Guild-linked groupings for classes.` | `Groupings that organize the class catalog.` (final — must **not** become "Guild groupings," which reads circularly; this phrasing also avoids the membership-Guild collision in §10 RISK) |
| `templates/classes/admin/categories.html:5` | search placeholder `Search categories…` | `Search guilds…` |
| `templates/classes/admin/categories.html:6` | primary button `+ New Category` | `+ New Guild` |
| `templates/classes/admin/categories.html:32` | confirm title `Delete this category?` **and** button `Delete Category` (x2) | `Delete this guild?` / `Delete Guild` |
| `templates/classes/admin/categories.html:32` | confirm message `Any classes still in this category will block the delete.` | `Any classes still in this guild will block the delete.` |
| `templates/classes/admin/categories.html:39` | empty state `No categories matching "{{ q }}".` / `No categories yet.` | `No guilds matching "{{ q }}".` / `No guilds yet.` |
| `templates/classes/admin/category_form.html:3` | page heading `New Category` (conditional `{% if mode == 'create' %}New Category{% else %}Edit: …`) | `New Guild` |
| `templates/classes/admin/classes_list.html:41` | `{% sort_header "Category" "category__name" … %}` column label | `"Guild"` (keep sort key `category__name`) |
| `templates/classes/admin/class_detail.html:12` | detail row label `Category` | `Guild` |
| `templates/classes/teach/classes_list.html:12` | table column header `Category` | `Guild` |
| `templates/classes/teach/class_overview.html:12` | detail row label `Category` | `Guild` |
| `templates/classes/public/list.html:12` | hero stat label `Categories` (`<div class="hs-l">Categories</div>`) | `Guilds` |
| `templates/classes/public/list.html:41` | filter field label `<span class="cp-filter__field-label">Category</span>` | `Guild` |
| `templates/classes/public/list.html:43` | default option `<option value="">All categories</option>` | `All guilds` |
| `templates/classes/admin/class_review.html:15` | `· {{ offering.category.name }}` — **data only, no "Category" label** | **No change** (audited; renders the row's own name) |
| `templates/classes/admin/class_review.html:90` | approval row `Guild Lead ({{ offering.category.guild.name }})` — **already says "Guild," names the *membership* `category.guild`** | **No change** (audited; see §6 + §10 RISK — this is the live cross-concept collision) |

> Reuse-map corrections (for the builder): on `public/list.html` the filter **label** is `:41` and the **default option** is `:43` (the reuse map collapsed both onto `:41`). On `categories.html` the delete-modal copy is `:32`, but there is **also** the empty-state copy at `:39` the reuse map missed — include it or the page will be half-relabeled. The `class_review.html` rows above carry **no literal "Category" label** to swap — they're audited and left as-is, but called out because §6's "every user-visible Category reads Guild" claim must be verified, not assumed, for this page.

### 2b. Python display strings to CHANGE

| Location (verified) | Old | New |
|---|---|---|
| `classes/models.py:103` | `verbose_name_plural = "Categories"` (no `verbose_name` present) | add `verbose_name = "Guild"` **and** `verbose_name_plural = "Guilds"` |
| `classes/models.py:212` | FK `help_text="Category grouping."` | `help_text="Guild grouping."` |

Additional admin-facing **help_text** that says "category" (not in the reuse map; surfaces in the Django admin / model forms — relabel for coherence, all display-only, no identifiers):

| Location | Old phrase | New phrase |
|---|---|---|
| `classes/models.py:71-73` (`icon_svg` help_text) | "shown next to the category name … Defaults to a Lucide icon seeded for known categories." | "shown next to the guild name … seeded for known guilds." |
| `classes/models.py:81` (`Category.guild` FK help_text) | "Optional link to the makerspace Guild that owns this category. Used for Mailchimp tagging." | **Final wording:** `Optional link to the membership Guild that owns this grouping. Used for Mailchimp tagging.` |

> **Vocabulary decision (resolved):** use exactly **two terms** everywhere — plain **"Guild"** for the class grouping (the relabel) and **"membership Guild"** (the qualifier, not a new noun) wherever the `Category.guild` FK / membership construct must be distinguished. We do **not** introduce "craft guild" — that would ship a third vocabulary, the half-translated inconsistency §6 warns against, and §10 already defers a full "Craft Guilds everywhere" rename. The disambiguator is the adjective "membership," nothing more.

> `name` field help_text at `models.py:55` ("Display name (e.g. Woodworking).") needs no change — it never says "category."

### 2c. Glass → Lamp Working (demo seed data only)

| Location (verified) | Old | New |
|---|---|---|
| `core/management/commands/demo_data.py:189` | slug `f"{DEMO_SLUG_PREFIX}glassblowing"` | `f"{DEMO_SLUG_PREFIX}lamp-working"` |
| `core/management/commands/demo_data.py:190` | name `"[DEMO] Glassblowing"` | `"[DEMO] Lamp Working"` |
| `core/management/commands/demo_data.py:299` | title `"[DEMO] Glassblowing Fundamentals (Past)"` | `"[DEMO] Lamp Working Fundamentals (Past)"` |

### DO-NOT-TOUCH — identifiers, not copy (explicit)

These contain the substring "categor" but are **code identifiers / routes / data keys**, not user-facing copy. Renaming any of them is out of scope and would break the app:

- Model name **`Category`** — `classes/models.py:54`.
- Related names **`related_name="categories"`** (`classes/models.py:80`, the `membership.Guild` → Category reverse) and **`related_name="classes"`** (`classes/models.py:212`).
- Storage path **`upload_to="classes/categories/"`** — `classes/models.py:59`.
- URL names **`admin_categories`, `admin_category_create`, `admin_category_edit`, `admin_category_delete`** — `classes/urls.py:99-102`; and the public **`public_category`** route `classes/urls.py:11`.
- View functions **`admin_categories`, `admin_category_create`, `admin_category_edit`, `admin_category_delete`** — `classes/views.py:2255,2270,2284,2299`; and **`public_category`**.
- Form class **`CategoryForm`** — `classes/forms.py:377`.
- Context keys `categories`, `total_categories`, `selected_category_slug`, and the `category` GET param / `name="category"` `<select>` — `classes/views.py:202,247,262` and `templates/classes/public/list.html:42`. The query-string param `?category=` and option `value="{{ cat.slug }}"` are **API surface**; leave them.
- The `CategoryFactory` and all spec usages of `name="Glass"`/`slug="glass"` (`classes/factories.py`, `classes/spec/...`) — those are the **guild logo prefix** "glass" (`membership/logos.py:17`), unrelated to the demo Glassblowing copy. Out of scope.

---

## 3. Where the code lives

No new files. Edits only, all inside existing coverage/mypy scope:

```
templates/classes/
  admin/settings_base.html      # nav subtab label
  admin/settings_hub.html       # hub card heading + subtitle
  admin/categories.html         # search, button, delete modal, empty state
  admin/category_form.html      # page heading
  admin/classes_list.html       # column header
  admin/class_detail.html       # detail row label
  teach/classes_list.html       # column header
  teach/class_overview.html     # detail row label
  admin/class_review.html       # AUDITED — no edit (data-only :15, membership "Guild Lead" :90); verify post-swap
  public/list.html              # hero stat label + filter label + default option  ← shared w/ sibling spec
classes/models.py               # Meta verbose_name(s) + FK/icon/guild help_text
core/management/commands/demo_data.py   # Glassblowing → Lamp Working (incl. slug)
plfog/version.py                # VERSION bump + CHANGELOG (at BUILD time)
```

## 4. Data model

**No schema change.** The only model edit is the `Meta` label and `help_text` strings on `classes/models.py::Category` / `ClassOffering.category`.

> Django **will** generate a no-op migration for `verbose_name`/`verbose_name_plural`/`help_text` changes (these are tracked in model state). Generate it with `makemigrations`, `ruff format` it, and commit it with the change (per the "ruff-format + git add new migrations together" rule). It alters no columns and its reverse is the autogenerated inverse — no hand-written `RunPython`.

## 5. Business logic (fat models)

None. No methods, managers, services, or views change. This is presentation copy only.

## 6. UI / UX — completeness pass (concrete, per surface)

The single risk for a relabel is a **half-translated surface** — one screen saying "Category" next to another saying "Guild." The bar here is: after the swap, **every user-visible "Category/Categories" in the CMS reads "Guild/Guilds," with correct singular/plural**, and no functional control changes. Walking each surface:

- **Admin Settings → Guilds tab** (`settings_base.html`, `settings_hub.html`, `categories.html`):
  - Nav subtab pill now reads **"Guilds"**; the Settings hub card heading reads **"Guilds"**; the subtitle is pinned to **"Groupings that organize the class catalog."** (per §2a — deliberately not "Guild groupings," which reads circularly, and not "Guild-linked," which collides with membership Guilds per §10).
  - Toolbar: search placeholder **"Search guilds…"**, primary button **"+ New Guild"** (singular — it creates one).
  - Table is unchanged (Name / Slug / Sort columns); Edit/Delete actions unchanged.
  - **Empty state:** must read **"No guilds yet."** / **"No guilds matching "{{ q }}"."** — this is the spot the reuse map missed; verify it after edit so the empty table doesn't say "categories."
  - **Delete (destructive):** keep the existing `confirm_modal.html` flow (button is already `hub-btn--sm hub-btn--danger` → `$dispatch('open-confirm', …)`). Relabel modal **title "Delete this guild?"**, **button "Delete Guild"**, and **message** ("…still in this guild will block the delete."). No structural change — confirm modal stays.
- **Guild create/edit form** (`category_form.html`): page heading **"New Guild"** (create) / **"Edit: {{ category.name }}"** (unchanged — shows the row's own name). The form itself (`form_field.html` fields, Save/Cancel) is untouched. Singular "New Guild," not plural.
- **Admin Classes list** (`classes_list.html:41`): the sortable column header label becomes **"Guild"**; the sort key stays `category__name` so sorting still works. Cell value `{{ c.category.name }}` is data, untouched.
- **Admin class detail** (`class_detail.html:12`) and **teach class overview** (`class_overview.html:12`): the detail-row label cell reads **"Guild"** (singular — one value).
- **Teach classes list** (`teach/classes_list.html:12`): column header **"Guild"** (singular header). Cell value untouched.
- **Public catalog** (`public/list.html`):
  - Hero stat **label** (`hs-l`) reads **"Guilds"** (plural — it's a count). The stat **number** (`{{ total_categories }}`) is unchanged. (The adjacent "Classes" stat at `:11` belongs to the sibling spec — see §10.)
  - Filter field **label** reads **"Guild"** (singular — labels one control). Its **default option** reads **"All guilds"** (plural — spans all). Each `<option>` is a guild name (data), untouched. The `name="category"` attribute and `?category=` param **stay** (API surface).
  - **No-results / loading / empty states** for the catalog live in `_list_results.html` (the HTMX target). **Verified:** its summary line reads `"…of N class…es"` (`:6`) and its no-results message reads `"No classes match your filters."` (`:9`) — **neither says "category." No change needed in `_list_results.html`.** (The HTMX swap behavior is unaffected regardless.)
- **Class review page** (`class_review.html`) — **audited, no literal "Category" label to swap, but it is the live cross-concept collision, so it is verified here, not assumed:**
  - `:15` renders the offering's grouping as **data only** — `· {{ offering.category.name }}` with no "Category:" label. Nothing to relabel; the value is whatever the grouping is named. **Leave as-is.**
  - `:90` already reads **"Guild Lead ({{ offering.category.guild.name }})"** — note this "Guild" is the **membership** `category.guild`, and "Guild Lead" is a membership role, **not** the class grouping. After this relabel, this page shows the grouping name (`:15`) on the same screen as a "Guild Lead" labeled by the membership Guild (`:90`). That is precisely the conflation §10 RISK describes. **We do not rename "Guild Lead"** (it correctly names the membership construct) — the mitigation is the §10 disambiguation, not an edit here. Builder: eyeball this page after the swap to confirm the two "Guild" meanings don't read as the same thing.
- **Django admin** (auto-registered): with `verbose_name="Guild"` / `verbose_name_plural="Guilds"`, the model changelist header and breadcrumbs read "Guilds." Admin help_text under the fields reads "guild" / "membership Guild," never "category" or "craft guild."

**Singular vs plural rule applied above:** singular for one item / one control ("+ New Guild", "Delete Guild", filter label "Guild", detail-row "Guild", column headers naming one value per row); plural for counts / collections / spans ("Guilds" tab + stat, "All guilds", "Search guilds…", "No guilds yet").

**Components:** no component changes — existing `confirm_modal.html`, `form_field.html`, `table_search.html`, `table_pagination.html`, `sort_header`, the `cp-filter__*` markup, and the hero block all stay; only their text content changes.

**States:** empty (relabeled, above), loading (HTMX filter swap unchanged), error (form validation in `CategoryForm` unchanged — error text references field labels, not the word "Category"), success (admin create/edit/delete redirects unchanged — no toast wiring touched).

**Dark + light:** **pure copy change, zero CSS** — no new classes, no inline styles, no form controls added. Risk of a theme bug is nil. Still, per house rule, **explicitly verify both Obsidian (dark) and Slate (light)** on the public catalog and the admin Guilds tab after the swap to confirm nothing renders oddly (it won't — same DOM, same tokens).

**Mobile:** unchanged. Same elements, same widths; "Guild"/"Guilds" are the same length-class as the words they replace, so no new wrapping or overflow. n/a beyond existing layout.

## 7. Notifications / emails / activity

None. No triggers, no `SiteActivity` kinds, no emails. (Note: Mailchimp tagging uses `category-{slug}` tags built from the slug, not the display name — relabeling display copy does not change any tag, and the demo slug change only affects disposable demo data, never a production tag.)

## 8. Build order (single phase; ships green)

1. **Copy + label swap (one phase).**
   - Edit the 16 template strings in §2a (admin, teach, public).
   - Edit `classes/models.py` `Meta` (`verbose_name`/`verbose_name_plural`) and the three help_text strings in §2b; run `makemigrations` (no-op label/help_text migration), `ruff format` it, commit it with the change.
   - Edit the three `demo_data.py` strings in §2c (name, title, **slug**).
   - Update the affected specs (§9).
   - Run `ruff format . && ruff check .`, then the suite in the `plfog-web` container.
   - **Last:** bump `plfog/version.py` VERSION (verify the next `release-0.19.x` patch at build time — see closing note) + add a member-friendly CHANGELOG entry.

> Spec only — do not build until approved.

## 9. Testing

Run in the `plfog-web` Docker image (`--no-cov` for a fast subset). BDD `*_spec.py`, `describe_*`/`it_*`, factory-boy.

**Existing specs grepped for literal rendered-string assertions — impact assessed:**

| Spec | Asserts | Impact |
|---|---|---|
| `classes/spec/views/admin_categories_spec.py` | only factory-supplied names ("Pottery", "New", etc.) and status codes — **no UI-label assertions** | **No change needed.** Survives the relabel as-is. (Filename keeps "categories" — it's a filename, not copy; renaming is optional, out of scope.) |
| `classes/spec/views/admin_nav_spec.py:21` | `b">Categories<" not in resp.content` on `admin_overview` (proving the *old top-level tab* is gone) | **No change needed.** The relabel makes that literal even more absent; the admin_overview page isn't touched by this work. |
| `classes/spec/views/public_spec.py` | factory category names + `?category=` filtering + logo paths — **no assertion on the "Categories"/"All categories" labels** | **No change needed.** |
| `tests/core/management/demo_data_spec.py` | demo classes by **slug prefix** (`DEMO_SLUG_PREFIX + "past-fundamentals"`, etc.) and the category only via `_ensure_category()` return — **never by the "Glassblowing" name or `glassblowing` slug** | **No change needed.** The glass→lamp rename (incl. slug) is test-safe. |

> Caveat for the builder: re-grep before editing — these files are under active edit. Search `classes/spec/` and `tests/` for `b"Categor`, `Categories`, `All categories`, `New Category`, `Delete this categ`, `Search categ`, and `Glassblowing` to catch any assertion added since this spec.

**New assertions to add** (small, high-value — they pin the relabel so a future edit can't silently regress it):

- `public_spec.py` (catalog): the rendered list page contains `b"Guilds"` (hero stat label) and `b"All guilds"` (filter default option), and does **not** contain `b">Categories<"` / `b"All categories"`.
- An admin Guilds-tab spec: GET `classes:admin_categories` renders `b"+ New Guild"` and `b"Search guilds"`; the empty state renders `b"No guilds yet"`.
- (Optional) a model spec: `Category._meta.verbose_name == "Guild"` and `verbose_name_plural == "Guilds"`.

No tz/date-window gotchas. Coverage is unaffected (no new branches); the gate is satisfied by the existing + new string assertions.

## 10. Open / deferred & RISK

**RISK — two different "Guild"s.** The makerspace already uses **"Guild"** for a distinct, first-class construct: `membership.Guild`, the member interest-group / funding-vote unit (see `membership/guild_spec.py`, the funding-vote calculator, Airtable's "Glass Guild" etc.). `Category.guild` is even a FK *to* that model (`classes/models.py:75-82`). After this relabel, the CMS will call class **groupings** "Guilds" too — so an admin editing the "Guilds" settings tab, or a member reading the catalog's "Guilds" stat, may briefly conflate the **class-grouping guild** with the **membership guild**.
- **The live collision is on `class_review.html`** (audited in §2a/§6): that page shows the grouping name as data (`:15`) on the same screen as **"Guild Lead ({{ offering.category.guild.name }})"** (`:90`), where "Guild Lead" is the *membership* Guild's lead. Post-relabel, both meanings of "Guild" coexist on one screen. We keep "Guild Lead" (it correctly names the membership role) and rely on the mitigation below — but the builder must eyeball this page after the swap.
- **Mitigation (in scope, fixed wording — two terms only):** use plain **"Guild"** for the grouping and the qualifier **"membership Guild"** wherever the FK/construct must be distinguished; **never** introduce "craft guild." Concretely: set the `Category.guild` FK help_text to the pinned string in §2b (*"Optional link to the membership Guild that owns this grouping. Used for Mailchimp tagging."*) and set the Settings-hub subtitle (`settings_hub.html:7`) to the pinned string *"Groupings that organize the class catalog."* (not "Guild-linked groupings," which reads circularly, and not "Guild groupings"). These are the cheapest insurance against the conflation.
- **Deferred:** any deeper disambiguation (renaming the grouping to "Craft Guild" everywhere, or a tooltip explaining the relationship) is out of scope unless the org reports confusion after launch. The locked decision is plain "Guild" + the "membership Guild" qualifier.

**Coordination — `templates/classes/public/list.html` is edited by two specs.** The sibling spec `2026-06-24-catalog-upcoming-session-count.md` changes the **adjacent** hero stat — the `total_classes` "Classes" stat at ~`:11`. **This** spec changes the **"Categories" → "Guilds" stat at `:12`** and the **filter label/default option at `:41`/`:43`**. The two touch the same hero `<div id="hero-stats">` and the same filter form. Whichever lands second must rebase its hunk, not clobber the other; the changes are on different lines and non-conflicting in intent — just don't blind-overwrite the block.

**Finished-hero check (assigned — neither spec currently owns it).** Once **both** specs have landed, the assembled hero will read roughly **"N Upcoming Sessions · M Guilds · K Instructors"**. **Whichever of the two specs ships second owns verifying the finished hero**: that the three stats line up coherently (label wording, spacing, no orphaned "Categories"/"Classes" leftover from a half-applied edit) and that the row renders correctly in **both Obsidian (dark) and Slate (light)** and **reflows on mobile** without overflow. This is a pure-copy/markup check — no CSS expected — but it must be done by the second-landing builder so the finished hero isn't an unowned gap.

**Explicitly out of scope:** renaming the `Category` model, the DB table, any URL, view, form, related_name, context key, `?category=` query param, Mailchimp `category-{slug}` tag, or the `CategoryFactory`; touching the `membership.Guild` model; any non-demo "Glass" reference (logo prefix, factory data, funding votes).

---

**Closing note (for the builder):** The version bump + member-friendly CHANGELOG entry happen at **BUILD time, not now.** This branch is `release-0.19.x` — each feature bumps the **patch**, and Discord aggregates all 0.19.x changes at merge. **Verify the next available patch number when you build** (check `plfog/version.py` then); do not assign a number in this spec. A friendly changelog line might read: *"Class groupings are now called 'Guilds' across the catalog and class manager, and our demo example studio is now Lamp Working."*
