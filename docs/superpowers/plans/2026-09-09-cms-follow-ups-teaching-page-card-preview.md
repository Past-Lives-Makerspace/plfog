# Class CMS follow ups: Teaching Marketing Page, the whole card in the preview, imported photos, and an unclipped tooltip

**Date:** 2026-09-09
**Ships as:** one PR off `main` (v1.46.0, commit `705a6951`), version `1.47.0`.
**Predecessors:** `2026-09-09-class-cms-composer-and-instructor-marketing.md` (design), `2026-09-09-host-a-workshop-page.md` (the admin editable copy). This spec supersedes those two wherever they disagree.

Jo's asks, verbatim in spirit:

1. Give the Host a Workshop copy its own settings card, named **Teaching Marketing Page**. No Markdown: use the rich text editor the codebase already has. On the Host a Workshop page, make the Hosting Guide disclosure obviously clickable, decorate What We Ask Of You and Common Questions properly, and add a section on the money split (70 / 20 / 10, instructor / Past Lives / guild).
2. Fix the status tooltip that gets clipped on `/classes/admin/classes/`.
3. The "catalog card" preview in the composer shows only the photo strip. It must be the whole card (photo, title, instructor, dates, price, spots), so nobody mistakes it for a photo cropper. And classes such as Glen's Blacksmithing 101 say "no hero image" in the Photos step when the class plainly has one. That is a bug.

Everything below is decided. The builder implements; questions go to the orchestrator, not to Jo.

---

## 0. Root causes found in research

**The "no hero image" bug.** `ClassOffering` has two photo columns: `image` (an upload in our storage) and `legacy_image_url` (a Drupal URL from classes.pastlives.space, set by the nightly `sync_legacy_cms` import and served through the `classes:legacy_image` proxy). The public catalog card, the public detail page and the flyer all fall back to the legacy URL. The composer, the card focus tool, the readiness checklist and `has_submittable_image` only look at `image`. On production today 23 offerings (22 published, including Glen's pk 627) carry only a legacy URL. `manage.py download_legacy_images` is the designed migration step that copies those pictures into our storage and clears the URL; it is idempotent and has not been run since the last import batch. So: the code must treat a legacy photo as the class's hero everywhere the composer looks (this recurs after every nightly import), and the orchestrator runs `download_legacy_images` against production once the PR is live (section G).

**The clipped tooltip.** `.pl-help__bubble` is `position: absolute` inside `.admin-table-wrap`, which is `overflow: hidden` (for its rounded corners). Anything inside a clipping or scrolling ancestor cuts the bubble. The same failure waits inside every `overflow-x: auto` table wrapper in the hub.

**The editor.** `core.widgets.PageContentEditorWidget` (Quill, `data-rte-seed="server"`) already does exactly what is needed: it renders a stored value that is either Markdown or saved HTML into the editor via `membership.markdown.render_page_content`, and the form's `clean_*` stores Quill HTML through `sanitize_page_submission`. `render_page_content(value, profile=...)` is the dual mode renderer (sniffs `<` at the start). Reuse all of it; write no new sanitizer.

---

## A. Teaching Marketing Page: its own settings page

### A.1 Navigation

- `templates/classes/admin/settings_hub.html`: split the last tile into two tiles.
  - **Waivers & Reminders**: "Liability text, photo release text, and reminder timing." (links to `classes:admin_settings`, unchanged route).
  - **Teaching Marketing Page**: "The page a member sees under Host a Workshop until an admin says yes. Every word of it, plus the money split." (links to the new route).
- `templates/classes/admin/base.html`: add `teaching_page` to the `settings_tabs` list so the Settings tab stays active on the new page.
- New route in `classes/urls.py`: `admin/settings/teaching-page/` → `views.admin_teaching_page_settings`, name `admin_teaching_page_settings`. Same decorator as `admin_settings` (`@classes_admin_access_required` + `@admin_required`, in that order; copy the stack exactly).

### A.2 The page (`templates/classes/admin/teaching_page_settings.html`)

Extends `classes/admin/base.html`, `active_tab = "teaching_page"`. Include `_components/rich_editor_assets.html` once at the top of `tab_content` (body level is fine under hx-boost head support; the announcement composer does the same).

Header row: `<h2>Teaching Marketing Page</h2>` with the existing `.pl-help` bubble ("This is the page a member sees under Host a Workshop in the sidebar until an admin says yes to them.") and a **View the Page** ghost button linking to `classes:teach_why` in a new tab. One intro line under it: "Every field here is one section of the page. Blank a field to hide its section."

One `<form method="post">`, fields grouped under `<h3>` section titles in page order, every field through `components/form_field.html` (the toggle renders itself for the boolean), one **Save** button at the bottom (Rule 21):

1. **Top of the Page**: `teach_page_title`, `teach_page_lead`.
2. **What You Get**: `teach_page_features` (the line based textarea stays; it drives the icon cards, it is not prose).
3. **How It Works**: `teach_page_how_it_works` (rich text).
4. **Where the Money Goes**: `teach_page_split_enabled` (toggle), then the three percentages in one row (`.pl-teach-split-inputs`, three `form_field.html` includes inside a flex row, each input `inputmode="numeric"`), then `teach_page_split_note`.
5. **What We Ask Of You**: `teach_page_expectations` (rich text).
6. **Common Questions**: `teach_page_faq` (rich text).
7. **Bottom Card**: `teach_page_cta_title`, `teach_page_cta_line`.
8. **Example Workshop**: `example_class`.

`templates/classes/admin/settings.html` loses the whole `.pl-teach-settings` section and its comment; it is the waivers and reminders page again. Delete the `.pl-teach-settings*` CSS if nothing else uses it (grep first).

### A.3 Form (`classes/forms.py`)

- `ClassSettingsForm`: back to `GENERAL_FIELDS` only. Drop `TEACH_PAGE_FIELDS`, `teach_page_fields()`, the teach widgets, the example_class queryset override, and the teach labels. Keep `general_fields()` only if the template still uses it; otherwise iterate the form.
- New `TeachingPageSettingsForm(forms.ModelForm)` on `ClassSettings` with exactly the fields in A.2, in that order. Labels as today plus: `teach_page_split_enabled` "Show the money section", `teach_page_split_instructor_pct` "You (the host)", `teach_page_split_space_pct` "Past Lives", `teach_page_split_guild_pct` "The guild", `teach_page_split_note` "Line under the split".
- Widgets: `PageContentEditorWidget(markdown_profile="member")` for `teach_page_how_it_works`, `teach_page_expectations`, `teach_page_faq` (the `page` toolbar: bold, italic, underline, strike, H2, H3, lists, link, blockquote). Plain textareas for lead / features / cta line as today; `NumberInput(attrs={"min": 0, "max": 100})` for the three percentages.
- `clean_teach_page_how_it_works` / `_expectations` / `_faq`: `return sanitize_page_submission(self.cleaned_data[name] or "")` (Markdown typed into a no JS textarea passes through unchanged and still renders; Quill HTML is sanitized before storage).
- `clean()`: the three percentages must add up to 100 when the section is enabled; otherwise `add_error("teach_page_split_instructor_pct", "The three shares have to add up to 100.")`. When disabled, skip the check (an admin hiding the section should not be blocked by stale numbers).
- `example_class` queryset limited to PUBLISHED, ordered by title (moved from `ClassSettingsForm`).

### A.4 View (`classes/views.py`)

`admin_teaching_page_settings`: `ClassSettings.load()`, bind the form, save, `messages.success(request, "Teaching Marketing Page saved.")`, redirect to itself. Mirror `admin_settings`.

### A.5 Model (`classes/models.py`)

New fields on `ClassSettings`, all with `help_text`:

- `teach_page_split_enabled = BooleanField(default=True)` "Show the Where the Money Goes section on the Host a Workshop page."
- `teach_page_split_instructor_pct = PositiveSmallIntegerField(default=70, validators=[MaxValueValidator(100)])` "The host's share of a paid class, as a percentage."
- `teach_page_split_space_pct = PositiveSmallIntegerField(default=20, ...)` "Past Lives' share, as a percentage."
- `teach_page_split_guild_pct = PositiveSmallIntegerField(default=10, ...)` "The guild's share, as a percentage."
- `teach_page_split_note = TextField(blank=True, default=DEFAULT_TEACH_PAGE_SPLIT_NOTE)` "The line under the split. Plain text. Leave blank to show no line."

`DEFAULT_TEACH_PAGE_SPLIT_NOTE = "Every paid class splits the same way. Run it free and there is nothing to split."`

The three prose defaults become HTML (what Quill would save), same words, no dashes:

```python
DEFAULT_TEACH_PAGE_HOW_IT_WORKS = (
    "<ol>"
    "<li><strong>Say you're interested.</strong> Tell us what you'd like to host. A sentence is plenty. "
    "An admin reads every note.</li>"
    "<li><strong>Build your page.</strong> Once you're in, the editor walks you through it in five short steps. "
    "Save a draft any time and come back.</li>"
    "<li><strong>Open sign ups.</strong> Send it for a quick look. Your guild lead and an admin check it over, "
    "then it goes into the catalog and out to members.</li>"
    "</ol>"
)
DEFAULT_TEACH_PAGE_EXPECTATIONS = (
    "<ul>"
    "<li>Know your material and know the tools you're using.</li>"
    "<li>Show up on time and leave the space the way you found it.</li>"
    "<li>Answer people when they message you through the app.</li>"
    "<li>Tell an admin as early as you can if you need to move or cancel a date.</li>"
    "</ul>"
)
DEFAULT_TEACH_PAGE_FAQ = (
    "<h3>Do I Need to Be an Expert?</h3>"
    "<p>No. You need to be safe and clear. Plenty of great workshops are run by people two steps ahead of "
    "everyone else in the room.</p>"
    "<h3>How Long Until I Hear Back?</h3>"
    "<p>An admin usually gets to it within a week. You can check this page any time to see where things stand.</p>"
    "<h3>Can I Charge for It?</h3>"
    "<p>Yes. You set the price and an optional member discount when you build the page. You can also run it free.</p>"
    "<h3>What If Nobody Signs Up?</h3>"
    "<p>You can cancel from your dashboard and everyone who signed up is told automatically. Nothing is stuck.</p>"
)
```

Keep the old Markdown strings as module constants `LEGACY_TEACH_PAGE_HOW_IT_WORKS_MD` etc. (private to the migration's reverse; put them in the data migration file itself, not the model).

Help text for the three prose fields no longer mentions Markdown: "The How It Works steps, a numbered list. Each step shows a gold number. Use the toolbar for bold, lists and links. Leave blank to hide the section." (and the matching wording for the other two).

Rendering: replace `_teach_page_markdown` with

```python
@staticmethod
def _teach_page_html(source: str) -> SafeString:
    """Render one of the page's prose fields, rich editor HTML or older Markdown, sanitized."""
    from membership.markdown import render_page_content
    return mark_safe(render_page_content(source, profile="member"))
```

and point the three `*_html` properties at it. Both paths sanitize (`sanitize_page_html` for HTML, `render_markdown` for Markdown), so `mark_safe` stays justified.

**FAQ items.** Add a frozen dataclass `FaqItem(question: str, answer_html: SafeString)` next to `FeatureCard`, and

```python
def teach_page_faq_items(self) -> list[FaqItem]:
    """Common Questions split into question and answer pairs for the accordion.

    Splits the SANITIZED HTML on its h2/h3 headings: each heading is a question and
    everything up to the next heading is its answer. Content before the first heading
    is the section's intro (``teach_page_faq_intro_html``). No headings means no
    items, and the template then renders the whole field as one block.
    """
```

Implementation: `re.split(r"<h[23]>(.*?)</h[23]>", html, flags=re.S)`; question is `strip_tags(q).strip()`; answer is `mark_safe(part.strip())`; skip pairs whose question is blank. `teach_page_faq_intro_html` returns `mark_safe(parts[0].strip())`. Both operate on `self.teach_page_faq_html` so nothing unsanitized is ever split.

### A.6 Migrations

- `classes/migrations/0061_...`: the five new fields plus the AlterField for the three prose defaults and help texts (`makemigrations` produces this; `ruff format` it).
- `classes/migrations/0062_convert_teach_page_markdown.py`: data migration.
  - Forward, per prose field: if the stored value equals the old Markdown default, store the new HTML default; else if it does not start with `<` (after `lstrip`), store `render_markdown(value, profile="member")`; else leave it.
  - Reverse: if the stored value equals the new HTML default, restore the old Markdown default; otherwise leave it (the old code's Markdown renderer passes sanitized HTML through, so nothing breaks). That is a real reverse, not `noop`.
  - Import `render_markdown` from `membership.markdown` inside the function (it is a pure function; no model imports).

### A.7 Prod data

The single `ClassSettings` row on production holds the Markdown defaults from v1.46.0; migration 0062 converts it on deploy. No seed command needed.

---

## B. The Host a Workshop page (`templates/classes/teach/why_teach.html`)

Section order becomes: hero → What You Get → See It For Yourself → How It Works → **Where the Money Goes** → What We Ask Of You → Common Questions → Read the Hosting Guide → bottom card. Every section stays guarded by its field (blank hides), the money section by `teach_page.teach_page_split_enabled`.

### B.1 Where the Money Goes (new)

```html
<section class="hub-card pl-teach-split" data-help-key="teach.money-split">
  <h2 class="pl-teach-section__title">Where the Money Goes</h2>
  {% if teach_page.teach_page_split_note %}<p class="pl-teach-split__note">{{ teach_page.teach_page_split_note }}</p>{% endif %}
  <div class="pl-teach-split__bar" role="img" aria-label="70 percent to you, 20 percent to Past Lives, 10 percent to the guild">
    <span class="pl-teach-split__seg pl-teach-split__seg--you" style="flex-basis: 70%">70%</span>
    <span class="pl-teach-split__seg pl-teach-split__seg--space" style="flex-basis: 20%">20%</span>
    <span class="pl-teach-split__seg pl-teach-split__seg--guild" style="flex-basis: 10%">10%</span>
  </div>
  <div class="pl-teach-split__tiles">
    <div class="pl-teach-split__tile"><span class="pl-teach-split__pct">70%</span><span class="pl-teach-split__who">You</span><span class="pl-teach-split__what">Your share of every seat sold.</span></div>
    ... Past Lives: "Keeps the shop open and the tools running."
    ... Your Guild: "Funds the guild whose space and tools you use."
  </div>
</section>
```

The numbers come from the three fields; the `style="flex-basis"` is the one place an inline style is allowed here because the value is data (percentages), the same reason the sale banner carries its width inline. Segment colors: you = `var(--color-tuscan-yellow)` with navy text; Past Lives = `var(--hub-blue)` (or the navy light token used by `.cls-img-ph`) with light text; guild = `var(--hub-accent)` with light text. Each tile carries a 4px top border in its segment color so the bar and the tiles read as one figure. A segment under 8% hides its inline label (`.pl-teach-split__seg--slim`, computed in the template from the value) so "10%" never overflows a narrow segment on a phone. Tiles are a three column grid, one column under 640px.

### B.2 What We Ask Of You

The field is free HTML now, so the decoration is CSS on the structure Quill emits. `.pl-md.pl-teach-asks-md ul` becomes a two column grid (one column under 640px) of `li` cards: `background: var(--hub-surface)`, 1px `var(--hub-border)`, radius 10px, padding `0.85rem 1rem 0.85rem 2.6rem`, and a gold check circle at the left via `::before` (a 1.35rem circle in `var(--color-tuscan-yellow)` holding a `&#10003;` in navy, exactly the numeral treatment on How It Works). `list-style: none`, no bullet. Paragraphs outside a list render as before.

### B.3 Common Questions

Server split accordion. If `faq_items` is non empty:

```html
<section class="hub-card">
  <h2 class="pl-teach-section__title">Common Questions</h2>
  {% if faq_intro_html %}<div class="pl-md pl-teach-faq__intro">{{ faq_intro_html }}</div>{% endif %}
  <div class="pl-teach-faq">
    {% for item in faq_items %}
    <details class="pl-disclosure pl-teach-faq__item"{% if forloop.first %} open{% endif %}>
      <summary class="pl-disclosure__summary">
        <span class="pl-teach-faq__badge" aria-hidden="true">Q</span>
        <span class="pl-disclosure__title">{{ item.question }}</span>
        <span class="pl-disclosure__chevron" aria-hidden="true"></span>
      </summary>
      <div class="pl-disclosure__body pl-md pl-teach-faq__answer">{{ item.answer_html }}</div>
    </details>
    {% endfor %}
  </div>
</section>
```

The first question opens by default so the section never looks empty. If there are no items (an admin wrote the section without headings), render the whole field as today: `<div class="pl-md pl-teach-faq-md">{{ teach_page.teach_page_faq_html }}</div>`. The `Q` badge is a 1.5rem rounded square in `var(--hub-blue-soft)` with `var(--hub-blue)` bold text.

### B.4 Read the Hosting Guide

Same `<details>`, rebuilt on the shared disclosure:

```html
<section class="hub-card pl-teach-guide-card">
  <details class="pl-disclosure pl-teach-guide">
    <summary class="pl-disclosure__summary">
      <span class="pl-teach-guide__icon" aria-hidden="true">{% include "classes/teach/partials/_feature_icon.html" with icon="page" %}</span>
      <span class="pl-disclosure__text">
        <span class="pl-disclosure__title">Read the Hosting Guide</span>
        <span class="pl-disclosure__hint">The full walkthrough, from your first draft to the day of the class. Tap to open.</span>
      </span>
      <span class="pl-disclosure__chevron" aria-hidden="true"></span>
    </summary>
    <div class="pl-disclosure__body"> ...the article body and the full guide link exactly as today... </div>
  </details>
</section>
```

Keep the summary text "Read the Hosting Guide" verbatim (specs and the e2e read it).

### B.5 The shared disclosure component (`static/css/hub.css`, one block, documented in FRONTEND.md)

`.pl-disclosure` is the hub's `<details>` pattern from now on:

- `summary` is `display: flex; align-items: center; gap: 0.75rem; cursor: pointer; list-style: none; padding: 0.85rem 1rem; border: 1px solid var(--hub-border); border-radius: 10px; background: var(--hub-surface);` with `::-webkit-details-marker { display: none }` and `::marker { content: "" }`. Hover and focus visible: border color `var(--color-tuscan-yellow)`.
- `.pl-disclosure__title` 600 weight, full text color; `.pl-disclosure__hint` muted, 0.8125rem, block under the title; `.pl-disclosure__text` is `flex: 1; display: flex; flex-direction: column`.
- `.pl-disclosure__chevron` is a 0.6rem square with `border-right: 2px solid currentColor; border-bottom: 2px solid currentColor; transform: rotate(45deg); transition: transform 0.2s;` in `var(--hub-text-muted)`; `[open] > summary .pl-disclosure__chevron { transform: rotate(-135deg) }`. That is the obvious open and close indicator Jo asked for; it points down when closed and up when open.
- `[open] > summary { border-bottom-left-radius: 0; border-bottom-right-radius: 0 }` and `.pl-disclosure__body { border: 1px solid var(--hub-border); border-top: 0; border-radius: 0 0 10px 10px; padding: 1rem }` so the open item reads as one card.

Add a **Disclosure (`.pl-disclosure`)** entry to FRONTEND.md's component library (markup above, when to use it, "never hide the marker without replacing it with the chevron").

### B.6 Context

`_why_teach_context` adds `faq_items` and `faq_intro_html` (from the model methods). `ClassSettings.load()` is one query; nothing new hits the database. The query count spec on the example card must still pass with the same number.

---

## C. The clipped tooltip (`static/js/pl_help.js`, new)

A tiny script, loaded from `templates/hub/base.html` next to `hero_placement.js` (plain `<script src>`, no defer needed: it only binds delegated listeners). Behavior:

- On `pointerenter` and `focusin` of any `.pl-help` (event delegation on `document`), when the viewport is wider than 768px (the phone CSS already pins the bubble to the viewport; leave it alone), measure the icon with `getBoundingClientRect()` and set the bubble to `position: fixed; top: rect.bottom + 8px; left: rect.left`, clamped so `left + bubbleWidth <= innerWidth - 12` (use `right` anchoring by flipping to `left: rect.right - bubbleWidth` when the bubble would overflow). Set `width` only if the bubble's natural width is wider than the viewport allowance. Mark the wrapper with `data-help-lifted` so the CSS transition still runs.
- On `pointerleave` and `focusout`, remove the inline styles.
- On `scroll` (capture) and `resize`, if a bubble is lifted, re-measure (or simply hide by clearing the styles; hiding is acceptable and simpler).
- Because the bubble is now viewport positioned it escapes `overflow: hidden` and `overflow: auto` ancestors, which is the whole fix. No markup changes anywhere; every existing `.pl-help` benefits.

Update FRONTEND.md's Help tooltip section: still CSS only for visibility; `pl_help.js` lifts the bubble out of clipping ancestors on desktop, so `.pl-help` may be used inside `.admin-table-wrap` and scrolling tables. Add a spec that `hub/base.html` renders the script tag (`tests/hub/base_scripts_spec.py` or the nearest existing base template spec).

---

## D. The whole card in the preview, and imported photos that count

### D.1 Model (`classes/models.py`, `ClassOffering`)

```python
@property
def hero_image_url(self) -> str:
    """The class's own hero photo as a URL, or "" when it has none.

    The uploaded file wins; otherwise a photo imported from the legacy class site is
    served through the ``classes:legacy_image`` proxy (same origin, so the cropper and
    the card frames can use it). The category fallback is deliberately NOT here: this
    is the photo the class itself owns, which is what the editor and the readiness
    checklist ask about.
    """

@property
def has_hero_photo(self) -> bool:
    """Whether the class carries its own hero, uploaded or imported."""
    return bool(self.image) or bool(self.legacy_image_url)
```

`readiness()` passes `has_hero=self.has_hero_photo`; `has_submittable_image` becomes `self.has_hero_photo and self.gallery_images.exists()` (docstring updated: an imported photo is the class's own photo). `email_hero_image_html` and `display_images` are unchanged (they already fall through to what the public page shows; do not add the legacy proxy URL to an email).

### D.2 One media partial, one photo rule (`templates/classes/public/_class_card_media.html`)

Replace the `image` and `legacy_image_url` branches with one branch on `offering.hero_image_url`, keeping the `object-position` style and the optional `live_position` binding on it. The category hero and logo placeholder branches stay. Every existing assertion in `class_card_partial_spec.py` keeps passing; add one for a legacy only class getting the position and the proxy `src`.

### D.3 The card partial in preview mode (`templates/classes/public/_class_card.html`)

Two new optional parameters, documented in the header comment:

- `preview`: truthy renders the media as a span (pass through to the media include), the title as `<span class="cls-title">` with no href, the instructor name as plain text, and drops the `+N more` buttons' `onclick` (leave the rows). Nothing in a preview frame navigates.
- `live_position` and `live_title`: Alpine expressions. `live_position` passes through to the media include. When `live_title` is set the title text is `<span x-text="{{ live_title }} || '{{ offering.title|strip_date_suffix|escapejs }}'">{{ offering.title|strip_date_suffix }}</span>` so the card follows what the host is typing on step 1 before the next save.

Default rendering (no params) is byte identical to today. The catalog and the Host a Workshop showcase pass nothing.

### D.4 Composer context (`classes/views.py` `_composer_context`)

Add `"card_group": CatalogGroup(saved) if saved is not None else None`. `_class_card.html` reads `offering.sessions.all` and `offering.instructor`; the edit views already load the offering, so this is at most one extra sessions query. Keep `django_assert_num_queries` specs honest: update the number if one covers the edit page and say so in the PR.

The composer root's `x-data` gains `liveTitle: ''` and the root `<div>` gets `@input="if ($event.target.id === 'id_title') liveTitle = $event.target.value"`.

### D.5 Step 2, Catalog Card pane (`templates/classes/_components/card_focus_field.html`)

- Heading stays "Catalog Card". Hint becomes: "This is your whole card, at the two widths members see. Drag the sliders to choose which part of the photo shows."
- When `card_group` is set and `offering.hero_image_url` is non empty: both frames render the REAL card: `{% include "classes/public/_class_card.html" with group=card_group preview=True live_position="objectPosition" live_title="liveTitle" %}`. The frames keep their laptop and phone widths; the card is taller now, which is the point.
- When the class is saved but has no photo of its own: the frames still render the real card (with the category or logo placeholder the catalog would show), the sliders are hidden, and the note reads "Add a photo above and the sliders appear." That shows a host exactly what skipping the photo looks like.
- When the class is unsaved (create mode): keep the `localSrc` mirror, but wrap it in a skeleton card: the media strip from the mirrored data URL plus a `.cls-body` with the live title (`x-text="liveTitle || 'Your class title'"`) and one muted line "Dates, price and spots show up after your first save." Sliders show only once `localSrc` is set (as today).
- The note under the frames: "Your photo, title, dates, price and spots, exactly as members see them. Dates and price update when you save."

### D.6 Step 5, How Your Card Looks (`class_composer.html`)

Same rule: when `card_group` is set and the class has a hero, the phone frame renders the full card (`preview=True live_position="cardPosition" live_title="liveTitle"`). Otherwise the empty state line "Add a photo on step 2 and your card shows up here." Remove the "photo only" sentence everywhere; nothing on the page may still describe the frames as the photo alone.

### D.7 Photos step, the current hero (`hero_image_field.html` and the composer include)

- `hero_image_field.html` takes `current_image_url` (a string) instead of `current_image`. The composer passes `current_image_url=offering.hero_image_url`. Grep for every other include of this partial (guild and category hero forms) and pass `current_image_url=<thing>.hero_image.url` or the matching URL; none of them may break.
- When the offering has no `image` but does have `legacy_image_url`, render a `pl-field-hint` under the preview: "This photo came over from the old class site. Everything here works the same. Upload a new one to replace it." The cropper mounts on it exactly as on an upload (the proxy is same origin).

### D.8 Readiness copy

`readiness_items` hint for the hero stays "Add a hero photo." The step 2 tab mark and the checklist now tick for a legacy photo because `has_hero_photo` feeds them.

---

## E. Version and changelog (`plfog/version.py`)

`VERSION = "1.47.0"`. One new entry at the top, `version: "1.47.0"`, `date: "2026-09-09"`, plain language, no dashes, no UI label strings that could collide with negative assertions:

- title: "The whole card in the preview, where the money goes, and imported photos that count"
- changes:
  1. "The card preview in the class editor is now the whole catalog card: photo, title, dates, price and spots, at the two widths members see. What you sign off on is what they get."
  2. "Classes brought over from the old class site had a photo on their page, but the editor and the submit checklist said they had none. They count now, and you can position them like any other photo."
  3. "Host a Workshop has a new Where the Money Goes section showing how a paid class splits between you, Past Lives and your guild. The common questions open and close, and the hosting guide has a clear open button."
  4. "Admins: the words on that page moved to their own Teaching Marketing Page under Class Settings, with the same formatting toolbar as announcements instead of Markdown."
  5. "Status tooltips on the admin classes list no longer get cut off by the table."

The entry text renders on every hub page; after writing it run `tests/plfog/` and one page level spec.

---

## F. Tests (BDD, `describe_*` / `it_*` only; `context_*` is never collected)

Update:
- `classes/spec/models/settings_spec.py`: defaults are HTML now (`it_carries_the_prose_sections`); the render counts (3 steps, 4 bullets, 4 headings) still hold; add `describe_teach_page_faq_items` (four items from the default; intro before the first heading; no headings → empty list; a script in a heading is stripped before it is split); add `describe_money_split` defaults (70/20/10, enabled, the note); the dash guard covers the new note.
- `classes/spec/views/admin_settings_spec.py`: the waivers page no longer renders any `teach_page_*` field or the example picker; the hub renders two tiles with the two titles.
- New `classes/spec/views/admin_teaching_page_settings_spec.py`: renders every field with the Quill mount (`data-rte-seed="server"`, `data-rte-toolbar="page"`) for the three prose fields and a plain textarea for features; prefills the defaults (rendered into the mount); round trips every field including the split; stores sanitized HTML for a Quill post (`<script>` gone, `<p>` kept); stores a Markdown post unchanged; rejects a split that does not sum to 100 with the error on the page; accepts any split when the section is disabled; 403 for a non admin on GET and POST; the "Teaching Marketing Page saved." message.
- `classes/spec/views/teach_why_spec.py`: `SECTION_MARKERS` gains the money section (hidden when disabled) and the FAQ block now renders `<details class="pl-disclosure pl-teach-faq__item"` four times with the first open; a legacy Markdown value stored in a prose field still renders (dual mode); the shared disclosure markup around the guide; the query count spec unchanged.
- `classes/spec/views/class_card_partial_spec.py`: the composer frames render the full card (`class="cls-body"`, the title, `cls-price`) twice on step 2 and once on step 5; a legacy only class renders frames with the proxy `src` and the object position; the saved no photo case renders the placeholder card and hides the sliders; preview mode renders no `href` inside a frame; `live_title` binding present.
- `classes/spec/views/class_composer_spec.py`: the create mode skeleton line; the Photos step hint for a legacy photo.
- `classes/spec/models/...`: `hero_image_url` (upload, legacy proxy, none), `has_hero_photo`, `readiness` ticking the hero for a legacy class, `has_submittable_image` with legacy + gallery.
- `tests/hub/` base template spec: `pl_help.js` script tag present.
- Migration 0062: a spec in `classes/spec/migrations/` (follow the existing migration spec pattern in the repo if one exists; otherwise a plain spec that imports the forward and reverse functions and runs them against `ClassSettings` rows: the old default converts to the new default, a custom Markdown value converts to HTML, HTML is left alone, and the reverse restores the old default).
- `tests/template_comment_lint_spec.py` and `manage.py check` must pass.

E2E: `tests/e2e/instructor_orientation_spec.py` reads HERO, the buttons and the banner; none of those strings change. Grep `tests/e2e/` for "Read the Hosting Guide" and "Waivers, Reminders" before pushing.

Verification commands the builder runs (judge by pass/fail lines; the global coverage gate always fails on partial runs and means nothing there):

```
.venv/bin/pytest classes/spec/models/settings_spec.py classes/spec/views/admin_settings_spec.py classes/spec/views/admin_teaching_page_settings_spec.py classes/spec/views/teach_why_spec.py classes/spec/views/class_card_partial_spec.py classes/spec/views/class_composer_spec.py classes/spec/views/composer_tour_targets_spec.py classes/spec/forms tests/template_comment_lint_spec.py tests/plfog tests/hub/teach_sidebar_spec.py -q
DATABASE_URL="sqlite:///tmp-check.sqlite3" .venv/bin/python manage.py check && DATABASE_URL="sqlite:///tmp-check.sqlite3" .venv/bin/python manage.py makemigrations --check --dry-run; rm -f tmp-check.sqlite3
.venv/bin/ruff format . && .venv/bin/ruff check --fix .
```

---

## G. Post deploy (orchestrator)

1. Watch the login page for the v1.47.0 badge, then confirm migrations 0061 and 0062 are APPLIED on production and that `ClassSettings.teach_page_faq` on production starts with `<h3>`.
2. Run `download_legacy_images` from the local checkout against production (`DATABASE_URL="$PROD_DATABASE_URL"`, the R2 vars from `.env`; print the connected host first). Expect 23 rows; verify by data that Glen's pk 627 has a non empty `image` and an empty `legacy_image_url`, and that the catalog card and the composer Photos step show it.
3. The Discord announce fires on the VERSION change on its own.
