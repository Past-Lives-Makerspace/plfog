"""Stripe webhook handlers for the Classes app.

Registered into the billing app's webhook dispatcher. All handlers must be
idempotent — Stripe retries failed deliveries and may also fire the same
event more than once.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from classes.emails import (
    send_admin_registration_notification,
    send_class_welcome_email,
    send_instructor_registration_notification,
    send_registration_confirmation,
)
from classes.models import DiscountCode, Registration

logger = logging.getLogger(__name__)


def handle_checkout_session_completed(event: dict[str, Any]) -> None:
    """Confirm a class registration whose Stripe Checkout Session completed.

    We only act on sessions tagged ``kind=class_registration`` in their
    metadata so we don't collide with future Checkout uses (e.g. Tab top-ups).
    Idempotent — re-delivery on an already-confirmed registration is a no-op.
    """
    session = event["data"]["object"]
    metadata = session.get("metadata") or {}
    if metadata.get("kind") != "class_registration":
        return

    registration_id = metadata.get("registration_id")
    if not registration_id:
        logger.warning("checkout.session.completed: missing registration_id in metadata")
        return

    if session.get("payment_status") != "paid":
        logger.info(
            "checkout.session.completed: ignoring session %s with payment_status=%s",
            session.get("id"),
            session.get("payment_status"),
        )
        return

    with transaction.atomic():
        try:
            registration = Registration.objects.select_for_update().get(pk=registration_id)
        except Registration.DoesNotExist:
            logger.warning("checkout.session.completed: no registration %s", registration_id)
            return

        if registration.status == Registration.Status.CONFIRMED:
            return  # already handled

        # Intentionally leave ``_acting_user`` unset: this is an automated Stripe
        # event with no human actor, so the audit feed correctly records "System".
        registration.status = Registration.Status.CONFIRMED
        registration.confirmed_at = timezone.now()
        registration.stripe_session_id = session.get("id", registration.stripe_session_id)
        registration.stripe_payment_id = session.get("payment_intent", "") or ""
        amount_total = session.get("amount_total")
        if isinstance(amount_total, int):
            registration.amount_paid_cents = amount_total
        registration.save(
            update_fields=[
                "status",
                "confirmed_at",
                "stripe_session_id",
                "stripe_payment_id",
                "amount_paid_cents",
            ]
        )
        if registration.discount_code_id:
            DiscountCode.objects.filter(pk=registration.discount_code_id).update(use_count=F("use_count") + 1)
            from classes import activity
            from classes.models import CmsActivity

            activity.log(
                CmsActivity.Kind.DISCOUNT_CODE_REDEEMED,
                class_offering=registration.class_offering,
                registration=registration,
                payload={"code": registration.discount_code.code},  # type: ignore[union-attr]  # discount_code_id guard ensures non-None
            )

    send_registration_confirmation(registration)
    send_class_welcome_email(registration)
    send_instructor_registration_notification(registration)
    _instructor = registration.class_offering.instructor
    if _instructor is not None and _instructor.user is not None:
        from core import notifications

        notifications.dispatch(
            "instructor_new_registration",
            [_instructor.user],
            title="New registration",
            body=registration.class_offering.title,
            url="/classes/teach/",
        )
    send_admin_registration_notification(registration)
    from classes.services.mailchimp_subscribe import subscribe_registration

    subscribe_registration(registration)
