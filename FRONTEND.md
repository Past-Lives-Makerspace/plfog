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

No build step. No npm. No bundler. No utility framework. All JS is loaded via `<script>` tags. A move to Tailwind has been proposed and nothing from it has shipped; until something does, this document describes the only stack in the repo, so write custom CSS under the tokens below.

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
- Visibility stays CSS-only. On desktop, `static/js/pl_help.js` (loaded once from `hub/base.html`) lifts the shown bubble out of any clipping ancestor by re-anchoring it to the viewport (`position: fixed`, clamped to the viewport gutters), so `.pl-help` is safe inside `.admin-table-wrap` and every `overflow: auto` table wrapper. Nothing to bind per page.

### Disclosure (`.pl-disclosure`)

The hub's `<details>` pattern: a bordered summary row with a title, an optional hint under it, and a chevron that points down when closed and up when open; the body continues the same card. Use it for anything a member opens and closes on a hub page (the Host a Workshop questions and hosting guide). It is inline markup in `hub.css`, **not** a `components/` include. Never hide the native marker without replacing it with the chevron: a summary with no open indicator reads as plain text and nobody clicks it.

```html
<details class="pl-disclosure">
  <summary class="pl-disclosure__summary">
    <span class="pl-disclosure__text">
      <span class="pl-disclosure__title">Read the Hosting Guide</span>
      <span class="pl-disclosure__hint">One line about what is inside. Tap to open.</span>
    </span>
    <span class="pl-disclosure__chevron" aria-hidden="true"></span>
  </summary>
  <div class="pl-disclosure__body">…</div>
</details>
```

- Drop `.pl-disclosure__text` and put `.pl-disclosure__title` straight in the summary when there is no hint; a leading badge or icon span (`.pl-teach-faq__badge`, `.pl-teach-guide__icon`) goes first.
- Add `open` to the first item of an accordion so the section never looks empty.

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
- Always add `pl-btn--spaced` so it clears the field/toggle above it. The class is `margin-top: 0.75rem` in `components.css`; do not write that as an inline style. A delete button flush against the last field is a bug, not a detail to fix later.

### Deleting a saved row saves the whole page (no lost work)

For a row already in the DB (`form.instance.pk`), the Delete button flips the hidden `DELETE` field and submits the form, so every other edit on the page is preserved:

```html
{% if form.instance.pk %}
  <div style="display:none;">{{ form.DELETE }}</div>
  <button type="button" class="pl-btn pl-btn--danger pl-btn--sm pl-btn--spaced"
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
| Optional / secondary form on a page | Toggle button reveals it (`x-show`, closed by default) | The Spaces "Request this space" form (`hub/partials/_space_request_form.html`) |

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

### Scripts under hx-boost

`hub/base.html` boosts the whole body (`hx-boost="true"` + the `head-support` extension), so an in-app navigation is an htmx **body swap**: `<head>` is merged tag by tag (a tag the next page also carries is kept, new ones are appended and run, missing ones are removed) and `<body>` is replaced, with every script tag in the new body duplicated and executed again. Where a script sits therefore decides how many times it runs.

| Script | Where | Why |
|--------|-------|-----|
| htmx, head-support, Alpine, `hub_boot.js`, `biometric-auth.js`, `pl_help.js` | `hub/base.html` `<head>`, `defer` | Run **once per document**. A second htmx replaces `window.onpopstate` and breaks Back; a second Alpine initialises the page before page scripts have registered their components. Never load either again from a page. |
| **Every** static file that registers an `Alpine.data(...)` component (`hero_placement.js`, `session_calendar.js`, `card_focus.js`, `space_map.js`), even one only a single page uses | `hub/base.html` `<head>`, `defer`, **before** `alpine.min.js` | Alpine initialises a swapped page a microtask after htmx inserts it, before any script the page itself loads could arrive, so the component has to be registered before the navigation starts. A body or `extra_head` copy is dead on every in-app arrival (`cardFocus is not defined`). Register defensively, on whichever side of `alpine:init` the script lands: `if (window.Alpine) register(); else document.addEventListener('alpine:init', register);` |
| An inline `<script>` in a page or partial that registers a component or defines a function `x-data` calls | In the body content, as today | htmx runs it synchronously as it inserts the page, before Alpine initialises anything. Use the same defensive registration (`hub/wiki_edit.html`, `hub/meeting_workspace.html`); a plain `alpine:init` listener never fires on a boosted arrival. |
| Anything else in `<body>` (`_tour.html`, the toast and loading bar scripts, `hero_cropper.js`) | Body | Re-runs on **every** boosted arrival. Make it a guarded IIFE (`if (window.__x) return; window.__x = true;`) or delegate on `document`; per-page boot logic that needs the fresh DOM belongs here on purpose. |

head-support keys head tags by their exact `outerHTML`, and the static storage hashes filenames, so the first boosted arrival after a deploy re-appends and executes every head script whose hash changed: harmless for the component files (a second registration overwrites the first), but an htmx or Alpine upgrade recreates the second-instance state for that document until the next full page load. Never boost into another document either: the Django admin loads its own Alpine, so every anchor into `/admin/` from a hub page carries `hx-boost="false"`.

Never `document.body.addEventListener(...)` from a head script at parse time (there is no body yet); listen on `document`, htmx events bubble there. `tests/hub/base_scripts_spec.py` pins the head order, fails if htmx or Alpine ever lands back inside `<body>`, and fails if a `static/js` file that calls `Alpine.data(` is missing from the head; `tests/e2e/boosted_navigation_spec.py` drives the real boosted arrival at the composer.

**Two rules for a body script's own bookkeeping**, both learned from bugs that looked correct on a single visit (issues #382, #383):

- **A "has this run already" flag for something bound to `document` goes on `window`, never in the IIFE.** The whole file is re-executed in a fresh scope on every boosted arrival, so a `var bound = false` is rebuilt as `false` each time and guards nothing. `window.plRteSettleBound`, `window.plMapEditorDocumentBound`.
- **A "have I claimed this node" key is a property on the element, not a `data-` attribute.** `element.plThingReady = true`, not `element.dataset.thingReady = "1"`. See `readyOnce` in `rich-editor-init.js` and `space_map_editor.js`. An attribute is markup, and markup gets serialized, so a node can come back from somewhere already claimed and the init silently does nothing. A property lives on the element object and dies with the node. (`pl_tour.js` still keys its two guards on the `data-pl-init` and `data-pl-tour-init` attributes. They are safe **only** because the rule below removed the one thing that serialized them; if a snapshot ever returns, they break the same way.)

**A view that answers some requests with a fragment and can be reached by a GET must use `core.htmx.wants_fragment`**, not a hand-written header check. `HX-Request` alone is not enough (a boosted navigation carries it too), and excluding `HX-Boosted` is only half the answer: since Back is a refetch, a history restore is *precisely* a non-boosted `HX-Request`, so a check that only excludes boosted requests hands the member a bare fragment where their page used to be. That shipped once on the public class catalog: 341,276 bytes with a document, 339 without. A restore is a GET, so `@require_POST` views cannot be reached this way; the six that still hand-roll the check (`registration_send_payment_link`, `registration_mark_paid` and `registration_confirm_pending` in `classes/views.py`; `hub_equipment_orientation_hours_save`, `guild_orientation_hours_save` and `hub_wiki_verify` in `hub/`) are correct for that reason, not because the check is fine. Drop the decorator and they are wrong.

The hub keeps **no htmx history cache** (`hub_boot.js` sets `htmx.config.historyCacheSize = 0`), because a snapshot of this body is a snapshot of Alpine's and Quill's output rather than of the markup the server sent: restoring it duplicated every `x-for` expansion in the class composer and brought back a rich-text editor that looked mounted and swallowed everything typed into it. Back is a refetch, so what a member returns to is server markup that Alpine initialises once. Do not add an `hx-history-elt` or otherwise reintroduce the snapshot.

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
9. **No inline styles.** Add a CSS class instead; spacing modifiers such as `pl-btn--spaced` exist so a one-off margin never needs a `style=`. The one place inline styling is correct is an email template (Rule 15). A `<style>` block inside a template's `extra_head` fails the pre-push hook (`scripts/check_no_inline_style_in_extra_head.py`).
10. **Image placeholders:** When designing product cards or profile sections, leave space for future image support but don't build upload infrastructure.
11. **Editing a list of rows, or deleting something?** Follow *Editable Lists & Destructive Actions* — `extra=0` + a "+ Add" button, real Delete buttons (never toggles) that save the page, with `pl-btn--spaced` so they clear the field above.
12. **Never put `display` in an inline `style` on an `x-show` element.** Alpine's `x-show` *removes* the inline `display` property when it reveals the element, so inline `display:flex`/`grid` silently reverts to the default on first show (collapsing flex columns, etc.). Put the layout in a CSS class — Alpine only toggles `display:none` on/off and the class provides the real display.
13. **Never inline-style a form control (`<select>`/`<input>`/`<textarea>`) with `background`/`color`.** Give it a CSS class that uses the theme's input tokens, or scope it under an existing field wrapper — `.hub-form-group` on hub pages, `.reg-field` on public-classes pages, `.bk-field` on book-account pages — which already style any `input`/`select`/`textarea` inside them. (A bare, un-wrapped `<textarea>` on a hub page renders as a browser-default white box — wrap it in `.hub-form-group`.) Valid input tokens: `--hub-input-bg` / `--hub-input-border` / `--text` (hub + public-classes pages) and `--bk-input-bg` / `--bk-input-color` (book-account pages). **`--surface` is NOT a defined token** — `background:var(--surface,#fff)` silently falls back to white, so the control renders as a white box with near-invisible light text on the dark theme. Also style `select option { background; color }` — native option popups don't inherit the select's colors.
14. **Native `<input type="date">` / `<input type="time">` need dark-mode help.** The browser's picker icon (`::-webkit-calendar-picker-indicator`) is black by default — invisible on the dark theme. Invert it (`filter: invert(1)`) and reset it under `[data-theme="light"]` (`filter: none`). Let the whole field open the picker, not just the icon: `@click="(() => { try { $el.showPicker() } catch (e) {} })()"`. The arrow IIFE matters: an Alpine handler is an **expression**, so a bare `try { … }` statement is a SyntaxError on every render. `tests/alpine_expression_lint_spec.py` enforces that half.
15. **Building or editing an email?** Follow *Email Templates* above — link the subject noun (class/guild/event/order) to its page, give one clear CTA plus the obviously-helpful secondary links, surface any human-written note (guarded), use absolute URLs and the branded shell (no "BETA"), keep subject/body in one timezone, and keep `.txt` + `.html` in sync. Inline styles are expected in emails (clients strip external CSS) — the one place Rule 9 doesn't apply.
16. **Image-upload fields use the draggable upload component — never a bare `<input type="file">`.** Every image field gets the drag-and-drop drop zone: either `components/image_field.html` or the local `.cls-image-upload-zone` pattern (a hidden file input inside a `<label class="cls-image-upload-zone">` with `.cls-image-upload-label` / `.cls-image-upload-hint`, and a `.drag-hover` class toggled while dragging). Always pair it with a **recommended-size tooltip** via the `.pl-help` `?` bubble — e.g. signage slides: "1920×1080 (16:9), JPG or PNG. Up to ~2400px wide." A raw `{{ field }}` file input renders the browser-default control with no drag target and no guidance. **In a formset that clones rows client-side** (a "+ Add" button copies a `<template>`'s `innerHTML`), do NOT rely on `image_field.html`'s per-field inline `<script>` — cloned `innerHTML` never executes its scripts, so a freshly-added row's drag/preview would be dead. Drive every zone from a single **delegated** script bound to the rows container (`change`/`dragover`/`dragleave`/`drop`), like the Slideshow Slides editor in `templates/hub/admin/site_settings.html`.
17. **`{# … #}` comments are single-line only.** A `{# #}` that wraps to a second line renders as visible text on the page. Use `{% comment %} … {% endcomment %}` for anything longer. Enforced by `tests/template_comment_lint_spec.py`.
18. **Buttons never touch an adjacent section.** A submit/action button at the end of a card or form gets clear breathing room (≥ `1.5rem` margin) before the next section heading or card begins — a button visually butting against the following section's title reads as belonging to it. Check the rendered page, not the template.
19. **A "?" help tooltip is the `.pl-help` bubble — never a native `title=` attribute or a hand-rolled popover.** Wrap a `.pl-help__icon` and a `.pl-help__bubble` inside `.pl-help` (see *Help tooltip* in the Component Library). It is CSS-only, so unlike the Community Calendar's one-off inline Alpine popover it works even on pages without Alpine (e.g. the token email-prefs page). Add `pl-help--right` when the icon sits in a narrow right-hand column.
20. **Per-minute time pickers are DISCONTINUED. Never use `<input type="time">` for scheduling.** Nobody schedules a meeting at 6:07 — every time-of-day field is a plain `<select>` of half-hour increments (15-minute only where a real need exists), like the guild "Meeting time" dropdown. Use the shared half-hour choices for new time fields; don't invent a new list. Native time inputs also drag in the Rule 14 dark-mode picker fixes for no benefit. (Duration stays a dropdown of sensible lengths, as in the session scheduler.)
21. **The Save button is the LAST thing in a form, and it just says "Save".** Never place inputs, toggles, or other controls *below* a save/submit button — a Save with fields stranded underneath reads as broken and users miss the controls beneath it. When a tab mixes a batch-save form with immediate-effect controls (e.g. an action toggle), put the immediate controls FIRST and the save form LAST so its button sits at the very bottom with nothing under it. Label the primary submit button simply **"Save"** — not "Save member" / "Save capabilities" / "Save preferences"; the surrounding tab/heading already says what's being saved. The member-edit Permissions tab is the reference layout.
22. **Section and card headings use Title Case — Capitalize Every Word.** A heading like "How it goes out" reads as unfinished; write "How It Goes Out". This is a site-wide copy convention for `<h1>`/`<h2>`/`<h3>` section titles and card headers (NOT body copy, hints, or sentence-style descriptions). Where a heading is generated or awkward to retitle string-by-string, enforce it with `text-transform: capitalize` on the heading's CSS class. The announcement composer's section titles (`.pl-compose-section__title` / `.pl-compose-channel__title`) do exactly this, rendered in the brand gold (`--color-tuscan-yellow`) at a larger size so section headers actually read as headers.
23. **Scripts on hub pages follow *Scripts under hx-boost*.** htmx and Alpine load once from `hub/base.html`; a static file that registers an Alpine component loads from that `<head>`, deferred, before Alpine, and registers on whichever side of `alpine:init` it lands; a body script re-runs on every boosted navigation. Enforced by `tests/hub/base_scripts_spec.py` (head order, nothing loads htmx or Alpine twice, every `Alpine.data` file is in the head) and driven end to end by `tests/e2e/boosted_navigation_spec.py`.

## CSS Files

All under `static/css/`. The pattern in use: one stylesheet per surface, linked from that surface's base or page template; anything two surfaces share goes in `components.css`. Add a new page's styles to a page file, not to `hub.css`.

| File | Loaded by | What goes here |
|------|-----------|----------------|
| `components.css` | `hub/base.html`, `admin/base.html` | Every reusable `pl-` component: buttons, modal, toast, toggle, form fields, help bubble. Shared by hub and admin. |
| `hub.css` | `hub/base.html` and the class screens | Hub layout, sidebar, topbar, and the hub page styles that accumulated before per-page files existed. It is the monolith (over 300 KB); add nothing page-specific to it. |
| `style.css` | `base.html` | Public pages: login, signup, landing. |
| `unfold-custom.css` | `plfog/settings.py` (Unfold config) | Django admin overrides only. |
| `cms-public.css` | `classes/base_public.html`, `guilds/base_public.html` | The public class catalog and public guild pages. |
| `cms-guilds.css` | `guilds/base_public.html` | Public guild pages, on top of `cms-public.css`. |
| `classes-register.css` | `classes/public/register.html` | The public registration form. |
| `book-account.css` | `account/*.html` | Login code and signup screens. |
| `class-flyer.css`, `guild-flyer.css` | the two flyer pages | Printable flyers. |
| `calendar.css`, `session-calendar.css` | community calendar, guild pages, class composer | Calendar grids. |
| `member-edit.css` | `hub/admin/member_edit.html` | One admin page. |
| `leadership.css` | `hub/leadership_directory.html`, `hub/admin/leadership.html` | The Leadership Directory cards and its admin page. |
| `voting-admin.css` | `hub/admin/voting_*.html` | Voting admin pages. |
| `notifications-catalogue.css`, `notifications-edit-copy.css`, `notifications-edit-discord.css` | `hub/admin/notifications/*.html` | Notification admin pages. |
| `signage.css` | `signage/base.html` | The lobby signage display. |
| `wiki-stickers.css` | `hub/wiki_sticker_sheet.html` | The QR sticker sheet. |
| `rich-editor.css` | `_components/rich_editor_assets.html` | Styling around the Quill editor. |
| `privacy.css` | `core/privacy_policy.html` | One page. |
| `quill.snow.css`, `driver.css` | rich editor assets, `hub/partials/_tour.html` | Vendor: the Quill theme and the driver.js tour. Do not edit. |
