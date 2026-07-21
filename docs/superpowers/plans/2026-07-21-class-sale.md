# Put a Class On Sale — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-21
**Surface:** book CMS public catalog + detail + register (`book.pastlives.test` / `book.pastlives.space`) and the FOG hub instructor/admin class edit forms (`pastlives.test`).
**Related:** `2026-07-13-class-catalog-timeframe-filter.md` (same catalog surface); discount-code plumbing in `classes/forms.py` / `classes/models.py`.

---

## 1. Summary

An instructor can flip a **Sale** toggle on their class and set either a **percent off** or a **flat dollar amount off**. The public class then wears a **fancy, animated announcement banner** across the top of its detail page, a **"Sale" badge** on its catalog card, and shows its **original price struck through beside the new sale price** everywhere it's quoted (catalog, detail rail, register summary, the register button, and the Stripe receipt). The instructor edits the banner headline (a catchy default is pre-filled). A second toggle decides whether a **discount code can still stack on top of the sale** — off by default ("can't be combined with other offers"); when off, the register page cleanly tells the buyer codes can't be combined and hides the code box rather than erroring after they submit.

Members' auto-discount keeps working, now computed off the sale price. Free classes can't go on sale (nothing to discount). There is no start/end date — the toggle *is* the schedule.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Sale shape | `sale_kind` = **PERCENT** or **FIXED** (one, not both). |
| Amount entry | Fixed amount edited in **dollars** via the existing `CentsAsDollarsField` (stored as `sale_amount_cents`). |
| Codes on top of a sale | New toggle `sale_allow_discount_codes`, **default False** — the standard "can't combine with other offers." On = allow stacking. |
| Discount order | base → **sale** → member discount → coupon. Coupon step **skipped** when a sale is active and codes aren't allowed (input hidden, auto-apply skipped — never an after-submit error). |
| Where the instructor edits it | A grouped **"Sale" section on the existing class edit form**, right under the pricing fields — no separate page. Same section on the admin twin. |
| Banner text | Required-when-enabled, but **falls back to the catchy default** if left blank (never blocks save on an empty banner). |
| Free classes | **Cannot** be put on sale (`price_cents` must be > 0 to enable). |
| Scheduling | **No** start/end date — YAGNI; the toggle is the schedule. |
| Confirmation-email line-item breakdown | **Out of scope** — the amount shown is what was paid; the Stripe product name gains a " (Sale)" suffix so the receipt reflects it. |

---

## 2. What already exists (reuse, don't reinvent)

This is almost entirely assembly on top of the pricing/discount plumbing that already runs every registration.

| Need | Existing thing | Location |
|---|---|---|
| Dollars-in / cents-stored form field | `CentsAsDollarsField` | `classes/forms.py:77-98` |
| Precedent: a declared cents-as-dollars field shadowing an int column | `DiscountCodeForm.discount_fixed_cents` | `classes/forms.py:465-469` |
| Precedent: "enabling X requires content" clean + a shared mixin | `TeachWelcomeEmailForm.clean` / `_FreeClassMixin` | `classes/forms.py:1063-1070` / `154-206` |
| The registration price pipeline to insert "sale" into | `RegistrationForm.compute_final_price_cents` | `classes/forms.py:774-781` |
| Member-discount property (the pattern for the new sale props) | `ClassOffering.member_price_cents` | `classes/models.py:902-911` |
| Coupon math already used per-code | `DiscountCode.apply_to` | `classes/models.py:1505-1510` |
| Auto-apply prefill to skip when codes blocked | `RegistrationForm._find_auto_apply_discount` | `classes/forms.py:720-730` |
| Drop-a-field-on-init precedent (waitlist drops `discount_code`) | `RegistrationForm.__init__` | `classes/forms.py:680-683` |
| Stripe $0.50 floor validation (must still hold post-sale) | `RegistrationForm.clean` | `classes/forms.py:757-764` |
| Free-result confirm path (sale can legitimately reach $0) | `register` view free branch | `classes/views.py:611-637` |
| Final amount → Checkout; product-name suffix precedent (series) | `create_class_checkout_session` call + series suffix | `classes/views.py:655-657, 660-672` |
| Instructor & admin class forms (fields auto-render via `form_field.html`) | `TeachClassOfferingForm` / `ClassOfferingForm` | `classes/forms.py:283-338` / `228-280` |
| The two class-edit templates that loop the form + skip named fields | `teach/class_form.html` / `admin/class_form.html` | both loop `{% for field in form %}` + `form_field.html` |
| Themed hub form controls incl. **native `<option>`** (so a new `<select>` is dark-safe with zero new CSS) | `.pl-form-group input/select/textarea` + `.pl-form-group select option` | `static/css/components.css:465-500, 964-978` |
| Toggle switch for booleans (auto via `form_field.html`) | `components/toggle.html` | — |
| Animated/attention strip precedent at top of detail | `.cp-detail__preview-banner` | `templates/classes/public/detail.html:9-13`, CSS `cms-public.css:561-569` |
| Catalog badge vocabulary (Flex / series / private) | `.cp-page .badge.*` | `static/css/cms-public.css:471-475`, used `_list_results.html:65-66` |
| Catalog price + member-price line | `.cls-price` / `member_price_cents` tag | `_list_results.html:119-124`, `cms-public.css:465-466` |
| Detail rail price row + member line | `.cp-detail__price-row` | `detail.html:360-365`, `cms-public.css:943-966` |
| Register summary + submit button price | `.reg-summary .total` / `.btn-reg` | `register.html:33-38, 186-188` |
| Register field theme scope (dark-safe inputs, native `option`) | `.reg-field` | `static/css/classes-register.css:16-39` |
| Money formatting filter (`$80`, `Free`, drops whole-dollar decimals) | `cents_as_price` | `classes/templatetags/classes_tags.py:95-106` |
| Member-price template tag | `member_price_cents` | `classes/templatetags/classes_tags.py:162-167` |
| Factories to extend | `ClassOfferingFactory` / `DiscountCodeFactory` / `RegistrationFactory` | `classes/factories.py:81, 154, 167` |

**Gaps to close (kept small):**
- Six new `ClassOffering` fields + three read-only properties + one migration (§4).
- A shared `_SaleMixin` for the two class forms (declared `CentsAsDollarsField` + `clean_sale_fields()`), plus the four small edits to `RegistrationForm` (§5).
- One new shared template partial (the Sale section), edits to four public/edit templates, and new CSS in two files (§6).
- One line in the checkout view for the " (Sale)" product-name suffix (§5).

---

## 3. Where the code lives

```
classes/
  models.py            # SaleKind TextChoices; 6 sale_* fields; sale_is_active / sale_price_cents /
                       #   sale_savings_display properties; DEFAULT_SALE_BANNER_TEXT constant
  migrations/
    0049_classoffering_sale_fields.py   # additive AddField x6 (auto-reversible)
  forms.py             # _SaleMixin (CentsAsDollarsField + clean_sale_fields); add mixin+fields to
                       #   ClassOfferingForm & TeachClassOfferingForm; RegistrationForm: sale in
                       #   compute_final_price_cents, sale_blocks_codes on __init__, auto-apply skip
  views.py             # checkout product_name " (Sale)" suffix; register/detail member_price_cents
                       #   context computed off sale_price_cents
  factories.py         # (no schema change needed — fields default off; tests pass sale_* kwargs)
templates/classes/
  _components/class_sale_section.html   # NEW — grouped Sale fieldset for both edit forms
  teach/class_form.html                 # include the Sale section under member_discount_pct; skip sale_* in loop
  admin/class_form.html                 # same include + skip (admin twin)
  public/_list_results.html             # SALE badge; struck original + sale price on the card
  public/detail.html                    # animated sale banner strip; struck+sale in the rail price row
  public/register.html                  # struck+sale in summary + submit button; code-blocked note
static/css/
  cms-public.css       # .badge.sale; .cp-detail__sale-banner (+ shimmer keyframes, reduced-motion, light);
                       #   .cls-price--was / .cp-detail__price--was struck-price styles
  classes-register.css # .reg-summary struck+sale + .reg-sale-note
classes/spec/          # model, form, registration-form, register-view, member-pricing-copy specs (§9)
plfog/version.py       # VERSION bump + new member-facing CHANGELOG entry
```

Home app: `classes`. Every touched `.py` file is already inside the coverage/mypy scope. No new app, no new URL.

## 4. Data model

All new fields live on **`ClassOffering`** (`classes/models.py:311`). All are additive and nullable/defaulted, so the migration is a plain `AddField` batch with **automatic reverse** (Django reverses `AddField` by dropping the column — no custom reverse function needed; no data migration).

New choices + module constant (near the top of the model / module, mirroring `DEFAULT_CLASS_FAQS`):

```python
DEFAULT_SALE_BANNER_TEXT = "🔥 Limited-time sale — save on this class while it lasts!"

class SaleKind(models.TextChoices):
    PERCENT = "percent", "Percent off"
    FIXED = "fixed", "Dollar amount off"
```

| Field | Type | Notes |
|---|---|---|
| `sale_enabled` | `BooleanField(default=False)` | help_text: "When on, this class shows a sale banner and charges the sale price." Renders as a toggle. |
| `sale_kind` | `CharField(max_length=10, choices=SaleKind.choices, default=SaleKind.PERCENT)` | help_text: "Percent off the full price, or a flat dollar amount off." |
| `sale_percent` | `PositiveIntegerField(null=True, blank=True)` | help_text: "Percent off (1–99). Used for a percent-off sale." |
| `sale_amount_cents` | `PositiveIntegerField(null=True, blank=True)` | help_text: "Flat amount off, in cents (edited in dollars). Must be less than the price. Used for a dollar-amount sale." |
| `sale_banner_text` | `CharField(max_length=200, blank=True, default=DEFAULT_SALE_BANNER_TEXT)` | help_text: "The headline shown on the sale banner. Leave blank to use the default." |
| `sale_allow_discount_codes` | `BooleanField(default=False)` | help_text: "Off by default — the sale price can't be combined with other offers. Turn on to let registrants add a discount code on top of the sale." |

`help_text` on **every** field per house rules. No new index/constraint — sales are read per-offering on pages that already load the offering; no query filters on these columns.

### Properties (fat model — templates and forms read these, never re-derive)

```python
@property
def sale_is_active(self) -> bool:
    """A sale counts only when switched on, the class is paid, and the matching
    amount is set. The form guarantees consistency, but a stray admin/import edit
    must not crash the catalog — so we re-check the amount defensively."""
    if not self.sale_enabled or self.price_cents <= 0:
        return False
    if self.sale_kind == self.SaleKind.PERCENT:
        return bool(self.sale_percent)
    return bool(self.sale_amount_cents)

@property
def sale_price_cents(self) -> int:
    """Public (non-member) price after the sale. Equals price_cents when no sale is
    active, so callers can use it unconditionally in place of price_cents."""
    if not self.sale_is_active:
        return self.price_cents
    if self.sale_kind == self.SaleKind.PERCENT:
        return int(self.price_cents * (100 - self.sale_percent) / 100)
    return max(0, self.price_cents - self.sale_amount_cents)

@property
def sale_savings_display(self) -> str:
    """Short 'what you save' string for the badge/banner pill — '20% off' or '$15 off'.
    Empty string when no sale is active."""
    if not self.sale_is_active:
        return ""
    if self.sale_kind == self.SaleKind.PERCENT:
        return f"{self.sale_percent}% off"
    # Whole dollars drop decimals, matching cents_as_price. sale_amount_cents is
    # always > 0 and < price here (guaranteed active + validated).
    dollars, rem = divmod(self.sale_amount_cents, 100)
    money = f"${dollars}" if rem == 0 else f"${dollars}.{rem:02d}"
    return f"{money} off"

@property
def sale_banner_display(self) -> str:
    """The banner headline to render, always non-empty. The form fills the default
    on blank, but a non-form edit (admin bulk action, shell, CMS import) can leave
    the text empty — so the render path falls back to the default too, and a
    blank-text sale row never shows a headless banner."""
    return self.sale_banner_text.strip() or DEFAULT_SALE_BANNER_TEXT
```

Templates render `offering.sale_banner_display`, **never** the raw `sale_banner_text`, so the fallback holds regardless of how the row was edited.

Note the deliberate design: **`sale_price_cents` collapses to `price_cents` when there's no sale**, so every price surface can call it unconditionally and the member-price tag can be pointed at it uniformly (see §6). No new "member price on sale" property is needed — the existing `member_price_cents` tag applied to `sale_price_cents` yields the right number in both cases.

## 5. Business logic (fat models / forms — views stay thin)

### 5a. `_SaleMixin` on the two class forms

A shared mixin (sibling to `_FreeClassMixin`, `classes/forms.py:154`) so `ClassOfferingForm` and `TeachClassOfferingForm` validate identically. It declares the dollars-entry field and a `clean_sale_fields()` called from each form's existing `clean()` (both already call `self.clean_is_free_pricing()` there):

```python
class _SaleMixin:
    """Declares sale_amount_cents as dollars and validates the Sale section.

    Enabling a sale needs a kind + the matching amount; percent must be 1–99; a
    fixed amount must be less than the price; a free class can't be put on sale;
    and a blank banner falls back to the catchy default (never blocks save).
    Mirrors _FreeClassMixin: the declared CentsAsDollarsField shadows the model's
    integer-cents column, and clean_sale_fields() is invoked from clean()."""

    def clean_sale_fields(self) -> None:
        cleaned = self.cleaned_data
        if not cleaned.get("sale_enabled"):
            return
        # ERROR VISIBILITY (blocker B1): sale_enabled renders through toggle.html,
        # which shows NO field.errors — so an error attached to sale_enabled is
        # silently swallowed. Every error here targets a VISIBLE non-toggle field
        # (price_cents, sale_percent, sale_amount_cents), and clean() surfaces the
        # cross-field cases via a non_field_error too (the templates now render a
        # non_field_errors block — see §6a).
        if cleaned.get("is_free"):
            self.add_error("price_cents", "A free class can't be on sale. Uncheck the free option or turn the sale off.")
            return
        price = cleaned.get("price_cents")
        if not price:  # None / "" / 0
            self.add_error("price_cents", "Set a price before putting this class on sale.")
            return
        if cleaned.get("sale_kind") == ClassOffering.SaleKind.PERCENT:
            pct = cleaned.get("sale_percent")
            if not pct:
                self.add_error("sale_percent", "Enter the percent off (1–99).")
            elif not (1 <= pct <= 99):
                self.add_error("sale_percent", "Percent off must be between 1 and 99.")
        else:  # FIXED
            amt = cleaned.get("sale_amount_cents")
            if not amt:
                self.add_error("sale_amount_cents", "Enter the dollar amount off.")
            elif amt >= price:
                self.add_error("sale_amount_cents", "The amount off must be less than the price.")
        # STRIPE FLOOR (M1): a sale that drops the public price into the 1–49¢
        # dead-zone can't be charged online and the buyer has no code to remove —
        # so reject it at authoring time. Only meaningful once the amount fields
        # above are valid (so guard on no prior errors for the amount field).
        if not self.errors.get("sale_percent") and not self.errors.get("sale_amount_cents"):
            resulting = self._resulting_sale_price_cents(cleaned, price)
            if resulting is not None and 0 < resulting < STRIPE_MIN_CHARGE_CENTS:
                target = "sale_percent" if cleaned.get("sale_kind") == ClassOffering.SaleKind.PERCENT else "sale_amount_cents"
                self.add_error(
                    target,
                    "This sale would drop the price below the $0.50 minimum we can charge online.",
                )
        if not (cleaned.get("sale_banner_text") or "").strip():
            cleaned["sale_banner_text"] = DEFAULT_SALE_BANNER_TEXT  # required-with-default

    @staticmethod
    def _resulting_sale_price_cents(cleaned: dict, price: int) -> int | None:
        """The public (non-member) sale price these cleaned values would produce,
        or None when the matching amount is absent. Mirrors ClassOffering.sale_price_cents."""
        if cleaned.get("sale_kind") == ClassOffering.SaleKind.PERCENT:
            pct = cleaned.get("sale_percent")
            return int(price * (100 - pct) / 100) if pct else None
        amt = cleaned.get("sale_amount_cents")
        return max(0, price - amt) if amt else None
```

`STRIPE_MIN_CHARGE_CENTS` (= 50) already lives at `classes/forms.py:73` — reuse it, don't redefine.

**Disabling a sale preserves its settings.** `clean_sale_fields` returns early when `sale_enabled` is off and touches none of the other `sale_*` values, so turning a sale off (and saving) keeps the kind/percent/amount/banner exactly as they were — re-enabling later needs no re-entry. (`sale_is_active` gates all display/pricing on `sale_enabled`, so dormant values are inert until switched back on.)

- Declared field on both forms (like `price_cents`): `sale_amount_cents = CentsAsDollarsField(required=False, label="Amount off ($)", help_text="Flat dollars off, e.g. 15.00 for $15 off.")`.
- Add the six field names to each form's `Meta.fields` (right after `member_discount_pct`, keeping the pricing cluster together).
- Add `_SaleMixin` to each class's bases and call `self.clean_sale_fields()` from the existing `clean()`.
- **Domain-appropriate errors, not generic** — each `add_error` names the fix in plain language a maker or lead understands.

### 5b. `RegistrationForm` — sale in the price pipeline (four small edits)

**Discount order** (`compute_final_price_cents`, `classes/forms.py:774-781`) becomes base → **sale** → member → coupon:

```python
def compute_final_price_cents(self) -> int:
    price = self.offering.sale_price_cents          # sale first (== price_cents when no sale)
    if self.member_discount_pct:                    # member discount off the (sale) price
        price = int(price * (100 - self.member_discount_pct) / 100)
    code = self._validated_discount
    if code is not None and not self.sale_blocks_codes:   # coupon last, unless the sale blocks it
        price = code.apply_to(price)
    return max(0, price)
```

**Block codes when a sale forbids stacking** — set a flag and drop the field on `__init__` (beside the existing waitlist `discount_code` pop, `forms.py:680-683`), so there is no code box to fill and nothing to reject after submit:

```python
self.sale_blocks_codes = offering.sale_is_active and not offering.sale_allow_discount_codes
if self.sale_blocks_codes:
    self.fields.pop("discount_code", None)
```

**Skip the auto-apply prefill** when codes are blocked — guard the block at `forms.py:706-710` with `and not self.sale_blocks_codes`, and make `_find_auto_apply_discount` (`forms.py:720-730`) compute its base off the **sale** price:

```python
def _find_auto_apply_discount(self) -> DiscountCode | None:
    base = self.offering.sale_price_cents
    if self.member is not None and self.offering.member_discount_pct:
        base = int(base * (100 - self.offering.member_discount_pct) / 100)
    return DiscountCode.objects.best_auto_apply_for(self.offering, base)
```

**Stripe floor unchanged and still correct** — `clean` (`forms.py:757-764`) already calls `compute_final_price_cents()`; a sale that lands the total between $0.01 and $0.49 still trips the same friendly "less than $0.50" error, and a sale (± member/code) that reaches exactly **$0** still flows through the existing free-confirm path (`views.py:611-637`). No change needed there; call it out in tests.

### 5c. Checkout product-name suffix (view)

Directly after the series-suffix block (`classes/views.py:655-657`), append the sale marker so the Stripe receipt reflects it (precedent: the series suffix):

```python
if offering.sale_is_active:
    product_name = f"{product_name} (Sale)"
```

The **amount** already flows through `compute_final_price_cents → create_class_checkout_session` untouched; this is a label-only change. Confirmation-email line-item breakdown stays **out of scope** (§10).

### 5d. Member-price context (thin view glue)

**Three** views pass `member_price_cents = offering.member_price_cents` (computed off full price): the public-detail view (`views.py:347`), the `register` view (`views.py:682`), **and `_render_class_preview` (`views.py:1520`, the context key at `views.py:1548`)** — the shared owner/admin/reviewer preview that renders the same `detail.html`. All **three** must point at the **sale-aware** base so the member line renders off the sale price, and so the instructor's *preview* matches what the public catalog/detail/register actually show: `member_price_cents(offering.sale_price_cents, offering.member_discount_pct)` (the same int formula the template tag uses — one line each, no new model property). Grep `member_price_cents = offering.member_price_cents` to catch all three. Catalog and detail templates use the `member_price_cents` **tag** directly and just get `offering.sale_price_cents` passed in.

## 6. UI / UX  ← completeness checklist applied per screen

Four screens change: the **instructor/admin class edit form** (the Sale controls), the **catalog card**, the **class detail page**, and the **register page**.

### 6a. Sale section — instructor edit form (`teach/class_form.html`) + admin twin (`admin/class_form.html`)

- **Screen / partial:** new `templates/classes/_components/class_sale_section.html`, **included in both** edit templates (`teach/class_form.html` *and* `admin/class_form.html`). Both already loop `{% for field in form %}` in their **second** field loop and render each via `components/form_field.html`, skipping named fields (title/category/image/scheduling_type/hero_crop and the collapsible text fields). Two template changes in **each** file:

  1. **The `member_discount_pct` elif branch is NEW in both templates** — today `member_discount_pct` has no branch and falls through to the final `{% else %}` (rendered as a plain `form_field.html`). Add an explicit branch that renders it and then injects the Sale section, so the section lands in the pricing cluster:

     ```django
     {% elif field.name == 'member_discount_pct' %}
         {% include "components/form_field.html" with field=field %}
         {% include "classes/_components/class_sale_section.html" %}
     ```

  2. **Six no-op skip branches** for the sale field names, so the loop's final `{% else %}` doesn't render them a second time (the partial owns them):

     ```django
     {% elif field.name == 'sale_enabled' or field.name == 'sale_kind' or field.name == 'sale_percent' or field.name == 'sale_amount_cents' or field.name == 'sale_banner_text' or field.name == 'sale_allow_discount_codes' %}
     ```

- **Partial markup (`class_sale_section.html`), spelled out** — a `.hub-card` fieldset that renders all six fields itself (the loop skips them), in order enabled → kind → percent → amount → banner → allow-codes:

  ```django
  <fieldset class="hub-card pl-sale-fields">
    <h3 class="pl-sale-fields__title">Sale</h3>
    <p class="hub-text-muted pl-sale-fields__hint">Put this class on sale — a banner and the sale price show automatically.</p>
    {% include "components/form_field.html" with field=form.sale_enabled %}
    {% include "components/form_field.html" with field=form.sale_kind %}
    {% include "components/form_field.html" with field=form.sale_percent %}
    {% include "components/form_field.html" with field=form.sale_amount_cents %}
    {% include "components/form_field.html" with field=form.sale_banner_text %}
    {% include "components/form_field.html" with field=form.sale_allow_discount_codes %}
  </fieldset>
  ```

  (`sale_enabled` and `sale_allow_discount_codes` auto-render as toggles via `form_field.html`; the rest as themed inputs/select. `.pl-sale-fields__title` gets the `margin-top:1.75rem` spacing; `.pl-sale-fields__hint` reuses `hub-text-muted`.)

- **Non-field error visibility (blocker B1):** the free-class-conflict and price-required errors from §5a attach to `price_cents` (a **visible** field, unlike the `sale_enabled` toggle which renders no errors). In addition, `admin/class_form.html:41-47` already renders a `{% if form.non_field_errors %}` block — **add the identical block to `teach/class_form.html`** (it currently has none) right after its second field loop, so any future non-field sale error is never swallowed:

  ```django
  {% if form.non_field_errors %}
  <ul class="pl-field-errors" style="margin-bottom:1rem;">
    {% for error in form.non_field_errors %}<li class="pl-field-error">{{ error }}</li>{% endfor %}
  </ul>
  {% endif %}
  ```

- **Layout & container:** a **`<fieldset class="hub-card pl-sale-fields">`** with an `<h3>Sale</h3>` heading and a one-line hint ("Put this class on sale — a banner and the sale price show automatically."). Inline on the page (this is a 4+-field cluster on an already-long form — no modal). Everything sits inside `.pl-form-group` wrappers (via `form_field.html`), which are already dark/light-correct **including native `<option>`** — so no new form-control CSS and no Rule-13 white-box risk.
- **Components used:** `components/form_field.html` for every field. `sale_enabled` and `sale_allow_discount_codes` auto-render as **`components/toggle.html`** (they're booleans). `sale_kind` renders as a themed `<select>`; `sale_percent` and `sale_amount_cents` as number/text inputs; `sale_banner_text` as a text input pre-filled with the default.
- **The controls, named explicitly:**
  - **Sale on/off:** `sale_enabled` toggle, label "Put this class on sale", description from `help_text`.
  - **Kind:** `sale_kind` select — "Percent off" / "Dollar amount off".
  - **Amounts:** `sale_percent` ("Percent off", 1–99) and `sale_amount_cents` ("Amount off ($)"). **Both are always shown** (no JS dependency) — the form validates that the one matching the chosen kind is filled and ignores the other. Progressive disclosure is *optional* (see below) and must degrade.
  - **Banner headline:** `sale_banner_text` text input, pre-filled with `DEFAULT_SALE_BANNER_TEXT` so it's catchy out of the box; a hint reads "Shown across the top of the class page. Leave blank to use the default."
  - **Stacking:** `sale_allow_discount_codes` toggle — "Allow discount codes on top of the sale", description "Off means the sale price can't be combined with other offers."
  - **Save:** **no new Save button** — the section rides the form's existing submit row (`teach/class_form.html:59-68`: "Save Draft" / "Save & Submit for Review"; admin: its own submit). Full-page POST → the existing view redirect + Django `messages.success` (this is a page form, not an HTMX mutation — Django messages are correct here, not a toast).
- **Optional progressive disclosure (must degrade):** if we hide the two amount inputs based on `sale_kind` with Alpine, do it with `x-data` on the fieldset and `x-show` on each amount's `.pl-form-group` — and **put no `display` in an inline `style`** on those `x-show` elements (Rule 12); the `.pl-form-group` class supplies layout. With JS off, both inputs simply show (the validation still guides the user). Recommended default: **skip the JS**, show both — simplest, zero-risk, matches "fine to always show."
- **States:**
  - *Empty / first time:* toggle off; banner field pre-filled with the default; amounts blank. Reads as "no sale."
  - *Validation error:* `form_field.html` renders `.pl-field-error` under the specific field (percent out of range, amount ≥ price, free-class conflict) — the exact `add_error` messages from §5a. Page re-renders with the values kept.
  - *Success:* redirect + "Class updated." (teach) / "…is published." (admin), per the existing views.
  - No loading state (full-page POST, not HTMX).
- **Dark + light:** all controls inherit `.pl-form-group` theme tokens (`--hub-input-bg` / `--hub-input-border` / `--hub-text`) and the existing `.pl-form-group select option` rule — **verify both themes**, but no new control CSS. The `.pl-sale-fields` fieldset is a `.hub-card`, already theme-aware. Any accent uses `--color-tuscan-yellow` / existing tokens only.
- **Spacing:** the `<h3>Sale</h3>` gets `margin-top: 1.75rem` (matching the "Dates & sessions" / "Gallery images" headings on the same form) so it clears the Member-discount field above it (Rule 18). No control butts against the next section.
- **Mobile:** `.pl-form-group` fields are full-width and stack; the fieldset reflows with the rest of the form. Toggles are real tap targets. No table.

### 6b. Catalog card (`public/_list_results.html`)

- **SALE badge:** after the Flex/series badges (`_list_results.html:65-66`), add
  `{% if offering.sale_is_active %}<span class="badge sale">Sale</span>{% endif %}`.
  New badge in the existing vocabulary (`cms-public.css:471-475`), committed to a **RED accent** treatment (tinted `--red` fill + border, like `.badge.pv`), **not** gold — gold would be visually indistinguishable from the gold-filled `.badge.series` when a series class is also on sale and both render together. Verify legibility on Obsidian **and** Slate:

  ```css
  .cp-page .badge.sale { background: rgba(248,113,113,.14); color: var(--red); border: 1px solid rgba(248,113,113,.30); }
  ```
- **Struck original + sale price** in the footer (`_list_results.html:119-124`). When on sale, the big gold `.cls-price` shows the **sale** price, preceded by a small struck original; the member line reads off the sale price:

  ```django
  <div>
    {% if offering.sale_is_active %}
      <span class="cls-price--was">{{ offering.price_cents|cents_as_price }}</span>
      <span class="cls-price">{{ offering.sale_price_cents|cents_as_price }}</span>
    {% else %}
      <span class="cls-price">{{ offering.price_cents|cents_as_price }}</span>
    {% endif %}
    {% member_price_cents offering.sale_price_cents offering.member_discount_pct as mp %}
    {% if mp %}<span class="cls-price-member">({{ mp|cents_as_price }} for Past Lives Members)</span>{% endif %}
  </div>
  ```

  (`sale_price_cents == price_cents` off-sale, so the member tag is correct in both branches — no `{% if %}` duplication for it.)
- **New CSS** (`cms-public.css`): `.cp-page .cls-price--was { text-decoration: line-through; color: var(--text3); font-weight: 400; font-size: 12px; margin-right: 4px; }` — muted, both themes.
- **States:** no new empty/loading/error — the card is read-only. Grouped multi-date cards reflect the **lead** offering's sale state, exactly as the price already reflects the lead offering (noted in §10).
- **Mobile:** the badge sits inline in the title (existing `.badge` wraps with `vertical-align:middle`); the two price spans sit inline in `.cls-footer` (already `flex; flex-wrap`). No overflow.

### 6c. Class detail page (`public/detail.html`)

- **Animated announcement banner strip** — the "fancy, catchy" centerpiece. Add immediately after the preview banner and before the hero (`detail.html:13`):

  ```django
  {% if offering.sale_is_active %}
  <div class="cp-detail__sale-banner" role="status">
    <span class="cp-detail__sale-banner-text">{{ offering.sale_banner_display }}</span>
    <span class="cp-detail__sale-banner-pill">{{ offering.sale_savings_display }}</span>
  </div>
  {% endif %}
  ```

  - **CSS (`cms-public.css`, no inline styles):** full-width strip, gold→navy gradient background with a **subtle moving shimmer** (a `linear-gradient` sheen animated via `@keyframes cp-sale-shimmer` on `background-position`), rounded like `.cp-detail__preview-banner`, bold heading font. The savings pill is a small contrasting chip.
  - **`prefers-reduced-motion: reduce` → animation: none** (static gradient still reads as a sale) — mandatory.
  - **Both themes:** gradient/pill use `--gold`, `--navy`/`--navy-dark`, `--gold-glow`, `--text` tokens; verify the shimmer is visible on Obsidian **and** Slate.
- **Rail price row** (`detail.html:360-365`): struck original + sale, member line off the sale price:

  ```django
  <div class="cp-detail__price-row">
    {% if offering.sale_is_active %}
      <div class="cp-detail__price--was">{{ offering.price_cents|cents_as_price }}</div>
      <div class="cp-detail__price">{{ offering.sale_price_cents|cents_as_price }}</div>
      <div class="cp-detail__sale-note">{{ offering.sale_savings_display }}</div>
    {% else %}
      <div class="cp-detail__price">{{ offering.price_cents|cents_as_price }}</div>
    {% endif %}
    {% if member_price_cents %}
      <div class="cp-detail__price-member">{{ member_price_cents|cents_as_price }} <span>for Past Lives Members</span></div>
    {% endif %}
  </div>
  ```

  `member_price_cents` context is the sale-aware value from §5d. New CSS: `.cp-detail__price--was` (line-through, `--text3`, ~20px), `.cp-detail__sale-note` (small gold caption). The CTA button copy is unchanged ("Register now" / "Register — Free"); the sale price sits prominently in the row directly above it (see §10 for the reading of the "CTA shows sale price" note).
- **States:** read-only page; the banner simply isn't rendered when `sale_is_active` is false. No dead ends.
- **Mobile:** the banner is full-width and wraps text above the pill (flex `wrap`); the price row already wraps (`cms-public.css:943-949`).

### 6d. Register page (`public/register.html`)

- **Summary** (`register.html:33-38`): struck original + sale, member line off sale price:

  ```django
  <div class="reg-summary">
    <div>{{ offering.title|strip_date_suffix }}</div>
    {% if offering.sale_is_active %}
      <div class="total"><span class="reg-was">{{ offering.price_cents|cents_as_price }}</span> {{ offering.sale_price_cents|cents_as_price }}</div>
      <div class="reg-sale-badge">{{ offering.sale_savings_display }}</div>
    {% else %}
      <div class="total">{{ offering.price_cents|cents_as_price }}</div>
    {% endif %}
    {% if member_price_cents %}<div style="font-size:11px;color:var(--steel-dim);margin-top:2px">Past Lives Members: {{ member_price_cents|cents_as_price }} (auto-applied if your email matches a verified member)</div>{% endif %}
    ...
  </div>
  ```

  The member-note `<div>` **keeps its existing inline style verbatim** (it's a pre-existing non-control element — Rule 9's one-off exception; don't churn it into a class). Only the genuinely-new elements below get CSS.

- **Submit button** (`register.html:186-188`): show the sale price — `Next — {{ offering.sale_price_cents|cents_as_price }}` (free path unchanged: "Confirm Registration"). **The free-vs-paid branch stays keyed on `offering.price_cents == 0`, NOT `sale_price_cents`** — a paid class on sale is still a paid class and goes through Stripe; validation guarantees `sale_price_cents > 0` for a paid class, so a sale price can never make this branch flip to the free label. (A total that reaches exactly $0 only after member+code is handled at POST by `compute_final_price_cents` / the free-confirm branch, not by this button's label.)
- **Code-blocked state — the load-bearing UX** (`register.html:114-118`). Because `__init__` **pops** `discount_code` when the sale blocks stacking, `form.discount_code` is falsy; render an explicit note instead of the input so the buyer is told up-front, never after submit:

  ```django
  {% if form.discount_code %}
    <div class="reg-field">
      <label for="{{ form.discount_code.id_for_label }}">Discount code (optional)</label>
      {{ form.discount_code }}
      {% if form.discount_code.errors %}<ul class="reg-errors">{% for e in form.discount_code.errors %}<li>{{ e }}</li>{% endfor %}</ul>{% endif %}
    </div>
  {% elif offering.sale_is_active %}
    <div class="reg-sale-note">Sale price applied — discount codes can't be combined with this offer.</div>
  {% endif %}
  ```

  (When codes **are** allowed on top of the sale, the field is present as normal and stacks per §5b.)
- **CSS (`classes-register.css`):** `.reg-was` (line-through, muted `--steel-dim`/`--text3`), `.reg-sale-badge` (small gold caption), `.reg-sale-note` (info note reusing the existing muted-note pattern on this page). No inline `background`/`color` on any control; the note is a `<div>`, not a form control.
- **States:**
  - *Happy path:* summary shows struck+sale, button shows sale price, submit → Stripe/free-confirm as today.
  - *Codes blocked:* the note appears, no code box — the buyer can't get stuck typing a code that would be rejected.
  - *Total < $0.50 after sale (+member/code when allowed):* the existing friendly non-field error at `register.html:70-74` fires (Rule from §5b). *Total == $0:* free-confirm path, "Confirm Registration."
  - *Error:* form re-renders with field errors as today.
- **Dark + light:** register controls stay inside `.reg-field` (dark-safe, native `option` themed); new elements are non-control `<div>`s using tokens. Verify both themes.
- **Mobile:** summary and note are full-width blocks; the struck+sale prices sit inline and wrap. No table.

### Checklist sign-off

- Primary action obvious on every screen? Yes — one Sale toggle on the form; "Register now"/"Next — $X" on the public side.
- Complete the task without a dead end? Yes — banner text has a default (never blocks save); blocked codes are explained before submit, not after.
- Half-built anything? No — a sale can be enabled, edited, and turned off from the same section; every price surface reflects it.
- Non-technical lead understands it? Yes — "Put this class on sale", "Percent off / Dollar amount off", "Allow discount codes on top of the sale."
- Simple? One section, one badge, one banner, struck prices — no new pages, no scheduler.

## 7. Notifications / emails / activity

- **No new email.** The confirmation email's amount is already what was paid; the Stripe **product name** gains " (Sale)" (§5c) so the receipt/line item reflects the sale. A line-item *breakdown* (was/now, savings) is **out of scope** (§10).
- **No new notification / broadcast** — a sale is a self-serve pricing toggle, not an announcement (Discord/blast is a separate future idea, §10).
- **Activity log:** none required. (Optional future: a `SiteActivity`/`CmsActivity` "sale enabled/disabled" entry — deferred, §10.)

## 8. Build order (phased; each phase ships green)

1. **Model + migration.** Add `SaleKind`, `DEFAULT_SALE_BANNER_TEXT`, the six fields, and the three properties; generate `0049_classoffering_sale_fields` (run `ruff format` and `git add` the migration together — CI checks `ruff format --check`). Model specs (§9) green; full suite + lint + mypy green.
2. **Forms + pricing pipeline.** Add `_SaleMixin`, wire it into both class forms (fields + `clean_sale_fields`), and make the four `RegistrationForm` edits (order, `sale_blocks_codes`, auto-apply skip, sale-aware base). Form + registration-form specs green.
3. **Checkout + view glue.** Product-name " (Sale)" suffix; sale-aware `member_price_cents` context in the register + detail views. Register-view specs green.
4. **Templates + CSS.** New Sale section partial + skip-list edits on both edit forms; catalog badge/price; detail banner + rail; register summary/button/code-note. New CSS in `cms-public.css` + `classes-register.css`. **Verify dark AND light** on all four screens and the shimmer's reduced-motion fallback; verify mobile reflow. Markup-assertion specs green.
5. **Housekeeping.** Bump `plfog/version.py` `VERSION` and add a **new, grouped, member-facing** CHANGELOG entry stamped at that VERSION (§9 note).

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*` (never `context_*` — not collected), factory-boy, run in the `plfog-web` Docker image against the ≥98% gate (`--no-cov` for subsets). Factories need no schema change — sale fields default off; tests pass `sale_enabled=True, sale_kind=…, sale_percent=…/sale_amount_cents=…` to `ClassOfferingFactory`.

**`classes/spec/models/class_offering_spec.py`** (extend):
- `describe_sale_price_cents`: percent (e.g. 20% off $50 → $40 / 4000), fixed ($15 off $50 → $35 / 3500), and `== price_cents` when `sale_enabled=False`.
- `describe_sale_is_active`: True when enabled+kind+amount+paid; False when disabled, when `price_cents == 0` (free), and when the matching amount is missing (percent enabled but `sale_percent=None`).
- `describe_sale_savings_display`: "20% off" / "$15 off" / "" off-sale.

**Sale-form validation** (new `classes/spec/forms/class_sale_form_spec.py`, mirroring the welcome-email spec family; cover **both** `TeachClassOfferingForm` and `ClassOfferingForm`):
- enabling with no kind-amount → error on the matching field; percent 0/100 → out-of-range error; fixed ≥ price → "less than the price"; free class (`is_free` or price 0) + sale → the free-class error attached to **`price_cents`** (a visible field, not the `sale_enabled` toggle — guards blocker B1).
- **Stripe-floor authoring rejection (M1):** a sale whose result lands in 1–49¢ (e.g. 99% off a $1.00 class) → error "…below the $0.50 minimum we can charge online" on the amount field; a sale resulting in exactly $0 or ≥ 50¢ is accepted.
- blank `sale_banner_text` while enabled → saves with the default text (no error).
- valid percent and valid fixed each round-trip onto the instance; `sale_allow_discount_codes` persists both ways.

**`classes/spec/forms/registration_form_spec.py`** (extend):
- `describe_compute_final_price_cents`: order is sale → member → coupon. Seed member + a code; assert the final equals `code.apply_to(member% of sale_price)` when stacking allowed.
- sale blocks codes: `sale_allow_discount_codes=False` → `discount_code` field absent, `sale_blocks_codes` True, a submitted code is ignored (coupon step skipped), and `_find_auto_apply_discount` returns None / isn't prefilled.
- codes allowed: `sale_allow_discount_codes=True` → code stacks on the sale.
- member discount computes off the sale price (member % applied to `sale_price_cents`, not `price_cents`).
- **sale-OFF regression:** with `sale_enabled=False`, `compute_final_price_cents` and the whole pipeline behave exactly as today — `sale_price_cents == price_cents`, `discount_code` field present, auto-apply and member/coupon math unchanged (a member+code case equals the pre-feature result). Guards against the sale branch leaking into the no-sale path.
- **auto-apply off the SALE base when stacking is ON:** with `sale_allow_discount_codes=True` and a class-scoped auto-apply code, `_find_auto_apply_discount` selects/computes the best code against `sale_price_cents` (post-member when a member matches), not `price_cents` — the prefilled code and final total reflect the sale base.
- Stripe floor: a sale landing the total in $0.01–$0.49 raises the "less than $0.50" `ValidationError`; a sale (± member/code) reaching exactly $0 does **not** raise and takes the free path.

**`classes/spec/views/register_spec.py`** (extend, near `describe_register_with_discount_code:354`):
- on-sale GET: summary shows struck original + sale price; submit button shows the sale price; when codes blocked, no code input and the "can't be combined" note is present.
- on-sale POST (paid): checkout is created with `amount_cents == sale_price` and `product_name` ending in " (Sale)" (assert via the `create_class_checkout_session` call / stub).
- on-sale POST reaching $0: free-confirm path (CONFIRMED, no Stripe).

**`classes/spec/views/member_pricing_copy_spec.py`** (extend — markup assertions): catalog card renders the `badge sale` + struck `.cls-price--was`; detail page renders `.cp-detail__sale-banner` with the banner text + `sale_savings_display`; register summary renders the struck original. Assert on **markup/classes**, not visible copy that could collide with the changelog widget.

**tz/date gotchas:** none new (sale has no date window). Keep using explicit offsets for session seeding so `is_bookable` stays deterministic.

**Changelog note:** this is a **net-new member-facing feature**, so add a **new grouped entry at the top** of `CHANGELOG`, stamped at the bumped `VERSION` (e.g. 0.23.12 to extend the current line, or 0.24.0 to open a new one — builder's call at merge per CLAUDE.md; keep the entry's `version == VERSION` so `announce_release` resolves). Sample copy: *"Classes can now go on sale — instructors flip a toggle for a percent or dollar discount, and the class shows a bright sale banner with the original price crossed out. You'll always see the sale price before you pay."* Plain language, no jargon/PR refs.

## 10. Open / deferred

- **"CTA shows sale price" reading.** The detail-page "Register now" CTA has never displayed the paid price (only "Register — Free" for free classes); this spec puts the struck-original + sale price in the price row **directly above** the CTA and leaves the CTA copy navigational. If product wants the price literally on the button, it's a one-line label tweak — flagged, not built.
- **Grouped multi-date cards.** A catalog card that groups several runs shows the **lead** offering's sale badge/price (matching how price already works). Per-run sale differences aren't surfaced on the collapsed card — out of scope; the detail/register pages are per-run and always correct.
- **Confirmation-email breakdown.** No was/now/savings line in the receipt email — locked out of scope; the Stripe product name carries " (Sale)".
- **Scheduling / auto-expiry.** No start/end date — YAGNI; the toggle is the schedule.
- **Announcing a sale** (Discord `#classes`, member email blast, "on sale now" catalog filter) — not requested; a sale is a quiet pricing change. Possible future feature.
- **Sale activity log entry** — not built; add a `CmsActivity` "sale enabled/disabled" record later if instructors want an audit trail.
- **Benign toggle race.** If a buyer had the register page open *before* an instructor turned on a non-stacking sale, then submits a discount code they'd typed, the code is **silently dropped** (the sale-blocks-codes guard in `compute_final_price_cents` ignores it), not rejected with an error. This is deliberate and benign — the buyer still gets the sale price (typically the better deal), never a 500 or a confusing "code rejected." No locking or re-validation is added for this edge; stated here so it isn't mistaken for a bug.
