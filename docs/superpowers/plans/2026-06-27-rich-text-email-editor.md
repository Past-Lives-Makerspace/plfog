# Rich-text editor for admin-authored emails

**Date:** 2026-06-27
**Branch / PR:** `release-0.19.x` (PR #109)
**Status:** Ready to build

## Summary

Two things ship together:

1. **CSS fix (already applied):** the Site Settings → Announcements composer rendered its
   *Subject* and *Message* fields as bare browser-default white boxes. The page's input
   styling is scoped under `.site-settings-form`; the announcements `<form>` was the one
   form missing that class. Adding the class themes the fields. (Done in
   `templates/hub/admin/site_settings.html`.)

2. **WYSIWYG rich-text editor (this spec):** give admins/instructors a real
   what-you-see-is-what-you-get editor — **bold, italic, underline, subheadings (H2/H3),
   bullet & numbered lists, links** — for every email body they author by hand. One
   reusable widget, wired into all four author-written email-body surfaces.

The recipient sees nicely formatted email; the author sees live formatting while typing.

## The four author-written email-body surfaces

Every one of these is a free-text body an admin/instructor types that gets emailed to a
member. All four today render the body as plain text (`{{ body|linebreaks }}` or escaped
paragraphs) inside a dark `#092E4C` card whose wrapper sets `color:#F4EFDD`.

| # | Surface | Form (field) | Stored on | Email render today |
|---|---------|--------------|-----------|--------------------|
| 1 | Sitewide announcement | `hub.forms.SiteAnnouncementForm.body` | not stored (emitted immediately) | `hub/views.py::_announcement_email_html` — escaped paragraphs; also feeds in-app bell + Discord |
| 2 | Class welcome email | `classes.forms.TeachWelcomeEmailForm.welcome_email_body` | `ClassOffering.welcome_email_body` (TextField) | `templates/classes/emails/welcome.html` — `{{ body\|linebreaks }}` |
| 3 | Guild orientation thank-you | `hub.forms.GuildOrientationSettingsForm.thankyou_email_body` | `GuildOrientationSettings.thankyou_email_body` (TextField) | `templates/membership/emails/orientation_thankyou.html` — `{{ body\|linebreaks }}` |
| 4 | Guild join / welcome | `hub.forms.GuildOrientationSettingsForm.join_email_body` | `GuildOrientationSettings.join_email_body` (TextField) | `templates/membership/emails/guild_welcome.html` — `{{ body\|linebreaks }}` |

**Subjects stay plain text** — rich text applies to the *body* only.

## Editor choice: Quill 2.x, vendored

The app has **no JS build step** (no `package.json`); JS is vendored into `static/js/`
and loaded with `{% static %}`. So vendor Quill as static files — do not add a bundler.

- **Why Quill:** single JS + single CSS file, no build, explicit toolbar config that
  matches the requested buttons exactly (underline + multi-level headings — which Trix
  can't do cleanly), clean-ish HTML output, easy to theme for dark mode.
- **Vendor** `quill.min.js` → `static/js/quill.min.js` and `quill.snow.css` →
  `static/css/quill.snow.css` (download the pinned 2.x release; record the version in a
  one-line comment at the top of `rich-editor.css`). No CDN at runtime — vendored, same as
  `alpine.min.js` / `htmx.min.js`.
- **Toolbar (minimal — do not add more):** bold, italic, underline, header (H2 + H3),
  bullet list, ordered list, link, clean/remove-formatting. No images, tables, colors,
  fonts, or font sizes (out of scope — see below).

### Known Quill gotcha to handle

Quill emits bullet lists as `<ol><li data-list="bullet">…` rather than semantic
`<ul><li>`. The server-side sanitizer **must normalize** this to real `<ul>`/`<ol>` so the
stored + emailed HTML is clean and renders right in mail clients. Cover it with a test.

## Components to build

### 1. Reusable widget — `core/widgets.py::RichTextEditorWidget`

```python
class RichTextEditorWidget(forms.Textarea):
    """A Quill-backed rich-text editor over a textarea.

    Renders the real (named) textarea visually hidden plus a Quill mount div. A small
    init script loads the textarea's HTML into Quill and syncs Quill's innerHTML back
    to the textarea on every change, so normal form submission carries the HTML.
    """
    template_name = "widgets/rich_text_editor.html"
```

- Template `templates/widgets/rich_text_editor.html`: renders the textarea (CSS-hidden,
  keeps its `name`/`id`), a `<div class="pl-rte" data-rte-for="{{ id }}">` mount, and an
  inline init `<script>` keyed to that field's auto id. Init runs on `DOMContentLoaded`,
  guards on `window.Quill` being present, instantiates one Quill per mount, seeds it from
  the textarea value, and writes `quill.root.innerHTML` back to the textarea on
  `text-change`. Multiple instances on one page (the orientation page has two) must each
  bind by their own id — never by a global selector.
- **Asset loading:** a partial `templates/_components/rich_editor_assets.html` emits the
  Quill `<link>`/`<script>` + `rich-editor.css`. Include it **once** near the top of each
  using-template (body-level `<script>`/`<link>` is fine and works inside partials). The
  widget's own init script assumes Quill is already loaded above it. Don't wire Django
  `form.media` (hub base doesn't render it) — the explicit include is simpler and visible.

### 2. Sanitizer + helpers — `core/html_sanitize.py`

Model this on the existing `membership/markdown.py` (same `bleach` + `_harden_link`
pattern). `bleach` is already a dependency.

```python
def sanitize_rich_html(raw: str) -> str:
    """Sanitize editor HTML to a tight allowlist; normalize Quill lists; harden links."""

def rich_html_to_text(html: str) -> str:
    """Flatten sanitized HTML to readable plain text (bell + Discord fallback)."""

def render_rich_email_body(value: str) -> str:
    """Email-ready HTML for a stored body, back-compatible with legacy plain text.

    If `value` already contains a block tag (editor HTML) -> sanitize + inline-style for
    the dark card. Otherwise treat as legacy plain text -> escape + paragraph-ize (blank
    line = new paragraph, single newline = <br>), then inline-style. Returns a value safe
    to mark |safe in a template.
    """
```

- **Allowlist** (`_ALLOWED_TAGS`): `p, br, strong, b, em, i, u, h2, h3, ul, ol, li, a,
  blockquote`. `_ALLOWED_ATTRS = {"a": ["href", "title"]}`. Strip everything else (incl.
  `style=`, `class=`, `script`, `iframe`, event handlers) keeping inner text.
- **Link hardening:** reuse the `_harden_link` approach (`rel="noopener nofollow
  noreferrer" target="_blank"`). Factor the shared bits so `membership/markdown.py` and
  this module don't duplicate the callback if it's clean to do so; otherwise a small
  copy is acceptable — don't contort the design to dedupe.
- **No `h1`** — H1 is the email shell's title; author subheadings are H2/H3.

### 3. Email inline-styling — `style_rich_email_fragment(fragment) -> str`

The existing `core/events/templates.py::_style_copy_fragment` only inline-styles `<p>` and
`<a>`. The richer tag set needs styling so it reads well on the dark `#092E4C` card.
Extend (or add a sibling used by `render_rich_email_body`) to inline-style `h2, h3, ul,
ol, li, blockquote, strong` with light (`#F4EFDD`) text, gold (`#EEB44B`) links, and sane
margins. Text color is inherited from the wrapper div, but headings/lists need explicit
margins and sizes so they don't render with ugly browser defaults across mail clients.
Keep the "don't clobber an already-`style=`d tag" rule from the original.

### 4. Plain-text fallback for bell + Discord (announcement only)

`_announcement_email_html` and the announcement send path in `hub/views.py` currently feed
the in-app bell + Discord from the body. After this change the email body is rich HTML;
the bell + Discord must get `rich_html_to_text(sanitized_body)` (then the existing
`_fit()` clipping applies). Email gets the full styled HTML. Verify the bell's 500-char
clip still holds.

### 5. Theming CSS — `static/css/rich-editor.css`

Quill's `snow` theme is light by default. Override the toolbar + editor surface to the hub
dark theme tokens (`--hub-input-bg`, `--hub-input-border`, `--hub-text`,
`--hub-text-muted`, gold accent `#EEB44B`): dark editor background, light text, themed
toolbar buttons + active states, themed link tooltip, and a `[data-theme="light"]` reset
so it also works on the light theme. Scope all rules under `.pl-rte` / the Quill container
class to avoid leaking. Match the input look of `.hub-form-group textarea` (border radius,
focus ring) so it sits naturally next to other fields.

## Wiring the four surfaces

For each: swap the body field's widget to `RichTextEditorWidget`, sanitize on save, and
switch the email render to the rich path.

1. **Announcement** (`hub/forms.py` + `hub/views.py`):
   - `SiteAnnouncementForm.body` → `RichTextEditorWidget`; add `clean_body` →
     `sanitize_rich_html`. Update `help_text` (drop "Plain text or simple HTML"; say
     "Use the toolbar to format — bold, headings, lists, links").
   - `_announcement_email_html`: build the fragment from `render_rich_email_body(body)`
     instead of escaped paragraphs (title stays escaped `<h2>`).
   - In-app/Discord text ← `rich_html_to_text`.
   - Preview iframe is automatically correct (it renders the same `_announcement_email_html`).
   - `templates/hub/admin/site_settings.html`: include `rich_editor_assets.html` in the
     announcements tab (or `extra_head`).

2. **Class welcome email** (`classes/forms.py` + template):
   - `TeachWelcomeEmailForm.welcome_email_body` → `RichTextEditorWidget`; sanitize in
     `clean_welcome_email_body` (or the form's `clean`). Keep the "required when enabled"
     rule — check the *stripped text* isn't empty (an empty Quill doc is `<p><br></p>`;
     treat that as blank).
   - `templates/classes/emails/welcome.html`: `{{ body|linebreaks }}` →
     `{{ body|rich_email_body }}` (new filter, see below).
   - `templates/classes/_components/welcome_email_form.html`: include the assets partial
     at the top; update the on-page "Preview" block + the "Plain text — line breaks are
     kept" helper line to reflect formatting.

3. **Guild thank-you** + 4. **Guild join** (`hub/forms.py` + templates):
   - `GuildOrientationSettingsForm.thankyou_email_body` and `join_email_body` →
     `RichTextEditorWidget`; sanitize both in `clean`. Keep the same empty-Quill-is-blank
     handling for the "required when enabled" validators.
   - `templates/membership/emails/orientation_thankyou.html` and `guild_welcome.html`:
     `{{ body|linebreaks }}` → `{{ body|rich_email_body }}`.
   - `templates/hub/orientation_settings.html`: include the assets partial once.

### Template filter

Register `rich_email_body` (wraps `core.html_sanitize.render_rich_email_body`, returns
`mark_safe`) in a templatetags module — `core/templatetags/rich_text.py`. Load it in the
three email templates. This is the single safe-render choke point for stored bodies.

## Back-compat

No DB migration — the three stored fields are already `TextField`. Existing values are
plain text; `render_rich_email_body` detects "no block tag → legacy plain text" and
paragraph-izes them, so already-saved welcome/thank-you/join emails keep rendering
correctly until an author re-edits (Quill loads the plain text fine and re-saves as HTML).

## Security

- **Never trust the client.** Every body is sanitized server-side in the form's `clean`
  before save/use — the editor HTML is treated as hostile input.
- Allowlist strips `script`, `style`, `iframe`, event handlers, inline `style=`, unknown
  tags. Links hardened with `rel`/`target`. Same posture as the existing guild-Markdown
  renderer.
- The preview iframe and all email templates only ever render *sanitized* HTML.

## Tests (100% branch coverage + mutation — this repo is strict)

BDD `*_spec.py` under each app's `spec/`. Add:

- `core/spec/html_sanitize_spec.py`:
  - `sanitize_rich_html`: keeps each allowed tag; strips `script`/`style`/`onclick`/
    `iframe`/unknown tags (keeps inner text); strips inline `style=`/`class=`; hardens
    links (`rel`/`target`); **normalizes Quill `<ol data-list="bullet">` → `<ul>`**;
    empty/blank input → `""`.
  - `rich_html_to_text`: tags flattened to readable text; entities unescaped; whitespace
    collapsed.
  - `render_rich_email_body`: editor-HTML branch (sanitized + inline-styled, headings/
    lists/links carry inline styles); legacy plain-text branch (blank line → new
    paragraph, single newline → `<br>`); empty → empty.
- `core/spec/widgets_spec.py`: widget renders the named textarea + a `.pl-rte` mount +
  init keyed to the field id; two instances render two distinct ids.
- Form specs: each of the 4 fields sanitizes on clean (a `<script>` in the submitted body
  is stripped); empty-Quill-doc (`<p><br></p>`) treated as blank for the
  "required-when-enabled" validators.
- View/email specs: announcement preview + send produce sanitized styled HTML and the
  bell/Discord text is the flattened plain text; the three template-based emails render
  `rich_email_body` output (assert a heading/list/bold survives and is styled).
- Template-filter spec for `rich_email_body` (safe-marked, sanitized).

## Versioning & changelog

- Bump `VERSION` in `plfog/version.py` to the next patch in the 0.19 line (current
  committed = `0.19.9` → `0.19.10`; if a parallel session has already taken it, use the
  next free patch).
- **Curate, don't pile on.** This is an authoring nicety on top of features already in the
  0.19 line (the Announcements composer, instructor welcome emails, guild orientation
  emails). Per `CLAUDE.md`'s changelog rule, **fold one short, member-friendly line into
  the most relevant existing 0.19 entry** and re-stamp that entry's `version`/`date` to
  the new `VERSION` (and move it to the top) rather than adding a brand-new entry —
  unless none fits, in which case add one concise entry. Keep an entry whose
  `version == VERSION` so `announce_release` resolves. Plain language, no jargon —
  something like "Format the emails you send — bold, italic, headings, and lists."

## Out of scope (YAGNI — do not build)

Image/file upload, tables, text color/highlight, font family/size, code blocks, emoji
pickers, Markdown round-tripping, a separate preview-toggle button (the existing previews
suffice), and rich text on **subjects** (plain only). Keep the toolbar to the seven
controls listed above.

## File-by-file change list

**New**
- `static/js/quill.min.js`, `static/css/quill.snow.css` (vendored, pinned 2.x)
- `static/css/rich-editor.css`
- `core/html_sanitize.py`
- `core/widgets.py` (or add to it if it exists)
- `core/templatetags/rich_text.py`
- `templates/widgets/rich_text_editor.html`
- `templates/_components/rich_editor_assets.html`
- specs listed above

**Modified**
- `hub/forms.py` — `SiteAnnouncementForm.body`, `GuildOrientationSettingsForm` two bodies → widget + sanitize
- `hub/views.py` — `_announcement_email_html` + announcement send (bell/Discord plain text)
- `classes/forms.py` — `TeachWelcomeEmailForm.welcome_email_body` → widget + sanitize
- `core/events/templates.py` — extend inline styler for the richer tag set
- `templates/hub/admin/site_settings.html` — assets include (CSS fix already applied)
- `templates/classes/_components/welcome_email_form.html` — assets include + helper copy
- `templates/hub/orientation_settings.html` — assets include
- `templates/classes/emails/welcome.html`,
  `templates/membership/emails/orientation_thankyou.html`,
  `templates/membership/emails/guild_welcome.html` — `|linebreaks` → `|rich_email_body`
- `plfog/version.py` — VERSION bump + changelog fold
