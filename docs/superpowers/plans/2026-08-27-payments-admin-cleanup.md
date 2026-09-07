# Payments Dashboard Cleanup + Admin Tools Relocation — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-27
**Surface:** Admin — billing dashboard (`/billing/admin/dashboard/`), hub sidebar, Admin Tools page, Site Settings copy.
**Related:** `2026-08-26-stripe-refunds-payments-panel.md` (parent — shipped the Payments tab and the `BILLING_APPROVER` gate this spec builds on).

---

## 1. Summary

With **My Tab turned off**, the admin Payments dashboard still opens on an Overview full of Tab-ledger content (Outstanding Tabs, tab stats, failed tab charges) and still offers an Open Tabs tab — machinery for a feature that isn't running. This spec makes the dashboard flag-aware: when `my_tab_enabled` is off, the tab-ledger tabs disappear and the dashboard opens straight on the **Payments** ledger. Independently, **Payments and Reports move off the hub sidebar into Admin Tools** as two new cards — which also finally gives non-admin Billing Administrators (`BILLING_APPROVER` holders) a navigation path to pages they could already open by URL.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Flag off: Overview tab | **Hidden entirely** — audited below (§6.1); every element on it is Tab-ledger content, so nothing survives. Payments becomes first AND default. |
| Flag off: Open Tabs tab | Hidden from the nav; `?tab=open-tabs` (and `?tab=overview`) **falls back** to the default tab — no 403. Settings/Stripe keep their existing 403-for-non-admins (that's permission, not feature). |
| Flag off: Settings + Stripe tabs | **Kept** (fog-admin only, as today). Stripe credentials power class and orientation payments regardless of the flag; the Settings tab is tab-billing config an admin may need while the feature is off (e.g. before re-enabling). Config never hides behind the feature it configures. |
| Flag on | Current behavior unchanged — Overview default, same tab order. Minimal change wins over cross-state order consistency. |
| Sidebar | Payments + Reports links removed from `hub/base.html` unconditionally (they exist only in the admin branch); the stale "never feature-toggled" comment goes with them. |
| Admin Tools | Two new cards, Payments and Reports, gated by billing-admin access (fog admin OR `BILLING_APPROVER`) — NOT the generic `_can_use_admin_tools` gate, so leads/instructors without the capability never see broken links. |
| Access gap fix | `_can_use_admin_tools` extended so a `BILLING_APPROVER` holder with no other elevated role can open Admin Tools at all (today they can't — and with the sidebar links gone they'd have zero navigation). |
| Flag help text | `my_tab_enabled` help_text rewritten to describe the new admin-side effects (its current text asserts the opposite). |
| Payments ledger content under the flag | The Tabs source chip and historical tab rows **stay** in the ledger with the flag off — payment history is a record, not a feature. |

## 2. What already exists (reuse, don't reinvent)

All locations verified against the current tree.

| Need | Existing thing | Location |
|---|---|---|
| The dashboard view + tab routing | `admin_tab_dashboard`, `_VALID_TABS = {overview, open-tabs, payments, settings, stripe}` | `billing/views.py:174`, `:219-342` |
| Existing tab-visibility gating pattern (role-based) | `viewer_is_fog_admin` hides Settings/Stripe links + 403 on direct hit | `billing/views.py:231-237`, `templates/billing/admin_dashboard.html:123-126` |
| The flag | `SiteConfiguration.my_tab_enabled` (singleton, `SiteConfiguration.load()`) | `core/models.py:296-302` |
| Flag already in every template context | `feature_flags` context processor exposes `my_tab_enabled` site-wide | `core/context_processors.py:47-59` |
| Existing flag-redirect idiom for member views | `setup_payment_method` checks the flag and redirects | `billing/views.py:70-75` |
| Sidebar links to remove | Payments `:162-168`, Reports `:170-178`, stale comment `:161` — admin branch (`{% if request.view_as.is_admin %}` at `:88`) only; absent from the member branch (`:219+`) and the mobile nav | `templates/hub/base.html` |
| Admin Tools page + card pattern | `hub_admin_tools` view with per-card `tool_*` flags; `<a class="hub-card pl-tool-card">` with icon/title/desc | `hub/views.py:2923-2956`, `templates/hub/admin_tools.html` |
| Admin Tools gate | `_can_use_admin_tools` (admin OR guild lead/staff OR instructor, view-as-aware for actual admins) | `hub/views.py:2523-2534` |
| Sidebar↔page parity for Admin Tools | `hub_sidebar` context processor delegates to the same gate | `hub/context_processors.py:43-56` |
| Billing-access gate (views) | `billing_admin_access_required` (fog-admin actual OR `BILLING_APPROVER`) | `hub/view_as.py:257-270` |
| Template-side capability-helper pattern to mirror | `has_refund_authority(request)` — "the template-side twin of the decorator" | `hub/view_as.py:289-302` |
| Capability check on Member | `member.has_admin_capability(cap)` (used by `_capability_or_admin_required`) | `hub/view_as.py:247-249` |
| Reports view (only its navigation moves) | `admin_reports` / `admin_reports_csv`, gated `billing_admin_access_required` | `billing/views.py:618-674` |
| Reports empty states (what a flag-off visitor sees) | "No entries match the current filters." / "No payouts in this window." | `templates/billing/admin_reports.html:156`, `:119` |
| The + Add Charge POST endpoint (needs a flag gate) | `admin_add_tab_entry`, `@fog_admin_required`, currently no flag check; serves the modal POST AND a full-page GET | `billing/views.py:472`, `billing/urls.py:12`, `templates/billing/admin_add_entry.html` |
| Tab-detail modal admin link (needs role gating) | static "View in Admin →" anchor, href set by JS to the Django admin change page | `templates/billing/admin_dashboard.html:579`, `:664-665` |
| Icons for the new cards | Payments (card) and Reports (bar chart) SVGs already drawn for the sidebar links being removed | `templates/hub/base.html:163-166`, `:171-176` |
| Existing dashboard specs to extend | `describe_admin_tab_dashboard` / `_extended` (incl. `it_defaults_to_overview_tab`, `it_unknown_tab_defaults_to_overview`) | `tests/billing/admin_dashboard_spec.py:25-164` |
| Existing nav-flag specs to correct | `it_hides_only_my_tab_when_disabled_payments_nav_stays` (now wrong by design) | `tests/hub/nav_feature_flags_spec.py:29` |
| Approver-access specs | dashboard gating spec | `tests/billing/dashboard_gating_spec.py` |

**Genuine gaps to close (kept small):**

1. No template-side "has billing admin access" helper — `has_refund_authority` exists for refunds, nothing equivalent for `BILLING_APPROVER`. One small function in `hub/view_as.py` (§5.2).
2. `_can_use_admin_tools` doesn't know about billing capability holders (§5.3).
3. The dashboard view has no notion of the flag (§5.1).

No new models, no migrations beyond a no-op help_text change, no new URLs, no new CSS.

## 3. Where the code lives

```
billing/
  views.py                       ~ admin_tab_dashboard: flag-aware allowed-tab set, default tab,
                                   fallback; skip building tab-ledger context when flag off
                                 ~ admin_add_tab_entry: flag gate (redirect idiom)
core/
  models.py                      ~ my_tab_enabled help_text rewrite (+ no-op migration)
hub/
  view_as.py                     + has_billing_admin_access(request) — twin of has_refund_authority
  views.py                       ~ _can_use_admin_tools: billing-capability arm;
                                   hub_admin_tools: tool_payments / tool_reports flags
templates/
  hub/base.html                  ~ remove Payments + Reports links and the stale :161 comment
  hub/admin_tools.html           + Payments card, Reports card
  billing/admin_dashboard.html   ~ tab nav gating + suppression, subtitle copy, add-charge gating,
                                   View-in-Admin link gating
  billing/admin_reports.html     + one-line flag-off notice
tests/
  billing/admin_dashboard_spec.py, billing/dashboard_gating_spec.py,
  hub/nav_feature_flags_spec.py, hub/admin_tools_spec.py (new), core/models_spec.py
```

Home apps: `billing` (dashboard), `hub` (nav + tools), `core` (flag copy) — all inside the coverage/mypy scope.

## 4. Data model

No schema change. `core/models.py` `my_tab_enabled` help_text (currently claims "The admin Payments and Reports pages are always available to billing admins" — stale twice over: the Overview/Open Tabs tabs now react to the flag, and "pages" now live in Admin Tools):

> "When off, hides the member My Tab pages, the balance pill, and the Buyables tab on guild pages; members visiting the Tab pages are redirected. The admin Payments dashboard also hides its Overview and Open Tabs tabs and opens straight on the Payments ledger. The Reports page and payment history are unaffected."

The Site Settings page renders this field via `{% include "components/form_field.html" with field=form.my_tab_enabled %}` (`templates/hub/admin/site_settings.html:433`) with no hand-written copy of its own, so the model help_text is the single source — no template edit needed there. Django emits a no-op `AlterField` state migration for the help_text; include it (auto-reversible).

## 5. Business logic (gating)

### 5.1 `admin_tab_dashboard` (`billing/views.py:219-342`)

The view learns the flag; the tab set becomes a function of `(flag, role)`:

```python
tabs_on = SiteConfiguration.load().my_tab_enabled
default_tab = "overview" if tabs_on else "payments"
allowed = ({"overview", "open-tabs", "payments"} if tabs_on else {"payments"})
if viewer_is_fog_admin:
    allowed |= {"settings", "stripe"}
active_tab = request.GET.get("tab", default_tab)
if active_tab in {"settings", "stripe"} and not viewer_is_fog_admin:
    return HttpResponse("Admin access required.", status=403)   # unchanged — permission, not feature
if active_tab not in allowed:
    active_tab = default_tab                                    # unknown OR feature-hidden → fall back
```

Order matters: the settings/stripe 403 stays a 403 (a non-admin probing an admin tab is an access question); `?tab=overview` / `?tab=open-tabs` with the flag off quietly land on Payments (a feature question — the locked "fall back, don't 403").

Context building: the Overview and Open Tabs querysets (`total_outstanding`, `collected_this_month`, `failed_count`, `locked_count`, `outstanding_tabs`, `failed_charges`, `open_tabs`) and `add_charge_form` are wrapped in `if tabs_on:` — no point walking three Tab tables for tabs that can't render. (The Payments context already builds only when active, `:310-313` — same idiom.) When skipped, the context omits those keys entirely; the template never references them with the flag off (§6.1). `_VALID_TABS` at `:174` is subsumed by the computed `allowed` set and is removed (its other user is only this view — verify with grep at build time).

`billing_admin_payments_table`, `admin_payments_csv`, `admin_reports`, `admin_reports_csv`: **untouched** — flag-independent, still `billing_admin_access_required`.

### 5.2 `has_billing_admin_access` (`hub/view_as.py`, new)

Mirror of `has_refund_authority` (`:289-302`) exactly, checking `AdminCapability.Capability.BILLING_APPROVER`: actual fog-admin via `view_as.has_actual(ROLE_ADMIN)`, else `member.has_admin_capability(...)`. Docstring: the template-side twin of `billing_admin_access_required` — computes visibility for the Admin Tools cards; the views stay decorator-gated regardless.

### 5.3 `_can_use_admin_tools` (`hub/views.py:2523-2534`)

The non-admin arm gains the billing capability:

```python
return member is not None and (
    member.is_guild_lead or member.is_guild_staff or member.is_instructor
    or member.has_admin_capability(AdminCapability.Capability.BILLING_APPROVER)
)
```

The actual-admin arm is unchanged (view-as-aware: an admin previewing as a plain member still doesn't see Admin Tools). Docstring updated to name the fourth audience. Because the sidebar entry delegates to this same gate (`hub/context_processors.py:47-56`), a capability-only member gets the sidebar entry and the page in one change — entry and page can still never disagree.

### 5.4 `hub_admin_tools` (`hub/views.py:2923-2956`)

Two new context flags, both from the §5.2 helper (per-card flags is the house pattern; they share one expression today and may diverge later):

```python
tool_payments = tool_reports = has_billing_admin_access(request)
```

Note the deliberate asymmetry with the other `tool_*` flags: `tool_manage_members` etc. use `is_admin` (view-as-aware), while `has_billing_admin_access` uses `has_actual`. No leak results — an actual admin previewing as a member is bounced off the page before the flags matter — and a non-admin capability holder has no view-as machinery. State this in a one-line comment so a reviewer doesn't "fix" it.

### 5.5 `admin_add_tab_entry` flag gate (`billing/views.py:472`)

Template gating alone doesn't survive a mid-session flip: an admin with Overview already open when the flag flips off can still POST "+ Add Charge", creating a `TabEntry` on a frozen ledger no member can see or pay (`bill_tabs` exits when the flag is off, `bill_tabs.py:68`). Gate the view server-side with the existing flag-redirect idiom (`setup_payment_method`, `billing/views.py:70-75`):

```python
if not SiteConfiguration.load().my_tab_enabled:
    django_messages.info(request, "My Tab is off, so new tab charges can't be added right now.")
    return redirect("billing_admin_dashboard")
```

Placed at the top of the view, so it covers both the modal POST and the standalone `admin_add_entry.html` GET page. The decorator stays `fog_admin_required`. **`billing_admin_retry_charge` is deliberately left alone** — retrying a failed charge collects debt that already exists, which is legitimate (and necessary) with the feature off.

## 6. UI / UX

### 6.1 Payments dashboard — `templates/billing/admin_dashboard.html`

**The Overview audit (locked decision, evidence):** every element of the Overview tab is Tab-ledger content —

| Element | Lines | Data source |
|---|---|---|
| Total Outstanding stat | `:135-138` | `TabEntry.objects.pending()` sum (view `:243-245`) |
| Collected This Month stat | `:139-142` | `TabCharge` SUCCEEDED sum (`:247-250`) |
| Failed Charges stat | `:143-146` | `TabCharge` FAILED count (`:252`) |
| Locked Tabs stat | `:147-150` | `Tab.is_locked` count (`:253`) |
| + Add Charge button | `:153-157` | writes a `TabEntry` |
| Outstanding Tabs table | `:159-183` | `Tab` with uncharged entries (`:255-262`) |
| Recent Failed Charges table | `:185-215` | `TabCharge` FAILED + retry (`:264-268`) |

Nothing on the tab is non-tab content, so with the flag off **Overview hides entirely** and Payments is first and default — the "substantially empty" branch of the locked decision, at 100%.

**Screen: the dashboard, flag OFF** (flag ON renders exactly as today):

- **Tab nav (`:119-127`):** the Overview and Open Tabs `<a>`s wrap in `{% if my_tab_enabled %}` (the flag is already in context via the `feature_flags` context processor — no new context needed). Nav order is untouched, so with the flag off the row reads Payments · Settings · Stripe (admin) or just Payments (Billing Administrator). Active-link styling unchanged.
- **Single-tab suppression:** a Billing Administrator with the flag off would see a tab bar with exactly one link — navigation chrome with nowhere to go reads as broken. Wrap the whole `.pl-tab-nav` in `{% if my_tab_enabled or viewer_is_fog_admin %}` — precisely the condition under which more than one tab renders (flag on gives everyone ≥3; an admin always has Settings/Stripe). One tab → no bar; the page opens as a plain Payments ledger.
- **Subtitle (`:115`):** "Tab billing, charge history, and Stripe configuration." is wrong with tabs off, and "Stripe configuration" is wrong for an approver who has no Stripe tab in any state. Since the string is being rewritten, make the flag-off copy role-aware: flag off + admin → "Payment history and Stripe configuration."; flag off + approver → "Payment history across the makerspace." Flag-on copy stays as-is (its Stripe mention for approvers is a pre-existing wart, not worth a fourth string — the flag-on approver still has three tabs of tab billing, which the sentence mostly describes). Title `Payments Dashboard` stays.
- **Overview + Open Tabs sections (`:132-262`):** structurally unreachable (`active_tab` can never be `overview`/`open-tabs` with the flag off) — no template change needed inside them, and their context keys are absent (§5.1), which is safe because the guarding `{% if active_tab == … %}` is false.
- **Add Charge modal (`:587-623`) + its opener injection:** the modal's only openers live on the two hidden tabs, but `openTabModal` (`:667-669`) also injects a "+ Add Charge" button into the tab-detail modal footer — reachable from historical tab rows on the Payments ledger. With tabs off, adding new tab entries makes no sense (members can't see or pay them; `bill_tabs` skips when the flag is off, `bill_tabs.py:68`). Wrap the add-charge modal markup in `{% if my_tab_enabled %}` and gate the JS injection on a template-set boolean (e.g. `const TABS_ON = {{ my_tab_enabled|yesno:"true,false" }};`) so the footer button only appears when the feature is on. Template gating is UX; the POST endpoint itself is server-gated per §5.5, which is what actually holds against a mid-session flag flip.
- **Tab-detail modal (`:539-582`) stays in both states** — historical tab rows in the Payments ledger open it (payment history is a record); read-only viewing of a past tab charge is correct with the feature off. Its footer "View in Admin →" link (`:579`, href JS-set to the Django admin change page at `:664-665`) is a dead end for a non-admin Billing Administrator — they'd click it and hit the Django admin login wall. Wrap the anchor in `{% if viewer_is_fog_admin %}` (already in context, view `:337`); the JS href assignment gets a null-guard (`if (adminLink) …`) so the modal still opens cleanly when the element is absent. And for an approver with the flag off, the footer would then hold nothing but the empty `#tdm-actions` div (admin link gated away, "+ Add Charge" injection gated away) — an empty strip under a border. Wrap the whole `.pl-modal__footer` in `{% if my_tab_enabled or viewer_is_fog_admin %}` — exactly the condition under which it has any content.
- **Payments tab content:** unchanged. The Tabs source chip stays (locked decision — history). Filters, CSV, refund modal, HTMX refresh: untouched.
- **States:** empty/loading/error/success states of the Payments tab already exist from the parent spec (empty-window message, `.htmx-request` opacity, error toasts) — nothing new to design; the flag-off dashboard opens directly into that already-complete screen. No forms, list editors, or destructive actions are added by this spec.
- **Dark + light:** no new styles. The page's `pl-*` chrome is dark-fixed by existing design; the nav/subtitle changes are conditionals on existing markup. Verify both themes anyway (the hosted hub components are theme-aware).
- **Mobile:** unchanged — existing `@media (max-width: 768px)` rules; the shorter tab row only helps.

**Tab matrix (flag × viewer → visible tabs, default bolded):**

| | fog-admin | `BILLING_APPROVER` (non-admin) |
|---|---|---|
| **Flag ON** | **Overview** · Open Tabs · Payments · Settings · Stripe | **Overview** · Open Tabs · Payments |
| **Flag OFF** | **Payments** · Settings · Stripe | **Payments** |

Deep-link behavior, flag OFF: `?tab=overview` → Payments (200), `?tab=open-tabs` → Payments (200), `?tab=open-tabs&filter=failed` → Payments (stray `filter` param ignored harmlessly), `?tab=settings` as non-admin → 403 (unchanged), `?tab=nonsense` → Payments. Flag ON: all current behavior, `?tab=nonsense` → Overview. Existing deep links from the orientation templates (`?tab=payments&source=orientation&status=failed`, `templates/hub/orientations_dashboard.html:95`, `orientation_respond.html:11,27`) resolve identically in both flag states — no changes there.

### 6.2 Hub sidebar — `templates/hub/base.html`

- Delete the Payments link (`:162-168`), the Reports link (`:170-178`), and the stale comment at `:161` ("permission-scoped (admin sidebar), never feature-toggled" — false on both counts once this ships). Both links exist only in the admin branch (`{% if request.view_as.is_admin %}`, `:88`); the member branch (`:219+`) and mobile nav never had them — verified, so "remove from both branches" is a one-branch deletion plus a grep to confirm no other occurrence.
- The admin sidebar then reads … Admin Tools · Class Catalog · Community Calendar · Meetings · Member Directory · (My Tab) · Spaces · Voting … — Payments and Reports are reached via Admin Tools.
- **Active-state note:** visiting `/billing/admin/…` now highlights no sidebar entry. This matches every other Admin Tools child (Announcements, Manage Members, Site Settings highlight nothing either) — deliberately not "fixed" here; a global "Admin Tools stays lit on child pages" change is out of scope (§9).
- No CSS, theme, or mobile changes — pure deletion.

### 6.3 Admin Tools — `templates/hub/admin_tools.html`

Two new cards, inserted **between Manage Members and Activity** (money tooling next to member administration; guides stay last). Exact markup pattern of the existing cards (`<a class="hub-card pl-tool-card">` + `pl-tool-card__icon/title/desc`), icons lifted verbatim from the deleted sidebar links so the visual identity travels:

- **Payments** — `{% if tool_payments %}`, `href="{% url 'billing_admin_dashboard' %}"`.
  - Icon: the card SVG (`<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/>`, at 22×22 like its siblings).
  - Title: `Payments`
  - Desc: `Review payments across the makerspace, with refund history and CSV export.`
    (Not "issue refunds" — refund buttons are gated by `has_refund_authority` (fog-admin or `REFUNDS` holder), and `BILLING_APPROVER` does not imply `REFUNDS`; the card promises only what the card's own gate guarantees every viewer.)
- **Reports** — `{% if tool_reports %}`, `href="{% url 'billing_admin_reports' %}"`.
  - Icon: the bar-chart SVG (`<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>`).
  - Title: `Reports`
  - Desc: `Revenue reports and guild payout summaries, with CSV export.`

Copy rules honored: Title Case titles, plain one-line descriptions, **no dashes** (member-visible copy). Card descriptions are flag-neutral on purpose — the Payments dashboard adapts itself.

- **Viewer walk:** fog-admin sees both cards among the full grid. A `BILLING_APPROVER`-only member (no lead/staff/instructor role) now sees the Admin Tools sidebar entry and a page containing **exactly two cards** — Payments and Reports (`tool_announcements`, `tool_orientations`, both guides: all false for them) — a small page, but a complete and honest one; no empty-state needed since the gate guarantees at least these two cards for anyone admitted via the new arm. A guild lead or instructor without the capability sees the page exactly as today — no billing cards, no broken links.
- **States / dark + light / mobile:** the cards are existing theme-aware components on an existing responsive grid (`.pl-tools-grid`); no new styles. Verify both themes.

### 6.4 Reports page — `templates/billing/admin_reports.html`

With My Tab off in production, the new Reports card lands a first-time visitor on the current-month default view — empty forever, because no new tab entries accrue. Worse than an empty-state message: on a no-params visit the entries card doesn't render at all (the "No entries match the current filters." card sits inside `{% elif query_string %}`, `:154-158`, and the default view has an empty query string), so the visitor gets a silent nothing below the filters; only a filtered visit earns the empty-state card, and payouts show "No payouts in this window." (`:119`). Minimal in-scope fix: a one-line notice near the top of the page, `{% if not my_tab_enabled %}` (flag already in context):

> "My Tab is off, so no new tab entries accrue. Pick an earlier date range to see history."

Rendered as a `pl-text-muted` line (existing class, theme-token colored, `:10`) under the page header — informational, not an alert banner; the page stays fully functional for historical ranges. No change when the flag is on. Everything else about Reports (filters, CSV, gating) is untouched — only its navigation moves (§6.3).

### 6.5 Site Settings

Renders the new help_text automatically (§4). No other screen changes.

### 6.6 User-lens sanity pass

Primary action obvious: an admin looking for money lands on Admin Tools → Payments, and with tabs off the dashboard opens directly on the ledger they actually use. No dead ends: every previously-bookmarked URL (`?tab=open-tabs`, sidebar-era `/billing/admin/reports/`) still resolves — links fall back or work unchanged, nothing 404s. Nothing half-built: the capability holder who could open pages but never navigate to them now has the full path (sidebar entry → Admin Tools → card → page). Fewest moving parts: no new pages, no new URLs, two template conditionals and one gate arm.

## 7. Build order (phased; each phase ships green)

1. **Gating plumbing** — `has_billing_admin_access` in `hub/view_as.py`; `_can_use_admin_tools` billing arm + docstring; `hub_admin_tools` `tool_payments`/`tool_reports` flags. Specs for all three (§8).
2. **Dashboard flag-awareness** — `admin_tab_dashboard` allowed-tab/default/fallback logic + conditional context building; `admin_add_tab_entry` flag gate (§5.5); template tab-nav conditionals + single-tab suppression, role-aware subtitle, add-charge modal + JS gating, View-in-Admin link gating. Specs incl. the full tab matrix and deep-link fallbacks.
3. **Navigation move** — delete the two sidebar links + stale comment; add the two Admin Tools cards; the Reports flag-off notice (§6.4); rewrite `it_hides_only_my_tab_when_disabled_payments_nav_stays` and add card/nav specs.
4. **Copy + housekeeping** — `my_tab_enabled` help_text + no-op migration; run `manage.py check` (CI runs system checks local pytest skips); full suite + `ruff` + mypy (pre-push hook).

Per the task instructions this spec carries **no VERSION bump or changelog work** — the shipping PR handles release housekeeping under the standing changelog rules.

> Spec only — do not build until approved.

## 8. Testing

BDD `*_spec.py`, `describe_*`/`it_*` (never `context_*` — silently skipped), factory-boy, 100% branch coverage, mutation-clean. The flag flips via `SiteConfiguration.load()` + save in fixtures (existing idiom in `tests/hub/nav_feature_flags_spec.py`).

- `tests/billing/admin_dashboard_spec.py` — new `describe_when_my_tab_disabled` block:
  - default tab is `payments` (no `?tab` param → payments content renders, `active_tab == "payments"`);
  - `?tab=overview` and `?tab=open-tabs` fall back to payments with HTTP 200 (never 403/404);
  - `?tab=open-tabs&filter=failed` also falls back cleanly;
  - `?tab=nonsense` falls back to payments (vs. overview when the flag is on — keep `it_unknown_tab_defaults_to_overview` for the on-state);
  - Overview/Open Tabs nav links absent from the response; Payments/Settings/Stripe links present for an admin;
  - subtitle reads the flag-off copy — role-aware: the admin string mentions Stripe configuration, the approver string doesn't; add-charge modal markup absent; tab-detail modal still present;
  - the JS gate is pinned literally: response contains `const TABS_ON = false` with the flag off and `const TABS_ON = true` with it on (a `yesno` argument-order inversion survives every other listed test — this one kills it);
  - `.pl-tab-nav` absent for an approver with the flag off (single-tab suppression); present for an admin with the flag off and for everyone with it on;
  - "View in Admin" anchor present for a fog-admin, absent for a non-admin approver (both flag states);
  - overview context keys (`outstanding_tabs`, `total_outstanding`, `open_tabs`, `add_charge_form`) absent from `response.context`;
  - `?tab=settings` as a non-admin approver still 403s with the flag off (403 wins over fallback).
  - Flag-on regression: existing `it_defaults_to_overview_tab` etc. unchanged and still green.
- `tests/billing/dashboard_gating_spec.py` — **nav/access parity**, the matrix pinned: for each (flag × fog-admin/approver) cell of §6.1's table, the set of tab links rendered equals the set of `?tab=` values that return 200 showing that tab — a link never points at a tab that falls back or 403s, and no reachable tab lacks a link. Approver with flag off sees exactly one tab (Payments).
- `tests/billing/admin_dashboard_spec.py` (where `admin_add_tab_entry` is covered today — verified; `views_spec.py` has none) — the §5.5 gate: with the flag off, a valid POST redirects to the dashboard with the info message and creates **no** `TabEntry` (assert the count); the GET page redirects too; flag on → behavior unchanged. Regression pin on the deliberate exception: `billing_admin_retry_charge` still executes with the flag off.
- `tests/billing/reports_spec.py` — the §6.4 notice: present with the flag off, absent with it on; and with the flag off an earlier date range still renders historical rows (the page stays useful, the notice isn't a lockout).
- `tests/hub/nav_feature_flags_spec.py` — **rewrite** `it_hides_only_my_tab_when_disabled_payments_nav_stays`: the sidebar contains neither the Payments nor the Reports link for an admin viewer with the flag ON and with it OFF (the links are simply gone); My Tab link behavior unchanged.
- `tests/hub/admin_tools_spec.py` (new file, or fold into wherever `_can_use_admin_tools` coverage currently lives — locate at build time):
  - a `BILLING_APPROVER`-only member (no lead/staff/instructor): sidebar shows Admin Tools; `hub_admin_tools` returns 200; response contains the Payments and Reports cards and **none** of the admin-only cards (Manage Members, Site Settings…);
  - a guild lead without the capability: page 200, **no** billing cards;
  - fog-admin: both cards present; fog-admin previewing as a plain member: still bounced home (regression);
  - card hrefs resolve to `billing_admin_dashboard` / `billing_admin_reports`;
  - **card/access parity:** everyone shown a billing card passes `billing_admin_access_required` on the target view, and a viewer who passes it and can reach Admin Tools always gets the cards.
- `tests/hub/` view_as spec (wherever `has_refund_authority` is specced): `has_billing_admin_access` — actual admin true (preview-independent), capability holder true, plain member false, memberless user false.
- `tests/core/models_spec.py` — no help_text assertion exists today; don't add one (copy is not a contract). Existing default-True test unchanged.
- Gotchas: the `CHANGELOG` renders into every hub page context — no negative assertions on generic strings like "Payments" against full-page HTML; assert on the anchor/`{% url %}` targets or distinctive card markup instead.

## 9. Open / deferred

- **Sidebar active-state for Admin Tools children** — `/billing/admin/…` (like every other tool page) highlights nothing; a global fix is its own tiny UX pass, not smuggled in here.
- **Admin-preview navigation** — an actual admin previewing as a plain member now has no path to the billing pages (Admin Tools hidden in preview, sidebar links gone). That is the existing view-as contract ("see what a member sees") applied consistently; exiting preview restores everything.
- **Pre-existing flag-ON wart: approver vs "+ Add Charge"** — with the flag on, a non-admin Billing Administrator sees the "+ Add Charge" buttons on Overview/Open Tabs, but `admin_add_tab_entry` is `fog_admin_required`, so their submit 403s. Pre-dates this spec, unchanged by it, documented here so it isn't rediscovered as a regression; the fix (align the button's visibility or the view's gate) is its own small decision.
- **Deeper Reports/flag interplay** — the page stays flag-independent by locked decision (history + future non-tab reporting), and the §6.4 notice covers the empty-by-default confusion. Anything beyond that (auto-widening the default range when the flag is off, per-source report filters) waits for a real need.
- **Settings-tab relevance with tabs off** — kept visible to fog-admins (config never hides behind its feature). If the tab-billing settings ever feel like clutter there, a "My Tab is currently off" notice on that tab is the next increment, not removal.
- **No VERSION/changelog work in this spec** — release housekeeping happens in the shipping PR.
