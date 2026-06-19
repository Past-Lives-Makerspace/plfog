"""Accessibility (axe-core) gate for the public/book surface pages.

The app carries known a11y debt (colour contrast, a few link-name and
landmark issues), so this is a *ratchet*, not an all-or-nothing gate: it fails
on any ``critical`` violation or any rule type outside the documented
``ACCEPTED_DEBT`` baseline, but tolerates the known issues for now. As debt is
paid down, delete entries from ``ACCEPTED_DEBT`` to raise the bar — a fixed
rule that reappears will then fail the build.

Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlparse

from axe_playwright_python.sync_playwright import Axe
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering

# Known, tolerated a11y debt (axe rule IDs). Anything outside this set — or any
# violation with impact "critical" — fails the gate. Pay these down over time.
ACCEPTED_DEBT = {
    "color-contrast",
    "link-name",
    "landmark-complementary-is-top-level",
    "landmark-main-is-top-level",
    "landmark-no-duplicate-main",
    "landmark-unique",
}


def _violations(page, url):
    page.goto(url, wait_until="networkidle")
    found = []
    for theme in ("light", "dark"):
        page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
        page.wait_for_timeout(200)
        for v in Axe().run(page).response["violations"]:
            found.append((theme, v["impact"], v["id"], len(v["nodes"])))
    return found


def describe_accessibility():
    def it_has_no_critical_or_unexpected_violations(live_server, page, settings):
        # Serve these pages on the public/book surface (what customers see).
        settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]

        offering = ClassOfferingFactory(
            title="Intro to Lost Wax Casting",
            slug="intro-to-lost-wax-casting",
            status=ClassOffering.Status.PUBLISHED,
            is_private=False,
            price_cents=0,
        )
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=7),
            ends_at=timezone.now() + timedelta(days=7, hours=2),
        )

        pages = {
            "catalog": reverse("classes:public_list"),
            "detail": reverse("classes:public_class_detail", kwargs={"slug": offering.slug}),
            "login": reverse("account_request_login_code"),
        }

        offenders = []
        for name, path in pages.items():
            for theme, impact, rule, nodes in _violations(page, f"{live_server.url}{path}"):
                if impact == "critical" or rule not in ACCEPTED_DEBT:
                    offenders.append(f"{name}/{theme}: [{impact}] {rule} ({nodes} node(s))")

        assert not offenders, "Critical or new (unbaselined) a11y violations:\n  " + "\n  ".join(offenders)
