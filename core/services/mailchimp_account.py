"""Push an account signup (onboarding completion) to Mailchimp.

Called from ``classes.account.views.OnboardingStepView.form_valid`` right after
the user finishes onboarding step 3. Never raises — Mailchimp must not block
the account flow.
"""

from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def derive_account_tags(profile) -> list[str]:
    """Build the Mailchimp tag list for an account signup.

    Always includes ``account-signup``. Adds ``persona-<status>`` when the
    user picked a first-attendance status, ``referral-<source>`` when they
    named a referral source, and ``interest-<slug>`` per category they opted
    into for new-class email notifications.
    """
    tags = ["account-signup"]
    if profile.first_attendance_status:
        tags.append(f"persona-{profile.first_attendance_status}")
    if profile.referral_source:
        tags.append(f"referral-{profile.referral_source}")
    for slug in profile.interest_category_slugs or []:
        tags.append(f"interest-{slug}")
    return tags


def subscribe_user(user) -> None:
    """Push the user's account signup into Mailchimp, idempotently.

    Skips when the user has no UserProfile, when Mailchimp is unconfigured,
    or when the profile already has ``subscribed_to_mailchimp_at`` set. On
    success, stamps that field. Never raises.
    """
    profile = getattr(user, "profile", None)
    if profile is None:
        return
    if profile.subscribed_to_mailchimp_at is not None:
        return

    from core.integrations.mailchimp import MailchimpClient

    client = MailchimpClient.from_site_config()
    if not client.enabled:
        return

    email = (user.email or "").strip()
    if not email:
        return

    first_name = (profile.preferred_name or user.first_name or "").strip()
    last_name = (user.last_name or "").strip()

    success = client.subscribe(
        email=email,
        first_name=first_name,
        last_name=last_name,
        tags=derive_account_tags(profile),
    )
    if not success:
        return

    profile.subscribed_to_mailchimp_at = timezone.now()
    profile.save(update_fields=["subscribed_to_mailchimp_at"])
