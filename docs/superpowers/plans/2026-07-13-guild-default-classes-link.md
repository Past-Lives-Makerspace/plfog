# Guild "[Guild] Classes" default link + `?guild=` catalog filter — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-13
**Surface:** FOG hub `pastlives.test` (guild detail page) + guilds surface `guilds.pastlives.app` + book/classes portal `book.pastlives.space` (public catalog). No admin surface.
**Related:** none (extends the existing guild-detail Links card + the classes public catalog filters).
**Size:** Small.

---

## 1. Summary

Every guild page will always show a link named **"[Guild name] Classes"** in its Links card. Clicking it opens
the class catalog pre-filtered to that guild, so a member can jump from a guild's page straight to everything that
guild teaches. The link is **always present** — it shows even for a brand-new guild that has no links and no
classes yet — and when the guild has nothing scheduled the catalog greets the visitor with a friendly
"No classes scheduled for [Guild] yet" instead of a dead end. Reaching the filtered catalog relies on a new
`?guild=<slug>` filter on the public class list.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Is the default link a stored `GuildLink` row? | **No — it is virtual / always-present.** Injected into the guild-detail `links` context at render time, so it needs no data migration, stays correct if the guild is renamed, and can't be accidentally deleted. |
| Can leads edit or delete it? | **No.** It exists only in the guild **detail** view's context, never in the guild **edit** Links formset, so there is structurally no control to edit or delete it. Real `GuildLink` rows remain fully editable next to it. |
| How does the link reach the right catalog view? | Via a **new `?guild=<slug>` filter** on `classes:public_list` that filters `category__guild__slug`. |
| What does the catalog show when the guild has zero classes? | The link still works; the catalog shows an active-filter heading **"Classes in [Guild]"** and an empty state **"No classes scheduled for [Guild] yet."** |

---

## 2. What already exists (reuse, don't reinvent)

| Need | Existing thing | Location |
|---|---|---|
| Guild links render loop | `{% if links %}` Links card + `{% for link in links %}<a href="{{ link.url }}" target="_blank" rel="noopener">{{ link.label }}</a>{% endfor %}` | `templates/hub/guild_detail.html:340-347` |
| `links` context is set + passed | `links = guild.links.all()` → `"links": links` in the render dict | `hub/views.py:514` (set) and `:574` (pass) — same `guild_detail` view |
| Catalog view + URL name | `public_list` at `/classes/`, name `classes:public_list` | `classes/views.py:182`, `classes/urls.py:10` |
| Where GET filters compose | `_apply_browse_filters(qs, request)` — already has a `?category=<slug>` branch filtering `category__slug` (`~156-158`); guild branch mirrors it | `classes/views.py:154-179` |
| Class → guild join | `ClassOffering.category` FK → `Category.guild` FK (`related_name="categories"`), so guild filter = `category__guild__slug=<slug>` | `classes/models.py:211`, `classes/models.py:74-80` |
| Guild slug (stable, unique, auto-generated) | `Guild.slug` SlugField, "stable across renames" | `membership/models.py:1073-1078` |
| Cross-surface link pattern already used on this page | Existing class links prefix `BOOK_BASE_URL` only on the guilds surface: `{% if is_guilds_surface %}{{ BOOK_BASE_URL }}{% endif %}{% url 'classes:register' … %}` | `templates/hub/guild_detail.html:134,183` |
| Surface + base-URL plumbing | `request.surface` ∈ `members`/`guilds`/`public`/…; `settings.BOOK_BASE_URL` | `core/context_processors.py:50-82`, `plfog/settings.py:484` |
| Catalog results summary / empty state | `.cp-results__summary` renders count or `No classes match your filters.` + reset link | `templates/classes/public/_list_results.html:4-12` |
| Portal CSS home + tokens | `cp-`/`cls-` classes, tokens `--text` / `--text2` / `--gold` / `--max` | `static/css/cms-public.css:1282-1295` |

**Gaps to close (all small):**
1. A `?guild=<slug>` branch in `_apply_browse_filters`.
2. Resolve the selected guild in `public_list` and pass `selected_guild` / `selected_guild_slug` to the template.
3. A hidden `guild` input in the filter form so the guild filter *persists* when a user also changes a dropdown/popover filter (composition).
4. An active-filter heading + guild-specific empty state in `_list_results.html`.
5. A thin fat-model method on `Guild` that returns the virtual link (label + surface-correct URL), prepended to `links` in `guild_detail`.

---

## 3. Where the code lives

```
membership/
  models.py                         # + Guild.classes_link(...) method + a small frozen VirtualLink dataclass
classes/
  views.py                          # + ?guild= branch in _apply_browse_filters; + selected_guild context in public_list
hub/
  views.py                          # guild_detail: prepend Guild.classes_link(...) to `links`
templates/
  hub/guild_detail.html             # (no change — the shared {% for link in links %} loop renders the virtual link as-is)
  classes/public/list.html          # + hidden <input name="guild"> in the filter form
  classes/public/_list_results.html # + "Classes in <Guild>" heading + guild-specific empty state
static/css/cms-public.css           # + .cp-results__guild heading style (portal-local prefix — see §6 note)
plfog/version.py                    # VERSION bump + CHANGELOG entry
```

No new app, no migration, no new template file. Home apps: `membership` (model), `classes` (filter/view + templates), `hub` (injection point).

---

## 4. Data model & business logic — no migration

**No schema change.** The virtual link is computed, never stored.

**`Guild.classes_link(*, guilds_surface: bool) -> VirtualLink` (fat model, `membership/models.py`).** Returns a small
frozen dataclass so it's typed and testable, and so the template only touches `.label` / `.url` exactly like a real
`GuildLink`:

```python
@dataclasses.dataclass(frozen=True)
class VirtualLink:
    label: str
    url: str

# on Guild:
def classes_link(self, *, guilds_surface: bool = False) -> VirtualLink:
    path = f"{reverse('classes:public_list')}?guild={self.slug}"
    url = f"{settings.BOOK_BASE_URL}{path}" if guilds_surface else path
    return VirtualLink(label=f"{self.name} Classes", url=url)
```

- **Label:** `"{self.name} Classes"` — recomputed each render, so a rename is reflected immediately (no stored copy to drift).
- **URL:** mirrors the page's existing class links — root-relative `/classes/?guild=<slug>` on the members surface
  (resolves on the member hub), absolute `BOOK_BASE_URL + /classes/?guild=<slug>` on the guilds surface (the classes
  app isn't in the guilds allowlist, same reason `classes:register` is prefixed there today).
- **No side effects, no query** — cheap, so a plain method is fine (not a property only because it takes the surface arg).

**`_apply_browse_filters` guild branch (`classes/views.py`).** Mirror the category branch exactly, immediately after it:

```python
guild_slug = request.GET.get("guild", "").strip()
if guild_slug:
    qs = qs.filter(category__guild__slug=guild_slug)
```

Because every branch narrows the same `qs` with `.filter(...)`, the guild filter **composes as an AND** with
category, instructor, price, members-only, free, and upcoming — no special-casing.

**`public_list` context additions.** Resolve the guild for the heading (fail-soft, mirroring how the raw `?category=`
filter never 404s):

```python
selected_guild_slug = request.GET.get("guild", "").strip()
selected_guild = Guild.objects.filter(slug=selected_guild_slug).first() if selected_guild_slug else None
# → add "selected_guild_slug" and "selected_guild" to the context dict (used by both the full page and the HTMX partial)
```

An unknown slug leaves `selected_guild = None`; the filter still yields zero rows, so the visitor sees the generic
"No classes match your filters." — acceptable, since the guild page only ever emits a valid slug.

---

## 5. Business logic notes

Injection point — `hub/views.py` `guild_detail`, replace the line at `:514`:

```python
guilds_surface = getattr(request, "surface", "members") == "guilds"
links = [guild.classes_link(guilds_surface=guilds_surface), *guild.links.all()]
```

The virtual link is **prepended** so it leads the Links card. Because this happens only in `guild_detail`, the guild
**edit** page's Links formset (which iterates `guild.links.all()` directly) never sees it — that's what makes it
non-editable / non-deletable, no extra guard needed.

---

## 6. UI / UX  — completeness checklist applied

Two screens are touched. No form is added (this is a link + a query filter), but the filtered catalog's active and
empty states are specced concretely below.

### Screen A — Guild detail Links card (`templates/hub/guild_detail.html:340-347`)

- **Layout & container:** the existing `<div class="hub-card">` "Links" card, rendered by the existing
  `{% for link in links %}` loop. **No template change** — the virtual link is just the first item in `links`.
- **Controls:** the link renders as `<a href="{{ link.url }}" target="_blank" rel="noopener">{{ guild.name }} Classes</a>`,
  visually identical to sibling links (same `.pl-guild-links` styling). No edit/delete controls here — the detail page
  renders links read-only; editing happens on the guild edit page, which this virtual link never reaches.
- **Always present (requirement 3):** since `links` is now never empty, the `{% if links %}` card **always renders**,
  so "[Guild] Classes" shows even when the guild has **zero** `GuildLink` rows **and zero** classes.
- **New-tab / boost behavior:** it inherits `target="_blank" rel="noopener"` from the shared loop. That is acceptable
  and, on the guilds surface, convenient — a `target="_blank"` link is skipped by `hx-boost`, so the cross-surface
  `BOOK_BASE_URL` navigation isn't boosted (the same reason the sibling class links carry `hx-boost="false"`). Opening
  our own catalog in a new tab is a mild smell but keeps the shared loop untouched; see §10 if the reviewer prefers
  same-tab (would require splitting the virtual link out of the loop).
- **States:** single, always-present link — no empty/loading/error states of its own; the destination handles empties.
- **Dark + light:** no new CSS; inherits `.pl-guild-links` which already uses theme tokens. Verify both themes.
- **Mobile:** unchanged — the Links card already reflows; one more link wraps naturally.

### Screen B — Filtered class catalog (`templates/classes/public/list.html` + `_list_results.html`)

- **Layout & container:** the existing portal list page. Two edits:
  1. **`list.html`** — add a hidden field to the filter `<form id="cp-filter-form">` so the guild filter **composes
     with** and **persists across** dropdown/popover changes (otherwise the form, serializing only its own fields,
     would drop `?guild=` on the next filter change):

     ```django
     {% if selected_guild_slug %}<input type="hidden" name="guild" value="{{ selected_guild_slug }}">{% endif %}
     ```

     `Reset all` / the popover `Reset` links already point at bare `{% url 'classes:public_list' %}`, so they clear
     the guild filter too — no change needed there.
  2. **`_list_results.html`** — an **active-filter heading** and a **guild-specific empty state**, both inside the
     HTMX-swapped partial so they stay correct after any client-side filter change:

     ```django
     {% if selected_guild %}
       <h2 class="cp-results__guild">Classes in {{ selected_guild.name }}
         <a href="{% url 'classes:public_list' %}" class="cp-results__reset-inline">View all classes</a>
       </h2>
     {% endif %}
     ```

     And rework the existing empty branch of `.cp-results__summary`:

     ```django
     {% else %}
       {% if selected_guild %}
         <em>No classes scheduled for {{ selected_guild.name }} yet.</em>
       {% else %}
         <em>No classes match your filters.</em>
       {% endif %}
       <a href="{% url 'classes:public_list' %}" class="cp-results__reset-inline">Reset all filters</a>
     {% endif %}
     ```

- **States (all four named):**
  - **Active/success:** "Classes in [Guild]" heading above the results grid tells the user the filter is on and gives
    a one-click "View all classes" out — so the filter is never a silent trap.
  - **Empty:** guild set + zero results → "No classes scheduled for [Guild] yet." (never a bare blank grid).
  - **Loading:** unchanged — the filter form's existing `hx-get`/`hx-target="#cls-results"` swap is reused; no new
    in-flight UI needed for a link-driven full-page arrival.
  - **Error:** unknown/garbled slug → `selected_guild` is `None`, filter yields zero, generic "No classes match your
    filters." with a reset link. No 500.
- **Composition (requirement 1):** the backend ANDs `category__guild__slug` with every other active filter; the hidden
  input keeps `guild` in the querystring (and thus in `hx-push-url` and `filter_querystring` pagination links) as the
  user layers on instructor/price/etc. The heading persists as long as `guild` is present and clears when they Reset.
- **Dark + light:** the new `.cp-results__guild` heading uses only existing portal tokens (`--text`, `--gold`), matching
  `.cp-results__summary` right above it. No inline `background`/`color`. Verify both themes (the portal's Slate/Obsidian).
- **Mobile:** the heading is a single wrapping line above the grid — no fixed width, reflows on narrow screens; the
  inline "View all classes" link wraps under the title if needed. 8px-grid spacing to match `.cp-results__summary`.

> **Prefix note (flagged per "match, don't invent"):** the classes public portal is written entirely in the page-local
> `cp-`/`cls-` prefixes (in `static/css/cms-public.css`), **not** the global `pl-`. The one new class `.cp-results__guild`
> deliberately follows the portal's convention rather than introducing `pl-` into a `cp-` page. Calling this out so the
> reviewer can veto if they'd rather keep the strict `pl-` default here.

---

## 7. Notifications / emails / activity

None — no email, notification, or `SiteActivity` is sent by this feature.

---

## 8. Build order (each phase ships green)

1. **Model + filter (backend).** Add `VirtualLink` dataclass + `Guild.classes_link(...)`; add the `?guild=` branch to
   `_apply_browse_filters`; add `selected_guild` / `selected_guild_slug` to `public_list` context. Specs for each. Green.
2. **Injection + templates (frontend).** Prepend `guild.classes_link(...)` to `links` in `guild_detail`; add the hidden
   `guild` input to `list.html`; add the heading + guild empty state to `_list_results.html`; add `.cp-results__guild`
   to `cms-public.css`. Verify both themes on both surfaces (members hub + guilds). Green.
3. **Housekeeping.** `ruff format .` + `ruff check .`. Bump `VERSION` in `plfog/version.py`. Add a member-friendly
   CHANGELOG entry (net-new member-facing feature) — *unless* the current unreleased `MAJOR.MINOR` line already has a
   guild-pages entry, in which case fold a bullet into it and re-stamp its `version`/`date` per the curation rules.
   Suggested copy: *"Every guild page now has a '[Guild] Classes' link that jumps straight to that guild's classes in
   the catalog — even before the guild has scheduled any."*

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*`, factory-boy, run in the `plfog-web` Docker image, ≥98% coverage.

**`membership` — `Guild.classes_link`:**
- `it_labels_the_link_with_the_guild_name_plus_classes` → label == `"{guild.name} Classes"`.
- `it_builds_a_root_relative_url_on_the_members_surface` → url == `/classes/?guild=<slug>`.
- `it_prefixes_book_base_url_on_the_guilds_surface` → url startswith `settings.BOOK_BASE_URL` and ends `/classes/?guild=<slug>`.
- `it_reflects_a_renamed_guild` → rename guild, label updates (no stored copy).

**`classes` — `_apply_browse_filters` / `public_list`:**
- `it_filters_offerings_by_category__guild__slug` when `?guild=` given.
- `it_ands_the_guild_filter_with_category` (guild + category together narrow correctly).
- `it_ands_the_guild_filter_with_price_or_instructor`.
- `it_ignores_a_blank_or_absent_guild_param` (full catalog unchanged).
- `it_sets_selected_guild_in_context_when_the_slug_resolves`.
- `it_leaves_selected_guild_none_for_an_unknown_slug` (and results are empty → generic empty copy).
- **Template state (assert on rendered markup, not free text — the "what's new" widget echoes the changelog):**
  - `it_renders_the_classes_in_guild_heading` → `.cp-results__guild` present with the guild name when `?guild=` resolves.
  - `it_shows_the_guild_specific_empty_state` → "No classes scheduled for <Guild> yet." when guild set and zero results.
  - `it_keeps_the_guild_filter_in_the_form` → hidden `<input name="guild" value="<slug>">` present when slug set (assert on the input tag, so a nested-`<form>`/structure regression is caught — a plain content grep wouldn't).

**`hub` — `guild_detail` injection:**
- `it_prepends_the_virtual_classes_link_to_links` → first item's `.label` == `"{guild.name} Classes"`.
- `it_shows_the_link_with_zero_guildlinks_and_zero_classes` (requirement 3) → Links card + virtual link render on a bare guild.
- `it_still_renders_real_guild_links_after_the_virtual_one` → real `GuildLink` rows follow it.

No timezone/date-window concerns (no dated logic added).

## 10. Open / deferred

- **New tab vs same tab.** The virtual link inherits `target="_blank"` from the shared Links loop (zero template
  change, and it conveniently disables `hx-boost` on the cross-surface guilds variant). If the reviewer prefers the
  internal catalog to open in the same tab, split the virtual link out of the loop into its own `<a>` above
  `{% for link in links %}` — costs one template branch. Recommend keeping it in the loop for simplicity.
- **`cp-` vs `pl-` prefix** for `.cp-results__guild` — flagged in §6; defaulting to the portal-local `cp-` convention.
- **"Guild" dropdown naming overlap.** The catalog's existing filter dropdown is labeled "Guild" but actually filters a
  single `category__slug`; the new `?guild=` filters all categories under a guild. They're orthogonal and coexist
  cleanly (the banner disambiguates). Renaming the dropdown is **out of scope**.
- **Category chips / counts** continue to reflect the unfiltered universe (existing behavior) — out of scope to
  re-scope them to the selected guild.
