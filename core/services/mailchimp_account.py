"""Account-signup → Mailchimp bridge.

The sibling of :mod:`classes.services.mailchimp_subscribe`, which handles the
class-registration opt-in. This module owns the *account* opt-in: the marketing
checkbox on the allauth signup form.

Both bridges share one Mailchimp audience and distinguish themselves by tag, so
a single list backs every subscribe path (see the module docstring on
:mod:`core.integrations.mailchimp`). Everyone who ticks a marketing box anywhere
gets ``newsletter``; the second tag records *which* door they came through.

Best-effort by design: Mailchimp being down, misconfigured, or disabled must
never break account creation. Every failure path returns False and the user is
still signed up.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

ACCOUNT_SIGNUP_TAG = "account-signup"
NEWSLETTER_TAG = "newsletter"


def derive_account_tags(user: User) -> list[str]:
    """Return the Mailchimp tags for an account-signup opt-in.

    ``newsletter`` is the shared cross-path marketing tag (the standalone
    ``/newsletter/`` page applies it too); ``account-signup`` records the door
    this subscriber came through, so Mailchimp can segment signups apart from
    class registrants.

    Args:
        user: The freshly created user. Accepted for signature symmetry with
            ``classes.services.mailchimp_subscribe.derive_tags`` and so
            onboarding-derived tags (referral source, category interests) can be
            folded in here later without changing any caller.

    Returns:
        The tag names to apply to the subscriber.
    """
    return [NEWSLETTER_TAG, ACCOUNT_SIGNUP_TAG]


def subscribe_user(user: User) -> bool:
    """Subscribe a newly signed-up user to Mailchimp and stamp their profile.

    Idempotent at two layers: this function early-returns when the user's
    profile already carries ``subscribed_to_mailchimp_at``, and the underlying
    Mailchimp call is a PUT upsert that never duplicates a contact or resurrects
    an unsubscribe.

    The profile stamp is what suppresses the marketing checkbox on later class
    registrations (``classes.forms.RegistrationForm._user_already_opted_in``),
    so it is only written after Mailchimp actually accepted the subscribe —
    a failed push must not make us stop asking.

    Args:
        user: The user who ticked the marketing opt-in at signup.

    Returns:
        True only when Mailchimp accepted the subscribe. False for every other
        outcome: no email address, already subscribed, integration disabled, or
        an API/network failure.
    """
    from django.utils import timezone

    from core.models import UserProfile

    email = user.email
    if not email:
        return False

    profile = UserProfile.objects.filter(user=user).first()
    if profile is not None and profile.subscribed_to_mailchimp_at is not None:
        return False

    from core.integrations.mailchimp import MailchimpClient

    client = MailchimpClient.from_site_config()
    if not client.enabled:
        logger.info("Mailchimp account signup opt-in for %s skipped: integration disabled", email)
        return False

    success = client.subscribe(
        email=email,
        first_name=user.first_name,
        last_name=user.last_name,
        tags=derive_account_tags(user),
    )
    if not success:
        return False

    if profile is None:
        profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.subscribed_to_mailchimp_at = timezone.now()
    profile.save(update_fields=["subscribed_to_mailchimp_at"])
    return True
