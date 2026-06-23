# CMS Category Logos + "Past Lives FOG" Brand Implementation Plan

> ✅ **SHIPPED in Release 2.5.7** (commit `d7ae3aa`, PR #107). All tasks below are implemented and live; the unchecked boxes are historical. One deviation from plan: the refreshed `.pl-badge--beta` pill landed in `static/css/unfold-custom.css`, not `static/css/hub.css`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a class has no image, fall back to its category's color logo (then the Past Lives mark); make every category resolve to a logo automatically; and rebrand the shared sidebar to "Past Lives FOG" with a nicer beta pill.

**Architecture:** A single shared helper maps a guild/category *name* → a logo-file prefix in `static/img/guild_logos/`. `Guild.logo_prefix` is refactored to use it, and `Category` gains its own `logo_prefix` property (resolved from its linked guild, else its own name). Templates use `category.logo_prefix` to render the color logo as *the* category icon everywhere, with `favicon.png` (the Past Lives mark) as the universal final fallback. The previously-seeded Lucide line-icons (`Category.icon_svg`) are no longer rendered — collapsing the app to one category-icon system.

**Tech Stack:** Django 5 templates, pytest + pytest-describe (BDD specs), factory-boy. No new dependencies. No DB schema change (the `icon_svg` column stays but is no longer displayed).

---

## Background / context for the implementer

- The CMS is the `classes/` app (public class catalog at the booking subdomain, plus admin/teach sidebars).
- There are currently **two** category-icon systems and this plan collapses them to one:
  1. `Category.icon_svg` — inline Lucide line-icons, seeded once for 14 known names in migration `classes/migrations/0024_category_icon_svg.py`. New/renamed categories have a blank `icon_svg`.
  2. Color logos — `static/img/guild_logos/<prefix>_color.svg` (15 of them, each with a `_bw.svg` sibling). These are the icons the product owner thinks of as "the category icon."
- `ClassOffering.category` is a **required** FK (`on_delete=PROTECT`). A class is therefore *always* assigned to a category — the realistic "no icon" case is a category whose name doesn't match a logo file, which is why we need the Past Lives-mark fallback.
- The shared sidebar brand block lives in `templates/hub/base.html` (lines ~51–55) and is inherited by the FOG member dashboard **and** the CMS admin/teach screens (both `classes/admin/base.html` and `classes/teach/base.html` extend `hub/base.html`). Changing it here updates all of them — this is the approved "everywhere" scope.
- Test layout is inconsistent by app: **membership** specs live under `tests/membership/`, **classes** specs live under `classes/spec/`. Follow each app's existing location.
- Run the suite with `pytest`. Lint/format with `ruff check .` and `ruff format .`. Type-check with `mypy .`.

### Decisions baked into this plan
- **Keep `category.hero_image` in the fallback chain** (admin-uploaded category banners still beat the generic logo).
- **`Category.icon_svg` is left in the DB but no longer rendered.** No destructive migration — avoids data loss and keeps admin editing intact. A future cleanup may remove it.
- **`logo_prefix` returns only a prefix string (or `None`)**; templates build the `{% static %}` URL, matching the existing idiom (`offering.category.guild.logo_prefix`). Keeps `static()` out of models.

---

## File Structure

- Create: `membership/logos.py` — name → logo-prefix mapping + `logo_prefix_for()`.
- Modify: `membership/models.py` (~599–624) — `Guild.logo_prefix` delegates to the helper.
- Modify: `classes/models.py` (`Category`, after the `icon_svg`/`guild` fields, ~74) — add `logo_prefix` property.
- Modify: `templates/classes/public/_list_results.html` (~18–35) — card image fallback + title logo.
- Modify: `templates/classes/public/detail.html` (~34–64, ~121–134) — hero fallback + cat-chip + title logo.
- Modify: `static/css/cms-public.css` — `.cls-img-ph--logo`, `.cp-detail__hero-logo`, cat-chip `img` sizing.
- Modify: `templates/hub/base.html` (~51–55) — wordmark "Past Lives FOG".
- Modify: `static/css/hub.css` (~441–458) — refreshed `.pl-badge--beta` pill.
- Modify: `plfog/version.py` — bump to 2.5.7 + changelog entry.
- Test: `tests/membership/logos_spec.py` (new), `classes/spec/models/category_spec.py`, `classes/spec/views/public_spec.py`.

---

## Task 1: Shared logo-prefix helper

**Files:**
- Create: `membership/logos.py`
- Modify: `membership/models.py:599-624` (`Guild.logo_prefix`)
- Test: `tests/membership/logos_spec.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/membership/logos_spec.py`:

```python
"""BDD specs for the guild/category logo-prefix helper."""

from __future__ import annotations

from membership.logos import logo_prefix_for


def describe_logo_prefix_for():
    def it_matches_a_guild_name_substring():
        assert logo_prefix_for("Woodworking") == "woodworking"

    def it_is_case_insensitive():
        assert logo_prefix_for("CERAMICS") == "ceramics"

    def it_maps_jewelry_category_to_jewelers_logo():
        assert logo_prefix_for("Jewelry") == "jewelers"

    def it_maps_writing_category_to_writers_logo():
        assert logo_prefix_for("Writing") == "writers"

    def it_returns_none_for_unknown_names():
        assert logo_prefix_for("Creative Business") is None

    def it_returns_none_for_blank():
        assert logo_prefix_for("") is None
        assert logo_prefix_for(None) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/membership/logos_spec.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'membership.logos'`.

- [ ] **Step 3: Create the helper**

Create `membership/logos.py`:

```python
"""Maps a guild/category name to its SVG logo file prefix in static/img/guild_logos/.

Logos ship as ``<prefix>_color.svg`` and ``<prefix>_bw.svg``. Both guilds and
class categories resolve a prefix from their name through this single map so
the same artwork represents the same craft everywhere in the app.
"""

from __future__ import annotations

# Case-insensitive name substring → logo file prefix.
_NAME_TO_PREFIX: dict[str, str] = {
    "art framing": "art_framing",
    "ceramics": "ceramics",
    "events": "events",
    "food independence": "food_independence",
    "garden": "garden",
    "glass": "glass",
    "jewelry": "jewelers",
    "jeweler": "jewelers",
    "leather": "leatherwork",
    "metal": "metalworking",
    "prison": "prison_outreach",
    "tech": "tech",
    "textile": "textiles",
    "visual": "visual_arts",
    "wood": "woodworking",
    "writer": "writers",
    "writing": "writers",
}


def logo_prefix_for(name: str | None) -> str | None:
    """Return the guild_logos file prefix matching ``name``, or None if none match."""
    if not name:
        return None
    lowered = name.lower()
    for key, prefix in _NAME_TO_PREFIX.items():
        if key in lowered:
            return prefix
    return None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/membership/logos_spec.py -v`
Expected: PASS (all 6).

- [ ] **Step 5: Refactor `Guild.logo_prefix` to delegate**

In `membership/models.py`, replace the body of `Guild.logo_prefix` (currently the inline `mapping = {...}` dict and loop, ~601–624) with:

```python
    @property
    def logo_prefix(self) -> str | None:
        """Map the guild name to its SVG logo prefix in static/img/guild_logos/."""
        from membership.logos import logo_prefix_for

        return logo_prefix_for(self.name)
```

- [ ] **Step 6: Run the existing guild specs to confirm no regression**

Run: `pytest tests/membership/ -v`
Expected: PASS. (The helper preserves every existing guild key; the only *added* key, `"writing"`, never appears in an existing guild logo name, so outputs are unchanged.)

- [ ] **Step 7: Lint + commit**

```bash
ruff format membership/logos.py membership/models.py tests/membership/logos_spec.py
ruff check --fix membership/logos.py membership/models.py tests/membership/logos_spec.py
git add membership/logos.py membership/models.py tests/membership/logos_spec.py
git commit -m "Extract shared logo_prefix_for helper; Guild.logo_prefix delegates to it"
```

---

## Task 2: `Category.logo_prefix` property

**Files:**
- Modify: `classes/models.py` (`Category`, immediately after the `guild` field / before `created_at`, ~74)
- Test: `classes/spec/models/category_spec.py`

- [ ] **Step 1: Write the failing tests**

Append to `classes/spec/models/category_spec.py`, inside `describe_Category()`:

```python
    def describe_logo_prefix():
        def it_resolves_from_the_category_name(db):
            category = CategoryFactory(name="Woodworking")
            assert category.logo_prefix == "woodworking"

        def it_prefers_the_linked_guild_name_over_its_own(db):
            from tests.membership.factories import GuildFactory

            guild = GuildFactory(name="Ceramics")
            category = CategoryFactory(name="Pottery & Clay", guild=guild)
            assert category.logo_prefix == "ceramics"

        def it_falls_back_to_its_own_name_when_guild_has_no_logo(db):
            from tests.membership.factories import GuildFactory

            guild = GuildFactory(name="Some Unmapped Guild")
            category = CategoryFactory(name="Glass", guild=guild)
            assert category.logo_prefix == "glass"

        def it_returns_none_when_nothing_matches(db):
            category = CategoryFactory(name="Creative Business")
            assert category.logo_prefix is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest classes/spec/models/category_spec.py -v -k logo_prefix`
Expected: FAIL — `AttributeError: 'Category' object has no attribute 'logo_prefix'`.

- [ ] **Step 3: Add the property**

In `classes/models.py`, in the `Category` class (after the `guild` field, before `created_at`), add:

```python
    @property
    def logo_prefix(self) -> str | None:
        """SVG logo prefix for this category's color logo in static/img/guild_logos/.

        Resolves from the linked guild's name when that maps to a logo, otherwise
        from the category's own name. Returns None when neither matches a logo file.
        """
        from membership.logos import logo_prefix_for

        if self.guild_id is not None:
            guild_prefix = self.guild.logo_prefix
            if guild_prefix:
                return guild_prefix
        return logo_prefix_for(self.name)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest classes/spec/models/category_spec.py -v -k logo_prefix`
Expected: PASS (all 4).

- [ ] **Step 5: Lint + commit**

```bash
ruff format classes/models.py classes/spec/models/category_spec.py
ruff check --fix classes/models.py classes/spec/models/category_spec.py
git add classes/models.py classes/spec/models/category_spec.py
git commit -m "Add Category.logo_prefix (guild link, else own name)"
```

---

## Task 3: Card image fallback → color logo → Past Lives mark

**Files:**
- Modify: `templates/classes/public/_list_results.html:18-35`
- Modify: `static/css/cms-public.css` (add `.cls-img-ph--logo`)
- Test: `classes/spec/views/public_spec.py`

- [ ] **Step 1: Write the failing view tests**

Append to `classes/spec/views/public_spec.py` (match the file's existing fixtures/imports — it already builds a published `ClassOfferingFactory` and GETs `reverse("classes:public_list")`). Add a `describe_card_image_fallback()` block:

```python
    def describe_card_image_fallback():
        def it_shows_the_category_color_logo_when_class_has_no_image(client, db):
            from classes.factories import ClassOfferingFactory
            from classes.models import ClassOffering

            offering = ClassOfferingFactory(
                status=ClassOffering.Status.PUBLISHED,
                image="",
                category__name="Woodworking",
            )
            resp = client.get(reverse("classes:public_list"))
            assert resp.status_code == 200
            assert "img/guild_logos/woodworking_color.svg" in resp.content.decode()

        def it_shows_the_past_lives_mark_when_category_has_no_logo(client, db):
            from classes.factories import ClassOfferingFactory
            from classes.models import ClassOffering

            ClassOfferingFactory(
                status=ClassOffering.Status.PUBLISHED,
                image="",
                category__name="Creative Business",
            )
            resp = client.get(reverse("classes:public_list"))
            assert resp.status_code == 200
            assert "img/favicon.png" in resp.content.decode()
```

> Note: `reverse` and `client` are already used in `public_spec.py`; reuse its existing imports/fixtures rather than re-importing. If the file scopes a different fixture name for the HTTP client, use that.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest classes/spec/views/public_spec.py -v -k card_image_fallback`
Expected: FAIL — the color-logo / favicon strings are not in the response (cards currently render `icon_svg` then category initials).

- [ ] **Step 3: Update the card template**

In `templates/classes/public/_list_results.html`, replace the image block inside `<a class="cls-media" ...>` (the `{% if offering.image %} ... {% endif %}` at ~18–30) with:

```django
        {% if offering.image %}
          <img class="cls-img" src="{{ offering.image.url }}" alt="" loading="lazy"
               {% if offering.hero_crop_w %}style="object-position: {{ offering.hero_object_position }};"{% endif %}>
        {% elif offering.legacy_image_url %}
          <img class="cls-img" src="{% url 'classes:legacy_image' %}?url={{ offering.legacy_image_url|urlencode }}" alt="" loading="lazy">
        {% elif offering.category.hero_image %}
          <img class="cls-img" src="{{ offering.category.hero_image.url }}" alt="" loading="lazy">
        {% elif offering.category.logo_prefix %}
          <div class="cls-img-ph cls-img-ph--logo">
            <img src="{% static 'img/guild_logos/'|add:offering.category.logo_prefix|add:'_color.svg' %}" alt="{{ offering.category.name }}" loading="lazy">
          </div>
        {% else %}
          <div class="cls-img-ph cls-img-ph--logo">
            <img src="{% static 'img/favicon.png' %}" alt="Past Lives Makerspace" loading="lazy">
          </div>
        {% endif %}
```

Also update the small title icon at ~34–35: replace `offering.category.guild.logo_prefix` with `offering.category.logo_prefix` so it shows even when the category has no guild link:

```django
          {% if offering.category.logo_prefix %}
            <img src="{% static 'img/guild_logos/'|add:offering.category.logo_prefix|add:'_color.svg' %}" width="16" height="16" alt="{{ offering.category.name }}" style="vertical-align: middle; margin-right: 0.25rem;">
          {% endif %}
```

- [ ] **Step 4: Add the logo-placeholder CSS**

In `static/css/cms-public.css`, after the existing `.cp-page .cls-img-ph--icon svg { ... }` rule (~1016), add:

```css
.cp-page .cls-img-ph--logo { display: flex; align-items: center; justify-content: center; }
.cp-page .cls-img-ph--logo img {
  width: 56%; height: 56%;
  object-fit: contain;
  opacity: .9;
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest classes/spec/views/public_spec.py -v -k card_image_fallback`
Expected: PASS (both).

- [ ] **Step 6: Commit**

```bash
git add templates/classes/public/_list_results.html static/css/cms-public.css classes/spec/views/public_spec.py
git commit -m "Cards fall back to category color logo, then Past Lives mark"
```

---

## Task 4: Detail hero fallback → color logo → Past Lives mark

**Files:**
- Modify: `templates/classes/public/detail.html:34-64`
- Modify: `static/css/cms-public.css` (add `.cp-detail__hero-logo`)
- Test: `classes/spec/views/public_spec.py`

- [ ] **Step 1: Write the failing test**

Append to `classes/spec/views/public_spec.py` a `describe_detail_hero_fallback()` block (uses `classes:public_class_detail` with the offering slug):

```python
    def describe_detail_hero_fallback():
        def it_shows_the_category_color_logo_when_no_images(client, db):
            from classes.factories import ClassOfferingFactory
            from classes.models import ClassOffering

            offering = ClassOfferingFactory(
                status=ClassOffering.Status.PUBLISHED,
                image="",
                category__name="Glass",
            )
            resp = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
            assert resp.status_code == 200
            assert "img/guild_logos/glass_color.svg" in resp.content.decode()

        def it_shows_the_past_lives_mark_when_category_has_no_logo(client, db):
            from classes.factories import ClassOfferingFactory
            from classes.models import ClassOffering

            offering = ClassOfferingFactory(
                status=ClassOffering.Status.PUBLISHED,
                image="",
                category__name="Education",
            )
            resp = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
            assert resp.status_code == 200
            assert "img/favicon.png" in resp.content.decode()
```

> Confirm the detail URL name and kwarg by checking `classes/urls.py` (the list template links via `{% url 'classes:public_class_detail' slug=offering.slug %}`). Use whatever the slug kwarg is actually named.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest classes/spec/views/public_spec.py -v -k detail_hero_fallback`
Expected: FAIL — the detail hero currently renders no `<img>` at all when there are no images, so neither string is present.

- [ ] **Step 3: Update the detail hero template**

In `templates/classes/public/detail.html`, the hero `<img>` chain ends at the `{% elif offering.category.hero_image %}` branch then `{% endif %}` (~54–64). Add two branches before the `{% endif %}`:

```django
    {% elif offering.category.logo_prefix %}
      <div class="cp-detail__hero-logo">
        <img src="{% static 'img/guild_logos/'|add:offering.category.logo_prefix|add:'_color.svg' %}" alt="{{ offering.category.name }}">
      </div>
    {% else %}
      <div class="cp-detail__hero-logo">
        <img src="{% static 'img/favicon.png' %}" alt="Past Lives Makerspace">
      </div>
    {% endif %}
```

(`{% load static %}` is already declared at the top of `detail.html`.)

- [ ] **Step 4: Add the hero-logo CSS**

In `static/css/cms-public.css`, after the `.cp-detail__hero-img { ... }` rule (~562), add:

```css
.cp-detail__hero-logo {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}
.cp-detail__hero-logo img {
  width: clamp(120px, 22vw, 200px);
  height: clamp(120px, 22vw, 200px);
  object-fit: contain;
  opacity: .9;
}
```

(`.cp-detail__hero` is already `position: relative`, so the absolutely-positioned logo centers within it and sits under the existing `.cp-detail__hero-overlay`.)

- [ ] **Step 5: Run to verify it passes**

Run: `pytest classes/spec/views/public_spec.py -v -k detail_hero_fallback`
Expected: PASS (both).

- [ ] **Step 6: Commit**

```bash
git add templates/classes/public/detail.html static/css/cms-public.css classes/spec/views/public_spec.py
git commit -m "Detail hero falls back to category color logo, then Past Lives mark"
```

---

## Task 5: Use the color logo as the category icon in the detail cat-chip + title

**Files:**
- Modify: `templates/classes/public/detail.html:121-134`
- Modify: `static/css/cms-public.css` (cat-chip `img` sizing)

This collapses the last Lucide-icon usages onto the single color-logo system.

- [ ] **Step 1: Update the cat-chip (replace inline `icon_svg`)**

In `detail.html` (~122–124), replace:

```django
        <a class="cp-detail__cat-chip" href="{% url 'classes:public_category' slug=offering.category.slug %}">
          {% if offering.category.icon_svg %}{{ offering.category.icon_svg|safe }}{% endif %}{{ offering.category.name }}
        </a>
```

with:

```django
        <a class="cp-detail__cat-chip" href="{% url 'classes:public_category' slug=offering.category.slug %}">
          {% if offering.category.logo_prefix %}<img src="{% static 'img/guild_logos/'|add:offering.category.logo_prefix|add:'_color.svg' %}" alt="">{% endif %}{{ offering.category.name }}
        </a>
```

- [ ] **Step 2: Update the title logo (use category, not guild, prefix)**

In `detail.html` (~131–133), replace `offering.category.guild and offering.category.guild.logo_prefix` / `offering.category.guild.logo_prefix` with the category property:

```django
        {% if offering.category.logo_prefix %}
          <img src="{% static 'img/guild_logos/'|add:offering.category.logo_prefix|add:'_color.svg' %}" width="32" height="32" alt="{{ offering.category.name }}" style="vertical-align: middle; margin-right: 0.5rem;">
        {% endif %}
```

- [ ] **Step 3: Update cat-chip icon CSS to size the `<img>`**

In `static/css/cms-public.css`, the rule `.cp-detail__cat-chip svg { ... }` (~1017) targets `svg`. Add an `img` selector alongside it:

```css
.cp-detail__cat-chip svg,
.cp-detail__cat-chip img {
  width: 13px; height: 13px;
  vertical-align: -2px;
  margin-right: 5px;
}
```

- [ ] **Step 4: Verify rendering manually + run detail specs**

Run: `pytest classes/spec/views/public_spec.py -v`
Expected: PASS (no assertions break; the new img URLs match the same `guild_logos` path).

- [ ] **Step 5: Commit**

```bash
git add templates/classes/public/detail.html static/css/cms-public.css
git commit -m "Detail cat-chip and title use the category color logo"
```

> The "Supported by the <guild>" section (~301–311) legitimately uses the *guild* logo — leave it on `offering.category.guild.logo_prefix`.

---

## Task 6: "Past Lives FOG" wordmark + refreshed beta pill

**Files:**
- Modify: `templates/hub/base.html:54` (wordmark text)
- Modify: `static/css/hub.css:441-458` (`.pl-badge--beta`)

- [ ] **Step 1: Change the wordmark text**

In `templates/hub/base.html`, line ~54, change:

```django
                <span class="pl-brand__text">Past Lives</span>
```

to:

```django
                <span class="pl-brand__text">Past Lives FOG</span>
```

Leave the beta `<button class="pl-brand__label pl-badge--beta" ...>BETA v{{ app_version }}</button>` markup and its changelog-modal `onclick` exactly as-is.

- [ ] **Step 2: Refresh the pill CSS**

In `static/css/hub.css`, replace the `.pl-badge--beta` and `.pl-badge--beta:hover` rules (~441–458) with a cleaner pill (subtle border + a small leading dot via `::before`):

```css
.pl-badge--beta {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    font-size: 0.625rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 0.15rem 0.55rem;
    border-radius: 999px;
    background-color: rgba(238, 180, 75, 0.15);
    color: var(--color-tuscan-yellow);
    border: 1px solid rgba(238, 180, 75, 0.35);
    cursor: pointer;
    font-family: var(--font-body);
    transition: background-color 0.15s ease, border-color 0.15s ease;
}

.pl-badge--beta::before {
    content: "";
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background-color: var(--color-tuscan-yellow);
    flex: none;
}

.pl-badge--beta:hover {
    background-color: rgba(238, 180, 75, 0.3);
    border-color: rgba(238, 180, 75, 0.55);
}
```

- [ ] **Step 3: Verify in the browser (run skill)**

Start the dev server and confirm the FOG sidebar reads "Past Lives FOG" with the new pill, and that the CMS admin/teach sidebars (which extend `hub/base.html`) show the same. Use the project `run` skill.
Expected: wordmark updated everywhere the shared sidebar renders; clicking the pill still opens the changelog modal.

- [ ] **Step 4: Commit**

```bash
git add templates/hub/base.html static/css/hub.css
git commit -m "Rebrand sidebar to 'Past Lives FOG' with a refreshed beta pill"
```

---

## Task 7: Version bump + changelog

**Files:**
- Modify: `plfog/version.py`

Per project rule, every PR bumps `VERSION` and prepends a member-friendly `CHANGELOG` entry (this feeds the Discord release post — plain language, no jargon).

- [ ] **Step 1: Bump version + prepend changelog entry**

In `plfog/version.py`, set `VERSION = "2.5.7"` and insert this as the first item in the `CHANGELOG` list:

```python
    {
        "version": "2.5.7",
        "date": "2026-06-15",
        "title": "Friendlier class images and a refreshed sidebar badge",
        "changes": [
            "Classes that don't have their own photo now show their category's colorful logo instead of a plain placeholder. If a category doesn't have a logo yet, you'll see the Past Lives mark.",
            "The sidebar now reads \"Past Lives FOG\" with a cleaner little beta badge.",
        ],
    },
```

- [ ] **Step 2: Commit**

```bash
git add plfog/version.py
git commit -m "Bump version to 2.5.7 and add changelog entry"
```

---

## Final verification

- [ ] **Run the full suite**

Run: `pytest`
Expected: all pass, 100% coverage (no new uncovered Python lines — the new property and helper are exercised by Tasks 1–2).

- [ ] **Lint + format + type-check**

```bash
ruff format .
ruff check .
mypy .
```
Expected: clean. (`mypy` needs `DATABASE_URL` — `export $(grep '^DATABASE_URL=' .env | xargs)` first if running before push.)

- [ ] **Manual smoke test (run skill)**

- Catalog page: a published class with no image whose category maps to a logo (e.g. Woodworking) shows the color logo; a class whose category doesn't map (e.g. Creative Business) shows the Past Lives mark.
- Detail page: same two cases render in the hero, and the cat-chip/title show the color logo.
- Sidebar (FOG dashboard + CMS admin/teach): reads "Past Lives FOG" with the new pill; pill opens the changelog modal.

---

## Notes for reviewer
- No DB migration. `Category.icon_svg` is retained but no longer rendered; the Lucide-seed migration `0024` stays for history. A follow-up could drop the column once we're sure nothing else reads it.
- Categories with no matching logo (e.g. "Creative Business", "Education") intentionally fall back to the Past Lives mark — that is the approved behavior, not a gap.
- Scope of the brand change is the shared sidebar (`hub/base.html`) per the approved "everywhere" decision; the public catalog topbar (`cp-topbar` in `base_public.html`) has no beta badge and is intentionally left unchanged.
