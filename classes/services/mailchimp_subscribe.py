"""Bridge between Registration and the Mailchimp client.

Called from the free-class flow (``classes.views.register``) and the Stripe
webhook handler (``classes.webhook_handlers.handle_checkout_session_completed``)
right after the confirmation email is sent. Never raises — Mailchimp must not
be allowed to block a user's registration confirmation.
"""

from __future__ import annotations

import logging

from django.utils.text import slugify

from classes.models import Registration

logger = logging.getLogger(__name__)


def derive_tags(registration: Registration) -> list[str]:
    """Build the Mailchimp tag list for a confirmed registration.

    Always includes ``class-registrant``. Adds category and instructor slugs
    so segmentation can target a specific kind of student. Adds a ``guild-``
    tag when the category is linked to a makerspace Guild. Adds
    ``first-time-student`` when this is the registrant's first confirmed
    registration (by email match — best-effort, since the same person could
    register under multiple email aliases).
    """
    offering = registration.class_offering
    tags = ["class-registrant"]
    category = offering.category
    if category:
        tags.append(f"category-{category.slug}")
        if category.guild_id and category.guild:
            tags.append(f"guild-{slugify(category.guild.name)}")
    if offering.instructor and offering.instructor.instructor_slug:
        tags.append(f"instructor-{offering.instructor.instructor_slug}")

    prior_confirmed = (
        Registration.objects.filter(
            email__iexact=registration.email,
            status=Registration.Status.CONFIRMED,
        )
        .exclude(pk=registration.pk)
        .exists()
    )
    if not prior_confirmed:
        tags.append("first-time-student")

    return tags


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
        tags=derive_tags(registration),
    )
    if not success:
        return

    registration.subscribed_to_mailchimp = True
    registration.save(update_fields=["subscribed_to_mailchimp"])
