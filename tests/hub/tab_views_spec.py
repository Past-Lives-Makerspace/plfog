"""BDD specs for hub tab views (tab_detail, tab_history)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import Client

from billing.models import TabCharge
from core.models import SiteConfiguration
from membership.signals import ensure_user_has_member
from tests.billing.factories import TabChargeFactory, TabEntryFactory, TabFactory

pytestmark = pytest.mark.django_db


def _disable_tab_payments():
    config = SiteConfiguration.load()
    config.my_tab_enabled = False
    config.save()


def describe_tab_detail():
    def it_requires_login(client: Client):
        response = client.get("/tab/")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_renders_for_member_with_no_tab(client: Client):
        User.objects.create_user(username="u1", password="pass")
        client.login(username="u1", password="pass")

        response = client.get("/tab/")

        assert response.status_code == 200
        assert response.context["tab"] is not None  # Tab created lazily

    def it_redirects_to_the_dashboard_when_tab_payments_disabled(client: Client):
        _disable_tab_payments()
        User.objects.create_user(username="tab_off", password="pass")
        client.login(username="tab_off", password="pass")

        response = client.get("/tab/")

        assert response.status_code == 302
        assert response.url == "/"
        from django.contrib.messages import get_messages

        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert "My Tab isn't available right now." in messages

    def it_handles_user_with_no_member_linked(client: Client):
        """When user has no Member, _get_member returns None → tab is None."""
        post_save.disconnect(ensure_user_has_member, sender=User)
        try:
            User.objects.create_user(username="orphan", password="pass")
            client.login(username="orphan", password="pass")
            response = client.get("/tab/")
            assert response.status_code == 200
            assert response.context["tab"] is None
        finally:
            post_save.connect(ensure_user_has_member, sender=User)

    def it_shows_pending_entries(client: Client):
        user = User.objects.create_user(username="has_entries", password="pass")
        tab = TabFactory(member=user.member)
        TabEntryFactory(tab=tab, description="Laser cutter", amount=Decimal("15.00"))
        client.login(username="has_entries", password="pass")

        response = client.get("/tab/")

        assert response.status_code == 200
        assert len(response.context["entries"]) == 1
        assert b"Laser cutter" in response.content

    def describe_payment_method_section():
        def it_shows_add_card_link_when_no_method_on_file(client: Client):
            User.objects.create_user(username="no_card", password="pass")
            client.login(username="no_card", password="pass")

            response = client.get("/tab/")

            assert response.status_code == 200
            assert b"No card on file" in response.content
            assert b"/billing/payment-method/setup/" in response.content

        def it_shows_saved_card_and_manage_link_when_method_on_file(client: Client):
            user = User.objects.create_user(username="has_card", password="pass")
            TabFactory(
                member=user.member,
                stripe_payment_method_id="pm_test_123",
                payment_method_brand="visa",
                payment_method_last4="4242",
            )
            client.login(username="has_card", password="pass")

            response = client.get("/tab/")

            assert response.status_code == 200
            assert b"Visa" in response.content
            assert b"4242" in response.content
            assert b"Manage card" in response.content
            assert b"/billing/payment-method/setup/" in response.content

    def it_rejects_post_requests(client: Client):
        """Tab detail is GET-only — adding items happens from guild pages, not here."""
        User.objects.create_user(username="poster", password="pass")
        client.login(username="poster", password="pass")

        response = client.post("/tab/", {"description": "Test", "amount": "10.00"})

        assert response.status_code == 405

    def it_does_not_render_add_to_tab_section(client: Client):
        user = User.objects.create_user(username="viewer", password="pass")
        TabFactory(member=user.member, stripe_payment_method_id="pm_test")
        client.login(username="viewer", password="pass")

        response = client.get("/tab/")

        assert response.status_code == 200
        import re

        # Strip the base template's <head>/<title> metadata — only the page body matters
        body = response.content
        body = re.sub(rb"<head\b.*?</head>", b"", body, flags=re.DOTALL)
        assert b"Add Item" not in body
        # No self-service form heading or button in the page body
        assert b"tab-add-form" not in body


def describe_tab_history():
    def it_requires_login(client: Client):
        response = client.get("/tab/history/")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_renders_empty_history(client: Client):
        User.objects.create_user(username="empty_hist", password="pass")
        client.login(username="empty_hist", password="pass")

        response = client.get("/tab/history/")

        assert response.status_code == 200
        assert len(response.context["charges"]) == 0

    def it_redirects_to_the_dashboard_when_tab_payments_disabled(client: Client):
        _disable_tab_payments()
        User.objects.create_user(username="hist_off", password="pass")
        client.login(username="hist_off", password="pass")

        response = client.get("/tab/history/")

        assert response.status_code == 302
        assert response.url == "/"

    def it_handles_user_with_no_member(client: Client):
        post_save.disconnect(ensure_user_has_member, sender=User)
        try:
            User.objects.create_user(username="orphan2", password="pass")
            client.login(username="orphan2", password="pass")
            response = client.get("/tab/history/")
            assert response.status_code == 200
            assert list(response.context["charges"]) == []
        finally:
            post_save.connect(ensure_user_has_member, sender=User)

    def it_shows_past_charges(client: Client):
        user = User.objects.create_user(username="with_charges", password="pass")
        tab = TabFactory(member=user.member)
        TabChargeFactory(tab=tab, status=TabCharge.Status.SUCCEEDED, amount=Decimal("50.00"))
        TabChargeFactory(tab=tab, status=TabCharge.Status.FAILED, amount=Decimal("25.00"))
        client.login(username="with_charges", password="pass")

        response = client.get("/tab/history/")

        assert response.status_code == 200
        assert len(response.context["charges"]) == 2

    def it_excludes_pending_charges(client: Client):
        user = User.objects.create_user(username="pending_only", password="pass")
        tab = TabFactory(member=user.member)
        TabChargeFactory(tab=tab, status=TabCharge.Status.PENDING, amount=Decimal("10.00"))
        TabChargeFactory(tab=tab, status=TabCharge.Status.SUCCEEDED, amount=Decimal("30.00"))
        client.login(username="pending_only", password="pass")

        response = client.get("/tab/history/")

        assert len(response.context["charges"]) == 1
