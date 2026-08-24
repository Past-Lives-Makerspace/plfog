"""BDD specs for the self-service account-deletion service.

ANONYMIZE + LOCK, never a hard delete. These specs prove the routine scrubs every
PII field, frees the email AND username so the address can be re-invited, deactivates
the linked User, strips every elevated authority, clears device/notification state,
is idempotent, wraps everything in one transaction, and leaves the finance-owned and
audit rows (billing.Tab, ClassOffering.instructor) untouched.
"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from core.models import (
    FcmDevice,
    Notification,
    NotificationPreference,
    PushSubscription,
    SiteActivity,
    UserProfile,
)
from membership.models import AdminCapability, GuildStaffMembership, Member, MemberEmail
from membership.services.account_deletion import delete_own_account
from membership.services.provisioning import provision_user_for_member
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberEmailFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db


def _png_upload() -> SimpleUploadedFile:
    """A genuine (tiny) PNG the ImageField accepts, for profile-photo tests."""
    buf = BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, "PNG")
    return SimpleUploadedFile("me.png", buf.getvalue(), content_type="image/png")


def _linked_member(**overrides) -> Member:
    """A fully-populated Member with a linked, active User (via the provisioning service)."""
    defaults = dict(
        full_legal_name="Jane Doe",
        preferred_name="Janey",
        phone="555-1111",
        discord_handle="janey#1",
        discord_user_id="123456789",
        discord_linked_at=timezone.now(),
        pronouns=Member.Pronouns.SHE_HER,
        about_me="I make things.",
        billing_name="Jane Q. Doe",
        emergency_contact_name="John Doe",
        emergency_contact_phone="555-2222",
        emergency_contact_relationship="spouse",
        instructor_bio="Teaches pottery.",
        commission_note="Open for pottery commissions.",
        open_for_commissions=True,
        can_self_approve_discounts=True,
    )
    defaults.update(overrides)
    member = MemberFactory(**defaults)
    provision_user_for_member(member)
    return member


def describe_delete_own_account():
    def it_raises_when_the_member_has_no_linked_user():
        member = MemberFactory()

        with pytest.raises(ValueError, match="no linked User"):
            delete_own_account(member)

    def describe_idempotency():
        def it_is_a_silent_noop_when_already_deleted():
            member = _linked_member()
            delete_own_account(member)
            first_deleted_at = member.deleted_at

            delete_own_account(member)

            member.refresh_from_db()
            assert member.deleted_at == first_deleted_at
            assert SiteActivity.objects.filter(kind=SiteActivity.Kind.ACCOUNT_DELETED).count() == 1

    def it_deactivates_the_linked_user():
        member = _linked_member()

        delete_own_account(member)

        member.user.refresh_from_db()
        assert member.user.is_active is False

    def it_frees_the_users_email_and_username():
        member = _linked_member()
        user = member.user
        old_email, old_username = user.email, user.username

        delete_own_account(member)

        user.refresh_from_db()
        placeholder = f"deleted-user-{user.pk}@deleted.pastlives.invalid"
        assert user.email == placeholder
        assert user.username == placeholder
        assert not User.objects.filter(email=old_email).exists()
        assert not User.objects.filter(username=old_username).exists()

    def describe_pii_scrub():
        @pytest.mark.parametrize(
            "field",
            [
                "preferred_name",
                "_pre_signup_email",
                "phone",
                "discord_handle",
                "discord_user_id",
                "pronouns",
                "about_me",
                "billing_name",
                "emergency_contact_name",
                "emergency_contact_phone",
                "emergency_contact_relationship",
                "instructor_bio",
                "commission_note",
            ],
        )
        def it_blanks_each_free_text_field(field):
            member = _linked_member()

            delete_own_account(member)

            member.refresh_from_db()
            assert getattr(member, field) == ""

        def it_replaces_the_legal_name_with_a_placeholder():
            member = _linked_member()

            delete_own_account(member)

            member.refresh_from_db()
            assert member.full_legal_name == "Deleted Member"

        def it_clears_the_discord_link_timestamp():
            member = _linked_member()

            delete_own_account(member)

            member.refresh_from_db()
            assert member.discord_linked_at is None

        def it_turns_off_open_for_commissions():
            member = _linked_member()

            delete_own_account(member)

            member.refresh_from_db()
            assert member.open_for_commissions is False

    def describe_profile_photo():
        def it_deletes_the_photo_file_and_clears_the_field():
            member = _linked_member(profile_photo=_png_upload())
            storage = member.profile_photo.storage
            path = member.profile_photo.name
            assert storage.exists(path)

            delete_own_account(member)

            member.refresh_from_db()
            assert not member.profile_photo
            assert not storage.exists(path)

        def it_does_not_raise_when_there_is_no_photo():
            member = _linked_member()

            delete_own_account(member)

            member.refresh_from_db()
            assert not member.profile_photo

    def it_sets_status_former_and_stamps_deleted_at():
        member = _linked_member()

        delete_own_account(member)

        member.refresh_from_db()
        assert member.status == Member.Status.FORMER
        assert member.deleted_at is not None

    def it_hides_even_a_force_listed_member_from_the_directory():
        member = _linked_member(fog_role=Member.FogRole.ADMIN, instructor_slug="jane-doe")
        GuildFactory(guild_lead=member)
        assert member.must_be_listed_in_directory is True

        delete_own_account(member)

        member.refresh_from_db()
        assert member.hide_from_directory is True
        assert member.must_be_listed_in_directory is False

    def it_resets_fog_role_and_strips_staff_and_superuser():
        member = _linked_member(fog_role=Member.FogRole.ADMIN)
        member.sync_user_permissions()
        assert member.user.is_staff and member.user.is_superuser

        delete_own_account(member)

        member.refresh_from_db()
        member.user.refresh_from_db()
        assert member.fog_role == Member.FogRole.MEMBER
        assert member.user.is_staff is False
        assert member.user.is_superuser is False

    def it_resets_can_self_approve_discounts():
        member = _linked_member()

        delete_own_account(member)

        member.refresh_from_db()
        assert member.can_self_approve_discounts is False

    def it_deletes_every_allauth_email_address():
        member = _linked_member()
        EmailAddress.objects.create(user=member.user, email="alias@example.com", verified=True, primary=False)

        delete_own_account(member)

        assert not EmailAddress.objects.filter(user=member.user).exists()

    def it_deletes_every_member_email_alias():
        member = _linked_member()
        MemberEmailFactory(member=member, email="staged@example.com")

        delete_own_account(member)

        assert not MemberEmail.objects.filter(member=member).exists()

    def it_clears_push_fcm_notifications_and_preferences():
        member = _linked_member()
        user = member.user
        PushSubscription.objects.create(user=user, endpoint="https://push/1", p256dh="k", auth="a")
        FcmDevice.objects.create(user=user, token="fcm-token-1")
        Notification.objects.create(user=user, trigger="t", title="Hi", body="there")
        NotificationPreference.objects.create(user=user, event_key="e", channel="email", enabled=True)

        delete_own_account(member)

        assert not PushSubscription.objects.filter(user=user).exists()
        assert not FcmDevice.objects.filter(user=user).exists()
        assert not Notification.objects.filter(user=user).exists()
        assert not NotificationPreference.objects.filter(user=user).exists()

    def describe_user_profile():
        def it_clears_the_user_profile_pii():
            member = _linked_member()
            UserProfile.objects.create(
                user=member.user,
                preferred_name="Janey",
                pronouns="she/her",
                phone="555-1111",
                first_attendance_status=UserProfile.FirstAttendance.FIRST_TIME,
            )

            delete_own_account(member)

            profile = UserProfile.objects.get(user=member.user)
            assert profile.preferred_name == ""
            assert profile.pronouns == ""
            assert profile.phone == ""
            assert profile.first_attendance_status == ""

        def it_does_not_raise_when_there_is_no_user_profile():
            member = _linked_member()

            delete_own_account(member)

            assert not UserProfile.objects.filter(user=member.user).exists()

    def it_nulls_the_guild_lead_fk_when_the_member_led_a_guild():
        member = _linked_member()
        guild = GuildFactory(guild_lead=member)

        delete_own_account(member)

        guild.refresh_from_db()
        assert guild.guild_lead_id is None

    def it_removes_guild_staff_memberships():
        member = _linked_member()
        GuildStaffMembershipFactory(member=member)

        delete_own_account(member)

        assert not GuildStaffMembership.objects.filter(member=member).exists()

    def it_revokes_every_admin_capability():
        member = _linked_member()
        member.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        member.admin_capabilities.create(capability=AdminCapability.Capability.SPACE_APPROVER)

        delete_own_account(member)

        assert member.admin_capabilities.count() == 0

    def it_keeps_the_member_and_user_rows():
        member = _linked_member()
        member_pk, user_pk = member.pk, member.user.pk

        delete_own_account(member)

        assert Member.objects.filter(pk=member_pk).exists()
        assert User.objects.filter(pk=user_pk).exists()

    def it_leaves_the_billing_tab_untouched():
        from tests.billing.factories import TabFactory

        member = _linked_member()
        tab = TabFactory(member=member)

        delete_own_account(member)

        from billing.models import Tab

        assert Tab.objects.filter(pk=tab.pk).exists()

    def it_leaves_the_class_offering_instructor_pointed_at_the_member():
        from classes.models import Category, ClassOffering

        member = _linked_member(instructor_slug="jane-doe")
        category = Category.objects.create(name="Pottery", slug="pottery")
        offering = ClassOffering.objects.create(
            title="Intro to Pottery", slug="intro-pottery", category=category, instructor=member, price_cents=5000
        )

        delete_own_account(member)

        offering.refresh_from_db()
        assert offering.instructor_id == member.pk

    def it_logs_an_account_deleted_activity():
        member = _linked_member()

        delete_own_account(member)

        activity = SiteActivity.objects.get(kind=SiteActivity.Kind.ACCOUNT_DELETED)
        assert activity.actor_id == member.user.pk
        assert activity.target == member

    def describe_atomicity():
        def it_rolls_everything_back_when_a_later_step_fails():
            member = _linked_member()

            with patch("core.models.SiteActivity.log", side_effect=RuntimeError("boom")):
                with pytest.raises(RuntimeError, match="boom"):
                    delete_own_account(member)

            member.refresh_from_db()
            member.user.refresh_from_db()
            assert member.deleted_at is None
            assert member.full_legal_name == "Jane Doe"
            assert member.user.is_active is True
            assert EmailAddress.objects.filter(user=member.user).exists()
