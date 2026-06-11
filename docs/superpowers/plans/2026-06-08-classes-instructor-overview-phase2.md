# Classes Management Redesign — Phase 2: Instructor Overview + Slim Instructor Nav

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give teaching members the same Overview treatment as admins — a dashboard at `/classes/instructor/` showing *their* drafts, classes awaiting review, recent sign-ups, and waitlists (no money, no approvals), with a "Create your first class" empty state — and slim the instructor nav to **Overview · Classes**.

**Architecture:** Mirrors Phase 1. New `instructor_overview` view aggregates the instructor's own `ClassOffering`/`Registration` rows and renders a new template extending `classes/instructor/base.html`. The My Classes list keeps view + name `instructor_dashboard` but its path moves to `/classes/instructor/classes/`. The instructor nav drops Registrations/Discount Codes/Profile as tabs; those pages stay reachable via "Quick links" on the Overview (so nothing is orphaned before Phase 3's per-class Workspace re-houses them). A "View live catalog ↗" header link is added.

**Tech Stack:** Django function-based views, pytest-describe BDD specs, factory-boy. Reuses `@instructor_required` (sets `request.instructor` = active `Member`), `ClassOffering.objects.for_instructor()`.

**Design decisions:**
- Overview becomes the instructor landing at `/classes/instructor/`; My Classes moves to `/classes/instructor/classes/` (name `instructor_dashboard` unchanged → all `{% url %}` refs and redirects keep working).
- Nav = **Overview · Classes** only. Registrations, Discount Codes, and Profile are reached from the Overview's "Quick links" (their pages/URLs are untouched). Profile-to-avatar-menu and discount-codes-into-the-workspace are deferred to Phase 3 per the agreed design.
- Instructor Overview shows **no money** and **no approvals queue** (they submit; they don't approve).
- "Needs attention" = their PENDING classes (awaiting admin review) + their DRAFT classes (finish/submit). Deriving per-class "changes requested" from `ClassApproval` is deferred (the feedback already shows on the class edit page); keeping Phase 2 query-simple.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `classes/views.py` | Modify | Add `instructor_overview` view |
| `classes/urls.py` | Modify | Repoint `instructor/` → overview; move My Classes to `instructor/classes/` |
| `templates/classes/instructor/overview.html` | Create | Instructor dashboard (attention, stats, recent sign-ups, empty state, quick links) |
| `templates/classes/instructor/base.html` | Modify | Nav → Overview · Classes; add "View live catalog ↗" |
| `templates/classes/public/list.html` | Modify | Instructor "Manage My Classes" CTA → `instructor_overview` |
| `classes/spec/views/instructor_overview_spec.py` | Create | BDD specs for the Overview |
| `classes/spec/views/instructor_nav_spec.py` | Create | BDD specs for the slimmed instructor nav |
| `plfog/version.py` | Modify | Append an instructor line to the existing 2.6.0 changelog entry |

---

## Task 1: Instructor Overview view + template + URL

**Files:**
- Modify: `classes/views.py` (add `instructor_overview`)
- Modify: `classes/urls.py`
- Create: `templates/classes/instructor/overview.html`
- Test: `classes/spec/views/instructor_overview_spec.py`

- [ ] **Step 1: Write the failing specs**

Create `classes/spec/views/instructor_overview_spec.py`:

```python
"""BDD specs for the instructor Overview dashboard."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import (
    ClassOfferingFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering
from membership.models import Member


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher T", instructor_slug="teacher-t")


@pytest.fixture
def other_instructor(db):
    user = UserFactory(username="other@example.com")
    return InstructorFactory(user=user, full_legal_name="Other", instructor_slug="other")


def describe_instructor_overview():
    def it_is_served_at_the_instructor_root(db):
        assert reverse("classes:instructor_overview") == "/classes/instructor/"

    def it_blocks_anonymous(db, client):
        resp = client.get(reverse("classes:instructor_overview"))
        assert resp.status_code == 302

    def it_blocks_inactive_members(db, client):
        user = UserFactory(username="inactive@example.com")
        InstructorFactory(user=user, status=Member.Status.FORMER)
        client.force_login(user)
        resp = client.get(reverse("classes:instructor_overview"))
        assert resp.status_code == 403

    def it_renders_for_an_active_member(member_user, client):
        client.force_login(member_user)
        resp = client.get(reverse("classes:instructor_overview"))
        assert resp.status_code == 200

    def describe_empty_state():
        def it_offers_create_first_class_when_they_have_none(instructor_fixture, client):
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:instructor_overview"))
            assert resp.context["has_classes"] is False
            assert reverse("classes:instructor_class_create").encode() in resp.content

    def describe_needs_attention():
        def it_lists_my_pending_class(instructor_fixture, client):
            ClassOfferingFactory(
                instructor=instructor_fixture, title="Forge Night",
                slug="forge", status=ClassOffering.Status.PENDING,
            )
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:instructor_overview"))
            assert b"Forge Night" in resp.content

        def it_does_not_show_another_instructors_class(instructor_fixture, other_instructor, client):
            ClassOfferingFactory(
                instructor=other_instructor, title="Not Mine",
                slug="notmine", status=ClassOffering.Status.PENDING,
            )
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:instructor_overview"))
            assert b"Not Mine" not in resp.content

    def describe_recent_signups():
        def it_shows_a_recent_registrant_on_my_class(instructor_fixture, client):
            mine = ClassOfferingFactory(instructor=instructor_fixture, slug="mine")
            RegistrationFactory(class_offering=mine, first_name="Jess", last_name="Park")
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:instructor_overview"))
            assert b"Jess" in resp.content

    def describe_quick_links():
        def it_links_to_registrations_codes_and_profile(instructor_fixture, client):
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:instructor_overview"))
            for name in [
                "classes:instructor_registrations",
                "classes:instructor_discount_codes",
                "classes:instructor_profile",
            ]:
                assert reverse(name).encode() in resp.content

    def describe_stats():
        def it_counts_my_published_classes(instructor_fixture, client):
            ClassOfferingFactory(instructor=instructor_fixture, slug="pub", status=ClassOffering.Status.PUBLISHED)
            client.force_login(instructor_fixture.user)
            resp = client.get(reverse("classes:instructor_overview"))
            assert resp.context["stats"]["published"] == 1
```

- [ ] **Step 2: Run specs to verify they fail**

Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest classes/spec/views/instructor_overview_spec.py -q --no-cov`
Expected: FAIL — `NoReverseMatch: 'instructor_overview'`.

- [ ] **Step 3: Add the `instructor_overview` view**

Add to `classes/views.py` immediately ABOVE `instructor_dashboard` (currently ~line 593):

```python
@instructor_required
def instructor_overview(request: HttpRequest) -> HttpResponse:
    """Teaching dashboard: the instructor's drafts, classes awaiting review,
    recent sign-ups, and active waitlists. No money, no approvals — they submit
    classes, they don't approve them. Empty state nudges a first class."""
    instructor: Member = request.instructor  # type: ignore[attr-defined]
    my_classes = ClassOffering.objects.for_instructor(instructor)

    drafts = my_classes.filter(status=ClassOffering.Status.DRAFT).select_related("category").order_by("-updated_at")
    pending = my_classes.filter(status=ClassOffering.Status.PENDING).select_related("category").order_by("created_at")
    waitlist_classes = (
        my_classes.annotate(
            waiting=Count(
                "registrations",
                filter=Q(registrations__status=Registration.Status.WAITLISTED),
            )
        )
        .filter(waiting__gt=0)
        .order_by("-waiting")
    )
    recent_registrations = (
        Registration.objects.filter(class_offering__instructor=instructor)
        .select_related("class_offering")
        .order_by("-registered_at")[:8]
    )

    stats = {
        "published": my_classes.filter(status=ClassOffering.Status.PUBLISHED).count(),
        "pending": pending.count(),
        "drafts": drafts.count(),
        "total_signups": Registration.objects.filter(
            class_offering__instructor=instructor, status=Registration.Status.CONFIRMED
        ).count(),
    }

    return render(
        request,
        "classes/instructor/overview.html",
        {
            "active_tab": "overview",
            "instructor": instructor,
            "drafts": drafts,
            "pending_classes": pending,
            "waitlist_classes": waitlist_classes,
            "recent_registrations": recent_registrations,
            "has_classes": my_classes.exists(),
            "stats": stats,
        },
    )
```

Note: the `.filter(waiting__gt=0)` on the annotated `waiting` count is correct at runtime; django-stubs/mypy can't see `annotate()` aliases and would false-positive on it — that's a known limitation, not a bug (the specs prove the query). Confirm `Count`, `Q`, `timezone` etc. are already imported (they are, from Phase 1). Confirm `ClassOffering`, `Registration`, `Member`, `render`, `instructor_required` are in scope (used throughout the file).

- [ ] **Step 4: Wire the URLs**

In `classes/urls.py`, replace:

```python
    path("instructor/", views.instructor_dashboard, name="instructor_dashboard"),
```

with:

```python
    path("instructor/", views.instructor_overview, name="instructor_overview"),
    path("instructor/classes/", views.instructor_dashboard, name="instructor_dashboard"),
```

- [ ] **Step 5: Create the Overview template**

Create `templates/classes/instructor/overview.html`:

```django
{% extends "classes/instructor/base.html" %}
{% load classes_tags %}

{% block tab_content %}
{% if not has_classes %}
  <div class="hub-card" style="text-align:center;padding:2.5rem 1rem;">
    <div style="font-size:2rem;">🛠</div>
    <h2 style="margin:0.5rem 0 0.25rem;">Teach your first class</h2>
    <p style="color:var(--hub-text-muted);max-width:34rem;margin:0 auto 1rem;">
      Set the details, add sessions, make it look great. We'll walk you through it —
      then an admin reviews it before it goes live.
    </p>
    <a class="hub-btn" href="{% url 'classes:instructor_class_create' %}">+ Create your first class</a>
  </div>
{% else %}
  <div style="display:flex;flex-direction:column;gap:1.25rem;">

    {# Needs attention: pending review + drafts #}
    <section class="hub-card" style="margin:0;">
      <h2 style="margin-top:0;">Needs you</h2>
      {% for c in pending_classes %}
        <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--hub-border);">
          <span>{{ c.title }}</span>
          <span style="color:var(--hub-text-muted);">awaiting admin review</span>
        </div>
      {% endfor %}
      {% for c in drafts %}
        <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--hub-border);">
          <a href="{% url 'classes:instructor_class_edit' pk=c.pk %}">{{ c.title }}</a>
          <span style="color:var(--hub-text-muted);">draft — finish &amp; submit</span>
        </div>
      {% endfor %}
      {% if not pending_classes and not drafts %}
        <p style="color:var(--hub-text-muted);">Nothing needs your attention right now.</p>
      {% endif %}
    </section>

    {# At a glance (no money) #}
    <section class="hub-card" style="margin:0;">
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;">
        <div><div style="font-size:1.6rem;font-weight:700;">{{ stats.published }}</div><div style="color:var(--hub-text-muted);font-size:.8rem;">published</div></div>
        <div><div style="font-size:1.6rem;font-weight:700;">{{ stats.pending }}</div><div style="color:var(--hub-text-muted);font-size:.8rem;">awaiting review</div></div>
        <div><div style="font-size:1.6rem;font-weight:700;">{{ stats.drafts }}</div><div style="color:var(--hub-text-muted);font-size:.8rem;">drafts</div></div>
        <div><div style="font-size:1.6rem;font-weight:700;">{{ stats.total_signups }}</div><div style="color:var(--hub-text-muted);font-size:.8rem;">total sign-ups</div></div>
      </div>
    </section>

    {# Active waitlists #}
    {% if waitlist_classes %}
    <section class="hub-card" style="margin:0;">
      <h2 style="margin-top:0;">Active waitlists</h2>
      {% for c in waitlist_classes %}
        <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--hub-border);">
          <span>{{ c.title }}</span><span style="color:var(--hub-text-muted);">{{ c.waiting }} waiting</span>
        </div>
      {% endfor %}
    </section>
    {% endif %}

    {# Recent sign-ups #}
    <section class="hub-card" style="margin:0;">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <h2 style="margin:0;">Recent sign-ups</h2>
        <a href="{% url 'classes:instructor_registrations' %}">View all →</a>
      </div>
      <table style="width:100%;margin-top:8px;">
        <tbody>
        {% for r in recent_registrations %}
          <tr><td>{{ r.first_name }} {{ r.last_name }}</td><td>{{ r.class_offering.title }}</td></tr>
        {% empty %}
          <tr><td style="color:var(--hub-text-muted);padding:0.5rem 0;">No sign-ups yet.</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </section>

    {# Quick links to the pages no longer in the tab strip #}
    <section class="hub-card" style="margin:0;">
      <div style="display:flex;gap:1rem;flex-wrap:wrap;">
        <a href="{% url 'classes:instructor_registrations' %}">Registrations</a>
        <a href="{% url 'classes:instructor_discount_codes' %}">Discount codes</a>
        <a href="{% url 'classes:instructor_profile' %}">Edit profile</a>
      </div>
    </section>

  </div>
{% endif %}
{% endblock %}
```

- [ ] **Step 6: Run specs to verify they pass**

Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest classes/spec/views/instructor_overview_spec.py -q --no-cov`
Then confirm the existing instructor specs still pass (they use `reverse("classes:instructor_dashboard")`, now at the new path):
Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest classes/spec/views/instructor_dashboard_spec.py -q --no-cov`

- [ ] **Step 7: Commit**

```bash
git add classes/views.py classes/urls.py templates/classes/instructor/overview.html classes/spec/views/instructor_overview_spec.py
git commit -m "[classes] Add instructor Overview dashboard at /classes/instructor/"
```

---

## Task 2: Slim the instructor nav + live-catalog link

**Files:**
- Modify: `templates/classes/instructor/base.html`
- Test: `classes/spec/views/instructor_nav_spec.py`

- [ ] **Step 1: Write the failing specs**

Create `classes/spec/views/instructor_nav_spec.py`:

```python
"""BDD specs for the slimmed instructor nav."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import InstructorFactory, UserFactory


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher T", instructor_slug="teacher-t")


def describe_instructor_nav():
    def it_shows_overview_and_classes_tabs(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:instructor_overview"))
        assert reverse("classes:instructor_overview").encode() in resp.content
        assert reverse("classes:instructor_dashboard").encode() in resp.content

    def it_drops_the_old_tabs_from_the_nav(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:instructor_overview"))
        # Registrations/Discount Codes/Profile are no longer top-level tabs.
        # They survive as Overview "Quick links", but the <nav> tab strip is just two tabs.
        body = resp.content.split(b"</nav>")[0]
        assert reverse("classes:instructor_registrations").encode() not in body
        assert reverse("classes:instructor_profile").encode() not in body

    def it_offers_a_live_catalog_link(instructor_fixture, client, settings):
        settings.BOOK_BASE_URL = "https://book.example.test"
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:instructor_overview"))
        assert b"https://book.example.test/classes/" in resp.content
        assert b"View live catalog" in resp.content
```

- [ ] **Step 2: Run specs to verify they fail**

Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest classes/spec/views/instructor_nav_spec.py -q --no-cov`

- [ ] **Step 3: Rewrite `templates/classes/instructor/base.html`**

Replace the entire file with:

```django
{% extends "hub/base.html" %}

{% block title %}Manage Classes & Workshops — {{ active_tab|title }}{% endblock %}

{% block content %}
<div class="hub-card">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:0.75rem;">
        <h1 style="margin:0;">Manage Classes & Workshops</h1>
        <span style="display:flex;gap:8px;flex-wrap:wrap;">
            {% if instructor.instructor_slug %}
            <a class="hub-btn hub-btn--sm hub-btn--ghost" href="{% url 'classes:public_instructor' slug=instructor.instructor_slug %}" target="_blank" rel="noopener">View public profile ↗</a>
            {% endif %}
            <a class="hub-btn hub-btn--sm hub-btn--ghost" href="{{ BOOK_BASE_URL }}/classes/" target="_blank" rel="noopener">View live catalog ↗</a>
        </span>
    </div>
    <nav style="display:flex; border-bottom:1px solid var(--hub-border); gap:0; margin-bottom:1.25rem; flex-wrap:wrap;" role="tablist">
        {% with overview_tabs="overview registrations discount_codes profile" %}
        <a href="{% url 'classes:instructor_overview' %}" class="vote-tab{% if active_tab in overview_tabs.split %} vote-tab--active{% endif %}">Overview</a>
        <a href="{% url 'classes:instructor_dashboard' %}" class="vote-tab{% if active_tab == 'classes' %} vote-tab--active{% endif %}">Classes</a>
        {% endwith %}
    </nav>
    {% block tab_content %}{% endblock %}
</div>
{% endblock %}
```

- [ ] **Step 4: Run specs to verify they pass**

Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest classes/spec/views/instructor_nav_spec.py -q --no-cov`

- [ ] **Step 5: Run the whole instructor spec set**

Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest classes/spec/views/instructor_dashboard_spec.py classes/spec/views/instructor_email_spec.py classes/spec/views/instructor_submit_spec.py classes/spec/views/instructor_overview_spec.py -q --no-cov`
Expected: PASS. If any spec asserted an old instructor nav tab anchor, update it to reflect the new structure (the page is reached via Quick links now, not a tab) and note it in the commit.

- [ ] **Step 6: Commit**

```bash
git add templates/classes/instructor/base.html classes/spec/views/instructor_nav_spec.py
git commit -m "[classes] Slim instructor nav to Overview/Classes + live-catalog link"
```

---

## Task 3: Repoint the public CTA + extend the changelog

**Files:**
- Modify: `templates/classes/public/list.html`
- Modify: `plfog/version.py`

- [ ] **Step 1: Point the "Manage My Classes" CTA at the instructor Overview**

In `templates/classes/public/list.html`, change:

```django
      <a class="cta-secondary" href="{% url 'classes:instructor_dashboard' %}">Manage My Classes</a>
```

to:

```django
      <a class="cta-secondary" href="{% url 'classes:instructor_overview' %}">Manage My Classes</a>
```

- [ ] **Step 2: Add an instructor line to the existing 2.6.0 changelog entry**

In `plfog/version.py`, the top `CHANGELOG` entry is `"version": "2.6.0"`. Append one item to its `"changes"` list (do NOT add a new version entry — Phase 2 ships in the same release):

```python
            "Instructors get the same upgrade on their teaching dashboard — opening it now shows your drafts, anything awaiting review, your latest sign-ups, and your waitlists at a glance, with a one-tap 'Create your first class' if you're just getting started.",
```

- [ ] **Step 3: Commit**

```bash
git add templates/classes/public/list.html plfog/version.py
git commit -m "[classes] Point instructor CTA to Overview; changelog for instructor dashboard"
```

---

## Task 4: Full verification

- [ ] **Step 1: Whole suite**

Run: `/home/josh/Code/plfog/.venv/bin/python -m pytest -q`
Expected: PASS, coverage ≥ 98%. If the new view has an uncovered branch, add a targeted spec (e.g., the `{% if not pending_classes and not drafts %}` path is covered by `it_renders_for_an_active_member` with an instructor who has no classes; the waitlist branch is covered by adding a waitlisted registration — add a spec if coverage flags it).

- [ ] **Step 2: Lint + format**

Run: `/home/josh/Code/plfog/.venv/bin/python -m ruff format . && /home/josh/Code/plfog/.venv/bin/python -m ruff check . `
Expected: clean.

- [ ] **Step 3: Commit any fixups**

```bash
git add -A && git commit -m "[classes] Phase 2 lint/coverage fixups" || echo "nothing to fix up"
```

---

## Self-Review notes (for the executor)

- **Context keys** the template uses must match the view: `has_classes`, `drafts`, `pending_classes`, `waitlist_classes`, `recent_registrations`, `stats` (with `published`/`pending`/`drafts`/`total_signups`), `instructor`.
- **No deletions:** `instructor_dashboard`, `instructor_registrations`, `instructor_discount_codes`, `instructor_profile` keep their views + URL names; only `instructor_dashboard`'s path moves to `/instructor/classes/`.
- **Gate parity:** `instructor_overview` uses `@instructor_required`, so anon → 302, inactive member → 403, active member → 200 (matches existing instructor specs).
- **Known follow-up:** Phase 3 (per-class Workspace) re-houses Registrations/Discount Codes per class and moves Profile to the avatar menu; until then they live as Overview Quick links.
