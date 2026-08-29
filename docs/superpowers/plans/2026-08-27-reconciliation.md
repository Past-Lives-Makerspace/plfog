# Reconciliation — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-27
**Surface:** Admin — the Payments dashboard (`/billing/admin/dashboard/`), a new **Reconciliation** tab; plus a read-only line on the guild-edit Orientations settings tab (`templates/hub/guild_edit.html`).
**Related:**
- `2026-08-26-stripe-refunds-payments-panel.md` — **shipped**; built `billing/payments_panel.py` (the merged ledger this feature re-uses), the `PaymentRefund` ledger, and `billing_admin_access_required`.
- `2026-08-26-paid-orientations.md` — **shipped**; added `OrientationBooking` payment fields + `GuildOrientationSettings.price_cents`.
- `2026-08-27-payments-admin-cleanup.md` — **shipped**; made `admin_tab_dashboard` compute its `allowed` tab set dynamically (the pattern this tab plugs into).
- Voting funding: `membership/models.py` `FundingSnapshot` + `membership/vote_calculator.py` (the snapshot CRUD Phase 3 mirrors).

---

## 1. Summary

An admin opens the Payments dashboard, clicks **Reconciliation**, and sees — for a chosen month (this month by default) — exactly how much money to hand to each **Guild**, each **Instructor**, each **Orientator**, and **Past Lives** itself, computed from every class payment, orientation payment, and member-tab charge in that window. Today an admin has the Payments ledger (a flat list of transactions) and the Reports page (tab splits only) but no single answer to "who do I pay, and how much, this month." This feature is that answer: one allocation table, split by the makerspace's revenue rules, exportable to CSV and PDF, with per-transaction corrections and a month-end snapshot so a paid-out month is frozen on the record.

The Reports page folds into this tab and stops being a separate destination.

### Locked decisions (from the owner)

| Decision | Choice |
|---|---|
| Where it lives | A new **Reconciliation** tab on the existing Payments dashboard, **admin-only** (gated like the Settings/Stripe tabs, not merely `BILLING_APPROVER`). The old standalone **Reports** page is absorbed into it. |
| Orientation split | **70% orientator / 15% guild / 15% Past Lives.** |
| Class split | **70% instructor / 10% guild / 20% Past Lives.** |
| Tab split | **Reuse the existing frozen `TabEntrySplit` snapshots** (admin -> Past Lives, guild -> that guild). No re-splitting; tabs already carry per-product admin/guild percentages. |
| Where the % live | **Read-only** on the Orientation settings surface; **editable only** in a new "Payments Dashboard Settings" surface (Phase 2). |
| Exports | **CSV** (Phase 1) and **PDF** (Phase 4). |
| Per-transaction control | Admins can **adjust a single transaction's split %** and **mark a transaction omitted** (not counted). Needs an override model (Phase 2). |
| Voting allocation | Each guild's **voting allocation** appears here, folded into that guild's overall total. **Rolling monthly:** a snapshot freezes it at month end and it resets to 0 for the new month; between snapshots the current votes show as **projected** funds. (Phase 3.) |
| Snapshot CRUD | **Mirror the Voting `FundingSnapshot` CRUD** (history / detail / take / delete) — **but no email, no `emit()` event, no Airtable sync** (all three are deliberately dropped, §5.6). |

### What "net-new" actually is

The merged three-source ledger already exists and is battle-tested (`build_payments_ledger`). What it does **not** carry is *who gets paid*: `PaymentRow` knows only a coarse `source_kind` ("tab"/"class"/"orientation"), never the instructor, orientator, or per-recipient split (scout-confirmed: `PaymentRow` has no such field; `ProductRevenueSplit`/`TabEntrySplit` `RecipientType` is `admin`/`guild` only). The core of this feature is a **read-time allocation engine** that walks the same three streams, attributes each payment to its recipients under the fixed splits, and aggregates per recipient. No fourth money table — same YAGNI stance the payments panel took.

## 2. What already exists (reuse, don't reinvent)

All locations verified against the current tree (2026-08-27).

| Need | Existing thing | Location |
|---|---|---|
| Three money streams, already merged + windowed | `build_payments_ledger(*, window, source, status, viewer_is_admin) -> PaymentsLedger`; per-stream `_tab_rows` / `_class_rows` / `_orientation_rows` | `billing/payments_panel.py:306`, `:154`, `:191`, `:249` |
| Date-window parse (defaults to current month) | `parse_window(start_raw, end_raw) -> PanelWindow` (+ `PanelWindow.start_dt/end_dt`, Portland tz) | `billing/payments_panel.py:128`, `:113` |
| The row shape to enrich (no recipient attribution) | `PaymentRow` frozen dataclass | `billing/payments_panel.py:59` |
| Streaming-CSV pattern (`_Echo` + generator) | `stream_payments_csv`, `stream_report_csv` | `billing/payments_panel.py:375`, `billing/reports.py:206` |
| Class payment attribution | `Registration.amount_paid_cents` (:1985), `confirmed_at` (:2047); instructor `ClassOffering.instructor` (:414, nullable), guild `ClassOffering.category.guild` (`classes/models.py:81`, nullable) | `classes/models.py` |
| Orientation payment attribution | `OrientationBooking.amount_paid_cents` (:8605), `requested_at` (:8621), orientator `oriented_by` (:8595, `SET_NULL`), `guild` FK; price `GuildOrientationSettings.price_cents` (:8162) | `membership/models.py` |
| Tab splits (frozen, admin/guild) | `TabEntrySplit` (:1077, `RecipientType` = ADMIN/GUILD only); `TabCharge` status (:849); `build_report()` already aggregates these into `PayoutRow` | `billing/models.py`, `billing/reports.py:94`, `:46` |
| Penny-rounding rule to mirror | `TabEntry.snapshot_splits()` — each share `round(amount*pct/100, ROUND_HALF_UP)`; **largest-percent row absorbs +/-1c drift** (ties -> first row); asserts children sum to total | `billing/models.py:768` |
| Refund state per payment (for netting) | `Registration.refund_state` / `.refundable_cents`; `OrientationBooking` mirror; `PaymentRefund` succeeded rows | shipped refunds engine |
| The dashboard to hang the tab on | `admin_tab_dashboard` computes `allowed` tab set + `active_tab`; Settings/Stripe restricted to actual admin (403 otherwise); payments tab context via `_payments_panel_context(request)` | `billing/views.py:217`, `:239`, `:243`, `:246`, and the `_payments_panel_context` helper |
| Tab nav + body switch pattern | `<div class="pl-tab-nav">` with `?tab=` links (`.active`); `{% if active_tab == "..." %}` bodies | `templates/billing/admin_dashboard.html:126-134`, `:142/232/277/340/401` |
| Reports engine being folded in | `admin_reports` (:639) -> `admin_reports.html`; `build_report()` / `PayoutRow` / `stream_report_csv` | `billing/views.py:639`, `billing/reports.py` |
| Admin-only gate (the two-tier pattern) | `billing_admin_access_required` (admin OR `BILLING_APPROVER`); actual-admin restriction via `view_as.has_actual("admin")` + 403 | `hub/view_as.py:257`, `billing/views.py:246` |
| Settings save pattern (separate POST view) | `billing_admin_save_settings` + `BillingSettingsForm`; `default_admin_percent` DecimalField(5,2) default 20.00 | `billing/views.py`, `billing/urls.py:13`, `billing/models.py:79` |
| Voting snapshot to mirror (Phase 3) | `FundingSnapshot` (:7304), `take()` classmethod (:7400); CRUD `voting_history` (:5092), `voting_history_detail` (:5101), `voting_snapshots` (:5113), `voting_snapshot_take` (:5179), `voting_snapshot_delete` (:5201) — all `@fog_admin_required` | `membership/models.py`, `hub/views.py` |
| Live vote projection (for "projected" voting funds) | `vote_calculator.calculate_results(votes, paying_voter_count, pool_override)`; `serialize_live_votes()` used by `voting_snapshots` | `membership/vote_calculator.py`, `hub/views.py:5113` |
| Scheduled-job registry + parity guard (Phase 3) | `SCHEDULED_JOBS` (`core/scheduled_jobs.py:59`), `take_cycle_snapshot` (:68); parity tuples `_DISPATCHER_ALWAYS/DAILY/WEEKLY` | `core/scheduled_jobs.py`, `core/spec/scheduled_jobs_spec.py` |
| Hub component set (theme-aware, on the dashboard, which extends `hub/base.html`) | `components/form_field.html`, `components/modal.html`, `components/confirm_modal.html`, `components/toggle.html`, `.pl-help`, `trigger_toast()` | `templates/components/`, `FRONTEND.md` |

**Genuine gaps to close (kept small):**

1. **Recipient attribution + allocation math** — nothing splits a class or orientation payment by recipient today. New read-time module `billing/reconciliation.py` (§5.1). *(Phase 1, the core.)*
2. **Reports fold-in** — the standalone `admin_reports` page/URL becomes a section of the Reconciliation tab; the tab splits still come from `build_report()`. *(Phase 1.)*
3. **Editable split config** — the percentages are constants in Phase 1; Phase 2 moves them onto `BillingSettings` and adds the "Payments Dashboard Settings" surface.
4. **Per-transaction override** — new `TransactionAdjustment` model (§4.2). *(Phase 2.)*
5. **Monthly snapshot** — new `ReconciliationSnapshot` model + take command + CRUD + month-end job. *(Phase 3.)*
6. **PDF** — no PDF dependency exists (scout-confirmed); Phase 4 adds one and justifies the pick.

**Explicitly NOT built:** a unified "Payment" table (the panel already proved the read-time aggregation is enough); per-guild Stripe Connect transfers (payouts stay manual, as `billing/CLAUDE.md` documents); editing a *tab charge's* internal splits from this surface (tabs are adjust-by-omit only, §4.2).

## 3. Where the code lives

```
billing/
  reconciliation.py        + NEW (Phase 1) — RecipientKind, RecipientAllocation,
                             TransactionLine, ReconciliationResult; build_reconciliation();
                             split constants; stream_reconciliation_csv()
  reconciliation_pdf.py     + NEW (Phase 4) — render_reconciliation_pdf()
  models.py                 ~ (Phase 2) BillingSettings: 6 split-percent fields
                            + (Phase 2) TransactionAdjustment
                            + (Phase 3) ReconciliationSnapshot (+ manager)
  views.py                  ~ admin_tab_dashboard: "reconciliation" in allowed (admin-only) +
                              _reconciliation_context(request); reconciliation table partial view
                            + admin_reconciliation_csv (Phase 1), _pdf (Phase 4)
                            + (Phase 2) billing_admin_save_reconciliation_settings;
                              reconciliation_adjust / _omit / _clear endpoints
                            + (Phase 3) reconciliation_snapshot_take / _delete
  forms.py                  + (Phase 2) ReconciliationSettingsForm, TransactionAdjustmentForm
  urls.py                   + routes for the above
  reports.py                ~ unchanged engine; its page/URL is retired (fold-in, §6.1)
  management/commands/
    take_reconciliation_snapshot.py   + NEW (Phase 3)
core/
  scheduled_jobs.py         ~ (Phase 3) register take_reconciliation_snapshot (month-end)
  spec/scheduled_jobs_spec.py         ~ (Phase 3) add key to the matching _DISPATCHER_* tuple
membership/
  (read-only) vote_calculator + FundingSnapshot are consumed, not changed
templates/
  billing/admin_dashboard.html                 ~ Reconciliation tab (nav link + body)
  billing/partials/reconciliation_table.html   + NEW — allocation table (page + HTMX refresh)
  billing/partials/reconciliation_settings.html+ NEW (Phase 2) — split-config form body
  billing/partials/reconciliation_snapshots.html + NEW (Phase 3) — history + take/delete
  hub/guild_edit.html                          ~ read-only split line on the Orientations tab
requirements.txt / pyproject.toml              ~ (Phase 4) add the PDF dep
tests/billing/  +  core/spec/                  + specs per §9
```

Home app: **`billing`** owns the engine, the model additions, the views, and the exports — all inside the existing coverage/mypy scope. `hub` gets one read-only template edit.

## 4. Data model

### Phase 1 — no model

The allocation is a **read-time aggregation**, exactly like `payments_panel.py`. `billing/reconciliation.py` defines frozen dataclasses only:

- `RecipientKind(str, Enum)` — `GUILD`, `INSTRUCTOR`, `ORIENTATOR`, `PL`. (A Python enum, not a Django `TextChoices`, because nothing is persisted in Phase 1. It becomes the `results`-JSON vocabulary in Phase 3.)
- `TransactionLine` — one contributing payment: `source_kind`, `source_pk`, `date`, `payer_name`, `item`, `gross_cents`, `refunded_cents`, `net_cents`, and `shares: dict[recipient_key, cents]` (recipient_key = e.g. `("guild", 7)`, `("instructor", 42)`, `("orientator", 42)`, `("pl", None)`), plus `omitted: bool` and `note: str` (e.g. "instructor unset -> Past Lives").
- `RecipientAllocation` — one output row: `kind`, `recipient_id` (int|None), `label`, `total_cents`, `transaction_count`, and (Phase 3) `voting_cents` + `voting_projected: bool` for guild rows.
- `ReconciliationResult` — `window`, `groups: dict[RecipientKind, list[RecipientAllocation]]`, `lines: list[TransactionLine]`, `grand_total_cents`, `unassigned_note_count`.

Split percentages are module constants in Phase 1:

```python
CLASS_SPLIT = {"instructor": Decimal("70"), "guild": Decimal("10"), "pl": Decimal("20")}
ORIENTATION_SPLIT = {"orientator": Decimal("70"), "guild": Decimal("15"), "pl": Decimal("15")}
```

### Phase 2 — editable splits + per-transaction overrides

**`BillingSettings` gains six percent fields** (mirror `default_admin_percent` — `DecimalField(max_digits=5, decimal_places=2)`, `help_text` on each), so the split config re-uses the existing singleton (no new settings table):

| Field | Default | Note |
|---|---|---|
| `orientation_orientator_percent` | `70.00` | Orientation payments -> the orientator. |
| `orientation_guild_percent` | `15.00` | Orientation payments -> the guild. |
| `orientation_pl_percent` | `15.00` | Orientation payments -> Past Lives. |
| `class_instructor_percent` | `70.00` | Class payments -> the instructor. |
| `class_guild_percent` | `10.00` | Class payments -> the guild. |
| `class_pl_percent` | `20.00` | Class payments -> Past Lives. |

Each triad must sum to exactly `100.00` — enforced in `ReconciliationSettingsForm.clean()` (§6.3), never in the model save (validation lives in forms). The engine reads these instead of the constants once Phase 2 lands. Migration: additive `AddField` x6, reverse = drop columns (auto-reversible). Tab splits stay `ProductRevenueSplit`-driven and are **not** configurable here (they already have their own per-product editor).

**`TransactionAdjustment` (new)** — one row per corrected transaction:

| Field | Type | Note |
|---|---|---|
| `source_kind` | `CharField(choices=SourceKind.choices)` | `TextChoices`: `TAB`, `CLASS`, `ORIENTATION`. Which stream the target lives in. |
| `source_pk` | `PositiveIntegerField` | PK of the `TabCharge` / `Registration` / `OrientationBooking`. |
| `is_omitted` | `BooleanField(default=False)` | When true, the transaction is excluded from allocation entirely. |
| `override_percents` | `JSONField(null=True, blank=True)` | `null` = use the configured split; else `{"instructor": 60, "guild": 20, "pl": 20}` (keys per source kind). Tab overrides support **omit only** — `override_percents` stays null for `TAB` (a tab charge spans many `TabEntrySplit` rows; re-splitting it is out of scope, §10). |
| `reason` | `TextField(blank=True, default="")` | Internal note (why it was adjusted). |
| `created_by` | FK `AUTH_USER_MODEL`, `SET_NULL`, null, `related_name="+"` | Who adjusted it. |
| `created_at` / `updated_at` | `DateTimeField` (auto_now_add / auto_now) | |

`Meta`: `UniqueConstraint(fields=["source_kind", "source_pk"], name="uq_txnadj_source")` (one adjustment per transaction; the modal edits it in place), `ordering = ["-updated_at"]`. `help_text` on every field; `__str__` = `f"{get_source_kind_display()} #{source_pk} ({'omitted' if is_omitted else 'adjusted'})"`. Manager helper `TransactionAdjustment.objects.as_map()` -> `dict[(source_kind, source_pk), TransactionAdjustment]` so the engine does one query, not N. `override_percents` validity (keys correct, sum 100) is enforced in `TransactionAdjustmentForm`, not the model. Migration: new model, reverse = drop table. Run `manage.py check` (constraint-name 30-char cap, E034).

### Phase 3 — `ReconciliationSnapshot`

Mirrors `FundingSnapshot`'s *shape and CRUD* but is a plain immutable record — **no `emit()`, no email, no Airtable** (§5.6).

| Field | Type | Note |
|---|---|---|
| `title` | `CharField(max_length=255, blank=True, default="")` | Optional label, e.g. "August 2026". |
| `period_start` / `period_end` | `DateField` | The window this snapshot froze (defaults to the month being closed). |
| `results` | `JSONField(default=dict)` | Frozen `ReconciliationResult` serialized: per-recipient totals (including each guild's `voting_cents`), the split % in force, and the contributing-transaction count. `default=dict`, never `{}`. |
| `grand_total_cents` | `PositiveIntegerField` | Denormalized for the history list. |
| `is_auto` | `BooleanField(default=False)` | True when the month-end job took it; False for a manual take. |
| `taken_by` | FK `AUTH_USER_MODEL`, `SET_NULL`, null, `related_name="+"` | Null for the automated job. |
| `taken_at` | `DateTimeField(auto_now_add=True)` | Ordering key (`-taken_at`), mirroring `FundingSnapshot.snapshot_at`. |

`Meta`: `ordering = ["-taken_at"]`. `help_text` on every field; `__str__` = `f"Reconciliation {period_start:%b %Y} (${grand_total_cents/100:,.2f})"`. `default=dict` per the model rules. Migration: new model, reverse = drop table.

**`take()` is a fresh, simpler classmethod** — deliberately NOT `FundingSnapshot.take()` (that one emits `voting.results_ready` and its `save()` fires Airtable sync; scout-confirmed). See §5.5.

## 5. Business logic (fat models / read-time service)

### 5.1 The allocation engine — `billing/reconciliation.py` (Phase 1)

`build_reconciliation(*, window: PanelWindow, viewer_is_admin: bool = True, adjustments=None) -> ReconciliationResult`. Walks the same three streams as the ledger; attributes each payment; aggregates. All money is integer cents; all rounding mirrors `snapshot_splits` (§5.2).

**Class stream** — paid `Registration` rows in the window (by `confirmed_at`), `net_cents = amount_paid_cents - succeeded-refund cents`:
- instructor = `offering.instructor`; guild = `offering.category.guild`.
- Split `net_cents` by the class percentages into instructor / guild / PL shares.
- **Unassigned fallback (fail loud, but always balance):** if `instructor is None`, its share is attributed to **PL** with a `TransactionLine.note` "instructor unset -> Past Lives"; if `category.guild is None`, that share also rolls to PL with a note. This keeps the table summing to the collected total and makes the gap visible (surfaced as `unassigned_note_count` + a banner, §6.1) rather than silently dropping money. A fully-refunded registration contributes `net_cents == 0` (every share 0).

**Orientation stream** — paid `OrientationBooking` rows in the window (by `requested_at`, `PENDING_PAYMENT` excluded, mirroring `_orientation_rows`), `net_cents = amount_paid_cents - succeeded refunds`:
- orientator = `oriented_by` (nullable via `SET_NULL`); guild = `booking.guild` (always set).
- Split by the orientation percentages; a null orientator's 70% rolls to PL with a note.

**Tab stream** — re-use the frozen splits, counting **only money actually collected**: `TabEntrySplit` rows whose `entry.tab_charge.status == SUCCEEDED` and whose charge date falls in the window (align with `_tab_rows` using `Coalesce(charged_at, created_at)`; non-voided entries only). `ADMIN` splits -> PL; `GUILD` splits -> that guild. No re-splitting, no instructor/orientator. Tab refunds are deferred product-wide (`can_refund=False`), so no netting on this stream today; a documented seam (§10) once tab reconciliation ships.

**Aggregate:** group `TransactionLine.shares` by recipient key into `RecipientAllocation` rows, one per distinct guild / instructor / orientator plus the single PL row. Labels: guild name, member `display_name` for instructor/orientator, "Past Lives" for PL. Sort within each group by `total_cents` desc, then label. `grand_total_cents` = sum of all net collected (must equal the ledger's collected minus refunds for the window — pinned by a cross-check test, §9).

**Adjustments (Phase 2):** `adjustments` (the `as_map()` dict) is consulted per transaction: `is_omitted` -> skip the line (and count it in an "omitted" tally for the banner); `override_percents` -> use those instead of the configured split for that one transaction. Phase 1 passes `adjustments=None` and uses the configured/constant splits throughout.

### 5.2 Penny rounding (mirror `snapshot_splits`)

Per transaction, each recipient share = `(net_cents * pct / 100)` rounded half-up to whole cents, and the **largest-percent recipient absorbs the +/-1c remainder** so the shares sum exactly to `net_cents` (ties broken by a fixed recipient order: PL last, so a genuine tie lands on a producer, matching the "first row wins" spirit of the original). Factor this into a shared `split_cents(amount_cents, percents: dict) -> dict` helper and unit-test it directly (0c, 1c, 33/33/34, 70/15/15 on odd amounts). This guarantees the allocation table never shows a total a penny off from what was collected.

### 5.3 Reports fold-in

`build_report()` / `PayoutRow` / `stream_report_csv` stay as-is; the Reconciliation tab renders the tab-splits payout summary as a **secondary section** beneath the allocation table (§6.1) by calling `build_report(start_date=window.start, end_date=window.end)`. The standalone `admin_reports` view + URL + template are retired; any inbound `/billing/admin/reports/` link 301-redirects to `?tab=reconciliation` for the window (so the removed Admin Tools "Reports" card / bookmarks don't 404). The reports CSV is superseded by the reconciliation CSV (which is a superset).

### 5.4 View wiring — `_reconciliation_context(request)` (Phase 1)

Mirror `_payments_panel_context` exactly: read `start`/`end` from GET -> `parse_window` (defaults current month), build the result, return `{reconciliation, reconciliation_start, reconciliation_end, reconciliation_query, viewer_is_fog_admin}`. In `admin_tab_dashboard`, add `"reconciliation"` to `allowed` **only for actual admins** and extend the existing Settings/Stripe 403 guard to include it:

```python
if viewer_is_fog_admin:
    allowed |= {"settings", "stripe", "reconciliation"}
if active_tab in {"settings", "stripe", "reconciliation"} and not viewer_is_fog_admin:
    return HttpResponse("Admin access required.", status=403)
...
if active_tab == "reconciliation":
    context.update(_reconciliation_context(request))
```

A standalone `reconciliation_table` partial view (admin-only) serves the same table markup for the date-range HTMX refresh and (Phase 2) the post-adjust refresh, exactly as `billing_admin_payments_table` does for the payments tab. `admin_reconciliation_csv` streams the CSV for the current window+filters.

### 5.5 Snapshot logic (Phase 3)

`ReconciliationSnapshot.take(*, period_start, period_end, title="", is_auto=False, actor=None) -> ReconciliationSnapshot` — a **plain create**, no side effects beyond a `SiteActivity.log(...)` audit row (the one effect worth keeping; the voting mirror's `voting.results_ready` emit, member emails, and Airtable sync are all dropped):

1. Build the result for `[period_start, period_end]` via `build_reconciliation` **with adjustments applied** and **with each guild's voting allocation folded in** (§5.6).
2. Serialize to `results` JSON (recipient rows, split % in force, transaction count, voting figures).
3. `create(...)` the row (`grand_total_cents` denormalized), `SiteActivity.log(SiteActivity.Kind.RECONCILIATION_SNAPSHOT_TAKEN, ...)` — a new `SiteActivity.Kind` value.
4. Return the row. No email, no `emit()`, no Airtable — spelled out here so nobody "helpfully" wires them in by copying `FundingSnapshot.take()`.

Management command `take_reconciliation_snapshot` (default period = the just-ended month; `--month YYYY-MM` override; `is_auto` when run by the job). Idempotency: refuse (loudly) to take a second auto snapshot for a period that already has one, so a re-run doesn't double-record.

### 5.6 Voting allocation, rolling monthly (Phase 3)

Each guild's **overall total** on the Reconciliation tab = its direct splits (class 10% + orientation 15% + tab guild splits) **plus** its **voting allocation** for the window:

- **Projected (default, no snapshot yet for the window):** compute the live per-guild allocation the same way the voting page does — `vote_calculator.calculate_results(...)` over current `VotePreference`s and the configured funding pool (`serialize_live_votes()` is the existing entry point). The guild row shows this as `voting_cents` with `voting_projected=True`, badged **"Projected"**.
- **Snapshotted:** once a `ReconciliationSnapshot` exists for the window, the guild row reads its frozen `voting_cents` (`voting_projected=False`), badged **"Snapshotted {date}"**.
- **Rolling reset:** the voting allocation is a **per-month** figure. A new month starts projecting from zero-carried votes; the prior month's figure lives only in its snapshot. The tab never shows two months' voting funds stacked — the window scopes it, and the snapshot freezes it. (This matches the owner's "resets to 0 for the new month; votes carry over as projected until the snapshot.")

Snapshot CRUD mirrors voting, all `@fog_admin_required`:

| Voting view | Reconciliation twin | Behavior |
|---|---|---|
| `voting_history` | history list section (on the tab) | `ReconciliationSnapshot.objects.order_by("-taken_at")` — title, period, grand total, auto/manual, taken-by/at, Detail + Delete. |
| `voting_history_detail` | `reconciliation_snapshot_detail` | Read-only render of a frozen `results` JSON (same table shell, "as snapshotted" banner). |
| `voting_snapshot_take` | `reconciliation_snapshot_take` (`@require_POST`) | Calls `ReconciliationSnapshot.take(...)` for the current window; toast + refresh. |
| `voting_snapshot_delete` | `reconciliation_snapshot_delete` (`@require_POST`) | `confirm_modal` -> `snapshot.delete()`; toast + refresh. No Airtable teardown (there was never a row). |

### 5.7 Permissions

Reconciliation is **admin-only** across the board: the tab, the table partial, the CSV/PDF, the settings save, every adjust/snapshot endpoint gate on **actual fog-admin** (`view_as.has_actual("admin")`), not merely `billing_admin_access_required`. Money *disbursement* decisions are a strictly-admin duty; a `BILLING_APPROVER` sees Overview/Open Tabs/Payments but never Reconciliation (nav link hidden AND view 403 — the same treatment Settings/Stripe already get). Introduce a small `reconciliation_admin_required` decorator (or reuse `fog_admin_required`) so the endpoints are guarded uniformly, and pin the 403s in specs.

## 6. UI / UX  (completeness checklist applied per screen)

The dashboard extends `hub/base.html`, so the full theme-aware hub component set (modal, confirm_modal, toggle, form_field, `.pl-help`, toasts) is available. New classes use the `pl-` prefix; page chrome follows the dashboard's existing `pl-*` styling.

### 6.1 Reconciliation tab — `templates/billing/admin_dashboard.html` + `partials/reconciliation_table.html` (Phase 1)

- **Nav:** a `<a href="?tab=reconciliation" class="{% if active_tab == 'reconciliation' %}active{% endif %}">Reconciliation</a>` link, rendered **only when `viewer_is_fog_admin`** (wrap in `{% if viewer_is_fog_admin %}`, beside Settings/Stripe which are already admin-gated). Body: `{% if active_tab == "reconciliation" %}`.
- **Header / date control:** title "Reconciliation", subtitle "How much to disburse to each guild, instructor, orientator, and Past Lives for the selected month." A `<form method="get">` with two `<input type="date">` (`start`/`end`, prefilled from `reconciliation_start/end`) and an **"Apply"** primary button, preserving `tab=reconciliation`. Rule 14 on the pickers: `filter: invert(1)` inverted-in-dark with a `[data-theme="light"]` reset and `showPicker()` in a try/catch. A "This month" reset link restores the default window. Right-aligned: **"Export CSV"** (`pl-btn--secondary pl-btn--sm`, links `admin_reconciliation_csv?{{ reconciliation_query }}`) and (Phase 4) **"Export PDF"**.
- **Header stats (`pl-stats`):** Collected (net) · Disbursed to producers · Past Lives cut · (Phase 3) Voting allocated — respecting the window.
- **Allocation table** (the core), rendered by the partial and refreshed via `hx-get` the partial URL on an `hx-trigger` from Apply / adjust actions. **Grouped by recipient kind** — four subsections with a `pl-section-label` each: **Guilds**, **Instructors**, **Orientators**, **Past Lives**. Columns: Recipient · Transactions · Amount (right-aligned). Guild rows (Phase 3) gain a **Voting** column ("$X Projected" muted / "$X Snapshotted" solid) and a **Total** column (direct + voting). A group subtotal row and a grand-total footer row.
- **Contributing transactions (expandable):** below the recipient table, a collapsed `x-show` "Transaction detail" section (closed by default, per the optional-secondary-form pattern) listing each `TransactionLine`: date · source badge · payer · item · gross · refunded · net · the recipients it paid · (Phase 2) an **Adjust** control. This is where per-transaction CRUD lives (§6.2).
- **Tab payout summary (fold-in):** a secondary `pl-card` "Guild payout (tab charges)" rendering `build_report()`'s `payout_summary` (the retired Reports page's table), so nothing is lost in the fold-in.
- **States:**
  - *Empty* — no payments in the window: a centered muted card "No payments to reconcile in this window. Pick a different month." (each recipient group also shows "No {guilds/instructors/orientators} paid in this window." rather than a bare empty table).
  - *Loading* — the HTMX table refresh shows the standard `.htmx-request` opacity on the table wrapper.
  - *Error* — a bad manual date range (end before start) re-renders with an inline `pl-field-error` "End date must be on or after the start date." (parsed defensively; never a 500). A failed adjust POST raises an error toast (§6.2).
  - *Unassigned banner* — when `unassigned_note_count > 0`, a muted warning line at the top: "N payment(s) had no instructor or guild set, so that share went to Past Lives. Set them on the class/booking to reattribute." Not an alert-red block; informational.
  - *Success* — Apply reloads the table (or full page for the GET form); adjust actions toast + refresh.
- **No list-editor formset here** — the table is computed output, not an editable list, so the §1-of-checklist "+ Add / per-row Delete" rules don't apply to the table itself. They *do* apply to the Phase 2 settings form's nothing-listy content (it's a fixed 6-field form) — noted so a reviewer doesn't expect an Add button that has no meaning.
- **Dark + light:** tokens only (`--hub-*`); badges use existing badge classes; date inputs per Rule 14. Verify both themes.
- **Mobile:** each recipient subsection is a table inside `.pl-table-scroll { overflow-x:auto; }`; stat grid collapses under 768px (existing behavior); the transaction detail is a stacked list on narrow screens. Export buttons stay full-size tap targets.

### 6.2 Per-transaction adjust / omit (Phase 2) — modal on the transaction-detail rows

- **Layout:** each `TransactionLine` row gets an **"Adjust"** button (`pl-btn pl-btn--secondary pl-btn--sm`) opening `components/modal.html` (`modal_id="txn-adjust"`, size `sm`) whose body is `hx-get` of a `TransactionAdjustmentForm` partial keyed by `(source_kind, source_pk)`.
- **Form (`TransactionAdjustmentForm`, validation in the form):**
  - **"Do not count this transaction"** — a `components/toggle.html` boolean (`is_omitted`). When on, the percent fields hide (`x-show`) — an omitted transaction needs no split.
  - Percent fields — one per recipient for the source kind (class: instructor/guild/PL; orientation: orientator/guild/PL), via `form_field.html`, prefilled with the current effective split. **Tab transactions show no percent fields** — only the omit toggle (tabs are omit-only, §4.2); a hint says "Tab charges use their product splits; here you can only exclude the whole charge."
  - `reason` — optional `CharField`, hint "Internal note. Members never see this."
  - `clean()`: when not omitted and percents are editable, the triad must sum to exactly 100 -> error "The three percentages must add up to 100." Bounds 0-100 each.
- **Controls:** primary **"Save adjustment"** submit (last element, per Rule 21) posting to `reconciliation_adjust`; a **"Reset to default"** `pl-btn--danger pl-btn--sm` (only when an adjustment already exists) posting to `reconciliation_clear` behind a `confirm_modal` ("Remove this adjustment? The transaction goes back to the standard split."). Both return `204` + `trigger_toast(...)` + `HX-Trigger: reconciliation-changed`; the table wrapper listens (`hx-trigger="reconciliation-changed from:body"`) and re-fetches, so the recipient totals update live. A shared listener also closes the modal.
- **Omit affordance in the row:** an omitted transaction renders struck-through/muted in the detail list with an "Omitted" chip and an "Undo" that posts `reconciliation_clear`.
- **States:** loading placeholder in the modal; validation errors re-render the partial in place; success toast + refresh; the row's amount and every affected recipient subtotal update on refresh. Dark/light via tokens; mobile: the modal is one-column and thumb-reachable.

### 6.3 "Payments Dashboard Settings" — split config (Phase 2)

The percentages are editable **only here**. Placement: a **"Reconciliation splits"** section within the existing **Settings** tab of the dashboard (fog-admin only already), directly beneath the current billing settings, so all admin billing config lives on one tab — OR (if the Settings tab grows unwieldy) a dedicated sub-panel reachable from a "Payments Dashboard Settings" button there. Pick the in-tab section (fewer surfaces; the Settings tab is already the money-config home).

- **Screen:** `templates/billing/partials/reconciliation_settings.html`, an inline form (6 fields -> inline, per the interaction table), each rendered via `form_field.html`, grouped visually as two triads (Orientation splits / Class splits) with a `pl-section-label` each and a running "= 100%" helper.
- **Form (`ReconciliationSettingsForm`, ModelForm on `BillingSettings`):** `clean()` enforces each triad sums to 100.00 -> field-level errors "Orientation percentages must add up to 100 (currently 105)." Fields are `DecimalField`, hints explain "Percent of each paid orientation that goes to the orientator," etc.
- **Save:** a single **"Save"** button (last element, label just "Save" per Rule 21), posting to a new `billing_admin_save_reconciliation_settings` view (mirrors `billing_admin_save_settings`), full-page POST -> Django success message "Reconciliation splits saved." A changed split affects **future reconciliation reads only**; already-taken snapshots keep their frozen % (they store the % in force).
- **Read-only echo:** the section header notes "These percentages also show, read-only, on each guild's Orientation settings."
- **States:** validation error re-renders inline with the offending triad flagged; success message; no empty/loading states (static form). Dark/light: fields inherit `.hub-form-group`; no inline control styles. Mobile: the triads stack.

### 6.4 Orientation settings — read-only split display (Phase 1)

On `templates/hub/guild_edit.html`, Orientations tab, directly under the existing **Orientation price** field (around line 353): a read-only, non-editable line:

> **Orientation payment split:** 70% orientator · 15% guild · 15% Past Lives.
> *Set by an admin on the Payments Dashboard settings. Applies when this orientation has a price.*

Rendered as static `hub-text-muted` text (Phase 1: from the constants; Phase 2: from `BillingSettings`), with a `.pl-help` "?" bubble: "This is how a paid orientation's fee is divided. Only an admin can change it, on the Payments Dashboard settings." **Not** a form field — no input, no save, so it never looks editable here (the owner's locked decision). Shown to whoever can edit the guild (leads/staff), so a lead understands where their cut comes from. Free guilds still see it (informational), or gate it on `is_paid` — gate on `is_paid` to avoid clutter on free guilds. Dark/light via tokens; wraps on mobile.

### 6.5 Snapshot history / take / delete (Phase 3)

Rendered as a section on the Reconciliation tab (and/or a "History" sub-view), mirroring the voting snapshot pages:

- **Take control:** a **"Take snapshot for {month}"** primary button (with an optional title input in a small `sm` modal) posting to `reconciliation_snapshot_take`. Confirms in the modal: "Freeze this month's reconciliation? Producers' voting allocations lock at their projected values and the new month starts fresh." Success toast + the history list refreshes; the current-window table flips its voting badges from "Projected" to "Snapshotted".
- **History list:** table of snapshots — Period · Title · Grand total · Auto/Manual · Taken by · Taken at · **Detail** link · **Delete** (`pl-btn--danger pl-btn--sm`). Empty state: "No snapshots yet. Take one at month end to freeze that month's payouts."
- **Detail:** read-only render of the frozen `results` (the same allocation table shell) with an "As snapshotted {date}" banner and a "back to history" link — no edit affordances (immutable record).
- **Delete:** `confirm_modal` "Delete this snapshot? This only removes the frozen record; it does not change any payments." -> `reconciliation_snapshot_delete`. Toast + refresh.
- **States/dark/light/mobile:** standard — empty (above), loading (HTMX opacity), success (toast), tables scroll in-container on mobile; tokens only.

## 7. Notifications / emails / activity

- **No emails, no `emit()` events, no push, no Discord** anywhere in this feature — it is an internal admin bookkeeping surface. This is the deliberate divergence from the voting `FundingSnapshot` mirror (§5.5/§5.6).
- **Activity:** one new `SiteActivity.Kind` value `RECONCILIATION_SNAPSHOT_TAKEN`, logged by `ReconciliationSnapshot.take()` and on delete (a `RECONCILIATION_SNAPSHOT_DELETED` kind, or reuse a generic admin-action log) — the audit trail for a frozen/removed month. No Airtable sync (there is no Airtable table for it, and none is wanted).

## 8. Build order (phased; each phase ships green)

Each phase is independently shippable green (full suite + `ruff` + `mypy` via the pre-push hook) and demo-able.

1. **Phase 1 — Reconciliation tab + allocation engine (demo core).**
   `billing/reconciliation.py` (dataclasses, `split_cents`, `build_reconciliation` over the three streams with the fixed-constant splits, net-of-refunds, unassigned->PL fallback) + `stream_reconciliation_csv`; `_reconciliation_context` + the `allowed`/403 wiring in `admin_tab_dashboard`; the table partial + partial view + `admin_reconciliation_csv`; the tab template (nav + body + date control + grouped table + tab-payout fold-in + states); the read-only split line on the Orientations tab; retire `admin_reports` (redirect its URL). Ships the whole demo-critical answer, CSV included.
2. **Phase 2 — Editable splits + per-transaction CRUD.**
   6 `BillingSettings` percent fields (+ migration) read by the engine; `ReconciliationSettingsForm` + `billing_admin_save_reconciliation_settings` + the Settings-tab section; `TransactionAdjustment` model (+ migration, `manage.py check`); `TransactionAdjustmentForm` + adjust/clear endpoints + the transaction-detail Adjust modal + omit affordance; engine consults `adjustments`.
3. **Phase 3 — Monthly snapshot + voting allocation.**
   `ReconciliationSnapshot` model (+ migration); `take()` (no email/event/Airtable) + `SiteActivity.Kind` additions; `take_reconciliation_snapshot` command; the take/detail/delete CRUD mirroring voting; voting allocation folded into guild totals (projected via `calculate_results`, snapshotted from the frozen JSON); register the month-end job in `SCHEDULED_JOBS` **and update the matching `_DISPATCHER_*` parity tuple in `core/spec/scheduled_jobs_spec.py`** (the command must be a real management command — it is).
4. **Phase 4 — PDF export.**
   Add a PDF dependency and `billing/reconciliation_pdf.py` + an `admin_reconciliation_pdf` view + the "Export PDF" button. **Pick `xhtml2pdf` (or `reportlab`) over `weasyprint`** — weasyprint needs system libraries (pango/cairo/gdk-pixbuf) that complicate the Render build, whereas `xhtml2pdf` is pure-Python and renders an HTML template to PDF with no OS packages; `reportlab` is the fallback if table fidelity needs hand-drawing. Justify the final pick in the PR. Render the same allocation table (a print-oriented template) to a downloadable PDF with the window and generated-at stamp.
5. **Housekeeping (each phase's shipping PR).**
   Bump `plfog/version.py` VERSION; **one** member-friendly CHANGELOG entry folded per the changelog rules. Reconciliation is an admin-internal tool, so the member-facing changelog line is minimal or omitted (git history is the record) — if anything, a single entry when the whole feature lands, plain language, e.g. *"Admins can now see, per month, exactly what to pay out to each guild, instructor, and orientator."* No jargon, no PR numbers.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*` (never `context_*` — silently skipped), factory-boy, 100% branch coverage, mutation-clean, run in the `plfog-web` image. Stripe/refunds are already-shipped and mocked the suite's way; this feature adds no Stripe calls. Never mock models/DB — build real `Registration`/`OrientationBooking`/`TabEntrySplit`/refund rows via factories.

- **`tests/billing/reconciliation_spec.py` (engine, the core):**
  - `split_cents` directly: 0c, 1c, even 70/15/15 and 70/10/20 on amounts where naive rounding drifts (largest-percent absorbs the +/-1c; sum-exactly invariant); PL-last tie rule.
  - Class stream: instructor/guild/PL shares correct; **instructor unset -> PL with a note** and `unassigned_note_count` bumped; **guild-less category -> PL with a note**; a partially-refunded registration splits the *net*; a fully-refunded one contributes 0.
  - Orientation stream: 70/15/15; null `oriented_by` -> PL note; `PENDING_PAYMENT` excluded; net-of-refunds.
  - Tab stream: only `SUCCEEDED`-charge `TabEntrySplit` rows counted; admin->PL, guild->that guild; voided entries excluded; failed/pending charges excluded.
  - Aggregation: distinct recipient rows; grand total; **cross-check** grand_total_cents equals `build_payments_ledger` collected-minus-refunded for the same window (the two engines must agree).
  - Date-window/tz: a `confirmed_at`/`requested_at`/`charged_at` just past UTC midnight lands in the correct Portland month bucket (fixtures at `now + timedelta(days=2)` and at month edges).
  - CSV: headers + one row per recipient (and/or per transaction line), amounts formatted, streams via `_Echo`.
- **View gating (`tests/billing/reconciliation_views_spec.py`):** tab/table-partial/CSV/settings-save/adjust/snapshot endpoints — **200 for actual fog-admin, 403 for `BILLING_APPROVER`, 403 for plain member**; the Reconciliation nav link present only for admins; `?tab=reconciliation` as non-admin 403 (not fallback); retired `/billing/admin/reports/` redirects to `?tab=reconciliation`.
- **Phase 2:** `ReconciliationSettingsForm` — each triad sums to 100 (105 and 95 rejected with the exact message), bounds; save updates `BillingSettings`; engine reads the new % (change split -> totals move). `TransactionAdjustment` — unique per `(source_kind, source_pk)`; `as_map()`; `TransactionAdjustmentForm` omit-hides-percents, override sums to 100, tab = omit-only; engine honors omit (line skipped, omitted tally) and override (custom split); clear removes the row. `manage.py check` after the migration (constraint-name cap).
- **Phase 3:** `ReconciliationSnapshot.take()` creates the row, denormalizes grand total, logs `SiteActivity`, **emits no event / sends no email / touches no Airtable** (assert `TransactionalEmailLog` unchanged and no `emit` call — patch-and-assert-not-called); idempotent auto-take refusal; voting allocation projected vs snapshotted (a guild's total includes `calculate_results` projection before a snapshot, the frozen figure after); take/detail/delete views + confirm modal; the command's default-month resolution and `--month`. **`core/spec/scheduled_jobs_spec.py`:** the new job key appears in `SCHEDULED_JOBS` and in the matching `_DISPATCHER_*` tuple (parity test stays green), and points at the real `take_reconciliation_snapshot` command.
- **Phase 4:** `render_reconciliation_pdf` returns a non-empty `application/pdf`; the view is admin-gated; the button links with the current window.
- **Templates:** empty state (no payments), unassigned banner (>0 notes), date-range error (end<start), both themes referenced; `template_comment_lint` (no multi-line `{# #}`); changelog-renders-everywhere gotcha — assert on `{% url %}`/distinctive markup, not generic strings like "Reconciliation" against full-page HTML.

## 10. Open / deferred

- **Tab per-transaction split editing** — a tab charge spans many `TabEntrySplit` rows; adjusting its internal split needs the same split-reversal design the refunds spec deferred. Tabs are **omit-only** here; revisit with tab-refund reconciliation.
- **Tab refund netting** — tab refunds don't exist product-wide yet (`can_refund=False`); when they ship, net the tab stream like class/orientation (the seam is `_tab_rows`' `SUCCEEDED` filter).
- **Automated Stripe Connect payouts** — out of scope; disbursement stays manual (per `billing/CLAUDE.md`). This feature computes the numbers; a human moves the money.
- **Configurable voting pool from the PL cut** — the voting pool stays whatever the voting system configures; wiring "fund voting from this month's PL cut" is a policy decision, not built here.
- **Per-recipient payout export / statements** — a per-instructor or per-guild PDF statement (vs. the whole-month table) is a natural Phase-4+ follow-on; the CSV already lets an admin filter externally.
- **Historical accuracy vs. split changes** — snapshots freeze the % in force; live reads use current %. A "recompute a past month at today's rules" toggle is deliberately absent (a snapshot is the record).
