"""BDD-style tests for billing views — payment method setup, confirm, remove."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client, RequestFactory

from billing.views import confirm_setup, create_setup_intent_api, remove_payment_method, setup_payment_method
from membership.models import Member
from tests.billing.factories import TabFactory

pytestmark = pytest.mark.django_db


def _user_without_member() -> User:
    """Create a User with no Member (deletes auto-created one and clears cache)."""
    user = User.objects.create_user(username=f"orphan_{User.objects.count()}", password="pass")
    Member.objects.filter(user=user).delete()
    # Clear Django's cached reverse relation
    if hasattr(user, "_member_cache"):
        del user._member_cache
    return User.objects.get(pk=user.pk)


def describe_no_member_guard():
    """Test the member-is-None branches via RequestFactory to bypass signal auto-creation."""

    def it_redirects_setup_payment_method(rf: RequestFactory):
        user = _user_without_member()
        request = rf.get("/billing/payment-method/setup/")
        request.user = user
        response = setup_payment_method(request)
        assert response.status_code == 302

    def it_returns_400_for_create_setup_intent(rf: RequestFactory):
        user = _user_without_member()
        request = rf.post("/billing/api/setup-intent/")
        request.user = user
        response = create_setup_intent_api(request)
        assert response.status_code == 400

    def it_redirects_confirm_setup(rf: RequestFactory):
        user = _user_without_member()
        request = rf.post("/billing/payment-method/confirm/", {"payment_method_id": "pm_test"})
        request.user = user
        response = confirm_setup(request)
        assert response.status_code == 302

    def it_redirects_remove_payment_method(rf: RequestFactory):
        user = _user_without_member()
        request = rf.post("/billing/payment-method/remove/")
        request.user = user
        response = remove_payment_method(request)
        assert response.status_code == 302


def describe_setup_payment_method():
    def it_requires_login(client: Client):
        response = client.get("/billing/payment-method/setup/")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_renders_for_member(client: Client):
        User.objects.create_user(username="pm_user", password="pass")
        client.login(username="pm_user", password="pass")

        response = client.get("/billing/payment-method/setup/")

        assert response.status_code == 200
        assert b"stripe_publishable_key" in response.content or response.context["stripe_publishable_key"] is not None

    def it_handles_user_with_no_member(client: Client):
        user = User.objects.create_user(username="no_member_pm", password="pass")
        Member.objects.filter(user=user).delete()
        client.login(username="no_member_pm", password="pass")

        response = client.get("/billing/payment-method/setup/")

        # Renders without error regardless of member state
        assert response.status_code in (200, 302)

    def it_redirects_to_the_dashboard_when_tab_payments_disabled(client: Client):
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.tab_payments_enabled = False
        config.save()
        User.objects.create_user(username="pm_off", password="pass")
        client.login(username="pm_off", password="pass")

        response = client.get("/billing/payment-method/setup/")

        assert response.status_code == 302
        assert response.url == "/"


def describe_create_setup_intent_api():
    def it_requires_login(client: Client):
        response = client.post("/billing/api/setup-intent/")
        assert response.status_code == 302

    def it_rejects_get_requests(client: Client):
        User.objects.create_user(username="get_user", password="pass")
        client.login(username="get_user", password="pass")

        response = client.get("/billing/api/setup-intent/")

        assert response.status_code == 405

    @patch("billing.stripe_utils.create_customer")
    @patch("billing.views.stripe_utils.create_setup_intent")
    def it_creates_customer_and_intent(mock_setup, mock_customer, client: Client):
        mock_customer.return_value = "cus_new_123"
        mock_setup.return_value = {"client_secret": "seti_secret", "setup_intent_id": "seti_123"}
        User.objects.create_user(username="setup_user", password="pass")
        client.login(username="setup_user", password="pass")

        response = client.post("/billing/api/setup-intent/")

        assert response.status_code == 200
        data = response.json()
        assert data["client_secret"] == "seti_secret"
        mock_customer.assert_called_once()

    @patch("billing.views.stripe_utils.create_setup_intent")
    def it_skips_customer_creation_if_already_exists(mock_setup, client: Client):
        mock_setup.return_value = {"client_secret": "seti_secret", "setup_intent_id": "seti_123"}
        user = User.objects.create_user(username="existing_cus", password="pass")
        TabFactory(member=user.member, stripe_customer_id="cus_existing")
        client.login(username="existing_cus", password="pass")

        response = client.post("/billing/api/setup-intent/")

        assert response.status_code == 200


def describe_confirm_setup():
    def it_requires_login(client: Client):
        response = client.post("/billing/payment-method/confirm/")
        assert response.status_code == 302

    @patch("billing.stripe_utils.attach_payment_method")
    @patch("billing.stripe_utils.retrieve_payment_method")
    def it_updates_tab_with_payment_method(mock_retrieve, mock_attach, client: Client):
        mock_retrieve.return_value = {"id": "pm_new_456", "brand": "visa", "last4": "4242"}
        user = User.objects.create_user(username="confirm_user", password="pass")
        TabFactory(member=user.member, stripe_customer_id="cus_123")
        client.login(username="confirm_user", password="pass")

        response = client.post("/billing/payment-method/confirm/", {"payment_method_id": "pm_new_456"})

        assert response.status_code == 302
        assert response.url == "/tab/"
        mock_attach.assert_called_once()

    @patch("billing.stripe_utils.retrieve_payment_method")
    def it_skips_attach_when_no_customer_id(mock_retrieve, client: Client):
        mock_retrieve.return_value = {"id": "pm_test_789", "brand": "mastercard", "last4": "5678"}
        user = User.objects.create_user(username="no_cus_confirm", password="pass")
        TabFactory(member=user.member, stripe_customer_id="")
        client.login(username="no_cus_confirm", password="pass")

        response = client.post("/billing/payment-method/confirm/", {"payment_method_id": "pm_test_789"})

        assert response.status_code == 302
        assert response.url == "/tab/"

    def it_redirects_if_no_payment_method_id(client: Client):
        User.objects.create_user(username="no_pm_id", password="pass")
        client.login(username="no_pm_id", password="pass")

        response = client.post("/billing/payment-method/confirm/", {})

        assert response.status_code == 302
        assert "setup" in response.url


def describe_remove_payment_method():
    def it_requires_login(client: Client):
        response = client.post("/billing/payment-method/remove/")
        assert response.status_code == 302

    @patch("billing.stripe_utils.detach_payment_method")
    def it_clears_tab_payment_fields(mock_detach, client: Client):
        user = User.objects.create_user(username="remove_user", password="pass")
        tab = TabFactory(
            member=user.member,
            stripe_payment_method_id="pm_old_789",
            payment_method_last4="1234",
            payment_method_brand="visa",
        )
        client.login(username="remove_user", password="pass")

        response = client.post("/billing/payment-method/remove/")

        assert response.status_code == 302
        tab.refresh_from_db()
        assert tab.stripe_payment_method_id == ""
        assert tab.payment_method_last4 == ""
        mock_detach.assert_called_once_with(payment_method_id="pm_old_789")

    def it_handles_no_payment_method(client: Client):
        user = User.objects.create_user(username="no_pm_remove", password="pass")
        TabFactory(member=user.member, stripe_payment_method_id="")
        client.login(username="no_pm_remove", password="pass")

        response = client.post("/billing/payment-method/remove/")

        assert response.status_code == 302

    def it_handles_user_with_no_member(client: Client):
        user = User.objects.create_user(username="no_mem_remove", password="pass")
        Member.objects.filter(user=user).delete()
        client.login(username="no_mem_remove", password="pass")

        response = client.post("/billing/payment-method/remove/")

        assert response.status_code in (200, 302)
