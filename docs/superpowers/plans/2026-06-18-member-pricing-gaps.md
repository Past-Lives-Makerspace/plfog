# Member Pricing — Remaining Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining gaps in the already-shipped member-pricing feature. The pricing engine, member detection, discount application, and "member rate" UI are all functional. The single certain piece of work is a **copy fix**: the public UI says "PL members" / "Members:" where the original feature spec *explicitly* requires the literal string **"Past Lives Members"** and *forbids* generic abbreviations like "PL". The other items in this plan are **decisions** (explicit `member_price` field vs. discount %, and the AirTable "active check") that, after investigation, are recommended as **no-ops** — they are documented here so the call is made deliberately, not silently.

**Architecture:** This is a copy/label change plus a documented decision record. No model, manager, view, form, or pricing-math changes. The member discount is stored as `ClassOffering.member_discount_pct` (per-class, default 10) with a site default in `ClassSettings.default_member_discount_pct`; the discounted figure is derived by `ClassOffering.member_price_cents` and the `member_price_cents` template tag. Member identity is detected from the local `Member` mirror, which is itself kept current from AirTable by the inbound `airtable_pull`. None of that moves.

**Tech Stack:** Django templates (the three edits), pytest + pytest-describe for a render-level regression guard, factory-boy for fixtures. No new dependencies, no migrations, no JS, no CSS.

---

## Background / context for the implementer

### The original feature spec (intended behavior)
> Past Lives Member Dynamic Pricing: store explicit standard_price and member_price; at render, detect member identity (local DB or active AirTable check) and apply member_price automatically. Render the explicit string "Past Lives Members" next to the reduced rate. Do NOT use generic abbreviations like "PL". Keep backend coupon/system codes masked from the DOM.

### What already exists and works (verified — do NOT rebuild)
- **Pricing fields** — `classes/models.py:167–168`: `price_cents` (full price) and `member_discount_pct = PositiveIntegerField(default=10, help_text="Auto-applied for verified members.")`.
- **Derived member price** — `classes/models.py:513–522`: `ClassOffering.member_price_cents` property returns `int(price_cents * (100 - member_discount_pct) / 100)`, or `None` when `member_discount_pct` is 0 (so callers treat `None` as "no separate member price to show").
- **Site-wide default** — `classes/models.py:1351–1353`: `ClassSettings.default_member_discount_pct = PositiveIntegerField(default=10, …)`.
- **Template tag** — `classes/templatetags/classes_tags.py:150–155`: `member_price_cents(price_cents, discount_pct)` mirrors the model property for the list view.
- **Member detection** — `classes/views.py:397–408`: `_member_for_email(email)` returns a verified `Member` whose `EmailAddress` matches (case-insensitive, `verified=True`) from the **local DB**.
- **Discount applied at checkout** — `classes/forms.py:675–688`: `RegistrationForm.member_discount_pct` returns the offering's discount only when `self.member` is set; `compute_final_price_cents()` applies the member discount first, then stacks any validated discount code on top, clamped at `max(0, …)`.
- **Member bound during registration** — `classes/views.py:451–464`: the register view re-reads the posted email, calls `_member_for_email()`, and passes the resolved `member` into `RegistrationForm` so the discount surfaces in the total.

### The copy violation (the one certain gap)
Three public templates render member-pricing copy. Verified by grep:
- `templates/classes/public/detail.html:358` — `… <span>for PL members</span>` (sticky booking rail). **Uses "PL" — spec violation.**
- `templates/classes/public/_list_results.html:112` — `(… for PL members)` (catalog card footer). **Uses "PL" — spec violation.**
- `templates/classes/public/register.html:27` — `Members: … (auto-applied if your email matches a verified member)` (registration summary). **Not "PL", but inconsistent** — should read "Past Lives Members" so all three surfaces match the spec's required string.

The admin/teach/email templates (`admin/class_detail.html:17`, `teach/class_overview.html:17`, `admin/class_review.html:100`, `emails/review_request.*`) say "% member discount" — internal-facing, not the public "member rate" label, and not in spec scope. Leave them.

### Coupon/system-code DOM audit (spec requirement — already satisfied)
Grep of `templates/classes/public/` for `discount_code` / `coupon` / `code` finds only `register.html:76–78`, which renders `form.discount_code` — the **registrant's own input field** for typing a code, not a backend/system code echoed into the DOM. The discounted figure is always shown as a dollar amount (`|cents_as_price`). No system or coupon codes leak into the public DOM. **No work needed; note the finding in the PR.**

### Test surface to mirror
- `classes/spec/views/public_spec.py:58–296` — `describe_public_list` / `describe_public_category`; uses `client.get(reverse("classes:public_list"))` and asserts on `response.content`, with the `published_class` fixture and `ClassOfferingFactory` (see `:196–229` for a `member_discount_pct` example).
- `classes/spec/forms/registration_form_spec.py` — existing member-discount form coverage to extend if needed.

---

## Decisions baked into this plan

- **Decision A — keep `member_discount_pct`; do NOT add an absolute `member_price` field.** The spec wording ("store explicit standard_price and member_price") and the shipped discount-% model are functionally equivalent — `member_price_cents` already derives the absolute figure on demand. The discount-% model is the single source of truth, is already shipped and tested, and has a sensible site-wide default. Introducing a stored absolute `member_price` would create a second field that can drift from `price_cents` and require a migration + backfill + dual-write logic for zero member-visible benefit. **Recommendation: keep the discount-%.** If — and only if — the studio later needs per-class absolute member prices that are *not* a clean percentage of the standard price, revisit; that work is **optional and out of scope here** (sketched in "Follow-up").

- **Decision B — keep local-DB member detection; do NOT add a live AirTable call at checkout.** The spec mentions "local DB or active AirTable check". Investigation: `airtable_sync/config.py:152–154` maps the AirTable `Status` field into `Member.status` during the inbound `airtable_pull` (the CLAUDE.md confirms Member/Space/Lease pull AirTable → Django). So the local `Member` mirror **is** the AirTable state, refreshed by the scheduled pull. `_member_for_email()` reading the local mirror therefore *is* the AirTable check, indirectly — without adding per-checkout network latency or a new failure mode (an AirTable outage would otherwise block member pricing or registration). **Recommendation: no live AirTable call.** (Note: `_member_for_email` matches on verified email only and does not currently filter `status=ACTIVE`; that is existing, intentional behavior and is **not** changed by this plan — flag only, see "Follow-up".)

- **Decision C — testing reality.** The certain change is template copy. We lock it with a render-level regression guard (the public list and detail pages render "Past Lives Members" and never the bare token "PL members") and a price-still-applied assertion, rather than a brittle full-DOM snapshot.

---

## File Structure

- Modify: `templates/classes/public/detail.html:358` — "for PL members" → "for Past Lives Members".
- Modify: `templates/classes/public/_list_results.html:112` — "for PL members" → "for Past Lives Members".
- Modify: `templates/classes/public/register.html:27` — "Members:" → "Past Lives Members:".
- Test: `classes/spec/views/member_pricing_copy_spec.py` (new) — render-level copy + price-applied guard.
- Modify: `plfog/version.py` — version bump + member-friendly changelog entry.

---

## Task 1: Failing copy regression test (red)

**Files:** `classes/spec/views/member_pricing_copy_spec.py` (new)

- [ ] **Step 1: Write the spec** mirroring `public_spec.py`'s client/fixture style. Assert the public catalog and detail pages render the exact string **"Past Lives Members"** and never the forbidden token **"PL members"**. Use a published offering with a non-zero `member_discount_pct` so the member-rate markup renders.
  ```python
  """Member-pricing copy must read 'Past Lives Members' — never the 'PL' abbreviation."""

  from __future__ import annotations

  from django.urls import reverse

  from classes.factories import ClassOfferingFactory  # confirm factory name/path


  def describe_member_pricing_copy():
      def it_uses_full_name_on_the_catalog_card(published_class, client):
          # published_class is a published offering with upcoming sessions; ensure it has a discount.
          published_class.member_discount_pct = 10
          published_class.save(update_fields=["member_discount_pct"])
          body = client.get(reverse("classes:public_list")).content.decode()
          assert "Past Lives Members" in body
          assert "PL members" not in body

      def it_uses_full_name_on_the_detail_rail(published_class, client):
          published_class.member_discount_pct = 10
          published_class.save(update_fields=["member_discount_pct"])
          url = reverse("classes:public_class_detail", kwargs={"slug": published_class.slug})
          body = client.get(url).content.decode()
          assert "Past Lives Members" in body
          assert "PL members" not in body
  ```
  > Confirm against `public_spec.py`: the `published_class` fixture (it must have an upcoming session so it appears in the list), the `ClassOfferingFactory` import path (`classes/factories.py`), and the detail URL name/kwargs (`classes:public_class_detail` per `views.py`). Reuse the existing fixture rather than building a new offering by hand.

- [ ] **Step 2: Run it and confirm RED** — `pytest classes/spec/views/member_pricing_copy_spec.py -v`. Both `it_*` must fail on the `"Past Lives Members" in body` assertion (the templates still say "PL members"). If they pass, the fixture isn't rendering the member-rate markup — fix the fixture before proceeding.

---

## Task 2: Fix the copy (green)

**Files:** the three public templates.

- [ ] **Step 1: `templates/classes/public/detail.html:358`** — change `<span>for PL members</span>` to `<span>for Past Lives Members</span>`.
- [ ] **Step 2: `templates/classes/public/_list_results.html:112`** — change `for PL members)` to `for Past Lives Members)`.
- [ ] **Step 3: `templates/classes/public/register.html:27`** — change the label `Members:` to `Past Lives Members:` (leave the parenthetical "(auto-applied if your email matches a verified member)" — it explains the behavior and is fine).
- [ ] **Step 4: Re-grep to prove no occurrence remains:**
  ```bash
  grep -rniE "for PL member|PL members" templates/ && echo "STILL PRESENT — fix" || echo "clean"
  ```
  Expected: `clean`.
- [ ] **Step 5: Run the spec and confirm GREEN** — `pytest classes/spec/views/member_pricing_copy_spec.py -v`. Both pass.

---

## Task 3: Member-price-still-applied regression guard (green)

**Files:** `classes/spec/views/member_pricing_copy_spec.py` (extend) — or reuse existing form coverage if it already asserts this.

The copy change must not have disturbed the actual discount. Add one assertion that the discounted figure is still computed and shown, so a future copy edit can't accidentally drop the member-rate block.

- [ ] **Step 1: First check** `classes/spec/forms/registration_form_spec.py` for an existing "member discount applied" test. If one exists and covers `compute_final_price_cents()` with a member, **do not duplicate** — just note it in the PR and skip to Task 4.
- [ ] **Step 2: If not covered**, add a guard that the catalog renders the discounted dollar amount for a discounted offering:
  ```python
  def it_still_shows_the_discounted_member_price(published_class, client):
      published_class.price_cents = 10000
      published_class.member_discount_pct = 10
      published_class.save(update_fields=["price_cents", "member_discount_pct"])
      body = client.get(reverse("classes:public_list")).content.decode()
      assert published_class.member_price_cents == 9000
      assert "$90" in body  # the 10%-off figure, rendered by cents_as_price
  ```
  > Adjust the expected string to whatever `cents_as_price` actually emits ("$90.00" vs "$90") — read the filter in `classes/templatetags/classes_tags.py` and match it exactly.
- [ ] **Step 3: Run** `pytest classes/spec/views/member_pricing_copy_spec.py -v`. All pass.

---

## Task 4: Lint / format / type-check

- [ ] **Step 1:** `ruff format . && ruff check .` (templates are untouched by ruff; this covers the new spec).
- [ ] **Step 2:** `mypy .` — export `DATABASE_URL` first if running before push: `export $(grep '^DATABASE_URL=' .env | xargs)`.

---

## Task 5: Version bump + changelog

**Files:** `plfog/version.py`

- [ ] **Step 1: Bump `VERSION` to the next patch after the merged release.** At time of writing the latest is `2.5.8` (PR #108, in flight). **Verify the actually-merged version first** (`git log`/the merged `plfog/version.py` on `main`) and use the next patch — likely `2.5.9`; do not assume.
- [ ] **Step 2: Prepend a member-friendly `CHANGELOG` entry** (plain language — this posts to Discord):
  ```python
  {
      "version": "2.5.9",  # verify
      "date": "2026-06-18",  # set to merge date
      "title": "Clearer 'Past Lives Members' pricing label",
      "changes": [
          "Class pages now spell out 'Past Lives Members' next to the member rate instead of the abbreviation 'PL members', so the discount is clear to everyone browsing the catalog.",
      ],
  }
  ```
- [ ] **Step 3: Commit** on a small focused branch off `main`.

---

## Final verification

- [ ] `pytest` — all pass, 100% coverage (the new spec is small and self-covering).
- [ ] `ruff format . && ruff check . && mypy .` — clean.
- [ ] `grep -rniE "PL members" templates/` — no output.
- [ ] Quick visual pass on `book.pastlives.space` (run skill): catalog card, detail rail, and register summary all read "Past Lives Members"; the discounted dollar figure still shows; no coupon/system code visible in page source.

---

## Follow-up (out of scope for this plan)

- **Absolute per-class `member_price` field (Decision A).** Only if the studio needs member prices that aren't a clean percentage of the standard price. Would require a nullable `member_price_cents` field, a migration with a reverse, `member_price_cents` property precedence (explicit field wins over derived %), and form/admin input in dollars. File a separate plan if the need is confirmed — do not pre-build it.
- **`status=ACTIVE` filter on member detection (Decision B note).** `_member_for_email()` (`views.py:397–408`) matches on a verified email regardless of `Member.status`. If the studio wants *former* members to lose the member rate at checkout, add `status=Member.Status.ACTIVE` to the filter — but confirm that's the intended policy first, since it's a behavior change, not a bug, and would also need a test for the former-member path.
- **Live AirTable check at checkout (Decision B).** Explicitly declined here for latency/reliability reasons. Revisit only if the `airtable_pull` cadence proves too stale for membership status to be trustworthy at registration time.
