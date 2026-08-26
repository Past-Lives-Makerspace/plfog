"""BDD-style tests for billing.stripe_utils — all Stripe API calls mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import stripe

from billing import stripe_utils

pytestmark = pytest.mark.django_db


def _mock_client() -> MagicMock:
    """Create a mock StripeClient with common nested attributes."""
    client = MagicMock()
    return client


def describe_get_stripe_client():
    def it_returns_a_stripe_client(configured_billing_stripe):
        client = stripe_utils._get_stripe_client()
        assert isinstance(client, stripe.StripeClient)

    def it_raises_when_platform_secret_not_configured():
        from django.core.exceptions import ImproperlyConfigured

        with pytest.raises(ImproperlyConfigured, match="platform secret key is not set"):
            stripe_utils._get_stripe_client()


def describe_create_customer():
    @patch("billing.stripe_utils._get_stripe_client")
    def it_returns_customer_id(mock_get_client):
        client = _mock_client()
        client.v1.customers.create.return_value = MagicMock(id="cus_test_123")
        mock_get_client.return_value = client

        result = stripe_utils.create_customer(email="test@example.com", name="Jane", member_pk=42)

        assert result == "cus_test_123"
        client.v1.customers.create.assert_called_once()
        call_kwargs = client.v1.customers.create.call_args
        assert call_kwargs.kwargs["params"]["email"] == "test@example.com"
        assert call_kwargs.kwargs["options"]["idempotency_key"] == "create-customer-member-42"


def describe_create_setup_intent():
    @patch("billing.stripe_utils._get_stripe_client")
    def it_returns_client_secret_and_id(mock_get_client):
        client = _mock_client()
        client.v1.setup_intents.create.return_value = MagicMock(client_secret="seti_secret_123", id="seti_123")
        mock_get_client.return_value = client

        result = stripe_utils.create_setup_intent(customer_id="cus_test_123")

        assert result == {"client_secret": "seti_secret_123", "setup_intent_id": "seti_123"}
        call_kwargs = client.v1.setup_intents.create.call_args
        assert call_kwargs.kwargs["params"]["usage"] == "off_session"


def describe_retrieve_payment_method():
    @patch("billing.stripe_utils._get_stripe_client")
    def it_returns_card_details(mock_get_client):
        client = _mock_client()
        pm = MagicMock()
        pm.id = "pm_test_456"
        pm.card.brand = "visa"
        pm.card.last4 = "4242"
        client.v1.payment_methods.retrieve.return_value = pm
        mock_get_client.return_value = client

        result = stripe_utils.retrieve_payment_method(payment_method_id="pm_test_456")

        assert result == {"id": "pm_test_456", "brand": "visa", "last4": "4242"}

    @patch("billing.stripe_utils._get_stripe_client")
    def it_handles_no_card(mock_get_client):
        client = _mock_client()
        pm = MagicMock()
        pm.id = "pm_test_789"
        pm.card = None
        client.v1.payment_methods.retrieve.return_value = pm
        mock_get_client.return_value = client

        result = stripe_utils.retrieve_payment_method(payment_method_id="pm_test_789")

        assert result == {"id": "pm_test_789", "brand": "", "last4": ""}


def describe_attach_payment_method():
    @patch("billing.stripe_utils._get_stripe_client")
    def it_attaches_and_sets_default(mock_get_client):
        client = _mock_client()
        mock_get_client.return_value = client

        stripe_utils.attach_payment_method(customer_id="cus_123", payment_method_id="pm_456")

        client.v1.payment_methods.attach.assert_called_once_with("pm_456", params={"customer": "cus_123"})
        client.v1.customers.update.assert_called_once_with(
            "cus_123", params={"invoice_settings": {"default_payment_method": "pm_456"}}
        )


def describe_detach_payment_method():
    @patch("billing.stripe_utils._get_stripe_client")
    def it_detaches(mock_get_client):
        client = _mock_client()
        mock_get_client.return_value = client

        stripe_utils.detach_payment_method(payment_method_id="pm_456")

        client.v1.payment_methods.detach.assert_called_once_with("pm_456")


def describe_create_payment_intent():
    @patch("billing.stripe_utils._get_stripe_client")
    def it_creates_intent_and_returns_details(mock_get_client):
        client = _mock_client()
        intent = MagicMock()
        intent.id = "pi_test_789"
        intent.status = "succeeded"
        intent.latest_charge = "ch_test_001"
        client.v1.payment_intents.create.return_value = intent

        charge = MagicMock()
        charge.id = "ch_test_001"
        charge.receipt_url = "https://stripe.com/receipt/123"
        client.v1.charges.retrieve.return_value = charge
        mock_get_client.return_value = client

        result = stripe_utils.create_payment_intent(
            customer_id="cus_123",
            payment_method_id="pm_456",
            amount_cents=5000,
            description="Tab charge",
            metadata={"tab_id": "1"},
            idempotency_key="test-key-1",
        )

        assert result == {
            "id": "pi_test_789",
            "status": "succeeded",
            "charge_id": "ch_test_001",
            "receipt_url": "https://stripe.com/receipt/123",
        }
        call_kwargs = client.v1.payment_intents.create.call_args
        assert call_kwargs.kwargs["params"]["off_session"] is True
        assert call_kwargs.kwargs["options"]["idempotency_key"] == "test-key-1"

    @patch("billing.stripe_utils._get_stripe_client")
    def it_handles_no_latest_charge(mock_get_client):
        client = _mock_client()
        intent = MagicMock()
        intent.id = "pi_test_no_charge"
        intent.status = "requires_action"
        intent.latest_charge = None
        client.v1.payment_intents.create.return_value = intent
        mock_get_client.return_value = client

        result = stripe_utils.create_payment_intent(
            customer_id="cus_123",
            payment_method_id="pm_456",
            amount_cents=5000,
            description="Tab charge",
            metadata={},
            idempotency_key="test-key-2",
        )

        assert result["charge_id"] == ""
        assert result["receipt_url"] == ""


def describe_construct_webhook_event():
    @patch("billing.stripe_utils.stripe.Webhook.construct_event")
    def it_delegates_to_stripe_sdk(mock_construct, configured_billing_stripe):
        mock_event = MagicMock()
        mock_construct.return_value = mock_event

        result = stripe_utils.construct_webhook_event(payload=b"raw_body", sig_header="sig_123")

        assert result == mock_event
        mock_construct.assert_called_once_with(
            payload=b"raw_body",
            sig_header="sig_123",
            secret="whsec_fake_for_testing",
        )

    def it_raises_when_platform_webhook_secret_not_configured():
        from django.core.exceptions import ImproperlyConfigured

        with pytest.raises(ImproperlyConfigured, match="platform webhook secret is not set"):
            stripe_utils.construct_webhook_event(payload=b"{}", sig_header="t=1,v1=foo")


def describe_active_key_selection():
    def _configure(*, test_mode: bool):
        from billing.models import BillingSettings

        bs = BillingSettings.load()
        bs.test_mode = test_mode
        bs.connect_platform_secret_key = "sk_live_secret"
        bs.connect_platform_webhook_secret = "whsec_live"
        bs.test_connect_platform_secret_key = "sk_test_secret"
        bs.test_connect_platform_webhook_secret = "whsec_test"
        bs.save()
        return bs

    def it_reads_the_test_secret_when_test_mode_on():
        _configure(test_mode=True)
        assert stripe_utils._platform_secret_key() == "sk_test_secret"

    def it_reads_the_live_secret_when_test_mode_off():
        _configure(test_mode=False)
        assert stripe_utils._platform_secret_key() == "sk_live_secret"

    def it_reads_the_test_webhook_secret_when_test_mode_on():
        _configure(test_mode=True)
        assert stripe_utils._platform_webhook_secret() == "whsec_test"

    def it_reads_the_live_webhook_secret_when_test_mode_off():
        _configure(test_mode=False)
        assert stripe_utils._platform_webhook_secret() == "whsec_live"


def describe_verify_platform_credentials():
    @patch("billing.stripe_utils.stripe.StripeClient")
    def it_returns_account_metadata(mock_stripe_client_cls):
        mock_account = MagicMock(
            id="acct_verified_001",
            charges_enabled=True,
            country="US",
        )
        mock_account.business_profile.name = "Past Lives Makerspace"
        client = MagicMock()
        client.v1.accounts.retrieve.return_value = mock_account
        mock_stripe_client_cls.return_value = client

        result = stripe_utils.verify_platform_credentials("sk_test_real")
        assert result["stripe_account_id"] == "acct_verified_001"
        assert result["display_name"] == "Past Lives Makerspace"
        assert result["charges_enabled"] is True
        assert result["country"] == "US"
        client.v1.accounts.retrieve.assert_called_once_with("self")

    @patch("billing.stripe_utils.stripe.StripeClient")
    def it_falls_back_to_account_id_when_no_business_profile_name(mock_stripe_client_cls):
        mock_account = MagicMock(id="acct_no_name", charges_enabled=False, country="")
        mock_account.business_profile = None
        client = MagicMock()
        client.v1.accounts.retrieve.return_value = mock_account
        mock_stripe_client_cls.return_value = client

        result = stripe_utils.verify_platform_credentials("sk_test_real")
        assert result["display_name"] == "acct_no_name"
        assert result["charges_enabled"] is False
        assert result["country"] == ""


def describe_create_refund():
    @patch("billing.stripe_utils._get_stripe_client")
    def it_refunds_the_full_payment_when_amount_is_none(mock_get_client):
        client = _mock_client()
        client.v1.refunds.create.return_value = MagicMock(id="re_full_1", status="succeeded", amount=6500)
        mock_get_client.return_value = client

        result = stripe_utils.create_refund(payment_intent_id="pi_refund_1", idempotency_key="pay-refund-1-a1")

        assert result == {"id": "re_full_1", "status": "succeeded", "amount": 6500}
        call_kwargs = client.v1.refunds.create.call_args
        assert call_kwargs.kwargs["params"] == {"payment_intent": "pi_refund_1"}
        assert call_kwargs.kwargs["options"]["idempotency_key"] == "pay-refund-1-a1"

    @patch("billing.stripe_utils._get_stripe_client")
    def it_passes_the_amount_for_a_partial_refund(mock_get_client):
        client = _mock_client()
        client.v1.refunds.create.return_value = MagicMock(id="re_part_1", status="pending", amount=2000)
        mock_get_client.return_value = client

        result = stripe_utils.create_refund(
            payment_intent_id="pi_refund_2", amount_cents=2000, idempotency_key="pay-refund-2-a1"
        )

        assert result == {"id": "re_part_1", "status": "pending", "amount": 2000}
        call_kwargs = client.v1.refunds.create.call_args
        assert call_kwargs.kwargs["params"] == {"payment_intent": "pi_refund_2", "amount": 2000}

    @patch("billing.stripe_utils._get_stripe_client")
    def it_propagates_stripe_errors(mock_get_client):
        client = _mock_client()
        client.v1.refunds.create.side_effect = stripe.StripeError("Charge has already been refunded.")
        mock_get_client.return_value = client

        with pytest.raises(stripe.StripeError, match="already been refunded"):
            stripe_utils.create_refund(payment_intent_id="pi_refund_3", idempotency_key="pay-refund-3-a1")
