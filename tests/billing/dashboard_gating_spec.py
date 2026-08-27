"""BDD specs for the billing admin gate — BILLING_APPROVER now opens the dashboard."""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.test import Client

from core.models import SiteConfiguration
from membership.models import AdminCapability, Member

pytestmark = pytest.mark.django_db

ALL_TABS = ("overview", "open-tabs", "payments", "settings", "stripe")


def _login_user(client: Client, username: str) -> Member:
    """A logged-in plain member (the ensure_user_has_member signal links the Member)."""
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user.member  # type: ignore[attr-defined]


def _login_billing_approver(client: Client, username: str = "biller") -> Member:
    member = _login_user(client, username)
    member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
    return member


def _login_superuser(client: Client, username: str = "fogadmin") -> User:
    User.objects.create_superuser(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return User.objects.get(username=username)


def _disable_my_tab() -> None:
    config = SiteConfiguration.load()
    config.my_tab_enabled = False
    config.save()


def _rendered_tab_links(content: str) -> set[str]:
    """The ``?tab=X`` values that appear as nav links (payments filter links carry a trailing ``&``)."""
    return set(re.findall(r'href="\?tab=([a-z-]+)"', content))


def _reachable_tabs(client: Client) -> set[str]:
    """Tabs that render themselves on a direct ``?tab=`` hit (200 and active_tab is that tab)."""
    reachable = set()
    for tab in ALL_TABS:
        resp = client.get(f"/billing/admin/dashboard/?tab={tab}")
        if resp.status_code == 200 and resp.context["active_tab"] == tab:
            reachable.add(tab)
    return reachable


def _assert_link_reachability_parity(client: Client) -> set[str]:
    """A rendered nav link always points at a genuinely reachable tab, and every reachable
    tab has a link — except the single-tab case, where the nav is suppressed entirely.
    Returns the reachable set for the caller to pin against the matrix.
    """
    content = client.get("/billing/admin/dashboard/").content.decode()
    rendered = _rendered_tab_links(content)
    reachable = _reachable_tabs(client)
    assert rendered <= reachable  # soundness: no link falls back or 403s
    if len(reachable) > 1:
        assert rendered == reachable  # completeness: nav lists every reachable tab
    else:
        assert rendered == set()  # single reachable tab → nav chrome suppressed
    return reachable


def describe_billing_admin_access():
    def it_admits_a_billing_administrator_to_the_dashboard(client: Client):
        _login_billing_approver(client)
        response = client.get("/billing/admin/dashboard/")
        assert response.status_code == 200
        assert response.context["viewer_is_fog_admin"] is False

    def it_forbids_a_plain_member(client: Client):
        _login_user(client, "plainmember")
        response = client.get("/billing/admin/dashboard/")
        assert response.status_code == 403

    def it_admits_a_billing_administrator_to_reports(client: Client):
        _login_billing_approver(client, "biller2")
        response = client.get("/billing/admin/reports/")
        assert response.status_code == 200

    def it_still_admits_a_fog_admin(client: Client):
        User.objects.create_superuser(username="fogadmin", password="pass", email="fogadmin@example.com")
        client.login(username="fogadmin", password="pass")
        response = client.get("/billing/admin/dashboard/")
        assert response.status_code == 200
        assert response.context["viewer_is_fog_admin"] is True

    def describe_settings_and_stripe_tabs():
        def it_403s_the_settings_tab_for_a_non_admin_approver(client: Client):
            _login_billing_approver(client, "biller3")
            response = client.get("/billing/admin/dashboard/?tab=settings")
            assert response.status_code == 403

        def it_403s_the_stripe_tab_for_a_non_admin_approver(client: Client):
            _login_billing_approver(client, "biller4")
            response = client.get("/billing/admin/dashboard/?tab=stripe")
            assert response.status_code == 403

        def it_hides_the_admin_only_tab_links_from_the_approver(client: Client):
            _login_billing_approver(client, "biller5")
            content = client.get("/billing/admin/dashboard/").content.decode()
            assert "?tab=settings" not in content
            assert "?tab=stripe" not in content

        def it_keeps_the_admin_only_tabs_for_a_fog_admin(client: Client):
            User.objects.create_superuser(username="fogadmin2", password="pass", email="fogadmin2@example.com")
            client.login(username="fogadmin2", password="pass")
            content = client.get("/billing/admin/dashboard/").content.decode()
            assert "?tab=settings" in content
            assert "?tab=stripe" in content

        def it_keeps_the_credential_views_admin_only(client: Client):
            _login_billing_approver(client, "biller6")
            response = client.post("/billing/admin/connect-platform/test/")
            assert response.status_code == 403


def describe_tab_link_reachability_parity():
    """The §6.1 matrix: rendered tab links equal the 200-reachable tabs, per (flag × role) cell."""

    def it_matches_for_a_fog_admin_with_the_flag_on(client: Client):
        _login_superuser(client, "parity_admin_on")
        reachable = _assert_link_reachability_parity(client)
        assert reachable == {"overview", "open-tabs", "payments", "settings", "stripe"}

    def it_matches_for_a_fog_admin_with_the_flag_off(client: Client):
        _disable_my_tab()
        _login_superuser(client, "parity_admin_off")
        reachable = _assert_link_reachability_parity(client)
        assert reachable == {"payments", "settings", "stripe"}

    def it_matches_for_an_approver_with_the_flag_on(client: Client):
        _login_billing_approver(client, "parity_appr_on")
        reachable = _assert_link_reachability_parity(client)
        assert reachable == {"overview", "open-tabs", "payments"}

    def it_matches_for_an_approver_with_the_flag_off(client: Client):
        _disable_my_tab()
        _login_billing_approver(client, "parity_appr_off")
        reachable = _assert_link_reachability_parity(client)
        assert reachable == {"payments"}  # single tab → nav suppressed (asserted in the helper)
