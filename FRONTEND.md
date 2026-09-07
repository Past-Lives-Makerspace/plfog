# plfog Frontend Guide

Reference for building pages, forms, and components in plfog. Read this before creating any template.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Templates | Django templates with `{% include %}` components |
| Interactivity | Alpine.js 3.x (`x-data`, `x-show`, `@click`, `$dispatch`) |
| Server communication | HTMX (`hx-get`, `hx-post`, `hx-target`, `hx-swap`) |
| Styling | Custom CSS with `pl-` prefix, CSS variables, dark/light themes |
| Admin | django-unfold + custom overrides in `unfold-custom.css` |

No build step. No npm. No bundler. All JS is loaded via `<script>` tags.

## Design System

### Colors (CSS Variables)

Dark mode is **"Obsidian"** (near-black charcoal); light mode is **"Slate"** (cool neutral gray). Surfaces form an **elevation ladder** — `bg` (deepest) → `surface` → `card-bg` → `elevated` — so layers read as depth instead of one flat tone. Both are anchored on the official PL blue **`#092e4b`**, which is used for *structure* (hero gradient, brand) while a brighter sibling (`--hub-blue`) handles *interactive* accents. Tokens are defined in `:root` (dark) and overridden under `[data-theme="light"]`.

| Token | Dark (Obsidian) | Light (Slate) | Usage |
|-------|------|-------|-------|
| `--hub-bg` | `#0a0b10` | `#eef0f3` | Page background (deepest layer) |
| `--hub-surface` | `#13151d` | `#e6e9ee` | Inset panels, tiles, table stripes |
| `--hub-card-bg` | `#181b24` | `#ffffff` | Card / modal background |
| `--hub-elevated` | `#20242f` | `#ffffff` | Dropdowns, popovers, raised pills |
| `--hub-text` | `#F4EFDD` | `#1D1E1E` | Primary text |
| `--hub-text-muted` | `#8b97a8` | `#5b6675` | Secondary text, labels, hints |
| `--hub-sidebar-bg` | `#0d0f15` | `#ffffff` | Sidebar background (**theme-aware** — follows light/dark) |
| `--hub-sidebar-text` | `#F4EFDD` | `#1D1E1E` | Sidebar text / brand |
| `--color-tuscan-yellow` | `#EEB44B` | `#EEB44B` | Primary accent — buttons, active nav, links |
| `--hub-blue` | `#3d8bd4` | `#2f6fb0` | Secondary/structural accent — info, focus rings |
| `--color-navy` | `#092E4C` | `#092E4C` | Brand structural blue — hero gradient, button text |

> The sidebar uses dedicated `--hub-sidebar-*` tokens (not `--color-navy`) so it switches with the theme. Never paint the sidebar with `--color-navy` directly — it won't respond to light mode.

### Typography

- **Headings:** Lato (400, 700, 900)
- **Body:** Inter (300, 400, 500, 700)
- Loaded via Google Fonts CDN in base templates.

### Spacing

8px grid: `0.25rem`, `0.5rem`, `0.75rem`, `1rem`, `1.25rem`, `1.5rem`, `2rem`

## Component Library

All components live in `templates/components/`. Include via `{% include "components/<name>.html" with param=value %}`.

### Modal (`components/modal.html`)

Reusable modal container. Content loaded via HTMX or passed as context.

**Parameters:**
- `modal_id` (required) — unique DOM id
- `modal_title` (required) — heading text
- `modal_size` — `sm` (400px), `md` (560px), `lg` (720px)

**Open a modal:**
```html
<button @click="$dispatch('open-modal', 'my-modal')">Open</button>
```

**Close a modal (from inside):**
```html
<button @click="$dispatch('close-modal', 'my-modal')">Done</button>
```

**Load content via HTMX:**
```html
<button
    @click="$dispatch('open-modal', 'edit-item')"
    hx-get="/items/42/edit-form/"
    hx-target="#edit-item-body"
    hx-swap="innerHTML">
    Edit
</button>
{% include "components/modal.html" with modal_id="edit-item" modal_title="Edit Item" %}
```

### Toast (`components/toast.html`)

Toast notifications. Already included in `hub/base.html` and `admin/base.html` — do not include again.

**Server-side (from views):**
```python
from hub.toast import trigger_toast

def my_view(request):
    # ... do work ...
    response = HttpResponse(status=204)
    trigger_toast(response, "Item saved!", "success")
    return response
```

**Client-side (from Alpine.js):**
```html
<button @click="$dispatch('show-toast', {message: 'Copied!', type: 'info'})">Copy</button>
```

**Types:** `success` (green), `error` (red), `info` (blue)

### Toggle (`components/toggle.html`)

Toggle switch for boolean fields. Automatically used by `form_field.html` for checkbox inputs.

**Parameters:**
- `field` (required) — Django BooleanField
- `toggle_label` — display label
- `toggle_description` — description text

```html
{% include "components/toggle.html" with field=form.is_active toggle_label="Active" toggle_description="Show this product to members" %}
```

### Form Field (`components/form_field.html`)

Standard field wrapper. Auto-detects checkboxes and renders as toggle.

**Parameters:**
- `field` (required) — Django form field
- `field_label` — label override
- `field_hint` — hint text override

```html
{% include "components/form_field.html" with field=form.name %}
{% include "components/form_field.html" with field=form.email field_hint="We'll never share this" %}
{% include "components/form_field.html" with field=form.is_active %}  {# auto-renders as toggle #}
```

### Help tooltip (`.pl-help`)

The app-wide "?" hover bubble next to a title, label, or column header (e.g. the Community Calendar title, the notifications matrix Push column). It is **CSS-only** (`.pl-help` in `hub.css`) so it works on every page with no Alpine dependency. It is inline markup, **not** a `components/` include. Never use a native `title=` attribute or hand-roll your own bubble — use this.

```html
<span class="pl-help">
  <span class="pl-help__icon" tabindex="0" role="img" aria-label="Short label: full explanation.">?</span>
  <span class="pl-help__bubble">Full explanation shown on hover or keyboard focus.</span>
</span>
```

- Reveals on `:hover` and `:focus-within` — `tabindex="0"` on the icon makes it keyboard-reachable.
- Anchored bottom-left by default. If the icon lives in the narrow right-hand aside, add `pl-help--right` to the wrapper so the 320px bubble does not overflow the viewport.
- On phones the bubble auto-pins to the viewport gutters — do not re-position it.

### Confirm Modal (`components/confirm_modal.html`)

For destructive actions (delete, void, deactivate).

**Parameters:**
- `confirm_id` (required) — unique DOM id
- `confirm_title` — heading (default: "Are you sure?")
- `confirm_message` — body text
- `confirm_action_url` — form POST target
- `confirm_button_text` — button label (default: "Confirm")
- `confirm_button_style` — `danger` (default) or `primary`

**Typed-confirmation (opt-in, plain-POST only):** for an especially destructive action, require the user to type an exact word before the Confirm button enables.
- `confirm_typed_value` — when set, a text input appears and Confirm stays disabled until the typed text matches this value exactly.
- `confirm_typed_placeholder` — the input's placeholder + label (default: "Type to confirm").
- `confirm_typed_field_name` — the POST field name the typed value is submitted under (default: "confirm").

Omit all three and the modal renders exactly as before.

```html
<button @click="$dispatch('open-confirm', 'void-charge')">Void</button>
{% include "components/confirm_modal.html" with confirm_id="void-charge" confirm_title="Void this charge?" confirm_message="This will remove the charge from the member's tab." confirm_action_url="/billing/void/42/" confirm_button_text="Void Charge" %}
```

## Editable Lists & Destructive Actions

Mandatory for any page that edits a list of rows (a Django formset) or deletes something. These exist because getting them wrong has burned us repeatedly: blank rows blocking save, delete rendered as a toggle switch, buttons clashing with the field above.

### Delete is a button, never a toggle

- A delete control is a **button**, never a checkbox/switch. Do **not** pass a formset's `DELETE` field through `form_field.html` — it auto-renders as a toggle (see Rule 3's exception). Render `{{ form.DELETE }}` hidden and drive it from a real button.
- Style: `pl-btn pl-btn--danger pl-btn--sm`. Never a raw full-size `<button>Delete</button>`.
- Always give it `style="margin-top:0.75rem;"` (or a class) so it clears the field/toggle above it. A delete button flush against the last field is a bug, not a detail to fix later.

### Deleting a saved row saves the whole page (no lost work)

For a row already in the DB (`form.instance.pk`), the Delete button flips the hidden `DELETE` field and submits the form, so every other edit on the page is preserved:

```html
{% if form.instance.pk %}
  <div style="display:none;">{{ form.DELETE }}</div>
  <button type="button" class="pl-btn pl-btn--danger pl-btn--sm" style="margin-top:0.75rem;"
          onclick="document.getElementById('{{ form.DELETE.id_for_label }}').checked = true; this.form.requestSubmit();">
    Delete
  </button>
{% endif %}
```

### Adding/removing rows — `extra=0` + an explicit "+ Add" button

A formset with `extra=1` renders a perpetual blank row that can **block save** — a required field on the blank row (or a checkbox defaulting checked) makes it fail validation, and the user can't save work they've already done. Don't do that. Instead:

- Build the formset with **`extra=0`** so only real rows render.
- Add rows on demand with a **"+ Add …" button** that clones a hidden `<template>` of `formset.empty_form`, replaces `__prefix__` with the new index, and bumps `id_<prefix>-TOTAL_FORMS`.
- Cloned (un-saved) rows get a **"Remove" button** that just removes the DOM node — no save needed, and a half-filled row the user abandons never blocks the save.

Canonical implementations to copy: the FAQ and Links editors, and the orientation recurring-hours editor, all in `templates/hub/guild_edit.html`.

## Interaction Patterns

| Scenario | Pattern | Example |
|----------|---------|---------|
| Quick action (1-3 fields) | Modal + Toast | "Add to Tab", "Enter Your Own Price" |
| Data entry (4+ fields) | Inline form on page | Profile settings, billing settings |
| Destructive action | Confirm modal | Delete product, void charge |
| Success feedback (HTMX) | Toast notification | "Added to your tab!" |
| Success feedback (full page) | Django messages | Login, signup |
| Optional / secondary form on a page | Toggle button reveals it (`x-show`, closed by default) | "Email selected registrants" |

**Rule of thumb:** If the action doesn't need the user to leave the page, use a modal + toast. If it's a full form with many fields, use an inline form or dedicated page.

## HTMX Patterns

### Form submission returning a toast

```python
# views.py
from hub.toast import trigger_toast

def add_to_cart(request, pk):
    # ... validate and process ...
    response = HttpResponse(status=204)
    trigger_toast(response, "Added to cart!", "success")
    return response
```

```html
<!-- template -->
<form hx-post="{% url 'hub_cart_add' guild.pk %}" hx-swap="none">
    {% csrf_token %}
    <input type="hidden" name="product_pk" value="{{ product.pk }}">
    <button type="submit">Add to Tab</button>
</form>
```

### Loading a partial into a modal

```html
<button
    @click="$dispatch('open-modal', 'my-modal')"
    hx-get="{% url 'my_partial' %}"
    hx-target="#my-modal-body"
    hx-swap="innerHTML">
    Open Form
</button>
```

### Updating another element after a form submit (OOB swap)

```python
# Return the updated element in the response body
response = render(request, "hub/partials/tab_pill.html", {"tab_balance": new_balance})
trigger_toast(response, "Items added to your tab!")
return response
```

```html
<!-- In the partial, use hx-swap-oob to update the tab pill -->
<a id="tab-balance-pill" hx-swap-oob="true" ...>${{ tab_balance }}</a>
```

## Email Templates

Emails live in `templates/**/emails/` (plus a few auth ones in `templates/account/email/`) and go out through the notification spine (`emit()`) or the helpers in `classes/emails.py` / `core/email.py`. Getting these right has burned us repeatedly — a "reminder" or "confirmation" that's a dead skeleton: the class name as plain text, one vague button, nothing that helps the reader actually do the next thing. The bar: **every email helps the recipient act, not just informs them.**

> Inline styles are the **exception to Rule 9 here.** Email clients strip `<style>` blocks and external CSS, so email templates style inline on purpose — match the existing shell, don't "fix" it into classes.

Mandatory for every transactional / notification email:

- **Link the subject noun.** The thing the email is about — class title, guild name, event name, the registration/order — is a **clickable link to its detail or management page**, never dead text. (The class reminder links `{{ offering.title }}` to the public class page; do the same for guild names → guild page, events → the calendar, receipts → billing.)
- **One obvious primary CTA, plus the helpful secondary links.** A reminder links to *Manage Registration* **and** *see full class details (what to bring, parking)*; a receipt links to billing history; a time-sensitive email offers add-to-calendar. Don't make the reader go hunting for the next step.
- **Surface the human content.** If a person wrote something relevant — an instructor's welcome/prep note, a guild lead's message — show it; don't send the bare scaffold. Guard it (`{% if offering.welcome_email_ready %}`) so it only appears when set.
- **Absolute URLs only.** Build links with the `_absolute_url()` helper (book-site base) or the spine's absolute-URL resolver — never a bare `/path`, which dead-ends in a mail client.
- **Branded shell, no "BETA".** Use the branded layout (`templates/membership/emails/_base.html`); the app is past beta — no stale BETA badge in the header.
- **Never ship a text-only email.** Every email has an HTML body in the branded shell — set `html_template` / `html_body` (a `.txt` fallback is fine, but `html_template=None` / a bare `Message(body=…)` ships unbranded plain text). For a flat-text body, wrap it with `_flat_text_email_html()`.
- **Copy-mode (spine) emails must be *styled* by the shell, not just wrapped by it.** The default copy in `core/events/copy.py` is bare `<p>`/`<a>` with no color — on the dark `#092E4C` card that renders black text and default-blue links (color doesn't inherit into `<a>`). `core/events/templates.py::_style_copy_fragment` + the `notification_shell.html` wrapper inject the cream/gold styling centrally; if you add a new spine event, verify its email renders cream-on-dark with gold links (not black-on-dark).
- **Subject and body agree on timezone** (project / Portland). A subject rendered in UTC over a body in local time is a bug.
- **Keep `.txt` and `.html` in sync.** Every email has both; change one, change the other.

Canonical example to copy: `templates/classes/emails/reminder.{html,txt}` and its builder `build_class_reminder_occurrence` in `classes/emails.py` — it adds `class_url` via `_absolute_url(reverse("classes:public_class_detail", …))`, links the title, surfaces the instructor note, and links to the full details.

## Rules for Claude / AI Agents

1. **Always use `components/form_field.html`** for form fields — never render raw `{{ field }}` with manual label/error HTML.
2. **Always use `components/modal.html`** for modals — never build one-off modal HTML with custom overlay/backdrop.
3. **Always use `components/toggle.html`** for boolean fields — never render checkboxes directly or build custom toggle HTML. **Exception:** a formset's `DELETE` field is not a user-facing boolean — render it hidden behind a real Delete button (see *Editable Lists & Destructive Actions*).
4. **Use the `pl-` CSS prefix** for all new component classes. Never add classes with other prefixes.
5. **Quick forms (1-3 fields) → modal.** Longer forms → inline or dedicated page.
6. **After mutating actions, return a toast** via `trigger_toast()`. Don't redirect with Django messages for HTMX requests.
7. **Test both dark and light themes** when adding new CSS.
8. **Card layout:** Wrap content sections in `<div class="hub-card">` for hub pages.
9. **No inline styles** except for truly one-off layout adjustments. Add a CSS class instead.
10. **Image placeholders:** When designing product cards or profile sections, leave space for future image support but don't build upload infrastructure.
11. **Editing a list of rows, or deleting something?** Follow *Editable Lists & Destructive Actions* — `extra=0` + a "+ Add" button, real Delete buttons (never toggles) that save the page, margin-spaced so they clear the field above.
12. **Never put `display` in an inline `style` on an `x-show` element.** Alpine's `x-show` *removes* the inline `display` property when it reveals the element, so inline `display:flex`/`grid` silently reverts to the default on first show (collapsing flex columns, etc.). Put the layout in a CSS class — Alpine only toggles `display:none` on/off and the class provides the real display. (This bit the orientation slot table: only the header — which had no `x-show` — kept its columns.)
13. **Never inline-style a form control (`<select>`/`<input>`/`<textarea>`) with `background`/`color`.** Give it a CSS class that uses the theme's input tokens, or scope it under an existing field wrapper — `.hub-form-group` on hub pages, `.reg-field` on public-classes pages, `.bk-field` on book-account pages — which already style any `input`/`select`/`textarea` inside them. (A bare, un-wrapped `<textarea>` on a hub page renders as a browser-default white box — wrap it in `.hub-form-group`.) Valid input tokens: `--hub-input-bg` / `--hub-input-border` / `--text` (hub + public-classes pages) and `--bk-input-bg` / `--bk-input-color` (book-account pages). **`--surface` is NOT a defined token** — `background:var(--surface,#fff)` silently falls back to white, so the control renders as a white box with near-invisible light text on the dark theme. Also style `select option { background; color }` — native option popups don't inherit the select's colors. (This is what broke the registration "Choose your dates" dropdown and the orientation "decline note" textarea.)
14. **Native `<input type="date">` / `<input type="time">` need dark-mode help.** The browser's picker icon (`::-webkit-calendar-picker-indicator`) is black by default — invisible on the dark theme. Invert it (`filter: invert(1)`) and reset it under `[data-theme="light"]` (`filter: none`). Also let the whole field open the picker, not just the icon: `@click="try { $event.currentTarget.showPicker() } catch (e) {}"` (the `try/catch` swallows the harmless "already open" error thrown when the icon itself is clicked). (This was the session-scheduler date/time/Duration popover.)
15. **Building or editing an email?** Follow *Email Templates* above — link the subject noun (class/guild/event/order) to its page, give one clear CTA plus the obviously-helpful secondary links, surface any human-written note (guarded), use absolute URLs and the branded shell (no "BETA"), keep subject/body in one timezone, and keep `.txt` + `.html` in sync. Inline styles are expected in emails (clients strip external CSS) — the one place Rule 9 doesn't apply.
16. **Image-upload fields use the draggable upload component — never a bare `<input type="file">`.** Every image field gets the drag-and-drop drop zone: either `components/image_field.html` or the local `.cls-image-upload-zone` pattern (a hidden file input inside a `<label class="cls-image-upload-zone">` with `.cls-image-upload-label` / `.cls-image-upload-hint`, and a `.drag-hover` class toggled while dragging). Always pair it with a **recommended-size tooltip** via the `.pl-help` `?` bubble — e.g. signage slides: "1920×1080 (16:9), JPG or PNG. Up to ~2400px wide." A raw `{{ field }}` file input renders the browser-default control with no drag target and no guidance. **In a formset that clones rows client-side** (a "+ Add" button copies a `<template>`'s `innerHTML`), do NOT rely on `image_field.html`'s per-field inline `<script>` — cloned `innerHTML` never executes its scripts, so a freshly-added row's drag/preview would be dead. Drive every zone from a single **delegated** script bound to the rows container (`change`/`dragover`/`dragleave`/`drop`), like the Slideshow Slides editor in `templates/hub/admin/site_settings.html`.
17. **`{# … #}` is single-line ONLY — a comment that wraps to a second line RENDERS as visible text on the page.** Django's template lexer only recognizes `{# … #}` when the open and close are on the same line; the moment a newline falls between `{#` and `#}`, the whole thing stops being a comment and prints verbatim (e.g. the literal `{# Member-event policy + … #}` that showed up on the Site Settings page). For any comment longer than one line, use `{% comment %} … {% endcomment %}`. Keep `{# … #}` for short single-line notes only. (This has now leaked to real pages FOUR times — `tests/template_comment_lint_spec.py` fails the suite on any multi-line `{# #}`, so run it before committing template changes.)
18. **Buttons never touch an adjacent section.** A submit/action button at the end of a card or form gets clear breathing room (≥ `1.5rem` margin) before the next section heading or card begins — a button visually butting against the following section's title reads as belonging to it. Check the rendered page, not the template. (This bit "Save orientation settings" sitting flush against the Recurring Hours heading.)
19. **A "?" help tooltip is the `.pl-help` bubble — never a native `title=` attribute or a hand-rolled popover.** Wrap a `.pl-help__icon` and a `.pl-help__bubble` inside `.pl-help` (see *Help tooltip* in the Component Library). It is CSS-only, so unlike the Community Calendar's one-off inline Alpine popover it works even on pages without Alpine (e.g. the token email-prefs page). Add `pl-help--right` when the icon sits in a narrow right-hand column. (This bit the notifications-matrix Push tooltip, first shipped as a bare `title=` attribute.)
20. **Per-minute time pickers are DISCONTINUED. Never use `<input type="time">` for scheduling.** Nobody schedules a meeting at 6:07 — every time-of-day field is a plain `<select>` of half-hour increments (15-minute only where a real need exists), like the guild "Meeting time" dropdown. Use the shared half-hour choices for new time fields; don't invent a new list. Native time inputs also drag in the Rule 14 dark-mode picker fixes for no benefit. (Duration stays a dropdown of sensible lengths, as in the session scheduler.)
21. **The Save button is the LAST thing in a form, and it just says "Save".** Never place inputs, toggles, or other controls *below* a save/submit button — a Save with fields stranded underneath reads as broken and users miss the controls beneath it. When a tab mixes a batch-save form with immediate-effect controls (e.g. an action toggle), put the immediate controls FIRST and the save form LAST so its button sits at the very bottom with nothing under it. Label the primary submit button simply **"Save"** — not "Save member" / "Save capabilities" / "Save preferences"; the surrounding tab/heading already says what's being saved. (This is why the member-edit Permissions tab renders the Instructor toggle before the capabilities form, and every save button on that page reads "Save".)
22. **Section and card headings use Title Case — Capitalize Every Word.** A heading like "How it goes out" reads as unfinished; write "How It Goes Out". This is a site-wide copy convention for `<h1>`/`<h2>`/`<h3>` section titles and card headers (NOT body copy, hints, or sentence-style descriptions). Where a heading is generated or awkward to retitle string-by-string, enforce it with `text-transform: capitalize` on the heading's CSS class. The announcement composer's section titles (`.pl-compose-section__title` / `.pl-compose-channel__title`) do exactly this, rendered in the brand gold (`--color-tuscan-yellow`) at a larger size so section headers actually read as headers.

## CSS Files

| File | Scope | What goes here |
|------|-------|---------------|
| `static/css/style.css` | Public pages (login, signup, landing) | Auth forms, hero, navigation |
| `static/css/hub.css` | Member hub | Hub layout, sidebar, topbar, page-specific styles |
| `static/css/components.css` | Shared (hub + admin) | All reusable component styles (modal, toast, toggle, etc.) |
| `static/css/unfold-custom.css` | Admin only | Unfold overrides, admin-specific layouts |
