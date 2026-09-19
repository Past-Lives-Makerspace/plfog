# 369: Member discount: admin-only, declinable at checkout, quoted right up front

Issue: https://github.com/Past-Lives-Makerspace/plfog/issues/369
Branch: fog/member-discount-pricing
Brief for this round: surgical, pragmatic, YAGNI. The issue's open questions are decided
below. Do not reopen them; do not add a setting, a model field, or a second pricing path.

## Decisions (locked)
- D1. Item 1 is a hard admin-only gate. No toggle setting.
- D2. `ClassSettings.default_member_discount_pct` is WIRED UP, not deleted: a new class from
  either composer starts at the setting's value instead of the hardcoded model default of 10.
  Existing rows keep their value.
- D3. Item 2: a declined discount is NOT tracked. No new field on `Registration`. The registrant
  pays the higher number and that is the whole story.
- D4. Item 2 copy: label "Apply my member discount", hint "Turn this off to pay full price and
  give the difference to the space." Default ON.
- D5. Item 2 waitlist: the toggle is hidden on the waitlist form exactly like `discount_code`.
  A promoted waitlister always gets the discount (`compute_promote_price_cents` unchanged).
- D6. Items 2 and 3 share ONE price summary block refreshed by HTMX, and ONE pricing function
  serves both the quote and the charge.

## Item 1: instructors cannot set the discount
- `classes/forms.py` `TeachClassOfferingForm`: remove `"member_discount_pct"` from
  `Meta.fields` and the label line in `__init__`. `ClassOfferingForm` keeps it.
- D2 wiring:
  - `TeachClassOfferingForm.__init__`: when `self.instance.pk is None`, set
    `self.instance.member_discount_pct = ClassSettings.load().default_member_discount_pct`
    (the field is off the form, so `construct_instance` leaves it and `save()` writes it).
  - `ClassOfferingForm.__init__`: when `self.instance.pk is None`, set
    `self.initial["member_discount_pct"]` to the same value (ModelForm seeds `self.initial`
    from `model_to_dict`, so `field.initial` alone loses to the model default).
- `templates/classes/_components/class_composer.html` ~284: move the include inside
  `{% if is_admin %}`. In the `{% else %}` render a read-only note in the same section from
  `form.instance.member_discount_pct`: "Members get N% off." and, when
  `form.instance.price_cents` is set, the resulting member price via the existing
  `member_price_cents` templatetag. `pl-compose-section__note` style, no new CSS.
- `classes/composer.py` COMPOSER_STEPS: UNCHANGED (`step_for_field` must keep mapping
  `member_discount_pct` to step 3 for the admin error summary; `composer_spec.py` asserts it).
- `composer_spec._teach_data` still posts `member_discount_pct`; that is now the crafted POST
  and must be ignored by the teach form.

## Items 3 + 2: the checkout quotes the price actually charged
Surfaces: `classes/views.py` (`register`, `_register_prefill`), `classes/forms.py`
(`RegistrationForm`), `templates/classes/public/register.html`.

- `_register_prefill`: on GET with no `?email=`, seed `bound_email` from the logged-in user's
  prefill email (`_registration_initial_for_user(request.user).get("email", "")`). Email stays
  the source of truth; `?email=` (the HTMX refresh) still overrides it.
- `RegistrationForm`:
  - New field `apply_member_discount = forms.BooleanField(required=False, initial=True, ...)`
    (D4). Popped in `__init__` when `member is None` OR `is_waitlist` (D5).
  - One private pricing core, e.g. `_price_cents(*, apply_member: bool, code) -> int`:
    sale price, then member pct when `apply_member and self.member`, then the code unless
    `sale_blocks_codes`, then `max(0, ...)`. Both call it:
    - `compute_final_price_cents()` (call site 1): `apply_member` from cleaned data (True when
      the field was popped), `code = _validated_discount`.
    - `_find_auto_apply_discount()` (call site 2): the base handed to `best_auto_apply_for`
      is the price after the member step honoring the toggle, so the cheapest code is chosen
      against the price actually paid.
  - New `quoted_price_cents() -> int` for rendering: toggle and raw code from `self.data` when
    bound, else from `self.initial`; a raw code is looked up leniently (same global-or-this-class
    filter as `clean_discount_code`, `is_currently_valid()`), blank or invalid falls back to
    `auto_applied_discount`. Never raises. This is the number on the summary and the button.
  - Do not leave a second pricing path. `member_discount_pct` may survive only as the
    "applicable pct" helper feeding the core.
- `register` view: on GET, pass `apply_member_discount` and `discount_code` from the query
  into the form `initial` (the HTMX refresh sends them); put `quoted_price_cents` in context;
  drop the `member_price_cents` context and footnote.
- `register.html`:
  - Wrap the summary total (and sale badge) in `<div id="reg-price-summary">`; the total shows
    `quoted_price_cents`. Keep the sale layout (struck original) when a sale is active. When the
    member discount is applied add one line "Member discount applied." A non-member sees the
    full price and NO footnote (delete the "Past Lives Members: $X (auto-applied if...)" line).
  - Submit label: `Next: <span id="reg-submit-label">{{ quoted_price_cents|cents_as_price }}</span>`
    (keep the existing `Confirm Registration` branch for `price_cents == 0`). No dash.
  - Render `apply_member_discount` (when present) inside `#reg-price-summary`, mirroring the
    page's existing `label.reg-check` checkbox pattern (this page does not use hub components).
  - HTMX: the email input, the toggle and the discount code input all carry `hx-get` to the
    register URL, `hx-trigger="change"`,
    `hx-include="[name=email],[name=apply_member_discount],[name=discount_code]"`,
    `hx-target="#reg-price-summary"`, `hx-select="#reg-price-summary"`, `hx-swap="outerHTML"`,
    and `hx-select-oob="#reg-submit-label"` (append `,#custom-questions-block` when the class
    has custom questions; this replaces today's email-only wiring that targets the questions
    block alone). Set the attrs in `__init__` where the current email attrs are set.
  - `?waitlist=1` and claim flows untouched.

## Acceptance (specs)
Item 1
- Teach composer GET as an instructor: no `member_discount_pct` input; the read-only note shows.
- Teach composer POST (create and edit) carrying `member_discount_pct=0`: value unchanged on
  edit, equals the settings default on create.
- Admin composer still saves it; a validation error on it renders in the step 3 error summary
  without raising.
- With `default_member_discount_pct=15`, a new class from either composer is 15.
Items 2 + 3 (`classes/spec/views/register_spec.py`, `classes/spec/forms/registration_form_spec.py`)
- A logged-in verified member GETting the page sees the member price inside
  `#reg-price-summary` and `#reg-submit-label` on first render.
- GET `?email=<non-member>` shows the full price. GET `?email=<member>` with the toggle off in
  the query shows the full price.
- POST with the toggle off charges the full (post-sale, post-code) price: assert the Stripe
  checkout mock amount.
- Auto-apply code choice uses the price actually paid (two class-scoped auto codes whose best
  differs between the discounted base and the full base).
- A non-member never sees the toggle; the waitlist form has no toggle.

## Out of scope
- Tracking, receipting or thanking declined discounts (D3).
- `compute_promote_price_cents` and the promotion path (D5).
- Hub-side reporting, any new setting.
