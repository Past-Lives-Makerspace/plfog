"""Tests for membership.stripe_utils."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from membership.stripe_utils import create_checkout_session, get_stripe_key
from tests.membership.factories import BuyableFactory

pytestmark = pytest.mark.django_db


def describe_get_stripe_key():
    def it_returns_stripe_secret_key(settings):
        settings.STRIPE_SECRET_KEY = "sk_test_abc123"
        assert get_stripe_key() == "sk_test_abc123"


def describe_create_checkout_session():
    @patch("membership.stripe_utils.stripe.checkout.Session.create")
    def it_calls_stripe_with_correct_params(mock_create):
        mock_session = MagicMock()
        mock_session.id = "cs_test_123"
        mock_session.url = "https://checkout.stripe.com/test"
        mock_create.return_value = mock_session

        buyable = BuyableFactory(name="Test Item", unit_price=Decimal("25.00"))

        result = create_checkout_session(
            buyable=buyable,
            quantity=2,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

        assert result.id == "cs_test_123"
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["mode"] == "payment"
        assert call_kwargs["success_url"] == "https://example.com/success"
        assert call_kwargs["cancel_url"] == "https://example.com/cancel"
        assert call_kwargs["line_items"][0]["quantity"] == 2
        assert call_kwargs["line_items"][0]["price_data"]["unit_amount"] == 2500

    @patch("membership.stripe_utils.stripe.checkout.Session.create")
    def it_sets_api_key(mock_create, settings):
        import stripe

        settings.STRIPE_SECRET_KEY = "sk_test_key_check"
        mock_create.return_value = MagicMock()
        buyable = BuyableFactory(name="Key Check Item")

        create_checkout_session(
            buyable=buyable,
            quantity=1,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

        assert stripe.api_key == "sk_test_key_check"
