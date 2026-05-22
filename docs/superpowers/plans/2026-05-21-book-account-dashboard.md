# book.pastlives.space `/account/` Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a public-surface `/account/` dashboard at `book.pastlives.space` covering overview, history, receipts, profile, themed allauth pages, an onboarding wizard, and a guest booking lookup — implementing the design bundle exported from Claude Design (pl-cms).

**Architecture:** Extend the existing `classes` app with new persona-aware views under `/account/`. Re-theme allauth on the public surface by branching on `request.surface` in a shared auth base template. Add a `UserProfile` model in `core` for onboarding answers (preferred name, pronouns, day-of phone, referral source, interests, first-attendance status). Add a human-readable `order_number` to `Registration` so guest lookup by last-name + order-number can work without a login. The four personas (anon, nonmember, member, instructor) are derived once per request by a new context processor and drive both topbar variants and dashboard subtleties.

**Tech Stack:** Django 5.x, allauth (passwordless email-code), pytest-describe BDD, factory-boy, ruff, mypy, django-guardian (existing). No new dependencies.

**Design source:** `/tmp/anthropic_design/pl-cms/` — read `project/account-chrome.jsx`, `account-screens.jsx`, and `styles.css` for the exact visual vocabulary. CSS tokens already match `templates/classes/base_public.html`.

**Reference patterns:**
- Fat-models/skinny-views: see `membership/models.py` for shape; views call model methods only.
- Forms own validation: see `core/forms.py` and `classes/forms.py`.
- BDD tests: see `tests/classes/` — `describe_X/context_Y/it_Z` in `*_spec.py`.
- factory-boy: see `classes/factories.py`.

---

## Out of scope

- The `/classes/*` catalog pages themselves are not redesigned (brief is explicit).
- The member dashboard at `members.pastlives.space` is not touched.
- Discord release-notification workflow is already in place — Phase 9 only bumps `plfog/version.py`.
- No new third-party integrations. (The cover-slide mention of "Mailchimp" and "SimplyBook" refers to integrations that already exist in the codebase — `classes/tasks.py` for Mailchimp, plus existing SimplyBook bridge. Do not add new code there.)

## Sequencing & commit cadence

Phases are committable independently. Land them in order; each phase keeps the tree green. Phase 1 (host-based surface routing) is already merged on this branch (commit `aa8f24d`).

| Phase | What | Why first |
|-------|------|-----------|
| 1 ✅ | Surface routing middleware | Done — `core/middleware.py` tags `request.surface` |
| 2 | Persona context processor + topbar evolution | Foundation: every later phase reads `request.persona` |
| 3 | `/account/` shell + URL routing + 404 protection | URL skeleton before content |
| 4 | Overview + History pages (read-only) | Most-visited, lowest risk |
| 5 | Receipts page (read-only) | Reads existing `Registration.amount_paid_cents` |
| 6 | Profile page (editable for non-members, read-only for members) | Mutates data — needs the form layer |
| 7 | `order_number` + guest lookup flow | New field, new view, link from auth |
| 8 | Themed allauth on public surface (login/signup/code) | Stop redirecting `/accounts/*` on book; theme three templates |
| 9 | Onboarding wizard (3 steps, post-signup) | New model + signal hook |
| 10 | Version bump + changelog + manual QA | Release tag |

---

## Decisions locked in

1. **Where the new code lives.** `/account/` views, URLs, forms, and templates live inside the existing `classes/` app — every page reads from `classes.Registration`, so keeping it co-located respects fat-models and avoids cross-app churn. Templates go under `templates/classes/account/`. Tests under `tests/classes/account/`.

2. **`UserProfile` lives in `core/`.** Onboarding answers (preferred_name, pronouns, phone, referral, interests, first-attendance status, onboarding_completed_at) are per-User and surface-agnostic; `core/` is the right home. The model is a `OneToOneField(User)` with `related_name="profile"`. Members already have a `Member` record with richer fields; `UserProfile` is a lightweight per-user record for everyone (members, non-members, instructors). For members the profile page is read-only and routes them to FOG.

3. **Persona derivation.** A single context processor `core.context_processors.persona` returns `{"persona": "anon"|"nonmember"|"member"|"instructor"}` and attaches the matching booleans (`is_member_persona`, `is_instructor_persona`, etc.). Derived once per request, cached on `request._persona`. Members who are *also* instructors get `persona = "member"` because the topbar Member pill is the more important signal; the instructor banner inside `/account/` shows regardless.

4. **`/account/` on the members host.** A new `PUBLIC_ONLY_PATH_PREFIXES` tuple in settings lets the middleware 302-redirect `/account/*` requests on the members host to the book host. Symmetry with the existing `MEMBER_ONLY_PATH_PREFIXES`.

5. **Allauth on book.** Stop the `/accounts/*` cross-host redirect for the login / signup / code-confirm pages. Cookies already scope to `.pastlives.space` (`SESSION_COOKIE_DOMAIN` in settings.py:49) so a login completed on book is recognized on members. The three allauth templates branch chrome on `is_public_surface`.

6. **Order number format.** `PL-XXXX-YY` where `XXXX` is 4 uppercase alphanumerics (no `0`, `O`, `I`, `1` to avoid confusion) and `YY` is the last two digits of the registration year. Generated on save when missing; unique constraint.

7. **No sidebar — pill tabs.** Per design + brief. Implemented as a horizontal scrollable row under the page H1. Active tab uses the gold pill style.

8. **Soft member nudge.** Appears once on the non-member overview only, dismissable via a cookie (`pl_nudge_dismissed=1`, 180-day expiry). Never on history, receipts, or profile.

---

## File structure

### Create

```
classes/account/                          # new submodule
  __init__.py
  views.py                                # OverviewView, HistoryView, ReceiptsView,
                                          #   ProfileView, LookupView, OnboardingStep{1,2,3}View
  forms.py                                # AccountProfileForm, LookupForm, OnboardingStep{1,2,3}Form
  selectors.py                            # upcoming_registrations(user), past_registrations(user),
                                          #   paid_registrations(user), persona_for(user)
  urls.py                                 # included from classes/urls.py under prefix "account/"

core/models.py (extend) — add UserProfile
core/migrations/00XX_userprofile.py
classes/migrations/00XX_registration_order_number.py

templates/classes/account/
  base.html                               # extends classes/base_public.html
                                          # — adds pill-tabs strip + H1 row
  overview.html
  history.html
  receipts.html
  profile.html
  lookup.html                             # supports 3 states: form / result / notfound (one template, branched)
  onboarding/
    step1.html
    step2.html
    step3.html
  _components/
    class_card.html                       # one upcoming/past card
    section_h.html                        # section heading with count + right link
    empty_state.html
    member_banner.html
    instructor_banner.html
    member_nudge.html
    readonly_banner.html
    receipt_row.html

templates/account/                        # allauth overrides — re-themed for surface
  login.html                              # overwritten
  signup.html                             # overwritten
  confirm_login_code.html                 # overwritten

static/css/book-account.css               # ~860 lines ported from pl-cms styles.css

tests/classes/account/
  __init__.py
  overview_spec.py
  history_spec.py
  receipts_spec.py
  profile_spec.py
  lookup_spec.py
  onboarding_spec.py
  order_number_spec.py
  persona_selector_spec.py
tests/core/
  persona_context_processor_spec.py
  user_profile_spec.py
  public_only_middleware_spec.py
```

### Modify

| File | Why |
|------|-----|
| `core/middleware.py` | Add `PUBLIC_ONLY_PATH_PREFIXES` handling (redirect `/account/*` from members to book). Remove the unconditional `/accounts/*` redirect; let allauth run on both surfaces. |
| `core/context_processors.py` | Add `persona(request)` processor. |
| `plfog/settings.py` | Register `core.context_processors.persona`; add `PUBLIC_ONLY_PATH_PREFIXES`. |
| `classes/urls.py` | `path("account/", include("classes.account.urls", namespace="account"))` — wait, classes.urls is already namespaced. Use `include(("classes.account.urls", "account"), namespace="account")` so reverse names are `account:overview`, etc. |
| `classes/models.py` | Add `order_number` field + generator on `Registration`. |
| `templates/hub/base.html` | Evolve `pl-public-topbar` to render four persona variants. |
| `static/css/...` (existing public topbar CSS) | Add `.pl-public-topbar__pill`, `.pl-public-topbar__ext`, `.pl-public-topbar__avatar` variants. |
| `plfog/version.py` | Bump to 2.0.0 final / bump CHANGELOG. |

### Untouched

- `classes/views.py` registration / checkout flow — read by selectors, never written by `/account/` (except profile mutations which don't touch Registration).
- `airtable_sync/` — UserProfile is not synced to Airtable in v2.0.
- `billing/` — receipts come from `Registration.stripe_payment_id` / `amount_paid_cents`, not the Tab system.

---

# Phase 2: Persona context processor + topbar evolution

**Files:**
- Create: `tests/core/persona_context_processor_spec.py`
- Modify: `core/context_processors.py:25-34`, `plfog/settings.py:129-141`
- Modify: `templates/hub/base.html:365-378`
- Modify: existing public topbar CSS (find the `.pl-public-topbar` block under `static/`)

### Task 2.1: Persona selector function

- [ ] **Step 1: Write the failing test**

```python
# tests/core/persona_context_processor_spec.py
import pytest
from django.contrib.auth.models import AnonymousUser
from core.context_processors import persona
from membership.factories import MemberFactory
from classes.factories import InstructorFactory

def describe_persona():
    def it_returns_anon_for_anonymous_user(rf):
        req = rf.get("/")
        req.user = AnonymousUser()
        assert persona(req) == {"persona": "anon", "is_member_persona": False, "is_instructor_persona": False}

    def it_returns_nonmember_for_plain_authed_user(rf, db, django_user_model):
        user = django_user_model.objects.create_user(email="a@b.com", username="a@b.com")
        req = rf.get("/")
        req.user = user
        result = persona(req)
        assert result["persona"] == "nonmember"

    def it_returns_member_for_user_linked_to_active_member(rf, db):
        member = MemberFactory(status="active")
        req = rf.get("/")
        req.user = member.user
        assert persona(req)["persona"] == "member"
        assert persona(req)["is_member_persona"] is True

    def it_returns_instructor_for_user_linked_to_instructor_no_member(rf, db, django_user_model):
        instructor = InstructorFactory()
        req = rf.get("/")
        req.user = instructor.user
        assert persona(req)["persona"] == "instructor"

    def context_when_user_is_both_member_and_instructor():
        def it_prefers_member_persona(rf, db):
            member = MemberFactory(status="active")
            InstructorFactory(user=member.user)
            req = rf.get("/")
            req.user = member.user
            result = persona(req)
            assert result["persona"] == "member"
            assert result["is_instructor_persona"] is True  # instructor flag still true

    def it_caches_result_on_request(rf, db):
        member = MemberFactory(status="active")
        req = rf.get("/")
        req.user = member.user
        first = persona(req)
        member.user.member.status = "inactive"
        member.user.member.save()
        second = persona(req)  # should return cached
        assert second == first
```

Add `rf` fixture (RequestFactory) if not already in conftest:

```python
# tests/conftest.py — add if missing
import pytest
from django.test import RequestFactory

@pytest.fixture
def rf():
    return RequestFactory()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/core/persona_context_processor_spec.py -v`
Expected: ImportError on `persona`.

- [ ] **Step 3: Implement persona processor**

Edit `core/context_processors.py`, add at the bottom:

```python
def persona(request: HttpRequest) -> dict[str, str | bool]:
    """Derive the active persona for the current request.

    Returns a single string in {"anon", "nonmember", "member", "instructor"} plus
    convenience booleans so templates can render banners and topbar variants
    without re-deriving. Cached on the request so it's safe to call from both
    the context processor and view code.
    """
    cached = getattr(request, "_persona", None)
    if cached is not None:
        return cached

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        result = {"persona": "anon", "is_member_persona": False, "is_instructor_persona": False}
        request._persona = result
        return result

    member = getattr(user, "member", None)
    is_active_member = bool(member and getattr(member, "status", None) == "active")
    is_instructor = hasattr(user, "instructor")

    if is_active_member:
        slug = "member"
    elif is_instructor:
        slug = "instructor"
    else:
        slug = "nonmember"

    result = {
        "persona": slug,
        "is_member_persona": is_active_member,
        "is_instructor_persona": is_instructor,
    }
    request._persona = result
    return result
```

- [ ] **Step 4: Register in settings**

Edit `plfog/settings.py:137` (after the existing `surface` line):

```python
                "core.context_processors.surface",
                "core.context_processors.persona",
                "billing.context_processors.tab_context",
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/core/persona_context_processor_spec.py -v`
Expected: 6 passing.

- [ ] **Step 6: Commit**

```bash
git add core/context_processors.py plfog/settings.py tests/core/persona_context_processor_spec.py
git commit -m "Persona context processor for book surface topbar"
```

### Task 2.2: Evolve the public topbar template

- [ ] **Step 1: Write the failing test**

```python
# tests/classes/account/topbar_spec.py
import pytest
from django.test import Client
from membership.factories import MemberFactory
from classes.factories import InstructorFactory

@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    c = Client(HTTP_HOST="book.pastlives.space")
    return c

def describe_public_topbar():
    def it_shows_log_in_for_anonymous_user(book_client, db):
        resp = book_client.get("/classes/")
        assert resp.status_code == 200
        assert b">Log in<" in resp.content
        assert b"My account" not in resp.content

    def it_shows_my_account_and_log_out_for_nonmember(book_client, db, django_user_model):
        user = django_user_model.objects.create_user(email="x@y.com", username="x@y.com")
        book_client.force_login(user)
        resp = book_client.get("/classes/")
        assert b"My account" in resp.content
        assert b"Log out" in resp.content
        assert b"Member" not in resp.content  # no member pill

    def it_shows_fog_link_and_member_pill_for_active_member(book_client, db):
        member = MemberFactory(status="active")
        book_client.force_login(member.user)
        resp = book_client.get("/classes/")
        assert b"My account" in resp.content
        assert b">FOG<" in resp.content
        assert b'class="pl-public-topbar__pill"' in resp.content

    def it_shows_my_account_no_pill_for_instructor_only(book_client, db):
        inst = InstructorFactory()
        book_client.force_login(inst.user)
        resp = book_client.get("/classes/")
        assert b"My account" in resp.content
        assert b">FOG<" not in resp.content
        assert b'class="pl-public-topbar__pill"' not in resp.content
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/classes/account/topbar_spec.py -v`
Expected: most assertions fail on the current single-variant topbar.

- [ ] **Step 3: Replace the topbar in `templates/hub/base.html:365-378`**

```django
<header class="pl-public-topbar">
    <a href="{% url 'classes:public_list' %}" class="pl-public-topbar__brand">
        <img src="{% static 'img/favicon.png' %}" alt="" width="22" height="22">
        <span>Past Lives <em>Makerspace</em></span>
    </a>
    <nav class="pl-public-topbar__nav" aria-label="Public navigation">
        <a href="{% url 'classes:public_list' %}" class="pl-public-topbar__link">Classes</a>
        {% if user.is_authenticated %}
            <a href="{% url 'account:overview' %}" class="pl-public-topbar__link">My account</a>
            {% if persona == "member" %}
                <a href="https://{{ MEMBER_HOST }}/" class="pl-public-topbar__ext" title="Member dashboard">
                    FOG
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                        <path d="M7 17 L17 7"/><path d="M8 7h9v9"/>
                    </svg>
                </a>
                <span class="pl-public-topbar__pill" title="Active member">Member</span>
            {% endif %}
            <a href="{% url 'account_logout' %}" class="pl-public-topbar__logout" hx-boost="false">Log out</a>
            <span class="pl-public-topbar__avatar" aria-hidden="true">{{ user.email|slice:":2"|upper }}</span>
        {% else %}
            <a href="{% url 'account_login' %}" class="pl-public-topbar__login" hx-boost="false">Log in</a>
        {% endif %}
    </nav>
</header>
```

`MEMBER_HOST` template variable: expose it via the existing `surface` context processor — extend it:

```python
# core/context_processors.py — extend the surface() function
def surface(request: HttpRequest) -> dict[str, str | bool]:
    value = getattr(request, "surface", "members")
    return {
        "surface": value,
        "is_public_surface": value == "public",
        "MEMBER_HOST": settings.MEMBER_HOST,
        "PUBLIC_HOST": (settings.PUBLIC_HOSTS or ["book.pastlives.space"])[0],
    }
```

Add `from django.conf import settings` at the top of `core/context_processors.py` if it isn't already imported.

- [ ] **Step 4: Add CSS variants**

The existing public topbar CSS lives in a static file — find it with:
```bash
grep -rn "pl-public-topbar" static/ templates/
```

Add the new classes near the existing block (matching the design tokens from `pl-cms/project/styles.css:106-122`):

```css
.pl-public-topbar__ext {
    color: var(--gold, #eeb44b);
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .09em;
    padding: 8px 14px;
    border-radius: 4px;
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    gap: 4px;
}
.pl-public-topbar__ext:hover { background: rgba(238,180,75,.08); }

.pl-public-topbar__pill {
    font-size: 9px;
    font-weight: 800;
    letter-spacing: .14em;
    text-transform: uppercase;
    padding: 3px 8px;
    border-radius: 999px;
    background: rgba(238,180,75,.12);
    border: 1px solid rgba(238,180,75,.4);
    color: var(--gold, #eeb44b);
}

.pl-public-topbar__avatar {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: linear-gradient(135deg, #0d3a5e, #092e4c);
    border: 1px solid rgba(238,180,75,.35);
    color: var(--gold, #eeb44b);
    font-family: 'Playfair Display', Georgia, serif;
    font-weight: 800;
    font-size: 13px;
}

.pl-public-topbar__logout {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .09em;
    padding: 7px 14px;
    border-radius: 4px;
    border: 1px solid rgba(26,69,104,.6);
    background: rgba(255,255,255,.02);
    color: #96acbb;
    text-decoration: none;
}
.pl-public-topbar__logout:hover { border-color: rgba(238,180,75,.35); color: #f4efdd; }
```

- [ ] **Step 5: Stub `account:overview` URL so the template reverse doesn't crash**

Create `classes/account/__init__.py` (empty) and `classes/account/urls.py`:

```python
# classes/account/urls.py
from __future__ import annotations
from django.urls import path
from django.views.generic import TemplateView

app_name = "account"

urlpatterns = [
    path("", TemplateView.as_view(template_name="classes/account/overview.html"), name="overview"),
]
```

Create an empty placeholder `templates/classes/account/overview.html`:

```django
{% extends "classes/base_public.html" %}
{% block portal_content %}<p>Account dashboard — coming soon.</p>{% endblock %}
```

Wire into `classes/urls.py` — add at the end of the imports / url list:

```python
# classes/urls.py — add to the imports
from django.urls import include
# add to urlpatterns (before the catch-all if any):
    path("../account/", include("classes.account.urls", namespace="account")),
```

Wait — `/classes/account/` is wrong; the brief calls for `/account/` at the root. Wire it from `plfog/urls.py` instead:

```python
# plfog/urls.py — add this line after the classes include
    path("account/", include("classes.account.urls", namespace="account")),
```

- [ ] **Step 6: Run topbar tests**

Run: `pytest tests/classes/account/topbar_spec.py -v`
Expected: all 4 passing.

- [ ] **Step 7: Commit**

```bash
git add templates/hub/base.html static/ core/context_processors.py \
        classes/account/ templates/classes/account/overview.html \
        plfog/urls.py tests/classes/account/topbar_spec.py
git commit -m "Public topbar: 4-persona variants with FOG link and Member pill"
```

---

# Phase 3: `/account/` URL skeleton + `PUBLIC_ONLY_PATH_PREFIXES`

**Files:**
- Modify: `core/middleware.py:51-74`, `plfog/settings.py` (new tuple), `classes/account/urls.py`
- Test: `tests/core/public_only_middleware_spec.py`

### Task 3.1: Public-only redirect (members → book for /account/)

- [ ] **Step 1: Write the failing test**

```python
# tests/core/public_only_middleware_spec.py
import pytest
from django.test import Client

@pytest.fixture(autouse=True)
def _surface_hosts(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    settings.MEMBER_HOST = "members.pastlives.space"
    settings.PUBLIC_ONLY_PATH_PREFIXES = ("/account/",)

def describe_public_only_redirect():
    def it_redirects_account_to_book_when_on_members_host(db):
        c = Client(HTTP_HOST="members.pastlives.space")
        resp = c.get("/account/")
        assert resp.status_code == 302
        assert resp["Location"].startswith("http://book.pastlives.space/account/")

    def it_does_not_redirect_account_when_on_book_host(db):
        c = Client(HTTP_HOST="book.pastlives.space")
        resp = c.get("/account/")
        # could be 200 or 302 to login, but NOT a cross-host redirect
        assert "members.pastlives.space" not in resp.get("Location", "")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/core/public_only_middleware_spec.py -v`

- [ ] **Step 3: Extend middleware**

Edit `core/middleware.py` — add a new method and a call from `__call__`:

```python
def __call__(self, request):
    # ... existing surface tagging ...
    if request.surface == "public":
        short_circuit = self._handle_public_surface(request)
        if short_circuit is not None:
            return short_circuit
    else:
        short_circuit = self._handle_members_surface(request)
        if short_circuit is not None:
            return short_circuit
    return self.get_response(request)

def _handle_members_surface(self, request):
    public_only_prefixes = tuple(getattr(settings, "PUBLIC_ONLY_PATH_PREFIXES", ()))
    public_hosts = list(getattr(settings, "PUBLIC_HOSTS", []))
    if not public_hosts:
        return None
    book_host = public_hosts[0]
    for prefix in public_only_prefixes:
        if request.path.startswith(prefix):
            scheme = "https" if request.is_secure() else "http"
            query = f"?{request.META['QUERY_STRING']}" if request.META.get("QUERY_STRING") else ""
            return HttpResponseRedirect(f"{scheme}://{book_host}{request.path}{query}")
    return None
```

- [ ] **Step 4: Add `PUBLIC_ONLY_PATH_PREFIXES` to settings**

Edit `plfog/settings.py`, after `MEMBER_ONLY_PATH_PREFIXES` (around line 74):

```python
# Paths that only exist on the public/book surface. Hits to these on the
# members host get 302-redirected to the book host.
PUBLIC_ONLY_PATH_PREFIXES: tuple[str, ...] = (
    "/account/",
)
```

- [ ] **Step 5: Verify**

Run: `pytest tests/core/public_only_middleware_spec.py tests/core/surface_middleware_spec.py -v`
Expected: new tests pass, existing surface tests still pass.

- [ ] **Step 6: Commit**

```bash
git add core/middleware.py plfog/settings.py tests/core/public_only_middleware_spec.py
git commit -m "PUBLIC_ONLY_PATH_PREFIXES redirects /account/ to book"
```

### Task 3.2: Build the account base template

- [ ] **Step 1: Port the design CSS**

Create `static/css/book-account.css`. Copy the contents of `/tmp/anthropic_design/pl-cms/project/styles.css` — strip the `:root` block (the variables already live in `base_public.html` under `.cp-page`); keep everything from `.bk` onwards. Replace the prefix `.bk` with `.bk` (no rename needed) but namespace the body styling so it doesn't clash with the design canvas backdrop. The canvas-only rules (`.bk-note`, body styles for the backdrop) can be dropped.

The final file has only the styles needed for: topbar variants (already in topbar CSS), page wrapper `.bk-page`, tabs `.bk-tabs`, cards `.bk-card*`, empty state `.bk-empty*`, receipts `.bk-receipts`/`.bk-rec-*`, forms `.bk-form`/`.bk-field*`, emails `.bk-emails`/`.bk-email-*`, banners `.bk-banner*`/`.bk-readonly-banner`/`.bk-nudge*`, auth `.bk-auth*`/`.bk-code`, onboarding `.bk-onb*`/`.bk-radio*`/`.bk-chips`/`.bk-chip*`, lookup `.bk-lookup-*`.

- [ ] **Step 2: Write the base template**

Create `templates/classes/account/base.html`:

```django
{% extends "classes/base_public.html" %}
{% load static %}

{% block classes_extra_head %}
<link rel="stylesheet" href="{% static 'css/book-account.css' %}">
{% endblock %}

{% block portal_content %}
<div class="bk-page">
  <div class="bk-container">
    {% block account_hero %}
      <div class="bk-h1-row">
        <div>
          <div class="bk-eyebrow">{% block account_eyebrow %}My account{% endblock %}</div>
          <h1 class="bk-h1">{% block account_title %}{% endblock %}</h1>
          {% block account_sub %}{% endblock %}
        </div>
      </div>
    {% endblock %}

    {% block account_tabs %}
    <div class="bk-tabs" role="tablist">
      <a href="{% url 'account:overview' %}"
         class="bk-tab {% if active_tab == 'overview' %}is-on{% endif %}">Upcoming</a>
      <a href="{% url 'account:history' %}"
         class="bk-tab {% if active_tab == 'history' %}is-on{% endif %}">Past classes</a>
      <a href="{% url 'account:receipts' %}"
         class="bk-tab {% if active_tab == 'receipts' %}is-on{% endif %}">Receipts</a>
      <a href="{% url 'account:profile' %}"
         class="bk-tab {% if active_tab == 'profile' %}is-on{% endif %}">Profile</a>
    </div>
    {% endblock %}

    {% block account_body %}{% endblock %}
  </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Stub the four routes**

Replace `classes/account/urls.py`:

```python
from __future__ import annotations
from django.urls import path
from classes.account import views

app_name = "account"

urlpatterns = [
    path("", views.OverviewView.as_view(), name="overview"),
    path("history/", views.HistoryView.as_view(), name="history"),
    path("receipts/", views.ReceiptsView.as_view(), name="receipts"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("lookup/", views.LookupView.as_view(), name="lookup"),
]
```

Create `classes/account/views.py` with login-required stubs:

```python
from __future__ import annotations
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView, FormView, View


class _LoggedInAccountView(LoginRequiredMixin, TemplateView):
    login_url = "/accounts/login/"
    active_tab: str = ""

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["active_tab"] = self.active_tab
        return ctx


class OverviewView(_LoggedInAccountView):
    template_name = "classes/account/overview.html"
    active_tab = "overview"


class HistoryView(_LoggedInAccountView):
    template_name = "classes/account/history.html"
    active_tab = "history"


class ReceiptsView(_LoggedInAccountView):
    template_name = "classes/account/receipts.html"
    active_tab = "receipts"


class ProfileView(_LoggedInAccountView):
    template_name = "classes/account/profile.html"
    active_tab = "profile"


class LookupView(TemplateView):
    template_name = "classes/account/lookup.html"
```

Create empty stub templates for each (e.g. `overview.html` extends `classes/account/base.html` with `{% block account_title %}Overview{% endblock %}` etc.). One per route.

- [ ] **Step 4: Skeleton routes smoke test**

```python
# tests/classes/account/skeleton_spec.py
import pytest
from django.test import Client
from membership.factories import MemberFactory

@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    return Client(HTTP_HOST="book.pastlives.space")

def describe_account_routes():
    def it_redirects_anonymous_to_login(book_client, db):
        for url in ["/account/", "/account/history/", "/account/receipts/", "/account/profile/"]:
            resp = book_client.get(url)
            assert resp.status_code == 302
            assert "/accounts/login/" in resp["Location"]

    def it_serves_logged_in_user_each_tab(book_client, db):
        member = MemberFactory(status="active")
        book_client.force_login(member.user)
        for url in ["/account/", "/account/history/", "/account/receipts/", "/account/profile/"]:
            resp = book_client.get(url)
            assert resp.status_code == 200, f"{url} returned {resp.status_code}"

    def it_serves_lookup_to_anonymous(book_client, db):
        resp = book_client.get("/account/lookup/")
        assert resp.status_code == 200
```

Run: `pytest tests/classes/account/skeleton_spec.py -v` — all pass.

- [ ] **Step 5: Commit**

```bash
git add classes/account/ templates/classes/account/ static/css/book-account.css \
        tests/classes/account/
git commit -m "Account dashboard skeleton: base template, tabs, 4 routes + lookup"
```

---

# Phase 4: Overview + History pages

**Files:**
- Modify: `classes/account/views.py`, `classes/account/selectors.py` (new)
- Modify: `templates/classes/account/overview.html`, `history.html`, plus partials
- Test: `tests/classes/account/overview_spec.py`, `history_spec.py`

### Task 4.1: Selectors for upcoming and past registrations

- [ ] **Step 1: Write the failing test**

```python
# tests/classes/account/selectors_spec.py
import pytest
from django.utils import timezone
from datetime import timedelta
from classes.factories import ClassOfferingFactory, ClassSessionFactory, RegistrationFactory
from classes.account.selectors import upcoming_registrations, past_registrations
from membership.factories import MemberFactory

def describe_upcoming_registrations():
    def it_returns_confirmed_registrations_with_future_sessions(db):
        member = MemberFactory(status="active")
        offering = ClassOfferingFactory(status="published")
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=7))
        reg = RegistrationFactory(
            email=member.user.email, member=member, class_offering=offering,
            status="confirmed",
        )
        result = upcoming_registrations(member.user)
        assert list(result) == [reg]

    def it_excludes_past_sessions(db):
        member = MemberFactory(status="active")
        offering = ClassOfferingFactory(status="published")
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now() - timedelta(days=7))
        RegistrationFactory(email=member.user.email, member=member, class_offering=offering, status="confirmed")
        assert upcoming_registrations(member.user).count() == 0

    def it_includes_waitlisted(db):
        member = MemberFactory(status="active")
        offering = ClassOfferingFactory(status="published")
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=7))
        RegistrationFactory(email=member.user.email, member=member, class_offering=offering, status="waitlisted")
        assert upcoming_registrations(member.user).count() == 1

    def it_excludes_cancelled(db):
        member = MemberFactory(status="active")
        offering = ClassOfferingFactory(status="published")
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=7))
        RegistrationFactory(email=member.user.email, member=member, class_offering=offering, status="cancelled")
        assert upcoming_registrations(member.user).count() == 0

    def it_matches_by_email_when_member_link_missing(db, django_user_model):
        user = django_user_model.objects.create_user(email="g@h.com", username="g@h.com")
        offering = ClassOfferingFactory(status="published")
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=7))
        RegistrationFactory(email="g@h.com", class_offering=offering, status="confirmed")
        assert upcoming_registrations(user).count() == 1


def describe_past_registrations():
    def it_returns_only_past_sessions(db):
        member = MemberFactory(status="active")
        offering_past = ClassOfferingFactory(status="published")
        ClassSessionFactory(class_offering=offering_past, starts_at=timezone.now() - timedelta(days=10))
        RegistrationFactory(email=member.user.email, member=member, class_offering=offering_past, status="confirmed")

        offering_future = ClassOfferingFactory(status="published")
        ClassSessionFactory(class_offering=offering_future, starts_at=timezone.now() + timedelta(days=10))
        RegistrationFactory(email=member.user.email, member=member, class_offering=offering_future, status="confirmed")

        assert past_registrations(member.user).count() == 1
```

- [ ] **Step 2: Run to verify failure**

`pytest tests/classes/account/selectors_spec.py -v` → ImportError.

- [ ] **Step 3: Implement selectors**

Create `classes/account/selectors.py`:

```python
"""Read-only queries for the /account/ dashboard.

These do not mutate; they just join Registration to ClassSession via
ClassOffering so the dashboard can show what a user is signed up for.
The user→registration link uses three paths in order: an explicit
Member.user link, a verified EmailAddress on the user, or the user's
own primary email — so guests who later sign up still find their old
bookings.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone

from classes.models import ClassSession, Registration

User = get_user_model()


def _emails_for(user) -> list[str]:
    """Every verified email plus the user's primary login email, lowercased."""
    emails: set[str] = {user.email.lower()} if user.email else set()
    # allauth EmailAddress
    if hasattr(user, "emailaddress_set"):
        for ea in user.emailaddress_set.filter(verified=True):
            emails.add(ea.email.lower())
    return sorted(emails)


def _registrations_for(user) -> QuerySet[Registration]:
    emails = _emails_for(user)
    member = getattr(user, "member", None)
    qs = Registration.objects.select_related("class_offering", "class_offering__instructor")
    if member is not None:
        return qs.filter(member=member) | qs.filter(email__in=emails)
    return qs.filter(email__in=emails)


def upcoming_registrations(user) -> QuerySet[Registration]:
    """Registrations with at least one future session, not cancelled/refunded."""
    now = timezone.now()
    future_sessions = ClassSession.objects.filter(
        class_offering=OuterRef("class_offering"), starts_at__gte=now,
    )
    return (
        _registrations_for(user)
        .filter(status__in=[Registration.Status.CONFIRMED, Registration.Status.WAITLISTED, Registration.Status.PENDING])
        .annotate(has_future=Exists(future_sessions))
        .filter(has_future=True)
        .distinct()
        .order_by("class_offering__sessions__starts_at")
    )


def past_registrations(user) -> QuerySet[Registration]:
    """Registrations whose last session is now in the past."""
    now = timezone.now()
    future_sessions = ClassSession.objects.filter(
        class_offering=OuterRef("class_offering"), starts_at__gte=now,
    )
    return (
        _registrations_for(user)
        .filter(status__in=[Registration.Status.CONFIRMED, Registration.Status.WAITLISTED])
        .annotate(has_future=Exists(future_sessions))
        .filter(has_future=False)
        .distinct()
        .order_by("-class_offering__sessions__starts_at")
    )


def paid_registrations(user) -> QuerySet[Registration]:
    """Registrations with a recorded Stripe payment — used for receipts."""
    return (
        _registrations_for(user)
        .exclude(stripe_payment_id="")
        .order_by("-confirmed_at")
    )
```

- [ ] **Step 4: Run tests, fix until green**

`pytest tests/classes/account/selectors_spec.py -v`

- [ ] **Step 5: Commit**

```bash
git add classes/account/selectors.py tests/classes/account/selectors_spec.py
git commit -m "Account dashboard selectors: upcoming, past, paid registrations"
```

### Task 4.2: Overview view + template

- [ ] **Step 1: Write the test**

```python
# tests/classes/account/overview_spec.py
import pytest
from datetime import timedelta
from django.test import Client
from django.utils import timezone
from membership.factories import MemberFactory
from classes.factories import ClassOfferingFactory, ClassSessionFactory, RegistrationFactory
from classes.factories import InstructorFactory

@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    return Client(HTTP_HOST="book.pastlives.space")

def describe_account_overview():
    def context_with_no_upcoming():
        def it_renders_empty_state_with_browse_cta(book_client, db, django_user_model):
            user = django_user_model.objects.create_user(email="x@y.com", username="x@y.com")
            book_client.force_login(user)
            resp = book_client.get("/account/")
            assert resp.status_code == 200
            assert b"No upcoming classes yet" in resp.content
            assert b"Browse classes" in resp.content

    def context_with_upcoming():
        def it_renders_each_class_card(book_client, db):
            member = MemberFactory(status="active")
            offering = ClassOfferingFactory(status="published", title="Forge a Hook")
            ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=5))
            RegistrationFactory(email=member.user.email, member=member,
                                class_offering=offering, status="confirmed")
            book_client.force_login(member.user)
            resp = book_client.get("/account/")
            assert b"Forge a Hook" in resp.content
            assert b"Registered" in resp.content

    def context_when_persona_is_member():
        def it_shows_the_member_banner(book_client, db):
            member = MemberFactory(status="active")
            book_client.force_login(member.user)
            resp = book_client.get("/account/")
            assert b"You're a Past Lives member" in resp.content
            assert b"Open FOG dashboard" in resp.content

    def context_when_persona_is_instructor():
        def it_shows_instructor_banner_with_count(book_client, db):
            inst = InstructorFactory()
            offering = ClassOfferingFactory(status="published", instructor=inst)
            ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=2))
            book_client.force_login(inst.user)
            resp = book_client.get("/account/")
            assert b"You&#x27;re teaching" in resp.content or b"You're teaching" in resp.content
            assert b"Teaching dashboard" in resp.content

    def context_when_persona_is_nonmember():
        def it_shows_the_soft_nudge_on_overview(book_client, db, django_user_model):
            user = django_user_model.objects.create_user(email="n@m.com", username="n@m.com")
            book_client.force_login(user)
            offering = ClassOfferingFactory(status="published")
            ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=2))
            RegistrationFactory(email="n@m.com", class_offering=offering, status="confirmed")
            resp = book_client.get("/account/")
            assert b"Membership opens the door" in resp.content

        def it_hides_the_nudge_when_cookie_set(book_client, db, django_user_model):
            user = django_user_model.objects.create_user(email="n2@m.com", username="n2@m.com")
            book_client.force_login(user)
            book_client.cookies["pl_nudge_dismissed"] = "1"
            offering = ClassOfferingFactory(status="published")
            ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=2))
            RegistrationFactory(email="n2@m.com", class_offering=offering, status="confirmed")
            resp = book_client.get("/account/")
            assert b"Membership opens the door" not in resp.content
```

- [ ] **Step 2: Implement the view**

Update `classes/account/views.py`:

```python
class OverviewView(_LoggedInAccountView):
    template_name = "classes/account/overview.html"
    active_tab = "overview"

    def get_context_data(self, **kwargs):
        from classes.account.selectors import upcoming_registrations
        ctx = super().get_context_data(**kwargs)
        ctx["upcoming"] = upcoming_registrations(self.request.user)
        ctx["instructor_upcoming_count"] = self._instructor_count()
        ctx["nudge_dismissed"] = self.request.COOKIES.get("pl_nudge_dismissed") == "1"
        return ctx

    def _instructor_count(self) -> int:
        from classes.models import ClassOffering, ClassSession
        from django.utils import timezone
        if not hasattr(self.request.user, "instructor"):
            return 0
        return (
            ClassOffering.objects
            .filter(instructor=self.request.user.instructor, status="published")
            .filter(sessions__starts_at__gte=timezone.now())
            .distinct()
            .count()
        )
```

- [ ] **Step 3: Write the template**

`templates/classes/account/overview.html`:

```django
{% extends "classes/account/base.html" %}
{% load humanize %}

{% block account_title %}
{% if persona == 'member' %}Welcome back, <em>{{ user.first_name|default:user.email }}</em>.
{% elif persona == 'instructor' %}Welcome back, <em>{{ user.first_name|default:user.email }}</em>.
{% else %}Welcome back, <em>{{ user.first_name|default:user.email }}</em>.{% endif %}
{% endblock %}

{% block account_sub %}
  <p class="bk-sub">
    {% if persona == 'member' %}Your upcoming classes. The rest of your membership — tab, voting, the works — lives on FOG.
    {% elif persona == 'instructor' %}Classes you've registered for. Your teaching tools stay on FOG.
    {% else %}Your upcoming Past Lives classes and workshops, in one place.{% endif %}
  </p>
{% endblock %}

{% block account_body %}
  {% if persona == 'instructor' and instructor_upcoming_count %}
    {% include "classes/account/_components/instructor_banner.html" with count=instructor_upcoming_count %}
  {% endif %}
  {% if persona == 'member' %}
    {% include "classes/account/_components/member_banner.html" %}
  {% endif %}

  {% if not upcoming %}
    {% include "classes/account/_components/empty_state.html" with glyph="✶" title="No upcoming classes yet." body="Browse the Makerspace — blacksmithing, lampworking, ceramics, framing, jewelry, and more on the schedule." cta="Browse classes" cta_url="/classes/" %}
  {% else %}
    <div class="bk-section-h">
      <h2>Upcoming</h2>
      <span class="ct">{{ upcoming|length }} {{ upcoming|length|pluralize:"item,items" }}</span>
    </div>
    {% for reg in upcoming %}
      {% include "classes/account/_components/class_card.html" with reg=reg %}
    {% endfor %}
  {% endif %}

  {% if persona == 'nonmember' and upcoming and not nudge_dismissed %}
    {% include "classes/account/_components/member_nudge.html" %}
  {% endif %}
{% endblock %}
```

Create the four partials in `templates/classes/account/_components/`:

`class_card.html`:
```django
<article class="bk-card">
  <div class="bk-card-thumb">{{ reg.class_offering.category.name|slice:":2"|upper }}</div>
  <div class="bk-card-body">
    <div class="bk-card-title">{{ reg.class_offering.title }}</div>
    <div class="bk-card-inst">with <a href="{% url 'classes:instructor_profile' reg.class_offering.instructor.slug %}">{{ reg.class_offering.instructor.display_name }}</a></div>
    <div class="bk-card-when">
      {% with sess=reg.class_offering.sessions.first %}
        {% if sess %}<span>{{ sess.starts_at|date:"D, M j" }}</span><span class="dot">·</span><span>{{ sess.starts_at|date:"g:i A" }}</span>{% endif %}
      {% endwith %}
    </div>
  </div>
  <div class="bk-card-meta">
    <span class="bk-card-status {% if reg.status == 'waitlisted' %}waitlist{% else %}ok{% endif %}">
      {% if reg.status == 'waitlisted' %}Waitlist{% elif reg.status == 'confirmed' %}Registered{% else %}{{ reg.get_status_display }}{% endif %}
    </span>
    <a class="bk-card-action" href="{% url 'classes:my_registration' reg.self_serve_token %}">View details →</a>
  </div>
</article>
```

`empty_state.html`:
```django
<div class="bk-empty">
  <div class="bk-empty-glyph">{{ glyph }}</div>
  <h3>{{ title }}</h3>
  <p>{{ body }}</p>
  {% if cta %}<a class="cta-primary" href="{{ cta_url }}">{{ cta }}</a>{% endif %}
</div>
```

`member_banner.html`:
```django
<div class="bk-banner">
  <span class="bk-banner-icon" aria-hidden="true">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>
      <rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>
    </svg>
  </span>
  <div class="bk-banner-body">
    <div class="bk-banner-title">You're a Past Lives member.</div>
    <div class="bk-banner-sub">Your tab, voting, and the full member dashboard live on <a href="https://{{ MEMBER_HOST }}/">FOG</a>. Member pricing applies here automatically.</div>
  </div>
  <a class="bk-banner-link" href="https://{{ MEMBER_HOST }}/">Open FOG dashboard</a>
</div>
```

`instructor_banner.html`:
```django
<div class="bk-banner is-instructor">
  <span class="bk-banner-icon" aria-hidden="true">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
    </svg>
  </span>
  <div class="bk-banner-body">
    <div class="bk-banner-title">You're teaching {{ count }} upcoming {{ count|pluralize:"class,classes" }}.</div>
    <div class="bk-banner-sub">Rosters, attendance, and class tools live in your teaching dashboard on FOG.</div>
  </div>
  <a class="bk-banner-link" href="https://{{ MEMBER_HOST }}/classes/instructor/">Teaching dashboard</a>
</div>
```

`member_nudge.html`:
```django
<div class="bk-nudge" data-nudge>
  <div class="bk-nudge-body">
    <div class="bk-nudge-eyebrow">·  ·  ·</div>
    <div class="bk-nudge-title">Coming back often? Membership opens the door.</div>
    <div class="bk-nudge-sub">Member dues cover 24/7 studio access, member pricing on classes, and a vote in how the space runs. No pressure — when you're ready, it'll be here.</div>
  </div>
  <a class="bk-nudge-cta" href="https://pastlives.space/membership">Learn about membership</a>
  <button class="bk-nudge-dismiss" aria-label="Dismiss"
          onclick="document.cookie='pl_nudge_dismissed=1; path=/; max-age=15552000'; this.closest('[data-nudge]').remove();">×</button>
</div>
```

- [ ] **Step 4: Run tests**

`pytest tests/classes/account/overview_spec.py -v`

- [ ] **Step 5: Commit**

```bash
git add classes/account/views.py classes/account/selectors.py \
        templates/classes/account/overview.html \
        templates/classes/account/_components/ \
        tests/classes/account/overview_spec.py
git commit -m "Account overview: upcoming classes, persona banners, soft nudge"
```

### Task 4.3: History page

- [ ] **Step 1: Write the test**

```python
# tests/classes/account/history_spec.py
import pytest
from datetime import timedelta
from django.test import Client
from django.utils import timezone
from membership.factories import MemberFactory
from classes.factories import ClassOfferingFactory, ClassSessionFactory, RegistrationFactory

@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    return Client(HTTP_HOST="book.pastlives.space")

def describe_account_history():
    def it_groups_past_classes_by_year(book_client, db):
        member = MemberFactory(status="active")
        # one in 2026, one in 2025
        for years_ago, month in [(0, 5), (1, 3)]:
            offering = ClassOfferingFactory(status="published")
            session_date = timezone.now().replace(month=month) - timedelta(days=365 * years_ago + 30)
            ClassSessionFactory(class_offering=offering, starts_at=session_date)
            RegistrationFactory(email=member.user.email, member=member,
                                class_offering=offering, status="confirmed")
        book_client.force_login(member.user)
        resp = book_client.get("/account/history/")
        content = resp.content.decode()
        assert "2026" in content
        assert "2025" in content
```

- [ ] **Step 2: Implement the view**

In `classes/account/views.py`:

```python
class HistoryView(_LoggedInAccountView):
    template_name = "classes/account/history.html"
    active_tab = "history"

    def get_context_data(self, **kwargs):
        from collections import defaultdict
        from classes.account.selectors import past_registrations
        ctx = super().get_context_data(**kwargs)
        grouped: dict[int, list] = defaultdict(list)
        for reg in past_registrations(self.request.user):
            sess = reg.class_offering.sessions.order_by("-starts_at").first()
            year = sess.starts_at.year if sess else reg.registered_at.year
            grouped[year].append(reg)
        ctx["grouped"] = sorted(grouped.items(), reverse=True)
        return ctx
```

- [ ] **Step 3: Write the template**

`templates/classes/account/history.html`:

```django
{% extends "classes/account/base.html" %}

{% block account_title %}Past <em>classes</em>.{% endblock %}
{% block account_sub %}
  <p class="bk-sub">Every class you've taken at Past Lives. Add a note if a project's still rattling around in your head.</p>
{% endblock %}

{% block account_body %}
  {% if not grouped %}
    {% include "classes/account/_components/empty_state.html" with glyph="✶" title="No past classes yet." body="Once you've attended a class, it'll show up here." cta="Browse classes" cta_url="/classes/" %}
  {% else %}
    {% for year, regs in grouped %}
      <div class="bk-section-h"><h2>{{ year }}</h2><span class="ct">{{ regs|length }} {{ regs|length|pluralize:"item,items" }}</span></div>
      {% for reg in regs %}
        <article class="bk-card is-past">
          <div class="bk-card-thumb">{{ reg.class_offering.category.name|slice:":2"|upper }}</div>
          <div class="bk-card-body">
            <div class="bk-card-title">{{ reg.class_offering.title }}</div>
            <div class="bk-card-inst">with <a href="{% url 'classes:instructor_profile' reg.class_offering.instructor.slug %}">{{ reg.class_offering.instructor.display_name }}</a></div>
            <div class="bk-card-when">
              {% with sess=reg.class_offering.sessions.last %}
                {% if sess %}<span>{{ sess.starts_at|date:"D, M j" }}</span><span class="dot">·</span><span>{{ sess.starts_at|date:"g:i A" }}</span>{% endif %}
              {% endwith %}
            </div>
          </div>
          <div class="bk-card-meta">
            <span class="bk-card-status attended">Attended</span>
          </div>
        </article>
      {% endfor %}
    {% endfor %}
  {% endif %}
{% endblock %}
```

- [ ] **Step 4: Run + commit**

```bash
pytest tests/classes/account/history_spec.py -v
git add classes/account/views.py templates/classes/account/history.html tests/classes/account/history_spec.py
git commit -m "Account history: past classes grouped by year"
```

---

# Phase 5: Receipts page

**Files:**
- Modify: `classes/account/views.py`
- Create: `templates/classes/account/receipts.html`
- Create: `templates/classes/account/_components/receipt_row.html`
- Test: `tests/classes/account/receipts_spec.py`

### Task 5.1: Implement receipts

- [ ] **Step 1: Write the test**

```python
# tests/classes/account/receipts_spec.py
import pytest
from django.test import Client
from django.utils import timezone
from membership.factories import MemberFactory
from classes.factories import ClassOfferingFactory, RegistrationFactory

@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    return Client(HTTP_HOST="book.pastlives.space")

def describe_account_receipts():
    def context_with_no_paid_registrations():
        def it_renders_empty_state(book_client, db, django_user_model):
            user = django_user_model.objects.create_user(email="x@y.com", username="x@y.com")
            book_client.force_login(user)
            resp = book_client.get("/account/receipts/")
            assert b"No receipts yet" in resp.content

    def context_with_paid_registrations():
        def it_renders_a_row_per_paid_registration(book_client, db):
            member = MemberFactory(status="active")
            offering = ClassOfferingFactory(status="published", title="Lampworking", price_cents=9500)
            RegistrationFactory(
                email=member.user.email, member=member,
                class_offering=offering, status="confirmed",
                amount_paid_cents=7600, stripe_payment_id="pi_xxx",
                confirmed_at=timezone.now(),
            )
            book_client.force_login(member.user)
            resp = book_client.get("/account/receipts/")
            assert b"Lampworking" in resp.content
            assert b"$76.00" in resp.content

        def it_hides_member_tag_for_nonmember(book_client, db, django_user_model):
            user = django_user_model.objects.create_user(email="x@y.com", username="x@y.com")
            offering = ClassOfferingFactory(status="published", title="Ceramics", price_cents=8000)
            RegistrationFactory(
                email="x@y.com", class_offering=offering, status="confirmed",
                amount_paid_cents=8000, stripe_payment_id="pi_yyy",
                confirmed_at=timezone.now(),
            )
            book_client.force_login(user)
            resp = book_client.get("/account/receipts/")
            assert b"is-discount" not in resp.content
```

- [ ] **Step 2: Implement view + template**

`classes/account/views.py`:

```python
class ReceiptsView(_LoggedInAccountView):
    template_name = "classes/account/receipts.html"
    active_tab = "receipts"

    def get_context_data(self, **kwargs):
        from classes.account.selectors import paid_registrations
        ctx = super().get_context_data(**kwargs)
        ctx["receipts"] = paid_registrations(self.request.user)
        return ctx
```

`templates/classes/account/receipts.html`:

```django
{% extends "classes/account/base.html" %}

{% block account_title %}Receipts.{% endblock %}
{% block account_sub %}
  <p class="bk-sub">
    {% if persona == 'member' %}Class purchases — member pricing applied automatically.
    {% else %}Class purchases. Free registrations don't appear here.{% endif %}
  </p>
{% endblock %}

{% block account_body %}
  {% if not receipts %}
    {% include "classes/account/_components/empty_state.html" with glyph="$" title="No receipts yet." body="When you register for a paid class, your receipt will show up here. Free classes don't generate one." cta="Browse classes" cta_url="/classes/" %}
  {% else %}
    <div class="bk-receipts">
      <div class="bk-rec-row is-head">
        <div>Date</div><div>Class</div><div>Method</div>
        <div style="text-align:right">Amount</div><div style="text-align:right">Receipt</div>
      </div>
      {% for reg in receipts %}
        <div class="bk-rec-row">
          <div class="bk-rec-date">{{ reg.confirmed_at|date:"M j, Y" }}</div>
          <div class="bk-rec-title">{{ reg.class_offering.title }}<span class="sub">{{ reg.class_offering.instructor.display_name }}</span></div>
          <div class="bk-rec-method">Stripe</div>
          <div class="bk-rec-amount {% if persona == 'member' and reg.member %}is-discount{% endif %}">${{ reg.amount_paid_cents|divisibleby:100|yesno:"," }}{{ reg.amount_paid_cents|stringformat:"d"|slice:":-2"|default:"0" }}.{{ reg.amount_paid_cents|stringformat:"d"|slice:"-2:" }}</div>
          <a class="bk-rec-action" href="{% url 'classes:my_registration' reg.self_serve_token %}">PDF →</a>
        </div>
      {% endfor %}
    </div>
  {% endif %}
{% endblock %}
```

> Note on the currency template logic: Django doesn't ship a built-in dollar formatter for cents. Replace the inline div with a custom template filter `{% load classes_extras %}{{ reg.amount_paid_cents|cents_as_dollars }}` and add to `classes/templatetags/classes_extras.py`:
>
> ```python
> @register.filter
> def cents_as_dollars(cents):
>     return f"${cents/100:,.2f}"
> ```

- [ ] **Step 3: Verify + commit**

```bash
pytest tests/classes/account/receipts_spec.py -v
git add classes/account/views.py templates/classes/account/receipts.html \
        classes/templatetags/classes_extras.py \
        tests/classes/account/receipts_spec.py
git commit -m "Account receipts: paid registrations, member discount tag"
```

---

# Phase 6: Profile page (editable for non-members, read-only for members)

**Files:**
- Modify: `classes/account/views.py`, `classes/account/forms.py` (new)
- Create: `templates/classes/account/profile.html`
- Test: `tests/classes/account/profile_spec.py`

### Task 6.1: Profile form + view

- [ ] **Step 1: Write the test**

```python
# tests/classes/account/profile_spec.py
import pytest
from django.test import Client
from membership.factories import MemberFactory
from allauth.account.models import EmailAddress

@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    return Client(HTTP_HOST="book.pastlives.space")

def describe_account_profile():
    def context_for_nonmember():
        def it_renders_editable_form(book_client, db, django_user_model):
            user = django_user_model.objects.create_user(
                email="a@b.com", username="a@b.com", first_name="Avery", last_name="Sandoval",
            )
            EmailAddress.objects.create(user=user, email="a@b.com", verified=True, primary=True)
            book_client.force_login(user)
            resp = book_client.get("/account/profile/")
            assert resp.status_code == 200
            assert b"Avery" in resp.content
            assert b"readonly" not in resp.content  # not read-only mode

        def it_saves_first_and_last_name(book_client, db, django_user_model):
            user = django_user_model.objects.create_user(email="a@b.com", username="a@b.com")
            book_client.force_login(user)
            resp = book_client.post("/account/profile/", {
                "first_name": "Avery", "last_name": "Sandoval",
                "pronouns": "they/them", "phone": "(503) 555-0146",
            })
            assert resp.status_code == 302
            user.refresh_from_db()
            assert user.first_name == "Avery"

    def context_for_member():
        def it_renders_read_only_with_edit_on_fog_link(book_client, db):
            member = MemberFactory(status="active")
            book_client.force_login(member.user)
            resp = book_client.get("/account/profile/")
            assert b"Read-only here" in resp.content
            assert b"Edit on FOG" in resp.content
            assert b"readonly" in resp.content

        def it_rejects_post_for_member(book_client, db):
            member = MemberFactory(status="active")
            book_client.force_login(member.user)
            resp = book_client.post("/account/profile/", {"first_name": "X", "last_name": "Y"})
            # Either 403 or 405 — but importantly, the name does not change.
            member.user.refresh_from_db()
            assert member.user.first_name != "X" or resp.status_code in (403, 405)
```

- [ ] **Step 2: Implement form**

Create `classes/account/forms.py`:

```python
from __future__ import annotations
from django import forms
from django.contrib.auth import get_user_model

User = get_user_model()


class AccountProfileForm(forms.ModelForm):
    pronouns = forms.CharField(max_length=50, required=False)
    phone = forms.CharField(max_length=20, required=False)

    class Meta:
        model = User
        fields = ["first_name", "last_name"]

    def save(self, commit: bool = True) -> User:
        user = super().save(commit=False)
        profile_data = {
            "pronouns": self.cleaned_data.get("pronouns", ""),
            "phone": self.cleaned_data.get("phone", ""),
        }
        if commit:
            user.save()
        from core.models import UserProfile
        UserProfile.objects.update_or_create(user=user, defaults=profile_data)
        return user
```

> The `UserProfile` model is defined in Phase 9 — for Phase 6, define a minimal placeholder version first and extend it in Phase 9:

```python
# core/models.py — append (small version, just for profile fields)
class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile",
        help_text="The user this profile belongs to.",
    )
    pronouns = models.CharField(max_length=50, blank=True, help_text="Optional pronouns.")
    phone = models.CharField(max_length=20, blank=True, help_text="Day-of contact phone.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Profile for {self.user.email}"
```

Generate the migration:

```bash
python manage.py makemigrations core --name userprofile
```

- [ ] **Step 3: Implement view**

```python
# classes/account/views.py
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.views.generic import FormView
from classes.account.forms import AccountProfileForm


class ProfileView(LoginRequiredMixin, FormView):
    template_name = "classes/account/profile.html"
    form_class = AccountProfileForm
    active_tab = "profile"
    login_url = "/accounts/login/"
    success_url = "/account/profile/"

    def is_member(self) -> bool:
        member = getattr(self.request.user, "member", None)
        return bool(member and member.status == "active")

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["instance"] = self.request.user
        if self.is_member():
            kw["data"] = None  # form is read-only
        return kw

    def get_initial(self):
        profile = getattr(self.request.user, "profile", None)
        return {
            "pronouns": profile.pronouns if profile else "",
            "phone": profile.phone if profile else "",
        }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["active_tab"] = self.active_tab
        ctx["is_readonly"] = self.is_member()
        return ctx

    def post(self, request, *args, **kwargs):
        if self.is_member():
            messages.info(request, "Edit your profile on FOG — it's read-only here for members.")
            return HttpResponseRedirect(self.success_url)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "Profile updated.")
        return super().form_valid(form)
```

- [ ] **Step 4: Write the template**

`templates/classes/account/profile.html`:

```django
{% extends "classes/account/base.html" %}

{% block account_title %}Your <em>profile</em>.{% endblock %}
{% block account_sub %}
  <p class="bk-sub">
    {% if is_readonly %}Your member profile lives on FOG — head over to edit it. The details below are what instructors and staff see when you book a class.
    {% else %}Edit how your name appears on roster sheets and confirmation emails. Manage which email addresses can sign in.{% endif %}
  </p>
{% endblock %}

{% block account_body %}
  {% if is_readonly %}
    <div class="bk-readonly-banner">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
      </svg>
      <span><b>Read-only here.</b> Your name, contact info, and emails are managed on your member profile.</span>
      <a href="https://{{ MEMBER_HOST }}/settings/">Edit on FOG →</a>
    </div>
  {% endif %}

  <form class="bk-form" method="post">{% csrf_token %}
    <div class="bk-field {% if is_readonly %}is-locked{% endif %}">
      <label>First name</label>
      <input name="first_name" value="{{ form.first_name.value|default:'' }}" {% if is_readonly %}readonly{% endif %}>
    </div>
    <div class="bk-field {% if is_readonly %}is-locked{% endif %}">
      <label>Last name</label>
      <input name="last_name" value="{{ form.last_name.value|default:'' }}" {% if is_readonly %}readonly{% endif %}>
    </div>
    <div class="bk-field {% if is_readonly %}is-locked{% endif %}">
      <label>Pronouns</label>
      <input name="pronouns" value="{{ form.pronouns.value|default:'' }}" {% if is_readonly %}readonly{% endif %}>
    </div>
    <div class="bk-field {% if is_readonly %}is-locked{% endif %}">
      <label>Phone</label>
      <input name="phone" value="{{ form.phone.value|default:'' }}" {% if is_readonly %}readonly{% endif %}>
      <div class="bk-field-hint">Only used if an instructor needs to reach you the day of class.</div>
    </div>

    <div class="bk-field full">
      <label>Email addresses</label>
      <div class="bk-emails">
        {% for ea in user.emailaddress_set.all %}
          <div class="bk-email-row">
            <span class="bk-email-addr">{{ ea.email }}</span>
            {% if ea.primary %}<span class="bk-email-tag primary">Primary</span>{% endif %}
            {% if not ea.verified %}<span class="bk-email-tag unverified">Unverified</span>{% endif %}
            {% if not is_readonly %}
              {# Email management uses allauth's existing /accounts/email/ view; link out. #}
            {% endif %}
          </div>
        {% endfor %}
        {% if not is_readonly %}
          <div class="bk-email-add">
            <a class="cta-primary" href="/accounts/email/" style="text-decoration:none;">Manage emails →</a>
          </div>
        {% endif %}
      </div>
    </div>

    {% if not is_readonly %}
      <div class="bk-form-actions">
        <button type="button" class="bk-btn-ghost" onclick="history.back()">Cancel</button>
        <button type="submit" class="bk-btn-primary">Save changes</button>
      </div>
    {% endif %}
  </form>
{% endblock %}
```

- [ ] **Step 5: Run + commit**

```bash
python manage.py makemigrations core --name userprofile
pytest tests/classes/account/profile_spec.py tests/core/user_profile_spec.py -v
git add core/models.py core/migrations/ classes/account/forms.py classes/account/views.py \
        templates/classes/account/profile.html tests/classes/account/profile_spec.py
git commit -m "Account profile: editable for nonmembers, read-only for members"
```

---

# Phase 7: Order number + guest lookup

**Files:**
- Modify: `classes/models.py` (add `order_number` field + generator)
- Create: `classes/migrations/00XX_registration_order_number.py`
- Modify: `classes/account/views.py`, `classes/account/forms.py`
- Create: `templates/classes/account/lookup.html`
- Test: `tests/classes/account/order_number_spec.py`, `lookup_spec.py`

### Task 7.1: Order number field on Registration

- [ ] **Step 1: Write the test**

```python
# tests/classes/account/order_number_spec.py
import pytest
import re
from classes.factories import RegistrationFactory

def describe_registration_order_number():
    def it_generates_on_save(db):
        reg = RegistrationFactory()
        assert reg.order_number
        assert re.match(r"^PL-[A-HJ-NP-Z2-9]{4}-\d{2}$", reg.order_number)

    def it_is_unique(db):
        regs = [RegistrationFactory() for _ in range(20)]
        codes = {r.order_number for r in regs}
        assert len(codes) == 20

    def it_does_not_overwrite_existing(db):
        reg = RegistrationFactory(order_number="PL-XXXX-99")
        reg.save()
        assert reg.order_number == "PL-XXXX-99"
```

- [ ] **Step 2: Add the field + generator**

Edit `classes/models.py`, `class Registration`:

```python
import string  # at top of file

# inside Registration:
order_number = models.CharField(
    max_length=12, blank=True, unique=True, db_index=True,
    help_text="Human-readable confirmation number shown in emails (PL-XXXX-YY).",
)

# extend save():
def save(self, *args, **kwargs) -> None:
    creating = self._state.adding
    if creating and not self.self_serve_token:
        self.self_serve_token = secrets.token_urlsafe(48)
    if creating and not self.order_number:
        self.order_number = self._generate_order_number()
    super().save(*args, **kwargs)
    if creating and self.member_id is None:
        self.link_member_by_email()

@staticmethod
def _generate_order_number() -> str:
    """PL-XXXX-YY where XXXX is unambiguous chars and YY is current year suffix."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0, O, I, 1
    year_suffix = timezone.now().strftime("%y")
    for _ in range(40):
        candidate = "PL-" + "".join(secrets.choice(alphabet) for _ in range(4)) + f"-{year_suffix}"
        if not Registration.objects.filter(order_number=candidate).exists():
            return candidate
    raise RuntimeError("Failed to generate a unique order number after 40 tries.")
```

- [ ] **Step 3: Generate + run the migration**

```bash
python manage.py makemigrations classes --name registration_order_number
```

Edit the generated migration to backfill order_numbers for existing rows:

```python
# classes/migrations/00XX_registration_order_number.py
# After the AddField op, add a RunPython:

def backfill_order_numbers(apps, schema_editor):
    Registration = apps.get_model("classes", "Registration")
    from django.utils import timezone
    import secrets
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    existing = set(Registration.objects.exclude(order_number="").values_list("order_number", flat=True))
    for reg in Registration.objects.filter(order_number=""):
        year_suffix = (reg.registered_at or timezone.now()).strftime("%y")
        while True:
            candidate = "PL-" + "".join(secrets.choice(alphabet) for _ in range(4)) + f"-{year_suffix}"
            if candidate not in existing:
                existing.add(candidate)
                break
        reg.order_number = candidate
        reg.save(update_fields=["order_number"])

def noop_reverse(apps, schema_editor):
    # CLAUDE.md says data migrations must include a reverse; clearing order_number
    # is safe because nothing else references it.
    Registration = apps.get_model("classes", "Registration")
    Registration.objects.update(order_number="")

# operations.append:
migrations.RunPython(backfill_order_numbers, noop_reverse),
```

- [ ] **Step 4: Run tests, fix until green; commit**

```bash
pytest tests/classes/account/order_number_spec.py -v
git add classes/models.py classes/migrations/ tests/classes/account/order_number_spec.py
git commit -m "Add human-readable order_number to Registration"
```

### Task 7.2: Guest lookup view

- [ ] **Step 1: Test**

```python
# tests/classes/account/lookup_spec.py
import pytest
from django.test import Client
from classes.factories import RegistrationFactory

@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    return Client(HTTP_HOST="book.pastlives.space")

def describe_guest_lookup():
    def it_renders_form_for_anonymous(book_client, db):
        resp = book_client.get("/account/lookup/")
        assert resp.status_code == 200
        assert b"Find your booking" in resp.content
        assert b"Order number" in resp.content

    def it_finds_a_booking(book_client, db):
        reg = RegistrationFactory(last_name="Sandoval", status="confirmed")
        resp = book_client.post("/account/lookup/", {
            "last_name": "Sandoval",
            "order_number": reg.order_number,
        })
        assert resp.status_code == 200
        assert reg.class_offering.title.encode() in resp.content
        assert b"Found" in resp.content

    def it_shows_notfound_with_helpful_text(book_client, db):
        resp = book_client.post("/account/lookup/", {
            "last_name": "Nobody", "order_number": "PL-XXXX-99",
        })
        assert resp.status_code == 200
        assert b"couldn&#x27;t find that one" in resp.content or b"couldn't find that one" in resp.content

    def it_is_case_insensitive_on_last_name(book_client, db):
        reg = RegistrationFactory(last_name="Sandoval")
        resp = book_client.post("/account/lookup/", {
            "last_name": "sandoval", "order_number": reg.order_number,
        })
        assert b"Found" in resp.content
```

- [ ] **Step 2: Form + view**

`classes/account/forms.py` (append):

```python
import re
from classes.models import Registration


class LookupForm(forms.Form):
    last_name = forms.CharField(max_length=100, label="Last name")
    order_number = forms.CharField(max_length=12, label="Order number")

    def clean_order_number(self):
        value = self.cleaned_data["order_number"].strip().upper()
        if not re.match(r"^PL-[A-HJ-NP-Z2-9]{4}-\d{2}$", value):
            raise forms.ValidationError("Order number should look like PL-XXXX-YY.")
        return value

    def find(self) -> Registration | None:
        return Registration.objects.filter(
            last_name__iexact=self.cleaned_data["last_name"],
            order_number=self.cleaned_data["order_number"],
        ).first()
```

`classes/account/views.py`:

```python
class LookupView(FormView):
    template_name = "classes/account/lookup.html"
    form_class = LookupForm

    def form_valid(self, form):
        result = form.find()
        ctx = self.get_context_data(form=form)
        ctx["lookup_state"] = "result" if result else "notfound"
        ctx["result"] = result
        return self.render_to_response(ctx)
```

- [ ] **Step 3: Template** — `templates/classes/account/lookup.html`:

Use the design from `account-screens.jsx:553-619`. Three states branched on `lookup_state`. Add a hint pointing back to login.

- [ ] **Step 4: Link from login + commit**

In the themed login template (Phase 8), add a footer link: `Booked as a guest? <a href="{% url 'account:lookup' %}">Look up your class.</a>`

```bash
pytest tests/classes/account/lookup_spec.py -v
git add classes/account/forms.py classes/account/views.py templates/classes/account/lookup.html \
        tests/classes/account/lookup_spec.py
git commit -m "Guest lookup: find a booking by last name + order number"
```

---

# Phase 8: Themed allauth on book

**Files:**
- Modify: `core/middleware.py` (remove the /accounts/* redirect on public)
- Overwrite: `templates/account/login.html`, `signup.html`, `confirm_login_code.html`
- Test: `tests/auth/allauth_book_theme_spec.py`

### Task 8.1: Stop redirecting `/accounts/*` from book

- [ ] **Step 1: Test**

```python
# tests/auth/allauth_book_theme_spec.py
import pytest
from django.test import Client

@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    settings.MEMBER_HOST = "members.pastlives.space"
    return Client(HTTP_HOST="book.pastlives.space")

def describe_login_on_book():
    def it_serves_login_page_in_place_no_redirect(book_client, db):
        resp = book_client.get("/accounts/login/")
        assert resp.status_code == 200
        assert b"book-themed-login" in resp.content  # marker from the themed template

    def it_uses_the_book_chrome(book_client, db):
        resp = book_client.get("/accounts/login/")
        assert b"pl-public-topbar" in resp.content

    def it_has_a_guest_lookup_link(book_client, db):
        resp = book_client.get("/accounts/login/")
        assert b"Look up your class" in resp.content
```

- [ ] **Step 2: Remove the redirect in middleware**

Edit `core/middleware.py:62-67` — delete the `if path.startswith("/accounts/"):` block. Allauth now serves on whichever host the user is on; the `.pastlives.space` cookie domain means the session works on both.

- [ ] **Step 3: Overwrite the templates**

`templates/account/login.html`:

```django
{% extends "classes/base_public.html" %}
{% load i18n allauth account %}
{% block portal_content %}
<div class="bk"><div class="bk-scroll"><div class="bk-auth">
  <div class="bk-auth-card" data-test="book-themed-login">
    <div class="bk-auth-eyebrow">Sign in</div>
    <h1 class="bk-auth-title">Welcome back.</h1>
    <p class="bk-auth-sub">We'll email you a 6-digit code — no password to remember.</p>
    <form method="post" action="{% url 'account_request_login_code' %}">{% csrf_token %}
      <div class="bk-field">
        <label>Email address</label>
        <input type="email" name="email" required>
      </div>
      <div class="bk-auth-actions">
        <button class="bk-btn-primary" type="submit">Email me a login code</button>
      </div>
    </form>
    <div class="bk-auth-divider">or</div>
    <div class="bk-auth-links">
      <p>New to Past Lives? <a href="{% url 'account_signup' %}">Create an account</a></p>
      <p>Booked as a guest? <a href="{% url 'account:lookup' %}">Look up your class.</a></p>
    </div>
    <div class="bk-auth-foot">
      <b>Heads up:</b> if you registered for a class as a guest, use the same email you signed up with — that's how we find your bookings.
    </div>
  </div>
</div></div></div>
{% endblock %}
```

`templates/account/signup.html` — analogous to LoginScreen → SignupScreen mapping in `account-screens.jsx:361-386`.

`templates/account/confirm_login_code.html` — six code inputs, see `account-screens.jsx:337-359`. Form action posts back to `{% url 'account_confirm_login_code' %}`.

- [ ] **Step 4: Verify nothing breaks on members surface**

```python
def describe_login_on_members():
    def it_still_serves_login_page(book_client):  # rename: actually members client
        c = Client(HTTP_HOST="members.pastlives.space")
        resp = c.get("/accounts/login/")
        assert resp.status_code == 200
        # Members host should still render the page (with member chrome or shared chrome).
```

The templated `templates/account/login.html` extends `classes/base_public.html` — that always renders public chrome regardless of host. Solution: branch the base template via `{% if is_public_surface %}…{% else %}…{% endif %}` or use two template files and pick via the surface context. Simplest pragmatic fix: keep using the book chrome on both surfaces for now (since allauth pages don't really belong to either surface's main content), and add a TODO comment that this is intentional. The session cookie is the cross-host thing.

Alternative if user complaints arise: dedicate `templates/account/login.html` to the public chrome and have the member host fall back to a different template by overriding allauth's template loader paths per surface — out of scope for this PR.

- [ ] **Step 5: Commit**

```bash
pytest tests/auth/allauth_book_theme_spec.py -v
git add core/middleware.py templates/account/login.html templates/account/signup.html \
        templates/account/confirm_login_code.html tests/auth/
git commit -m "Theme allauth login/signup/code pages with book chrome"
```

---

# Phase 9: Onboarding wizard

**Files:**
- Modify: `core/models.py` (extend `UserProfile`)
- Create: `core/migrations/00XX_userprofile_onboarding_fields.py`
- Modify: `classes/account/views.py`, `forms.py`
- Modify: `plfog/adapters.py` (signal hook on signup)
- Create: `templates/classes/account/onboarding/step{1,2,3}.html`
- Test: `tests/classes/account/onboarding_spec.py`

### Task 9.1: Extend UserProfile with onboarding fields

- [ ] **Step 1: Test**

```python
# tests/core/user_profile_spec.py
import pytest
from core.models import UserProfile
from membership.factories import MemberFactory

def describe_UserProfile():
    def it_persists_onboarding_answers(db, django_user_model):
        user = django_user_model.objects.create_user(email="x@y.com", username="x@y.com")
        profile = UserProfile.objects.create(
            user=user,
            first_attendance_status=UserProfile.FirstAttendance.FIRST_TIME,
            preferred_name="Avery",
            pronouns="they/them",
            phone="(503) 555-0146",
            referral_source=UserProfile.Referral.INSTAGRAM,
        )
        assert profile.first_attendance_status == "first_time"
        assert profile.is_onboarded is False

    def it_marks_onboarded_when_completed_at_set(db, django_user_model):
        from django.utils import timezone
        user = django_user_model.objects.create_user(email="x@y.com", username="x@y.com")
        profile = UserProfile.objects.create(user=user, onboarding_completed_at=timezone.now())
        assert profile.is_onboarded is True
```

- [ ] **Step 2: Extend the model**

In `core/models.py`, replace the placeholder `UserProfile` with:

```python
class UserProfile(models.Model):
    class FirstAttendance(models.TextChoices):
        FIRST_TIME = "first_time", "First time"
        RETURNING = "returning", "Returning"
        EVENT_ONLY = "event_only", "Event only, no class"
        UNKNOWN = "unknown", "Can't remember"

    class Referral(models.TextChoices):
        FRIEND = "friend", "Friend or family"
        INSTAGRAM = "instagram", "Instagram"
        GOOGLE = "google", "Google"
        EVENT = "event", "Open studio / event"
        MAIN_SITE = "main_site", "Past Lives main site"
        OTHER = "other", "Somewhere else"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile",
        help_text="The user this profile belongs to.",
    )
    preferred_name = models.CharField(max_length=100, blank=True, help_text="Preferred name on rosters.")
    pronouns = models.CharField(max_length=50, blank=True)
    phone = models.CharField(max_length=20, blank=True, help_text="Day-of contact phone.")
    first_attendance_status = models.CharField(
        max_length=20, choices=FirstAttendance.choices, blank=True,
        help_text="Self-reported on first signup.",
    )
    referral_source = models.CharField(max_length=20, choices=Referral.choices, blank=True)
    interest_category_slugs = models.JSONField(
        default=list, blank=True,
        help_text="List of Category slugs the user opted into for new-class emails.",
    )
    accessibility_note = models.TextField(blank=True, help_text="Free-text accessibility note.")
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Profile for {self.user.email}"

    @property
    def is_onboarded(self) -> bool:
        return self.onboarding_completed_at is not None
```

Run `python manage.py makemigrations core --name userprofile_onboarding_fields`.

- [ ] **Step 3: Wire onboarding step views**

```python
# classes/account/views.py
class OnboardingStepView(LoginRequiredMixin, FormView):
    login_url = "/accounts/login/"
    step: int = 1
    form_class = None  # set per subclass

    def get_template_names(self):
        return [f"classes/account/onboarding/step{self.step}.html"]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["step"] = self.step
        ctx["total_steps"] = 3
        return ctx

    def get_success_url(self):
        if self.step < 3:
            return reverse(f"account:onboarding_step{self.step + 1}")
        return reverse("account:overview")

    def form_valid(self, form):
        from core.models import UserProfile
        profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
        for field, value in form.cleaned_data.items():
            setattr(profile, field, value)
        if self.step == 3:
            from django.utils import timezone
            profile.onboarding_completed_at = timezone.now()
        profile.save()
        return super().form_valid(form)


class OnboardingStep1(OnboardingStepView):
    step = 1
    form_class = OnboardingStep1Form

class OnboardingStep2(OnboardingStepView):
    step = 2
    form_class = OnboardingStep2Form

class OnboardingStep3(OnboardingStepView):
    step = 3
    form_class = OnboardingStep3Form
```

Forms in `classes/account/forms.py` — one per step with only the fields that step writes. Step 1 has `first_attendance_status` (radio). Step 2 has `preferred_name`, `pronouns`, `phone`, `referral_source`. Step 3 has `interest_category_slugs` (MultipleChoiceField against the live Category list) and `accessibility_note` (optional).

URLs in `classes/account/urls.py`:

```python
path("onboarding/", views.OnboardingStep1.as_view(), name="onboarding_step1"),
path("onboarding/2/", views.OnboardingStep2.as_view(), name="onboarding_step2"),
path("onboarding/3/", views.OnboardingStep3.as_view(), name="onboarding_step3"),
```

- [ ] **Step 4: Redirect after signup**

In `plfog/adapters.py`, add a method that fires after successful login-by-code if the user has no profile or `is_onboarded is False`:

```python
def get_login_redirect_url(self, request):
    user = request.user
    from core.models import UserProfile
    profile = UserProfile.objects.filter(user=user).first()
    if profile is None or not profile.is_onboarded:
        from django.urls import reverse
        return reverse("account:onboarding_step1")
    return super().get_login_redirect_url(request)
```

This is the standard allauth hook — verify the method signature matches the version of allauth in `requirements.txt`.

- [ ] **Step 5: Templates** — three templates implementing the wizard. Use `pl-cms/project/account-screens.jsx:416-549` as the source of truth for copy + structure. Skip button on every step links to `account:overview`. "Back" on steps 2 and 3 links to the previous step.

- [ ] **Step 6: Tests + commit**

```bash
python manage.py makemigrations core --name userprofile_onboarding_fields
pytest tests/classes/account/onboarding_spec.py tests/core/user_profile_spec.py -v
git add core/models.py core/migrations/ classes/account/ templates/classes/account/onboarding/ \
        plfog/adapters.py tests/classes/account/onboarding_spec.py tests/core/user_profile_spec.py
git commit -m "Onboarding wizard: 3-step opt-in profile after first signup"
```

---

# Phase 10: Version bump + changelog + QA

### Task 10.1: Bump version, write changelog

- [ ] **Step 1: Edit `plfog/version.py`**

```python
VERSION = "2.0.0"

CHANGELOG = """
The new account dashboard at book.pastlives.space is live!

If you've ever booked a class with us, you now have a clean, focused home for your bookings:
- See your upcoming classes at a glance.
- Look back at the classes you've taken.
- Pull up receipts for paid classes.
- Manage your profile and email addresses in one place.

Members will see a friendly link back to FOG for the full member dashboard. Instructors get a quick jump to their teaching tools. And if you booked as a guest, you can now find your booking with just your last name and order number — no account needed.

After your first sign-in, a short 3-step welcome helps us tailor what you see (skip anytime).
"""
```

Update `2.0.0` from 2.0.0.dev or wherever it stands.

- [ ] **Step 2: Manual QA on local dev**

Run the dev server with PUBLIC_HOSTS including localhost:

```bash
PUBLIC_HOSTS=book.localhost MEMBER_HOST=members.localhost python manage.py runserver
```

Open `http://book.localhost:8000/` and verify:
- Anonymous topbar → `/classes/` → register a guest booking → confirmation email sent (or printed to console) → `/account/lookup/` with the order number finds it.
- Sign up via allauth on book; verify the themed login/signup pages render.
- After signup, get redirected into onboarding step 1; complete all 3 steps; verify `UserProfile.onboarding_completed_at` is set.
- Switch the user to a Member; reload `/account/` → see Member banner + FOG link in topbar.
- Switch the user to an Instructor; reload `/account/` → see instructor banner.
- On members.localhost:8000/account/ — verify 302 to book.localhost/account/.

- [ ] **Step 3: Run the full suite**

```bash
ruff check . && ruff format --check .
mypy .
pytest
```

- [ ] **Step 4: Commit + PR**

```bash
git add plfog/version.py
git commit -m "Bump version to 2.0.0 with public booking dashboard changelog"
git push origin v2.0-public-booking-subdomain
gh pr edit --add-label "ready-for-review"
```

---

## Spec coverage check

| Brief requirement | Implemented in |
|-|-|
| Public subdomain face, dark theme, brand vocab | Phase 2 (topbar), Phase 3 (`book-account.css`), already-existing public chrome |
| 4 personas | Phase 2 (context processor), Phase 4 (banners), Phase 6 (profile) |
| `/account/` overview | Phase 4 |
| `/account/history/` | Phase 4 |
| `/account/receipts/` | Phase 5 |
| `/account/profile/` | Phase 6 |
| Topbar updated for 4 personas | Phase 2 |
| Member FOG link + pill | Phase 2 |
| No sidebar on book — pill tabs | Phase 3 |
| Mobile-first | CSS ported from `pl-cms` already includes mobile breakpoints |
| Auth/login/signup themed | Phase 8 |
| Member discount tag on receipts | Phase 5 |
| Soft member nudge (one place, dismissable) | Phase 4 |
| Read-only profile for members with FOG link | Phase 6 |
| Persistent member banner | Phase 4 (member_banner.html) |
| Instructor link to teaching dashboard | Phase 4 (instructor_banner.html) |
| Onboarding wizard (3 steps, "have we met?", profile, interests) | Phase 9 |
| Guest lookup by last name + order number | Phase 7 |
| Empty states for each section | Phases 4, 5, 6 |
| Reuse existing catalog visual language | Phase 3 (`book-account.css` shares tokens with `base_public.html`) |
| `/classes/*` URLs unchanged | Phases 1-10 (we add `/account/`, never touch classes routes) |
| `/account/` on members 302's to book | Phase 3 |

## Notes for execution

- **Run lint + tests after every task** — don't batch. The repo's CLAUDE.md insists on 100% coverage; the existing `pytest.ini` has `addopts = "--strict-markers --tb=short -q"`.
- **Members table reverse-relation** — `Member` does NOT specify a `related_name`, so it's accessed as `user.member` (auto-generated). Confirm with `hasattr(user, "member")` — that's what the persona processor relies on.
- **Member status field** — check `membership/models.py` for the exact `Status.ACTIVE` value (may be `"active"` lowercase). The persona processor compares to that.
- **Don't touch `members.pastlives.space` chrome** — every template change lives in `templates/classes/account/`, `templates/account/`, or `templates/hub/base.html` (single topbar block only).
- **Migrations are reversible** — both new migrations include `RunPython` reverse functions per the CLAUDE.md mandate.
