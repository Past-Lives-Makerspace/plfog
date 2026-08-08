"""Create or link a passwordless account for a confirmed guest registration.

Called from the free-class confirm branch (``classes.views.register``) and the
Stripe webhook (``classes.webhook_handlers.handle_checkout_session_completed``)
right after a registration is confirmed/paid — never at form submit, so we never
mint accounts for abandoned checkouts.

Design (see docs/superpowers/plans/2026-06-23-guest-account-creation-and-mailchimp-answers.md):

- **Opt-in.** Only runs when the registration asked for an account
  (``Registration.create_account``). The guard lives in the callers; this
  service is the single place that does the create-or-link work.
- **Idempotent.** Re-delivery of a Stripe webhook, a double-submit, or a guest
  who already has an account all resolve to "link, never duplicate".
- **Respects INVITE_ONLY.** Auto-minting accounts would bypass the invite gate,
  so we skip creation under ``SiteConfiguration.RegistrationMode.INVITE_ONLY``
  (the guest's booking still works via its ``self_serve_token``).
- **Fails closed.** Account creation must never block a booking confirmation, so
  every path swallows-and-logs rather than raising into the request/webhook.

Reuses the existing signup plumbing: creating a ``User`` fires
``membership.signals.ensure_user_has_member`` (links or creates the ``Member``
and promotes any staged ``MemberEmail`` rows to a verified primary allauth
``EmailAddress`` via ``MemberEmail.objects.migrate_to_user``). We never build a
``Member`` or ``EmailAddress`` by hand here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from classes.models import Registration


def ensure_account_for_registration(registration: Registration) -> None:
    """Create or link a passwordless account for a confirmed registration.

    Safe to call unconditionally after confirmation — it self-guards on the
    opt-in flag and on invite-only mode, and never raises. Links the
    registration (and any other guest registrations sharing the email) to the
    resolved user, and seeds the user's profile with the registration's answers.

    Args:
        registration: A confirmed (free) or paid registration.
    """
    try:
        _ensure_account_for_registration(registration)
    except Exception:  # noqa: BLE001 - account creation must never block a booking
        logger.exception(
            "Guest account creation failed for registration %s (%s); booking stands.",
            registration.pk,
            registration.email,
        )


def _ensure_account_for_registration(registration: Registration) -> None:
    if not registration.create_account:
        return
    if _is_invite_only():
        logger.info(
            "Skipping guest account creation for %s: registration mode is invite-only.",
            registration.email,
        )
        return

    email = (registration.email or "").strip()
    if not email:
        return

    user = _resolve_or_create_user(registration, email)
    if user is None:
        return

    _link_registrations_to_user(user, email)
    _seed_profile_from_registration(user, registration)


def _is_invite_only() -> bool:
    from core.models import SiteConfiguration

    site = SiteConfiguration.load()
    return site.registration_mode == SiteConfiguration.RegistrationMode.INVITE_ONLY


def _resolve_or_create_user(registration: Registration, email: str):  # noqa: ANN202 - returns the User model
    """Find the user this email already belongs to, or create a passwordless one.

    Resolution order, never duplicating an existing account:
      1. The registration's already-linked member's user.
      2. Any user whose email matches across the three email stores.
      3. A new passwordless ``User`` (the post-save signal links/creates the
         ``Member`` and promotes the email to a verified primary EmailAddress).
    """
    from django.contrib.auth import get_user_model

    user_model = get_user_model()

    member = registration.member
    if member is not None and member.user_id:
        return member.user

    existing = _find_user_for_email(email)
    if existing is not None:
        return existing

    from django.db import IntegrityError, transaction

    try:
        # Wrap in a savepoint so a username clash (double submit / webhook race)
        # doesn't poison the surrounding transaction for the re-resolve below.
        with transaction.atomic():
            return user_model.objects.create_user(username=email, email=email)
    except IntegrityError:
        logger.info("Guest account already exists for %s; re-using it.", email)
        return _find_user_for_email(email)


def _find_user_for_email(email: str):  # noqa: ANN202 - returns the User model or None
    """Resolve an existing user for this email, or None.

    Checks ``User.email`` and any matching allauth ``EmailAddress`` (verified or
    not). A linked Member always has its emails promoted to ``EmailAddress`` by
    ``MemberEmail.objects.migrate_to_user``, so these two lookups cover every
    case where an actual ``User`` already exists — there's no linked user behind
    a bare ``Member._pre_signup_email`` or ``MemberEmail`` alias to find.
    """
    from django.contrib.auth import get_user_model

    user_model = get_user_model()

    return (
        user_model.objects.filter(email__iexact=email).first()
        or user_model.objects.filter(emailaddress__email__iexact=email).first()
    )


def _link_registrations_to_user(user, email: str) -> None:  # noqa: ANN001 - User model
    """Attach this user's Member to every guest registration sharing the email.

    A guest may have booked more than once before making an account; backfilling
    them all means their bookings show up under the new account. Only fills in a
    null member — never reassigns a registration already linked elsewhere.
    """
    from membership.models import Member

    from classes.models import Registration

    member = Member.objects.filter(user=user).first()
    if member is None:
        # The signal couldn't create a Member (e.g. no MembershipPlan seeded).
        # The account still exists and the bookings keep working via their
        # self-serve tokens; just nothing to link.
        logger.warning("No Member for user %s; leaving registrations unlinked.", user.pk)
        return
    Registration.objects.filter(email__iexact=email, member__isnull=True).update(member=member)


def _seed_profile_from_registration(user, registration: Registration) -> None:  # noqa: ANN001 - User model
    """Seed the new account's profile from the registration (mirrors the
    logged-in caching path in ``classes.views._cache_registration_to_profile``,
    which no-ops for the anonymous guest case)."""
    from django.utils import timezone

    from core.models import UserProfile

    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.cache_from_registration(registration)
    answers = {a.question_id: a.answer_text for a in registration.custom_answers.all()}
    if answers:
        profile.set_custom_answers(answers)

    # Mirror a completed newsletter opt-in onto the profile, so the marketing
    # checkbox isn't re-asked on this person's next booking.
    #
    # This can't live in ``subscribe_registration``: that runs *before* us (it has
    # to, or ``derive_tags`` would treat this brand-new account as a known member
    # and drop the ``first-time-student`` tag), and at that point the guest has no
    # user and therefore no profile to stamp. So the stamp lands here, where the
    # profile is first created.
    if registration.subscribed_to_mailchimp and profile.subscribed_to_mailchimp_at is None:
        profile.subscribed_to_mailchimp_at = timezone.now()
        profile.save(update_fields=["subscribed_to_mailchimp_at"])
