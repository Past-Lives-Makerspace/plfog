"""Bridge between Registration and the Mailchimp client.

Called from the free-class flow (``classes.views.register``) and the Stripe
webhook handler (``classes.webhook_handlers.handle_checkout_session_completed``)
right after the confirmation email is sent. Never raises — Mailchimp must not
be allowed to block a user's registration confirmation.
"""

from __future__ import annotations

import logging

from classes.models import Registration

logger = logging.getLogger(__name__)


def subscribe_registration(registration: Registration) -> None:
    """Subscribe a confirmed registrant to Mailchimp if they opted in.

    Sets ``Registration.subscribed_to_mailchimp = True`` on success so we can
    detect duplicates on Stripe webhook redelivery. Idempotent at this layer
    (early-returns when already subscribed) AND at the HTTP layer (Mailchimp's
    PUT upsert handles re-subscription safely).
    """
    if not registration.wants_newsletter:
        return
    if registration.subscribed_to_mailchimp:
        return

    from core.integrations.mailchimp import MailchimpClient

    client = MailchimpClient.from_site_config()
    if not client.enabled:
        return

    success = client.subscribe(
        email=registration.email,
        first_name=registration.first_name,
        last_name=registration.last_name,
        tags=["class-registrant"],
    )
    if not success:
        return

    registration.subscribed_to_mailchimp = True
    registration.save(update_fields=["subscribed_to_mailchimp"])
