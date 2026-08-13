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


def _is_known_member(email: str) -> bool:
    """True when this email belongs to a known Past Lives Member.

    Unions the three email stores (see membership/CLAUDE.md): the member's
    stored pre-signup email (the Airtable-mirror value), any staged alias,
    and any verified allauth EmailAddress for a linked member. Anyone the
    Airtable pull created has a Member row, so this also satisfies the
    "check the email against the member registry" requirement without a
    separate Airtable lookup (Airtable mirrors no class history).
    """
    from membership.models import Member, MemberEmail

    if Member.objects.filter(_pre_signup_email__iexact=email).exists():
        return True
    if MemberEmail.objects.filter(email__iexact=email).exists():
        return True
    return Member.objects.filter(
        user__emailaddress__email__iexact=email,
        user__emailaddress__verified=True,
    ).exists()


def _answer_tags(registration: Registration) -> list[str]:
    """Mailchimp tags derived from the registrant's own question answers.

    Only Yes/No and Single Choice answers are pushed (free-text makes poor
    tags); a Yes/No "no" is omitted so segments key on the affirmative. The
    tag name is admin-controlled per question via ``RegistrationQuestion``.
    Duplicates are dropped while preserving order.
    """
    tags: list[str] = []
    seen: set[str] = set()
    for answer in registration.custom_answers.select_related("question"):
        tag = answer.question.tag_for(answer.answer_text)
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def derive_tags(registration: Registration) -> list[str]:
    """Build the Mailchimp tag list for a confirmed registration.

    Always includes ``class-registrant``. Adds category and instructor slugs
    so segmentation can target a specific kind of student. Adds a ``guild-``
    tag when the category is linked to a makerspace Guild. Adds
    ``first-time-student`` only when this is the registrant's first confirmed
    registration (by email match) AND the email does not belong to a known
    Past Lives Member — so returning members and Airtable-imported members are
    never mis-tagged as first-timers. Finally appends a tag per taggable
    question answer so Mailchimp segments can route on what the registrant
    actually told us.
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
    if not prior_confirmed and not _is_known_member(registration.email):
        tags.append("first-time-student")

    tags.extend(_answer_tags(registration))

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

    _stamp_profile_subscribed(registration)


def _tags_still_justified(registration: Registration) -> set[str]:
    """Tags the registrant still legitimately holds via their OTHER confirmed registrations.

    A member with two confirmed registrations who cancels one must keep the tags the
    surviving registration still earns (``class-registrant``, a shared category/guild,
    a same-instructor tag). We union ``derive_tags`` across every *other* still-confirmed
    registration at this email so the cancel only strips tags unique to it.
    """
    others = Registration.objects.filter(
        email__iexact=registration.email,
        status=Registration.Status.CONFIRMED,
    ).exclude(pk=registration.pk)
    held: set[str] = set()
    for other in others:
        held.update(derive_tags(other))
    return held


def unsubscribe_registration(registration: Registration) -> None:
    """Remove this registration's unique Mailchimp tags when it is cancelled.

    Only strips tags this registration alone justifies — tags a surviving confirmed
    registration still earns (see :func:`_tags_still_justified`) are left in place so the
    member stays in the segments they still belong to. Clears
    ``subscribed_to_mailchimp`` only when the removal actually succeeds (or there was
    nothing unique to remove), so a failed call is logged and can be retried rather than
    silently marked done.
    """
    if not registration.subscribed_to_mailchimp:
        return

    tags_to_remove = [tag for tag in derive_tags(registration) if tag not in _tags_still_justified(registration)]
    if not tags_to_remove:
        registration.subscribed_to_mailchimp = False
        registration.save(update_fields=["subscribed_to_mailchimp"])
        return

    from core.integrations.mailchimp import MailchimpClient

    client = MailchimpClient.from_site_config()
    if not client.enabled:
        return

    if not client.member_tags_remove(registration.email, tags_to_remove):
        logger.warning("Mailchimp tag-remove did not complete for registration %s; leaving flag set", registration.pk)
        return

    registration.subscribed_to_mailchimp = False
    registration.save(update_fields=["subscribed_to_mailchimp"])


def _stamp_profile_subscribed(registration: Registration) -> None:
    """Mirror the opt-in onto the registrant's UserProfile when one exists.

    Keeps the 'already opted in' signal (used to hide the marketing checkbox)
    accurate across both the class-registration and account-signup paths.
    Fails closed: anonymous registrants have no profile, so nothing happens.
    """
    from django.utils import timezone

    member = registration.member
    user = getattr(member, "user", None) if member is not None else None
    profile = getattr(user, "profile", None) if user is not None else None
    if profile is None or profile.subscribed_to_mailchimp_at is not None:
        return
    profile.subscribed_to_mailchimp_at = timezone.now()
    profile.save(update_fields=["subscribed_to_mailchimp_at"])
