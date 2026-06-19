"""Accessibility (axe-core) gate for the public/book surface pages.

The catalog, class-detail and login pages are held to full WCAG-AA on the axe
ruleset, in both light and dark: ``ACCEPTED_DEBT`` is now empty, so ANY
violation (contrast, link-name, landmark, critical, …) fails the build.
``ACCEPTED_DEBT`` remains as a ratchet escape hatch — if a future change
introduces genuinely unavoidable debt, add the rule id here with a note rather
than silencing the whole gate.

Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import urlparse

from axe_playwright_python.sync_playwright import Axe
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering

# Tolerated a11y debt (axe rule IDs). Currently empty — these pages are fully
# AA-clean. Add a rule id here only as a documented, temporary escape hatch.
ACCEPTED_DEBT: set[str] = set()


def _violations(page, url):
    page.goto(url, wait_until="networkidle")
    found = []
    for theme in ("light", "dark"):
        page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
        page.wait_for_timeout(200)
        for v in Axe().run(page).response["violations"]:
            node = v["nodes"][0] if v["nodes"] else {}
            target = (node.get("target") or ["?"])[0]
            fg = re.search(r"foreground color: (#[0-9a-f]{6})", node.get("failureSummary", "") or "")
            detail = f"{target} {fg.group(1) if fg else ''}".strip()
            found.append((theme, v["impact"], v["id"], len(v["nodes"]), detail))
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
            for theme, impact, rule, nodes, detail in _violations(page, f"{live_server.url}{path}"):
                if impact == "critical" or rule not in ACCEPTED_DEBT:
                    offenders.append(f"{name}/{theme}: [{impact}] {rule} ({nodes} node(s)) — {detail}")

        assert not offenders, "Critical or new (unbaselined) a11y violations:\n  " + "\n  ".join(offenders)
