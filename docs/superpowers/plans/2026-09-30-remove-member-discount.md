# Remove the automatic member discount on classes

**Date:** 2026-09-30. **Decided by:** Felix. **Status:** agreed, building.

## User story

As an admin, when I price a class, I want the price to be the price. Currently every class carries
`member_discount_pct` (default 10) that is taken off automatically when the registrant's email matches
a verified member, with a toggle on the register page to decline it. Members should type the member
discount code at checkout instead; that code (`MEMBER10`, a global `DiscountCode`) is already live in
production and is not part of this change.

## Summary

Classes stop taking a percentage off automatically for members; the only price path is full price,
then the sale price if a sale is on, then a typed discount code.

## Goal

One pricing engine with no member detection in it. Less to explain on the page, less to test, and the
composer, settings, emails and receipts stop talking about a discount that no longer exists.

## Current behavior

- `ClassOffering.member_discount_pct` (default 10) and `ClassSettings.default_member_discount_pct` exist.
  `ClassOffering.member_price_cents` and the `member_price_cents` template tag derive the member price.
- `RegistrationForm` resolves the member from the email, applies the percentage, and renders an
  "Apply my member discount" toggle (with a hidden twin) inside the price summary; the HTMX price
  refresh includes it. `Registration.compute_promote_price_cents` applies the same percentage.
- The catalog card, the class detail rail, the register page, the composer (admin field, instructor
  note), the admin settings page, teach pages, review emails, the promote confirm partial, the flyer,
  receipts, the member nudge and the help entries all mention or render it. The catalog has a
  "Member discount available" filter (`members_only=1`).

## Expected behavior

Nothing anywhere mentions, stores, computes or filters on a member discount. Price = sale price (or
full price) -> discount code -> floor at 0.

## Acceptance criteria

- [ ] `ClassOffering` has no `member_discount_pct` and no `member_price_cents`; `ClassSettings` has no
      `default_member_discount_pct`. One migration (`classes/0071_...`) removes both columns. Its
      reverse re-adds them with default 10 (RemoveField reverses itself; no RunPython needed).
- [ ] `RegistrationForm`: no `apply_member_discount` field, no `member_discount_pct` property, no toggle
      helpers (`clean_apply_member_discount`, `_toggle_absent_from_post`, `_toggle_as_shown`,
      `quotes_member_discount`). `_price_cents` takes the code only. `_wire_price_refresh` includes only
      `[name=email],[name=discount_code]`. The `member` argument stays (the registration still links to
      the Member row) and never affects the price.
- [ ] `classes/views.py`: `_registration_request_state` stops reading `apply_member_discount` from the
      GET; `_class_detail_context` stops computing `member_price_cents`; the catalog view drops the
      `members_only` filter (an URL still carrying `members_only=1` is ignored); the
      `compute_member_price_cents` import goes.
- [ ] `Registration.compute_promote_price_cents`: sale price, then the stored code (unless the sale
      blocks codes), floor at 0. Docstring updated.
- [ ] Forms: `ClassOfferingForm` drops the field, its label, its initial and `clean_member_discount_pct`
      (and the `MAX_MEMBER_DISCOUNT_PCT` / `MEMBER_DISCOUNT_RANGE_MESSAGE` constants if nothing else uses
      them); `TeachClassOfferingForm` drops the "studio default on create" assignment and the docstring
      sentence; `ClassSettingsForm` drops `default_member_discount_pct`.
- [ ] `classes/composer.py` step 3 fields drop `member_discount_pct`.
- [ ] Templates, each with no remaining member discount text or markup:
      `templates/classes/public/_class_card.html` (the "for Past Lives Members" line),
      `public/detail.html` (the member price line), `public/register.html` (the "Member discount
      applied." line and the toggle block), `public/list.html` (the "Member discount available" filter
      checkbox), `_components/class_composer.html` (step 1 note stops pointing at step 3; step 3 section
      titled "Seats", note "How many can attend.", no admin field, no instructor note),
      `teach/class_overview.html`, `teach/class_form_published.html`, `admin/class_review.html`,
      `emails/review_request.html` + `.txt`, `emails/admin_validation_request.html` + `.txt`,
      `partials/promote_confirm_body.html`, `class_flyer.html` (the member price line),
      `account/receipts.html` (subtitle "Class purchases. Free registrations don't appear here." for
      everyone; no `is-discount` class), `account/_components/member_nudge.html` (drop "member pricing
      on classes" from the dues pitch, keep the rest).
- [ ] Help copy: `core/help_registry.py` `class.register` and `teach.class-pricing` short texts, and
      `membership/help_content.py` (the catalog Filters sentence near line 486, the "Fair pricing" tip
      near line 964) no longer promise automatic member pricing. Plain words, no dashes.
- [ ] Dead CSS removed: `.cp-detail__price-member` and `.cp-detail__price-member span`
      (`static/css/cms-public.css`), `.cp-page .cls-price-member` (same file),
      `.bk-rec-amount.is-discount::before` (`static/css/book-account.css`).
- [ ] `classes/templatetags/classes_tags.py`: `member_price_cents` tag removed.
- [ ] Test data: `classes/factories.py`, `classes/spec/conftest.py`,
      `core/management/commands/demo_data.py`, `tests/e2e/screenshot_seed.py` and
      `scripts/generate_fixture.py` no longer set `member_discount_pct` (grep each).
- [ ] Specs updated or deleted so the suite is green: delete
      `classes/spec/views/member_pricing_copy_spec.py` and the `#369` member discount describe blocks in
      `classes/spec/views/class_composer_spec.py`; strip `member_discount_pct` from payloads and
      factories in `admin_classes_spec.py`, `teach_dashboard_spec.py`, `composer_spec.py`
      (`step_for_field`), `settings_spec.py`, `class_offering_spec.py` (`describe_member_price_cents`),
      `registration_roster_spec.py`, `forms/registration_form_spec.py`, `views/register_spec.py`,
      `views/admin_settings_spec.py`, `tests/classes/account/receipts_spec.py` (the two is-discount
      specs), `public_spec.py` (`it_filters_members_only`). Grep the whole tree for
      `member_discount`, `member_price`, `apply_member_discount`, `members_only`, `is-discount`,
      `quotes_member_discount` and leave nothing behind outside `changelog/history.json`,
      `docs/superpowers/` and migrations.
- [ ] `tests/e2e/register_price_refresh_spec.py` rewritten around what still refreshes: a logged in
      member lands on a $100 class and the label reads $100; typing a valid 10 percent global code and
      tabbing out swaps the label to $90; clearing it swaps back to $100. Drop the toggle and
      membership cases. Keep the module docstring honest.
- [ ] New coverage: a `RegistrationForm` spec proving a verified member's email is charged the full
      price (and the sale price when a sale is on), and a `public_spec` case proving `members_only=1`
      no longer narrows the catalog. Both in the existing spec files' style (pytest-describe,
      factories).
- [ ] `DATABASE_URL="sqlite:///tmp-check.sqlite3" .venv/bin/python manage.py check` and
      `makemigrations --check` clean; `ruff check .` and `ruff format --check .` clean; mypy on
      `plfog/ core/ membership/ hub/` clean (`classes/` judged against its ~26 pre-existing errors);
      `tests/template_comment_lint_spec.py` green.

## Area

Classes pricing: models and one migration, the registration form and its price refresh, the public
catalog, detail and register pages, the composer, admin settings, teach pages, review emails,
receipts, help copy, CSS, factories and specs.

## Constraints

- The migration applies while the previous release still serves (STANDARDS.md section 10). A
  RemoveField makes the old code's SELECTs fail for the seconds between migrate and the traffic
  switch; `0011` and `0013` removed fields the same way. Accepted; the PR body names it.
- Leave `DiscountCode.auto_apply` (per class auto apply sale codes), the Sale feature (`sale_*`),
  discount code requests and `Member.can_self_approve_discounts` alone. Separate features.
- No dashes in member facing copy. Single line `{# #}` template comments. No inline styles added.
- The changelog fragment is the orchestrator's; do not add one.
- `membership/help_content.py` reaches production only through `seed_help_center`; the PR body says so.

## Out of scope

Removing `DiscountCode.auto_apply`; announcing or changing the `MEMBER10` code; membership dues.
