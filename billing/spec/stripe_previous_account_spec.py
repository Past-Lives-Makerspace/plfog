"""BDD specs for routing Stripe calls between the active and the previous account (#702)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import stripe
from django.test import Client

from billing import refunds, stripe_utils
from billing.models import BillingSettings, PaymentRefund
from classes.factories import RegistrationFactory
from classes.models import Registration
from tests.billing.factories import TabFactory

pytestmark = pytest.mark.django_db

ACTIVE_KEY = "sk_live_new_account"
PREVIOUS_KEY = "sk_live_old_account"
ACTIVE_WEBHOOK = "whsec_new_account"
PREVIOUS_WEBHOOK = "whsec_old_account"
TEST_ACTIVE_KEY = "sk_test_new_account"
TEST_PREVIOUS_KEY = "sk_test_old_account"
TEST_ACTIVE_WEBHOOK = "whsec_test_new_account"
TEST_PREVIOUS_WEBHOOK = "whsec_test_old_account"
ACCOUNTS_WEBHOOK = "whsec_connected_accounts"


def _missing() -> stripe.InvalidRequestError:
    return stripe.InvalidRequestError("No such object", param="id", code="resource_missing")


def _configure(*, previous_key: str = PREVIOUS_KEY, previous_webhook: str = PREVIOUS_WEBHOOK) -> BillingSettings:
    bs = BillingSettings.load()
    bs.test_mode = False
    bs.connect_platform_secret_key = ACTIVE_KEY
    bs.connect_platform_webhook_secret = ACTIVE_WEBHOOK
    bs.previous_secret_key = previous_key
    bs.previous_webhook_secret = previous_webhook
    bs.save()
    return bs


@pytest.fixture
def accounts() -> Iterator[dict[str, MagicMock]]:
    """One fake Stripe client per secret key, so a spec can see which account a call reached."""
    clients = {key: MagicMock(name=key) for key in (ACTIVE_KEY, PREVIOUS_KEY, TEST_ACTIVE_KEY, TEST_PREVIOUS_KEY)}
    with patch("billing.stripe_utils.stripe.StripeClient", side_effect=lambda key: clients[key]):
        yield clients


def _active(accounts: dict[str, MagicMock]) -> MagicMock:
    return accounts[ACTIVE_KEY]


def _previous(accounts: dict[str, MagicMock]) -> MagicMock:
    return accounts[PREVIOUS_KEY]


def _signed(payload: bytes, secret: str) -> str:
    timestamp = int(time.time())
    signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


def describe_a_refund_on_a_previous_account_payment():
    def it_succeeds_against_the_previous_account(accounts):
        _configure()
        registration = RegistrationFactory(
            status=Registration.Status.CONFIRMED, amount_paid_cents=5000, stripe_payment_id="pi_old_1"
        )
        _active(accounts).v1.refunds.create.side_effect = _missing()
        _previous(accounts).v1.refunds.create.return_value = MagicMock(id="re_old_1", status="succeeded", amount=5000)

        refund = refunds.issue_refund(registration)

        assert refund.status == PaymentRefund.Status.SUCCEEDED
        assert refund.stripe_refund_id == "re_old_1"
        assert _previous(accounts).v1.refunds.create.call_args.kwargs["params"]["payment_intent"] == "pi_old_1"

    def it_stays_on_the_active_account_when_the_payment_is_there(accounts):
        _configure()
        _active(accounts).v1.refunds.create.return_value = MagicMock(id="re_new_1", status="succeeded", amount=500)

        result = stripe_utils.create_refund(payment_intent_id="pi_new_1", idempotency_key="k1")

        assert result["id"] == "re_new_1"
        assert _previous(accounts).mock_calls == []

    def it_raises_the_miss_when_no_previous_key_is_set(accounts):
        _configure(previous_key="")
        _active(accounts).v1.refunds.create.side_effect = _missing()

        with pytest.raises(stripe.InvalidRequestError):
            stripe_utils.create_refund(payment_intent_id="pi_gone", idempotency_key="k2")

    def it_does_not_retry_an_error_that_is_not_a_missing_object(accounts):
        _configure()
        _active(accounts).v1.refunds.create.side_effect = stripe.InvalidRequestError(
            "Charge already refunded", param="charge", code="charge_already_refunded"
        )

        with pytest.raises(stripe.InvalidRequestError):
            stripe_utils.create_refund(payment_intent_id="pi_new_2", idempotency_key="k3")
        assert _previous(accounts).mock_calls == []


def describe_lookups_on_previous_account_objects():
    def it_lists_refunds_on_the_previous_account(accounts):
        _configure()
        _active(accounts).v1.refunds.list.side_effect = _missing()
        _previous(accounts).v1.refunds.list.return_value = MagicMock(
            data=[MagicMock(id="re_old_2", status="succeeded", amount=1500)]
        )

        result = stripe_utils.list_refunds_for_payment_intent(payment_intent_id="pi_old_2")

        assert result == [{"id": "re_old_2", "status": "succeeded", "amount": 1500}]

    def it_retrieves_a_checkout_session_from_the_previous_account(accounts):
        _configure()
        _active(accounts).v1.checkout.sessions.retrieve.side_effect = _missing()
        _previous(accounts).v1.checkout.sessions.retrieve.return_value = MagicMock(
            id="cs_old_1",
            url="",
            status="complete",
            payment_status="paid",
            payment_intent="pi_old_3",
            amount_total=2500,
        )

        result = stripe_utils.retrieve_checkout_session(session_id="cs_old_1")

        assert result["id"] == "cs_old_1"
        assert result["payment_intent"] == "pi_old_3"

    def it_reports_a_session_missing_on_both_accounts_as_not_found(accounts):
        _configure()
        _active(accounts).v1.checkout.sessions.retrieve.side_effect = _missing()
        _previous(accounts).v1.checkout.sessions.retrieve.side_effect = _missing()

        with pytest.raises(stripe_utils.CheckoutSessionNotFound):
            stripe_utils.retrieve_checkout_session(session_id="cs_nowhere")

    def it_expires_a_checkout_session_on_the_previous_account(accounts):
        _configure()
        _active(accounts).v1.checkout.sessions.expire.side_effect = _missing()

        stripe_utils.expire_checkout_session(session_id="cs_old_2")

        _previous(accounts).v1.checkout.sessions.expire.assert_called_once_with("cs_old_2")


def describe_previous_account_webhooks():
    def _charge_refunded_payload(payment_intent_id: str) -> bytes:
        return json.dumps(
            {
                "id": "evt_old_1",
                "object": "event",
                "type": "charge.refunded",
                "data": {
                    "object": {
                        "id": "ch_old_1",
                        "object": "charge",
                        "payment_intent": payment_intent_id,
                        "amount_refunded": 5000,
                    }
                },
            }
        ).encode()

    def it_accepts_and_handles_an_event_signed_by_the_previous_account(client: Client, accounts):
        _configure()
        registration = RegistrationFactory(
            status=Registration.Status.CONFIRMED, amount_paid_cents=5000, stripe_payment_id="pi_old_4"
        )
        _active(accounts).v1.refunds.list.side_effect = _missing()
        _previous(accounts).v1.refunds.list.return_value = MagicMock(
            data=[MagicMock(id="re_old_4", status="succeeded", amount=5000)]
        )
        payload = _charge_refunded_payload("pi_old_4")

        response = client.post(
            "/billing/webhooks/stripe/",
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=_signed(payload, PREVIOUS_WEBHOOK),
        )

        assert response.status_code == 200
        assert registration.refunds.get().stripe_refund_id == "re_old_4"

    def it_rejects_an_event_no_configured_secret_signed(client: Client):
        _configure()
        payload = _charge_refunded_payload("pi_old_5")

        response = client.post(
            "/billing/webhooks/stripe/",
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=_signed(payload, "whsec_stranger"),
        )

        assert response.status_code == 400


_CREATION_CALLS: dict[str, tuple[Callable[[], Any], Callable[[MagicMock], MagicMock]]] = {
    "customer": (
        lambda: stripe_utils.create_customer(email="a@example.com", name="A", member_pk=1),
        lambda client: client.v1.customers.create,
    ),
    "setup intent": (
        lambda: stripe_utils.create_setup_intent(customer_id="cus_1"),
        lambda client: client.v1.setup_intents.create,
    ),
    "charge": (
        lambda: stripe_utils.create_payment_intent(
            customer_id="cus_1",
            payment_method_id="pm_1",
            amount_cents=100,
            description="d",
            metadata={},
            idempotency_key="k",
        ),
        lambda client: client.v1.payment_intents.create,
    ),
    "checkout": (
        lambda: stripe_utils.create_checkout_session(
            amount_cents=100,
            product_name="p",
            customer_email="a@example.com",
            success_url="https://x/s",
            cancel_url="https://x/c",
            metadata={},
            idempotency_key="k",
        ),
        lambda client: client.v1.checkout.sessions.create,
    ),
    "express account": (
        lambda: stripe_utils.create_express_account(email="a@example.com", member_pk=1, idempotency_key="k"),
        lambda client: client.v1.accounts.create,
    ),
}


def describe_creation_calls():
    @pytest.mark.parametrize("name", list(_CREATION_CALLS))
    def it_uses_the_active_account_only_even_when_stripe_reports_a_missing_object(name: str, accounts):
        _configure()
        call, method = _CREATION_CALLS[name]
        method(_active(accounts)).side_effect = _missing()

        with pytest.raises(stripe.InvalidRequestError):
            call()

        assert _previous(accounts).mock_calls == []


def _event(event_type: str, obj: dict[str, Any]) -> bytes:
    return json.dumps({"id": "evt_1", "object": "event", "type": event_type, "data": {"object": obj}}).encode()


def _post(client: Client, payload: bytes, secret: str) -> int:
    return client.post(
        "/billing/webhooks/stripe/",
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=_signed(payload, secret),
    ).status_code


_CARD_EVENTS = {
    "setup_intent.succeeded": {
        "id": "seti_old",
        "object": "setup_intent",
        "customer": "cus_moved",
        "payment_method": "pm_other",
    },
    "payment_method.detached": {"id": "pm_moved", "object": "payment_method"},
    "payment_method.updated": {
        "id": "pm_moved",
        "object": "payment_method",
        "card": {"last4": "0000", "brand": "amex"},
    },
}


def describe_previous_account_card_events():
    @pytest.mark.parametrize("event_type", list(_CARD_EVENTS))
    def it_acknowledges_and_ignores_them_leaving_the_tab_untouched(event_type: str, client: Client, accounts):
        _configure()
        tab = TabFactory(stripe_customer_id="cus_moved", stripe_payment_method_id="pm_moved")

        status = _post(client, _event(event_type, _CARD_EVENTS[event_type]), PREVIOUS_WEBHOOK)

        assert status == 200
        tab.refresh_from_db()
        assert (tab.stripe_payment_method_id, tab.payment_method_last4, tab.payment_method_brand) == (
            "pm_moved",
            "4242",
            "visa",
        )
        assert all(stripe_client.mock_calls == [] for stripe_client in accounts.values())

    def it_still_handles_the_same_detach_from_the_active_account(client: Client):
        _configure()
        tab = TabFactory(stripe_customer_id="cus_moved", stripe_payment_method_id="pm_moved")

        status = _post(
            client, _event("payment_method.detached", _CARD_EVENTS["payment_method.detached"]), ACTIVE_WEBHOOK
        )

        assert status == 200
        tab.refresh_from_db()
        assert tab.stripe_payment_method_id == ""


def describe_webhook_secret_order():
    def it_verifies_with_the_previous_secret_when_the_connected_accounts_secret_does_not_match():
        bs = _configure()
        bs.connect_accounts_webhook_secret = ACCOUNTS_WEBHOOK
        bs.save()
        payload = _event("charge.refunded", {"id": "ch_old", "object": "charge", "payment_intent": "pi_old"})

        event = stripe_utils.construct_webhook_event(payload=payload, sig_header=_signed(payload, PREVIOUS_WEBHOOK))

        assert event is not None
        assert event.type == "charge.refunded"


def _configure_both_modes(*, test_mode: bool) -> None:
    bs = _configure()
    bs.test_mode = test_mode
    bs.test_connect_platform_secret_key = TEST_ACTIVE_KEY
    bs.test_connect_platform_webhook_secret = TEST_ACTIVE_WEBHOOK
    bs.test_previous_secret_key = TEST_PREVIOUS_KEY
    bs.test_previous_webhook_secret = TEST_PREVIOUS_WEBHOOK
    bs.save()


def describe_previous_account_mode_selection():
    def describe_with_testing_mode_on():
        def it_falls_back_to_the_test_previous_key_only(accounts):
            _configure_both_modes(test_mode=True)
            accounts[TEST_ACTIVE_KEY].v1.checkout.sessions.expire.side_effect = _missing()

            stripe_utils.expire_checkout_session(session_id="cs_old_t")

            accounts[TEST_PREVIOUS_KEY].v1.checkout.sessions.expire.assert_called_once_with("cs_old_t")
            assert accounts[PREVIOUS_KEY].mock_calls == []
            assert accounts[ACTIVE_KEY].mock_calls == []

        def it_verifies_with_the_test_previous_webhook_secret_only():
            _configure_both_modes(test_mode=True)
            payload = _event("charge.refunded", {"id": "ch_old", "object": "charge", "payment_intent": "pi_old"})

            assert stripe_utils.construct_webhook_event(
                payload=payload, sig_header=_signed(payload, TEST_PREVIOUS_WEBHOOK)
            )
            with pytest.raises(stripe.SignatureVerificationError):
                stripe_utils.construct_webhook_event(payload=payload, sig_header=_signed(payload, PREVIOUS_WEBHOOK))

    def describe_with_testing_mode_off():
        def it_falls_back_to_the_live_previous_key_only(accounts):
            _configure_both_modes(test_mode=False)
            accounts[ACTIVE_KEY].v1.checkout.sessions.expire.side_effect = _missing()

            stripe_utils.expire_checkout_session(session_id="cs_old_l")

            accounts[PREVIOUS_KEY].v1.checkout.sessions.expire.assert_called_once_with("cs_old_l")
            assert accounts[TEST_PREVIOUS_KEY].mock_calls == []
            assert accounts[TEST_ACTIVE_KEY].mock_calls == []

        def it_verifies_with_the_live_previous_webhook_secret_only():
            _configure_both_modes(test_mode=False)
            payload = _event("charge.refunded", {"id": "ch_old", "object": "charge", "payment_intent": "pi_old"})

            assert stripe_utils.construct_webhook_event(payload=payload, sig_header=_signed(payload, PREVIOUS_WEBHOOK))
            with pytest.raises(stripe.SignatureVerificationError):
                stripe_utils.construct_webhook_event(
                    payload=payload, sig_header=_signed(payload, TEST_PREVIOUS_WEBHOOK)
                )
