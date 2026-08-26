"""BDD specs for the billing admin gate — BILLING_APPROVER now opens the dashboard."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client

from membership.models import AdminCapability, Member

pytestmark = pytest.mark.django_db


def _login_user(client: Client, username: str) -> Member:
    """A logged-in plain member (the ensure_user_has_member signal links the Member)."""
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user.member  # type: ignore[attr-defined]


def _login_billing_approver(client: Client, username: str = "biller") -> Member:
    member = _login_user(client, username)
    member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
    return member


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
