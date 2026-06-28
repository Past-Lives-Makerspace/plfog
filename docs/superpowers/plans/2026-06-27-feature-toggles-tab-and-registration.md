# Site Settings → Features: kill switches for My Tab/Payments and class registration

**Date:** 2026-06-27
**Branch / PR:** `release-0.19.x` (PR #109)
**Status:** Ready to build

## Summary

Add a new **Features** tab to Site Settings with two admin toggles for the launch:

1. **Enable My Tab & Payments** (default ON). When OFF, hide every member-facing
   Tab/Payments surface — the "My Tab" sidebar link, the topbar balance pill, and the
   **Buyables** tab on guild pages — and hide the admin "Payments"/"Reports" nav links.
   Direct hits to the member Tab routes get a friendly redirect.
2. **Allow class registration** (default ON). When OFF, the public class **Register**
   button is replaced by a disabled button + an admin-editable note, and the registration
   view refuses sign-ups (defense in depth).

These are soft-launch gates: the features stay built, an admin just flips them on when
ready. Defaults are ON so deploying this change is a no-op until someone toggles.

## Background — the surfaces involved

| Concern | Where |
|---|---|
| Site Settings singleton + form | `core.models.SiteConfiguration` (singleton, `load()`); `hub.forms.SiteSettingsForm` (ModelForm); template `templates/hub/admin/site_settings.html`; view `hub.views.admin_site_settings` (+ `_handle_site_settings_save`) |
| "My Tab" member nav | `templates/hub/base.html` — sidebar link (~L108 and a second copy ~L216, both inside `{% if request.view_as.is_member %}`); topbar balance pill (~L367, gated on `tab_balance is not None`) |
| Admin Payments nav | `templates/hub/base.html` — "Payments" (`billing_admin_dashboard`, ~L127) and "Reports" (`billing_admin_reports`, ~L135) links |
| Topbar pill data | `billing.context_processors.tab_context` → `tab_balance` / `tab_is_locked` |
| Buyables tab | `templates/hub/guild_detail.html` — tab button (~L105) + panel (~L388), Alpine `section === 'buyables'` |
| Member Tab routes | `hub.views.tab_detail` (`/tab/`), `tab_history` (`/tab/history/`), `setup_payment_method` |
| CMS register button | `templates/classes/public/detail.html` — CTA block (~L380–397) in the sticky booking rail |
| Register view | `classes.views.register` (`classes/views.py:485`) — already has an `is_bookable` early-redirect to mirror |
| Context processors | registered in `plfog/settings.py` TEMPLATES; existing `core.context_processors.*` |

## Model changes — `core/models.py::SiteConfiguration`

Add three fields (after `google_analytics_measurement_id`, before `Meta`). New booleans
default **True** (current behavior preserved; admins opt to turn features off):

```python
tab_payments_enabled = models.BooleanField(
    default=True,
    verbose_name="Enable My Tab & Payments",
    help_text="When off, hides My Tab, the balance pill, the Buyables tab on guild pages, "
    "and the admin Payments/Reports nav. Members visiting the Tab pages are redirected.",
)
class_registration_enabled = models.BooleanField(
    default=True,
    verbose_name="Allow class registration",
    help_text="When off, the public Register button is disabled (with the note below) and "
    "the registration form refuses sign-ups.",
)
class_registration_disabled_note = models.TextField(
    blank=True,
    default="Online registration is paused right now. Email info@pastlives.space and we'll help you sign up.",
    verbose_name="Registration-off message",
    help_text="Shown under the disabled Register button when class registration is off.",
)
```

**Migration:** one migration in `core/migrations/` adding the three fields. Standard
add-field (Django generates a reversible migration — no custom reverse needed). Run
`ruff format` on it and `git add` it with the code (per project memory: unformatted
committed migrations fail CI's `ruff format --check`).

## Exposing the flags to templates

The register button lives on the **public** surface and the nav on the **members**
surface, so the flags must be global. Add a context processor and register it:

```python
# core/context_processors.py
def feature_flags(request: HttpRequest) -> dict[str, Any]:
    """Expose the Site Settings → Features toggles site-wide (members + public)."""
    from core.models import SiteConfiguration
    config = SiteConfiguration.load()
    return {
        "tab_payments_enabled": config.tab_payments_enabled,
        "class_registration_enabled": config.class_registration_enabled,
        "class_registration_disabled_note": config.class_registration_disabled_note,
    }
```

Register `"core.context_processors.feature_flags"` in `plfog/settings.py` TEMPLATES
context_processors list.

Also make the **topbar pill** vanish automatically: in
`billing.context_processors.tab_context`, return `{}` early when
`SiteConfiguration.load().tab_payments_enabled` is False (so `tab_balance` is absent and
the existing `{% if tab_balance is not None %}` gate hides the pill with no template
change). Keep one `SiteConfiguration.load()` call; it's cheap + already used per-request.

## Template gating

### `templates/hub/base.html`
- **Both** "My Tab" sidebar links: tighten the wrapping condition to
  `{% if request.view_as.is_member and tab_payments_enabled %}`.
- "Payments" and "Reports" admin links: wrap each in `{% if tab_payments_enabled %}`.
- Topbar pill: no change (auto-hidden via `tab_context`).

### `templates/hub/guild_detail.html`
- Wrap the **Buyables** tab button (~L105) and its panel (~L388) in
  `{% if tab_payments_enabled %}`. Confirm no Alpine default lands on `'buyables'` when
  it's hidden (the page defaults `section` to another tab — verify the initial `section`
  isn't `'buyables'`; if it could be, leave the default as-is since Buyables is not the
  first tab).

### `templates/classes/public/detail.html` — the CTA block
Insert a new branch so the disabled state wins over the normal Register/waitlist CTA but
still respects the "already started" close. Current shape:

```
{% if not is_bookable %} …already started… {% else %} …spots + Register/waitlist… {% endif %}
```

New shape:

```
{% if not is_bookable %}
  …already started… (unchanged)
{% elif not class_registration_enabled %}
  <div class="cp-detail__spots cp-detail__spots--full">Registration unavailable</div>
  <span class="cp-detail__cta cp-detail__cta--disabled" aria-disabled="true" role="button">Registration unavailable</span>
  <div class="cp-detail__waitlist-hint">{{ class_registration_disabled_note }}</div>
{% else %}
  …spots + Register/waitlist… (unchanged)
{% endif %}
```

Add a `.cp-detail__cta--disabled` modifier to the stylesheet that styles `.cp-detail__cta`
(find it — likely `static/css/classes-detail.css`): muted/greyed background, default
cursor, `pointer-events:none`, no hover. Match the design system's disabled-control look.
It's a `<span>`, not an `<a>` — nothing to click.

## View gating (defense in depth — never rely on a hidden button)

### `classes.views.register`
Mirror the existing `is_bookable` gate. Right after `settings_obj = ClassSettings.load()`
(or alongside the `is_bookable` check), add:

```python
from core.models import SiteConfiguration
if not SiteConfiguration.load().class_registration_enabled:
    messages.info(request, SiteConfiguration.load().class_registration_disabled_note or
                  "Online registration is currently unavailable.")
    return redirect("classes:public_class_detail", slug=offering.slug)
```

(Resolve the redirect URL name against `classes/urls.py` — use the same name the existing
`is_bookable` branch uses: `classes:public_class_detail`.)

### Member Tab routes — `hub.views.tab_detail`, `tab_history`, `setup_payment_method`
When `tab_payments_enabled` is False, redirect to the hub dashboard with an info message
("My Tab isn't available right now."). Add a tiny guard at the top of each; or factor a
small helper if it reads cleaner. Use the dashboard URL name already used elsewhere in
`hub/views.py`.

### Admin billing views — leave reachable
Do **NOT** hard-block `billing_admin_dashboard` / `billing_admin_reports`. We only hide
their nav links; an admin can still open them by URL to view history / wind down. (Flag
for the reviewer: if the user wants these hard-blocked too, it's a one-line guard each —
but the default is reachable.)

## Site Settings → Features tab (`templates/hub/admin/site_settings.html`)

- **Tab button:** add a "Features" `vote-tab` button next to General/Calendar/Legacy
  CMS/Announcements, wired to the existing Alpine `tab` state (`@click="tab = 'features'"`,
  `:class` active when `tab === 'features'`).
- **Panel:** a new `<div x-show="tab === 'features'" x-cloak>` **inside the main
  `<form class="site-settings-form">`** (the same form as General/Calendar/Legacy CMS — it
  posts through `SiteSettingsForm`, NOT the separate announcements form). Render the three
  new fields explicitly (label + widget + help_text + errors), matching the General tab's
  `.field-row` / `.field-checkbox` markup. Two checkboxes + one textarea.
- **Keep them out of the General loop:** the General tab does
  `{% for field in form %}{% if field.name != 'sync_classes_enabled' and … %}`. Add the
  three new field names (`tab_payments_enabled`, `class_registration_enabled`,
  `class_registration_disabled_note`) to that exclusion `{% if %}` so they render only in
  the Features panel.
- **Form:** add the three field names to `SiteSettingsForm.Meta.fields`. The
  `class_registration_disabled_note` widget can be a small `forms.Textarea(attrs={"rows": 3})`.
- **Save round-trip:** the Features panel uses the existing `submitted_tab` hidden input +
  `_handle_site_settings_save`, so a save returns to the Features tab automatically. No
  view change needed beyond the fields flowing through `SiteSettingsForm`. Verify
  `admin_site_settings` doesn't allowlist tab names in a way that drops `features` (it
  reads `request.GET.get("tab", "general")` and `submitted_tab` — confirm both accept an
  arbitrary string; if there's an allowlist set, add `"features"`).

## Tests (100% branch coverage + mutation — this repo is strict)

BDD `*_spec.py`. Cover:

- **Context processor** (`core/spec/…`): returns the three flags from the singleton;
  reflects toggled values.
- **`tab_context`**: returns `{}` (no `tab_balance`) when `tab_payments_enabled` is False;
  normal payload when True.
- **Register view** (`classes` specs): when `class_registration_enabled` is False, a GET
  and a POST both redirect to the detail page with the note message and create **no**
  Registration; when True, unchanged behavior still works.
- **Member Tab routes**: redirect with message when disabled; normal render when enabled.
- **Nav/template rendering**: `tab_payments_enabled=False` → rendered hub base omits the
  My Tab link, Payments/Reports admin links; guild detail omits the Buyables tab. Public
  detail page with `class_registration_enabled=False` shows the disabled CTA + note and
  not the live Register link.
- **SiteSettingsForm**: accepts/saves the three new fields; the Features fields are
  excluded from the General loop (assert they're not double-rendered — a render test).
- **Model**: defaults are True / the note default is set on a fresh singleton.

## Versioning & changelog

- Bump `VERSION` in `plfog/version.py` to the next free 0.19 patch (current = `0.19.10` →
  `0.19.11`).
- **No new member-facing changelog entry.** These are admin/ops launch toggles — turning
  them off just hides not-yet-ready features; members don't "get" a feature. To preserve
  the `announce_release` invariant (one entry must have `version == VERSION`), **re-stamp
  the existing top entry's `version` to `0.19.11`** (date may stay or update) without
  adding new bullets. (Per CLAUDE.md's changelog curation rule; use judgment, but lean to
  no new entry here.)

## Out of scope (YAGNI)

- No per-guild or per-class overrides — these are global site switches.
- No scheduling ("auto-enable on date") — admin flips manually.
- No hard-blocking the admin billing dashboards (nav hidden only; see above).
- No new note field for the Tab disable (it's removed from nav, nothing to annotate); only
  class registration gets the editable note.

## File-by-file change list

**Modified**
- `core/models.py` — three new `SiteConfiguration` fields
- `core/migrations/00XX_…py` — **new** migration (add three fields)
- `core/context_processors.py` — new `feature_flags` processor
- `plfog/settings.py` — register `feature_flags` in context_processors
- `billing/context_processors.py` — `tab_context` returns `{}` when Tab/Payments off
- `hub/forms.py` — `SiteSettingsForm.Meta.fields` += three fields (+ note widget)
- `hub/views.py` — guard `tab_detail` / `tab_history` / `setup_payment_method`
- `classes/views.py` — guard `register`
- `templates/hub/base.html` — gate both My Tab links + Payments/Reports admin links
- `templates/hub/guild_detail.html` — gate Buyables tab button + panel
- `templates/hub/admin/site_settings.html` — Features tab button + panel; extend General-loop exclusion
- `templates/classes/public/detail.html` — disabled-CTA branch + note
- the stylesheet defining `.cp-detail__cta` — add `.cp-detail__cta--disabled`
- `plfog/version.py` — VERSION → 0.19.11; re-stamp top entry
- specs as listed above
