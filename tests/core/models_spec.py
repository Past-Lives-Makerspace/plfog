"""BDD-style tests for core.models — SiteConfiguration and Invite."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.utils import timezone

from core.models import Invite, SiteActivity, SiteConfiguration, TransactionalEmailLog
from membership.models import Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory

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

    def describe_feature_switches():
        def it_defaults_tab_payments_enabled_to_true():
            config = SiteConfiguration.load()
            assert config.tab_payments_enabled is True

        def it_defaults_class_registration_enabled_to_true():
            config = SiteConfiguration.load()
            assert config.class_registration_enabled is True

        def it_sets_the_registration_off_note_default_on_a_fresh_singleton():
            config = SiteConfiguration.load()
            assert config.class_registration_disabled_note == (
                "Online registration is paused right now. Email info@pastlives.space and we'll help you sign up."
            )

        def it_defaults_help_page_enabled_to_true():
            config = SiteConfiguration.load()
            assert config.help_page_enabled is True

        def it_defaults_wiki_link_enabled_to_true():
            config = SiteConfiguration.load()
            assert config.wiki_link_enabled is True


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
        # send_invite_email now emits the ``member.invited`` event: a FORCED email to
        # the invitee's raw address, with DB-editable copy. It routes through the
        # choke-point (core.email.send → send_mail), so the underlying send_mail is
        # still the seam these characterization tests assert on.
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

        def it_emits_member_invited_as_a_forced_email(admin_user):
            invite = Invite.objects.create(email="forced@example.com", invited_by=admin_user)
            invite.send_invite_email()
            # The email is audited under the member.invited event key (one vocabulary).
            log = TransactionalEmailLog.objects.filter(trigger_kind="member.invited").first()
            assert log is not None
            assert log.to_email == "forced@example.com"

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

        def it_reuses_an_existing_invited_member(admin_user):
            from tests.membership.factories import MemberFactory

            existing = MemberFactory(_pre_signup_email="airtable@example.com", status=Member.Status.INVITED)
            with patch("core.email.send_mail"):
                invite = Invite.create_and_send(email="airtable@example.com", invited_by=admin_user)

            assert invite.member == existing
            assert Member.objects.filter(_pre_signup_email__iexact="airtable@example.com").count() == 1

        def it_sends_invite_email(admin_user):
            MembershipPlanFactory()
            Invite.create_and_send(email="send@example.com", invited_by=admin_user)

            assert TransactionalEmailLog.objects.filter(trigger_kind="member.invited").exists()

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

        def it_logs_member_invited_site_activity(admin_user):
            MembershipPlanFactory()
            with patch("core.email.send_mail"):
                Invite.create_and_send(email="invited@example.com", invited_by=admin_user)

            row = SiteActivity.objects.filter(kind=SiteActivity.Kind.MEMBER_INVITED).first()
            assert row is not None
            assert row.actor == admin_user
            assert row.payload["email"] == "invited@example.com"

    def describe_mark_accepted_site_activity():
        def it_logs_invite_accepted_site_activity(admin_user):
            MembershipPlanFactory()
            with patch("core.email.send_mail"):
                invite = Invite.create_and_send(email="accepted@example.com", invited_by=admin_user)
            SiteActivity.objects.all().delete()
            invite.mark_accepted()

            row = SiteActivity.objects.filter(kind=SiteActivity.Kind.INVITE_ACCEPTED).first()
            assert row is not None
            assert row.target == invite.member

    def describe_sent_at():
        def it_returns_last_sent_at_when_set(admin_user):
            invite = Invite.objects.create(email="s@example.com", invited_by=admin_user)
            stamp = timezone.now() - timedelta(days=2)
            invite.last_sent_at = stamp
            assert invite.sent_at == stamp

        def it_falls_back_to_created_at_when_never_sent(admin_user):
            invite = Invite.objects.create(email="s@example.com", invited_by=admin_user)
            assert invite.last_sent_at is None
            assert invite.sent_at == invite.created_at

    def describe_is_expired():
        def it_is_false_when_accepted(admin_user):
            invite = Invite.objects.create(email="e@example.com", invited_by=admin_user)
            Invite.objects.filter(pk=invite.pk).update(
                accepted_at=timezone.now(),
                created_at=timezone.now() - timedelta(days=60),
            )
            invite.refresh_from_db()
            assert invite.is_expired is False

        def it_is_false_when_recently_sent(admin_user):
            invite = Invite.objects.create(email="e@example.com", invited_by=admin_user)
            invite.last_sent_at = timezone.now() - timedelta(days=3)
            assert invite.is_expired is False

        def it_is_true_when_un_accepted_and_aged(admin_user):
            invite = Invite.objects.create(email="e@example.com", invited_by=admin_user)
            Invite.objects.filter(pk=invite.pk).update(created_at=timezone.now() - timedelta(days=30))
            invite.refresh_from_db()
            assert invite.is_expired is True

        def it_resets_when_resent_on_an_aged_invite(admin_user):
            # The display-honesty case: created long ago, but last_sent recent → NOT expired.
            invite = Invite.objects.create(email="e@example.com", invited_by=admin_user)
            Invite.objects.filter(pk=invite.pk).update(created_at=timezone.now() - timedelta(days=60))
            invite.refresh_from_db()
            invite.last_sent_at = timezone.now()
            assert invite.is_expired is False

    def describe_status():
        def it_is_accepted_when_accepted_at_set(admin_user):
            invite = Invite.objects.create(email="st@example.com", invited_by=admin_user)
            invite.accepted_at = timezone.now()
            assert invite.status == Invite.Status.ACCEPTED
            assert invite.status_label == "Accepted"

        def it_is_expired_past_the_cutoff(admin_user):
            invite = Invite.objects.create(email="st@example.com", invited_by=admin_user)
            Invite.objects.filter(pk=invite.pk).update(created_at=timezone.now() - timedelta(days=30))
            invite.refresh_from_db()
            assert invite.status == Invite.Status.EXPIRED
            assert invite.status_label == "Expired"

        def it_is_pending_within_the_window(admin_user):
            invite = Invite.objects.create(email="st@example.com", invited_by=admin_user)
            assert invite.status == Invite.Status.PENDING
            assert invite.status_label == "Pending"

    def describe_send_invite_email_stamps_last_sent_at():
        def it_sets_last_sent_at_on_send(admin_user):
            invite = Invite.objects.create(email="stamp@example.com", invited_by=admin_user)
            with patch("core.email.send_mail"):
                invite.send_invite_email()
            invite.refresh_from_db()
            assert invite.last_sent_at is not None

        def it_moves_last_sent_at_forward_on_resend(admin_user):
            invite = Invite.objects.create(email="stamp@example.com", invited_by=admin_user)
            old = timezone.now() - timedelta(days=20)
            Invite.objects.filter(pk=invite.pk).update(last_sent_at=old)
            invite.refresh_from_db()
            with patch("core.email.send_mail"):
                invite.send_invite_email()
            invite.refresh_from_db()
            assert invite.last_sent_at > old

        def it_flips_expired_to_pending_after_resend(admin_user):
            invite = Invite.objects.create(email="stamp@example.com", invited_by=admin_user)
            Invite.objects.filter(pk=invite.pk).update(created_at=timezone.now() - timedelta(days=30))
            invite.refresh_from_db()
            assert invite.status == Invite.Status.EXPIRED
            with patch("core.email.send_mail"):
                invite.send_invite_email()
            invite.refresh_from_db()
            assert invite.status == Invite.Status.PENDING

    def describe_InviteManager():
        def it_outstanding_excludes_accepted(admin_user):
            pending = Invite.objects.create(email="p@example.com", invited_by=admin_user)
            accepted = Invite.objects.create(email="a@example.com", invited_by=admin_user)
            accepted.mark_accepted()
            outstanding = list(Invite.objects.outstanding())
            assert pending in outstanding
            assert accepted not in outstanding

        def it_pending_uses_the_coalesced_send_time(admin_user):
            # Old created_at but recent last_sent_at → lands in pending(), not expired().
            invite = Invite.objects.create(email="resent@example.com", invited_by=admin_user)
            Invite.objects.filter(pk=invite.pk).update(
                created_at=timezone.now() - timedelta(days=60),
                last_sent_at=timezone.now(),
            )
            assert invite in Invite.objects.pending()
            assert invite not in Invite.objects.expired()

        def it_expired_partitions_aged_invites(admin_user):
            invite = Invite.objects.create(email="old@example.com", invited_by=admin_user)
            Invite.objects.filter(pk=invite.pk).update(created_at=timezone.now() - timedelta(days=30))
            assert invite in Invite.objects.expired()
            assert invite not in Invite.objects.pending()

        def it_pending_and_expired_exclude_accepted(admin_user):
            accepted = Invite.objects.create(email="acc@example.com", invited_by=admin_user)
            accepted.mark_accepted()
            assert accepted not in Invite.objects.pending()
            assert accepted not in Invite.objects.expired()

    def describe_clear_expired():
        def it_revokes_every_expired_invite_and_returns_the_count(admin_user):
            expired_a = Invite.objects.create(email="ea@example.com", invited_by=admin_user)
            expired_b = Invite.objects.create(email="eb@example.com", invited_by=admin_user)
            Invite.objects.filter(pk__in=[expired_a.pk, expired_b.pk]).update(
                created_at=timezone.now() - timedelta(days=30)
            )
            fresh = Invite.objects.create(email="fresh@example.com", invited_by=admin_user)

            cleared = Invite.objects.clear_expired()

            assert cleared == 2
            assert not Invite.objects.filter(pk=expired_a.pk).exists()
            assert not Invite.objects.filter(pk=expired_b.pk).exists()
            assert Invite.objects.filter(pk=fresh.pk).exists()

        def it_returns_zero_when_nothing_is_expired(admin_user):
            Invite.objects.create(email="fresh@example.com", invited_by=admin_user)
            assert Invite.objects.clear_expired() == 0

        def it_leaves_accepted_invites_untouched(admin_user):
            accepted = Invite.objects.create(email="acc@example.com", invited_by=admin_user)
            Invite.objects.filter(pk=accepted.pk).update(created_at=timezone.now() - timedelta(days=30))
            accepted.refresh_from_db()
            accepted.mark_accepted()
            assert Invite.objects.clear_expired() == 0
            assert Invite.objects.filter(pk=accepted.pk).exists()

        def it_deletes_the_placeholder_member_and_logs_activity(admin_user):
            MembershipPlanFactory()
            with patch("core.email.send_mail"):
                invite = Invite.create_and_send(email="ghost@example.com", invited_by=admin_user)
            aged = timezone.now() - timedelta(days=30)
            Invite.objects.filter(pk=invite.pk).update(created_at=aged, last_sent_at=aged)
            member_pk = invite.member_id
            SiteActivity.objects.all().delete()

            cleared = Invite.objects.clear_expired()

            assert cleared == 1
            assert not Member.objects.filter(pk=member_pk).exists()
            assert SiteActivity.objects.filter(kind=SiteActivity.Kind.MEMBER_INVITE_REVOKED).exists()

    def describe_for_management_panel():
        def it_includes_un_accepted(admin_user):
            invite = Invite.objects.create(email="u@example.com", invited_by=admin_user)
            assert invite in Invite.objects.for_management_panel()

        def it_includes_recently_accepted(admin_user):
            invite = Invite.objects.create(email="r@example.com", invited_by=admin_user)
            Invite.objects.filter(pk=invite.pk).update(accepted_at=timezone.now() - timedelta(days=10))
            assert invite in Invite.objects.for_management_panel()

        def it_excludes_accepted_older_than_30_days(admin_user):
            invite = Invite.objects.create(email="o@example.com", invited_by=admin_user)
            Invite.objects.filter(pk=invite.pk).update(accepted_at=timezone.now() - timedelta(days=45))
            assert invite not in Invite.objects.for_management_panel()

    def describe_revoke():
        def it_deletes_the_invite(admin_user):
            MembershipPlanFactory()
            with patch("core.email.send_mail"):
                invite = Invite.create_and_send(email="rev@example.com", invited_by=admin_user)
            pk = invite.pk
            invite.revoke()
            assert not Invite.objects.filter(pk=pk).exists()

        def it_deletes_a_bare_placeholder_member(admin_user):
            MembershipPlanFactory()
            with patch("core.email.send_mail"):
                invite = Invite.create_and_send(email="rev@example.com", invited_by=admin_user)
            member_pk = invite.member.pk
            invite.revoke()
            assert not Member.objects.filter(pk=member_pk).exists()

        def it_does_not_delete_a_reused_airtable_stub(admin_user):
            stub = MemberFactory(
                _pre_signup_email="air@example.com",
                status=Member.Status.INVITED,
                airtable_record_id="recABC123",
            )
            with patch("core.email.send_mail"):
                invite = Invite.create_and_send(email="air@example.com", invited_by=admin_user)
            assert invite.member == stub
            invite.revoke()
            assert Member.objects.filter(pk=stub.pk).exists()

        def it_does_not_delete_a_member_with_a_linked_user(admin_user):
            MembershipPlanFactory()
            user = User.objects.create_user(username="linked", email="linked@example.com", password="p")
            member = user.member
            member.status = Member.Status.INVITED
            member.save()
            invite = Invite.objects.create(email="linked@example.com", invited_by=admin_user, member=member)
            invite.revoke()
            assert Member.objects.filter(pk=member.pk).exists()

        def it_raises_on_an_already_accepted_invite(admin_user):
            invite = Invite.objects.create(email="done@example.com", invited_by=admin_user)
            invite.mark_accepted()
            with pytest.raises(ValueError, match="already been accepted"):
                invite.revoke()

        def it_logs_member_invite_revoked_activity(admin_user):
            invite = Invite.objects.create(email="log@example.com", invited_by=admin_user)
            invite.revoke()
            row = SiteActivity.objects.filter(kind=SiteActivity.Kind.MEMBER_INVITE_REVOKED).first()
            assert row is not None
            assert row.payload["email"] == "log@example.com"
