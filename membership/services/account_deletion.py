"""Self-service account deletion: anonymize a member's PII and lock their login.

ANONYMIZE + LOCK, never a hard delete. Member and User rows are always kept -- several
PROTECT relations (billing.Tab, classes.ClassOffering.instructor) depend on the Member
row continuing to exist, and keeping an inactive User with a freed email/username is
what defeats every login-code auto-resurrection branch in AutoCreateUserLoginCodeForm.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

if TYPE_CHECKING:
    from membership.models import Member

logger = logging.getLogger(__name__)


def delete_own_account(member: "Member") -> None:
    """Anonymize this member's PII in place and deactivate their linked User.

    Idempotent: a second call on an already-deleted member is a silent no-op. Wrapped in
    one atomic transaction so a failure partway through never leaves a half-scrubbed member.

    Args:
        member: The member deleting their own account.

    Raises:
        ValueError: If member.user is not set (self-service always starts authenticated).
    """
    if member.deleted_at is not None:
        logger.info("delete_own_account: member pk=%s already deleted, no-op.", member.pk)
        return
    if member.user_id is None:
        raise ValueError(f"Member {member.pk} has no linked User; cannot self-delete.")

    from allauth.account.models import EmailAddress

    from core.models import (
        FcmDevice,
        Notification,
        NotificationPreference,
        PushSubscription,
        SiteActivity,
        UserProfile,
    )
    from membership.models import Guild, GuildStaffMembership, MemberContact, MemberEmail

    user = member.user
    placeholder = f"deleted-user-{user.pk}@deleted.pastlives.invalid"

    with transaction.atomic():
        # Free every email store so the address can be re-invited later.
        EmailAddress.objects.filter(user=user).delete()
        MemberEmail.objects.filter(member=member).delete()

        # Remove the member's website/social/phone contact methods (free-text PII).
        MemberContact.objects.filter(member=member).delete()

        # Lock the User out and free username/email (both unique).
        user.is_active = False
        user.username = placeholder
        user.email = placeholder
        user.save(update_fields=["is_active", "username", "email"])

        # Strip elevated authority (is_active=False is the real gate; this is defense in depth).
        Guild.objects.filter(guild_lead=member).update(guild_lead=None)
        GuildStaffMembership.objects.filter(member=member).delete()
        member.sync_admin_capabilities([])

        # Clear device/notification state (FK is to User).
        PushSubscription.objects.filter(user=user).delete()
        FcmDevice.objects.filter(user=user).delete()
        Notification.objects.filter(user=user).delete()
        NotificationPreference.objects.filter(user=user).delete()
        UserProfile.objects.filter(user=user).update(
            preferred_name="",
            pronouns="",
            phone="",
            first_attendance_status="",
            accessibility_note="",
            custom_question_answers={},
        )

        # Scrub Member PII in place.
        if member.profile_photo:
            member.profile_photo.delete(save=False)
        member.full_legal_name = "Deleted Member"
        member.preferred_name = ""
        member._pre_signup_email = ""
        member.phone = ""
        member.discord_handle = ""
        member.discord_user_id = ""
        member.discord_linked_at = None
        member.pronouns = ""
        member.about_me = ""
        member.billing_name = ""
        member.emergency_contact_name = ""
        member.emergency_contact_phone = ""
        member.emergency_contact_relationship = ""
        member.instructor_bio = ""
        member.instructor_slug = ""
        member.instructor_oriented_at = None
        member.commission_note = ""
        member.open_for_commissions = False
        member.hide_from_directory = True
        member.fog_role = member.FogRole.MEMBER
        member.can_self_approve_discounts = False
        member.status = member.Status.FORMER
        member.deleted_at = timezone.now()
        member.save(
            update_fields=[
                "profile_photo",
                "full_legal_name",
                "preferred_name",
                "_pre_signup_email",
                "phone",
                "discord_handle",
                "discord_user_id",
                "discord_linked_at",
                "pronouns",
                "about_me",
                "billing_name",
                "emergency_contact_name",
                "emergency_contact_phone",
                "emergency_contact_relationship",
                "instructor_bio",
                "instructor_slug",
                "instructor_oriented_at",
                "commission_note",
                "open_for_commissions",
                "hide_from_directory",
                "fog_role",
                "can_self_approve_discounts",
                "status",
                "deleted_at",
            ]
        )
        member.sync_user_permissions()

        SiteActivity.log(SiteActivity.Kind.ACCOUNT_DELETED, actor=user, target=member)

    logger.warning("Account self-deleted: member pk=%s, user pk=%s.", member.pk, user.pk)
