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

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, TypeVar

import stripe
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from stripe.params import RefundCreateParams, SubscriptionUpdateParams
from stripe.params.billing_portal import SessionCreateParams as PortalSessionCreateParams
from stripe.params.checkout import SessionCreateParams

if TYPE_CHECKING:
    from billing.models import BillingSettings

_T = TypeVar("_T")

logger = logging.getLogger(__name__)

CLASS_CHECKOUT_SESSION_LIFETIME = timedelta(hours=1)
"""How long a class Checkout Session stays payable before Stripe expires it server-side.

Mirrors the orientation flow's ``_CHECKOUT_SESSION_LIFETIME``, for the same reason: a
registrant reads the waiver before checkout, not during it, so an hour is generous for
the card step and short enough that an abandoned signup stops holding a seat the same
morning. Stripe's default is 24 hours, which is what let 14 production rows sit on seats
for up to 115 days. The classes hold sweep's age threshold is deliberately longer than
this (``classes.models.ABANDONED_HOLD_SWEEP_AGE``), so the backstop never races a
checkout Stripe still considers live.
"""


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


# ---------------------------------------------------------------------------
# Previous account (#702): remove this block once PLM FOG closes
# ---------------------------------------------------------------------------
# plfog moved to a new Stripe account; payments made before the switch live on the
# previous one. Stripe ids belong to one account, so ``resource_missing`` on the active
# account is a definitive "not here" and the call is retried once on the previous
# account. Only calls on an object that already exists go through here; creation calls
# never do, so nothing new is ever made on the previous account.


def _on_owning_account(call: Callable[[stripe.StripeClient], _T]) -> _T:
    """Run ``call`` on the active account, or on the previous one when the active one has no such object."""
    try:
        return call(_get_stripe_client())
    except stripe.InvalidRequestError as exc:
        previous_key = _billing_settings().active_previous_secret_key
        if exc.code != "resource_missing" or not previous_key:
            raise
        return call(stripe.StripeClient(previous_key))


_PREVIOUS_ACCOUNT_IGNORED_EVENTS = ("setup_intent.", "payment_method.")
"""Event type prefixes acknowledged and ignored when the previous account sends them.

Only the payment and refund lifecycle (charge, refund, checkout session, payment intent,
dispute) is handled from the previous account. Saved cards are the active account's:
copied cards keep their ids, so a ``payment_method.detached`` on the previous account
must not clear a card that still works on the active one, and a ``setup_intent`` there
would look its card up on the wrong account.
"""


def _previous_account_event(*, payload: bytes, sig_header: str, bs: BillingSettings) -> stripe.Event | None:
    """Verify ``payload`` with the previous account's webhook secret; ``None`` for an event plfog ignores there.

    Raises:
        stripe.SignatureVerificationError: No previous secret is set, or it does not match.
    """
    secret = bs.active_previous_webhook_secret
    if not secret:
        raise stripe.SignatureVerificationError("No previous account webhook secret is set.", sig_header)
    event = stripe.Webhook.construct_event(payload=payload, sig_header=sig_header, secret=secret)
    if event.type.startswith(_PREVIOUS_ACCOUNT_IGNORED_EVENTS):
        logger.info("Ignored %s %s from the previous Stripe account.", event.type, event.id)
        return None
    return event


# ---------------------------------------------------------------------------


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


#: The ``metadata.kind`` on a SetupIntent the My Tab card page makes. ``setup_intent.succeeded``
#: saves a card onto the Tab only for these, so a card the member adds elsewhere on the same Stripe
#: customer (the Billing Portal behind a cubby's Manage Payment, #978) never replaces the Tab's.
TAB_SETUP_INTENT_KIND = "tab_payment_method"


def create_setup_intent(*, customer_id: str) -> dict[str, str]:
    """Create a Stripe SetupIntent for collecting a payment method.

    Returns dict with 'client_secret' and 'setup_intent_id'.
    Includes usage='off_session' for future off-session charges (SCA/3DS compliance).
    Tagged ``kind=tab_payment_method`` (:data:`TAB_SETUP_INTENT_KIND`).
    """
    client = _get_stripe_client()
    intent = client.v1.setup_intents.create(
        params={
            "customer": customer_id,
            "usage": "off_session",
            "payment_method_types": ["card"],
            "metadata": {"kind": TAB_SETUP_INTENT_KIND},
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
    """Attach a payment method to a customer, leaving the customer's default card alone.

    My Tab charges its card by id (:func:`create_payment_intent`), so it never needs the default.
    The default belongs to the cubby subscriptions (#978), which pay from it once
    :func:`move_subscription_card_to_customer` has put their card there; a Tab card made the
    default would take over their billing, and removing it would leave them no card at all.
    """
    client = _get_stripe_client()
    client.v1.payment_methods.attach(
        payment_method_id,
        params={"customer": customer_id},
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
    """Create a Checkout Session for a class registration — delegates to :func:`create_checkout_session`.

    Stamps ``expires_at`` one hour out (:data:`CLASS_CHECKOUT_SESSION_LIFETIME`) so an
    abandoned checkout dies on Stripe's side and fires ``checkout.session.expired``, which
    is what releases the seat it was holding. Applied here rather than at the call site so
    every class session gets it, including the replacement one a resumed signup mints.
    """
    return create_checkout_session(
        amount_cents=amount_cents,
        product_name=product_name,
        customer_email=customer_email,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
        idempotency_key=idempotency_key,
        expires_at=int((timezone.now() + CLASS_CHECKOUT_SESSION_LIFETIME).timestamp()),
    )


def expire_checkout_session(*, session_id: str) -> None:
    """Expire an open Checkout Session so its hosted page can no longer take payment.

    Called best-effort when a seat hold is released — an open Stripe tab must not
    be able to pay for a booking that no longer exists. Stripe errors propagate;
    callers swallow them (the session's own ``expires_at`` is the backstop).
    """
    _on_owning_account(lambda client: client.v1.checkout.sessions.expire(session_id))


class CheckoutSessionNotFound(Exception):
    """Stripe has no Checkout Session with this id, and never will.

    Separated from every other Stripe failure because the two mean opposite things to a
    caller deciding whether to act. A timeout, a 500 or a rate limit is Stripe declining to
    answer: ask again later. ``resource_missing`` is Stripe answering — this account has no
    such session — and no amount of retrying changes it.

    In practice it means the stored id belongs to somewhere else: a test-mode id left behind
    after the keys moved to live, or a row from a different Stripe account. Either way the
    id can never be verified and can never turn out to have been paid.
    """


def retrieve_checkout_session(*, session_id: str) -> dict[str, Any]:
    """Retrieve a Checkout Session — the Stripe-verified hold-release check.

    Returns dict with 'id', 'url', 'status' (``open`` / ``complete`` / ``expired``),
    'payment_status' (``paid`` / ``unpaid`` / ``no_payment_required``),
    'payment_intent', and 'amount_total'. Stripe errors propagate — callers
    treat an unreachable Stripe as "unknown" and keep the hold.

    Raises:
        CheckoutSessionNotFound: Stripe knows no such session. A definitive answer, not a
            failure to answer, so a caller may act on it rather than retrying forever.
    """
    try:
        session = _on_owning_account(lambda client: client.v1.checkout.sessions.retrieve(session_id))
    except stripe.InvalidRequestError as exc:
        # resource_missing is the only InvalidRequestError that means "no such object".
        # The rest (a malformed parameter, a bad API version) are our bug and must keep
        # propagating loudly rather than being read as an answer about the session.
        if exc.code == "resource_missing":
            raise CheckoutSessionNotFound(session_id) from exc
        raise
    return {
        "id": session.id,
        "url": session.url or "",
        "status": session.status or "",
        "payment_status": session.payment_status or "",
        "payment_intent": str(session.payment_intent) if session.payment_intent else "",
        "amount_total": session.amount_total,
        "subscription": _expandable_id(session.subscription),
        "invoice": _expandable_id(session.invoice),
        "metadata": session.metadata.to_dict() if session.metadata else {},
    }


def _expandable_id(value: Any) -> str:
    """The id of an expandable field, whether Stripe sent the id or the object; "" when empty."""
    if not value:
        return ""
    return value if isinstance(value, str) else str(value.id)


def create_refund(*, payment_intent_id: str, amount_cents: int | None = None, idempotency_key: str) -> dict[str, Any]:
    """Refund a PaymentIntent — full refund when ``amount_cents`` is ``None``.

    Returns dict with 'id' (the Stripe ``re_…`` refund id), 'status', and 'amount'.
    The idempotency_key is REQUIRED so a retried request never double-refunds.
    Stripe errors propagate — the caller (``billing.refunds``) handles them loudly. A payment
    made on the previous Stripe account is refunded there (#702).
    """
    params: RefundCreateParams = {"payment_intent": payment_intent_id}
    if amount_cents is not None:
        params["amount"] = amount_cents
    refund = _on_owning_account(
        lambda client: client.v1.refunds.create(params=params, options={"idempotency_key": idempotency_key})
    )
    return {"id": refund.id, "status": refund.status, "amount": refund.amount}


def list_refunds_for_payment_intent(*, payment_intent_id: str) -> list[dict[str, Any]]:
    """List the refunds on a PaymentIntent — the ``charge.refunded`` fetch fallback.

    Since Stripe API version 2022-11-15 the ``Charge`` payload no longer embeds
    its ``refunds`` list by default (and we do not pin ``api_version``), so the
    webhook handler fetches them explicitly. Returns a list of dicts with
    'id', 'status', and 'amount' — the shape the reconciler reads.
    """
    refunds = _on_owning_account(lambda client: client.v1.refunds.list(params={"payment_intent": payment_intent_id}))
    return [{"id": refund.id, "status": refund.status, "amount": refund.amount} for refund in refunds.data]


def construct_webhook_event(*, payload: bytes, sig_header: str) -> stripe.Event | None:
    """Verify and construct a Stripe webhook event from the raw payload.

    Uses the raw request body to verify the Stripe signature. The signing
    secrets are read from BillingSettings and tried in order: the platform
    endpoint's, the Connected accounts endpoint's, then the previous Stripe
    account endpoint's (#702).

    Returns:
        The event, or ``None`` for a previous account event plfog acknowledges
        without handling (see ``_PREVIOUS_ACCOUNT_IGNORED_EVENTS``).

    Raises:
        stripe.SignatureVerificationError: If no configured secret matches the signature.
        ImproperlyConfigured: If the platform webhook secret is not set.
    """
    try:
        return stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=_platform_webhook_secret(),
        )
    except stripe.SignatureVerificationError as platform_error:
        bs = _billing_settings()
        # Connect events (account.updated for payouts, #662) come from a second Stripe
        # endpoint scoped to Connected accounts, pointed at the same URL with its own secret.
        accounts_secret = bs.active_accounts_webhook_secret
        if accounts_secret:
            try:
                return stripe.Webhook.construct_event(payload=payload, sig_header=sig_header, secret=accounts_secret)
            except stripe.SignatureVerificationError:
                pass
        # Events about payments made before the switch come from the previous account's endpoint.
        try:
            return _previous_account_event(payload=payload, sig_header=sig_header, bs=bs)
        except stripe.SignatureVerificationError:
            raise platform_error from None


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


# ---------------------------------------------------------------------------
# Stripe Connect Express accounts for instructor and orientor payouts (#662)
# ---------------------------------------------------------------------------


def create_express_account(*, email: str, member_pk: int, idempotency_key: str) -> str:
    """Create a US Express account that can receive transfers, paid out to its bank daily.

    Returns the ``acct_...`` id. Past Lives pays the Connect fees, so nothing here takes a fee.
    Stripe errors propagate to the caller.
    """
    client = _get_stripe_client()
    account = client.v1.accounts.create(
        params={
            "type": "express",
            "country": "US",
            "email": email,
            "business_type": "individual",
            "capabilities": {"transfers": {"requested": True}},
            "settings": {"payouts": {"schedule": {"interval": "daily"}}},
            "metadata": {"member_pk": str(member_pk)},
        },
        options={"idempotency_key": idempotency_key},
    )
    return account.id


def create_account_link(*, account_id: str, return_url: str) -> str:
    """A Stripe-hosted signup link for ``account_id``; ``return_url`` is also its refresh URL."""
    client = _get_stripe_client()
    link = client.v1.account_links.create(
        params={
            "account": account_id,
            "type": "account_onboarding",
            "return_url": return_url,
            "refresh_url": return_url,
        }
    )
    return link.url


def create_login_link(*, account_id: str) -> str:
    """A single use link into the payee's Stripe Express dashboard (bank, deposits, tax forms)."""
    client = _get_stripe_client()
    return client.v1.accounts.login_links.create(account_id).url


def retrieve_account(*, account_id: str) -> dict[str, Any]:
    """The Account object as a plain dict, the same shape ``account.updated`` delivers."""
    client = _get_stripe_client()
    return client.v1.accounts.retrieve(account_id).to_dict()


def charge_for_payment_intent(*, payment_intent_id: str) -> str:
    """The charge (ch_...) behind a PaymentIntent: its ``latest_charge``, the transfer's ``source_transaction``."""
    client = _get_stripe_client()
    payment_intent = client.v1.payment_intents.retrieve(payment_intent_id)
    latest_charge = payment_intent.latest_charge
    if latest_charge is None:
        # No charge behind it (never captured): the transfer fails and is flagged like any Stripe rejection.
        raise stripe.InvalidRequestError(f"{payment_intent_id} has no charge to send a share from.", param=None)
    return latest_charge if isinstance(latest_charge, str) else latest_charge.id


def create_transfer(
    *, amount_cents: int, destination: str, source_transaction: str, idempotency_key: str, metadata: dict[str, str]
) -> str:
    """Transfer ``amount_cents`` to a connected account, tied to the charge that paid for it.

    ``source_transaction`` lets the transfer succeed while the charge is still settling; once
    the charge has settled it draws on the available balance (#662 plan, Risk). Returns the
    ``tr_...`` id. Stripe errors propagate to ``Payout.send``.
    """
    client = _get_stripe_client()
    transfer = client.v1.transfers.create(
        params={
            "amount": amount_cents,
            "currency": "usd",
            "destination": destination,
            "source_transaction": source_transaction,
            "metadata": metadata,
        },
        options={"idempotency_key": idempotency_key},
    )
    return transfer.id


def find_payout_transfer(*, payout_pk: int, created_after: datetime) -> str | None:
    """The transfer plfog made for payout ``payout_pk`` (its ``payout_pk`` metadata), if any.

    Lists the platform's transfers created since the payout row; volume is a few a day.
    Stripe errors propagate.
    """
    client = _get_stripe_client()
    page = client.v1.transfers.list(params={"created": {"gte": int(created_after.timestamp())}, "limit": 100})
    for transfer in page.auto_paging_iter():
        metadata = transfer.metadata
        if metadata is not None and "payout_pk" in metadata and metadata["payout_pk"] == str(payout_pk):
            return transfer.id
    return None


def reverse_transfer(*, transfer_id: str, amount_cents: int, refund_pk: int) -> str:
    """Take ``amount_cents`` of a sent share back from the payee: a transfer reversal (#662, part 3).

    Keyed ``payout-reversal-<refund pk>`` and tagged with ``refund_pk`` metadata, so a replay
    returns the same reversal and ``find_transfer_reversal`` can find it after the key expires.
    Returns the ``trr_...`` id. Stripe errors propagate.
    """
    client = _get_stripe_client()
    reversal = client.v1.transfers.reversals.create(
        transfer_id,
        params={"amount": amount_cents, "metadata": {"refund_pk": str(refund_pk)}},
        options={"idempotency_key": f"payout-reversal-{refund_pk}"},
    )
    return reversal.id


def find_transfer_reversal(*, transfer_id: str, refund_pk: int) -> str | None:
    """The reversal plfog already made on ``transfer_id`` for refund ``refund_pk``, if any. Stripe errors propagate."""
    client = _get_stripe_client()
    for reversal in client.v1.transfers.reversals.list(transfer_id, params={"limit": 100}).auto_paging_iter():
        metadata = reversal.metadata
        if metadata is not None and "refund_pk" in metadata and metadata["refund_pk"] == str(refund_pk):
            return reversal.id
    return None


# ---------------------------------------------------------------------------
# Cubby rentals (#978): the first recurring billing, a monthly subscription per rental
# ---------------------------------------------------------------------------


def create_subscription_checkout_session(
    *,
    customer_id: str,
    amount_cents: int,
    product_name: str,
    description: str,
    success_url: str,
    cancel_url: str,
    metadata: dict[str, str],
    idempotency_key: str,
    expires_at: int,
) -> dict[str, str]:
    """A hosted Checkout that starts a monthly subscription at ``amount_cents`` for ``customer_id``.

    The price is inline (``price_data``, ``recurring.interval = month``) on a product made inline
    (``product_data``), so nothing account specific is stored before the money moves: the product
    id is read back from the subscription. ``metadata`` rides on the session and, as
    ``subscription_data.metadata``, on the subscription and every invoice it makes
    (``invoice.parent.subscription_details.metadata``). ``description`` names the cubby on the
    subscription, which is what the Billing Portal lists.

    Returns dict with 'id' and 'url'. Stripe errors propagate.
    """
    client = _get_stripe_client()
    params: SessionCreateParams = {
        "mode": "subscription",
        "customer": customer_id,
        # Card only: a delayed payment method could complete the Checkout before its money lands.
        "payment_method_types": ["card"],
        "line_items": [
            {
                "quantity": 1,
                "price_data": {
                    "currency": "usd",
                    "unit_amount": amount_cents,
                    "recurring": {"interval": "month"},
                    "product_data": {"name": product_name},
                },
            }
        ],
        "metadata": metadata,
        "subscription_data": {"metadata": metadata, "description": description},
        "success_url": success_url,
        "cancel_url": cancel_url,
        "expires_at": expires_at,
    }
    session = client.v1.checkout.sessions.create(params=params, options={"idempotency_key": idempotency_key})
    return {"id": session.id, "url": session.url or ""}


def _subscription_dict(subscription: stripe.Subscription) -> dict[str, Any]:
    """The few subscription facts a cubby rental mirrors, as a plain dict.

    'id', 'status', 'customer', 'metadata', 'cancel_at_period_end', 'cancel_at' (unix or None),
    'default_payment_method' ("" when the customer's default pays), 'latest_invoice', and from its
    one item 'item_id', 'product_id', 'unit_amount' and 'current_period_end' (the period end lives
    on the item since API 2025-03-31).
    """
    data = subscription.to_dict()
    item = data["items"]["data"][0]
    price = item["price"]
    product = price["product"]
    return {
        "id": data["id"],
        "status": data["status"],
        "customer": _expandable_id(subscription.customer),
        "metadata": dict(data.get("metadata") or {}),
        "cancel_at_period_end": bool(data["cancel_at_period_end"]),
        "cancel_at": data.get("cancel_at"),
        "default_payment_method": _expandable_id(subscription.default_payment_method),
        "latest_invoice": _expandable_id(subscription.latest_invoice),
        "item_id": item["id"],
        "product_id": product if isinstance(product, str) else product["id"],
        "unit_amount": price["unit_amount"],
        "current_period_end": item["current_period_end"],
    }


def retrieve_subscription(*, subscription_id: str) -> dict[str, Any]:
    """The subscription as it is now (see :func:`_subscription_dict`). Stripe errors propagate."""
    client = _get_stripe_client()
    return _subscription_dict(client.v1.subscriptions.retrieve(subscription_id))


def move_subscription_card_to_customer(*, customer_id: str, subscription_id: str, payment_method_id: str) -> None:
    """Make the card Checkout saved on the subscription the customer's default, and clear the subscription's own.

    A subscription's own ``default_payment_method`` wins over the customer's, and the Billing
    Portal's Update Payment Method sets the customer's. Moving the card here is what lets a card
    the member fixes in the portal pay the next charge. The My Tab card is untouched: My Tab
    charges its own saved card by id, never the customer default.
    """
    client = _get_stripe_client()
    client.v1.customers.update(customer_id, params={"invoice_settings": {"default_payment_method": payment_method_id}})
    client.v1.subscriptions.update(subscription_id, params={"default_payment_method": ""})


def update_subscription_amount(*, subscription_id: str, item_id: str, product_id: str, amount_cents: int) -> None:
    """Charge ``amount_cents`` a month from the next charge on: a new inline price on the same product.

    ``proration_behavior="none"``: nothing is charged or credited now. Stripe errors propagate.
    """
    client = _get_stripe_client()
    params: SubscriptionUpdateParams = {
        "items": [
            {
                "id": item_id,
                "price_data": {
                    "currency": "usd",
                    "product": product_id,
                    "unit_amount": amount_cents,
                    "recurring": {"interval": "month"},
                },
            }
        ],
        "proration_behavior": "none",
    }
    client.v1.subscriptions.update(subscription_id, params=params)


def set_subscription_cancel_at_period_end(*, subscription_id: str, cancel: bool) -> None:
    """Stop (``cancel=True``) or resume the subscription at the end of the paid month.

    Resuming also clears any ``cancel_at`` date, which a dashboard or portal cancel may have set
    instead of ``cancel_at_period_end``.
    """
    client = _get_stripe_client()
    params: SubscriptionUpdateParams = {"cancel_at_period_end": True} if cancel else {"cancel_at": ""}
    if not cancel:
        params["cancel_at_period_end"] = False
    client.v1.subscriptions.update(subscription_id, params=params)


def cancel_subscription(*, subscription_id: str) -> None:
    """End the subscription now. No proration and no refund. Stripe errors propagate."""
    client = _get_stripe_client()
    client.v1.subscriptions.cancel(subscription_id)


def create_payment_method_portal_session(*, customer_id: str, return_url: str) -> str:
    """A Billing Portal link that opens straight on Update Payment Method and comes back to ``return_url``."""
    client = _get_stripe_client()
    params: PortalSessionCreateParams = {
        "customer": customer_id,
        "return_url": return_url,
        "flow_data": {
            "type": "payment_method_update",
            "after_completion": {"type": "redirect", "redirect": {"return_url": return_url}},
        },
    }
    return client.v1.billing_portal.sessions.create(params=params).url


def invoice_payment_intent(*, invoice_id: str) -> str:
    """The PaymentIntent that paid ``invoice_id``, or "" when none did.

    Since API 2025-03-31 an invoice no longer carries ``payment_intent``; its payments are their
    own list, not in the webhook payload. Stripe errors propagate.
    """
    client = _get_stripe_client()
    payments = client.v1.invoice_payments.list(params={"invoice": invoice_id, "status": "paid"})
    for invoice_payment in payments.data:
        payment_intent = invoice_payment.payment.payment_intent
        if payment_intent:
            return _expandable_id(payment_intent)
    return ""
