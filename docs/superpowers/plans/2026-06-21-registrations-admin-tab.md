# Plan: Registrations admin tab — filters, CSV export, and admin actions (cancel / move / refund)

## Context

The classes admin already has a **global registrations page** (`admin_registrations` →
`/classes/admin/registrations/` → `templates/classes/admin/registrations.html`) built on the
`prepare_table` search/sort/paginate helper. But today it is:

- **Admin-only** (`@classes_admin_access_required`, which checks `view_as.has_actual("admin")`).
- **Not a visible tab** — `active_tab="registrations"` is folded into the *Overview* group in
  `templates/classes/admin/base.html` (`overview_tabs="overview activity registrations"`), so there
  is no dedicated "Registrations" nav item.
- **No filters** beyond the free-text search, **no CSV export** (export exists only per-class via
  `admin_class_export` → `classes/exports.stream_registrations_csv`), and **no row actions** other
  than cancel-from-detail (`admin_registration_cancel` → `Registration.cancel()`).

We already have the building blocks: `prepare_table` (`classes/table.py`), the streaming-CSV pattern
(`classes/exports.py`), `Registration.cancel()` (frees the spot + promotes waitlist), and the
`ClassOffering.objects.editable_by(member)` manager (built in the guild-lead refactor) for per-instructor
scoping.

### Decisions locked with the user
- **Refunds:** **record-only**. Mark `status=REFUNDED`, free the spot, promote the waitlist, log it,
  and deep-link to the Stripe payment so a human issues the actual money refund by hand. *No Stripe
  write code* — matches today's manual process and the `register` view's own note ("Refunds aren't
  automated — admins handle them").
- **Non-admin scope:** **one shared, role-scoped page**. Same URL `/classes/admin/registrations/`,
  opened to instructors/guild-leads, with the queryset scoped to classes they can edit
  (`editable_by`). Admins see everything. The *other* admin tabs stay admin-only.
- **Move/reassign:** **move as-is, no price reconciliation**. Change the class, keep `amount_paid_cents`,
  re-check waitlist on the source class, log it. Any money difference is handled by the admin manually
  (or via the record-only refund).

## Approach

### A. Surface Registrations as a real, role-aware tab
- `hub/view_as.py`: add a no-arg `is_actual_admin` property (`return self.has_actual("admin")`) so
  templates can branch without calling a method with args.
- `templates/classes/admin/base.html`: drop `registrations` from the `overview_tabs` group
  (→ `overview_tabs="overview activity"`). Render **Overview / Classes / Settings** only when
  `request.view_as.is_actual_admin`; render a dedicated **Registrations** tab when
  `is_actual_admin or active_tab == 'registrations'` (so admins always see it, and a scoped instructor
  viewing the page sees *only* it — no dead tabs that would 403).

### B. Role-scoped access + queryset (DRY helper)
- `classes/views.py`:
  - New decorator `classes_registrations_access_required` — allow when `view_as.has_actual("admin")`,
    else when the linked member has ≥1 editable class (`editable_by(member).exists()`), else
    `HttpResponseForbidden`. Mirrors `classes_admin_access_required`'s shape.
  - New helper `_scoped_registrations(request) -> QuerySet[Registration]`:
    admin/officer → all; else `Registration.objects.filter(class_offering__in=editable_by(member))`;
    no member → `.none()`. Always `select_related("class_offering", "member")`.

### C. Filters + the list view
- New helper `_filter_registrations(request, qs)` applies optional GET params:
  - `status` → `qs.filter(status=...)` (validated against `Registration.Status.values`; blank = all)
  - `class` → `qs.filter(class_offering_id=...)` (int-parsed; bad value ignored)
  These params ride through to pagination links automatically — `prepare_table` already copies unknown
  GET keys into `base_params`.
- `admin_registrations` rewritten: `qs = _filter_registrations(request, _scoped_registrations(request))`,
  then `prepare_table(...)`, then render with context: `active_tab="registrations"`, the table dict,
  `status_choices=Registration.Status.choices`, `status=<current>`, `class_options=<scoped offerings>`,
  `class_filter=<current>`, and `export_qs=base_params` for the export link.
- `templates/classes/admin/registrations.html`: replace the bare search include with a small filter
  toolbar — a single GET `<form>` containing the search box, a **Status** `<select>`, a **Class**
  `<select>` (options = scoped offerings), hidden `sort`/`dir` to preserve ordering, and an **Apply**
  button + an **Export CSV** link (`{% url 'classes:admin_registrations_export' %}?{{ base_params }}`).
  Add an **Order #** column.

### D. CSV export of the filtered, scoped list
- `classes/exports.py`: add `stream_registrations_query_csv(registrations, *, filename_stem)` —
  generalizes the existing function to any queryset and adds **Order #** + **Class** columns. Refactor
  `stream_registrations_csv(offering)` to delegate to it (keeps the per-class export working unchanged).
- `classes/views.py`: new `admin_registrations_export` (same `classes_registrations_access_required`
  gate) → rebuilds `_filter_registrations(request, _scoped_registrations(request))` and streams it.

### E. Admin row actions on the detail page (admin-only)
Model methods (fat models — `classes/models.py`):
- `Registration.mark_refunded(self, reason="", actor=None) -> None` — sibling of `cancel()`:
  capture `previously_held_a_spot`, set `self._acting_user = actor`, `status = REFUNDED`,
  `cancelled_at = now`, store `reason` in `cancellation_reason`, `save(update_fields=[...])` (the
  existing `REFUNDED` transition in `save()` logs `REGISTRATION_REFUNDED` + notifies the registrant),
  then `class_offering.promote_next_from_waitlist()` if it held a spot. **No Stripe call.**
- `Registration.move_to(self, target, actor=None) -> None` — raise `ValueError` if `target == current`;
  capture `held_spot`; reassign `class_offering`; `save(update_fields=["class_offering"])`; log a new
  `CmsActivity.Kind.REGISTRATION_MOVED` with payload `{"from": <old title>, "to": <new title>}`;
  promote the **source** class's waitlist if it held a spot. `amount_paid_cents` untouched.
- `classes/models.py` `CmsActivity.Kind`: add `REGISTRATION_MOVED = "registration_moved", "Moved"`
  (+ a no-op `AlterField` migration for the choices change).

Views (skinny — `classes/views.py`):
- `admin_registration_detail` → switch gate to `classes_registrations_access_required` and fetch via
  `get_object_or_404(_scoped_registrations(request), pk=pk)` so an instructor can view their own
  classes' registrants (out-of-scope → 404). Mutation buttons render only for `is_actual_admin`.
- `admin_registration_cancel` (exists) stays `@classes_admin_access_required`.
- New `admin_registration_move` (`@classes_admin_access_required`, POST) — `RegistrationMoveForm`
  validates the target offering (excludes current); on valid → `registration.move_to(target, actor)`.
- New `admin_registration_refund` (`@classes_admin_access_required`, `@require_POST`) →
  `registration.mark_refunded(reason, actor)`.
- `classes/forms.py`: `RegistrationMoveForm(forms.Form)` with
  `target = forms.ModelChoiceField(queryset=ClassOffering.objects.all())`; `__init__(current=...)`
  excludes the current offering; `clean_target` rejects the current class.
- `templates/classes/admin/registration_detail.html`: an **Actions** block, admin-only — **Cancel**
  and **Refund** as `.hub-btn--sm .hub-btn--danger` wired to `confirm_modal.html`; **Move** opens a
  small modal with the class `<select>`; when `registration.stripe_payment_id`, show a "Refund in
  Stripe ↗" deep link to `https://dashboard.stripe.com/payments/<payment_intent_id>` next to the
  record-only Refund button.

### F. URLs + housekeeping
- `classes/urls.py`: add `admin/registrations/export/` → `admin_registrations_export`,
  `admin/registrations/<pk>/move/` → `admin_registration_move`,
  `admin/registrations/<pk>/refund/` → `admin_registration_refund`.
- `plfog/version.py`: member-friendly changelog bullet (admins/instructors can now see, filter, and
  export class registrations in one place, and cancel / move / refund a registrant).

## Critical files
- `hub/view_as.py` — `is_actual_admin` property (A)
- `templates/classes/admin/base.html` — role-aware tab nav + Registrations tab (A)
- `classes/views.py` — gate decorator, `_scoped_registrations`, `_filter_registrations`, list/export/
  detail/move/refund views (B–E)
- `classes/exports.py` — `stream_registrations_query_csv` + delegate (D)
- `classes/models.py` — `mark_refunded`, `move_to`, `REGISTRATION_MOVED` kind (E)
- `classes/forms.py` — `RegistrationMoveForm` (E)
- `templates/classes/admin/registrations.html` — filter toolbar + export + Order # (C/D)
- `templates/classes/admin/registration_detail.html` — admin actions + Stripe deep link (E)
- `classes/urls.py` — export/move/refund routes (F)
- new migration for `REGISTRATION_MOVED` choices change (E)
- `plfog/version.py` — changelog (F)

## Reuse (don't reinvent)
- `prepare_table` (search/sort/paginate, carries unknown params) — `classes/table.py`.
- Streaming-CSV `_Echo` + `StreamingHttpResponse` pattern — `classes/exports.py`.
- `Registration.cancel()` spot-freeing/waitlist-promotion shape — model the refund/move on it.
- `ClassOffering.objects.editable_by(member)` — per-instructor/guild-lead scoping.
- `confirm_modal.html` + `.hub-btn--sm .hub-btn--danger` — destructive-button standard.
- `classes_admin_access_required` decorator shape — copy for the role-scoped gate.

## Testing / verification (BDD `*_spec.py`, ≥98% gate)
- **Models:** `mark_refunded` (sets REFUNDED, frees spot, promotes source waitlist, attributes actor,
  stores reason, idempotent on already-refunded); `move_to` (reassigns class, keeps `amount_paid_cents`,
  promotes source waitlist, raises on same-class).
- **`is_actual_admin`** property (true for real admin; false under view-as-member preview).
- **Exports:** `stream_registrations_query_csv` headers + a row's values; per-class delegate unchanged.
- **List view:** admin sees all; instructor sees only their classes' rows; guild-lead sees their guild's;
  plain member → 403; status filter; class filter; export streams the filtered/scoped set.
- **Detail/actions:** instructor can view in-scope detail but sees no mutation buttons; out-of-scope →
  404; `move` POST reassigns; `refund` POST marks refunded; all mutation routes 403 for non-admins.
- **Manual** on `book.pastlives.test:8000` (never localhost): admin sees the Registrations tab + filters
  + export + cancel/move/refund; an instructor account sees only their classes' rows and no action
  buttons; the Stripe deep link points at the right payment.
- **CI mirror** (SQLite via Docker): full `pytest` green, coverage ≥ 98%, scoped `mypy` clean.

## Notes / scope
- Mutations (cancel/move/refund) are **admin-only** per the user's framing ("options an admin would
  want"); instructors get the consolidated list + export + read-only detail. They already manage their
  own classes' registrations in the Teaching portal.
- Move ignores target capacity (admin override); the modal surfaces the target's spots-remaining as
  information, not a hard block.
- Date-range filtering is intentionally out of scope for v1 (status + class cover the asks).
