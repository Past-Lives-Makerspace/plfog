# Classes Management Redesign — Phase 1: Admin Overview + Slim Nav + Settings Hub

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the admin portal's flat 7-tab strip with a 3-item nav (Overview · Classes · Settings), where Overview is a new dashboard that surfaces the approvals queue, recent registrations, activity, and at-a-glance stats — reusing every existing view.

**Architecture:** Pure Django MPT (multi-page templated) work. A new `admin_overview` view aggregates existing models (`ClassOffering`, `Registration`, `CmsActivity`) into a dashboard context and renders a new template that extends the existing `classes/admin/base.html`. The 7-tab nav in `base.html` collapses to three; the rarely-used config pages (Categories, Discount Codes, Questions, Waivers) move behind a new `admin_settings_hub` landing. The full Registrations table and Activity log keep their existing views/URLs and are reached via "view all" links on Overview. No models change; no routes are deleted (only the top-level path of the classes list and the waivers form move).

**Tech Stack:** Django 6, function-based views, `pytest` + `pytest-describe` BDD specs (`*_spec.py`), `factory-boy`, Django templates with the project's `vote-tab` nav styling and `classes_tags` filters (`cents_as_dollars`).

**Design decisions (the "why"):**
- Overview becomes the admin landing at `/classes/admin/`; the classes list moves to `/classes/admin/classes/`. URL **names** are unchanged, so all `{% url %}` references keep working — only paths move.
- Activity and Registrations stop being top-level tabs but keep their views/URLs; Overview links to them via "View all / View full log". This honors the original "remove the Registrations tab" ask without deleting the searchable table that admins still need for cross-class registrant lookup.
- Waiver/legal text stays admin-only (it lives under Settings); this is unchanged here but matters for later phases.
- Nav highlight is driven by matching `active_tab` against a set, so the ~10 existing config views need **no** edits.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `classes/views.py` | Modify | Add `admin_overview` + `admin_settings_hub` views; ensure aggregate imports present |
| `classes/urls.py` | Modify | Repoint `admin/` → overview, move classes list to `admin/classes/`, add settings hub, move waivers form path |
| `templates/classes/admin/overview.html` | Create | The dashboard template (4 sections) |
| `templates/classes/admin/settings_hub.html` | Create | The Settings landing (link cards) |
| `templates/classes/admin/base.html` | Modify | 7 tabs → Overview · Classes · Settings + "View live catalog ↗" action |
| `templates/classes/admin/activity.html` | Modify | Remove stale comment referencing "the Registrations tab" |
| `templates/classes/public/list.html` | Modify | Admin "Manage" CTA → `admin_overview` |
| `classes/spec/views/admin_overview_spec.py` | Create | BDD specs for the Overview view |
| `classes/spec/views/admin_settings_hub_spec.py` | Create | BDD specs for the Settings hub |
| `classes/spec/views/admin_nav_spec.py` | Create | BDD specs for nav structure + catalog link |
| `plfog/version.py` | Modify | Version bump + changelog entry (final task) |

---

## Task 1: Admin Overview view + template + URL

**Files:**
- Modify: `classes/views.py` (add `admin_overview`; ensure imports)
- Modify: `classes/urls.py` (add `admin/` → `admin_overview`, move classes list)
- Create: `templates/classes/admin/overview.html`
- Test: `classes/spec/views/admin_overview_spec.py`

- [ ] **Step 1: Write the failing specs**

Create `classes/spec/views/admin_overview_spec.py`:

```python
"""BDD specs for the admin Overview dashboard."""

from __future__ import annotations

from django.urls import reverse


def describe_admin_overview():
    def it_gates_behind_admin_role(member_user, client, db):
        client.force_login(member_user)
        resp = client.get(reverse("classes:admin_overview"))
        assert resp.status_code == 403

    def it_renders_for_admin(admin_user, client, db):
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_overview"))
        assert resp.status_code == 200

    def it_is_served_at_the_admin_root(admin_user, client, db):
        assert reverse("classes:admin_overview") == "/classes/admin/"

    def describe_approvals_queue():
        def it_lists_a_pending_class(admin_user, client, db):
            from classes.factories import ClassOfferingFactory
            from classes.models import ClassOffering

            client.force_login(admin_user)
            ClassOfferingFactory(title="Forge Night", status=ClassOffering.Status.PENDING)
            resp = client.get(reverse("classes:admin_overview"))
            assert b"Forge Night" in resp.content

        def it_omits_published_classes_from_the_queue(admin_user, client, db):
            from classes.factories import ClassOfferingFactory
            from classes.models import ClassOffering

            client.force_login(admin_user)
            ClassOfferingFactory(title="Already Live", status=ClassOffering.Status.PUBLISHED)
            resp = client.get(reverse("classes:admin_overview"))
            assert b"Already Live" not in resp.content

    def describe_waitlist_panel():
        def it_shows_a_class_with_a_waitlisted_registration(admin_user, client, db):
            from classes.factories import ClassOfferingFactory, RegistrationFactory
            from classes.models import ClassOffering, Registration

            client.force_login(admin_user)
            offering = ClassOfferingFactory(title="Blacksmithing", status=ClassOffering.Status.PUBLISHED)
            RegistrationFactory(class_offering=offering, status=Registration.Status.WAITLISTED)
            resp = client.get(reverse("classes:admin_overview"))
            assert b"Blacksmithing" in resp.content

    def describe_recent_registrations():
        def it_shows_a_recent_registrant_linking_to_detail(admin_user, client, db):
            from classes.factories import RegistrationFactory

            client.force_login(admin_user)
            reg = RegistrationFactory(first_name="Jess", last_name="Park")
            resp = client.get(reverse("classes:admin_overview"))
            assert b"Jess" in resp.content
            detail = reverse("classes:admin_registration_detail", kwargs={"pk": reg.pk})
            assert detail.encode() in resp.content

        def it_links_to_the_full_registrations_table(admin_user, client, db):
            client.force_login(admin_user)
            resp = client.get(reverse("classes:admin_overview"))
            assert reverse("classes:admin_registrations").encode() in resp.content

    def describe_activity_panel():
        def it_links_to_the_full_activity_log(admin_user, client, db):
            client.force_login(admin_user)
            resp = client.get(reverse("classes:admin_overview"))
            assert reverse("classes:admin_activity").encode() in resp.content

    def describe_stats():
        def it_counts_a_registration_from_this_week(admin_user, client, db):
            from classes.factories import RegistrationFactory

            client.force_login(admin_user)
            RegistrationFactory()
            resp = client.get(reverse("classes:admin_overview"))
            # one registration this week → the "new this week" tile shows at least 1
            assert resp.status_code == 200
            assert resp.context["stats"]["new_regs_week"] == 1

        def it_sums_collected_cents_over_30_days(admin_user, client, db):
            from classes.factories import RegistrationFactory
            from classes.models import Registration

            client.force_login(admin_user)
            RegistrationFactory(amount_paid_cents=4500, status=Registration.Status.CONFIRMED)
            resp = client.get(reverse("classes:admin_overview"))
            assert resp.context["stats"]["collected_30d"] == 4500

        def it_builds_a_14_day_registration_series(admin_user, client, db):
            client.force_login(admin_user)
            resp = client.get(reverse("classes:admin_overview"))
            assert len(resp.context["reg_by_day"]) == 14
```

- [ ] **Step 2: Run the specs to verify they fail**

Run: `pytest classes/spec/views/admin_overview_spec.py -q`
Expected: FAIL — `NoReverseMatch: 'admin_overview' is not a valid view function or pattern name`.

- [ ] **Step 3: Ensure aggregate imports exist in `classes/views.py`**

At the top of `classes/views.py`, confirm these imports are present; add any that are missing (do not duplicate existing ones):

```python
from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
```

(`Count` and `Q` are already imported; `Sum`, `TruncDate`, `timedelta`, `timezone` may need adding.)

- [ ] **Step 4: Add the `admin_overview` view**

Add this view to `classes/views.py` immediately above `admin_classes` (around line 927):

```python
@classes_admin_access_required
def admin_overview(request: HttpRequest) -> HttpResponse:
    """Admin dashboard: approvals queue, waitlist pressure, recent registrations,
    recent activity, and at-a-glance stats. Each panel links to the full
    table/log it summarizes."""
    now = timezone.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    pending = (
        ClassOffering.objects.pending_review()
        .select_related("instructor", "category")
        .order_by("created_at")
    )
    waitlist_classes = (
        ClassOffering.objects.annotate(
            waiting=Count(
                "registrations",
                filter=Q(registrations__status=Registration.Status.WAITLISTED),
            )
        )
        .filter(waiting__gt=0)
        .select_related("instructor")
        .order_by("-waiting")
    )
    recent_registrations = Registration.objects.select_related("class_offering").order_by(
        "-registered_at"
    )[:8]
    recent_activity = CmsActivity.objects.select_related(
        "class_offering", "registration", "actor"
    ).order_by("-created_at")[:8]

    start = (now - timedelta(days=13)).date()
    counts = {
        row["day"]: row["c"]
        for row in Registration.objects.filter(registered_at__date__gte=start)
        .annotate(day=TruncDate("registered_at"))
        .values("day")
        .annotate(c=Count("pk"))
    }
    reg_by_day = [
        {"date": start + timedelta(days=i), "count": counts.get(start + timedelta(days=i), 0)}
        for i in range(14)
    ]

    stats = {
        "pending": pending.count(),
        "new_regs_week": Registration.objects.filter(registered_at__gte=week_ago).count(),
        "active_registrations": Registration.objects.filter(
            status=Registration.Status.CONFIRMED
        ).count(),
        "collected_30d": Registration.objects.filter(registered_at__gte=month_ago).aggregate(
            total=Sum("amount_paid_cents")
        )["total"]
        or 0,
    }

    return render(
        request,
        "classes/admin/overview.html",
        {
            "active_tab": "overview",
            "pending_classes": pending,
            "waitlist_classes": waitlist_classes,
            "recent_registrations": recent_registrations,
            "recent_activity": recent_activity,
            "reg_by_day": reg_by_day,
            "reg_by_day_max": max((d["count"] for d in reg_by_day), default=0),
            "stats": stats,
        },
    )
```

- [ ] **Step 5: Wire the URLs**

In `classes/urls.py`, replace the line:

```python
    path("admin/", views.admin_classes, name="admin_classes"),
```

with:

```python
    path("admin/", views.admin_overview, name="admin_overview"),
    path("admin/classes/", views.admin_classes, name="admin_classes"),
```

- [ ] **Step 6: Create the Overview template**

Create `templates/classes/admin/overview.html`:

```django
{% extends "classes/admin/base.html" %}
{% load classes_tags %}

{% block tab_content %}
<div class="cms-overview" style="display:flex;flex-direction:column;gap:1.25rem;">

  {# ① Needs your attention #}
  <section style="display:grid;grid-template-columns:2fr 1fr;gap:1rem;">
    <div class="hub-card" style="margin:0;">
      <h2 style="margin-top:0;">Awaiting approval <span style="color:var(--hub-text-muted);">({{ stats.pending }})</span></h2>
      {% for c in pending_classes %}
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--hub-border);">
          <a href="{% url 'classes:admin_class_detail' pk=c.pk %}">{{ c.title }}</a>
          <span>
            <a class="hub-btn hub-btn--sm hub-btn--ghost" href="{% url 'classes:admin_class_review' pk=c.pk %}">Review</a>
            <form method="post" action="{% url 'classes:admin_class_approve' pk=c.pk %}" style="display:inline;">
              {% csrf_token %}
              <button type="submit" class="hub-btn hub-btn--sm">Approve</button>
            </form>
          </span>
        </div>
      {% empty %}
        <p style="color:var(--hub-text-muted);">Nothing waiting — you're all caught up.</p>
      {% endfor %}
    </div>
    <div class="hub-card" style="margin:0;">
      <h2 style="margin-top:0;">Active waitlists</h2>
      {% for c in waitlist_classes %}
        <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--hub-border);">
          <a href="{% url 'classes:admin_class_detail' pk=c.pk %}">{{ c.title }}</a>
          <span style="color:var(--hub-text-muted);">{{ c.waiting }} waiting</span>
        </div>
      {% empty %}
        <p style="color:var(--hub-text-muted);">No active waitlists.</p>
      {% endfor %}
    </div>
  </section>

  {# ② At a glance #}
  <section class="hub-card" style="margin:0;">
    <div style="display:flex;gap:1rem;flex-wrap:wrap;align-items:stretch;">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;flex:1;min-width:240px;">
        <div><div style="font-size:1.6rem;font-weight:700;">{{ stats.pending }}</div><div style="color:var(--hub-text-muted);font-size:.8rem;">awaiting approval</div></div>
        <div><div style="font-size:1.6rem;font-weight:700;">{{ stats.new_regs_week }}</div><div style="color:var(--hub-text-muted);font-size:.8rem;">new sign-ups (7d)</div></div>
        <div><div style="font-size:1.6rem;font-weight:700;">{{ stats.active_registrations }}</div><div style="color:var(--hub-text-muted);font-size:.8rem;">confirmed registrations</div></div>
        <div><div style="font-size:1.6rem;font-weight:700;">{{ stats.collected_30d|cents_as_dollars }}</div><div style="color:var(--hub-text-muted);font-size:.8rem;">collected (30d)</div></div>
      </div>
      <div style="flex:1.3;min-width:260px;">
        <div style="color:var(--hub-text-muted);font-size:.8rem;margin-bottom:6px;">Registrations · last 14 days</div>
        <div style="display:flex;align-items:flex-end;gap:3px;height:70px;">
          {% for d in reg_by_day %}
            <div title="{{ d.date }}: {{ d.count }}"
                 style="flex:1;background:var(--hub-accent, #c46a1b);min-height:2px;height:{% widthratio d.count reg_by_day_max 100 %}%;"></div>
          {% endfor %}
        </div>
      </div>
    </div>
  </section>

  {# ③ Recent registrations #}
  <section class="hub-card" style="margin:0;">
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <h2 style="margin:0;">Recent sign-ups</h2>
      <a href="{% url 'classes:admin_registrations' %}">View all registrations →</a>
    </div>
    <table style="width:100%;margin-top:8px;">
      <tbody>
      {% for r in recent_registrations %}
        <tr>
          <td><a href="{% url 'classes:admin_registration_detail' pk=r.pk %}">{{ r.first_name }} {{ r.last_name }}</a></td>
          <td>{{ r.class_offering.title }}</td>
          <td style="text-align:right;">{% if r.amount_paid_cents %}{{ r.amount_paid_cents|cents_as_dollars }}{% else %}Free{% endif %}</td>
        </tr>
      {% empty %}
        <tr><td style="color:var(--hub-text-muted);padding:0.5rem 0;">No registrations yet.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </section>

  {# ④ Activity #}
  <section class="hub-card" style="margin:0;">
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <h2 style="margin:0;">Activity</h2>
      <a href="{% url 'classes:admin_activity' %}">View full log →</a>
    </div>
    <ul style="margin:8px 0 0;padding-left:1rem;color:var(--hub-text-muted);">
      {% for ev in recent_activity %}
        <li>{{ ev.created_at|date:"M j, g:i a" }} — {{ ev.get_kind_display }}{% if ev.class_offering %}: {{ ev.class_offering.title }}{% endif %}</li>
      {% empty %}
        <li>No activity yet.</li>
      {% endfor %}
    </ul>
  </section>

</div>
{% endblock %}
```

- [ ] **Step 7: Run the specs to verify they pass**

Run: `pytest classes/spec/views/admin_overview_spec.py -q`
Expected: PASS (all specs green).

- [ ] **Step 8: Commit**

```bash
git add classes/views.py classes/urls.py templates/classes/admin/overview.html classes/spec/views/admin_overview_spec.py
git commit -m "[classes] Add admin Overview dashboard at /classes/admin/"
```

---

## Task 2: Settings hub view + template + URL moves

**Files:**
- Modify: `classes/views.py` (add `admin_settings_hub`)
- Modify: `classes/urls.py` (add hub at `admin/settings/`; move waivers form to `admin/settings/waivers/`)
- Create: `templates/classes/admin/settings_hub.html`
- Test: `classes/spec/views/admin_settings_hub_spec.py`

- [ ] **Step 1: Write the failing specs**

Create `classes/spec/views/admin_settings_hub_spec.py`:

```python
"""BDD specs for the admin Settings hub landing."""

from __future__ import annotations

from django.urls import reverse


def describe_admin_settings_hub():
    def it_gates_behind_admin_role(member_user, client, db):
        client.force_login(member_user)
        resp = client.get(reverse("classes:admin_settings_hub"))
        assert resp.status_code == 403

    def it_renders_for_admin(admin_user, client, db):
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_settings_hub"))
        assert resp.status_code == 200

    def it_is_served_at_admin_settings(admin_user, client, db):
        assert reverse("classes:admin_settings_hub") == "/classes/admin/settings/"

    def it_links_to_each_config_area(admin_user, client, db):
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_settings_hub"))
        for name in [
            "classes:admin_categories",
            "classes:admin_discount_codes",
            "classes:admin_registration_questions",
            "classes:admin_settings",
        ]:
            assert reverse(name).encode() in resp.content


def describe_waivers_form_move():
    def it_serves_the_waivers_form_at_its_new_path(admin_user, client, db):
        assert reverse("classes:admin_settings") == "/classes/admin/settings/waivers/"

    def it_still_renders_the_waivers_form(admin_user, client, db):
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_settings"))
        assert resp.status_code == 200
```

- [ ] **Step 2: Run the specs to verify they fail**

Run: `pytest classes/spec/views/admin_settings_hub_spec.py -q`
Expected: FAIL — `NoReverseMatch: 'admin_settings_hub'` and the waivers-path assertion fails (still `/classes/admin/settings/`).

- [ ] **Step 3: Add the `admin_settings_hub` view**

Add to `classes/views.py` immediately above the existing `admin_settings` view (around line 1629):

```python
@classes_admin_access_required
def admin_settings_hub(request: HttpRequest) -> HttpResponse:
    """Landing page that groups the rarely-touched config areas."""
    return render(request, "classes/admin/settings_hub.html", {"active_tab": "settings"})
```

- [ ] **Step 4: Move the waivers form path and add the hub path**

In `classes/urls.py`, replace:

```python
    path("admin/settings/", views.admin_settings, name="admin_settings"),
```

with:

```python
    path("admin/settings/", views.admin_settings_hub, name="admin_settings_hub"),
    path("admin/settings/waivers/", views.admin_settings, name="admin_settings"),
```

- [ ] **Step 5: Create the Settings hub template**

Create `templates/classes/admin/settings_hub.html`:

```django
{% extends "classes/admin/base.html" %}

{% block tab_content %}
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem;">
  <a class="hub-card" style="margin:0;text-decoration:none;" href="{% url 'classes:admin_categories' %}">
    <h3 style="margin:0 0 4px;">Categories</h3>
    <p style="margin:0;color:var(--hub-text-muted);">Guild-linked groupings for classes.</p>
  </a>
  <a class="hub-card" style="margin:0;text-decoration:none;" href="{% url 'classes:admin_discount_codes' %}">
    <h3 style="margin:0 0 4px;">Discount Codes</h3>
    <p style="margin:0;color:var(--hub-text-muted);">Global codes and approvals.</p>
  </a>
  <a class="hub-card" style="margin:0;text-decoration:none;" href="{% url 'classes:admin_registration_questions' %}">
    <h3 style="margin:0 0 4px;">Questions</h3>
    <p style="margin:0;color:var(--hub-text-muted);">Onboarding questions on every registration.</p>
  </a>
  <a class="hub-card" style="margin:0;text-decoration:none;" href="{% url 'classes:admin_settings' %}">
    <h3 style="margin:0 0 4px;">Waivers &amp; reminders</h3>
    <p style="margin:0;color:var(--hub-text-muted);">Liability text, model-release text, reminder timing.</p>
  </a>
</div>
{% endblock %}
```

- [ ] **Step 6: Run the specs to verify they pass**

Run: `pytest classes/spec/views/admin_settings_hub_spec.py -q`
Expected: PASS.

- [ ] **Step 7: Run the existing settings spec to confirm the move didn't break it**

Run: `pytest classes/spec/views/admin_settings_spec.py -q`
Expected: PASS (it uses `reverse("classes:admin_settings")`, which now resolves to the new path).

- [ ] **Step 8: Commit**

```bash
git add classes/views.py classes/urls.py templates/classes/admin/settings_hub.html classes/spec/views/admin_settings_hub_spec.py
git commit -m "[classes] Add admin Settings hub; move waivers form under it"
```

---

## Task 3: Slim the nav + add the live-catalog action

**Files:**
- Modify: `templates/classes/admin/base.html`
- Test: `classes/spec/views/admin_nav_spec.py`

- [ ] **Step 1: Write the failing specs**

Create `classes/spec/views/admin_nav_spec.py`:

```python
"""BDD specs for the slimmed admin nav and the live-catalog link."""

from __future__ import annotations

from django.urls import reverse


def describe_admin_nav():
    def it_shows_the_three_top_level_tabs(admin_user, client, db):
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_overview"))
        body = resp.content
        assert reverse("classes:admin_overview").encode() in body
        assert reverse("classes:admin_classes").encode() in body
        assert reverse("classes:admin_settings_hub").encode() in body

    def it_drops_the_old_top_level_tabs(admin_user, client, db):
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_overview"))
        # Categories/Questions/Discount Codes/Activity are no longer nav tabs;
        # they are reachable via Overview links + the Settings hub, not the tab strip.
        assert b">Categories<" not in resp.content
        assert b">Questions<" not in resp.content

    def it_offers_a_live_catalog_link(admin_user, client, db, settings):
        settings.BOOK_BASE_URL = "https://book.example.test"
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_overview"))
        assert b"https://book.example.test/classes/" in resp.content
        assert b"View live catalog" in resp.content
```

- [ ] **Step 2: Run the specs to verify they fail**

Run: `pytest classes/spec/views/admin_nav_spec.py -q`
Expected: FAIL — the live-catalog string and the 3-tab structure are not present yet (old 7-tab `base.html` still renders Categories/Questions tabs).

- [ ] **Step 3: Rewrite the nav in `templates/classes/admin/base.html`**

Replace the entire file contents with:

```django
{% extends "hub/base.html" %}
{% load hub_tags %}

{% block title %}Classes Admin — {{ active_tab|title }}{% endblock %}

{% block content %}
<div class="hub-card">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:0.75rem;">
        <h1 style="margin:0;">Classes</h1>
        <a class="hub-btn hub-btn--sm hub-btn--ghost" href="{{ BOOK_BASE_URL }}/classes/"
           target="_blank" rel="noopener" title="See the public booking catalog">View live catalog ↗</a>
    </div>
    <nav style="display: flex; border-bottom: 1px solid var(--hub-border); gap: 0; margin-bottom: 1.25rem; flex-wrap: wrap;" role="tablist">
        {% with overview_tabs="overview activity registrations" settings_tabs="categories discount_codes questions settings" %}
        <a href="{% url 'classes:admin_overview' %}" class="vote-tab{% if active_tab in overview_tabs.split %} vote-tab--active{% endif %}">Overview</a>
        <a href="{% url 'classes:admin_classes' %}" class="vote-tab{% if active_tab == 'classes' %} vote-tab--active{% endif %}">Classes</a>
        <a href="{% url 'classes:admin_settings_hub' %}" class="vote-tab{% if active_tab in settings_tabs.split %} vote-tab--active{% endif %}">Settings</a>
        {% endwith %}
    </nav>
    {% block tab_content %}{% endblock %}
</div>
{% endblock %}
```

- [ ] **Step 4: Run the specs to verify they pass**

Run: `pytest classes/spec/views/admin_nav_spec.py -q`
Expected: PASS.

- [ ] **Step 5: Run the broader admin spec suite to catch nav-coupled assertions**

Run: `pytest classes/spec/views/ -q`
Expected: PASS. If any existing spec asserted the literal old tab labels (e.g. a "Registrations" tab link in the nav), update that spec to reflect the new structure — the tab is intentionally gone; its page is still reachable by URL. Note any such change in the commit message.

- [ ] **Step 6: Commit**

```bash
git add templates/classes/admin/base.html classes/spec/views/admin_nav_spec.py
git commit -m "[classes] Slim admin nav to Overview/Classes/Settings + live-catalog link"
```

---

## Task 4: Repoint entry points + clean stale copy

**Files:**
- Modify: `templates/classes/public/list.html` (admin CTA → overview)
- Modify: `templates/classes/admin/activity.html` (drop stale "Registrations tab" comment)

- [ ] **Step 1: Point the admin "Manage" CTA at Overview**

In `templates/classes/public/list.html`, change:

```django
      <a class="cta-secondary" href="{% url 'classes:admin_classes' %}">Manage Classes &amp; Workshops</a>
```

to:

```django
      <a class="cta-secondary" href="{% url 'classes:admin_overview' %}">Manage Classes &amp; Workshops</a>
```

- [ ] **Step 2: Remove the stale comment in `activity.html`**

Open `templates/classes/admin/activity.html`. Find the comment block (near the top) that reads:

```
For the registrations-only management table with email tools, use the Registrations tab.
```

Replace that sentence with:

```
For the registrations-only management table with email tools, open Recent sign-ups → "View all registrations" on the Overview.
```

- [ ] **Step 3: Verify nothing else hardcodes the old admin root path**

Run: `grep -rn "classes/admin/\"" templates/ classes/ | grep -v "admin/settings\|admin/classes\|admin/categories\|admin/discount\|admin/questions\|admin/registrations\|admin/activity"`
Expected: no results that assume `/classes/admin/` is the classes *list* (it is now the Overview). If any are found, repoint them to the correct named URL.

- [ ] **Step 4: Commit**

```bash
git add templates/classes/public/list.html templates/classes/admin/activity.html
git commit -m "[classes] Repoint admin CTA to Overview; clear stale activity copy"
```

---

## Task 5: Full verification

- [ ] **Step 1: Run the entire test suite**

Run: `pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 2: Coverage on the touched code**

Run: `pytest classes/spec/views/admin_overview_spec.py classes/spec/views/admin_settings_hub_spec.py classes/spec/views/admin_nav_spec.py --cov=classes.views --cov-report=term-missing -q`
Expected: the new `admin_overview` and `admin_settings_hub` lines are covered. Add specs for any uncovered branch (e.g. the empty-state `{% empty %}` paths are template-only; the view's `or 0` fallback needs a no-registration case — already covered by `it_renders_for_admin` with an empty DB).

- [ ] **Step 3: Lint + format + type-check**

Run: `ruff format . && ruff check --fix . && mypy .`
Expected: clean. Fix anything reported.

- [ ] **Step 4: Commit any fixups**

```bash
git add -A
git commit -m "[classes] Lint/type fixups for admin Overview phase"
```

---

## Task 6: Version bump + changelog

**Files:**
- Modify: `plfog/version.py`

- [ ] **Step 1: Bump version and add a member-friendly changelog entry**

Read `plfog/version.py`. Set `VERSION = "2.6.0"` (this redesign is a feature beyond the in-flight 2.5.0 release — see the coordination note in the handoff). Prepend a new entry to the top of the `CHANGELOG` list, matching the existing entry shape:

```python
    {
        "version": "2.6.0",
        "date": "2026-06-08",
        "summary": "A cleaner home for managing classes & workshops",
        "changes": [
            "Admins now land on a new Overview page showing classes waiting for approval, recent sign-ups, and recent activity at a glance.",
            "The class management area was simplified to three sections — Overview, Classes, and Settings.",
            "Added a quick link from the management area to the public class catalog.",
        ],
    },
```

(Match the exact keys/structure of the existing first entry in `CHANGELOG`; adjust key names if they differ.)

- [ ] **Step 2: Commit**

```bash
git add plfog/version.py
git commit -m "[release] Bump to 2.6.0 — classes management Overview"
```

---

## Self-Review notes (for the executor)

- **Spec coverage:** Overview (Task 1) covers approvals, waitlist, recent registrations + "view all", activity + "view full log", stats, 14-day series, and gating. Settings hub (Task 2) covers gating, links, and the waivers-form move. Nav (Task 3) covers the 3 tabs, dropped tabs, and the catalog link.
- **Type consistency:** the view returns context keys `pending_classes`, `waitlist_classes`, `recent_registrations`, `recent_activity`, `reg_by_day`, `reg_by_day_max`, `stats` — the template (Step 6) and the stats specs reference exactly these names.
- **No deletions:** `admin_classes`, `admin_registrations`, `admin_activity`, `admin_categories`, `admin_discount_codes`, `admin_registration_questions`, `admin_settings` all keep their view + URL **name**; only two paths move (`admin_classes` → `/admin/classes/`, `admin_settings` → `/admin/settings/waivers/`).
- **Known follow-up risk:** if any existing spec asserts the literal old nav (e.g. a "Registrations" tab anchor), Task 3 Step 5 catches and updates it.
