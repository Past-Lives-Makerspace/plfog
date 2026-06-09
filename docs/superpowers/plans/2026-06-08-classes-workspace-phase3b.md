# Classes Management Redesign — Phase 3b: Instructor Per-Class Workspace + Profile to avatar

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give instructors the same per-class Workspace admins got in Phase 3 — **Overview · Registrations · Waitlist · Discount Codes** for one of *their* classes — and move the instructor Profile link into the hub avatar/user menu.

**Architecture:** Mirror the admin Phase 3 implementation (already merged) for the instructor portal. A new `instructor/class_detail_base.html` sub-tab base (parallel to `admin/class_detail_base.html`) with instructor routes; new `instructor_class_detail/registrations/waitlist/discount_codes` views scoped to `request.instructor`'s own classes (404 otherwise); reuse the existing instructor edit form + email form + discount-code create. The My Classes list links each class to its Workspace. Instructor restrictions carry over (edit only draft/pending; no approve/delete/archive; Submit-for-review on drafts).

**Tech Stack:** Django function-based views, pytest-describe BDD specs, factory-boy. Reference implementation: the admin Phase 3 files (`admin_class_registrations/waitlist/discount_codes`, `_class_workspace_counts`, `class_detail_base.html`, `class_waitlist.html`).

**Design decisions:**
- Tabs: **Overview · Registrations · Waitlist · Discount Codes** (same 4 as admin; Email inline on Registrations; Edit/Submit/Preview as Overview actions).
- Scope every query to the instructor's own class — `get_object_or_404(ClassOffering.objects.filter(instructor=request.instructor), pk=pk)`.
- Overview actions respect instructor permissions: **Edit** only when draft/pending; **Submit for review** only when draft; **Preview** always; a note "Published/archived classes are edited by an admin" when locked.
- **Profile** moves to the hub avatar menu (it was a Phase-2 Overview "Quick link"); remove it from the instructor Overview quick links.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `classes/views.py` | Modify | Add `_instructor_class_or_404` helper + `instructor_class_detail/registrations/waitlist/discount_codes` (+ a per-class `instructor_class_email`) views, reusing `_class_workspace_counts` |
| `classes/urls.py` | Modify | Add `instructor/classes/<pk>/...` tab routes |
| `templates/classes/instructor/class_detail_base.html` | Create | Sub-tab base (mirror admin) with instructor routes |
| `templates/classes/instructor/class_overview.html` | Create | Overview tab: summary + instructor actions |
| `templates/classes/instructor/class_registrations.html` | Create | Registrations tab (students + inline email), mirror admin |
| `templates/classes/instructor/class_waitlist.html` | Create | Waitlist tab, mirror admin |
| `templates/classes/instructor/class_discount_codes.html` | Create | Class-scoped codes + "+ New code" |
| `templates/classes/instructor/classes_list.html` | Modify | Link each class title to its Workspace Overview |
| `templates/classes/instructor/overview.html` | Modify | Drop the "Edit profile" Quick link (moved to avatar menu) |
| `templates/hub/base.html` | Modify | Add "Edit teaching profile" to the avatar/user menu for members |
| `classes/spec/views/instructor_class_workspace_spec.py` | Create | BDD specs |
| `plfog/version.py` | Modify | Append a changelog line to 2.6.0 |

---

## Task 1: Instructor per-class Workspace (views + URLs + templates)

**Reference:** read the admin equivalents first — `admin_class_registrations/waitlist/discount_codes` + `_class_workspace_counts` in `classes/views.py`, and `templates/classes/admin/class_detail_base.html`, `class_waitlist.html`, `class_registrations.html`, `class_discount_codes.html`. Build instructor parallels.

**Files:** as in the File Structure table (the instructor templates + views + URLs + spec).

- [ ] **Step 1: Write the failing specs**

Create `classes/spec/views/instructor_class_workspace_spec.py`:

```python
"""BDD specs for the instructor per-class Workspace tabs."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import (
    ClassOfferingFactory,
    DiscountCodeFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering, Registration


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher T", instructor_slug="teacher-t")


@pytest.fixture
def other_instructor(db):
    user = UserFactory(username="other@example.com")
    return InstructorFactory(user=user, full_legal_name="Other", instructor_slug="other")


def describe_instructor_class_workspace_scope():
    def it_404s_for_another_instructors_class(instructor_fixture, other_instructor, client):
        theirs = ClassOfferingFactory(instructor=other_instructor, slug="theirs-ws")
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:instructor_class_detail", kwargs={"pk": theirs.pk}))
        assert resp.status_code == 404

    def it_blocks_non_members(db, client):
        offering = ClassOfferingFactory()
        resp = client.get(reverse("classes:instructor_class_detail", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302  # login redirect


def describe_instructor_overview_tab():
    def it_shows_summary_and_subnav(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-ws", status=ClassOffering.Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:instructor_class_detail", kwargs={"pk": mine.pk}))
        assert resp.status_code == 200
        assert reverse("classes:instructor_class_registrations", kwargs={"pk": mine.pk}).encode() in resp.content
        # Draft → Edit action available
        assert reverse("classes:instructor_class_edit", kwargs={"pk": mine.pk}).encode() in resp.content

    def it_hides_edit_for_published_classes(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-pub", status=ClassOffering.Status.PUBLISHED)
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:instructor_class_detail", kwargs={"pk": mine.pk}))
        assert reverse("classes:instructor_class_edit", kwargs={"pk": mine.pk}).encode() not in resp.content


def describe_instructor_registrations_tab():
    def it_shows_my_classs_registrant(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-regs")
        RegistrationFactory(class_offering=mine, first_name="Jess", last_name="Park")
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:instructor_class_registrations", kwargs={"pk": mine.pk}))
        assert resp.status_code == 200
        assert b"Jess" in resp.content


def describe_instructor_waitlist_tab():
    def it_lists_waitlisted(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-wait")
        RegistrationFactory(class_offering=mine, first_name="Wait", last_name="Lister", status=Registration.Status.WAITLISTED)
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:instructor_class_waitlist", kwargs={"pk": mine.pk}))
        assert resp.status_code == 200
        assert b"Wait" in resp.content


def describe_instructor_discount_codes_tab():
    def it_shows_a_class_scoped_code(instructor_fixture, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine-codes")
        DiscountCodeFactory(code="MINE10", class_offering=mine)
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:instructor_class_discount_codes", kwargs={"pk": mine.pk}))
        assert resp.status_code == 200
        assert b"MINE10" in resp.content
```

- [ ] **Step 2: Run → confirm FAIL** (`NoReverseMatch`).

Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest classes/spec/views/instructor_class_workspace_spec.py -q --no-cov`

- [ ] **Step 3: Add the helper + views**

In `classes/views.py`, add a scope helper and the four tab views (reuse the existing `_class_workspace_counts`). Model them on the admin versions but scope to the instructor's own class:

```python
def _instructor_class_or_404(request: HttpRequest, pk: int) -> ClassOffering:
    instructor: Member = request.instructor  # type: ignore[attr-defined]
    return get_object_or_404(ClassOffering.objects.filter(instructor=instructor), pk=pk)


@instructor_required
def instructor_class_detail(request: HttpRequest, pk: int) -> HttpResponse:
    offering = _instructor_class_or_404(request, pk)
    return render(
        request,
        "classes/instructor/class_overview.html",
        {
            "active_tab": "classes",
            "active_subtab": "overview",
            "instructor": request.instructor,  # type: ignore[attr-defined]
            "offering": offering,
            **_class_workspace_counts(offering),
        },
    )


@instructor_required
def instructor_class_registrations(request: HttpRequest, pk: int) -> HttpResponse:
    offering = _instructor_class_or_404(request, pk)
    registrations = (
        offering.registrations.select_related("member")
        .prefetch_related("custom_answers__question")
        .order_by("-registered_at")
    )
    return render(
        request,
        "classes/instructor/class_registrations.html",
        {
            "active_tab": "classes",
            "active_subtab": "registrations",
            "instructor": request.instructor,  # type: ignore[attr-defined]
            "offering": offering,
            "registrations": registrations,
            **_class_workspace_counts(offering),
        },
    )


@instructor_required
def instructor_class_waitlist(request: HttpRequest, pk: int) -> HttpResponse:
    offering = _instructor_class_or_404(request, pk)
    waitlist_registrations = list(
        offering.registrations.filter(status=Registration.Status.WAITLISTED).order_by("registered_at")
    )
    return render(
        request,
        "classes/instructor/class_waitlist.html",
        {
            "active_tab": "classes",
            "active_subtab": "waitlist",
            "instructor": request.instructor,  # type: ignore[attr-defined]
            "offering": offering,
            "waitlist_registrations": waitlist_registrations,
            **_class_workspace_counts(offering),
        },
    )


@instructor_required
def instructor_class_discount_codes(request: HttpRequest, pk: int) -> HttpResponse:
    offering = _instructor_class_or_404(request, pk)
    codes = DiscountCode.objects.filter(
        Q(class_offering=offering) | Q(class_offering__isnull=True)
    ).order_by("code")
    return render(
        request,
        "classes/instructor/class_discount_codes.html",
        {
            "active_tab": "classes",
            "active_subtab": "discount_codes",
            "instructor": request.instructor,  # type: ignore[attr-defined]
            "offering": offering,
            "codes": codes,
            **_class_workspace_counts(offering),
        },
    )
```

For the **inline email on the Registrations tab**: reuse `instructor_registrations_email` (it validates `registration_ids` against the instructor's classes). After sending, it currently redirects to `instructor_registrations` (the grouped page). For the workspace, add a thin per-class email view OR pass a `?next=` — the SIMPLEST: add `instructor_class_email(request, pk)` mirroring `admin_class_email` (POST-only, uses `InstructorEmailForm`, redirects to `instructor_class_registrations`). Read `admin_class_email` + `instructor_registrations_email` and mirror. The Registrations template posts to `instructor_class_email`.

- [ ] **Step 4: Add URLs**

In `classes/urls.py`, in the instructor block:

```python
    path("instructor/classes/<int:pk>/", views.instructor_class_detail, name="instructor_class_detail"),
    path("instructor/classes/<int:pk>/registrations/", views.instructor_class_registrations, name="instructor_class_registrations"),
    path("instructor/classes/<int:pk>/registrations/email/", views.instructor_class_email, name="instructor_class_email"),
    path("instructor/classes/<int:pk>/waitlist/", views.instructor_class_waitlist, name="instructor_class_waitlist"),
    path("instructor/classes/<int:pk>/discount-codes/", views.instructor_class_discount_codes, name="instructor_class_discount_codes"),
```

NOTE ordering: `instructor/classes/new/` and `instructor/classes/<pk>/edit|submit/` already exist; `<int:pk>` won't match `new`, and the literal sub-segments don't collide. Place these so `instructor/classes/new/` stays before `instructor/classes/<int:pk>/`.

- [ ] **Step 5: Create the instructor sub-tab base** `templates/classes/instructor/class_detail_base.html`

Mirror `admin/class_detail_base.html` but: extend `classes/instructor/base.html`; the nav links to the `instructor_class_*` routes; tabs Overview · Registrations ({{ active_registration_count }}) · Waitlist ({{ waitlist_count }}) · Discount Codes; keep the header (title, status, Preview ↗ via `class_preview`).

- [ ] **Step 6: Create the four tab templates** — mirror the admin ones, extending `classes/instructor/class_detail_base.html`:
  - `class_overview.html`: details + sessions summary + **instructor actions** — Edit (if `offering.status == 'draft' or 'pending'`), Submit for review (if draft, POST to `instructor_class_submit`), Preview; show a muted note when published/archived ("Published and archived classes are edited by an admin.").
  - `class_registrations.html`: mirror admin's (students table + inline email), but post the email form to `{% url 'classes:instructor_class_email' pk=offering.pk %}`.
  - `class_waitlist.html`: copy admin's `class_waitlist.html` content (it's chrome-agnostic; just extends the instructor base).
  - `class_discount_codes.html`: mirror admin's, but the "+ New code" links to `{% url 'classes:instructor_discount_code_create' %}?class={{ offering.pk }}`, and DROP the admin-only Approve/Unapprove action (instructors can't approve). Keep code/discount/scope/active columns + Edit/Delete via `instructor_discount_code_edit`/`instructor_discount_code_delete`.

- [ ] **Step 7: Link the My Classes list to the Workspace**

In `templates/classes/instructor/classes_list.html`, wrap each class title (line ~21) in a link to its Workspace:

```django
            <td style="padding:0.5rem 0.75rem;"><a href="{% url 'classes:instructor_class_detail' pk=offering.pk %}">{{ offering.title }}</a></td>
```

- [ ] **Step 8: Run specs → PASS**, then run the existing instructor specs to confirm no regression:

Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest classes/spec/views/instructor_class_workspace_spec.py classes/spec/views/instructor_dashboard_spec.py classes/spec/views/instructor_overview_spec.py -q --no-cov`

- [ ] **Step 9: Commit**

```bash
git add classes/views.py classes/urls.py templates/classes/instructor/ classes/spec/views/instructor_class_workspace_spec.py
git commit -m "[classes] Add instructor per-class Workspace (Overview/Registrations/Waitlist/Discount Codes)"
```

---

## Task 2: Move Profile to the avatar menu

**Files:**
- Modify: `templates/hub/base.html` (avatar/user menu)
- Modify: `templates/classes/instructor/overview.html` (drop the Profile Quick link)
- Test: `classes/spec/views/instructor_overview_spec.py` (Quick-links spec) + a small hub spec if one exists

- [ ] **Step 1: Find the avatar/user menu in `templates/hub/base.html`**

Read `templates/hub/base.html` and locate the topbar user menu (the avatar dropdown, near the "Log out" / "Settings" links). Identify the existing condition used to detect a teaching member (look for `request.view_as.is_member`, `persona`, or `instructor_slug` usage already in the file).

- [ ] **Step 2: Add an "Edit teaching profile" link**

In the user menu, add a link to `{% url 'classes:instructor_profile' %}` shown when the user is a teaching member (mirror whatever condition the public/list.html CTA uses to show "Manage My Classes" — `request.view_as.is_member`). Keep it consistent with the existing menu items' markup.

- [ ] **Step 3: Drop the Profile Quick link from the instructor Overview**

In `templates/classes/instructor/overview.html`, remove the `Edit profile` link from the "Quick links" row (Registrations + Discount codes stay). Update the `it_links_to_registrations_codes_and_profile` spec in `classes/spec/views/instructor_overview_spec.py`: rename to `it_links_to_registrations_and_codes`, and assert `instructor_registrations` + `instructor_discount_codes` are present but no longer require `instructor_profile` on the Overview (it's in the avatar menu now). Add a small assertion that the Overview still 200s.

- [ ] **Step 4: Run the affected specs → PASS**

Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest classes/spec/views/instructor_overview_spec.py -q --no-cov`

- [ ] **Step 5: Commit**

```bash
git add templates/hub/base.html templates/classes/instructor/overview.html classes/spec/views/instructor_overview_spec.py
git commit -m "[classes] Move instructor Profile to the avatar menu"
```

---

## Task 3: Verify + changelog

- [ ] **Step 1: Full suite**

Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest -q`
Expected: PASS, coverage ≥ 98%. Add specs for any uncovered branch (the instructor discount-codes empty state; the per-class email redirect to `instructor_class_registrations`; the published-class "edit hidden" path).

- [ ] **Step 2: Lint + type-check**

Run: `/home/josh/Code/plfog/.venv/bin/python -m ruff format . && /home/josh/Code/plfog/.venv/bin/python -m ruff check .`
Then: `DATABASE_URL="sqlite://:memory:" /home/josh/Code/plfog/.venv/bin/python -m mypy .` — must stay `Success: no issues found` (add targeted `# type: ignore[...]` for any django-stubs annotation/`request.instructor` false positives in the new views, matching the existing pattern).

- [ ] **Step 3: Changelog line (2.6.0)**

In `plfog/version.py`, append to the 2.6.0 `"changes"`:

```python
            "Instructors get that same per-class workspace for their own classes — open any class you teach to see its sign-ups, waitlist, and discount codes in one place. Your teaching profile now lives in the top-right account menu.",
```

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "[classes] Phase 3b verify + changelog"
```

---

## Self-Review notes

- **Scope:** every instructor workspace query goes through `_instructor_class_or_404` (filters `instructor=request.instructor`) → a 404 for someone else's class. The spec proves it.
- **Permissions:** Overview shows Edit/Submit only for draft/pending; no approve/archive/delete (those are admin-only and absent from the instructor templates).
- **Reuse:** counts via the shared `_class_workspace_counts`; waitlist template is a near-copy of the admin one; email reuses `InstructorEmailForm`.
- **mypy:** new views use `request.instructor` (dynamic attr) and an annotated waitlist count if added — carry the same `# type: ignore[...]` pattern so `mypy` stays clean.
