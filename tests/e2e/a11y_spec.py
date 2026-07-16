"""Accessibility (axe-core) gate for the public/book surface + members/kiosk pages.

The catalog, class-detail and login pages are held to full WCAG-AA on the axe
ruleset, in both light and dark: ``ACCEPTED_DEBT`` is now empty, so ANY
violation (contrast, link-name, landmark, critical, …) fails the build.
``ACCEPTED_DEBT`` remains as a ratchet escape hatch — if a future change
introduces genuinely unavoidable debt, add the rule id here with a note rather
than silencing the whole gate.

Two more surfaces get their own scans (own login/host, so they can't ride the
public loop): the members Notifications page and the public signage player. Each
carries its OWN allowlist so pre-existing chrome debt on those surfaces is never
leaked onto the public gate above — the global bar stays at zero.

Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import urlparse

from axe_playwright_python.sync_playwright import Axe
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from tests.membership.factories import (
    GuildFactory,
    MembershipPlanFactory,
    SlideshowSlideFactory,
    SlideshowZoneFactory,
)

# Tolerated a11y debt (axe rule IDs). Currently empty — these pages are fully
# AA-clean. Add a rule id here only as a documented, temporary escape hatch.
ACCEPTED_DEBT: set[str] = set()

# Per-surface allowlists for the two scans below (members hub / kiosk). Kept
# separate from ACCEPTED_DEBT so the public gate never inherits their debt. Add a
# rule id here (with a note) only for genuine pre-existing debt on that surface's
# shared chrome — never a critical-impact rule.
MEMBERS_HUB_DEBT: set[str] = {
    # Pre-existing hub-chrome debt (NOT the notifications feature): the topbar
    # ".pl-badge--version" amber pill (#eeb44b) fails AA contrast in the light theme.
    # Shared across every authed hub page; worth its own fix, out of scope for this
    # test-only pass. Serious, not critical — the gate still fails on anything worse.
    "color-contrast",
}
SIGNAGE_DEBT: set[str] = {
    # The kiosk is a deliberately chromeless full-screen deck: its slide <section>s
    # aren't wrapped in a landmark (no nav/main on a wall monitor), which trips the
    # "region" rule. By-design for an unattended sign, moderate impact — not critical.
    "region",
}


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


def _offenders_for(page, url, allowed):
    """Scan one URL (both themes) and return the offender lines the gate should fail on.

    An offender is any critical-impact violation, or any rule not in ``allowed`` — so an
    allowlist only ever tolerates non-critical, documented debt.
    """
    out = []
    for theme, impact, rule, nodes, detail in _violations(page, url):
        if impact == "critical" or rule not in allowed:
            out.append(f"{theme}: [{impact}] {rule} ({nodes} node(s)) — {detail}")
    return out


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

    def it_has_no_violations_on_the_members_notifications_page(live_server, page, login_via_code):
        # Members surface (the autouse _e2e_settings keeps the live host off PUBLIC_HOSTS),
        # signed in through the real code flow, with an unread notification so rows render.
        from core.models import Notification

        MembershipPlanFactory()
        email = "a11y-notes@example.com"
        login_via_code(email)
        user = get_user_model().objects.get(username=email)
        Notification.objects.create(user=user, trigger="x", title="Welcome to the hub", body="Say hi in your guild.")

        offenders = _offenders_for(page, f"{live_server.url}{reverse('notification_list')}", MEMBERS_HUB_DEBT)
        assert not offenders, "Critical or new (unbaselined) a11y violations on /notifications/:\n  " + "\n  ".join(
            offenders
        )

    def it_has_no_violations_on_the_v22_admin_surfaces(live_server, page, login_via_code):
        # The v22 editor pages: the guild editor's Meetings tab (studio-hours formset)
        # and Site Settings' Automations tab. Signed in as an admin; both live on the
        # members surface, so they share its chrome debt allowlist. The ?tab= param
        # seeds Alpine's active section, and axe only scans what's visible.
        MembershipPlanFactory()
        email = "a11y-admin@example.com"
        login_via_code(email)
        user = get_user_model().objects.get(username=email)
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])
        guild = GuildFactory(name="Ceramics Guild")

        pages = {
            "guild-edit-meetings": f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=meetings",
            "site-settings-automations": f"{reverse('hub_admin_site_settings')}?tab=automations",
        }
        offenders = []
        for name, path in pages.items():
            offenders += [f"{name} {o}" for o in _offenders_for(page, f"{live_server.url}{path}", MEMBERS_HUB_DEBT)]
        assert not offenders, "Critical or new (unbaselined) a11y violations:\n  " + "\n  ".join(offenders)

    def it_has_no_violations_on_the_class_flyer(live_server, page, login_via_code):
        # The printable flyer is editor-gated and mostly bespoke print chrome, so it
        # gets its own scan (as an admin) against the members-surface allowlist.
        MembershipPlanFactory()
        email = "a11y-flyer@example.com"
        login_via_code(email)
        user = get_user_model().objects.get(username=email)
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])

        offering = ClassOfferingFactory(
            title="Intro to Lost Wax Casting", status=ClassOffering.Status.PUBLISHED, is_private=False
        )
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=7),
            ends_at=timezone.now() + timedelta(days=7, hours=2),
        )

        url = f"{live_server.url}{reverse('classes:class_flyer', args=[offering.pk])}"
        offenders = _offenders_for(page, url, MEMBERS_HUB_DEBT)
        assert not offenders, "Critical or new (unbaselined) a11y violations on the flyer:\n  " + "\n  ".join(offenders)

    def it_has_no_violations_on_the_discord_link_landing(live_server, page):
        # The anon-allowed Discord link landing: a bare GET (no code/state) renders the
        # oauth-failed state, which must still be a calm, accessible page with a next step.
        url = f"{live_server.url}{reverse('hub_discord_link_callback')}"
        offenders = _offenders_for(page, url, MEMBERS_HUB_DEBT)
        assert not offenders, "Critical or new (unbaselined) a11y violations on the Discord landing:\n  " + "\n  ".join(
            offenders
        )

    def it_has_no_violations_on_the_signage_player(live_server, page, settings):
        # Kiosk surface: point the live host at SIGNAGE_HOSTS so localhost resolves there.
        settings.SIGNAGE_HOSTS = [urlparse(live_server.url).hostname]
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.signage_show_events = False
        config.save()
        zone = SlideshowZoneFactory(slug="woodshop", is_enabled=True)
        SlideshowSlideFactory(zone=zone, title="Welcome to the space")

        offenders = _offenders_for(page, f"{live_server.url}/{zone.slug}/", SIGNAGE_DEBT)
        assert not offenders, "Critical or new (unbaselined) a11y violations on the signage player:\n  " + "\n  ".join(
            offenders
        )
