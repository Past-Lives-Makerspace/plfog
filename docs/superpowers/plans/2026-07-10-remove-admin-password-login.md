# Remove the deprecated Django-admin password login (`/admin/login/`)

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-10
**Surface:** Django admin (`/admin/`) on the members host. Touches routing (`plfog/urls.py`), a tiny new module, one deleted template, a latent link in `templates/home.html`, a new `robots.txt`, and the admin-login test.
**Why:** A regular (non-staff) member reached the admin's username/password login page, tried to sign in, couldn't, and reported it as "I can't log in." Members authenticate only via allauth **email login codes** — the admin password form is vestigial and should never be shown to anyone. This rips it out and makes the page unreachable.

---

## 1. Summary

The admin login page at `/admin/login/` renders a real **username + password** form (`templates/admin/login.html`, unfold-themed). Members have no passwords — they sign in with an emailed code — so this page can only ever confuse or dead-end a member, and (worse) its "Sign in with email code instead" link carries `next=/admin/`, which **loops a non-staff member back to the login page** after they enter their code. That loop is exactly the "I logged in but it keeps showing me the login" symptom that was reported.

After this change:
- **Anonymous** visitors to `/admin/login/` (or any `/admin/…` URL) are 302-redirected to the real **allauth email-code login** (`/accounts/login/code/?next=…`). A legitimate admin who isn't signed in yet logs in by email code and lands in the admin; a member gets bounced to the normal sign-in they already know.
- **Authenticated non-staff** members who reach `/admin/` get a **plain 403 Forbidden** — no password form, no loop.
- **Authenticated staff** are unaffected: `/admin/` serves the dashboard exactly as today (Django never calls the login view for an authorized user).
- The password template is **deleted**, a **`robots.txt`** stops search engines from indexing `/admin/`, and a latent "Go to Dashboard → admin" link in `home.html` is removed so no future change can turn it into a funnel.

### How the member found it (investigation result)

Nothing in the member UI links to `/admin/`, and `LOGIN_URL` already resolves to allauth — so **no in-app redirect can explain it**. The member reached `/admin/login/` **out-of-band**, and two real gaps let that happen:

1. **It's crawlable.** There is no `robots.txt` anywhere in the project and `/admin/login/` returns a normal, themed **HTTP 200**. A typed/bookmarked/shared `/admin` link — or a search-engine result — drops the member straight onto a page that *looks* like the site's sign-in.
2. **It loops.** `templates/admin/login.html:61` links `"/accounts/login/code/?next=/admin/"`. The member clicks it, enters a valid code, allauth honors `next=/admin/` → `/admin/` → they're not staff → **back to `/admin/login/`**. Hence "it keeps failing."

Ranked vectors (from recon): (A) direct/out-of-band navigation to `/admin/`, (B) a search-engine result (no `robots.txt`, indexable 200), (C) a `@staff_member_required` custom admin URL someone shared bouncing to `admin:login`, (D) the email-code `next=/admin/` bounce-loop as the amplifier that made it "keep" failing.

### Locked decisions

| Decision | Choice |
|---|---|
| Keep any password login? | **No — remove entirely.** `/admin/login/` always redirects; the password form is never served to anyone. Emergency "break-glass" access remains possible via the Render **server shell** (`manage.py shell` / a management command), so nothing is lost operationally. |
| Authenticated non-staff at `/admin/` | **Plain 403 Forbidden** (Django's default 403). No redirect to allauth (which would loop), no friendly hub bounce. |
| Redirect target for anonymous `/admin/login/` | The **allauth email-code** login: `{% url 'account_request_login_code' %}?next=<original next>` (i.e. `/accounts/login/code/?next=…`). Members have no password, so this is the login they actually use; it also matches the URL the old template already linked to. |
| The password template | **Deleted** (`templates/admin/login.html`). Unreachable once the override redirects before render. |
| Discoverability | Add a **`robots.txt`** with `Disallow: /admin/` (plus the private account/settings prefixes). Cheap defense against future indexing. |
| Latent `home.html` funnel | **Remove** the `admin:index` "Go to Dashboard" link (`templates/home.html:16`). It's dead today (the `home` view redirects authed users to the hub) but is a landmine. |

---

## 2. What already exists (and what implements the login)

Confirmed in the codebase (line numbers may drift):

| Thing | Where | Note |
|---|---|---|
| Stock `AdminSite` singleton | `plfog/urls.py:84` `path("admin/", admin.site.urls)` | **No custom `AdminSite` subclass** and **no `admin.site.login` override** anywhere. `admin.site.login` is the stock Django view — it only renders the login template when the requester is **not** authenticated-and-staff. |
| The password page | `templates/admin/login.html` | Extends `unfold/layouts/unauthenticated.html`; `id="login-form"` + `form.username` + `form.password` (`:37-42`); the loop link `/accounts/login/code/?next=/admin/` (`:61`); an `{% if %}`-guarded `admin_password_reset` link (`:49-53`). |
| Custom admin URLs (before `admin.site.urls`) | `plfog/urls.py:18-46`, views in `plfog/admin_views.py` | Six `@staff_member_required` views (`invite_member`, `site_announcement`, `member_aliases*`). `staff_member_required` defaults to `login_url="admin:login"`, so a non-staff hit here 302s to `/admin/login/?next=…`. |
| The email-code login the redirect targets | `plfog/urls.py:87` `SeededRequestLoginCodeView` name `account_request_login_code` → `/accounts/login/code/` | Already honors `?next=` and `?email=`. |
| `LOGIN_URL` | **Unset** (`plfog/settings.py` has only `LOGIN_REDIRECT_URL = "/"`) → Django default `/accounts/login/` (allauth) | So `@login_required` already bounces to allauth, **not** the admin login. This is why no in-app path explains the report. |
| Admins are provisioned without passwords | `membership/management/commands/create_admin_user.py` (via `run_create_admin_users.sh`); `ADMIN_DOMAINS` in `plfog/adapters.py:258-269`; `Member.sync_user_permissions()` `membership/models.py:925` | Admins get `is_staff`/`is_superuser` and log in by **email code**. The password form has **no real consumer** in this repo's flow. |
| No `robots.txt` / `sitemap.xml` / custom `handler403/404` | — | `/admin/login/` is fully crawlable. This is a genuine gap. |

**Not related (leave alone):** `/accounts/restart-login/` (`core/views.py:122`) and `/accounts/find-account/` (`core/views.py:128`, tested in `tests/core/find_account_spec.py`) are active allauth helpers, not deprecated admin surfaces. `/site-migration/` in `MEMBER_ONLY_PATH_PREFIXES` has no backing view (dead prefix) — noted in Open/deferred, not touched here.

---

## 3. Where the code lives

```
plfog/
  admin_login.py          # NEW — the login override + installer
  urls.py                 # call install_admin_login_redirect() BEFORE path("admin/", admin.site.urls)
core/
  urls.py                 # + path("robots.txt", robots_txt, name="robots_txt")
  views.py                # + robots_txt(request) — tiny text/plain view
templates/
  admin/login.html        # DELETE (unreachable after the override)
  home.html               # remove the admin:index "Go to Dashboard" link (:16)
tests/
  admin/admin_login_spec.py   # REWRITE (200→302/403 assertions; see §7)
  core/robots_spec.py         # NEW — robots.txt disallows /admin/
```

No model, no migration, no settings change required (an optional explicit `LOGIN_URL = "/accounts/login/"` for clarity is listed in Open/deferred).

---

## 4. Business logic — the login override (fat, thin view)

`admin.site` is the stock singleton, so the lowest-footprint fix is to replace its bound `login` view with a thin redirect/deny. Put it in a dedicated module and install it once.

```python
# plfog/admin_login.py
from urllib.parse import quote

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse


def admin_login_redirect(request: HttpRequest, extra_context: dict | None = None) -> HttpResponse:
    """Replace the stock admin password login.

    - active staff .................. straight to the admin index (they're already
                                       authenticated via allauth; stock convenience preserved)
    - authenticated non-staff ....... 403 (never a redirect — that would loop)
    - anonymous ..................... the allauth email-code login, preserving ?next
    """
    if request.user.is_authenticated:
        if admin.site.has_permission(request):   # is_active and is_staff
            return redirect("admin:index")
        raise PermissionDenied
    next_url = request.GET.get("next") or reverse("admin:index")
    return redirect(f"{reverse('account_request_login_code')}?next={quote(next_url)}")


def install_admin_login_redirect() -> None:
    """Point ``admin.site.login`` at :func:`admin_login_redirect`.

    MUST run before ``admin.site.urls`` is first evaluated (i.e. before the
    ``path("admin/", admin.site.urls)`` line in ``plfog/urls.py``), because
    ``AdminSite.get_urls()`` binds ``self.login`` at URLconf-build time.
    """
    admin.site.login = admin_login_redirect
```

```python
# plfog/urls.py  (near the top, BEFORE urlpatterns)
from plfog.admin_login import install_admin_login_redirect

install_admin_login_redirect()   # ordering matters — see docstring
```

**Why this is loop-free and safe:**
- **Authenticated staff → `/admin/`:** Django's `admin_view` wrapper checks `has_permission` and serves the page; it never calls `login`. Unchanged (proven by `tests/plfog/dashboard_spec.py::it_loads_admin_index`).
- **Authenticated staff → `/admin/login/` directly:** the override sends them to `admin:index` (matches stock behavior).
- **Authenticated non-staff → `/admin/`:** `admin_view` redirects them to `/admin/login/?next=/admin/` → the override sees authenticated-but-no-permission → `PermissionDenied` → **403** (one hop, no loop). Decision honored.
- **Anonymous → `/admin/` or `/admin/login/`:** 302 to `/accounts/login/code/?next=…`. A real admin logs in by code and, now staff, reaches the admin; a member lands on the sign-in they know. The old loop is gone because the password page (and its `next=/admin/` link) no longer renders.

> `redirect_to_login(next, settings.LOGIN_URL)` is deliberately **not** used: `LOGIN_URL` defaults to `/accounts/login/` (the allauth *password/login* page), but members have no password — we want the **code** page (`account_request_login_code`).

### `robots.txt`

```python
# core/views.py
from django.http import HttpResponse

def robots_txt(request: HttpRequest) -> HttpResponse:
    lines = [
        "User-agent: *",
        "Disallow: /admin/",
        "Disallow: /accounts/",
        "Disallow: /settings/",
        "Disallow: /billing/",
        "Disallow: /tab/",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")
```

```python
# core/urls.py
path("robots.txt", views.robots_txt, name="robots_txt"),
```

A single flat `robots.txt` on the members host is enough to stop `/admin/` indexing. (The public book/guilds surfaces *want* indexing; per-surface robots is noted in Open/deferred, not built here — this file lives on the members host and its only load-bearing line is `Disallow: /admin/`.)

---

## 5. UI / UX  ← completeness checklist applied

This change is almost entirely routing; the only surfaces a human sees are the redirect target, the 403, and the deleted page.

- **Anonymous at `/admin/login/`:** no page of our own — an immediate 302 to the allauth email-code login (`/accounts/login/code/`), which is already a полished, responsive, dual-theme allauth screen. No dead end: it's the normal sign-in, with `?next=` carrying them onward after login. **States:** the target page owns empty/loading/error/success; our step is a bare redirect.
- **Authenticated non-staff at `/admin/`:** Django's default **403 Forbidden** page. Per the locked decision this is intentionally plain (not a branded hub bounce). *Completeness note:* it is a terminal 403, not a loop — the member still has the persistent app chrome via the browser back button, and the topbar/sidebar are reachable by navigating away. A branded `403.html` in hub chrome is a **possible** nicety but is explicitly **out of scope** (Open/deferred) — the decision was "plain 403."
- **The deleted password page:** removing `templates/admin/login.html` eliminates the username/password form, the confusing "authenticated as X but not authorized" message, and the loop-causing `next=/admin/` email-code link in one stroke. Nothing renders it after the override.
- **`home.html`:** the `admin:index` "Go to Dashboard" anchor is removed. It sits inside `{% if user.is_authenticated %}`, but the `home` view (`core/views.py:140`) redirects authed users to `hub_home` before `home.html` renders, so it's dead today — removing it prevents a future change from silently reopening the funnel. No visible change to any current user.
- **Forms / Save / lists:** none introduced. No editable lists, no toggles, no formsets — the FRONTEND editable-list rules don't apply here.
- **Dark + light / mobile:** nothing new to theme; the redirect target (allauth) and the default 403 are unaffected by this change.

---

## 6. Notifications / emails / activity

None. No emails, no notification-spine events, no `SiteActivity`. Purely routing + template removal.

---

## 7. Testing

BDD `*_spec.py`, `describe_*` / `it_*` (never `context_*` — not collected), run in the `plfog-web` Docker image, ≥98% coverage gate.

**Rewrite `tests/admin/admin_login_spec.py`** — it currently pins the password page (200, `id="login-form"`, `type="password"`, the `next=/admin/` link). Replace with the new contract:

```python
import pytest
from django.contrib.auth.models import User

pytestmark = pytest.mark.django_db


def describe_admin_login():
    def it_redirects_anonymous_to_the_email_code_login(client):
        resp = client.get("/admin/login/")
        assert resp.status_code == 302
        assert resp["Location"] == "/accounts/login/code/?next=/admin/"

    def it_preserves_the_next_param_on_redirect(client):
        resp = client.get("/admin/login/?next=/admin/membership/member/")
        assert resp.status_code == 302
        assert resp["Location"] == "/accounts/login/code/?next=/admin/membership/member/"

    def it_403s_an_authenticated_non_staff_member(client):
        User.objects.create_user(username="m", email="m@x.com", password="p")
        client.login(username="m", password="p")
        resp = client.get("/admin/login/")
        assert resp.status_code == 403

    def it_sends_authenticated_staff_to_the_admin_index(client):
        User.objects.create_superuser(username="a", email="a@x.com", password="p")
        client.login(username="a", password="p")
        resp = client.get("/admin/login/")
        assert resp.status_code == 302
        assert resp["Location"].endswith("/admin/")

    def it_never_serves_a_password_form(client):
        # The whole point: no username/password form is reachable anymore.
        resp = client.get("/admin/login/", follow=False)
        assert resp.status_code in (302, 403)
```

**Also:**
- **`it_403s_a_non_staff_member_hitting_the_admin_index`** — authenticated non-staff GET `/admin/` → after the internal bounce to `/admin/login/?next=/admin/`, the final status is **403** (use `follow=True` and assert `resp.status_code == 403`; assert the response body contains no `type="password"`).
- **`tests/plfog/dashboard_spec.py::it_loads_admin_index` stays green** (authenticated staff → `/admin/` → 200). Do not change it; it proves the safe path is untouched.
- **New `tests/core/robots_spec.py`** — GET `/robots.txt` → 200, `content_type` startswith `text/plain`, body contains `"Disallow: /admin/"`.
- **`templates/home.html`** — the removed link only ever lived inside the `{% if user.is_authenticated %}` branch, and the `home` **view** redirects authed users to the hub *before* `home.html` renders — so a plain "GET `/` as anonymous → no admin link" assertion is **tautological** (it passes before and after; it never exercises the removed line). Instead, render the **template directly** with an authenticated user in context so the `is_authenticated` branch is actually executed, and assert the admin link is gone:
  ```python
  def it_has_no_admin_dashboard_link_in_the_authenticated_branch(rf):
      from django.contrib.auth.models import User
      from django.template.loader import render_to_string
      user = User(username="u", is_authenticated=True)  # or a saved user
      html = render_to_string("home.html", {"user": user, "request": rf.get("/")})
      assert "/admin/" not in html
      assert "admin:index" not in html  # belt-and-suspenders: no unreversed tag either
  ```
  This renders the exact branch the link lived in, so it fails if the link is ever re-added. (The view-level redirect for authed users is already covered elsewhere — don't rely on it to hide this link.)
- **`tests/billing/admin_dashboard_spec.py:213 & 554`** — the tolerant `"/accounts/login/" or "/admin/login/"` assertions still pass (those views use `fog_admin_required` → `login_required` → `/accounts/login/`). No change needed; re-run to confirm.
- **Gotcha:** the login override must be installed for the test process — since it's wired in `plfog/urls.py` at import, it's active for all tests automatically. Assert on `resp["Location"]` (exact), not body text, for the redirect cases.

---

## 8. Build order (phased; each phase ships green)

1. **Override + redirect.** Add `plfog/admin_login.py`; call `install_admin_login_redirect()` in `plfog/urls.py` above `path("admin/", admin.site.urls)`. Rewrite `tests/admin/admin_login_spec.py` (§7). Now `/admin/login/` redirects/403s. (Green.)
2. **Delete the page + fix the funnel.** Remove `templates/admin/login.html`; remove the `admin:index` link from `templates/home.html:16`; add the `home.html` no-admin-link test. (Green.)
3. **`robots.txt`.** Add `core/views.robots_txt` + the `core/urls.py` route + `tests/core/robots_spec.py`. (Green.)
4. **Housekeeping (last).** Bump `plfog/version.py` `VERSION` and add the member-facing CHANGELOG entry (below). Run the full suite + `ruff` + `mypy`.

> Spec only — do not build until approved.

**Draft changelog entry** (member-facing; this is a fix to something members *lived with*, so it earns its own plain-language entry — see CLAUDE.md changelog rules):

> **Title:** "A clearer sign-in"
> - Fixed a confusing extra login screen some members could stumble onto (an old staff-only page). Signing in now always uses your email code — no dead ends.

---

## 9. Open / deferred

1. **`LOGIN_URL` (optional clarity).** Consider setting `LOGIN_URL = "/accounts/login/"` explicitly in `plfog/settings.py` so the default is documented. Not required — behavior is already correct. Not built here.
2. **Extra redirect hop from custom admin URLs.** `@staff_member_required` views (`plfog/admin_views.py`, `core/views.site_activity`) send anon users to `admin:login` first, which now 302s again to the email-code login (one extra hop, functionally fine). If the double-hop is ever undesirable, repoint those decorators' `login_url` at the allauth login. Out of scope.
3. **`MEMBER_ONLY_PATH_PREFIXES` no-op bug (separate fix).** Recon found the prefixes `/find-account/` and `/restart-login/` never match the real URLs (`/accounts/find-account/`, `/accounts/restart-login/`), so those `MEMBER_ONLY_PATH_PREFIXES` entries are dead in `core/middleware.py`. And `/site-migration/` has no backing view at all. Worth a separate cleanup; **not** folded into this change.
4. **Branded 403 (nice-to-have).** A hub-chrome `403.html` for the authenticated-non-staff case is deferred — the decision was a plain 403.
5. **Per-surface `robots.txt` (deferred).** The book/guilds/signage public surfaces want indexing; a host-aware robots view could allow those while disallowing `/admin/`. The single flat file here is sufficient for the immediate goal.
6. **Break-glass documentation.** Since the password login is gone, document the emergency path (Render server shell → `manage.py shell`/a management command to grant a superuser or trigger an email-code login) in the ops runbook so nobody assumes `/admin/login/` is the fallback.
7. **Release number** — decided at build time (one PR, one VERSION bump).

---

**Spec only — do not build until approved.**
