"""Thin wrapper for all Stripe API calls. All Stripe interactions go through here.

All Stripe credentials (platform secret key, webhook signing secret) live in the
`BillingSettings` singleton row in the database — no environment variables. The
only Stripe-related env var still in use is `STRIPE_FIELD_ENCRYPTION_KEY`, which
is the Fernet key that encrypts secrets at rest.

Since v1.5.0, all charges route through a single platform Stripe account — no
more destination charges to per-guild Connect accounts, no more direct-keys
Checkout sessions. Revenue split to guilds is reconciled via the admin Reports
page (Payments → Reports) and paid out manually.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import stripe
from django.core.exceptions import ImproperlyConfigured
from stripe.params import RefundCreateParams
from stripe.params.checkout import SessionCreateParams

if TYPE_CHECKING:
    from billing.models import BillingSettings


def _billing_settings() -> BillingSettings:
    """Lazy import to avoid circular dependency between models and stripe_utils."""
    from billing.models import BillingSettings as _BS

    return _BS.load()


def _platform_secret_key() -> str:
    bs = _billing_settings()
    if not bs.active_secret_key:
        raise ImproperlyConfigured(
            "Stripe platform secret key is not set. Configure it in the admin Payments dashboard → Stripe tab."
        )
    return bs.active_secret_key


def _platform_webhook_secret() -> str:
    bs = _billing_settings()
    if not bs.active_webhook_secret:
        raise ImproperlyConfigured(
            "Stripe platform webhook secret is not set. Configure it in the admin Payments dashboard → Stripe tab."
        )
    return bs.active_webhook_secret


def _get_stripe_client() -> stripe.StripeClient:
    """Get a Stripe client configured with the platform secret key (from DB)."""
    return stripe.StripeClient(_platform_secret_key())


def create_customer(*, email: str, name: str, member_pk: int) -> str:
    """Create a Stripe customer and return the customer ID.

    Uses an idempotency key based on member_pk to prevent duplicate customers.
    """
    client = _get_stripe_client()
    customer = client.v1.customers.create(
        params={"email": email, "name": name, "metadata": {"member_pk": str(member_pk)}},
        options={"idempotency_key": f"create-customer-member-{member_pk}"},
    )
    return customer.id


def create_setup_intent(*, customer_id: str) -> dict[str, str]:
    """Create a Stripe SetupIntent for collecting a payment method.

    Returns dict with 'client_secret' and 'setup_intent_id'.
    Includes usage='off_session' for future off-session charges (SCA/3DS compliance).
    """
    client = _get_stripe_client()
    intent = client.v1.setup_intents.create(
        params={
            "customer": customer_id,
            "usage": "off_session",
            "payment_method_types": ["card"],
        },
    )
    return {"client_secret": intent.client_secret or "", "setup_intent_id": intent.id}


def retrieve_payment_method(*, payment_method_id: str) -> dict[str, Any]:
    """Retrieve a payment method's details from Stripe.

    Returns dict with 'id', 'brand', 'last4'.
    """
    client = _get_stripe_client()
    pm = client.v1.payment_methods.retrieve(payment_method_id)
    return {
        "id": pm.id,
        "brand": pm.card.brand if pm.card else "",
        "last4": pm.card.last4 if pm.card else "",
    }


def attach_payment_method(*, customer_id: str, payment_method_id: str) -> None:
    """Attach a payment method to a customer and set it as the default."""
    client = _get_stripe_client()
    client.v1.payment_methods.attach(
        payment_method_id,
        params={"customer": customer_id},
    )
    client.v1.customers.update(
        customer_id,
        params={"invoice_settings": {"default_payment_method": payment_method_id}},
    )


def detach_payment_method(*, payment_method_id: str) -> None:
    """Detach a payment method from its customer."""
    client = _get_stripe_client()
    client.v1.payment_methods.detach(payment_method_id)


def create_payment_intent(
    *,
    customer_id: str,
    payment_method_id: str,
    amount_cents: int,
    description: str,
    metadata: dict[str, str],
    idempotency_key: str,
) -> dict[str, Any]:
    """Create a Stripe PaymentIntent for an off-session charge.

    Returns dict with 'id', 'status', 'charge_id', 'receipt_url'.
    The idempotency_key is REQUIRED to prevent duplicate charges.
    """
    client = _get_stripe_client()
    intent = client.v1.payment_intents.create(
        params={
            "customer": customer_id,
            "payment_method": payment_method_id,
            "amount": amount_cents,
            "currency": "usd",
            "description": description,
            "metadata": metadata,
            "off_session": True,
            "confirm": True,
        },
        options={"idempotency_key": idempotency_key},
    )
    # Extract charge details if available
    charge_id = ""
    receipt_url = ""
    if intent.latest_charge:
        charge = client.v1.charges.retrieve(str(intent.latest_charge))
        charge_id = charge.id
        receipt_url = charge.receipt_url or ""

    return {
        "id": intent.id,
        "status": intent.status,
        "charge_id": charge_id,
        "receipt_url": receipt_url,
    }


def create_checkout_session(
    *,
    amount_cents: int,
    product_name: str,
    customer_email: str,
    success_url: str,
    cancel_url: str,
    metadata: dict[str, str],
    idempotency_key: str,
    expires_at: int | None = None,
) -> dict[str, str]:
    """Create a hosted Stripe Checkout Session for a one-off payment.

    Purpose-neutral: callers convey what is being bought via ``metadata["kind"]``
    (``"class_registration"``, ``"orientation_booking"``, …) and the matching
    ``checkout.session.completed`` handler self-filters on it. Stripe collects
    card details on its hosted page, then redirects back to ``success_url``.

    ``expires_at`` is an optional unix timestamp (Stripe allows 30 min–24 h out)
    after which the session dies server-side and fires ``checkout.session.expired``.

    Returns dict with 'id' and 'url' (the hosted Checkout page).
    The idempotency_key is REQUIRED to prevent duplicate sessions on retry.
    """
    client = _get_stripe_client()
    params: SessionCreateParams = {
        "mode": "payment",
        "customer_email": customer_email,
        "line_items": [
            {
                "quantity": 1,
                "price_data": {
                    "currency": "usd",
                    "unit_amount": amount_cents,
                    "product_data": {"name": product_name},
                },
            }
        ],
        "metadata": metadata,
        "payment_intent_data": {"metadata": metadata},
        "success_url": success_url,
        "cancel_url": cancel_url,
    }
    if expires_at is not None:
        params["expires_at"] = expires_at
    session = client.v1.checkout.sessions.create(
        params=params,
        options={"idempotency_key": idempotency_key},
    )
    return {"id": session.id, "url": session.url or ""}


def create_class_checkout_session(
    *,
    amount_cents: int,
    product_name: str,
    customer_email: str,
    success_url: str,
    cancel_url: str,
    metadata: dict[str, str],
    idempotency_key: str,
) -> dict[str, str]:
    """Create a Checkout Session for a class registration — delegates to :func:`create_checkout_session`."""
    return create_checkout_session(
        amount_cents=amount_cents,
        product_name=product_name,
        customer_email=customer_email,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
        idempotency_key=idempotency_key,
    )


def expire_checkout_session(*, session_id: str) -> None:
    """Expire an open Checkout Session so its hosted page can no longer take payment.

    Called best-effort when a seat hold is released — an open Stripe tab must not
    be able to pay for a booking that no longer exists. Stripe errors propagate;
    callers swallow them (the session's own ``expires_at`` is the backstop).
    """
    client = _get_stripe_client()
    client.v1.checkout.sessions.expire(session_id)


def retrieve_checkout_session(*, session_id: str) -> dict[str, Any]:
    """Retrieve a Checkout Session — the Stripe-verified hold-release check.

    Returns dict with 'id', 'url', 'status' (``open`` / ``complete`` / ``expired``),
    'payment_status' (``paid`` / ``unpaid`` / ``no_payment_required``),
    'payment_intent', and 'amount_total'. Stripe errors propagate — callers
    treat an unreachable Stripe as "unknown" and keep the hold.
    """
    client = _get_stripe_client()
    session = client.v1.checkout.sessions.retrieve(session_id)
    return {
        "id": session.id,
        "url": session.url or "",
        "status": session.status or "",
        "payment_status": session.payment_status or "",
        "payment_intent": str(session.payment_intent) if session.payment_intent else "",
        "amount_total": session.amount_total,
    }


def create_refund(*, payment_intent_id: str, amount_cents: int | None = None, idempotency_key: str) -> dict[str, Any]:
    """Refund a PaymentIntent — full refund when ``amount_cents`` is ``None``.

    Returns dict with 'id' (the Stripe ``re_…`` refund id), 'status', and 'amount'.
    The idempotency_key is REQUIRED so a retried request never double-refunds.
    Stripe errors propagate — the caller (``billing.refunds``) handles them loudly.
    """
    client = _get_stripe_client()
    params: RefundCreateParams = {"payment_intent": payment_intent_id}
    if amount_cents is not None:
        params["amount"] = amount_cents
    refund = client.v1.refunds.create(
        params=params,
        options={"idempotency_key": idempotency_key},
    )
    return {"id": refund.id, "status": refund.status, "amount": refund.amount}


def list_refunds_for_payment_intent(*, payment_intent_id: str) -> list[dict[str, Any]]:
    """List the refunds on a PaymentIntent — the ``charge.refunded`` fetch fallback.

    Since Stripe API version 2022-11-15 the ``Charge`` payload no longer embeds
    its ``refunds`` list by default (and we do not pin ``api_version``), so the
    webhook handler fetches them explicitly. Returns a list of dicts with
    'id', 'status', and 'amount' — the shape the reconciler reads.
    """
    client = _get_stripe_client()
    refunds = client.v1.refunds.list(params={"payment_intent": payment_intent_id})
    return [{"id": refund.id, "status": refund.status, "amount": refund.amount} for refund in refunds.data]


def construct_webhook_event(*, payload: bytes, sig_header: str) -> stripe.Event:
    """Verify and construct a Stripe webhook event from the raw payload.

    Uses the raw request body to verify the Stripe signature. The signing
    secret is read from BillingSettings.

    Raises:
        stripe.SignatureVerificationError: If the signature is invalid.
        ImproperlyConfigured: If the platform webhook secret is not set.
    """
    return stripe.Webhook.construct_event(
        payload=payload,
        sig_header=sig_header,
        secret=_platform_webhook_secret(),
    )


def verify_platform_credentials(secret_key: str) -> dict[str, Any]:
    """Make a test API call against the given secret key to verify it works.

    Used by the admin "Test connection" button on the Settings tab.

    Returns:
        dict with 'stripe_account_id', 'display_name', 'charges_enabled', 'country'.

    Raises:
        stripe.AuthenticationError: Invalid API key.
        stripe.StripeError: Other Stripe API failures.
    """
    client = stripe.StripeClient(secret_key)
    account = client.v1.accounts.retrieve("self")
    display_name = ""
    if account.business_profile and account.business_profile.name:
        display_name = account.business_profile.name
    return {
        "stripe_account_id": account.id,
        "display_name": display_name or account.id,
        "charges_enabled": bool(account.charges_enabled),
        "country": account.country or "",
    }
