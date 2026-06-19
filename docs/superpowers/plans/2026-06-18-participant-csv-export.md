# Participant CSV Export — Per-Class Registration Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Each task is a TDD loop (write failing test → confirm it fails → implement → confirm it passes → lint + commit). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "Export Data" button to the per-class registration views (admin **and** teach) that downloads a clean server-side CSV of that one class's registrants. Columns must include the five from the spec — First Name, Last Name, Email Address, Registration Date, Payment Status — plus a small set of justified extras. Instructors get **read + export** access to their own classes; only admins/staff keep delete/refund. The instructor export must be scoped so a teaching member can only export **their own** classes, never an arbitrary `class_id`.

**Architecture:** Fat-model / skinny-view. The CSV-building logic lives in a new `classes/exports.py` module (mirroring `billing/reports.py`), which exposes a single function that takes a `ClassOffering` and returns a `StreamingHttpResponse`. Two thin views (`admin_class_export`, `teach_class_export`) reuse the **existing** scoping decorators and helpers — `@classes_admin_access_required` for admin, `@teaching_member_required` + `_teach_class_or_404` for teach — then delegate to the shared exporter. No new model fields, no migration, no new dependency.

**Tech Stack:** Django views + `csv.writer` + `StreamingHttpResponse` (stdlib + Django, exactly as `billing/reports.py` already does). Django templates for the two buttons. pytest + pytest-describe + factory-boy for tests. ruff (line 120) + mypy. No JS, no new packages, no DB changes.

---

## Background / context for the implementer

### The CSV pattern to copy (already in the repo — mirror it, don't reinvent)
- `billing/reports.py:185-189` — the `_Echo` file-like class whose `write()` returns the row payload (so `csv.writer` can feed a generator).
- `billing/reports.py:192-203` — module-level `CSV_HEADERS` list.
- `billing/reports.py:206-246` — `stream_report_csv(...)`: builds `csv.writer(_Echo())`, yields the header row then one row per record from a generator, wraps it in `StreamingHttpResponse(iter_rows(), content_type="text/csv")`, and sets `Content-Disposition: attachment; filename="...-<YYYYMMDD>.csv"` via `timezone.now().strftime("%Y%m%d")`.
- **This is the whole template.** The new exporter is a smaller version of the same shape: header + one row per `Registration`, filename stamped with the date and the class slug.

### The two views to add an export sibling to (both already class-scoped — reuse their scoping)
- **Admin:** `classes/views.py:1537-1555` `admin_class_registrations(request, pk)` — decorated `@classes_admin_access_required` (`classes/views.py:700-717`, requires `request.view_as.has_actual("admin")`). Looks up the offering via `get_object_or_404(ClassOffering, pk=pk)` (any class — admins see all).
- **Teach:** `classes/views.py:1076-1095` `teach_class_registrations(request, pk)` — decorated `@teaching_member_required` (`classes/views.py:682-697`, requires an ACTIVE member; sets `request.teaching_member`). Looks up the offering via `_teach_class_or_404(request, pk)`.
- **The scoping helper that makes the instructor boundary safe:** `classes/views.py:1054-1057`
  ```python
  def _teach_class_or_404(request: HttpRequest, pk: int) -> ClassOffering:
      """Scope a per-class Workspace lookup to the logged-in teaching member's own class."""
      teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
      return get_object_or_404(ClassOffering.objects.filter(instructor=teaching_member), pk=pk)
  ```
  Because the queryset is pre-filtered to `instructor=teaching_member`, requesting another instructor's `pk` raises 404. **The teach export MUST go through this helper** — that is the entire security boundary for spec item #3. The existing teach tabs (`teach_class_registrations`, `teach_class_waitlist`, `teach_class_email`) all use it, and `teach_class_workspace_spec.py:51-66` already proves the 404 boundary for those tabs; the export needs the same coverage.

### The Registration fields the CSV needs (all confirmed present)
All on `classes/models.py` `Registration` (model starts `classes/models.py:905`):
- `first_name` — `classes/models.py:927` (`CharField`).
- `last_name` — `classes/models.py:928` (`CharField`).
- `email` — `classes/models.py:930` (`EmailField`).
- `registered_at` — `classes/models.py:983` (`DateTimeField(auto_now_add=True)`).
- `status` — `classes/models.py:948-953`; choices on `Registration.Status` (`classes/models.py:906-911`): `PENDING="pending"`, `CONFIRMED="confirmed"`, `WAITLISTED="waitlisted"`, `CANCELLED="cancelled"`, `REFUNDED="refunded"`. Human label via `get_status_display()`.
- `phone` — `classes/models.py:931` (`CharField(blank=True)`) — the one justified extra (see Decisions).
- `amount_paid_cents` — `classes/models.py:947` (`PositiveIntegerField`) — second justified extra; the registrations table already shows it (`templates/classes/admin/class_registrations.html:36`).
- The reverse accessor from the offering is `offering.registrations` (`related_name="registrations"`, `classes/models.py:914-918`), already used by both registration views.

### The two templates to add the button to
- `templates/classes/admin/class_registrations.html` — extends `classes/admin/class_detail_base.html`; the registrations live in a `<form>` and there's an existing button row at `:44-46` (`<div style="margin-top:0.75rem;"> ... hub-btn hub-btn--sm hub-btn--ghost ...`). The empty-state `{% else %}` branch is `:59-61`.
- `templates/classes/teach/class_registrations.html` — identical structure; button row at `:44-46`, empty state at `:59-61`.
- Both already `{% load classes_tags %}` and reference `offering.pk`. The button is a plain `<a href="{% url ... pk=offering.pk %}">` styled like the existing ghost button — **not** inside the POST email form (a GET download link must not nest in the email `<form>`; place it adjacent).
- Button standard (from project memory `feedback_button_standards.md`): use `hub-btn hub-btn--sm` classes. Export is non-destructive, so `hub-btn--ghost` (matching the neighboring "Email selected students" button) is correct; do **not** use `--danger`.

### URL wiring (existing names + where to slot the new ones)
`classes/urls.py` (namespace `classes:`):
- Admin registrations: `classes/urls.py:66` `path("admin/<int:pk>/registrations/", views.admin_class_registrations, name="admin_class_registrations")`.
- Teach registrations: `classes/urls.py:23-27` `path("teach/classes/<int:pk>/registrations/", views.teach_class_registrations, name="teach_class_registrations")`.
- Add the two export paths immediately after their respective registrations path.

### Test infrastructure (reuse, don't invent)
- Spec dir: `classes/spec/views/` (BDD `*_spec.py`). View specs already exist for both surfaces: `classes/spec/views/admin_class_email_spec.py`, `classes/spec/views/teach_class_workspace_spec.py`.
- Shared fixtures `classes/spec/conftest.py`: `admin_user` (`:9-25`, member with `fog_role=ADMIN`, calls `sync_user_permissions()`) and `member_user` (`:28-39`, plain member — used to assert 403 on admin endpoints).
- Factories `classes/factories.py`: `RegistrationFactory` (`:117-125` — `first_name="Test"`, `last_name="User"`, sequenced `email`, `amount_paid_cents=0`), `ClassOfferingFactory` (`:75-`), `InstructorFactory` (`:43-`), `UserFactory` (`:34-`). The teach spec builds its own `instructor_fixture` / `other_instructor` (`teach_class_workspace_spec.py:27-37`) — copy that pattern for the instructor-boundary test.
- The admin email spec proves the existing decorator behavior to mirror: `it_requires_admin_access` returns **403** for a plain member (`admin_class_email_spec.py:24-31`). The export's admin 403 test mirrors this.

### Decisions baked into this plan
- **Column set (7 columns).** The five required, in spec order, plus two justified extras:
  1. `First Name` ← `first_name`
  2. `Last Name` ← `last_name`
  3. `Email Address` ← `email`
  4. `Registration Date` ← `registered_at.date().isoformat()` (date only, mirrors `billing/reports.py:230` which uses `.date().isoformat()`; the table shows date-only too)
  5. `Payment Status` ← `get_status_display()` (human label, e.g. "Confirmed", not the raw `confirmed`)
  6. `Phone` ← `phone` (justified: organizers contact registrants by phone for class logistics; it's a captured field with no PII beyond what staff already see on the detail page)
  7. `Amount Paid` ← `amount_paid_cents` rendered as dollars (justified: already shown in the registrations table at `class_registrations.html:36`; needed for reconciliation). Format as `f"{cents / 100:.2f}"` (plain number, no `$`, so spreadsheets parse it).
  - **Header labels are the human-readable strings above** (e.g. `"Email Address"`, `"Registration Date"`, `"Payment Status"`) to match the spec wording — not snake_case keys. This differs from `billing/reports.py` (which uses lowercase keys) deliberately: this CSV is for non-technical organizers.
  - **No filtering by status.** Export every registration for the class (including cancelled/refunded) so the file is a complete record. Order `-registered_at` to match the on-screen table. (The email feature excludes cancelled because you can't email them; export has no such reason.)
- **Shared exporter, two thin views.** Put the CSV logic in `classes/exports.py` as `stream_registrations_csv(offering: ClassOffering) -> StreamingHttpResponse`. Both views call it. This keeps the views skinny (parse → scope → delegate) and the logic testable in isolation, matching how `billing/reports.py` is separate from billing views.
- **GET, not POST.** A download is a safe, idempotent read — GET with a normal `<a>` link. No CSRF concern, no form. (The teach button must sit *outside* the email POST `<form>` in the template.)
- **RBAC is already correct for deletion — do NOT rebuild it.** The only cancel endpoint is `admin_registration_cancel` (`classes/views.py:2000-2006`), decorated `@classes_admin_access_required`. Teach views are read/email-only. This plan adds *export* to teach; it does **not** touch cancel/refund. Note it in the verification, don't re-implement it.
- **Filename:** `participants-<class-slug>-<YYYYMMDD>.csv` via `offering.slug` and `timezone.now().strftime("%Y%m%d")`.

---

## File Structure

- **Create:** `classes/exports.py` — `_Echo`, `CSV_HEADERS`, `stream_registrations_csv(offering)`.
- **Modify:** `classes/views.py` — add `admin_class_export` (after `admin_class_registrations`, ~`:1555`) and `teach_class_export` (after `teach_class_registrations`, ~`:1095`); add `StreamingHttpResponse` to the `django.http` import (`classes/views.py:17`).
- **Modify:** `classes/urls.py` — add `admin_class_export` path after `:66` and `teach_class_export` path after `:27`.
- **Modify:** `templates/classes/admin/class_registrations.html` — add Export Data link near the button row (`:44-46`).
- **Modify:** `templates/classes/teach/class_registrations.html` — add Export Data link near the button row (`:44-46`), outside the email `<form>`.
- **Create test:** `classes/spec/exports_spec.py` — exporter unit tests (columns, header, content, formatting).
- **Create test:** `classes/spec/views/registration_export_spec.py` — view tests (admin access, teach own-class success, teach foreign-class 404 boundary, headers/Content-Disposition).
- **Modify:** `plfog/version.py` — version bump + member-friendly changelog entry.

---

## Task 1: Build the shared CSV exporter (`classes/exports.py`)

**Files:** `classes/exports.py` (new), `classes/spec/exports_spec.py` (new)

- [ ] **Step 1: Write the failing spec.** Create `classes/spec/exports_spec.py`:
  ```python
  """BDD specs for the per-class registration CSV exporter."""

  from __future__ import annotations

  import csv
  import io

  import pytest

  from classes.exports import CSV_HEADERS, stream_registrations_csv
  from classes.factories import ClassOfferingFactory, RegistrationFactory
  from classes.models import Registration

  pytestmark = pytest.mark.django_db


  def _read_csv(response) -> list[list[str]]:
      body = b"".join(response.streaming_content).decode()
      return list(csv.reader(io.StringIO(body)))


  def describe_stream_registrations_csv():
      def it_emits_the_spec_columns_in_order():
          assert CSV_HEADERS[:5] == [
              "First Name",
              "Last Name",
              "Email Address",
              "Registration Date",
              "Payment Status",
          ]

      def it_writes_a_header_row_first():
          offering = ClassOfferingFactory(slug="csv-class")
          rows = _read_csv(stream_registrations_csv(offering))
          assert rows[0] == CSV_HEADERS

      def it_writes_one_row_per_registration():
          offering = ClassOfferingFactory()
          RegistrationFactory(class_offering=offering, first_name="Ada", last_name="Lovelace",
                              email="ada@example.com", status=Registration.Status.CONFIRMED, amount_paid_cents=2500)
          rows = _read_csv(stream_registrations_csv(offering))
          assert len(rows) == 2  # header + 1
          data = rows[1]
          assert data[0] == "Ada"
          assert data[1] == "Lovelace"
          assert data[2] == "ada@example.com"
          assert data[4] == "Confirmed"  # human label, not "confirmed"
          assert data[6] == "25.00"      # amount_paid_cents formatted as dollars

      def it_includes_all_statuses_including_cancelled():
          offering = ClassOfferingFactory()
          RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
          RegistrationFactory(class_offering=offering, status=Registration.Status.CANCELLED)
          rows = _read_csv(stream_registrations_csv(offering))
          assert len(rows) == 3  # header + 2

      def it_only_includes_rows_for_this_offering():
          mine = ClassOfferingFactory(slug="mine")
          other = ClassOfferingFactory(slug="other")
          RegistrationFactory(class_offering=mine, email="in@example.com")
          RegistrationFactory(class_offering=other, email="out@example.com")
          body = b"".join(stream_registrations_csv(mine).streaming_content).decode()
          assert "in@example.com" in body
          assert "out@example.com" not in body

      def it_sets_a_csv_attachment_disposition_with_slug_and_date():
          offering = ClassOfferingFactory(slug="pottery-101")
          response = stream_registrations_csv(offering)
          assert response["Content-Type"] == "text/csv"
          disp = response["Content-Disposition"]
          assert "attachment" in disp
          assert "participants-pottery-101-" in disp
          assert disp.endswith('.csv"')
  ```

- [ ] **Step 2: Confirm it fails.** `pytest classes/spec/exports_spec.py` → ImportError / failures (module doesn't exist yet).

- [ ] **Step 3: Implement `classes/exports.py`** mirroring `billing/reports.py:185-246`:
  ```python
  """Per-class registration CSV export.

  Mirrors the streaming-CSV pattern in ``billing/reports.py`` — an ``_Echo``
  file-like object feeds ``csv.writer`` row-by-row into a
  ``StreamingHttpResponse``. The column labels are human-readable (this file is
  for class organizers, not engineers).
  """

  from __future__ import annotations

  import csv
  from typing import TYPE_CHECKING, Iterator

  from django.http import StreamingHttpResponse
  from django.utils import timezone

  if TYPE_CHECKING:
      from classes.models import ClassOffering


  class _Echo:
      """File-like object whose ``write()`` returns the payload (for StreamingHttpResponse)."""

      def write(self, value: str) -> str:
          return value


  CSV_HEADERS = [
      "First Name",
      "Last Name",
      "Email Address",
      "Registration Date",
      "Payment Status",
      "Phone",
      "Amount Paid",
  ]


  def stream_registrations_csv(offering: "ClassOffering") -> StreamingHttpResponse:
      """Stream every registration for one class as a CSV download."""
      pseudo = _Echo()
      writer = csv.writer(pseudo)
      registrations = offering.registrations.order_by("-registered_at")

      def iter_rows() -> Iterator[str]:
          yield writer.writerow(CSV_HEADERS)
          for reg in registrations.iterator(chunk_size=500):
              yield writer.writerow(
                  [
                      reg.first_name,
                      reg.last_name,
                      reg.email,
                      reg.registered_at.date().isoformat(),
                      reg.get_status_display(),
                      reg.phone,
                      f"{reg.amount_paid_cents / 100:.2f}",
                  ]
              )

      response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
      stamp = timezone.now().strftime("%Y%m%d")
      response["Content-Disposition"] = f'attachment; filename="participants-{offering.slug}-{stamp}.csv"'
      return response
  ```
  > `get_status_display()` is Django's auto-generated accessor for the `status` choice field — confirmed via `Registration.Status` at `classes/models.py:906-911`.

- [ ] **Step 4: Confirm it passes.** `pytest classes/spec/exports_spec.py -v` → all green.

- [ ] **Step 5: Lint + commit.** `ruff format . && ruff check --fix . && mypy classes/exports.py classes/spec/exports_spec.py`, then commit ("Add per-class registration CSV exporter").

---

## Task 2: Add the two thin export views + URLs

**Files:** `classes/views.py`, `classes/urls.py`, `classes/spec/views/registration_export_spec.py` (new)

- [ ] **Step 1: Write the failing view spec.** Create `classes/spec/views/registration_export_spec.py` — cover the admin-access gate, the teach own-class success, **the teach foreign-class 404 boundary** (the security-sensitive case), and the response headers:
  ```python
  """BDD specs for the per-class participant CSV export views (admin + teach)."""

  from __future__ import annotations

  import pytest
  from django.urls import reverse

  from classes.factories import ClassOfferingFactory, InstructorFactory, RegistrationFactory, UserFactory
  from classes.models import Registration


  @pytest.fixture
  def instructor_fixture(db):
      user = UserFactory(username="teacher-export@example.com")
      return InstructorFactory(user=user, full_legal_name="Teach Export", instructor_slug="teach-export")


  @pytest.fixture
  def other_instructor(db):
      user = UserFactory(username="other-export@example.com")
      return InstructorFactory(user=user, full_legal_name="Other Export", instructor_slug="other-export")


  def _csv_body(response) -> str:
      return b"".join(response.streaming_content).decode()


  def describe_admin_class_export():
      def it_requires_admin_access(member_user, client):
          offering = ClassOfferingFactory()
          client.force_login(member_user)
          resp = client.get(reverse("classes:admin_class_export", kwargs={"pk": offering.pk}))
          assert resp.status_code == 403

      def it_streams_a_csv_for_any_class(admin_user, client):
          offering = ClassOfferingFactory(slug="admin-export")
          RegistrationFactory(class_offering=offering, first_name="Grace", email="grace@example.com")
          client.force_login(admin_user)
          resp = client.get(reverse("classes:admin_class_export", kwargs={"pk": offering.pk}))
          assert resp.status_code == 200
          assert resp["Content-Type"] == "text/csv"
          assert "attachment" in resp["Content-Disposition"]
          body = _csv_body(resp)
          assert "First Name" in body
          assert "grace@example.com" in body


  def describe_teach_class_export():
      def it_streams_a_csv_for_my_own_class(instructor_fixture, client):
          mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-export")
          RegistrationFactory(class_offering=mine, email="mine@example.com")
          client.force_login(instructor_fixture.user)
          resp = client.get(reverse("classes:teach_class_export", kwargs={"pk": mine.pk}))
          assert resp.status_code == 200
          assert resp["Content-Type"] == "text/csv"
          assert "mine@example.com" in _csv_body(resp)

      def it_404s_exporting_another_instructors_class(instructor_fixture, other_instructor, client):
          theirs = ClassOfferingFactory(instructor=other_instructor, slug="theirs-export")
          RegistrationFactory(class_offering=theirs, email="secret@example.com")
          client.force_login(instructor_fixture.user)
          resp = client.get(reverse("classes:teach_class_export", kwargs={"pk": theirs.pk}))
          assert resp.status_code == 404  # _teach_class_or_404 scopes to the logged-in instructor

      def it_blocks_anonymous_users(db, client):
          offering = ClassOfferingFactory()
          resp = client.get(reverse("classes:teach_class_export", kwargs={"pk": offering.pk}))
          assert resp.status_code == 302  # login redirect
  ```
  > The `it_404s_exporting_another_instructors_class` test is the **authorization-boundary test** for spec item #3: it builds a class owned by `other_instructor`, logs in as `instructor_fixture`, and asserts the export 404s rather than leaking `secret@example.com`. This mirrors the existing tab-boundary test at `teach_class_workspace_spec.py:51-66`.

- [ ] **Step 2: Confirm it fails.** `pytest classes/spec/views/registration_export_spec.py` → `NoReverseMatch` (URLs/views don't exist yet).

- [ ] **Step 3: Add `StreamingHttpResponse` to the import** at `classes/views.py:17`:
  ```python
  from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse, StreamingHttpResponse
  ```

- [ ] **Step 4: Add the admin view** directly after `admin_class_registrations` (`classes/views.py:~1555`):
  ```python
  @classes_admin_access_required
  def admin_class_export(request: HttpRequest, pk: int) -> StreamingHttpResponse:
      """Download a CSV of every registration for one class (admin — any class)."""
      from classes.exports import stream_registrations_csv

      offering = get_object_or_404(ClassOffering, pk=pk)
      return stream_registrations_csv(offering)
  ```

- [ ] **Step 5: Add the teach view** directly after `teach_class_registrations` (`classes/views.py:~1095`):
  ```python
  @teaching_member_required
  def teach_class_export(request: HttpRequest, pk: int) -> StreamingHttpResponse:
      """Download a CSV of every registration for one of the teaching member's own classes."""
      from classes.exports import stream_registrations_csv

      offering = _teach_class_or_404(request, pk)  # scopes to instructor=request.teaching_member → 404 otherwise
      return stream_registrations_csv(offering)
  ```
  > Use the lazy local import of `stream_registrations_csv` inside each view (matching the file's existing pattern of local imports for forms/services, e.g. `classes/views.py:1108`, `:1597`) to keep module import order clean. `StreamingHttpResponse` is a subtype of `HttpResponse`, so the decorators (typed `Callable[..., HttpResponse]`, `classes/views.py:61`) accept it.

- [ ] **Step 6: Wire the URLs** in `classes/urls.py`:
  - After `:66` (admin registrations):
    ```python
    path("admin/<int:pk>/registrations/export/", views.admin_class_export, name="admin_class_export"),
    ```
  - After `:27` (teach registrations, inside the existing `path(...)` block group):
    ```python
    path("teach/classes/<int:pk>/registrations/export/", views.teach_class_export, name="teach_class_export"),
    ```

- [ ] **Step 7: Confirm it passes.** `pytest classes/spec/views/registration_export_spec.py -v` → all green, including the 404 boundary test.

- [ ] **Step 8: Lint + commit.** `ruff format . && ruff check --fix . && mypy classes/views.py`, then commit ("Add per-class CSV export views + URLs for admin and teach").

---

## Task 3: Add the "Export Data" button to both registration templates

**Files:** `templates/classes/admin/class_registrations.html`, `templates/classes/teach/class_registrations.html`

The button is a GET download link styled like the neighboring ghost button. It must live **outside** the email POST `<form>` (a download link nested in a POST form is wrong and Alpine-scoped). Render it whether or not there are registrations (an empty class can still export a header-only file), so place it in the outer `x-data` div, before the `{% if registrations %}`.

- [ ] **Step 1: Admin template** (`templates/classes/admin/class_registrations.html`). Add a button row at the top of the `x-data` div (after `:4`, before `:5` `{% if registrations %}`):
  ```html
  <div style="margin-bottom:0.75rem; text-align:right;">
      <a href="{% url 'classes:admin_class_export' pk=offering.pk %}" class="hub-btn hub-btn--sm hub-btn--ghost">Export Data</a>
  </div>
  ```

- [ ] **Step 2: Teach template** (`templates/classes/teach/class_registrations.html`). Add the identical row with the teach URL name:
  ```html
  <div style="margin-bottom:0.75rem; text-align:right;">
      <a href="{% url 'classes:teach_class_export' pk=offering.pk %}" class="hub-btn hub-btn--sm hub-btn--ghost">Export Data</a>
  </div>
  ```

- [ ] **Step 3: Add render assertions** to `classes/spec/views/registration_export_spec.py` (the button must appear on the registrations tab so users can find the download):
  ```python
  def describe_export_button_on_registrations_tab():
      def it_shows_export_link_on_admin_registrations(admin_user, client):
          offering = ClassOfferingFactory()
          client.force_login(admin_user)
          resp = client.get(reverse("classes:admin_class_registrations", kwargs={"pk": offering.pk}))
          assert resp.status_code == 200
          assert reverse("classes:admin_class_export", kwargs={"pk": offering.pk}).encode() in resp.content
          assert b"Export Data" in resp.content

      def it_shows_export_link_on_teach_registrations(instructor_fixture, client):
          mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-btn")
          client.force_login(instructor_fixture.user)
          resp = client.get(reverse("classes:teach_class_registrations", kwargs={"pk": mine.pk}))
          assert resp.status_code == 200
          assert reverse("classes:teach_class_export", kwargs={"pk": mine.pk}).encode() in resp.content
          assert b"Export Data" in resp.content
  ```

- [ ] **Step 4: Confirm pass.** `pytest classes/spec/views/registration_export_spec.py -v` → green.

- [ ] **Step 5: Commit.** ("Add Export Data button to admin + teach registration tabs"). (ruff/mypy don't lint templates; the spec file additions are covered by the earlier ruff run — re-run `ruff check .` to be safe.)

---

## Task 4: Version bump + changelog

**Files:** `plfog/version.py`

- [ ] **Step 1: Verify the latest version first.** At time of writing, `plfog/version.py:5` is `VERSION = "2.5.8"` and PR #108 (`Release 2.5.8`) is **OPEN/in flight** (confirmed via `gh pr list`). The next patch is therefore `2.5.9`. **Re-confirm with `gh pr list --state all --limit 5` and the file before committing — use the next unused patch, do not assume 2.5.9 if a later one has merged.**

- [ ] **Step 2: Bump `VERSION`** to `2.5.9` (or the verified next patch).

- [ ] **Step 3: Prepend a member-friendly `CHANGELOG` entry** (plain language — this posts to Discord; no jargon, no PR numbers):
  ```python
  {
      "version": "2.5.9",  # verify
      "date": "2026-06-18",  # set to the merge date
      "title": "Download your class participant list",
      "changes": [
          "You can now download a spreadsheet (CSV) of everyone signed up for a class. Look for the 'Export Data' button on a class's Registrations page. Instructors can export their own classes; admins can export any class.",
      ],
  }
  ```

- [ ] **Step 4: Commit.** ("Bump version to 2.5.9 + changelog for participant CSV export").

---

## Final verification

- [ ] `pytest` — full suite passes, **100% coverage** (the new `classes/exports.py`, both views, and both spec files are small and fully exercised by Tasks 1-3).
- [ ] `ruff format . && ruff check .` — clean.
- [ ] `mypy .` — clean (export `DATABASE_URL` first if running before push: `export $(grep '^DATABASE_URL=' .env | xargs)`).
- [ ] **RBAC sanity (no rebuild needed — confirm only):** the only registration-mutating endpoint, `admin_registration_cancel` (`classes/views.py:2000-2006`), is still `@classes_admin_access_required`; teach gained *export* only, not cancel/refund. Grep `teach_` view functions to confirm none mutate registrations.
- [ ] **Manual smoke (run skill):** as an admin, open a class's Registrations tab → click "Export Data" → a `participants-<slug>-<date>.csv` downloads with the 7 headers and one row per registrant. As an instructor, do the same on an owned class; then hand-edit the URL to another instructor's class `pk` and confirm a 404 (the boundary holds in the browser, not just in tests).

---

## Follow-up (out of scope for this plan)

- **Aggregate "all registrations" export.** There's an admin-wide `admin_registrations` list (`classes/views.py` / `classes/urls.py:89`). A site-wide export across all classes could reuse `stream_registrations_csv`'s row logic, but it's a separate view + a separate filtering story (date ranges, status filters like `billing/reports.py`'s `ReportFilterForm`). Not requested here.
- **Custom-question answers in the CSV.** Registrations carry `custom_answers` (prefetched in both registration views). Including per-question columns is a richer export that needs a dynamic header; defer until asked.
- **Excel (.xlsx) format.** CSV satisfies the spec ("clean server-side CSV"). If organizers later want native Excel, that's a new dependency (`openpyxl`) and a separate plan.
