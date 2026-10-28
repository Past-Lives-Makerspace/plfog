"""Renting a guild cubby (#976 part 2, #978): every way a rental's money starts, changes and stops.

The only module that changes a :class:`~membership.models.CubbyRental`'s status. A rental is a
monthly Stripe subscription at the amount the member chose:

* **Rent** (:func:`start_rental`) holds the cubby Awaiting Payment under the guild lock part 1
  takes (then the cubby row), and opens a subscription Checkout. A double tap resumes the same
  Checkout. The hold dies with the Checkout (``checkout.session.expired``, the cancel page, or the
  :func:`release_stale_holds` sweep, which always asks Stripe first).
* **Stripe is the truth.** Every webhook re-reads the subscription and :func:`settle_subscription`
  mirrors it onto the row under ``select_for_update``, so the order events arrive in never
  matters. Ended is final: nothing brings an ended rental back.
* **Money.** ``invoice.paid`` records one :class:`~membership.models.CubbyPayment` per invoice
  (the unique invoice id makes a retry a no-op), with the guild's payee frozen at that moment.
* **Nobody pays for a cubby they did not get.** A paid Checkout whose hold is gone takes the
  cubby if it is still free; if someone else holds it, the subscription is cancelled and the
  first payment refunded in full, and the member is told.
* **Change Amount, Cancel, Keep My Cubby, Manage Payment** call Stripe and mirror the answer.
* **Staff (#979).** :func:`assign_rental` gives a cubby to a member as Comped or Paid Another
  Way: Active at once, no subscription, never charged. :func:`end_rental` (End Rental) ends any
  live rental today with no refund, cancelling a subscription now under the rental's row lock, so
  the ``customer.subscription.deleted`` that follows finds it Ended and adds nothing. A member's
  own Cancel Rental on an assigned rental ends it today the same way.

Every change that frees a cubby (a rental ending however it ends, a hold deleted) calls
:func:`membership.cubby_waitlist.open_for_waitlist` inside it, so the member first in that guild's
waitlist hears once the change commits (#980).

Notifications go out after the change commits, each exactly once through its emit ``period``. The
renter hears about their own rental; the guild's lead and staff hear about a new rental, a Past
Due one (the 8 AM digest drops it once it is paid) and an ended one, whose shelf needs clearing.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from membership.cubby_waitlist import leave_everywhere, open_for_waitlist
from membership.models import (
    CUBBY_PRICE_MAX_CENTS,
    CUBBY_PRICE_MIN_CENTS,
    Cubby,
    CubbyPayment,
    CubbyRental,
    Guild,
    Member,
    cubby_dollars,
)

if TYPE_CHECKING:
    from django.db.models import QuerySet

logger = logging.getLogger(__name__)

#: ``metadata.kind`` on the Checkout, its subscription and every invoice the subscription makes.
CHECKOUT_KIND = "cubby_rental"
#: How long a rent Checkout stays payable; Stripe expires it and fires ``checkout.session.expired``.
CHECKOUT_LIFETIME = timedelta(hours=1)
#: A hold with no Checkout yet, younger than this, is another tab's rent still reaching Stripe.
HOLD_IN_PROGRESS = timedelta(minutes=2)
BEING_PAID_FOR = "{label} is being paid for right now. Try again in a minute."
_TOKEN_SALT = "cubby-rental-checkout"
_TOKEN_MAX_AGE = 30 * 24 * 3600

Status = CubbyRental.Status

#: Stripe subscription status -> the rental status it mirrors. ``unpaid`` is Past Due here and
#: also cancels the subscription (:func:`settle_subscription`); ``incomplete`` keeps the hold.
_STATUS_FOR: dict[str, str] = {
    "active": Status.ACTIVE,
    "trialing": Status.ACTIVE,
    "past_due": Status.PAST_DUE,
    "unpaid": Status.PAST_DUE,
    # Collection paused (a trial ended without a card, or paused in the dashboard): not paying, so Past Due.
    "paused": Status.PAST_DUE,
    "incomplete": Status.PENDING_PAYMENT,
    "canceled": Status.ENDED,
    "incomplete_expired": Status.ENDED,
}
_STRIPE_ENDED = ("canceled", "incomplete_expired")


class CubbyRentalError(Exception):
    """A rental action refused, with the sentence the member sees."""


class RefundNotReadyError(Exception):
    """A paid invoice of a cubby the member never got has no payment on Stripe to refund yet."""


class CubbyTakenError(CubbyRentalError):
    """Someone else holds the cubby (rented, or paying for it right now)."""


class CubbyUnavailableError(CubbyRentalError):
    """The cubby cannot be rented: archived, out of service, the tab is off, or it is already yours."""


class AmountError(CubbyRentalError):
    """The monthly amount is below the floor or above the ceiling."""


# ── Amounts ──────────────────────────────────────────────────────────────────


def amount_problem(cubby: Cubby, amount_cents: int) -> str:
    """Why ``amount_cents`` a month cannot be paid for ``cubby``, or "".

    At least the cubby's minimum today (raising the floor reaches new rentals and Change Amount,
    never an existing amount) and at most $500. The floor is never below $1, so "under $1" is the
    same refusal as "under the floor".
    """
    if amount_cents > CUBBY_PRICE_MAX_CENTS:
        return f"The most a cubby can be is {cubby_dollars(CUBBY_PRICE_MAX_CENTS)} a month."
    floor = cubby.floor
    if amount_cents < floor:
        return f"The lowest you can pay for {cubby.label} is {cubby_dollars(floor)} a month. Pick {cubby_dollars(floor)} or more."
    return ""


# ── Links and tokens ─────────────────────────────────────────────────────────


def _absolute_url(path: str) -> str:
    return f"{settings.MEMBER_BASE_URL.rstrip('/')}{path}"


def tab_url(guild: Guild) -> str:
    """The guild page opened on its Cubbies tab (relative)."""
    return f"{reverse('hub_guild_detail', args=[guild.slug])}?tab=cubbies"


def make_checkout_token(rental: CubbyRental) -> str:
    """Sign a token for one rental's Checkout return and cancel pages; it names the cubby too."""
    return signing.dumps({"rental": rental.pk, "cubby": rental.cubby_id}, salt=_TOKEN_SALT)


def read_checkout_token(token: str) -> tuple[int, int]:
    """The rental pk and cubby pk a checkout token names (the cubby outlives a released hold).

    Raises:
        signing.BadSignature: If the token is invalid or expired.
    """
    data = signing.loads(token, salt=_TOKEN_SALT, max_age=_TOKEN_MAX_AGE)
    return int(data["rental"]), int(data["cubby"])


def _checkout_urls(rental: CubbyRental) -> tuple[str, str]:
    token = make_checkout_token(rental)
    return (
        _absolute_url(reverse("hub_cubby_checkout_return", args=[token])),
        _absolute_url(reverse("hub_cubby_checkout_cancelled", args=[token])),
    )


# ── Locks and reads ──────────────────────────────────────────────────────────


def _lock_guild(guild_id: int) -> None:
    """Part 1's one lock for every cubby change (``membership.cubbies``), taken before any cubby row."""
    Guild.objects.select_for_update().filter(pk=guild_id).first()


def _locked_rental(**lookup: Any) -> CubbyRental | None:
    """The rental row under ``select_for_update`` (its own row only), or None."""
    return (
        CubbyRental.objects.select_for_update(of=("self",))
        .select_related("cubby__unit__guild", "member__user")
        .filter(**lookup)
        .first()
    )


def unavailable_reason(cubby: Cubby) -> str:
    """Why nobody can rent ``cubby`` right now (archived, out of service, the tab off), or ""."""
    if cubby.is_archived or cubby.unit.is_archived or not cubby.unit.guild.show_cubbies_tab:
        return f"{cubby.label} isn't available to rent right now."
    if cubby.out_of_service:
        return f"{cubby.label} is out of service right now."
    return ""


def live_rentals_for(member: Member, guild: Guild | None = None) -> QuerySet[CubbyRental]:
    """The member's live rentals, newest first; within ``guild`` when given."""
    rentals = CubbyRental.objects.live().filter(member=member).select_related("cubby__unit__guild")
    if guild is not None:
        rentals = rentals.filter(cubby__unit__guild=guild)
    return rentals


# ── Rent ─────────────────────────────────────────────────────────────────────


def start_rental(cubby: Cubby, member: Member, amount_cents: int) -> str:
    """Hold ``cubby`` for ``member`` and open a subscription Checkout; return its URL.

    The member's Stripe customer comes first (the Tab's, so the portal shows one customer). Then,
    under the guild lock and the cubby row lock: the cubby must be rentable and nobody else may
    hold it, and the amount is checked against the floor as it is now. The hold is a Pending
    rental; Stripe is called after the lock is released. A Stripe failure deletes the hold.

    A member who already holds this cubby awaiting payment (a double tap, a second tab) gets that
    same Checkout back while it is open at the same amount; otherwise the old hold is settled
    first (a paid one becomes theirs, a dead one is released) and the rent starts again once.

    Raises:
        CubbyTakenError: Someone else holds it.
        CubbyUnavailableError: It cannot be rented, or it is already the member's.
        AmountError: The amount is outside the floor and the ceiling.
    """
    from billing import stripe_utils
    from billing.models import Tab

    tab, _created = Tab.objects.get_or_create(member=member)
    customer_id = tab.get_or_create_stripe_customer()
    released = False
    for _attempt in range(2):
        try:
            rental, own_hold = _hold(cubby, member, amount_cents, customer_id)
        except CubbyRentalError:
            if released:
                # The member's old hold was released and this rent was refused: the cubby is open (#980).
                open_for_waitlist(cubby)
            raise
        if own_hold is not None:
            url = _resume_or_settle(own_hold, amount_cents)
            if url:
                return url
            released = True
            continue
        assert rental is not None
        locked_cubby = rental.cubby
        guild = locked_cubby.unit.guild
        success_url, cancel_url = _checkout_urls(rental)
        try:
            session = stripe_utils.create_subscription_checkout_session(
                customer_id=customer_id,
                amount_cents=amount_cents,
                product_name=f"Cubby {locked_cubby.label}, {locked_cubby.unit.name} ({guild.name})",
                description=f"Cubby {locked_cubby.label} on the {locked_cubby.unit.name}, {guild.name}",
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={
                    "kind": CHECKOUT_KIND,
                    "rental_pk": str(rental.pk),
                    "cubby_pk": str(locked_cubby.pk),
                    "member_pk": str(member.pk),
                },
                # Stripe replays the first answer for a key, so a retried call never mints a second Checkout.
                idempotency_key=f"cubby-checkout-{rental.pk}",
                expires_at=int((timezone.now() + CHECKOUT_LIFETIME).timestamp()),
            )
        except Exception:
            _delete_hold(rental, expire_session=False)
            raise
        rental.stripe_checkout_session_id = session["id"]
        rental.save(update_fields=["stripe_checkout_session_id"])
        return session["url"]
    # Both tries released a hold of the member's and neither was replaced, so the cubby is open (#980).
    open_for_waitlist(cubby)
    raise CubbyTakenError(BEING_PAID_FOR.format(label=cubby.label))


def _hold(
    cubby: Cubby, member: Member, amount_cents: int, customer_id: str
) -> tuple[CubbyRental | None, CubbyRental | None]:
    """Under the locks: a new Pending hold, or the member's own existing hold to resume. One is None."""
    with transaction.atomic():
        _lock_guild(cubby.unit.guild_id)
        locked = Cubby.objects.select_for_update(of=("self",)).select_related("unit__guild").get(pk=cubby.pk)
        reason = unavailable_reason(locked)
        if reason:
            raise CubbyUnavailableError(reason)
        live = CubbyRental.objects.live().filter(cubby=locked).first()
        if live is not None:
            if live.member_id != member.pk:
                raise CubbyTakenError(f"Someone just rented {locked.label}. Pick another open cubby.")
            if live.status != Status.PENDING_PAYMENT:
                raise CubbyUnavailableError(f"{locked.label} is already yours.")
            return None, live
        problem = amount_problem(locked, amount_cents)
        if problem:
            raise AmountError(problem)
        rental = CubbyRental.objects.create(
            cubby=locked,
            member=member,
            amount_cents=amount_cents,
            status=Status.PENDING_PAYMENT,
            stripe_customer_id=customer_id,
        )
        return rental, None


def _resume_or_settle(hold: CubbyRental, amount_cents: int) -> str:
    """The open Checkout of the member's own hold at the same amount, or "" once the hold is settled.

    Raises:
        CubbyUnavailableError: The hold was paid after all; the cubby is now theirs.
    """
    from billing import stripe_utils

    if not hold.stripe_checkout_session_id:
        if hold.created_at > timezone.now() - HOLD_IN_PROGRESS:
            # Another tab of the member's is creating this hold's Checkout right now: leave it be.
            raise CubbyTakenError(BEING_PAID_FOR.format(label=hold.cubby.label))
        _delete_hold(hold, expire_session=False, reopens=False)
        return ""
    try:
        session = stripe_utils.retrieve_checkout_session(session_id=hold.stripe_checkout_session_id)
    except stripe_utils.CheckoutSessionNotFound:
        _delete_hold(hold, expire_session=False, reopens=False)
        return ""
    if session["status"] == "complete" and session["subscription"]:
        settle_subscription(session["subscription"])
        raise CubbyUnavailableError(f"{hold.cubby.label} is already yours.")
    if session["status"] == "open" and session["url"] and hold.amount_cents == amount_cents:
        return str(session["url"])
    _delete_hold(hold, expire_session=session["status"] == "open", reopens=False)
    return ""


def _delete_hold(rental: CubbyRental, *, expire_session: bool = True, reopens: bool = True) -> bool:
    """Delete an unpaid hold (status guarded, so a row a webhook just activated survives).

    Its cubby opens, so the waitlist hears (#980), unless ``reopens`` is False: the member's own
    hold being replaced by a new one in the same rent. Returns whether a hold was deleted, so a
    second delivery of the same release tells nobody.
    """
    if expire_session:
        _expire_session_best_effort(rental)
    deleted, _rows = CubbyRental.objects.filter(pk=rental.pk, status=Status.PENDING_PAYMENT).delete()
    if deleted and reopens:
        open_for_waitlist(rental.cubby)
    return bool(deleted)


def _expire_session_best_effort(rental: CubbyRental) -> None:
    from billing import stripe_utils

    if not rental.stripe_checkout_session_id:
        return
    try:
        stripe_utils.expire_checkout_session(session_id=rental.stripe_checkout_session_id)
    except Exception:
        logger.info("Could not expire Checkout session for cubby hold %s (best effort).", rental.pk)


# ── Settling a subscription: the one mirror ──────────────────────────────────


def settle_subscription(subscription_id: str) -> str:
    """Read the subscription from Stripe and make its rental match it. Safe to call any number of times.

    Every road that learns something about a subscription ends here: the Checkout webhooks and
    pages, ``invoice.*``, ``customer.subscription.*`` and the sweep. A subscription that is still
    billing keeps its card on the customer, not on itself, so a card fixed in the Billing Portal
    pays the next charge (:func:`billing.stripe_utils.move_subscription_card_to_customer`).

    Returns:
        ``"settled"`` (mirrored onto its rental), ``"recovered"`` (its hold was gone and the cubby
        was free, so the rental was made again), ``"refunded"`` (the member paid for a cubby they
        did not get: cancelled and refunded), or ``"ignored"`` (not a cubby subscription).
    """
    from billing import stripe_utils

    snapshot = stripe_utils.retrieve_subscription(subscription_id=subscription_id)
    metadata = snapshot["metadata"]
    if metadata.get("kind") != CHECKOUT_KIND:
        return "ignored"
    if snapshot["default_payment_method"] and snapshot["status"] not in _STRIPE_ENDED:
        stripe_utils.move_subscription_card_to_customer(
            customer_id=snapshot["customer"],
            subscription_id=snapshot["id"],
            payment_method_id=snapshot["default_payment_method"],
        )
    rental_pk = int(metadata["rental_pk"])
    if not CubbyRental.objects.filter(pk=rental_pk).exists():
        outcome = _recover_lost_hold(snapshot, rental_pk)
        if outcome != "exists":
            return outcome
    return _mirror(snapshot, rental_pk)


def _apply_snapshot(rental: CubbyRental, snapshot: dict[str, Any]) -> None:
    """Copy the subscription's ids, amount, cancel flag and period end onto ``rental`` (unsaved)."""
    rental.stripe_subscription_id = snapshot["id"]
    rental.stripe_customer_id = snapshot["customer"] or rental.stripe_customer_id
    rental.stripe_subscription_item_id = snapshot["item_id"]
    rental.stripe_product_id = snapshot["product_id"]
    rental.amount_cents = snapshot["unit_amount"]
    # A dashboard or portal cancel may set cancel_at rather than cancel_at_period_end.
    rental.cancel_at_period_end = bool(snapshot["cancel_at_period_end"] or snapshot["cancel_at"])
    rental.current_period_end = datetime.fromtimestamp(snapshot["current_period_end"], tz=UTC)


def _mirror(snapshot: dict[str, Any], rental_pk: int) -> str:
    """Mirror ``snapshot`` onto rental ``rental_pk`` under its row lock, then notify and clean up."""
    status = _STATUS_FOR[snapshot["status"]]
    became_active = ended = refund = False
    with transaction.atomic():
        rental = _locked_rental(pk=rental_pk)
        assert rental is not None  # checked or recreated by settle_subscription; rentals are never deleted once paid
        if rental.status == Status.ENDED:
            # Final. One that never started is a cubby the member did not get: whatever Stripe says
            # now (an earlier attempt may already have cancelled it), its payment goes back.
            refund = rental.start_date is None
        else:
            _apply_snapshot(rental, snapshot)
            if status == Status.ENDED:
                ended = True
                rental.status = Status.ENDED
                rental.end_date = timezone.localdate()
                # Its cubby is open once this commits; a retried webhook finds it Ended above and opens nothing (#980).
                open_for_waitlist(rental.cubby)
            elif status != Status.PENDING_PAYMENT:
                if rental.status == Status.PENDING_PAYMENT:
                    became_active = True
                    rental.start_date = timezone.localdate()
                if status == Status.ACTIVE:
                    rental.payment_failed_at = None
                    rental.next_payment_attempt = None
                rental.status = status
            rental.save()
    if refund:
        return "refunded" if _refund_first_payment(rental, snapshot) else "settled"
    if snapshot["status"] == "unpaid":
        # Stripe gave up on the card: the rental ends now, and customer.subscription.deleted frees the cubby.
        _cancel_subscription(snapshot["id"])
    if became_active:
        notify_rented(rental)
        notify_staff_rental_started(rental)
    if ended and rental.start_date is not None:
        notify_ended(rental)
        notify_staff_ended(rental)
    return "settled"


def _recover_lost_hold(snapshot: dict[str, Any], rental_pk: int) -> str:
    """A paid subscription whose hold is gone: the cubby is the member's if still free, else refund.

    The rental is made again under its old pk, so every later invoice and webhook finds it. When
    the cubby is archived, out of service or held by someone else, it is made Ended and the
    subscription is cancelled and its first payment refunded.

    Returns ``"exists"`` when a concurrent call already made it, else ``"recovered"``, ``"refunded"``
    or ``"settled"`` (not free, and nothing paid to refund yet).
    """
    metadata = snapshot["metadata"]
    cubby = Cubby.objects.select_related("unit__guild").get(pk=int(metadata["cubby_pk"]))
    member = Member.objects.get(pk=int(metadata["member_pk"]))
    with transaction.atomic():
        _lock_guild(cubby.unit.guild_id)
        locked = Cubby.objects.select_for_update(of=("self",)).select_related("unit__guild").get(pk=cubby.pk)
        if CubbyRental.objects.filter(pk=rental_pk).exists():
            return "exists"
        taken = CubbyRental.objects.live().filter(cubby=locked).exists()
        closed = locked.is_archived or locked.unit.is_archived or locked.out_of_service
        free = not taken and not closed and member.deleted_at is None
        rental = CubbyRental(pk=rental_pk, cubby=locked, member=member)
        _apply_snapshot(rental, snapshot)
        if free:
            # Made as a hold again and mirrored like any other below, which activates it and tells the member.
            rental.status = Status.PENDING_PAYMENT
        else:
            rental.status = Status.ENDED
            rental.end_date = timezone.localdate()
        rental.save(force_insert=True)
    logger.error(
        "Cubby rental %s was paid after its hold was released; the cubby was %s.",
        rental_pk,
        "free" if free else "not free",
    )
    if free:
        _mirror(snapshot, rental_pk)
        return "recovered"
    return "refunded" if _refund_first_payment(rental, snapshot) else "settled"


def _cancel_subscription(subscription_id: str, *, idempotency_key: str | None = None) -> None:
    """Cancel now, quietly when a concurrent webhook or an earlier attempt already did.

    Stripe refuses to cancel a canceled subscription; that refusal is checked against the
    subscription as it is now, and any other refusal propagates.
    """
    import stripe

    from billing import stripe_utils

    try:
        if idempotency_key is None:
            stripe_utils.cancel_subscription(subscription_id=subscription_id)
        else:
            stripe_utils.cancel_subscription(subscription_id=subscription_id, idempotency_key=idempotency_key)
    except stripe.InvalidRequestError:
        if stripe_utils.retrieve_subscription(subscription_id=subscription_id)["status"] not in _STRIPE_ENDED:
            raise


def _refund_first_payment(rental: CubbyRental, snapshot: dict[str, Any]) -> bool:
    """Cancel the subscription and refund what it charged, for a cubby the member did not get.

    Decided by our own state (an Ended rental that never started, see :func:`_mirror`), never by
    Stripe's status, so a retry after a failure partway through still refunds: the cancel is skipped
    once Stripe has it, and the refund once a payment row records it. An invoice not paid yet is
    refunded when its ``invoice.paid`` arrives (:func:`record_invoice_paid`).

    Returns:
        True when this call refunded a payment.
    """
    if snapshot["status"] not in _STRIPE_ENDED:
        _cancel_subscription(snapshot["id"])
    invoice_id = snapshot["latest_invoice"]
    if not invoice_id:
        return False
    return _refund_invoice(rental, invoice_id) is not None


def _refund_invoice(rental: CubbyRental, invoice_id: str) -> CubbyPayment | None:
    """Refund ``invoice_id`` in full once, record it on its payment row (no payee) and tell the member.

    Returns the refunded payment row when this call refunded, else None: already refunded (the row
    says so), or not paid yet (no payment intent; its ``invoice.paid`` comes back here).
    Stripe errors propagate, and the webhook's retry starts over from here.
    """
    from billing import stripe_utils

    if CubbyPayment.objects.filter(stripe_invoice_id=invoice_id).exclude(stripe_refund_id="").exists():
        return None
    payment_intent = stripe_utils.invoice_payment_intent(invoice_id=invoice_id)
    if not payment_intent:
        logger.info("Cubby rental %s: invoice %s is not paid yet; refunding it when it is.", rental.pk, invoice_id)
        return None
    refund = stripe_utils.create_refund(payment_intent_id=payment_intent, idempotency_key=f"cubby-refund-{invoice_id}")
    payment, _created = CubbyPayment.objects.get_or_create(
        stripe_invoice_id=invoice_id,
        defaults={
            "rental": rental,
            "stripe_payment_intent_id": payment_intent,
            "amount_cents": refund["amount"],
            "paid_at": timezone.now(),
        },
    )
    payment.stripe_refund_id = refund["id"]
    payment.payee = None
    payment.save(update_fields=["stripe_refund_id", "payee"])
    notify_refunded(rental, refund["amount"])
    return payment


# ── Invoices ─────────────────────────────────────────────────────────────────


def _invoice_subscription(invoice: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """The subscription metadata and id behind an invoice ({} and "" when it has none)."""
    details = (invoice.get("parent") or {}).get("subscription_details") or {}
    subscription = details.get("subscription") or ""
    if isinstance(subscription, dict):
        subscription = subscription["id"]
    return dict(details.get("metadata") or {}), str(subscription)


def _timestamp(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, tz=UTC) if value else None


def record_invoice_paid(invoice: dict[str, Any]) -> CubbyPayment | None:
    """Record a paid cubby invoice once and bring its rental up to date (``invoice.paid``).

    Settles the subscription first, which makes a Pending or Past Due rental Active (also when this
    arrives before ``checkout.session.completed``). The payment row is one per invoice; its payee is
    the guild's cubby payee right now. An invoice of a cubby the member never got is refunded instead
    and never owes a payee anything. Returns the payment, or None for an invoice not a cubby's. Raises
    :class:`RefundNotReadyError` when a never-started rental's paid invoice has no payment yet, so the
    webhook answers 500 and Stripe retries.
    """
    from billing import stripe_utils

    metadata, subscription_id = _invoice_subscription(invoice)
    if metadata.get("kind") != CHECKOUT_KIND or not subscription_id:
        return None
    settle_subscription(subscription_id)
    payment_intent = stripe_utils.invoice_payment_intent(invoice_id=invoice["id"])
    lines = (invoice.get("lines") or {}).get("data") or [{}]
    period = lines[0].get("period") or {}
    paid_at = _timestamp((invoice.get("status_transitions") or {}).get("paid_at")) or timezone.now()
    with transaction.atomic():
        rental = _locked_rental(pk=int(metadata["rental_pk"]))
        assert rental is not None  # settle_subscription made or kept it
        if not (rental.status == Status.ENDED and rental.start_date is None):
            # The unique invoice id is the guard: a re-delivery (or a racing one) fails the insert in its savepoint.
            try:
                with transaction.atomic():
                    return CubbyPayment.objects.create(
                        rental=rental,
                        stripe_invoice_id=invoice["id"],
                        stripe_payment_intent_id=payment_intent,
                        amount_cents=invoice["amount_paid"],
                        paid_at=paid_at,
                        period_start=_timestamp(period.get("start")),
                        period_end=_timestamp(period.get("end")),
                        payee_id=rental.cubby.unit.guild.cubby_payee_id,
                    )
            except IntegrityError:
                return CubbyPayment.objects.get(stripe_invoice_id=invoice["id"])
    # A cubby the member never got: this money goes back, and no payee is ever owed a share of it.
    _refund_invoice(rental, invoice["id"])
    payment = CubbyPayment.objects.filter(stripe_invoice_id=invoice["id"]).first()
    if payment is None:
        # Paid, yet Stripe lists no payment for it yet: fail the delivery so Stripe sends it again.
        raise RefundNotReadyError(f"Cubby invoice {invoice['id']} is paid but has no payment to refund yet.")
    return payment


def record_payment_failed(invoice: dict[str, Any]) -> None:
    """Mark the rental Past Due as Stripe has it, and tell the member once per invoice (``invoice.payment_failed``).

    The subscription is read again, so a failure delivered after a later payment changes nothing
    and tells nobody.
    """
    metadata, subscription_id = _invoice_subscription(invoice)
    if metadata.get("kind") != CHECKOUT_KIND or not subscription_id:
        return
    settle_subscription(subscription_id)
    with transaction.atomic():
        rental = _locked_rental(pk=int(metadata["rental_pk"]))
        assert rental is not None  # settle_subscription made or kept it
        if rental.status != Status.PAST_DUE or invoice.get("status") == "paid":
            return
        rental.payment_failed_at = timezone.now()
        rental.next_payment_attempt = _timestamp(invoice.get("next_payment_attempt"))
        rental.save(update_fields=["payment_failed_at", "next_payment_attempt"])
    notify_payment_failed(rental, invoice)
    notify_staff_past_due(rental, invoice)


# ── The member's own buttons ─────────────────────────────────────────────────


def _locked_billing_rental(rental: CubbyRental) -> CubbyRental:
    """The rental re-read under its lock, refusing one that is not billing (Active or Past Due)."""
    locked = _locked_rental(pk=rental.pk)
    if locked is None or locked.status not in (Status.ACTIVE, Status.PAST_DUE) or not locked.stripe_subscription_id:
        raise CubbyRentalError("This rental isn't active any more.")
    return locked


def change_amount(rental: CubbyRental, amount_cents: int) -> CubbyRental:
    """Charge ``amount_cents`` a month from the next charge on. Nothing is charged or credited now.

    Keeping the current amount is always allowed, even below a floor raised since; any other
    amount follows the floor as it is now.

    Raises:
        AmountError: The new amount is outside the floor and the ceiling.
        CubbyRentalError: The rental is not billing.
    """
    from billing import stripe_utils

    with transaction.atomic():
        locked = _locked_billing_rental(rental)
        if amount_cents == locked.amount_cents:
            return locked
        problem = amount_problem(locked.cubby, amount_cents)
        if problem:
            raise AmountError(problem)
    stripe_utils.update_subscription_amount(
        subscription_id=locked.stripe_subscription_id or "",
        item_id=locked.stripe_subscription_item_id,
        product_id=locked.stripe_product_id,
        amount_cents=amount_cents,
    )
    CubbyRental.objects.filter(pk=locked.pk).update(amount_cents=amount_cents)
    locked.amount_cents = amount_cents
    return locked


def set_cancelling(rental: CubbyRental, *, cancel: bool) -> CubbyRental:
    """Cancel Rental (the cubby stays theirs until the paid month ends) or Keep My Cubby.

    An assigned rental has no paid month, so its Cancel Rental ends it today (#979), and it is
    never cancelling, so Keep My Cubby has nothing to undo.

    Raises:
        CubbyRentalError: The rental is not billing, or an assigned rental was asked to keep.
    """
    from billing import stripe_utils

    if rental.is_assigned:
        if not cancel:
            raise CubbyRentalError("This rental isn't ending.")
        return end_rental(rental)

    with transaction.atomic():
        locked = _locked_billing_rental(rental)
    stripe_utils.set_subscription_cancel_at_period_end(
        subscription_id=locked.stripe_subscription_id or "", cancel=cancel
    )
    CubbyRental.objects.filter(pk=locked.pk).update(cancel_at_period_end=cancel)
    locked.cancel_at_period_end = cancel
    return locked


def portal_url(rental: CubbyRental) -> str:
    """A Billing Portal link on the member's customer, open on Update Payment Method, back to the tab.

    Raises:
        CubbyRentalError: The rental is not billing.
    """
    from billing import stripe_utils

    with transaction.atomic():
        locked = _locked_billing_rental(rental)
    return stripe_utils.create_payment_method_portal_session(
        customer_id=locked.stripe_customer_id, return_url=_absolute_url(tab_url(locked.cubby.unit.guild))
    )


# ── Holds: the return page, the cancel page and the sweep ────────────────────


def finish_checkout(rental_pk: int) -> str:
    """What the Checkout return page shows: ``"active"``, ``"pending"`` (Stripe not done yet) or ``"gone"``."""
    from billing import stripe_utils

    rental = CubbyRental.objects.filter(pk=rental_pk).first()
    if rental is None:
        return "gone"
    if rental.status == Status.PENDING_PAYMENT and rental.stripe_checkout_session_id:
        try:
            session = stripe_utils.retrieve_checkout_session(session_id=rental.stripe_checkout_session_id)
        except Exception:
            logger.exception("Checkout return: could not read the session for cubby hold %s.", rental.pk)
            return "pending"
        if session["status"] == "complete" and session["subscription"]:
            settle_subscription(session["subscription"])
        rental.refresh_from_db()
    if rental.status in (Status.ACTIVE, Status.PAST_DUE):
        return "active"
    return "pending" if rental.status == Status.PENDING_PAYMENT else "gone"


def release_hold_if_unpaid(rental: CubbyRental) -> str:
    """Release a hold after asking Stripe: ``"released"``, ``"paid"`` (it became the member's) or ``"unknown"``."""
    from billing import stripe_utils

    if rental.status != Status.PENDING_PAYMENT:
        return "paid" if rental.is_live else "released"
    if not rental.stripe_checkout_session_id:
        _delete_hold(rental, expire_session=False)
        return "released"
    try:
        session = stripe_utils.retrieve_checkout_session(session_id=rental.stripe_checkout_session_id)
    except stripe_utils.CheckoutSessionNotFound:
        _delete_hold(rental, expire_session=False)
        return "released"
    except Exception:
        logger.exception("Could not verify the Checkout for cubby hold %s; keeping it.", rental.pk)
        return "unknown"
    if session["status"] == "complete" and session["subscription"]:
        settle_subscription(session["subscription"])
        return "paid"
    _delete_hold(rental, expire_session=session["status"] == "open")
    return "released"


def release_expired_hold(session: dict[str, Any]) -> None:
    """``checkout.session.expired``: delete the hold it held. Stripe fires it only for an unpaid session."""
    metadata = session.get("metadata") or {}
    if metadata.get("kind") != CHECKOUT_KIND:
        return
    hold = CubbyRental.objects.filter(pk=int(metadata["rental_pk"]), status=Status.PENDING_PAYMENT).first()
    if hold is not None:
        _delete_hold(hold, expire_session=False)


def complete_checkout(session: dict[str, Any]) -> str:
    """``checkout.session.completed``: settle the subscription it started; "ignored" for another kind."""
    metadata = session.get("metadata") or {}
    if metadata.get("kind") != CHECKOUT_KIND:
        return "ignored"
    subscription = session.get("subscription")
    if not subscription:
        logger.error("Cubby Checkout %s completed with no subscription.", session.get("id"))
        return "ignored"
    return settle_subscription(subscription if isinstance(subscription, str) else subscription["id"])


def release_stale_holds(*, now: datetime | None = None) -> tuple[int, int]:
    """The sweep: holds older than two hours, each checked with Stripe, never released on age alone.

    Returns:
        ``(released, recovered)``: holds deleted, and paid ones whose webhook was lost.
    """
    cutoff = (now or timezone.now()) - CubbyRental.HOLD_SWEEP_AGE
    released = recovered = 0
    for rental in CubbyRental.objects.filter(status=Status.PENDING_PAYMENT, created_at__lt=cutoff):
        outcome = release_hold_if_unpaid(rental)
        if outcome == "released":
            released += 1
        elif outcome == "paid":
            recovered += 1
    return released, recovered


def end_for_deleted_account(member: Member) -> None:
    """A member deleting their account: every live rental stops now, with no refund.

    A hold is released (its Checkout expired); a billing rental's subscription is cancelled and
    the rental ended at once, telling the member nothing (the account is going) and the guild's
    staff that the shelf needs clearing. Stripe errors propagate, so
    the deletion stops rather than leave a card billing an account that no longer exists. They
    leave every cubby waitlist first, so the cubbies they free go to someone else (#980).
    """
    leave_everywhere(member)
    for rental in CubbyRental.objects.live().filter(member=member).select_related("cubby__unit__guild", "member"):
        if rental.status == Status.PENDING_PAYMENT:
            _delete_hold(rental)
            continue
        if rental.stripe_subscription_id:
            _cancel_subscription(rental.stripe_subscription_id)
        # Guarded on a live status: the webhook for that cancel may have ended it already, and only
        # the change that ends it opens the cubby, so one opening tells one member (#980).
        ended = (
            CubbyRental.objects.live().filter(pk=rental.pk).update(status=Status.ENDED, end_date=timezone.localdate())
        )
        if not ended:
            continue
        rental.status = Status.ENDED
        open_for_waitlist(rental.cubby)
        # The member hears nothing, but the guild still has a shelf to clear (#979).
        notify_staff_ended(rental)


# ── Staff: assign and end (#979) ─────────────────────────────────────────────


def assign_problem(cubby: Cubby) -> str:
    """Why guild staff cannot assign ``cubby`` right now (archived, or out of service), or "".

    Unlike renting, the Cubbies tab switch does not matter: staff assign while it is off too.
    """
    if cubby.is_archived or cubby.unit.is_archived:
        return f"{cubby.label} is archived."
    if cubby.out_of_service:
        return f"{cubby.label} is out of service right now."
    return ""


def assign_rental(cubby: Cubby, member: Member, *, source: str, amount_cents: int, actor: Member | None) -> CubbyRental:
    """Give ``cubby`` to ``member`` without Stripe: Comped (free) or Paid Another Way (cash or a check).

    Under the guild lock and the cubby row lock (part 1's and part 2's), the cubby must be
    assignable and nobody may hold it: not rented, not being paid for. The rental is Active
    from today with no subscription, and nothing ever charges it. The member is told the cubby
    is theirs; the guild's other lead and staff hear about the new rental.

    Raises:
        CubbyTakenError: Someone rents it or is paying for it right now.
        CubbyUnavailableError: It is archived or out of service.
        AmountError: A Paid Another Way amount outside $1 to $500 a month.
    """
    if source == CubbyRental.Source.COMPED:
        amount_cents = 0
    elif source == CubbyRental.Source.OUTSIDE:
        if not CUBBY_PRICE_MIN_CENTS <= amount_cents <= CUBBY_PRICE_MAX_CENTS:
            raise AmountError(
                f"Enter between {cubby_dollars(CUBBY_PRICE_MIN_CENTS)} and {cubby_dollars(CUBBY_PRICE_MAX_CENTS)} a month."
            )
    else:
        raise ValueError(f"Staff assign a cubby as Comped or Paid Another Way, not {source!r}.")
    with transaction.atomic():
        _lock_guild(cubby.unit.guild_id)
        locked = Cubby.objects.select_for_update(of=("self",)).select_related("unit__guild").get(pk=cubby.pk)
        problem = assign_problem(locked)
        if problem:
            raise CubbyUnavailableError(problem)
        live = CubbyRental.objects.live().select_related("member").filter(cubby=locked).first()
        if live is not None:
            if live.status == Status.PENDING_PAYMENT:
                raise CubbyTakenError(BEING_PAID_FOR.format(label=locked.label))
            raise CubbyTakenError(f"{locked.label} is already rented by {live.member.display_name}.")
        rental = CubbyRental.objects.create(
            cubby=locked,
            member=member,
            amount_cents=amount_cents,
            source=source,
            status=Status.ACTIVE,
            start_date=timezone.localdate(),
        )
    notify_rented(rental)
    notify_staff_rental_started(rental, actor=actor)
    return rental


def end_rental(rental: CubbyRental, *, reason: str = "") -> CubbyRental:
    """End a live rental today, with no refund: staff End Rental, or a member cancelling an assigned rental.

    A rental billing through Stripe has its subscription cancelled now (``prorate=false``, key
    ``cubby-end-<pk>``) while its row is locked, so the ``customer.subscription.deleted`` Stripe
    sends next waits on the lock, then finds it Ended and adds nothing. An assigned rental makes
    no Stripe call. The cubby opens at once. The member is told (with ``reason`` when staff gave
    one) and the guild's lead and staff are asked to clear the shelf.

    Raises:
        CubbyRentalError: It is not Active or Past Due any more.
    """
    with transaction.atomic():
        locked = _locked_rental(pk=rental.pk)
        if locked is None or locked.status not in (Status.ACTIVE, Status.PAST_DUE):
            raise CubbyRentalError("This rental isn't active any more.")
        if locked.stripe_subscription_id and not locked.is_assigned:
            _cancel_subscription(locked.stripe_subscription_id, idempotency_key=f"cubby-end-{locked.pk}")
        locked.status = Status.ENDED
        locked.end_date = timezone.localdate()
        locked.end_reason = reason.strip()
        locked.save(update_fields=["status", "end_date", "end_reason"])
        open_for_waitlist(locked.cubby)
    notify_ended(locked)
    notify_staff_ended(locked)
    return locked


# ── Notifications ────────────────────────────────────────────────────────────


def retry_sentence(rental: CubbyRental) -> str:
    """ "We'll try your card again Mon, Nov 10." from Stripe's next attempt, or "" when it will not try again."""
    if rental.next_payment_attempt is None:
        return ""
    return f"We'll try your card again {date_display(rental.next_payment_attempt)}."


def ordinal(day: int) -> str:
    """A day of the month as "1st", "2nd", "27th"."""
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def date_display(value: date | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = timezone.localtime(value).date()
    return f"{value:%a, %b} {value.day}"


def _email_context(rental: CubbyRental) -> dict[str, Any]:
    cubby = rental.cubby
    guild = cubby.unit.guild
    return {
        "rental": rental,
        "cubby": cubby,
        "guild": guild,
        "first_name": rental.member.display_name.split(" ")[0],
        "tab_url": _absolute_url(tab_url(guild)),
        "guild_url": _absolute_url(reverse("hub_guild_detail", args=[guild.slug])),
        "next_charge": date_display(rental.current_period_end),
        "charge_day": ordinal(timezone.localtime(rental.current_period_end).day) if rental.current_period_end else "",
    }


def _send(
    rental: CubbyRental,
    event_key: str,
    *,
    template: str,
    subject: str,
    title: str,
    body: str,
    period: str,
    **extra: Any,
) -> None:
    """One member email from ``membership/emails/<template>.{txt,html}`` (the branded shell) plus the bell row."""
    from core.events.senders import emit_with_email_shell

    url = tab_url(rental.cubby.unit.guild)
    emit_with_email_shell(
        event_key,
        target=rental,
        context={"user": rental.member.user},
        subject=subject,
        text_template=f"membership/emails/{template}.txt",
        html_template=f"membership/emails/{template}.html",
        template_context={**_email_context(rental), **extra},
        in_app_title=title,
        in_app_body=body,
        url=url,
        period=period,
    )


def notify_rented(rental: CubbyRental) -> None:
    """``cubbies.rented``: the cubby is the member's now."""
    cubby = rental.cubby
    _send(
        rental,
        "cubbies.rented",
        template="cubby_rented",
        subject=f"{cubby.label} is yours",
        title=f"{cubby.label} is yours",
        body=_rented_sentence(rental),
        period=f"cubby_rental:{rental.pk}:rented",
        manage_url="" if rental.is_assigned else _absolute_url(reverse("hub_cubby_rental_portal", args=[rental.pk])),
    )


def _rented_sentence(rental: CubbyRental) -> str:
    """The bell line for a cubby that is the member's now, in how it is paid for."""
    cubby = rental.cubby
    where = f"cubby {cubby.label} on the {cubby.unit.name}"
    if rental.is_comped:
        return f"Guild staff gave you {where}. It's free."
    if rental.is_assigned:
        return f"Guild staff gave you {where}. You pay the guild {rental.amount_display} a month directly."
    return f"You're renting {where} for {rental.amount_display} a month."


def notify_payment_failed(rental: CubbyRental, invoice: dict[str, Any]) -> None:
    """``cubbies.payment_failed``: once per failed invoice."""
    cubby = rental.cubby
    retry_line = retry_sentence(rental)
    _send(
        rental,
        "cubbies.payment_failed",
        template="cubby_payment_failed",
        subject="Your cubby payment didn't go through",
        title="Your cubby payment didn't go through",
        body=f"Update your card to keep {cubby.label}. {retry_line}".strip(),
        period=f"cubby_rental:{rental.pk}:invoice:{invoice['id']}",
        failed_on=date_display(rental.payment_failed_at),
        retry_line=retry_line,
        charged=cubby_dollars(int(invoice.get("amount_due") or rental.amount_cents)),
        manage_url=_absolute_url(reverse("hub_cubby_rental_portal", args=[rental.pk])),
    )


def notify_ended(rental: CubbyRental) -> None:
    """``cubbies.rental_ended``: the subscription ended and the cubby is free again."""
    cubby = rental.cubby
    _send(
        rental,
        "cubbies.rental_ended",
        template="cubby_rental_ended",
        subject=f"Your rental of {cubby.label} has ended",
        title=f"Your rental of {cubby.label} has ended",
        body=_ended_sentence(rental),
        period=f"cubby_rental:{rental.pk}:ended",
    )


def _ended_sentence(rental: CubbyRental) -> str:
    """The bell line for an ended rental, with the reason guild staff gave."""
    cubby = rental.cubby
    sentence = f"Cubby {cubby.label} on the {cubby.unit.name} is no longer yours"
    sentence += "." if rental.is_assigned else ", and you won't be charged again."
    if rental.end_reason:
        sentence += f" Guild staff said: {rental.end_reason}"
    return sentence


def notify_refunded(rental: CubbyRental, amount_cents: int) -> None:
    """``cubbies.rental_refunded``: someone else got the cubby first, so nothing was kept."""
    cubby = rental.cubby
    _send(
        rental,
        "cubbies.rental_refunded",
        template="cubby_rental_refunded",
        subject=f"You've been refunded for {cubby.label}",
        title=f"You've been refunded for {cubby.label}",
        body=f"{cubby.label} wasn't free by the time your payment went through, so we refunded your {cubby_dollars(amount_cents)}.",
        period=f"cubby_rental:{rental.pk}:refunded",
        refunded=cubby_dollars(amount_cents),
    )


# ── Staff notifications (#979) ───────────────────────────────────────────────


def rentals_url(guild: Guild) -> str:
    """The staff Rentals tab of ``guild``'s Cubbies manager (relative)."""
    return reverse("hub_guild_cubbies_rentals", args=[guild.pk])


def rental_staff_url(rental: CubbyRental, show: str = "all") -> str:
    """One rental's own place on the Rentals tab (relative): its chip and its row's anchor.

    Each staff event links its own rental, so the 8 AM digest (which keys a line, and its
    still-open check, on the link) keeps one line per rental and drops only the one paid.
    """
    return f"{rentals_url(rental.cubby.unit.guild)}?show={show}#rental-{rental.pk}"


def _staff_context(rental: CubbyRental, show: str = "all") -> dict[str, Any]:
    """What the three staff events' copy reads (``core/events/copy.py``), plus the guild the resolver needs."""
    cubby = rental.cubby
    guild = cubby.unit.guild
    return {
        "guild": guild,
        "guild_name": guild.name,
        "cubby_label": cubby.label,
        "unit_name": cubby.unit.name,
        "member_name": rental.member.display_name,
        "amount": "Comped" if rental.is_comped else f"{rental.amount_display} a month",
        "reason_line": f" The reason given: {rental.end_reason.rstrip('.')}." if rental.end_reason else "",
        "rentals_url": _absolute_url(rental_staff_url(rental, show)),
    }


def _emit_staff(
    rental: CubbyRental, event_key: str, *, period: str, show: str = "all", actor: Member | None = None
) -> None:
    """One staff event to the guild's lead and staff (the bell, and email where it has one), once per ``period``.

    ``actor`` (staff who did it themselves) is left out; the rest of the leadership still hears.
    """
    from core.events.emit import emit

    context = _staff_context(rental, show)
    recipient_user_ids: set[int] | None = None
    if actor is not None:
        guild: Guild = context["guild"]
        recipient_user_ids = {
            member.user_id
            for member in guild.leadership_members()
            if member.user_id is not None and member.pk != actor.pk
        }
    emit(
        event_key,
        target=rental,
        context=context,
        url=rental_staff_url(rental, show),
        period=period,
        recipient_user_ids=recipient_user_ids,
    )


def notify_staff_rental_started(rental: CubbyRental, *, actor: Member | None = None) -> None:
    """``cubbies.rental_started``: a member rented one of the guild's cubbies, or staff assigned one."""
    _emit_staff(rental, "cubbies.rental_started", period=f"cubby_rental:{rental.pk}:staff_started", actor=actor)


def notify_staff_past_due(rental: CubbyRental, invoice: dict[str, Any]) -> None:
    """``cubbies.rental_past_due``: a monthly charge failed. Its email waits for the 8 AM digest, which
    drops it once the rental is paid (the still-open check reads ``cubby_past_due:<pk>``)."""
    _emit_staff(
        rental, "cubbies.rental_past_due", period=f"cubby_past_due:{rental.pk}:{invoice['id']}", show="past_due"
    )


def notify_staff_ended(rental: CubbyRental) -> None:
    """``cubbies.staff_rental_ended``: a rental ended however it ended, so its shelf needs clearing."""
    _emit_staff(rental, "cubbies.staff_rental_ended", period=f"cubby_rental:{rental.pk}:staff_ended", show="ended")
