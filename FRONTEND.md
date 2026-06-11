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

### Confirm Modal (`components/confirm_modal.html`)

For destructive actions (delete, void, deactivate).

**Parameters:**
- `confirm_id` (required) — unique DOM id
- `confirm_title` — heading (default: "Are you sure?")
- `confirm_message` — body text
- `confirm_action_url` — form POST target
- `confirm_button_text` — button label (default: "Confirm")
- `confirm_button_style` — `danger` (default) or `primary`

```html
<button @click="$dispatch('open-confirm', 'void-charge')">Void</button>
{% include "components/confirm_modal.html" with confirm_id="void-charge" confirm_title="Void this charge?" confirm_message="This will remove the charge from the member's tab." confirm_action_url="/billing/void/42/" confirm_button_text="Void Charge" %}
```

## Interaction Patterns

| Scenario | Pattern | Example |
|----------|---------|---------|
| Quick action (1-3 fields) | Modal + Toast | "Add to Tab", "Enter Your Own Price" |
| Data entry (4+ fields) | Inline form on page | Profile settings, billing settings |
| Destructive action | Confirm modal | Delete product, void charge |
| Success feedback (HTMX) | Toast notification | "Added to your tab!" |
| Success feedback (full page) | Django messages | Login, signup |

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

## Rules for Claude / AI Agents

1. **Always use `components/form_field.html`** for form fields — never render raw `{{ field }}` with manual label/error HTML.
2. **Always use `components/modal.html`** for modals — never build one-off modal HTML with custom overlay/backdrop.
3. **Always use `components/toggle.html`** for boolean fields — never render checkboxes directly or build custom toggle HTML.
4. **Use the `pl-` CSS prefix** for all new component classes. Never add classes with other prefixes.
5. **Quick forms (1-3 fields) → modal.** Longer forms → inline or dedicated page.
6. **After mutating actions, return a toast** via `trigger_toast()`. Don't redirect with Django messages for HTMX requests.
7. **Test both dark and light themes** when adding new CSS.
8. **Card layout:** Wrap content sections in `<div class="hub-card">` for hub pages.
9. **No inline styles** except for truly one-off layout adjustments. Add a CSS class instead.
10. **Image placeholders:** When designing product cards or profile sections, leave space for future image support but don't build upload infrastructure.

## CSS Files

| File | Scope | What goes here |
|------|-------|---------------|
| `static/css/style.css` | Public pages (login, signup, landing) | Auth forms, hero, navigation |
| `static/css/hub.css` | Member hub | Hub layout, sidebar, topbar, page-specific styles |
| `static/css/components.css` | Shared (hub + admin) | All reusable component styles (modal, toast, toggle, etc.) |
| `static/css/unfold-custom.css` | Admin only | Unfold overrides, admin-specific layouts |
