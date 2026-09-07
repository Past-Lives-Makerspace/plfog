# PLAT-1: Brand config and a `brand()` context processor

**Status:** ready to build. One PR off `main`.
**Date:** 2026-09-07
**Tracking:** https://github.com/HexagonStorms/fletcher/issues/2 (Fletcher SaaS epic, Phase A, first story)
**Surfaces touched:** `core.SiteConfiguration`, `core/context_processors.py`, Site Settings (`/manage/site-settings/`), and four base templates.

---

## 1. Summary

The app has the Past Lives identity typed directly into its templates. This story moves the
identity into seven editable settings, exposes them to every template through a context
processor, gives an admin a **Brand** tab in Site Settings to change them, and swaps the four
highest-traffic templates over to the new values.

Every field ships with the current Past Lives value as its DB default, so on the live instance
this change renders **byte-identical HTML**. Nothing a member sees moves.

**Acceptance:** changing **Organization name** in Site Settings changes the name on the member
hub, the public classes pages, the public guilds pages, and the privacy policy. No hardcoded
organization name, support email, website URL, logo path, or brand color remains in those four
templates.

### Why `SiteConfiguration` and not a new `Organization` model

One deployment equals one organization equals one `SiteConfiguration` row. A parallel
`Organization` model would only pay off under shared multi-tenancy, which is explicitly
deferred. Do not create one.

---

## 2. What exists today (verified, build on this)

| Thing | Location |
|---|---|
| The singleton, ~45 fields, zero brand fields | `core/models.py:459` `class SiteConfiguration` |
| Singleton forcing + `load()` | `core/models.py` `save()` (forces `pk = 1`) and `load()` (`get_or_create(pk=1)`) |
| Existing context processors | `core/context_processors.py` (`registration_mode`, `app_version`, `makerspace_wiki`, `theme`, `feature_flags`, `google_analytics`, `surface`, `notification_badge`, `persona`, `tour_runtime`) |
| Registered processor list | `plfog/settings.py:235` to `:252` |
| Site Settings view | `hub/views.py:7111` `admin_site_settings`, save helper at `hub/views.py:7047` `_save_site_settings` |
| Site Settings form | `hub/forms.py:891` `class SiteSettingsForm` |
| Site Settings template | `templates/hub/admin/site_settings.html` (1264 lines) |
| URL | `hub/urls.py:596` `manage/site-settings/` |
| ImageField house pattern | `membership/models.py:2759` (`OrgInfoPage.banner_image`), `membership/models.py:1780` (`Guild.banner_image`) |
| Image size validator | `core/validators.py:10` `validate_image_size` |
| Orphan cleanup on replace | `core/files.py:45` `delete_orphan_on_replace` |
| Media storage | `plfog/settings.py:393` to `:419`. `STORAGES["default"]` is Cloudflare R2 when the `R2_*` env vars are set, else local `FileSystemStorage`. **An `ImageField` needs nothing but `upload_to=`; storage is global.** |
| Image upload UI component | `templates/components/image_field.html` |
| Color picker component | `templates/components/color_picker.html` (routed to automatically by `form_field.html` when the widget is `type="color"`) |
| A singleton with an image, saved the same way | `membership/models.py:2839` `OrgInfoPage.save()` — copy this shape |
| Legal entity name in the tree | `classes/models.py:46` `"Past Lives Makerspace LLC, 2808 SE 9th Ave, Portland, OR 97202"` |

`SiteConfiguration.load()` does **not** cache. `registration_mode`, `feature_flags`, and
`google_analytics` each call it, so a page render already costs three `SELECT`s on this table.

---

## 3. The seven fields

Add to `core/models.py`, at the **end** of the `SiteConfiguration` field block (immediately
before `class Meta`), as one contiguous group under a comment. Import
`from core.validators import validate_hex_color, validate_image_size` and
`from core.files import delete_orphan_on_replace` at the top of the module.

```python
    # Brand block (PLAT-1). One deployment is one organization; these are the strings and
    # assets that identify it. Defaults are the Past Lives values so the migration is a
    # no-op on the live instance.
    org_name = models.CharField(
        max_length=200,
        default="Past Lives Makerspace",
        verbose_name="Organization name",
        help_text="Your organization's full name. Shown in page titles, the privacy policy, and public page descriptions.",
    )
    org_short_name = models.CharField(
        max_length=60,
        blank=True,
        default="Past Lives",
        verbose_name="Short name",
        help_text="The compact wordmark used in the sidebar, the public topbar, and browser tab titles. Blank uses the full name.",
    )
    org_legal_name = models.CharField(
        max_length=200,
        blank=True,
        default="Past Lives Makerspace LLC",
        verbose_name="Legal name",
        help_text="Your registered legal entity, for receipts and legal notices. Blank uses the organization name.",
    )
    org_logo = models.ImageField(
        upload_to="brand/logo/",
        blank=True,
        validators=[validate_image_size],
        verbose_name="Logo",
        help_text="Square logo shown in the sidebar, the public topbar, and the browser tab. Blank uses the built in mark.",
    )
    org_primary_color = models.CharField(
        max_length=7,
        blank=True,
        default="#092E4C",
        validators=[validate_hex_color],
        verbose_name="Primary brand color",
        help_text="Your main brand color as a hex code, e.g. #092E4C. Sets the browser and mobile app chrome color.",
    )
    org_support_email = models.EmailField(
        blank=True,
        default="info@pastlives.space",
        verbose_name="Support email",
        help_text="The address members are told to write to for help. Shown in the privacy policy.",
    )
    org_website_url = models.URLField(
        blank=True,
        default="https://pastlives.space",
        verbose_name="Public website",
        help_text="Your main marketing website, with no trailing slash. The public topbar's Home, Guilds, Membership, and Contact links and the sidebar globe icon are built from it.",
    )
```

### Where each default comes from

| Field | Default | Evidence in the tree today |
|---|---|---|
| `org_name` | `Past Lives Makerspace` | `templates/core/privacy_policy.html:4,16,24,99`; `templates/classes/base_public.html:108`; `templates/guilds/base_public.html:108` |
| `org_short_name` | `Past Lives` | `templates/hub/base.html:8` (title suffix), `:80` (`alt`), `:573`; `templates/classes/base_public.html:19`; `templates/guilds/base_public.html:21` |
| `org_legal_name` | `Past Lives Makerspace LLC` | `classes/models.py:46` |
| `org_logo` | blank (falls back to `static/img/favicon.png`) | `templates/hub/base.html:9,80,572`; `templates/classes/base_public.html:18`; `templates/guilds/base_public.html:20` |
| `org_primary_color` | `#092E4C` | `templates/hub/base.html:7` `<meta name="theme-color" content="#092E4C">`. Also `--color-navy` in FRONTEND.md's token table. |
| `org_support_email` | `info@pastlives.space` | `templates/core/privacy_policy.html:19,68,100` |
| `org_website_url` | `https://pastlives.space` | `templates/hub/base.html:401`; the ten `https://pastlives.space*` links in `templates/classes/base_public.html` and eight in `templates/guilds/base_public.html` |

### Three field decisions, stated plainly

1. **`org_name` is the only required field** (no `blank=True`). A deployment with no name renders
   an empty title and an empty privacy policy, which is a broken product, so it fails loudly at
   the form. Every other field degrades gracefully via a fallback in the context processor or a
   static asset. This does mean existing settings POST fixtures must gain `org_name`; see §10.
2. **`org_primary_color` defaults to the structural navy `#092E4C`, not the gold `#EEB44B`.**
   FRONTEND.md calls `--color-tuscan-yellow` the primary *accent*, so the naming is genuinely
   ambiguous. The tiebreaker is the no-op rule: the one place a brand color is consumed in these
   four templates is `<meta name="theme-color">`, which is navy today. PLAT-7 decides how a
   tenant color maps onto the accent and structural tokens; PLAT-1 only stores it and feeds
   `theme-color`.
3. **The logo is NOT run through `normalize_field_if_uploaded`.** That helper re-encodes to JPEG
   and flattens RGBA onto white (`core/images.py:33` to `:69`), which would put a white box
   behind a transparent PNG logo on the dark theme. Logos are transparent PNGs. `Guild.banner_image`
   and `OrgInfoPage.banner_image` are likewise un-normalized, so this matches precedent. Size is
   bounded by `validate_image_size` server side (10 MB) and by `max_bytes` client side in the
   upload component.

### New validator

`core/validators.py` has no hex validator (`RegexValidator` appears nowhere in the repo). Add one
in the same plain-function shape as its neighbours:

```python
def validate_hex_color(value: str) -> None:
    """Reject a color that is not a six digit hex code with a leading #."""
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValidationError(f"Enter a six digit hex color like #092E4C (got '{value}').")
```

`import re` at the top of `core/validators.py`. Django's `run_validators` skips empty values, so a
blank `org_primary_color` passes without a special case. **Do not** retrofit this validator onto
`general_calendar_color` or `classes_calendar_color`; existing rows are not guaranteed to satisfy it
and that is not this story.

### `save()` change

`SiteConfiguration.save()` (`core/models.py`, the `save` that forces `pk = 1`) gains one line
before `super().save(...)`, mirroring `OrgInfoPage.save()`:

```python
        delete_orphan_on_replace(self, "org_logo")
```

Safe on first create: `pk` is already forced to 1, the row does not exist, `delete_orphan_on_replace`
catches `DoesNotExist` and returns.

---

## 4. Migration

Run `python manage.py makemigrations core`. The next number is **0077** (`core/migrations/` ends at
`0076_biometriccredential.py`); expect `0077_siteconfiguration_org_legal_name_and_more.py`.

- Seven `AddField` operations, each carrying its `default`. Django will not prompt, because every
  non-nullable field has a default.
- **No `RunPython`, therefore no reverse function is needed.** CLAUDE.md's reverse-function rule
  applies to data migrations; this is schema only and `AddField` reverses itself.
- **Behavioral no-op check.** On the live instance the singleton row gains seven columns already
  holding the exact strings the templates print today, so every swap in §5 renders the identical
  bytes. Verify this claim by eye against the swap table before opening the PR.
- Run `python manage.py check` after generating it. CI runs system checks that pytest skips, and
  this repo has been bitten by the 30-character index-name cap (PR #205). No new indexes here, but
  run it.

---

## 5. The `brand()` context processor

Add to `core/context_processors.py`, immediately after `feature_flags` (whose shape it copies).
Add `from urllib.parse import urlsplit` to the module imports.

```python
def brand(request: HttpRequest) -> dict[str, str]:
    """Expose the Site Settings -> Brand block site-wide (member hub, public, guest surfaces).

    Shaped like ``feature_flags``: one ``SiteConfiguration.load()`` per request, no caching,
    because that is what every config-backed processor in this module already does.
    ``brand_short_name`` / ``brand_legal_name`` fall back to the full name so a template never
    has to write the fallback, and ``brand_logo_url`` is empty when nothing is uploaded so the
    templates can pick the static mark with ``{% firstof %}``.
    """
    from core.models import SiteConfiguration

    config = SiteConfiguration.load()
    website = config.org_website_url.rstrip("/")
    return {
        "brand_name": config.org_name,
        "brand_short_name": config.org_short_name or config.org_name,
        "brand_legal_name": config.org_legal_name or config.org_name,
        "brand_logo_url": config.org_logo.url if config.org_logo else "",
        "brand_primary_color": config.org_primary_color,
        "brand_support_email": config.org_support_email,
        "brand_website_url": website,
        "brand_website_display": urlsplit(website).netloc or website,
    }
```

Register it in `plfog/settings.py`, on the line after `"core.context_processors.feature_flags",`:

```python
                "core.context_processors.brand",
```

**On the extra query.** This adds a fourth `SiteConfiguration.load()` per request. That is the
house pattern, not an oversight: `registration_mode`, `feature_flags`, and `google_analytics` each
do the same. **Do not invent a cache for this processor alone** — a per-request cache that only
one of four callers uses saves nothing. Folding all four onto one request-cached load is a real
cleanup, but it is a separate PR and it belongs with PLAT-7.

**`brand_logo_url` is a plain string, not a `FieldFile`,** so no template ever risks calling
`.url` on an empty file. `brand_website_display` exists for the sidebar globe icon's tooltip,
which today reads `pastlives.space` (host only, no scheme).

---

## 6. The four templates: every occurrence

48 hardcoded brand occurrences, of which **45 are replaced** and **3 stay**.

> The epic said 42. That number is the count of the literal substrings `Past Lives` / `pastlives`
> (9 + 12 + 11 + 10). It includes three matches inside comments that render nothing
> (`templates/hub/base.html:19`, `:29` in a JS comment; `templates/guilds/base_public.html:4`
> inside a `{% comment %}`) and excludes the five `img/favicon.png` logo paths and the one
> hardcoded brand hex. 42 minus 3 comments plus 6 non-literal occurrences is 45 replaced, 48
> counting the three that stay.

### 6.1 `templates/hub/base.html` (12 occurrences, 11 replaced)

| Line | Current | Replace with |
|---|---|---|
| 7 | `<meta name="theme-color" content="#092E4C">` | `content="{{ brand_primary_color }}"` |
| 8 | `<title>{% block title %}Member View{% endblock %} - Past Lives</title>` | `... - {{ brand_short_name }}</title>` |
| 9 | `<link rel="icon" type="image/png" href="{% static 'img/favicon.png' %}">` | `href="{% firstof brand_logo_url fallback_logo %}"` |
| 80 | `<img src="{% static 'img/favicon.png' %}" alt="Past Lives" ...>` | `src="{% firstof brand_logo_url fallback_logo %}" alt="{{ brand_short_name }}"` |
| 401 | `href="https://pastlives.space"` | `href="{{ brand_website_url }}"` |
| 401 | `title="pastlives.space"` | `title="{{ brand_website_display }}"` |
| 401 | `aria-label="Past Lives main site"` | `aria-label="{{ brand_short_name }} main site"` |
| 408 | `href="https://discord.com/channels/933589656996565023"` | **STAYS.** Not one of the seven fields. `SiteConfiguration.discord_server_id` exists but is not verified populated on production, so deriving the link here could dark the sidebar icon. PLAT-8. |
| 408 | `aria-label="Past Lives Discord"` | `aria-label="{{ brand_short_name }} Discord"` |
| 572 | `<img src="{% static 'img/favicon.png' %}" alt="" ...>` | `src="{% firstof brand_logo_url fallback_logo %}"` |
| 573 | `<span>Past Lives Member Portal</span>` | `<span>{{ brand_short_name }} Member Portal</span>` |

**The `fallback_logo` variable.** `{% firstof %}` cannot call `{% static %}`, so declare it once
per template scope with the `as` form. In `hub/base.html`, put it immediately after
`{% load static hub_tags %}` on line 1:

```html
{% static 'img/favicon.png' as fallback_logo %}
```

`{% block title %}` stays `Member View`; that is product copy, not brand.
Line 82's `<span class="pl-brand__text">Member Portal</span>` stays; "Member Portal" is the
product name, not the organization's.

### 6.2 `templates/classes/base_public.html` (13 occurrences, all replaced)

Declare `{% static 'img/favicon.png' as fallback_logo %}` inside `{% block public_topbar %}`
(the `as` assignment is block-scoped, and this template's logo use is inside that block).

| Line | Current | Replace with |
|---|---|---|
| 18 | `<img src="{% static 'img/favicon.png' %}" ...>` | `src="{% firstof brand_logo_url fallback_logo %}"` |
| 19 | `<span class="cp-topbar__wordmark">Past Lives</span>` | `>{{ brand_short_name }}<` |
| 22, 83 | `href="https://pastlives.space"` | `href="{{ brand_website_url }}"` |
| 23, 84 | `href="https://pastlives.space/guilds"` | `href="{{ brand_website_url }}/guilds"` |
| 24, 59, 85, 98 | `href="https://pastlives.space/membership"` | `href="{{ brand_website_url }}/membership"` |
| 26, 87 | `href="https://pastlives.space/contact"` | `href="{{ brand_website_url }}/contact"` |
| 108 | `content="{% block meta_description %}Past Lives Makerspace — Portland's community workshop for creators, builders, and makers. Browse classes and workshops and register online.{% endblock %}"` | `{{ brand_name }} — Portland's community workshop ...` (name only) |

### 6.3 `templates/guilds/base_public.html` (11 occurrences, all replaced)

Same `fallback_logo` declaration inside `{% block public_topbar %}`. Lines 27 and 88 already use
`{{ BOOK_BASE_URL }}`; leave them.

| Line | Current | Replace with |
|---|---|---|
| 20 | `<img src="{% static 'img/favicon.png' %}" ...>` | `src="{% firstof brand_logo_url fallback_logo %}"` |
| 21 | `<span class="cp-topbar__wordmark">Past Lives</span>` | `>{{ brand_short_name }}<` |
| 24, 85 | `href="https://pastlives.space"` | `href="{{ brand_website_url }}"` |
| 26, 61, 87, 100 | `href="https://pastlives.space/membership"` | `href="{{ brand_website_url }}/membership"` |
| 28, 89 | `href="https://pastlives.space/contact"` | `href="{{ brand_website_url }}/contact"` |
| 108 | `...Browse every guild at Past Lives Makerspace in Portland, OR — ...` | `...Browse every guild at {{ brand_name }} in Portland, OR — ...` |

### 6.4 `templates/core/privacy_policy.html` (12 occurrences, 10 replaced)

| Line | Current | Replace with |
|---|---|---|
| 4 | `{% block title %}Privacy Policy — Past Lives Makerspace{% endblock %}` | `Privacy Policy — {{ brand_name }}` |
| 16 | `This policy explains what information Past Lives Makerspace collects through its` | `... {{ brand_name }} collects ...` |
| 19 | `<a href="mailto:info@pastlives.space">info@pastlives.space</a>` | `<a href="mailto:{{ brand_support_email }}">{{ brand_support_email }}</a>` (2 occurrences) |
| 24 | `This app is the member hub for Past Lives Makerspace, a community workshop in` | `... for {{ brand_name }}, a community workshop in` |
| 25 | `Portland, Oregon.` | **STAYS.** No location field in the seven. PLAT-8. |
| 68 | `<a href="mailto:info@pastlives.space">info@pastlives.space</a>` | as line 19 (2 occurrences) |
| 99 | `Past Lives Makerspace, Portland, Oregon<br>` | `{{ brand_name }}, Portland, Oregon<br>` — the name is swapped, `Portland, Oregon` **STAYS** |
| 100 | `<a href="mailto:info@pastlives.space">info@pastlives.space</a>` | as line 19 (2 occurrences) |

**Do not** use `brand_legal_name` in the privacy policy. It reads "Past Lives Makerspace" today,
not "Past Lives Makerspace LLC", and swapping it would be a visible change on the live instance.
`org_legal_name` has **no consumer in PLAT-1**; it is stored for PLAT-8 (receipts, invoices, legal
footers). That is deliberate, not an oversight.

**Residual descriptive copy.** Three strings keep Past-Lives-specific prose after the name is
swapped: the two meta descriptions ("Portland's community workshop for creators, builders, and
makers", "in Portland, OR") and the privacy policy's "a community workshop in Portland, Oregon".
These need an `org_tagline` and an `org_location`, which are not among the seven fields this story
adds. They are PLAT-8's. Say so in the PR description rather than quietly inventing fields.

---

## 7. Site Settings: a Brand tab

A new tab, placed **first** in the strip, before General. Reasons: the brand block is the first
thing a new deployment configures; the General tab's field loop is a single 2,000 character
`{% if %}` blacklist (`templates/hub/admin/site_settings.html:159`) and seven more clauses in it
would be needed either way, so a tab costs nothing extra; and PLAT-7 and PLAT-8 will add more
brand fields to the same place.

All edits are in `templates/hub/admin/site_settings.html`.

### 7.1 Tab button

Insert as the **first** child of the tab strip `<div>` that opens at line 95, before the General
button, copying that button's markup exactly:

```html
        <button type="button"
                @click="tab = 'brand'"
                :class="{ 'vote-tab--active': tab === 'brand' }"
                class="vote-tab">
          Brand
        </button>
```

### 7.2 The form tag

Line 152 becomes:

```html
  <form method="post" id="site-settings-form" enctype="multipart/form-data" class="site-settings-form" style="display:flex;flex-direction:column;gap:1.25rem;max-width:760px;">
```

**Keep `enctype` after `id`.** `tests/hub/admin_views_spec.py` locates this form with
`html.index('<form method="post" id="site-settings-form"')`; inserting `enctype` before `id`
breaks that regression test.

### 7.3 The panel

Insert immediately after `<input type="hidden" name="submitted_tab" :value="tab">` (line 154),
before the General panel. Layout goes in a CSS class, not inline, per FRONTEND.md Rule 12 and the
`pl-slideshow-tab-a` precedent in this same file.

```html
    {# -------- Brand tab -------- #}
    <div x-show="tab === 'brand'" x-cloak class="pl-brand-tab">
      <div>
        <h2 style="margin:0 0 0.25rem;font-size:1rem;color:var(--hub-text);">Brand</h2>
        <p style="margin:0;color:var(--hub-text-muted);font-size:0.875rem;">
          Your organization's name, logo, and color. These appear across the member hub, the public
          class and guild pages, and the privacy policy.
        </p>
      </div>

      {% include "components/form_field.html" with field=form.org_name %}
      {% include "components/form_field.html" with field=form.org_short_name %}
      {% include "components/form_field.html" with field=form.org_legal_name %}
      {% include "components/image_field.html" with field=form.org_logo current_image=config.org_logo modal_id="delete-org-logo" label="Logo" shape="rect" thumbnail_width="120px" thumbnail_height="120px" max_bytes=max_upload_image_bytes tooltip_html="Square, about 512x512. PNG (transparency is kept) or JPG." hint_html="Optional. Shown in the sidebar, the public topbar, and the browser tab. Leave blank to use the built in mark." %}
      {% include "components/form_field.html" with field=form.org_primary_color %}
      {% include "components/form_field.html" with field=form.org_support_email %}
      {% include "components/form_field.html" with field=form.org_website_url %}
    </div>
```

The `<h2>`/`<p>` inline styles match the Calendar (line 181), Features (line 427), Automations, and
Slideshow section headings in this file verbatim. Do not invent a different heading style here.

### 7.4 The logo's delete modal must be a SIBLING of the form

`components/image_field.html` renders its own `confirm_modal.html` only when `delete_url`,
`modal_id`, **and** `current_image` are all set. That modal's markup contains a `<form>` inside an
`<template x-teleport="body">`, and
`tests/hub/admin_views_spec.py::it_keeps_the_save_button_inside_the_settings_form` scans the raw
HTML between `<form ... id="site-settings-form"` and the first `</form>` and asserts no nested
`<form>` appears. A teleported modal inside the settings form fails that test twice over.

So: pass `modal_id` but **not** `delete_url` (done above), which renders the Delete button without
the modal, and add the modal as a sibling **after** `</form>` (line 723), next to the existing
`run-bill_tabs` modal that uses this exact idiom:

```html
  {% if config.org_logo %}
  {% url 'hub_admin_brand_logo_delete' as brand_logo_delete_url %}
  {% include "components/confirm_modal.html" with confirm_id="delete-org-logo" confirm_title="Delete logo?" confirm_message="Your logo will be removed from our storage and the built in mark will show instead. This can't be undone." confirm_action_url=brand_logo_delete_url confirm_button_text="Delete logo" %}
  {% endif %}
```

### 7.5 CSS

`static/css/hub.css`, beside the `.pl-slideshow-tab-a` block at line 5107:

```css
/* Brand admin tab (Site Settings). Layout lives in a class, never inline on an
   x-show element (Rule 12). */
.pl-brand-tab {
    display: flex;
    flex-direction: column;
    gap: 1.5rem;
}
```

---

## 8. Form, view, and URL changes

### `hub/forms.py`

`SiteSettingsForm.Meta.fields` — add the seven names at the **top** of the list (they are the
first tab):

```python
            "org_name",
            "org_short_name",
            "org_legal_name",
            "org_logo",
            "org_primary_color",
            "org_support_email",
            "org_website_url",
```

`SiteSettingsForm.Meta.widgets` — one addition, matching `classes_calendar_color`:

```python
            "org_primary_color": forms.TextInput(attrs={"type": "color"}),
```

That widget is what routes the field through `components/color_picker.html` inside
`form_field.html`.

### `templates/hub/admin/site_settings.html:159`

Append seven clauses to the General tab's exclusion `{% if %}` so the brand fields render once, on
the Brand tab, and not again in the General auto-loop:

```
and field.name != 'org_name' and field.name != 'org_short_name' and field.name != 'org_legal_name' and field.name != 'org_logo' and field.name != 'org_primary_color' and field.name != 'org_support_email' and field.name != 'org_website_url'
```

### `hub/views.py`

1. `_save_site_settings` (line 7047) — bind uploads. This is required or the logo silently never
   saves:
   ```python
       form = SiteSettingsForm(request.POST, request.FILES, instance=config)
   ```
   The two unbound `SiteSettingsForm(instance=config)` constructions elsewhere in
   `admin_site_settings` are correct as they are.

2. `admin_site_settings` (line 7111) — add `"brand"` to the `allowed_tabs` set, add
   `"max_upload_image_bytes": settings.MAX_UPLOAD_IMAGE_BYTES,` to the render context (the pattern
   is `hub/views.py:2734`), and extend the docstring's tab list with `brand`.

3. New view, next to the other Site Settings sub-views, mirroring `guild_banner_delete`
   (`hub/views.py:2966`):
   ```python
   @fog_admin_required
   @require_POST
   def admin_brand_logo_delete(request: HttpRequest) -> HttpResponse:
       """Clear the uploaded brand logo; the built in mark takes over everywhere."""
       from core.models import SiteConfiguration

       config = SiteConfiguration.load()
       if config.org_logo:
           config.org_logo.delete(save=True)
           messages.success(request, "Logo removed.")
       return redirect(f"{reverse('hub_admin_site_settings')}?tab=brand")
   ```

### `hub/urls.py`

Beside the slideshow save routes (after line 596):

```python
    path(
        "manage/site-settings/brand/logo/delete/",
        views.admin_brand_logo_delete,
        name="hub_admin_brand_logo_delete",
    ),
```

### `core/admin.py`

Add a `Brand` fieldset to `SiteConfigurationAdmin.fieldsets`, first, so the Django admin does not
silently hide a now-required field:

```python
        (
            "Brand",
            {
                "fields": [
                    "org_name",
                    "org_short_name",
                    "org_legal_name",
                    "org_logo",
                    "org_primary_color",
                    "org_support_email",
                    "org_website_url",
                ],
                "description": "Your organization's identity. These strings and this logo appear across the member hub, the public pages, and the privacy policy.",
            },
        ),
```

---

## 9. FRONTEND.md rules that bear on this change

Read `FRONTEND.md` before touching a template. These are the rules this change can violate:

| Rule | Bearing |
|---|---|
| **1** — always use `components/form_field.html` | Every brand text field goes through it. Never render `{{ form.org_name }}` with hand-rolled label markup. |
| **9** — no inline styles | The Brand panel's layout goes in `.pl-brand-tab`. The `<h2>`/`<p>` inline styles are copied verbatim from the four existing section headings in this file; do not add new inline styling beyond that. |
| **12** — never put `display` in an inline `style` on an `x-show` element | The Brand panel is an `x-show` element, so its `display:flex` goes in the CSS class. The older panels in this file get this wrong; the slideshow panels were fixed. Follow the fixed ones. |
| **13** — never inline-style a form control, and `--surface` is not a real token | The brand inputs inherit `.site-settings-form input[type=...]` styling already defined at the top of the template. Add no `background`/`color` of your own, and never reach for `var(--surface, #fff)`. |
| **16** — image uploads use the draggable component, never a bare file input, and always with a recommended-size tooltip | `components/image_field.html` with `tooltip_html`. There is no client-side row cloning here, so the component's own inline script is fine (the delegated-script caveat does not apply). |
| **17** — `{# ... #}` is single line only | The one-line `{# -------- Brand tab -------- #}` marker is fine. Any longer note uses `{% comment %}`. Run `tests/template_comment_lint_spec.py`. |
| **18** — buttons never touch an adjacent section | The shared Save sits below every panel already, with `margin-bottom:1.5rem`. Nothing to do, but check the rendered page. |
| **19** — a "?" tooltip is `.pl-help`, never `title=` | `image_field.html`'s `tooltip_html` renders a real `.pl-help` bubble. The sidebar globe's `title="{{ brand_website_display }}"` is an existing plain link tooltip, not a help bubble; leave it a `title`. |
| **21** — Save is last and just says "Save" | Do not add a Brand-specific save button. The shared "Save settings" button at line 721 already covers the tab. |
| **22** — section headings use Title Case | The heading is `Brand`. One word, already correct. |

**Does `org_primary_color` feed a CSS custom property?** **No, not in PLAT-1.** It sets
`<meta name="theme-color">` in `templates/hub/base.html:7` and nothing else. Wiring it into
`--color-navy` / `--color-tuscan-yellow` means touching 154 tokens and 776 hardcoded hexes, which
is PLAT-5 and PLAT-7. Do not attempt it here.

---

## 10. Tests

BDD style, `describe_*` / `it_*`, factory-boy for data. **`context_*` blocks are not collected**
(`pyproject.toml` `python_functions = ["it_*", "test_*", "describe_*"]`), so every nested block is
`describe_*`. Branch coverage gate is `fail_under = 98`; mutation testing runs in CI
(`pytest --leela --target core --target hub ...`).

### 10.1 New: `core/spec/models/site_configuration_brand_spec.py`

```
describe_SiteConfiguration_brand_defaults
    it_defaults_to_the_past_lives_identity          # all seven defaults, asserted literally
    it_starts_with_no_uploaded_logo                 # bool(config.org_logo) is False
    it_survives_load_on_a_fresh_database            # load() creates the row with the defaults

describe_org_primary_color_validation
    it_accepts_a_six_digit_hex                      # full_clean() passes for #ABCDEF and #abcdef
    it_accepts_a_blank_value                        # blank=True, validators skipped on empty
    it_rejects_a_three_digit_shorthand              # #FFF raises ValidationError
    it_rejects_a_value_without_a_hash               # 092E4C raises
    it_rejects_a_non_hex_character                  # #GGGGGG raises

describe_org_logo_replacement
    it_deletes_the_previous_file_when_a_new_one_is_saved   # delete_orphan_on_replace wired in save()
    it_leaves_storage_alone_when_the_logo_is_unchanged
```

The two replacement tests assert against `default_storage.exists(old_name)`. `conftest.py`
`pytest_sessionstart` forces `STORAGES["default"]` to `FileSystemStorage`, so uploads land under
`MEDIA_ROOT` and are inspectable. Build uploads with
`SimpleUploadedFile("logo.png", <1x1 png bytes>, content_type="image/png")`.

### 10.2 Extend: `tests/core/context_processors_spec.py`

Add a `describe_brand()` block beside the existing `describe_feature_flags()`. Import `brand`.

```
describe_brand
    it_returns_the_past_lives_defaults              # all eight keys, exact values
    it_reflects_an_edited_org_name
    it_falls_back_to_the_full_name_for_a_blank_short_name
    it_falls_back_to_the_full_name_for_a_blank_legal_name
    it_returns_an_empty_logo_url_when_nothing_is_uploaded
    it_returns_the_stored_logo_url_when_one_is_uploaded
    it_strips_a_trailing_slash_from_the_website_url
    it_reduces_the_website_url_to_its_host_for_display   # https://x.org/y -> x.org
    it_falls_back_to_the_raw_value_when_the_website_has_no_host   # blank url -> ""
```

The last two pin both branches of `urlsplit(website).netloc or website`, which mutation testing
will otherwise flag.

### 10.3 New: `tests/hub/site_settings_brand_spec.py`

Model the file on `tests/hub/site_settings_discord_events_spec.py` (a `_superuser(client)` helper
plus a `_settings_post(**overrides)` payload builder).

```
describe_brand_fields_on_the_form
    it_declares_all_seven_brand_fields              # in SiteSettingsForm.Meta.fields
    it_renders_the_color_field_through_the_color_picker   # 'pl-color-picker' in the GET body
    it_requires_the_organization_name               # form invalid with org_name=""
    it_allows_every_other_brand_field_to_be_blank

describe_brand_tab_render
    it_shows_the_brand_tab_button
    it_renders_each_brand_input_exactly_once        # count('name="org_name"') == 1, etc.
    it_makes_the_settings_form_multipart            # enctype="multipart/form-data" present
    it_renders_the_logo_upload_zone                 # 'cls-image-upload-zone' present (Rule 16)
    it_keeps_the_save_button_inside_the_settings_form   # mirrors admin_views_spec; guards §7.4

describe_brand_save
    it_round_trips_every_brand_field_onto_the_singleton
    it_redirects_back_to_the_brand_tab              # ?tab=brand after submitted_tab=brand
    it_rejects_a_malformed_primary_color            # 200 re-render, not 302
    it_saves_an_uploaded_logo                       # POST with a SimpleUploadedFile; config.org_logo truthy

describe_brand_logo_delete
    it_clears_the_logo_and_redirects_to_the_brand_tab
    it_is_a_no_op_when_no_logo_is_set
    it_rejects_a_get
    it_forbids_a_plain_member
```

`it_saves_an_uploaded_logo` is the test that fails if `request.FILES` is not bound in
`_save_site_settings`. Do not skip it.

### 10.4 New: `tests/hub/brand_templates_spec.py`

The acceptance test. Set `config.org_name = "Fletcher Test Space"`, `org_short_name = "Fletcher"`,
`org_support_email = "help@fletcher.test"`, `org_website_url = "https://fletcher.test"`, save, then
render each surface and assert the new values appear and no `Past Lives` / `pastlives.space`
substring survives.

```
describe_hub_base
    it_renders_the_configured_short_name_in_the_title_and_sidebar
    it_renders_the_configured_website_on_the_sidebar_globe
    it_renders_the_configured_primary_color_as_the_theme_color
    it_leaves_no_past_lives_string_in_the_chrome

describe_classes_public_base
    it_renders_the_configured_wordmark_and_nav_links
    it_renders_the_configured_name_in_the_meta_description

describe_guilds_public_base
    it_renders_the_configured_wordmark_and_nav_links
    it_renders_the_configured_name_in_the_meta_description

describe_privacy_policy
    it_renders_the_configured_name_and_support_email
    it_leaves_no_past_lives_string_on_the_page

describe_logo_fallback
    it_uses_the_static_mark_when_no_logo_is_uploaded    # 'img/favicon' in the body
    it_uses_the_uploaded_logo_when_one_is_set           # the stored url in the body
```

Surface recipe, copied from `tests/hub/guild_detail_head_spec.py:17` to `:36`: wrap the guilds
tests in `override_settings(ALLOWED_HOSTS=[...], GUILDS_HOSTS=["guilds.pastlives.app"], ...)` and
pass `HTTP_HOST="guilds.pastlives.app"`. The classes public surface uses `PUBLIC_HOSTS`
(`core/middleware.py:62`). The privacy policy and the hub base render on the default host.

The "no `Past Lives` string" assertions must exclude the three intentional survivors: the Discord
channel URL in `hub/base.html:408` carries no brand literal, so it is safe; `Portland, Oregon` in
the privacy policy carries no brand literal either. Asserting `b"Past Lives" not in body` and
`b"pastlives.space" not in body` is therefore clean for all four surfaces.

### 10.5 Existing tests that break and must be edited

`org_name` is required, so every dict POSTed to `hub_admin_site_settings` (and every direct
`SiteSettingsForm(data, ...)` construction) needs `"org_name": "Past Lives Makerspace",`. A missed
one fails loudly as a 200 where a 302 was asserted.

| File | What to add |
|---|---|
| `tests/hub/calendar_config_forms_spec.py:30` | `_settings_payload` base dict |
| `tests/hub/site_settings_automations_spec.py:52` | `_settings_post` base dict |
| `tests/hub/site_settings_discord_events_spec.py:26` | `_settings_post` base dict |
| `tests/hub/site_settings_discord_info_spec.py:33` | `_settings_post` base dict |
| `tests/hub/site_settings_discord_spec.py:28` | `_settings_post` base dict |
| `tests/hub/site_settings_form_spec.py` | the inline dict in `it_round_trips_a_save_onto_the_singleton` |
| `tests/hub/admin_views_spec.py` | roughly eight inline dicts inside `describe_admin_site_settings` (around lines 694, 750, 778, 807, 934, 964, 990). The two deliberately-invalid payloads (`registration_mode = "not-a-real-mode"`) already expect a 200 and need no change. |

Find them all with:

```
grep -rn 'hub_admin_site_settings' tests/ | grep -i post
grep -rn 'SiteSettingsForm(' tests/ core/spec
```

`tests/core/admin_spec.py:113` posts to the **Django admin** form (`fields = "__all__"`), which
takes `org_name` from the instance, so it needs no change; confirm by running it.

Nothing else in the suite pins a brand string on the four templates. `tests/core/home_spec.py`
asserts `b"Past Lives Member Portal"`, but it renders `templates/home.html` on
`templates/base.html`, neither of which is in scope, so it passes untouched.

---

## 11. VERSION and CHANGELOG

Bump `VERSION` in `plfog/version.py` from `1.44.2` to `1.44.3`.

**No `CHANGELOG` entry.** This change is invisible to members by construction: every default
reproduces the string that was hardcoded, so nothing on any page moves. The changelog is
member-facing release notes, and an entry describing an internal configuration refactor is noise.

The Discord announce workflow posts only entries whose `version` equals the current `VERSION`.
Bumping to `1.44.3` with no `1.44.3` entry means the announce run posts nothing, which is the
correct outcome here. Do **not** re-stamp the existing `1.44.0` entry onto `1.44.3` to make the
workflow resolve; that would re-announce a shipped feature.

---

## 12. Verification before opening the PR

Run the canonical local stack from the primary checkout (not a worktree):

```
docker compose up -d
```

Then browse `http://pastlives.test:8000` and sign in as a member with an admin role (login codes
land in mailpit at `http://localhost:8025`).

1. `/manage/site-settings/` opens on **Brand**. Seven controls render: three text inputs, a
   drag-and-drop logo zone with a `?` tooltip, a color swatch plus hex box, an email input, a URL
   input. Check both themes with the topbar toggle.
2. Change **Organization name** to `Fletcher Test Space` and Save. Confirm the success toast and
   that the page returns to `?tab=brand`.
3. Reload the hub. The browser tab title ends in the short name, the sidebar `alt` and the public
   topbar read the new name. Visit `/privacy/` and confirm the new name and support email.
4. Change **Short name** to `Fletcher`. Confirm the sidebar wordmark and title suffix follow.
5. Change **Public website** to `https://example.test`. Confirm the sidebar globe link and its
   tooltip (`example.test`, host only), and the public topbar's Home / Guilds / Membership /
   Contact links.
6. Upload a transparent PNG logo. Confirm the preview appears before save, the sidebar mark and
   the browser favicon change after save, and **transparency is preserved** (this is what proves
   normalization was correctly left off).
7. Click Delete on the logo, confirm the modal, and confirm the built in mark returns.
8. Set **Primary brand color** to something obvious and confirm `<meta name="theme-color">` in view
   source.
9. Restore every field to its Past Lives default and diff the rendered `<head>` of `/` against the
   pre-change output. It must be identical.

Then:

```
pytest tests/hub/site_settings_brand_spec.py tests/hub/brand_templates_spec.py tests/core/context_processors_spec.py core/spec/models/site_configuration_brand_spec.py
pytest tests/hub/admin_views_spec.py tests/hub/site_settings_form_spec.py tests/hub/site_settings_discord_spec.py tests/hub/site_settings_discord_info_spec.py tests/hub/site_settings_discord_events_spec.py tests/hub/site_settings_automations_spec.py tests/hub/calendar_config_forms_spec.py tests/core/admin_spec.py
pytest tests/template_comment_lint_spec.py
python manage.py check
python scripts/check_no_inline_style_in_extra_head.py
ruff format .
ruff check .
mypy plfog/ core/ membership/ hub/
```

Capture pytest's own exit code; do not read it through a pipe to `tail`.

---

## 13. Risks and gotchas

1. **`request.FILES` is not bound today.** `hub/views.py:7060` builds
   `SiteSettingsForm(request.POST, instance=config)`. Without adding `request.FILES` the logo
   upload posts, validates, and silently saves nothing. `it_saves_an_uploaded_logo` is the guard.
2. **`enctype` and the nested-form regression test.** Both `admin_views_spec.py`'s form-index
   lookup (§7.2) and its no-nested-`<form>` assertion (§7.4) are load-bearing. Put `enctype` after
   `id`, and put the logo's confirm modal outside the form.
3. **Required `org_name` breaks unrelated settings specs.** Seven files, roughly fourteen payload
   dicts. Enumerated in §10.5. Every miss is a loud 200-instead-of-302, not a silent pass.
4. **A save from any tab writes every brand field.** All panels live in one `<form>`, so a Discord
   tab save posts `org_name` too. That is what makes the existing "one tab guarantee" test in
   `tests/hub/calendar_config_forms_spec.py` pass, and it is also the hazard: if any future work
   moves a panel out of `#site-settings-form`, saving from it would blank the brand block. Note it
   in the PR.
5. **`color_picker.html` defaults to `#EEB44B` when the field value is falsy.** An admin who blanks
   the color and saves gets gold, not blank. Acceptable, and out of scope to fix, but do not be
   surprised by it in manual testing.
6. **Do not normalize the logo.** Running `normalize_field_if_uploaded` on it flattens transparency
   onto white and re-encodes as JPEG. On the dark theme that is a white box behind the mark. See
   §3, decision 3.
7. **The Discord invite URL in `hub/base.html:408` stays hardcoded.** `discord_server_id` exists on
   `SiteConfiguration` but is not verified populated on production; deriving the link from it could
   dark the sidebar icon for every member. PLAT-8, after checking the production value.
8. **Fourth `SiteConfiguration.load()` per request.** Accepted, and consistent with the three that
   already exist. Do not fix it in this PR.
9. **R2 needs no new configuration.** `STORAGES["default"]` is global; `upload_to="brand/logo/"` is
   the only thing the field needs. Production already has the `R2_*` env vars; local dev and CI
   fall back to `FileSystemStorage` automatically.

---

## 14. Out of scope

**PLAT-7 (per-tenant theming)** owns everything that turns `org_primary_color` into actual style:
mapping it onto `--color-navy` / `--color-tuscan-yellow` / `--hub-blue`, deriving a full palette,
and per-tenant CSS delivery. PLAT-1 stores the value and feeds `<meta name="theme-color">`. Nothing
else.

**PLAT-8 (de-brand the remaining surface)** owns everything outside the four templates. Do not pull
any of it into this PR.

### Appendix: brand strings found elsewhere, for PLAT-8

A repo-wide count of the literal substrings `Past Lives` / `pastlives` across `templates/`,
`core/`, `hub/`, `membership/`, `classes/`, `billing/`, and `static/`, excluding tests, docs,
migrations, and `plfog/version.py`, is **609**. The four templates in this story account for 42 of
them. The heaviest remaining files:

| File | Occurrences | Notes |
|---|---|---|
| `core/events/copy.py` | 155 | Every notification and email subject/body |
| `membership/help_content.py` | 24 | Seeded Help Center articles |
| `membership/models.py` | 16 | Signage deck titles, defaults |
| `classes/models.py` | 13 | Includes `classes/models.py:46`, the receipt footer carrying the legal name and street address (`org_legal_name`'s eventual consumer) |
| `core/models.py` | 11 | `DISCORD_INFO_LINKS_DEFAULT` |
| `core/middleware.py` | 10 | Host constants, correctly configuration rather than copy |
| `core/context_processors.py` | 9 | Docstrings and `BOOK_BASE_URL` / `GUILDS_BASE_URL` fallbacks |

Also flagged specifically, since they sit adjacent to this work:

- `templates/base.html:6,7,11,12,25` — the non-hub base's meta description, `theme-color`,
  `apple-mobile-web-app-title`, `<title>`, and nav brand. The privacy policy extends **this** base,
  so the page is only partly de-branded after PLAT-1.
- `templates/hub/admin/site_settings.html:3` — `{% block title %}Site Settings — Past Lives{% endblock %}`.
- `static/manifest.json` — PWA `name` / `short_name` / `description`, pinned by
  `tests/core/test_manifest_spec.py`.
- `templates/classes/emails/*.html`, `templates/classes/public/detail.html`,
  `templates/classes/class_flyer.html`, `templates/classes/public/my_registration.html` — the
  `2808 SE 9th Ave, Portland, OR 97202` footer block, which wants an `org_address`.
- Two fields the residual copy in §6 will need: **`org_tagline`** ("Portland's community workshop
  for creators, builders, and makers") and **`org_location`** ("Portland, Oregon" / "Portland, OR").
