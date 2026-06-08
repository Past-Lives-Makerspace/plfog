"""BDD-style tests for core.models — SiteConfiguration and Invite."""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError

from core.models import Invite, SiteConfiguration, TransactionalEmailLog
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db


def describe_SiteConfiguration():
    def it_creates_with_invite_only_default():
        config = SiteConfiguration.load()
        assert config.registration_mode == SiteConfiguration.RegistrationMode.INVITE_ONLY

    def it_enforces_singleton_by_forcing_pk_1():
        config1 = SiteConfiguration.load()
        config1.registration_mode = SiteConfiguration.RegistrationMode.OPEN
        config1.save()

        config2 = SiteConfiguration(registration_mode=SiteConfiguration.RegistrationMode.INVITE_ONLY)
        config2.save()

        assert SiteConfiguration.objects.count() == 1
        config2.refresh_from_db()
        assert config2.registration_mode == SiteConfiguration.RegistrationMode.INVITE_ONLY

    def it_returns_existing_instance_from_load():
        config = SiteConfiguration.load()
        config.registration_mode = SiteConfiguration.RegistrationMode.OPEN
        config.save()

        loaded = SiteConfiguration.load()
        assert loaded.registration_mode == SiteConfiguration.RegistrationMode.OPEN

    def it_has_str_representation():
        config = SiteConfiguration.load()
        assert str(config) == "Site Settings"


def describe_Invite():
    @pytest.fixture()
    def admin_user():
        return User.objects.create_user(username="admin", email="admin@example.com", password="testpass")

    def it_creates_with_pending_status(admin_user):
        invite = Invite.objects.create(email="new@example.com", invited_by=admin_user)
        assert invite.is_pending is True
        assert invite.accepted_at is None

    def it_has_str_representation_when_pending(admin_user):
        invite = Invite.objects.create(email="new@example.com", invited_by=admin_user)
        assert str(invite) == "Invite for new@example.com (pending)"

    def it_has_str_representation_when_accepted(admin_user):
        invite = Invite.objects.create(email="new@example.com", invited_by=admin_user)
        invite.mark_accepted()
        assert str(invite) == "Invite for new@example.com (accepted)"

    def describe_mark_accepted():
        def it_sets_accepted_at_timestamp(admin_user):
            invite = Invite.objects.create(email="new@example.com", invited_by=admin_user)
            invite.mark_accepted()
            invite.refresh_from_db()
            assert invite.accepted_at is not None
            assert invite.is_pending is False

    def describe_is_pending():
        def it_returns_true_when_accepted_at_is_none(admin_user):
            invite = Invite.objects.create(email="new@example.com", invited_by=admin_user)
            assert invite.is_pending is True

        def it_returns_false_when_accepted(admin_user):
            invite = Invite.objects.create(email="new@example.com", invited_by=admin_user)
            invite.mark_accepted()
            assert invite.is_pending is False

    def describe_unique_email():
        def it_enforces_unique_email_constraint(admin_user):
            Invite.objects.create(email="dup@example.com", invited_by=admin_user)
            with pytest.raises(IntegrityError):
                Invite.objects.create(email="dup@example.com", invited_by=admin_user)

    def describe_send_invite_email():
        def it_sends_plaintext_email(admin_user):
            invite = Invite.objects.create(email="new@example.com", invited_by=admin_user)
            with patch("core.email.send_mail") as mock_send:
                invite.send_invite_email()

                mock_send.assert_called_once()
                call_kwargs = mock_send.call_args
                assert call_kwargs[1]["recipient_list"] == ["new@example.com"]
                assert "new%40example.com" in call_kwargs[1]["message"]
                assert "/accounts/signup/" in call_kwargs[1]["message"]
                assert call_kwargs[1]["subject"] == "You're invited to Past Lives Makerspace"

        def it_includes_signup_url_with_email(admin_user, settings):
            settings.DEBUG = True
            invite = Invite.objects.create(email="test@example.com", invited_by=admin_user)
            with patch("core.email.send_mail") as mock_send:
                invite.send_invite_email()

                message = mock_send.call_args[1]["message"]
                assert "/accounts/signup/?email=test%40example.com" in message

        def it_url_encodes_plus_addressing_in_email(admin_user, settings):
            settings.DEBUG = True
            invite = Invite.objects.create(email="user+tag@example.com", invited_by=admin_user)
            with patch("core.email.send_mail") as mock_send:
                invite.send_invite_email()

                message = mock_send.call_args[1]["message"]
                assert "user%2Btag%40example.com" in message
                assert "user+tag@example.com" not in message.split("?")[1]

    def describe_create_and_send():
        def it_creates_invite_and_member_placeholder(admin_user):
            MembershipPlanFactory()
            with patch("core.email.send_mail"):
                invite = Invite.create_and_send(email="fresh@example.com", invited_by=admin_user)

            assert invite.email == "fresh@example.com"
            assert invite.invited_by == admin_user
            assert invite.member is not None
            assert invite.member.status == Member.Status.INVITED
            assert invite.member._pre_signup_email == "fresh@example.com"

        def it_sends_invite_email(admin_user):
            MembershipPlanFactory()
            Invite.create_and_send(email="send@example.com", invited_by=admin_user)

            assert TransactionalEmailLog.objects.filter(trigger_kind="core.invite").exists()

        def it_raises_when_active_member_exists(admin_user):
            from tests.membership.factories import MemberFactory

            MemberFactory(_pre_signup_email="exists@example.com", status=Member.Status.ACTIVE)
            with pytest.raises(ValueError, match="already exists"):
                Invite.create_and_send(email="exists@example.com", invited_by=admin_user)

        def it_raises_when_pending_invite_exists(admin_user):
            MembershipPlanFactory()
            with patch("core.email.send_mail"):
                Invite.create_and_send(email="dup@example.com", invited_by=admin_user)

            with pytest.raises(ValueError, match="pending invite"):
                Invite.create_and_send(email="dup@example.com", invited_by=admin_user)

        def it_raises_when_no_membership_plan(admin_user):
            from membership.models import Member, MembershipPlan

            Member.objects.all().delete()
            MembershipPlan.objects.all().delete()
            with pytest.raises(ValueError, match="no membership plan"):
                Invite.create_and_send(email="noplan@example.com", invited_by=admin_user)
