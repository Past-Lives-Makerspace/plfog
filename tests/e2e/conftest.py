"""Fixtures for Playwright end-to-end specs.

These run a real browser (pytest-playwright's ``page``) against a live Django
server (pytest-django's ``live_server``) and drive the genuine allauth
login-by-code flow — the real screens a member sees, not a session shortcut.

They are slow and need a browser, so they are deselected from the default
``pytest`` run via the ``e2e`` marker (pyproject's addopts filters
``-m 'not e2e'``). Run them explicitly with ``pytest -m e2e``.
"""

from __future__ import annotations

import os
import re

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse

# Playwright's *sync* API drives an asyncio event loop (via greenlet) on the
# test thread. Django's async-safety guard then sees a running loop and blocks
# the synchronous ORM and test-DB calls these specs legitimately make (factory
# writes, ``mail.outbox`` reads, ``setup_databases``). The "async context" is a
# false positive here, so opt out of the guard. Read at the first guarded call,
# so setting it at conftest import time (before any test runs) is in time.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")


def pytest_collection_modifyitems(items):
    """Tag every spec under tests/e2e/ with the ``e2e`` marker automatically.

    Keeps the default run browserless and lets ``pytest -m e2e`` select exactly
    this suite, without each spec having to remember to mark itself.
    """
    for item in items:
        if "tests/e2e/" in str(item.path).replace("\\", "/"):
            item.add_marker("e2e")


@pytest.fixture(autouse=True)
def _e2e_settings(settings):
    """Make the live server usable from a real browser over plain HTTP.

    - locmem email backend so the login code lands in ``mail.outbox`` where the
      test can read it (the real adapter still renders and "sends" it).
    - non-secure, host-scoped cookies: production marks session/CSRF cookies
      Secure and scopes them to ``.pastlives.space``; a browser hitting
      ``http://localhost`` would silently drop those, so the login wouldn't
      stick. Override them here so the session cookie survives the redirect.
    - keep ``localhost`` off PUBLIC_HOSTS so it resolves to the members surface.
    """
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    settings.SESSION_COOKIE_DOMAIN = None
    settings.CSRF_COOKIE_DOMAIN = None
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]


@pytest.fixture
def login_via_code(page, live_server):
    """Return a helper that signs ``email`` in via the real login-by-code flow.

    Drives the actual allauth screens: request a code, read it out of the email
    that gets sent, then submit it — exactly what a member does. Returns once
    the post-login redirect has carried the browser off the auth screens, which
    only happens when allauth accepted the code.
    """
    user_model = get_user_model()

    def _login(email):
        from allauth.account.models import EmailAddress

        # Seed an existing, sign-in-ready account (a known member).
        user, _ = user_model.objects.get_or_create(username=email, defaults={"email": email})
        EmailAddress.objects.get_or_create(user=user, email=email, defaults={"verified": True, "primary": True})
        mail.outbox = []

        # Request a login code.
        page.goto(f"{live_server.url}{reverse('account_request_login_code')}")
        page.fill('input[name="email"]', email)
        page.locator('input[name="email"]').press("Enter")

        # allauth redirects to the confirm screen; the code email is now sent.
        page.wait_for_selector('input[name="code"]')
        assert mail.outbox, "expected a login-code email to be sent"
        body = mail.outbox[-1].body
        # The code is on its own line after "...is:" and is hyphen-grouped
        # (e.g. "HGJL-SQMX"), so grab the whole non-whitespace token.
        match = re.search(r"is:\s+(\S+)", body)
        assert match, f"no login code found in email body: {body!r}"

        # Enter the code and wait for the post-login redirect off the auth flow.
        page.fill('input[name="code"]', match.group(1))
        page.locator('input[name="code"]').press("Enter")
        page.wait_for_url(lambda url: "code/confirm" not in url)

        # A brand-new member lands on the hub with the one-time first-login
        # welcome modal open — a deliberate blocking overlay (no backdrop/escape
        # dismissal, must pick a button). That's correct on a real first sign-in,
        # but it covers the hub pages these specs then interact with, so mark it
        # dismissed for the member that just signed in. The next page.goto()
        # re-renders without it. No-op when no Member exists (e.g. book-only flows).
        from django.utils import timezone as _tz

        from membership.models import Member

        Member.objects.filter(user=user).update(welcome_dismissed_at=_tz.now())

    return _login
