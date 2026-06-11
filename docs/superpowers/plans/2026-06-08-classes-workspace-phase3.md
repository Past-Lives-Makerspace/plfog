# Classes Management Redesign — Phase 3: Per-Class Workspace (Admin)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn the admin per-class detail page into a tabbed **Workspace** — **Overview · Registrations · Waitlist · Discount Codes** — by *finishing* the half-built `class_detail_base.html` sub-tab foundation. Email stays inline on Registrations; Edit/Preview/Duplicate/Archive are header actions.

**Architecture:** A sub-tab base template (`classes/admin/class_detail_base.html`) already exists with a `vote-tab` sub-nav and `{% block detail_content %}`. Two orphaned templates (`class_registrations.html`, `class_discount_codes.html`) already extend it but were never routed. Phase 3 adds the missing tab views/URLs (`admin_class_registrations`, `admin_class_waitlist`, `admin_class_discount_codes`), adds a Waitlist tab + template, and refactors the existing detail page (`class_detail.html`) to extend the base as the **Overview** tab (summary + actions; the inline students table and waitlist move to their dedicated tabs). Everything reuses existing templates/views/forms; no model changes.

**Tech Stack:** Django function-based views, pytest-describe BDD specs, factory-boy. The decision per the user: 4 tabs, Email inline, Edit as a button.

**Design decisions (confirmed with user):**
- Tabs: **Overview · Registrations · Waitlist · Discount Codes** (NOT Edit-as-tab, NOT Email-as-tab).
- Email composer stays inline on the Registrations tab (existing Alpine form, reused as-is).
- Edit + Preview + Duplicate + Archive + Delete are header/Overview actions.
- The Overview tab = class summary (details, sessions) + the action toolbar. The inline students table and inline waitlist move OUT of Overview onto the Registrations and Waitlist tabs (no duplication).

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `classes/views.py` | Modify | Add `_class_workspace_counts` helper + `admin_class_registrations` / `admin_class_waitlist` / `admin_class_discount_codes` views; update `admin_class_detail` to pass `active_subtab` + counts |
| `classes/urls.py` | Modify | Add the three per-class tab routes |
| `templates/classes/admin/class_detail_base.html` | Modify | Add the Waitlist tab to the sub-nav |
| `templates/classes/admin/class_waitlist.html` | Create | Waitlist tab content |
| `templates/classes/admin/class_discount_codes.html` | Modify | Fix the "+ New Code" button to a real route |
| `templates/classes/admin/class_detail.html` | Modify | Refactor into the Overview tab (extend base; drop inline students + waitlist; keep summary + actions) |
| `classes/spec/views/admin_class_workspace_spec.py` | Create | BDD specs for the tabs |
| `plfog/version.py` | Modify | Append a line to the 2.6.0 changelog |

---

## Task 1: Wire the Registrations / Waitlist / Discount-Codes tab views

**Files:**
- Modify: `classes/views.py` (helper + 3 views)
- Modify: `classes/urls.py`
- Modify: `templates/classes/admin/class_detail_base.html` (add Waitlist tab)
- Create: `templates/classes/admin/class_waitlist.html`
- Modify: `templates/classes/admin/class_discount_codes.html` (fix "+ New Code")
- Test: `classes/spec/views/admin_class_workspace_spec.py`

- [ ] **Step 1: Write the failing specs**

Create `classes/spec/views/admin_class_workspace_spec.py`:

```python
"""BDD specs for the per-class admin Workspace tabs."""

from __future__ import annotations

from django.urls import reverse

from classes.factories import ClassOfferingFactory, DiscountCodeFactory, RegistrationFactory
from classes.models import Registration


def describe_class_registrations_tab():
    def it_gates_behind_admin(member_user, client, db):
        offering = ClassOfferingFactory()
        client.force_login(member_user)
        resp = client.get(reverse("classes:admin_class_registrations", kwargs={"pk": offering.pk}))
        assert resp.status_code == 403

    def it_shows_a_registrant(admin_user, client, db):
        offering = ClassOfferingFactory()
        RegistrationFactory(class_offering=offering, first_name="Jess", last_name="Park")
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_registrations", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        assert b"Jess" in resp.content

    def it_shows_the_subtab_nav(admin_user, client, db):
        offering = ClassOfferingFactory()
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_registrations", kwargs={"pk": offering.pk}))
        assert reverse("classes:admin_class_waitlist", kwargs={"pk": offering.pk}).encode() in resp.content
        assert reverse("classes:admin_class_discount_codes", kwargs={"pk": offering.pk}).encode() in resp.content


def describe_class_waitlist_tab():
    def it_lists_waitlisted_registrants(admin_user, client, db):
        offering = ClassOfferingFactory()
        RegistrationFactory(class_offering=offering, first_name="Wait", last_name="Lister", status=Registration.Status.WAITLISTED)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_waitlist", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        assert b"Wait" in resp.content

    def it_shows_the_waitlist_count_in_the_nav(admin_user, client, db):
        offering = ClassOfferingFactory()
        RegistrationFactory(class_offering=offering, status=Registration.Status.WAITLISTED)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_waitlist", kwargs={"pk": offering.pk}))
        assert b"Waitlist (1)" in resp.content

    def it_empty_states_when_no_waitlist(admin_user, client, db):
        offering = ClassOfferingFactory()
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_waitlist", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        assert b"Waitlist (0)" in resp.content


def describe_class_discount_codes_tab():
    def it_shows_a_class_scoped_code(admin_user, client, db):
        offering = ClassOfferingFactory()
        DiscountCodeFactory(code="CLASS10", class_offering=offering)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_discount_codes", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        assert b"CLASS10" in resp.content

    def it_also_shows_global_codes(admin_user, client, db):
        offering = ClassOfferingFactory()
        DiscountCodeFactory(code="GLOBAL5", class_offering=None)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_discount_codes", kwargs={"pk": offering.pk}))
        assert b"GLOBAL5" in resp.content
```

- [ ] **Step 2: Run specs → confirm FAIL**

Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest classes/spec/views/admin_class_workspace_spec.py -q --no-cov`
Expected: `NoReverseMatch` for the new names.

- [ ] **Step 3: Add the shared counts helper + three views**

In `classes/views.py`, add this helper above `admin_class_detail` (and confirm `Q`, `DiscountCode`, `Registration`, `ClassOffering`, `get_object_or_404`, `render` are imported — they are):

```python
def _class_workspace_counts(offering: ClassOffering) -> dict[str, int]:
    """Sub-tab badge counts shared by every per-class Workspace tab."""
    regs = offering.registrations
    return {
        "active_registration_count": regs.exclude(
            status__in=[Registration.Status.CANCELLED, Registration.Status.REFUNDED]
        ).count(),
        "waitlist_count": regs.filter(status=Registration.Status.WAITLISTED).count(),
    }
```

Then add the three tab views (place them just after `admin_class_detail`):

```python
@classes_admin_access_required
def admin_class_registrations(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(ClassOffering, pk=pk)
    registrations = (
        offering.registrations.select_related("member")
        .prefetch_related("custom_answers__question")
        .order_by("-registered_at")
    )
    return render(
        request,
        "classes/admin/class_registrations.html",
        {
            "active_tab": "classes",
            "active_subtab": "registrations",
            "offering": offering,
            "registrations": registrations,
            **_class_workspace_counts(offering),
        },
    )


@classes_admin_access_required
def admin_class_waitlist(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(ClassOffering, pk=pk)
    waitlist_registrations = list(
        offering.registrations.filter(status=Registration.Status.WAITLISTED).order_by("registered_at")
    )
    return render(
        request,
        "classes/admin/class_waitlist.html",
        {
            "active_tab": "classes",
            "active_subtab": "waitlist",
            "offering": offering,
            "waitlist_registrations": waitlist_registrations,
            **_class_workspace_counts(offering),
        },
    )


@classes_admin_access_required
def admin_class_discount_codes(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(ClassOffering, pk=pk)
    codes = DiscountCode.objects.filter(
        Q(class_offering=offering) | Q(class_offering__isnull=True)
    ).order_by("code")
    return render(
        request,
        "classes/admin/class_discount_codes.html",
        {
            "active_tab": "classes",
            "active_subtab": "discount_codes",
            "offering": offering,
            "codes": codes,
            **_class_workspace_counts(offering),
        },
    )
```

- [ ] **Step 4: Add the URLs**

In `classes/urls.py`, add after the existing `admin/<int:pk>/...` routes (the literal `registrations`/`waitlist`/`discount-codes` segments can't be shadowed by `admin/<int:pk>/`):

```python
    path("admin/<int:pk>/registrations/", views.admin_class_registrations, name="admin_class_registrations"),
    path("admin/<int:pk>/waitlist/", views.admin_class_waitlist, name="admin_class_waitlist"),
    path("admin/<int:pk>/discount-codes/", views.admin_class_discount_codes, name="admin_class_discount_codes"),
```

- [ ] **Step 5: Add the Waitlist tab to the sub-nav**

In `templates/classes/admin/class_detail_base.html`, the `<nav>` currently has Overview / Registrations / Discount Codes. Add a Waitlist tab between Registrations and Discount Codes:

```django
    <a href="{% url 'classes:admin_class_waitlist' pk=offering.pk %}" class="vote-tab{% if active_subtab == 'waitlist' %} vote-tab--active{% endif %}">Waitlist ({{ waitlist_count }})</a>
```

(The existing Registrations tab shows `({{ active_registration_count }})` — leave it. Both counts now come from `_class_workspace_counts`.)

- [ ] **Step 6: Create the Waitlist tab template**

Create `templates/classes/admin/class_waitlist.html`:

```django
{% extends "classes/admin/class_detail_base.html" %}
{% block detail_content %}
{% if waitlist_registrations %}
<div class="admin-table-wrap">
<table>
    <thead>
        <tr><th style="width:3rem;">#</th><th>Name</th><th>Email</th><th>Joined</th><th>Notified</th></tr>
    </thead>
    <tbody>
    {% for reg in waitlist_registrations %}
        <tr>
            <td>{{ forloop.counter }}</td>
            <td>{{ reg.first_name }} {{ reg.last_name }}</td>
            <td>{{ reg.email }}</td>
            <td>{{ reg.registered_at|date:"M j, Y g:i A" }}</td>
            <td>
                {% if reg.waitlist_notified_at %}
                    <span style="color:var(--color-tuscan-yellow);">{{ reg.waitlist_notified_at|date:"M j, g:i A" }}</span>
                {% else %}
                    <span style="color:var(--hub-text-muted);">Not yet</span>
                {% endif %}
            </td>
        </tr>
    {% endfor %}
    </tbody>
</table>
</div>
{% else %}
<div style="padding:1.5rem 0; text-align:center; color:var(--hub-text-muted);">No one is on the waitlist.</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 7: Fix the "+ New Code" button**

`templates/classes/admin/class_discount_codes.html` line ~5 links to `classes:admin_class_discount_code_create`, which does not exist. Inspect the existing `admin_discount_code_create` view in `classes/views.py`: if it already honors a `?class=<pk>` query param (scoping the new code to that class), change the button to:

```django
    <a class="hub-btn hub-btn--primary hub-btn--sm" href="{% url 'classes:admin_discount_code_create' %}?class={{ offering.pk }}">+ New Code</a>
```

If `admin_discount_code_create` does NOT honor `?class=`, instead add a thin view `admin_class_discount_code_create(request, pk)` that reuses the same form/logic pre-scoped to the offering and redirects back to `classes:admin_class_discount_codes` on success, add its URL (`admin/<int:pk>/discount-codes/new/`), and point the button at it. Pick whichever is the smaller, cleaner change after reading the existing create view; note which you chose.

- [ ] **Step 8: Run specs → confirm PASS**

Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest classes/spec/views/admin_class_workspace_spec.py -q --no-cov`

- [ ] **Step 9: Commit**

```bash
git add classes/views.py classes/urls.py templates/classes/admin/class_detail_base.html templates/classes/admin/class_waitlist.html templates/classes/admin/class_discount_codes.html classes/spec/views/admin_class_workspace_spec.py
git commit -m "[classes] Wire per-class Workspace tabs (registrations/waitlist/discount codes)"
```

---

## Task 2: Refactor the detail page into the Overview tab

**Files:**
- Modify: `templates/classes/admin/class_detail.html`
- Modify: `classes/views.py` (`admin_class_detail` passes `active_subtab` + counts)
- Test: extend `classes/spec/views/admin_class_workspace_spec.py`

- [ ] **Step 1: Write the failing specs**

Append to `classes/spec/views/admin_class_workspace_spec.py`:

```python
def describe_class_overview_tab():
    def it_renders_summary_and_actions(admin_user, client, db):
        from classes.models import ClassOffering

        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        # Summary + Edit action present
        assert reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}).encode() in resp.content
        # Sub-tab nav present (Overview is now part of the workspace)
        assert reverse("classes:admin_class_registrations", kwargs={"pk": offering.pk}).encode() in resp.content

    def it_no_longer_shows_the_inline_student_email_form(admin_user, client, db):
        offering = ClassOfferingFactory()
        RegistrationFactory(class_offering=offering)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk}))
        # The bulk-email POST form moved to the Registrations tab; Overview no longer posts to admin_class_email.
        assert reverse("classes:admin_class_email", kwargs={"pk": offering.pk}).encode() not in resp.content
```

- [ ] **Step 2: Run → confirm the second spec FAILS** (Overview still has the inline email form).

Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest classes/spec/views/admin_class_workspace_spec.py -k overview -q --no-cov`

- [ ] **Step 3: Update `admin_class_detail` view**

In `classes/views.py`, `admin_class_detail` currently passes `active_tab`, `waitlist_registrations`, `offering`, `registrations`, `active_registration_count`. Change it so the Overview no longer needs the inline registrations/waitlist (they're on their own tabs) but still provides the sub-nav counts + `active_subtab`. Replace its context with:

```python
    return render(
        request,
        "classes/admin/class_detail.html",
        {
            "active_tab": "classes",
            "active_subtab": "overview",
            "offering": offering,
            **_class_workspace_counts(offering),
        },
    )
```

You can also drop the now-unused `registrations` / `waitlist_registrations` / `active_count` locals from this view (the counts come from the helper). Keep `offering` with its `select_related`/`prefetch_related("sessions")` for the summary.

- [ ] **Step 4: Refactor `templates/classes/admin/class_detail.html` into the Overview tab**

Make these surgical edits:
1. Change the first line `{% extends "classes/admin/base.html" %}` → `{% extends "classes/admin/class_detail_base.html" %}`.
2. Change `{% block tab_content %}` → `{% block detail_content %}` (and the matching `{% endblock %}`).
3. **Delete the page header block** (the `<div>` with the `<h2>{{ offering.title }}</h2>`, status span, and "Preview ↗" link) — the base template already renders title/status/Preview.
4. **Delete the entire Waitlist section** (`{# --- Waitlist section --- #}` through its closing `{% endif %}`) — it now lives on the Waitlist tab.
5. **Delete the entire Students section** (`{# --- Students section --- #}` `<div x-data=...>` … through its closing `</div>`, including the inline email form) — it now lives on the Registrations tab.
6. **Keep**: the details table (Instructor/Category/Price/Capacity), the Sessions table, and the final `admin-toolbar` actions block (Edit/Approve/Review/Duplicate/Archive/Delete) unchanged.

The result: Overview = summary tables + action toolbar, inside the sub-tab base.

- [ ] **Step 5: Run the workspace specs → confirm PASS**

Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest classes/spec/views/admin_class_workspace_spec.py -q --no-cov`
Then run the existing detail spec to confirm nothing else broke:
Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest classes/spec/views/admin_classes_spec.py -q --no-cov`
If a spec asserted inline-students/waitlist content on the detail page that has intentionally moved to a tab, update it to assert against the new tab URL instead, and note it.

- [ ] **Step 6: Commit**

```bash
git add templates/classes/admin/class_detail.html classes/views.py classes/spec/views/admin_class_workspace_spec.py
git commit -m "[classes] Make class detail the Workspace Overview tab (summary + actions)"
```

---

## Task 3: Verify + changelog

- [ ] **Step 1: Full suite**

Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest -q`
Expected: PASS, coverage ≥ 98%. Add specs for any uncovered branch (e.g. the discount-codes empty state, the new helper's both counts).

- [ ] **Step 2: Lint/format**

Run: `/home/josh/Code/plfog/.venv/bin/python -m ruff format . && /home/josh/Code/plfog/.venv/bin/python -m ruff check .`

- [ ] **Step 3: Changelog line (2.6.0)**

In `plfog/version.py`, append to the existing 2.6.0 `"changes"` list:

```python
            "Opening a class now puts everything for that class in one place — a tabbed workspace for its registrations, waitlist, and discount codes, with Edit and Preview right at the top.",
```

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "[classes] Phase 3 verify + changelog"
```

---

## Self-Review notes

- **Counts source of truth:** every tab view (including Overview) passes `_class_workspace_counts(offering)`, so the sub-nav badges (`active_registration_count`, `waitlist_count`) are consistent on every tab.
- **No deletions of behavior:** the students table + bulk email move to the Registrations tab (existing `class_registrations.html`, unchanged); the waitlist moves to the new Waitlist tab. Edit/Preview/Duplicate/Archive/Delete remain (Overview toolbar + base header Preview).
- **Reuse:** `class_registrations.html` and `class_discount_codes.html` are used as-is (already extend the base); only the discount-codes "+ New Code" button is repointed.
- **Out of scope (later — Phase 3b):** the instructor per-class Workspace (reuse this shell with `InstructorClassOfferingForm` + own-class scoping), and moving instructor Profile to the avatar menu.
```
