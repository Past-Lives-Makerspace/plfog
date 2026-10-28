"""BDD specs for the cubby rental Stripe helpers in billing.stripe_utils (#978).

The client is a MagicMock (the stripe_utils_spec idiom); subscription and invoice payment answers
are real stripe objects built with ``construct_from``, so the shape read is the SDK's own
(API 2026-08-26.dahlia: the period end on the item, the payment intent through invoice payments).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import stripe

from billing import stripe_utils

pytestmark = pytest.mark.django_db


def _subscription(**overrides: Any) -> stripe.Subscription:
    values: dict[str, Any] = {
        "id": "sub_cub",
        "object": "subscription",
        "status": "active",
        "customer": "cus_cub",
        "metadata": {"kind": "cubby_rental", "rental_pk": "7"},
        "cancel_at_period_end": False,
        "cancel_at": None,
        "default_payment_method": "pm_checkout",
        "latest_invoice": "in_cub",
        "items": {
            "object": "list",
            "data": [
                {
                    "id": "si_cub",
                    "object": "subscription_item",
                    "current_period_end": 1795000000,
                    "price": {"id": "price_cub", "object": "price", "product": "prod_cub", "unit_amount": 2200},
                }
            ],
        },
    }
    values.update(overrides)
    return stripe.Subscription.construct_from(values, "sk_test")


@pytest.fixture
def client():
    mock = MagicMock()
    with patch("billing.stripe_utils._get_stripe_client", return_value=mock):
        yield mock


def describe_create_subscription_checkout_session():
    def it_starts_a_monthly_subscription_at_the_chosen_amount_on_the_members_customer(client):
        client.v1.checkout.sessions.create.return_value = MagicMock(id="cs_cub", url="https://checkout.example/cs")
        metadata = {"kind": "cubby_rental", "rental_pk": "7"}

        result = stripe_utils.create_subscription_checkout_session(
            customer_id="cus_cub",
            amount_cents=2200,
            product_name="Cubby B3, Glaze Wall (Ceramics)",
            description="Cubby B3 on the Glaze Wall, Ceramics",
            success_url="https://members.example/ok",
            cancel_url="https://members.example/back",
            metadata=metadata,
            idempotency_key="cubby-checkout-7",
            expires_at=1795000000,
        )

        assert result == {"id": "cs_cub", "url": "https://checkout.example/cs"}
        call = client.v1.checkout.sessions.create.call_args
        params = call.kwargs["params"]
        assert params["mode"] == "subscription"
        assert params["customer"] == "cus_cub"
        assert params["payment_method_types"] == ["card"]
        assert "customer_email" not in params
        assert params["line_items"] == [
            {
                "quantity": 1,
                "price_data": {
                    "currency": "usd",
                    "unit_amount": 2200,
                    "recurring": {"interval": "month"},
                    "product_data": {"name": "Cubby B3, Glaze Wall (Ceramics)"},
                },
            }
        ]
        assert params["metadata"] == metadata
        assert params["subscription_data"] == {
            "metadata": metadata,
            "description": "Cubby B3 on the Glaze Wall, Ceramics",
        }
        assert params["expires_at"] == 1795000000
        assert call.kwargs["options"] == {"idempotency_key": "cubby-checkout-7"}

    def it_answers_an_empty_url_when_stripe_gives_none(client):
        client.v1.checkout.sessions.create.return_value = MagicMock(id="cs_cub", url=None)
        result = stripe_utils.create_subscription_checkout_session(
            customer_id="c", amount_cents=100, product_name="p", description="d", success_url="s", cancel_url="c",
            metadata={}, idempotency_key="k", expires_at=1,
        )  # fmt: skip
        assert result["url"] == ""


def describe_retrieve_subscription():
    def it_reads_the_item_price_product_and_period_end(client):
        client.v1.subscriptions.retrieve.return_value = _subscription()
        assert stripe_utils.retrieve_subscription(subscription_id="sub_cub") == {
            "id": "sub_cub",
            "status": "active",
            "customer": "cus_cub",
            "metadata": {"kind": "cubby_rental", "rental_pk": "7"},
            "cancel_at_period_end": False,
            "cancel_at": None,
            "default_payment_method": "pm_checkout",
            "latest_invoice": "in_cub",
            "item_id": "si_cub",
            "product_id": "prod_cub",
            "unit_amount": 2200,
            "current_period_end": 1795000000,
        }
        client.v1.subscriptions.retrieve.assert_called_once_with("sub_cub")

    def it_reads_expanded_objects_and_empty_ones(client):
        sub = _subscription(
            customer={"id": "cus_x", "object": "customer"},
            default_payment_method=None,
            latest_invoice={"id": "in_x", "object": "invoice"},
            metadata=None,
        )
        sub["items"]["data"][0]["price"]["product"] = {"id": "prod_x", "object": "product"}
        client.v1.subscriptions.retrieve.return_value = sub
        result = stripe_utils.retrieve_subscription(subscription_id="sub_cub")
        assert (result["customer"], result["default_payment_method"], result["latest_invoice"]) == ("cus_x", "", "in_x")
        assert (result["product_id"], result["metadata"]) == ("prod_x", {})


def describe_move_subscription_card_to_customer():
    def it_makes_the_card_the_customers_default_and_clears_the_subscriptions_own(client):
        stripe_utils.move_subscription_card_to_customer(
            customer_id="cus_cub", subscription_id="sub_cub", payment_method_id="pm_checkout"
        )
        client.v1.customers.update.assert_called_once_with(
            "cus_cub", params={"invoice_settings": {"default_payment_method": "pm_checkout"}}
        )
        client.v1.subscriptions.update.assert_called_once_with("sub_cub", params={"default_payment_method": ""})
        # Nothing is detached: the My Tab card stays attached and is still charged by its own id.
        client.v1.payment_methods.detach.assert_not_called()


def describe_update_subscription_amount():
    def it_puts_a_new_inline_price_on_the_same_product_from_the_next_charge(client):
        stripe_utils.update_subscription_amount(
            subscription_id="sub_cub", item_id="si_cub", product_id="prod_cub", amount_cents=2500
        )
        client.v1.subscriptions.update.assert_called_once_with(
            "sub_cub",
            params={
                "items": [
                    {
                        "id": "si_cub",
                        "price_data": {
                            "currency": "usd",
                            "product": "prod_cub",
                            "unit_amount": 2500,
                            "recurring": {"interval": "month"},
                        },
                    }
                ],
                "proration_behavior": "none",
            },
        )


def describe_set_subscription_cancel_at_period_end():
    def it_cancels_at_period_end(client):
        stripe_utils.set_subscription_cancel_at_period_end(subscription_id="sub_cub", cancel=True)
        client.v1.subscriptions.update.assert_called_once_with("sub_cub", params={"cancel_at_period_end": True})

    def it_keeps_it_clearing_any_cancel_date_too(client):
        stripe_utils.set_subscription_cancel_at_period_end(subscription_id="sub_cub", cancel=False)
        client.v1.subscriptions.update.assert_called_once_with(
            "sub_cub", params={"cancel_at": "", "cancel_at_period_end": False}
        )


def describe_cancel_subscription():
    def it_cancels_now(client):
        stripe_utils.cancel_subscription(subscription_id="sub_cub")
        client.v1.subscriptions.cancel.assert_called_once_with("sub_cub")


def describe_create_payment_method_portal_session():
    def it_opens_straight_on_update_payment_method_and_comes_back(client):
        client.v1.billing_portal.sessions.create.return_value = MagicMock(url="https://billing.stripe.example/s")
        url = stripe_utils.create_payment_method_portal_session(customer_id="cus_cub", return_url="https://m.example/t")
        assert url == "https://billing.stripe.example/s"
        client.v1.billing_portal.sessions.create.assert_called_once_with(
            params={
                "customer": "cus_cub",
                "return_url": "https://m.example/t",
                "flow_data": {
                    "type": "payment_method_update",
                    "after_completion": {"type": "redirect", "redirect": {"return_url": "https://m.example/t"}},
                },
            }
        )


def describe_invoice_payment_intent():
    def _payments(*intents: Any) -> stripe.ListObject:
        data = [
            {
                "id": f"inpay_{i}",
                "object": "invoice_payment",
                "payment": {"type": "payment_intent", "payment_intent": pi},
            }
            for i, pi in enumerate(intents)
        ]
        return stripe.ListObject.construct_from({"object": "list", "data": data}, "sk_test")

    def it_reads_the_paid_payment_intent_from_the_invoices_payments(client):
        client.v1.invoice_payments.list.return_value = _payments(None, "pi_cub")
        assert stripe_utils.invoice_payment_intent(invoice_id="in_cub") == "pi_cub"
        client.v1.invoice_payments.list.assert_called_once_with(params={"invoice": "in_cub", "status": "paid"})

    def it_reads_an_expanded_payment_intent_and_answers_empty_when_there_is_none(client):
        client.v1.invoice_payments.list.return_value = _payments({"id": "pi_x", "object": "payment_intent"})
        assert stripe_utils.invoice_payment_intent(invoice_id="in_cub") == "pi_x"
        client.v1.invoice_payments.list.return_value = _payments()
        assert stripe_utils.invoice_payment_intent(invoice_id="in_cub") == ""


def describe_retrieve_checkout_session_for_a_subscription():
    def it_names_the_subscription_invoice_and_metadata(client):
        client.v1.checkout.sessions.retrieve.return_value = stripe.checkout.Session.construct_from(
            {
                "id": "cs_cub",
                "object": "checkout.session",
                "url": None,
                "status": "complete",
                "payment_status": "paid",
                "payment_intent": None,
                "amount_total": 2200,
                "subscription": "sub_cub",
                "invoice": {"id": "in_cub", "object": "invoice"},
                "metadata": {"kind": "cubby_rental"},
            },
            "sk_test",
        )
        result = stripe_utils.retrieve_checkout_session(session_id="cs_cub")
        assert (result["subscription"], result["invoice"], result["metadata"]) == (
            "sub_cub",
            "in_cub",
            {"kind": "cubby_rental"},
        )

    def it_answers_empty_for_a_one_off_payment(client):
        client.v1.checkout.sessions.retrieve.return_value = stripe.checkout.Session.construct_from(
            {
                "id": "cs_pay",
                "object": "checkout.session",
                "url": None,
                "status": "open",
                "payment_status": "unpaid",
                "payment_intent": None,
                "amount_total": 100,
                "subscription": None,
                "invoice": None,
                "metadata": None,
            },
            "sk_test",
        )
        result = stripe_utils.retrieve_checkout_session(session_id="cs_pay")
        assert (result["subscription"], result["invoice"], result["metadata"]) == ("", "", {})


def describe_create_setup_intent_tag():
    def it_tags_the_my_tab_card_page_intent(client):
        client.v1.setup_intents.create.return_value = MagicMock(client_secret="s", id="seti_1")
        stripe_utils.create_setup_intent(customer_id="cus_cub")
        assert client.v1.setup_intents.create.call_args.kwargs["params"]["metadata"] == {"kind": "tab_payment_method"}
