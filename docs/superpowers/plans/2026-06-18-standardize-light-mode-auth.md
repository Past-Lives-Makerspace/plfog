# Standardize Light Mode — Booking Auth & Onboarding Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the booking-site auth and onboarding pages (`/accounts/login/`, `/accounts/signup/`, login-code confirm, and onboarding steps 1–3) honor the same light/dark theming as the account dashboard (`/account/`). Today the dashboard is fully theme-aware and looks correct in light mode; the auth/onboarding pages render a hardcoded "dark-glass" card that never themes, producing low-contrast inputs and a jarring dark block on the otherwise-light booking surface.

**Reference (the "good" light mode):** `https://book.pastlives.space/account/` → `templates/classes/account/base.html`, wrapped in `.bk-page`.
**Defect (the "bad" light mode):** `https://book.pastlives.space/accounts/login/` → `templates/account/login.html`, wrapped in `.bk-themed-login` → `.bk-auth-card`.

**Architecture:** The fix is CSS-token plumbing, not a redesign. `book-account.css` already defines a complete `--bk-*` token set scoped to `.bk-page` (light defaults that delegate to the theme-aware `--hub-*` tokens, plus a `[data-theme="dark"] .bk-page` override). The auth/onboarding pages live *outside* that scope and hardcode dark colors. We (1) extend the token scope to a single shared `.bk-auth-surface` wrapper applied to every auth/onboarding page, and (2) rewrite the `.bk-auth*` rules to consume those tokens instead of hardcoded values — moving the "dark-glass" glow into a `[data-theme="dark"]` block so dark mode is unchanged and light mode becomes a clean light card with dark-charcoal input text.

**Tech Stack:** Django templates, plain CSS custom properties (no preprocessor), the existing `[data-theme]` system from `hub.css`, pytest + pytest-describe for the (limited) template-render assertions. No new dependencies, no model/DB changes, no JS changes.

---

## Background / context for the implementer

### The token system (already correct — reuse it)
- `static/css/hub.css:37–49` — dark defaults for `--hub-text`, `--hub-card-bg`, `--hub-card-border`, `--hub-surface`, `--hub-input-bg`, …
- `static/css/hub.css:93` — `[data-theme="light"] { … }` flips all of the above to light values (`--hub-card-bg: #ffffff`, `--hub-text: #1D1E1E`, `--hub-input-bg: #f4f6f8`, …).
- `static/css/cms-public.css:331–348` (light) / `:372–374` (dark) — raw brand tokens: `--gold` (constant cream/gold), `--cream` (constant), `--border` and `--text3` (these two DO flip per theme).
- `templates/classes/base_public.html:11` sets `{% block default_theme %}light{% endblock %}` and `:59` renders the `.cp-topbar__theme-toggle` (stores `theme` in `localStorage`; default is light). **So the booking surface defaults to LIGHT** — which is exactly when the auth pages look wrong.

### The `--bk-*` bridge tokens (the thing to extend)
- `static/css/book-account.css:110–134` — `.bk-page { --bk-h1: var(--hub-text); --bk-card-bg: var(--hub-card-bg); --bk-input-bg: var(--hub-input-bg); --bk-input-color: var(--hub-text); … }` (light, via `--hub-*`).
- `static/css/book-account.css:137–161` — `[data-theme="dark"] .bk-page { --bk-card-bg: rgba(9,46,76,.32); --bk-input-bg: rgba(13,38,60,.55); --bk-input-color: var(--color-cream); … }` (dark).
- **Key fact:** these tokens are scoped to `.bk-page`. The auth/onboarding wrappers are NOT `.bk-page`, so inside them `--bk-input-bg` / `--bk-input-color` resolve to *nothing* and `.bk-field input` (`:492`) loses its background and text color → the contrast failure.
- The code comment at `:106–109` explicitly notes auth/onboarding "keeps its dark-glass look" — i.e. this was an intentional shortcut we are now undoing.

### The hardcoded auth rules (the thing to rewrite)
All in `static/css/book-account.css`:
- `.bk-auth` `:642` (layout — fine, leave as is)
- `.bk-auth-card` `:657` — `background: rgba(7,30,50,.78)`, `border: 1px solid rgba(238,180,75,.14)`, gold box-shadow.
- `.bk-auth-card::before` `:667` — gold top hairline (dark-glass accent).
- `.bk-auth-eyebrow` `:671` — `color: var(--gold)` (ok in both themes, gold is constant).
- `.bk-auth-title` `:676` — `color: var(--cream)` ← **invisible on light**.
- `.bk-auth-sub` `:681` — `color: rgba(244,239,221,.65)` ← light-on-light on a light card.
- `.bk-auth-divider` `:689` / `::before,::after` `:695` — gold-tinted.
- `.bk-auth-links` `:698` — `color: rgba(244,239,221,.6)` ← low contrast on light.
- `.bk-auth-foot` `:721` — light text on gold-tint bg.
- `.bk-auth-foot b` `:729` — `color: var(--cream)`.
- `.bk-code input` `:709` — `background: rgba(13,38,60,.55)`, `color: var(--cream)` (login-code boxes — same problem).
- `.bk-dev-toast` `:731` — DEBUG-only login-code toast; check but likely fine.

### Affected templates (the pages to wrap)
All currently render `.bk-auth*` and need the shared surface class so the tokens are in scope:
1. `templates/account/login.html:14` — `.bk-themed-login` (the `is_public_surface` branch only).
2. `templates/account/signup.html` — `.bk-themed-signup` / `.bk-auth-card`.
3. `templates/account/confirm_login_code.html` — `.bk-auth` / `.bk-auth-card`.
4. `templates/classes/account/onboarding/step1.html` — `.bk-auth`.
5. `templates/classes/account/onboarding/step2.html` — `.bk-auth`.
6. `templates/classes/account/onboarding/step3.html` — `.bk-auth`.

### Decisions baked into this plan
- **Single shared scope class `.bk-auth-surface`.** Rather than enumerate `.bk-themed-login, .bk-themed-signup, …` in the token selector forever, add ONE class to every auth/onboarding wrapper and define tokens on `.bk-page, .bk-auth-surface`. One durable move; new auth pages just add the class.
- **Dark mode must not regress.** The current dark-glass look is the *intended* dark appearance. All hardcoded dark values move into `[data-theme="dark"]` blocks; the base rules become token-driven (light by default).
- **Out of scope (flagged, not done):** `templates/account/find_account.html`, `logout.html`, `signup_closed.html`, and the `{% else %}` branch of `login.html` use a different `.auth-page` / `.auth-card` system from `base.html` (the hub/non-booking surface). They are a separate surface and a separate audit. See "Follow-up" at the end. Do not touch them in this plan unless a quick check shows they're reachable on `book.pastlives.space` in light mode and broken — if so, file a follow-up.
- **Testing reality:** CSS appearance is not unit-testable here. We assert the testable structural facts (templates carry `.bk-auth-surface`; the CSS no longer hardcodes the dark card background outside a `[data-theme="dark"]` block) and rely on a manual light/dark visual pass (Task 7) for the actual contrast verification.

---

## File Structure

- Modify: `static/css/book-account.css` — extend token scope (`:110`, `:137`); rewrite `.bk-auth*` and `.bk-code input` (`:657–729`, `:709`) to be token-driven with a `[data-theme="dark"]` block.
- Modify: `templates/account/login.html` — add `bk-auth-surface` to `.bk-themed-login` (`:14`).
- Modify: `templates/account/signup.html` — add `bk-auth-surface` to the wrapper.
- Modify: `templates/account/confirm_login_code.html` — add `bk-auth-surface` to the wrapper.
- Modify: `templates/classes/account/onboarding/step1.html`, `step2.html`, `step3.html` — add `bk-auth-surface` to each wrapper.
- Test: `classes/spec/templates/auth_theme_spec.py` (new) — structural render assertions.
- Modify: `plfog/version.py` — version bump + changelog entry.

---

## Task 1: Extend the `--bk-*` token scope to a shared auth surface

**Files:** `static/css/book-account.css`

- [ ] **Step 1: Add `.bk-auth-surface` to the light token selector.**
  In `book-account.css`, change the selector at `:110` from `.bk-page {` to:
  ```css
  .bk-page,
  .bk-auth-surface {
  ```
  (Leave the token body `--bk-h1: var(--hub-text); …` unchanged.)

- [ ] **Step 2: Add `.bk-auth-surface` to the dark override selector.**
  At `:137`, change `[data-theme="dark"] .bk-page {` to:
  ```css
  [data-theme="dark"] .bk-page,
  [data-theme="dark"] .bk-auth-surface {
  ```

- [ ] **Step 3: Sanity check** — `ruff` does not lint CSS, so just confirm the file still parses by loading any account page locally (Task 7 covers full verification). No test yet; this step only puts the tokens in scope.

---

## Task 2: Make the auth card + typography theme-aware

**Files:** `static/css/book-account.css`

Rewrite each hardcoded rule so the **base** (light) uses tokens and a **`[data-theme="dark"]`** block restores the dark-glass look.

- [ ] **Step 1: `.bk-auth-card` (`:657`).** Replace the hardcoded background/border/shadow with tokens:
  ```css
  .bk-auth-card {
    width: 100%;
    max-width: 440px;
    background: var(--bk-card-bg);
    border: 1px solid var(--bk-card-border);
    border-radius: var(--r2);
    padding: 36px 36px 28px;
    box-shadow: 0 12px 40px rgba(9, 46, 76, .08);
    position: relative;
  }
  [data-theme="dark"] .bk-auth-card {
    background: rgba(7, 30, 50, .78);
    border-color: rgba(238, 180, 75, .14);
    box-shadow: 0 0 80px rgba(238, 180, 75, .05), 0 32px 80px rgba(0, 0, 0, .45);
  }
  ```

- [ ] **Step 2: `.bk-auth-card::before` (`:667`)** — the gold hairline is a dark-glass accent. Gate it to dark:
  ```css
  .bk-auth-card::before { content: none; }
  [data-theme="dark"] .bk-auth-card::before {
    content: ''; position: absolute; top: -1px; left: 20%; right: 20%; height: 1px;
    background: linear-gradient(90deg, transparent, rgba(238, 180, 75, .4), transparent);
  }
  ```

- [ ] **Step 3: Title + sub (`:676`, `:681`).** Use theme tokens:
  ```css
  .bk-auth-title { /* keep font/size/etc. */ color: var(--bk-h1); }
  .bk-auth-sub   { /* keep layout */        color: var(--bk-sub); }
  ```
  (Drop `var(--cream)` and `rgba(244,239,221,.65)`.)

- [ ] **Step 4: Links + foot (`:698`, `:702`, `:721`, `:729`).**
  ```css
  .bk-auth-links { color: var(--bk-sub); }
  .bk-auth-links a { color: var(--gold); } /* gold is constant; fine in both themes */
  .bk-auth-foot {
    background: var(--bk-empty-bg);
    border: 1px solid var(--bk-empty-border);
    color: var(--bk-sub);
  }
  .bk-auth-foot b { color: var(--bk-h1); }
  ```

- [ ] **Step 5: Divider (`:689`, `:695`).** The gold tint reads fine on dark but muddy on light. Use a neutral separator token with a dark accent:
  ```css
  .bk-auth-divider { color: var(--text3); }
  .bk-auth-divider::before, .bk-auth-divider::after { background: var(--bk-section-sep); }
  [data-theme="dark"] .bk-auth-divider { color: rgba(238,180,75,.5); }
  [data-theme="dark"] .bk-auth-divider::before,
  [data-theme="dark"] .bk-auth-divider::after { background: rgba(238,180,75,.18); }
  ```

- [ ] **Step 6: Login-code boxes `.bk-code input` (`:709`).**
  ```css
  .bk-code input {
    /* keep size/font */
    background: var(--bk-input-bg);
    border: 1px solid var(--border);
    color: var(--bk-input-color);
  }
  .bk-code input:first-child { border-color: var(--gold); }
  ```

- [ ] **Step 7: Verify dark mode visually is unchanged** and light mode now shows a light card (full pass in Task 7).

---

## Task 3: Confirm the input-contrast fix (spec item #1)

**Files:** none (verification) — the fix falls out of Tasks 1–2.

`.bk-field input` (`:492`) already consumes `--bk-input-bg` and `--bk-input-color`. Once Task 1 puts those tokens in scope on `.bk-auth-surface`, the email field on `/accounts/login/` renders with a light input background and dark-charcoal text in light mode (and the dark input in dark mode). No rule change needed here — but this is the literal acceptance criterion from spec #1, so verify explicitly in Task 7.

- [ ] **Step 1:** Confirm `.bk-field input` and `.bk-field input:focus` (`:499`) need no edits (they reference `--bk-input-bg`, `--bk-input-color`, `--border`, `--gold` — all now resolvable/theme-correct).

---

## Task 4: Apply `.bk-auth-surface` to every affected template

Add the class to the outermost auth/onboarding wrapper so the tokens are in scope.

- [ ] **Step 1: `templates/account/login.html:14`** — `<div class="bk-themed-login">` → `<div class="bk-themed-login bk-auth-surface">`.
- [ ] **Step 2: `templates/account/signup.html`** — add `bk-auth-surface` to its `.bk-themed-signup` wrapper.
- [ ] **Step 3: `templates/account/confirm_login_code.html`** — add `bk-auth-surface` to its outer `.bk-auth` wrapper (or a div around it).
- [ ] **Step 4: `templates/classes/account/onboarding/step1.html`, `step2.html`, `step3.html`** — add `bk-auth-surface` to each onboarding wrapper that currently carries `.bk-auth`.
- [ ] **Step 5:** Grep to confirm no auth/onboarding page renders `.bk-auth-card` or `.bk-auth` *without* an ancestor carrying `.bk-auth-surface`:
  ```bash
  grep -rL "bk-auth-surface" $(grep -rl "bk-auth" templates/account templates/classes/account/onboarding)
  ```
  Expected: no output (every file with `bk-auth` also has `bk-auth-surface`).

---

## Task 5: Structural render tests

**Files:** `classes/spec/templates/auth_theme_spec.py` (new)

CSS appearance isn't unit-testable, but we can lock the structural contract so a future edit can't silently drop the surface class or re-hardcode the dark card.

- [ ] **Step 1: Write the spec.** Assert that the booking login and signup pages render the `.bk-auth-surface` wrapper. Use the existing public/booking test client setup (mirror how `classes/spec/views/public_spec.py` issues GETs; the booking surface is selected by host — reuse its fixture/helper).
  ```python
  """The booking auth pages must stay inside the themed token scope."""

  from __future__ import annotations

  from django.urls import reverse


  def describe_booking_auth_theming():
      def it_renders_login_inside_the_themed_surface(client, db):
          resp = client.get(reverse("account_login"))  # confirm URL name in urls
          assert resp.status_code == 200
          assert "bk-auth-surface" in resp.content.decode()

      def it_renders_signup_inside_the_themed_surface(client, db):
          resp = client.get(reverse("account_signup"))
          assert resp.status_code == 200
          assert "bk-auth-surface" in resp.content.decode()
  ```
  > Confirm the URL names and the host/surface fixture in `classes/spec/` before finalizing — login renders the themed branch only when `is_public_surface` is true, so the test must hit the booking host. If the existing specs use a `book_client`/host override fixture, use it.

- [ ] **Step 2: Add a CSS guard test** asserting the dark card background is only declared under a dark selector (cheap regression guard against re-hardcoding):
  ```python
  from pathlib import Path

  def describe_book_account_css():
      def it_does_not_hardcode_the_dark_auth_card_in_light():
          css = Path("static/css/book-account.css").read_text()
          # the dark-glass card bg must live under a [data-theme="dark"] rule only
          idx = css.index("rgba(7, 30, 50, .78)")
          preceding = css[:idx]
          assert preceding.rfind('[data-theme="dark"]') > preceding.rfind(".bk-auth-card {")
  ```
  > If this guard proves brittle, downgrade it to a simple presence check or drop it — the structural template tests are the primary contract.

- [ ] **Step 3: Run** `pytest classes/spec/templates/auth_theme_spec.py -v`. Expected: PASS.

---

## Task 6: Lint / format / type-check

- [ ] **Step 1:** `ruff format . && ruff check .` (CSS/templates are untouched by ruff; this covers the new spec file).
- [ ] **Step 2:** `mypy .` (export `DATABASE_URL` first if running before push: `export $(grep '^DATABASE_URL=' .env | xargs)`).

---

## Task 7: Manual light/dark visual verification (run skill)

Start the dev server (project `run` skill) on the booking host and check **both** themes via the topbar toggle.

- [ ] `/accounts/login/` — light: light card, dark-charcoal title/sub, email input has light bg + dark readable text, focus ring gold. Dark: unchanged dark-glass.
- [ ] `/accounts/signup/` — same checks across all fields.
- [ ] login-code confirm page — the 6 code boxes are readable in both themes.
- [ ] onboarding step 1, 2, 3 — cards, labels, inputs, and buttons all theme correctly.
- [ ] `/account/` (the reference page) — confirm it is visually **unchanged** (we only added a selector; `.bk-page` behavior must not move).
- [ ] Quick AA contrast check on input text and card title against their backgrounds in light mode (target WCAG AA ≥ 4.5:1 for body text).

---

## Task 8: Version bump + changelog

**Files:** `plfog/version.py`

- [ ] **Step 1:** Bump `VERSION` to the next patch after the current released version. **At time of writing the latest is `2.5.8` (PR #108, in flight) — verify the merged version first and use the next one (e.g. `2.5.9`); do not assume.**
- [ ] **Step 2:** Prepend a member-friendly `CHANGELOG` entry (plain language — this posts to Discord):
  ```python
  {
      "version": "2.5.9",  # verify
      "date": "2026-06-18",  # set to merge date
      "title": "Cleaner light mode on the sign-in and onboarding pages",
      "changes": [
          "The booking-site sign-in, sign-up, and welcome/onboarding pages now match the light theme used everywhere else, so text is easy to read in light mode instead of washed out.",
      ],
  }
  ```
- [ ] **Step 3:** Commit.

---

## Final verification

- [ ] `pytest` — all pass, 100% coverage (the new spec file is small and self-covering).
- [ ] `ruff format . && ruff check . && mypy .` — clean.
- [ ] Visual pass (Task 7) signed off in both themes on all six pages, dashboard unchanged.

---

## Follow-up (out of scope for this plan)

- **`.auth-page` family:** `templates/account/find_account.html`, `logout.html`, `signup_closed.html`, and the `{% else %}` branch of `login.html` extend `base.html` and use `.auth-page` / `.auth-card` (hub/non-booking surface). If these are reachable on `book.pastlives.space` and look wrong in light mode, file a separate plan to give them the same treatment. They are intentionally excluded here to keep this change scoped to the booking-auth surface the user flagged.
- **`.bk-dev-toast` (`book-account.css:731`):** DEBUG-only login-code toast. Verify it themes acceptably; fix only if it shows the same hardcoded-dark issue.
