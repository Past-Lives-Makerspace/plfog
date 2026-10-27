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

Spacing is generous by default: nothing touches. Rule 26.

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
- `field_tooltip` — plain text for a `?` help bubble (`.pl-help`) beside the label, outside it so the bubble never joins the field's accessible name

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

The hub's `<details>` pattern: a bordered summary row with a title, an optional hint under it, and a chevron that points down when closed and up when open; the body continues the same card. Use it for anything a member opens and closes on a hub page (the Host a Class questions and hosting guide). It is inline markup in `hub.css`, **not** a `components/` include. Never hide the native marker without replacing it with the chevron: a summary with no open indicator reads as plain text and nobody clicks it.

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

### Tab row and section picker (`{% tab_picker %}`)

**Rule: every row of tabs or steps is a section picker on a phone.** Write a new row inside `{% tab_picker %}` from the start, or, for tabs nested under another row, through `{% class_sections %}`. Never add a bare `.pl-tabs` row, a tab or step bar of your own, or any tab row that scrolls sideways on a phone: nobody can tell it scrolls, so the tabs past the edge go unseen (#852). `tests/components/tab_rows_spec.py` fails a template with a row outside the tag.

A row of tabs is the shared `.pl-tabs` strip of `.vote-tab` links (`aria-current="page"` on the current one) or in-page Alpine buttons; a composer's step bar is `.pl-phase-tabs` of `.pl-phase-tab` buttons (the class composer and Email Members), wrapped the same way. On a phone a long row hid most of its tabs off the right edge, so every top level row is wrapped in `{% tab_picker "<eyebrow>" %}` (`core/templatetags/tab_picker.py`, `{% load tab_picker %}`): the row renders exactly as written for 640px and up, and below 640px it hides and `components/tab_picker.html` takes its place, one row naming the group (the eyebrow), the current section and "n of N" that opens a list of every section. The picker reads its sections out of the row it wraps, so the `{% if %}`s that decide who gets which tab stay on the row and the two can never disagree; a row with fewer than two tabs gets no picker.

```html
{% tab_picker "Guild Sections" %}
<nav role="tablist" class="pl-tabs">…</nav>
{% endtab_picker %}
```

- A row of links works with no script (`<details>`); a row of buttons is mirrored in the browser by `plTabPicker` (`static/js/tab_picker.js`), whose choices press the row's own button, so the pane switches as a desktop click would. Both close on Escape and on a tap outside.
- Keep a count in the label as "Label (3)" or in a child whose class names a count (`pl-kiln-count`); the picker shows it apart.
- Wrap only the row, not an `{% if %}` around it: the tag fails loudly when it wraps no `.pl-tabs` or `.pl-phase-tabs` row.
- A step's done tick and error dot are children with a `__mark` class (`pl-phase-tab__mark`) with `aria-invalid` on the step; the picker lists them beside each step, never in its label.
- A row nested under another (the per-class tabs under the Classes portal row) is not a picker: nested navigation must never look the same twice. The class screen's own tabs come from `{% class_sections %}` (`classes/_components/class_sections.html`), which on a phone shows a list of every other section with a one line hint on the Overview and a back row ("‹ Class Overview", where you are, "n of N") on every other tab.
- Filter chips are not tabs: `.pl-wp-chiprow` scrolls by itself and never borrows `.pl-tabs`.

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

For a row already in the DB (`form.instance.pk`), the Delete button flips the hidden `DELETE` field and submits the form, so every other edit on the page is preserved (the Help editor's FAQ rows in `templates/hub/org_info_edit.html`):

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

Canonical implementations to copy: the Help editor's FAQ and Links rows in `templates/hub/org_info_edit.html`.

### A list that saves itself (the guild settings idiom)

On a page whose forms autosave (`templates/hub/guild_edit.html`, driven by `static/js/guild_autosave.js` and answered by `hub/autosave.py`) there is no submit at all: the form carries `data-autosave` (or `data-autosave="change"` to skip the typing pause), `data-formset="<prefix>"` and `data-formset-required="{{ formset.empty_form|required_fields }}"`; each row is `[data-formset-row]`, the rows live in `[data-formset-rows]`, the clone source is `<template data-formset-template>`, and the add and the per row Delete / Remove buttons are `data-formset-add` / `data-formset-remove` with no `onclick`. The script posts the whole form on every change, stamps each returned pk into the row's hidden `id`, renumbers the rows saved first, and on Delete flips the row's `DELETE` field (made on the fly for a row saved since the page loaded) and posts; a form with `data-autosave-confirm="<confirm_id>"` opens that confirm first, whose `confirm_js` dispatches `pl-guild-autosave-delete`. A half typed new row (a required field still blank) is posted as it rendered, so Django skips it while the rest of the form saves. A cloned row's inline scripts are swapped for fresh copies once it is in the page (`runInlineScripts`), so `components/image_field.html` works in a row added with "+ Add" (the Orientation Types photo, #502).

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

### Inputs That Take Room Start Collapsed

When a new input takes a lot of space (a list of pills or checkboxes, a formset row, a block of fields), the page shows the chosen values and an **Edit** button, and the controls expand only when the member interacts with it. A page full of open controls buries everything around it; the values are what a returning manager came to read.

- **Collapsed is one line:** the row's label, its chosen values (read only pills, or a short summary such as "Mon, Wed · 9:00 AM to 5:00 PM"), and an Edit button on the right (`pl-btn pl-btn--secondary pl-btn--sm`, `aria-expanded`, reading "Edit" while closed and "Done" while open). Show an honest empty state ("Nothing ticked yet") rather than a blank line.
- **The fields stay in the DOM.** Hide the body with `x-show` (plus `x-cloak` when it starts closed), never `x-if`, so one Save still posts every row. Layout goes in a CSS class, never an inline `display` (Rule 12). Give the hidden body no `id`: on a boosted Save, htmx settles an element whose `id` was also on the old page by writing the server's `style` back after Alpine has already hidden it, so a saved row comes back open.
- **A new or errored row starts open.** A row just added with "+ Add", the empty `<template>` it is cloned from, and every row after a Save that failed on them render open, so the member sees what to fill in or fix. A saved row starts collapsed.
- **The summary follows the edit.** Build it from the row's own controls in the browser, with the same words the server writes, so it is right when the member presses Done without saving.
- Delete and Remove live inside the expanded body (*Editable Lists & Destructive Actions*).

References to copy: the Opening Hours row (`templates/hub/partials/_equipment_hours_row.html`, its `plEquipHoursRow` component in `templates/hub/equipment_manage.html`), the collapsed orientations (#744, `templates/hub/partials/_orientation_row_summary.html` with `static/js/orientation_row.js`), and the Ways to Qualify editor (`templates/hub/partials/_equipment_way.html`, `plEquipWays` in `static/js/equipment_ways.js`).

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
| **Every** static file that registers an `Alpine.data(...)` component (`hero_placement.js`, `session_calendar.js`, `card_focus.js`, `space_map.js`, `guild_autosave.js`, `list_calendar.js`, `orientation_row.js`, `equipment_ways.js`, `inquiry_member_picker.js`, `hq_people_picker.js`, `tab_picker.js`, `pl-rotator.js`, `pl-upcoming-queue.js`), even one only a single page uses | `hub/base.html` `<head>`, `defer`, **before** `alpine.min.js` | Alpine initialises a swapped page a microtask after htmx inserts it, before any script the page itself loads could arrive, so the component has to be registered before the navigation starts. A body or `extra_head` copy is dead on every in-app arrival (`cardFocus is not defined`). Register defensively, on whichever side of `alpine:init` the script lands: `if (window.Alpine) register(); else document.addEventListener('alpine:init', register);` |
| An inline `<script>` in a page or partial that registers a component or defines a function `x-data` calls | In the body content, as today | htmx runs it synchronously as it inserts the page, before Alpine initialises anything. Use the same defensive registration (`hub/wiki_edit.html`, `hub/meeting_workspace.html`); a plain `alpine:init` listener never fires on a boosted arrival. |
| Anything else in `<body>` (`_tour.html`, the toast and loading bar scripts, `hero_cropper.js`) | Body | Re-runs on **every** boosted arrival. Make it a guarded IIFE (`if (window.__x) return; window.__x = true;`) or delegate on `document`; per-page boot logic that needs the fresh DOM belongs here on purpose. |

head-support keys head tags by their exact `outerHTML`, and the static storage hashes filenames, so the first boosted arrival after a deploy re-appends and executes every head script whose hash changed: harmless for the component files (a second registration overwrites the first), but an htmx or Alpine upgrade recreates the second-instance state for that document until the next full page load. Never boost into another document either: the Django admin loads its own Alpine, so every anchor into `/admin/` from a hub page carries `hx-boost="false"`.

Never `document.body.addEventListener(...)` from a head script at parse time (there is no body yet); listen on `document`, htmx events bubble there. `tests/hub/base_scripts_spec.py` pins the head order, fails if htmx or Alpine ever lands back inside `<body>`, and fails if a `static/js` file that calls `Alpine.data(` is missing from the head; `tests/e2e/boosted_navigation_spec.py` drives the real boosted arrival at the composer.

**Two rules for a body script's own bookkeeping**, both learned from bugs that looked correct on a single visit (issues #382, #383):

- **A "has this run already" flag for something bound to `document` goes on `window`, never in the IIFE.** The whole file is re-executed in a fresh scope on every boosted arrival, so a `var bound = false` is rebuilt as `false` each time and guards nothing. `window.plRteSettleBound`, `window.plMapEditorDocumentBound`.
- **A "have I claimed this node" key is a property on the element, not a `data-` attribute.** `element.plThingReady = true`, not `element.dataset.thingReady = "1"`. See `readyOnce` in `rich-editor-init.js` and `space_map_editor.js`. An attribute is markup, and markup gets serialized, so a node can come back from somewhere already claimed and the init silently does nothing. A property lives on the element object and dies with the node. (`pl_tour.js` still keys its two guards on the `data-pl-init` and `data-pl-tour-init` attributes. They are safe **only** because the rule below removed the one thing that serialized them; if a snapshot ever returns, they break the same way.)

**A view that answers some requests with a fragment and can be reached by a GET must use `core.htmx.wants_fragment`**, not a hand-written header check. `HX-Request` alone is not enough (a boosted navigation carries it too), and excluding `HX-Boosted` is only half the answer: since Back is a refetch, a history restore is *precisely* a non-boosted `HX-Request`, so a check that only excludes boosted requests hands the member a bare fragment where their page used to be. That shipped once on the public class catalog: 341,276 bytes with a document, 339 without. A restore is a GET, so `@require_POST` views cannot be reached this way; the six that still hand-roll the check (`registration_send_payment_link`, `registration_mark_paid` and `registration_confirm_pending` in `classes/views.py`; `hub_equipment_orientation_hours_save`, `guild_orientation_hours_save` and `hub_wiki_verify` in `hub/`) are correct for that reason, not because the check is fine. Drop the decorator and they are wrong.

**An element a script decorates carries no `id`.** On a swap, htmx copies the outgoing page's `class`, `style`, `width` and `height` onto every incoming element whose `id` is on both pages, then puts the server's values back on a 20ms settle timer. A boosted Save that redirects to its own page matches every id, so a class a body script adds during the swap is overwritten at settle. The rich-text mount carried an id and lost Quill's `ql-container ql-snow` that way after every thank-you email Save. Key such an element on a `data-` attribute (`data-rte-for`), or initialise it on `htmx:afterSettle`.

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
16. **Image-upload fields use the draggable upload component — never a bare `<input type="file">`.** Every image field gets the drag-and-drop drop zone: either `components/image_field.html` or the local `.cls-image-upload-zone` pattern (a hidden file input inside a `<label class="cls-image-upload-zone">` with `.cls-image-upload-label` / `.cls-image-upload-hint`, and a `.drag-hover` class toggled while dragging). Always pair it with a **recommended-size tooltip** via the `.pl-help` `?` bubble — e.g. signage slides: "1920×1080 (16:9), JPG or PNG. Up to ~2400px wide." A raw `{{ field }}` file input renders the browser-default control with no drag target and no guidance. **In a formset that clones rows client-side** (a "+ Add" button copies a `<template>`'s `innerHTML`), do NOT rely on `image_field.html`'s per-field inline `<script>` — cloned `innerHTML` never executes its scripts, so a freshly-added row's drag/preview would be dead. Drive every zone from a single **delegated** script bound to the rows container (`change`/`dragover`/`dragleave`/`drop`), like the Slideshow Slides editor in `templates/hub/admin/site_settings.html`, or have the "+ Add" code swap the cloned row's scripts for fresh copies after it inserts the row, as the Orientation Types editors do (`runInlineScripts` in `guild_autosave.js`).
17. **`{# … #}` comments are single-line only.** A `{# #}` that wraps to a second line renders as visible text on the page. Use `{% comment %} … {% endcomment %}` for anything longer. Enforced by `tests/template_comment_lint_spec.py`.
18. **Buttons never touch an adjacent section.** A submit/action button at the end of a card or form gets clear breathing room (≥ `1.5rem` margin) before the next section heading or card begins — a button visually butting against the following section's title reads as belonging to it. Check the rendered page, not the template.
19. **A "?" help tooltip is the `.pl-help` bubble — never a native `title=` attribute or a hand-rolled popover.** Wrap a `.pl-help__icon` and a `.pl-help__bubble` inside `.pl-help` (see *Help tooltip* in the Component Library). It is CSS-only, so unlike the Community Calendar's one-off inline Alpine popover it works even on pages without Alpine (e.g. the token email-prefs page). Add `pl-help--right` when the icon sits in a narrow right-hand column.
20. **Per-minute time pickers are DISCONTINUED. Never use `<input type="time">` for scheduling.** Nobody schedules a meeting at 6:07 — every time-of-day field is a plain `<select>` of half-hour increments (15-minute only where a real need exists), like the guild "Meeting time" dropdown. Use the shared half-hour choices for new time fields; don't invent a new list. Native time inputs also drag in the Rule 14 dark-mode picker fixes for no benefit. (Duration stays a dropdown of sensible lengths, as in the session scheduler.)
21. **The Save button is the LAST thing in a form, and it just says "Save".** Never place inputs, toggles, or other controls *below* a save/submit button — a Save with fields stranded underneath reads as broken and users miss the controls beneath it. When a tab mixes a batch-save form with immediate-effect controls (e.g. an action toggle), put the immediate controls FIRST and the save form LAST so its button sits at the very bottom with nothing under it. Label the primary submit button simply **"Save"** — not "Save member" / "Save capabilities" / "Save preferences"; the surrounding tab/heading already says what's being saved. The member-edit Permissions tab is the reference layout. **Exception: a save that sends something to members names it.** The event editor's button reads "Save and announce" or "Save and schedule" while saving would announce, and plain "Save" once the event is live; the propose page's "Publish event" and "Submit for review" do the same. The label is the promise, so take it from the same flag the view acts on (`CommunityEventForm.saves_announce`), never from a second guess in the template.
22. **Section and card headings use Title Case — Capitalize Every Word.** A heading like "How it goes out" reads as unfinished; write "How It Goes Out". This is a site-wide copy convention for `<h1>`/`<h2>`/`<h3>` section titles, card headers, button labels and the arrow links that close a card ("See Past Polls →", never "See past polls →"); NOT body copy, hints, or sentence-style descriptions. Small words (a, an, the, and, or, of, to, in, on, for, at, by, with, as, per, vs) stay lowercase unless first. The arrow links are enforced by `tests/more_links_title_case_spec.py`. Where a heading is generated or awkward to retitle string-by-string, enforce it with `text-transform: capitalize` on the heading's CSS class. Email Members' section titles (`.pl-compose-section__title` / `.pl-compose-channel__title`) do exactly this, rendered in the brand gold (`--color-tuscan-yellow`) at a larger size so section headers actually read as headers.
23. **Scripts on hub pages follow *Scripts under hx-boost*.** htmx and Alpine load once from `hub/base.html`; a static file that registers an Alpine component loads from that `<head>`, deferred, before Alpine, and registers on whichever side of `alpine:init` it lands; a body script re-runs on every boosted navigation. Enforced by `tests/hub/base_scripts_spec.py` (head order, nothing loads htmx or Alpine twice, every `Alpine.data` file is in the head) and driven end to end by `tests/e2e/boosted_navigation_spec.py`.
24. **A link to a download carries `hx-boost="false"`.** `hub/base.html` boosts every same-origin anchor and form under its body, so a link to an attachment (an `.ics`, a QR image, a CSV export, an uploaded document) is fetched over XHR and its raw text swapped into the page in place of a download, with the file's URL in the address bar. The `download` attribute alone changes nothing; htmx 2.0.4 does not look at it. The attribute is inert on a page that is not boosted, so every such link carries it and no template has to know its base. A link to one of **our own** attachments also carries `data-pl-download`: the Capacitor shells drop a download outright (no download handling on either platform yet), so `static/js/native-downloads.js` hides marked links inside the native app and does nothing in a browser. Links to uploaded documents are not marked, because they live on another host that the shells hand to the system. Enforced by `tests/download_links_lint_spec.py`, which registers every view that sets `Content-Disposition` (a new one fails the spec until it is listed) and holds every anchor or form that points at one, including through the Share & Print card, to both attributes; driven end to end by `tests/e2e/event_ics_download_spec.py` and `tests/e2e/native_download_links_spec.py`. A Django-client spec cannot see this, because the response itself is a correct attachment.
25. **An input that takes a lot of room starts collapsed.** Show its chosen values and an Edit button; expand the controls only when the member interacts with it, keep them in the DOM (`x-show`) so one Save posts them, and open a new or errored row. See *Inputs That Take Room Start Collapsed* under Interaction Patterns.
26. **Be generous with spacing: nothing touches.** Every block that follows a control, a heading or another block clears it by at least `0.75rem` inside a form or modal and `1.5rem` between sections, on the 8px grid, through a CSS class. A block carries its **own top margin**; never lean on the element above for the gap, because `.pl-form-group:last-child { margin-bottom: 0 }` (`components.css`) strips the bottom margin of the last group inside any wrapper (an `x-show` div, a modal body) and `hub.css`'s universal reset (`* { margin: 0 }`) zeroes paragraph margins, so a bare `<p>` after a field renders flush against it (the Re-assign modal's sentence, #764). A free-standing line in a form or modal (a live summary sentence, a note under a picker) is `.pl-form-note` (`margin: 1rem 0 1.5rem`). Rule 18 and `pl-btn--spaced` are instances of this rule, not the whole of it. Check the rendered page in both themes before taking the screenshot: two things touching is a bug, not polish.
27. **A wrapper that reloads itself disinherits the attributes it owns: `hx-disinherit="hx-select hx-swap"`.** htmx inherits `hx-select`, `hx-swap` and `hx-target` down the tree, and `hub/base.html` boosts every anchor and form under its body, so a boosted link inside a `<div hx-get hx-select="[data-x]" hx-swap="outerHTML">` selects `[data-x]` from the next page and swaps that into the body: an empty page when the next page has no such element (Manage Bookings' View all into the Orientations Bookings tab, #768), or the pane alone, without header, tabs or sidebar (the Bookings tabs' chips). Name the attributes: `hx-disinherit="*"` is not the same thing, because `hx-boost` travels the same walk, so `*` turns every link and form inside the wrapper into a full document load (htmx 2.0.4). The three bookings refresh wrappers carry it; `tests/e2e/bookings_boosted_links_spec.py` fails without it, and fails on `*` because its stamp on `window` does not survive a document load.
28. **Size an input to what it holds; never stretch it across the card.** A number or a short code (days, a count, a price, a PIN) is a few characters wide (`width: 6rem`); a one line text field (a title, a question, an answer) stops at a readable width (`max-width: 520px`); only a textarea or rich text editor takes the full column. A few fixed values are chips, not a box: the New Poll composer (`polls/partials/_poll_form.html`) offers Runs for as five radio chips, types each answer into a pill the width of the vote button it becomes, and runs as wide as the cards around it. On a phone the capped field is simply full width. A 1,000px box for a two digit number reads as unfinished, and Felix rejected #928 for exactly this.

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
| `event-flyer.css` | `hub/partials/_event_flyer_sheet.html`, on `hub/event_flyer.html` and `hq/flyer.html` | A Community Event's flyer: a Letter sheet sized in container units, so it is the same full size or scaled into the HQ's flyer frame; forced light. |
| `event-flyer-page.css` | `hub/event_flyer.html` | The page around the event flyer: follows the theme on screen, prints the sheet alone on one Letter page. |
| `qr-sheet.css` | `hub/equipment_flyer.html`, `hub/orientation_type_flyer.html` | The equipment and orientation QR sheets (#631): printable one page Letter flyers, forced light like the other two. |
| `calendar.css`, `session-calendar.css` | community calendar, guild pages, the Orientations and Reservations pages, class composer | Calendar grids. |
| `member-edit.css` | `hub/admin/member_edit.html` | One admin page. |
| `member-directory.css` | `hub/member_directory.html` | The Member Directory's additions since #650: a card's leadership badge pills and the line of Leadership Directory titles under the name (`.pl-directory-titles`). Its older card rules stay in `hub.css`; the pill itself (`.pl-leader-badge`) is in `components.css`. |
| `leadership.css` | `hub/leadership_directory.html`, `hub/admin/leadership.html` | The Leadership Directory cards and its admin page. |
| `voting-admin.css` | `hub/admin/voting_*.html` | Voting admin pages. |
| `announcement-compose.css` | `hub/announcement_compose.html`, `hub/announcement_sent.html` | The Email Members composer's own additions (the Discord preview card, the back link, the line about a resumed draft); its older styles still live in `hub.css`. The sent view loads it for the preview cards. |
| `orientations.css` | `hub/orientations.html` | The Orientations page cards: owner line, meta line, the next bookable times, the Completed fold and the empty state. The member state rules they share with the guild and equipment pages stay in `hub.css`. |
| `bookings-tab.css` | `hub/orientations.html`, `hub/equipment_index.html` | The Bookings tab both pages share (#626, #627): chips (and the Needs Approval chip's count, #748), the filter bar that folds into a Filters disclosure on phones, and the stacked `.pl-members-table` row with its "..." pinned to the first line. The staff view's Print QR Sheet menu (#631). |
| `equipment-manage.css` | `hub/equipment_manage.html`, `hub/equipment_add.html` | The equipment Manage page's additions since #731: the `.pl-equip-panel` card spacing, an Opening Hours row collapsed to its summary line, the Ways to Qualify editor both pages share (#747, `.pl-equip-ways`, `.pl-equip-way`), the Needs approval switch and Needs Approval card rows (#748), and the Pricing card with its Who gets paid select, warn and status lines, and an unpaid hold's Upcoming row (#749). Its older rules stay in `hub.css`. |
| `equipment-detail.css` | `hub/equipment_detail.html` | The equipment page's additions since #747: the requirements banner's ways, each a short list with an "or" between them and a Done mark. Its older rules stay in `hub.css`. |
| `equipment-agreement.css` | `hub/equipment_detail.html`, `hub/equipment_manage.html` | The usage agreement (#734): the agree prompt card, the modal's scrolling text box, link and actions, the banner's text line, and the Details tab note on how many members agreed. |
| `equipment-schedule.css` | `hub/equipment_detail.html`, `hub/equipment_checkout_return.html` | The equipment page's schedule additions since #748: a request awaiting approval drawn dashed on the day timeline, the Book a Time terms line, and a row's state line and badge on Your Reservations Here; the price chip row, the live Total, the confirm's summary line and the reservation checkout landing card (#749). Its older rules stay in `hub.css`. |
| `guild-settings.css` | `hub/guild_edit.html` | Guild settings styles added since #502: the settings card and the Reservations tab's item list. The page's older rules stay in `hub.css`. |
| `announcements.css` | `hub/announcements.html`, `hub/announcement_sent.html`, `hub/email_members_release.html` | The Email Members page (#920: Drafts, Sent and Mailing Lists tabs, the list on top of `.pl-members-table` stacking, the admin Release Update card `.pl-release-entry`), the sent view's facts list, the Release Update page's back link and the Mailing Lists tab's guild list (`.pl-mailing-lists`). |
| `notifications-catalogue.css`, `notifications-edit-copy.css`, `notifications-edit-discord.css` | `hub/admin/notifications/*.html` | Notification admin pages. |
| `spotlight.css` | `hub/base.html` (every hub page) | The Spotlight (#708, #709), a static version card since #935 (`hub/partials/_spotlight.html`): the version pill, "Updated X" and Details, joined to the logo above the sidebar's divider; the Details panel (`hub/partials/_spotlight_expanded.html`: the latest update, Have an idea, the version link and the changelog view); a Spotlight message's own rich text everywhere (`.pl-ann-body`, the Home ticker's and the admin rows'); and Admin Tools > Spotlight's card and the add and edit page's errors. The changelog pager's styles sit with the changelog's in `hub.css`, `style.css` and `unfold-custom.css`. |
| `polls.css` | `hub/home.html`, `polls/index.html`, `polls/admin.html`, `polls/admin_poll.html`, and `hub/guild_detail.html` when it shows the Polls tab | Polls (#925): the poll card (`polls/partials/_poll_card.html`; on Home `--home` and on a guild's Open Poll `--guild` draw no box of their own; a guild poll's name pill `__guild` on Past Polls), its tally with the member's Your vote pill (`_poll_results.html`, the count dropping under the answer below 480px), Past Polls' list and pager, Admin Tools > Polls (`.pl-polls-admin`: the open poll and the Past Polls table), and the New Poll composer (`.pl-poll-compose`, `_poll_form.html`, from `mockups/925-poll-composer.html`): the poll card with a dashed gold edge, the question as a borderless auto growing textarea, answer pills with x, the dashed Add pill, the Draft pill and live Closes line, and the settings rail (Runs for chips, Post Poll) beside it from 860px, under it below; and a guild's Polls tab (`.pl-guild-polls`, `_guild_polls.html`: a 1.5rem column of cards, Ask the Guild's title and New Poll button on one row); and written answers (#925 part 3): the card's write your own form under a divider (`.pl-poll-card__write-in`), the composer's dashed write in preview (`.pl-poll-compose__writein`) and the Written Answers list under the counts (`.pl-poll-written`, `_poll_written.html`). |
| `door-code.css` | `hub/home.html` | The Door Code on Home (#813, `hub/partials/_door_code.html`): the compact header box beside the title (`.pl-home-header-actions`, from 1200px, keeping the header on one row, the description on one line, and the box as tall as the title column so Show me around (`_home_tour_link.html`, shown only until the member tour is taken or dismissed) ends level with its bottom) and the card under the quick links (below 1200px); the masked value with Tap to reveal / Tap to hide, the Changed line and an admin's Change link. |
| `home-featured.css` | `hub/home.html` | Home's Upcoming queue (#937, `hub/partials/_home_upcoming.html`): the featured card (the picture above the text on a phone, left of it from 768px, every picture a 16:9 frame; a photo covers it, an item with none gets the date poster in its kind's color, sized in container units), the cluster of previous, pause and next beside the kind pill, and the list's rows sliding under it. One color per kind from existing tokens, shared by the list's pill and the poster: class gold, event blue, meeting green, studio hours violet, orientation rose. Borrows `rotator.css`'s control and time bar classes. |
| `rotator.css` | `hub/home.html` | The `.pl-rotator` block for `static/js/pl-rotator.js` (`hub/partials/_rotator_controls.html`), moved out of `home-featured.css` in #890 and loaded by Home alone since #935, when the sidebar Spotlight and the Home banner stopped rotating: slides stacked in one grid cell so the tallest sets the height, the 400ms fade, arrows over the picture (on hover or focus with a mouse, always on touch), the time bar along its foot, the dots and the pause button, and the `prefers-reduced-motion` rules. Home's Upcoming queue (#937, `pl-upcoming-queue.js`, `home-featured.css`) borrows its arrow, pause and time bar classes for its own component. A pause lasts until play or logging out (#890): the rotator root carries `data-rotator-name` (`home-upcoming`), and `pl-rotator.js` keeps the pause in localStorage as `pl-rotator-paused:<user pk>:<name>`, the pk from `<html data-pl-user>` (both base templates, empty when signed out, never an email). A submit to a `/logout/` URL clears every key, and on load the script drops every key but the signed in user's (all of them on a signed out page, which is why `base.html` loads it too). With storage blocked a pause lasts for the page. |
| `home-announcements.css` | `hub/home.html`, `hub/admin/spotlight.html`, `hub/admin/announcement_form.html` | The Spotlight messages on Home (#889, named announcements until #920): since #935 a ticker at the top of Home (`hub/partials/_home_announcements.html`), one slim muted line in a pill with a megaphone, the messages crawling right to left with a dot between them, and an X. The track holds the list twice (the copy `aria-hidden` and `inert`) and a CSS animation moves it by one list's width per loop; the pacing script (`hub/partials/_ticker_pace_script.html`, `window.plTicker.pace`) sets `--pl-ticker-duration` from the list's width and the ticker's `data-ticker-speed` (`SiteConfiguration.spotlight_crawl_speed`, 25px a second by default, #942) and `--pl-ticker-span` to the line's width, so a short list still loops cleanly. `:hover` and `:focus-within` pause it; under `prefers-reduced-motion` nothing moves, the copy is hidden and the messages wrap. The message's own rich text, `.pl-ann-body`, comes from `spotlight.css`. Also Admin Tools > Spotlight's Spotlight Messages rows (on top of hub.css's Slideshow reorder controls), its Crawl speed slider (`.pl-crawl-speed`) with a preview that is the same ticker track (`hub/partials/_ticker_track.html`, inert) crawling at the slider's speed, and the add and edit page with its "N / 160" count. |
| `tour-banner.css` | `hub/home.html` | The Take the tour banner on Home (#824, `hub/partials/_home_tour_banner.html`): the member tour's offer as the first card under the header, compass icon, line and summary, Take the tour stacking under the text on a phone. Its close button is the Get Started card's `.pl-onboarding__close` in `hub.css`. |
| `feedback.css` | `hub/beta_feedback.html`, `hub/admin/feedback_inbox.html`, `hub/admin/feedback_request.html` | The Feedback page's Your Requests rows, the admin Feedback inbox (its Member replied pill) and a request's admin page (#693); the comment thread both sides share (`hub/partials/_feedback_thread.html`, drawn like the kiln ticket thread) and its reply box. |
| `locations.css` | `hub/guild_detail.html`, `hub/admin/locations.html`, `hub/admin/location_edit.html` | The guild page's location lights (with token fallbacks, since the guilds surface does not load the hub tokens): compact pills in one wrapping flex row, each sized to its words and capped at the row's width so a long message wraps inside its pill (#892). Also the admin Locations page. |
| `guild-announcements.css` | `hub/guild_detail.html`, `hub/guild_announcements.html` | Guild announcements (#952, `hub/partials/_guild_announcement_card.html`): the one card on a guild page, its body text smaller than the page's, paragraphs and bulleted lists spaced inside it, a long body folded to about seven lines under a fade with Read More, the No current announcements line and the Past Announcements link; the Past Announcements page's title, list and pager. Color tokens carry fallbacks for the guilds surface. |
| `site-settings.css` | `hub/admin/site_settings.html`, `member_agreement.html`, `manage_features.html`, `manage_cms.html`, `automations.html` | Site Settings and the four pages split from it (#922): the themed form controls all five share (`.site-settings-form`), the Calendar tab's feed rows, the new pages' title, lead, form column and section headings (`.pl-settings-page__*`, `.pl-settings-section__*`), Manage CMS's Sync Now and match table, and the Run now spinner and progress bar driven by `hub/admin/_run_now_script.html`. The job and feature cards (`.pl-automation-*`) stay in `hub.css`. |
| `admin-tools.css` | `hub/admin_tools.html` | Admin Tools' sections (#912): Manage, Tools, Reports and Webmaster, each a `.hub-detail-label` heading over its own `.pl-tools-grid`, with the gaps between them. Guild Tools' guild section (#921) puts the guild's name and its picker in one `.pl-tools-section__head` row that wraps on a phone. The cards themselves stay in `hub.css`; the Email Members card's "Formerly called Announcements" pill (`.pl-tool-card__formerly`, #920) sits here. |
| `hq.css` | `hq/page.html`, `hq/volunteers.html`, `hq/volunteer_roster.html`, `hq/wine.html`, `hq/wine_donors.html`, `hq/edit.html`, `hq/card_edit.html` | The Holiday Bizarre HQ (Track C): the navy hero band with its gold eyebrow, Playfair title and countdown figure, the goal chips with their `.pl-hq-meter` bars, the door cards on `.pl-tools-grid` (a placeholder is a greyed `<div>`, never a link), the admin line under each card, the volunteer sign-up and wine wall pages in the look of Lee's sheets (`.pl-hq-vol`, `.pl-hq-wine`: band, meter, shift rows, bottle list, roster and donors tables), and the two edit pages (goal rows, shift rows, Move up and Move down, the share panel and its Can open and Team people pickers). |
| `events-admin.css` | `hub/admin/events_list.html`, `hub/admin/event_detail.html` | Manage Events (#831): the list's Kind, Date(s) and Created by columns (the last hidden below 1100px), the status pills' light-theme contrast, and the per event screen's section spacing. The table itself is the Manage Classes styling in `hub.css`. |
| `event-page.css` | `hub/event_detail.html` | The public event page (#831, part 5) in the Holiday Bizarre HQ's look, its palette scoped on `.pl-event-page` with `hq.css`'s values: the hero (cropped photo under the navy shade, or the navy band) with its gold eyebrow and Playfair title, the going band with the big people count, the goal meter and the pitch, the Count Me In card, the white cards (description, gallery, video, location, Who's Going), the navy primary and ghost buttons, the Share menu and the footer line. `templates/404.html` keeps the four `.pl-event-detail` layout rules in `hub.css`. |
| `tours.css` | `tours/bookings.html`, `tours/hours.html`, `tours/settings.html` | Facility tours (#947): the Tours page's Upcoming and Past tabs (#949: one `.pl-tours-booking` card per booking with its actions, the empty states, and the Reschedule modal's `.pl-tours-move` date strip, times and chosen time), its My Hours tab (the admin's guide picker, tour hours rows folded to a summary with Edit, the Save row, the Days Off list and the Add Day Off modal's fields and actions, and the Google Calendar card of #951, `.pl-tours-gcal`: the account line with Disconnect, the calendar picker and the broken notice with Reconnect) and its admin Settings tab (its cards and the closed message under the bookings switch); the page top shared by both tabs (`tours/partials/_page_top.html`: the Back to Tools link `.pl-tours-back`, header and tab row). Number inputs `pl-tours-input--narrow` (6rem), text `pl-tours-input--text` (520px). Not the guided tours' `.pl-tour-card`, which stays in `hub.css`. |
| `tours-public.css` | `tours/public/base.html` | The visitor's tour pages on tours.pastlives.space (#948), on top of `cms-public.css` and `classes-register.css` (`.reg-field`): the date strip (`.pl-tourpub__days`, wrapping, never scrolling sideways), the time choices, the chosen time, the form's party size (8rem) and account toggle, a booking's facts and its Reschedule and Cancel actions. |
| `kiln-tickets.css` | `kiln/*.html`, and `hub/guild_detail.html` when it shows the Kiln Tickets tab | Kiln tickets (#691): ticket rows, the tab's crew links, the ticket form, detail (maker and crew) with flags and the reply thread, Load the Kiln's photo tiles, Unload's checklist, the Kiln Log (table on desktop, cards on a phone), a firing's page and the crew's lists, lifted from `mockups/kiln-tickets.html`. |
| `eventbrite-tab.css` | `classes/teach/class_eventbrite.html` | A live class's Eventbrite tab: Status, Eventbrite's Rules, Fees and Listing Settings split by dividers, the callouts for its notes, the two fee choice cards and the Turn Off row. |
| `instructor-inquiries.css` | `classes/admin/instructor_inquiries.html` | The Instructor Inquiries page (#690): filter bar, status pills and the Board Report charts; a row's View Application panel with every answer (#793); the Add Inquiry modal and its scrolling Member picker (#805). |
| `registrations-admin.css` | `classes/admin/registrations.html` | Manage All Classes > Registrations (#911): the filter toolbar grid (one column on a phone, two from 640px, three from 1100px, the From and To pair always side by side), the bar under it with Apply, My Classes beside its ?, and Payment Report and Export CSV at the end, and the totals line. |
| `teach-application.css` | `classes/teach/apply.html` | The teaching application page behind I'm Interested (#793): section cards spaced by a column gap, chip groups for pick one and pick any questions, and the fields a Yes opens. |
| `email-queue.css` | `hub/admin/activity.html` | The Email Queue tab under Manage > Site Activity (#894): 1.5rem between its cards, rows split by a divider, each row's meta and pill lines, one disclosure per person with its row filling the summary, and the pills' light theme contrast. |
| `growth.css` | `hub/admin/growth.html` | The admin Growth dashboard (#795): the date range bar, stat tiles and sparklines, the Trend chart's controls, the Classes and CMS charts, legend and table, and the series palette (`--pl-growth-*`). Each chart is drawn at a desktop and a phone width; `.pl-growth__svg--wide` and `--narrow` show the one that fits. |
| `signage.css` | `signage/base.html` | The lobby signage display. |
| `wiki-stickers.css` | `hub/wiki_sticker_sheet.html` | The QR sticker sheet. |
| `rich-editor.css` | `_components/rich_editor_assets.html` | Styling around the Quill editor. |
| `privacy.css` | `core/privacy_policy.html` | One page. |
| `quill.snow.css`, `driver.css` | rich editor assets, `hub/partials/_tour.html` | Vendor: the Quill theme and the driver.js tour. Do not edit. |
