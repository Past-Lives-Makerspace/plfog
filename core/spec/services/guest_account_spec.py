"""BDD specs for guest account creation on confirmed registrations."""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model

from classes.factories import RegistrationFactory, _MembershipPlanFactory
from classes.models import RegistrationAnswer, RegistrationQuestion
from core.models import SiteConfiguration, UserProfile
from core.services.guest_account import ensure_account_for_registration
from membership.models import Member

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def membership_plan():
    # ``ensure_user_has_member`` needs a MembershipPlan to auto-create a Member.
    return _MembershipPlanFactory()


@pytest.fixture
def open_registration():
    site = SiteConfiguration.load()
    site.registration_mode = SiteConfiguration.RegistrationMode.OPEN
    site.save()
    return site


def describe_ensure_account_for_registration():
    def describe_opt_in_box_checked():
        def it_creates_a_user_and_member_and_links_the_registration(membership_plan, open_registration):
            reg = RegistrationFactory(email="ada@example.com", create_account=True, member=None)
            ensure_account_for_registration(reg)
            user = User.objects.get(email="ada@example.com")
            member = Member.objects.get(user=user)
            reg.refresh_from_db()
            assert reg.member_id == member.pk

        def it_creates_a_verified_primary_email_address(membership_plan, open_registration):
            reg = RegistrationFactory(email="ada@example.com", create_account=True, member=None)
            ensure_account_for_registration(reg)
            user = User.objects.get(email="ada@example.com")
            address = EmailAddress.objects.get(user=user, email__iexact="ada@example.com")
            assert address.primary is True
            assert address.verified is True

        def it_seeds_profile_pronouns_and_phone(membership_plan, open_registration):
            reg = RegistrationFactory(
                email="ada@example.com",
                create_account=True,
                member=None,
                pronouns="they/them",
                phone="503-555-0100",
            )
            ensure_account_for_registration(reg)
            user = User.objects.get(email="ada@example.com")
            profile = UserProfile.objects.get(user=user)
            assert profile.pronouns == "they/them"
            assert profile.phone == "503-555-0100"

        def it_seeds_profile_custom_answers(membership_plan, open_registration):
            question = RegistrationQuestion.objects.create(
                prompt="Experience?",
                question_type=RegistrationQuestion.QuestionType.SHORT_TEXT,
            )
            reg = RegistrationFactory(email="ada@example.com", create_account=True, member=None)
            RegistrationAnswer.objects.create(registration=reg, question=question, answer_text="Beginner")
            ensure_account_for_registration(reg)
            user = User.objects.get(email="ada@example.com")
            profile = UserProfile.objects.get(user=user)
            assert profile.custom_question_answers[str(question.pk)] == "Beginner"

        def it_stamps_the_profile_when_the_registration_already_subscribed(membership_plan, open_registration):
            # subscribe_registration runs before us and cannot stamp the profile
            # itself: at that point the guest has no account. The stamp is what
            # stops us re-asking for the newsletter opt-in on their next booking.
            reg = RegistrationFactory(
                email="ada@example.com",
                create_account=True,
                member=None,
                wants_newsletter=True,
                subscribed_to_mailchimp=True,
            )
            ensure_account_for_registration(reg)
            user = User.objects.get(email="ada@example.com")
            assert UserProfile.objects.get(user=user).subscribed_to_mailchimp_at is not None

        def it_does_not_stamp_the_profile_when_the_guest_did_not_opt_in(membership_plan, open_registration):
            reg = RegistrationFactory(
                email="ada@example.com",
                create_account=True,
                member=None,
                wants_newsletter=False,
                subscribed_to_mailchimp=False,
            )
            ensure_account_for_registration(reg)
            user = User.objects.get(email="ada@example.com")
            assert UserProfile.objects.get(user=user).subscribed_to_mailchimp_at is None

        def it_leaves_an_existing_stamp_untouched(membership_plan, open_registration):
            from datetime import timedelta

            from django.utils import timezone

            user = User.objects.create_user(username="ada@example.com", email="ada@example.com")
            original = timezone.now() - timedelta(days=30)
            UserProfile.objects.create(user=user, subscribed_to_mailchimp_at=original)
            reg = RegistrationFactory(
                email="ada@example.com",
                create_account=True,
                member=None,
                wants_newsletter=True,
                subscribed_to_mailchimp=True,
            )
            ensure_account_for_registration(reg)
            assert UserProfile.objects.get(user=user).subscribed_to_mailchimp_at == original

        def it_backfills_other_guest_registrations_sharing_the_email(membership_plan, open_registration):
            older = RegistrationFactory(email="ada@example.com", create_account=False, member=None)
            reg = RegistrationFactory(email="ada@example.com", create_account=True, member=None)
            ensure_account_for_registration(reg)
            user = User.objects.get(email="ada@example.com")
            member = Member.objects.get(user=user)
            older.refresh_from_db()
            assert older.member_id == member.pk

    def describe_opt_in_box_unchecked():
        def it_creates_no_account(membership_plan, open_registration):
            reg = RegistrationFactory(email="guest@example.com", create_account=False, member=None)
            ensure_account_for_registration(reg)
            assert not User.objects.filter(email="guest@example.com").exists()

        def it_leaves_the_registration_unlinked(membership_plan, open_registration):
            reg = RegistrationFactory(email="guest@example.com", create_account=False, member=None)
            ensure_account_for_registration(reg)
            reg.refresh_from_db()
            assert reg.member_id is None

    def describe_username_collision():
        def it_does_not_raise_and_creates_nothing_new_on_a_username_clash(membership_plan, open_registration):
            # A user already owns this username but under a *different* email, so
            # neither email lookup matches. create_user then hits the username
            # unique constraint; the service swallows it and re-resolves to None
            # rather than 500-ing or duplicating.
            User.objects.create_user(username="ada@example.com", email="different@example.com")
            reg = RegistrationFactory(email="ada@example.com", create_account=True, member=None)
            ensure_account_for_registration(reg)  # must not raise
            reg.refresh_from_db()
            assert reg.member_id is None

    def describe_email_already_has_an_account():
        def it_links_without_creating_a_duplicate(membership_plan, open_registration):
            existing = User.objects.create_user(username="ada@example.com", email="ada@example.com")
            reg = RegistrationFactory(email="ada@example.com", create_account=True, member=None)
            ensure_account_for_registration(reg)
            assert User.objects.filter(email__iexact="ada@example.com").count() == 1
            reg.refresh_from_db()
            assert reg.member.user_id == existing.pk

        def it_matches_case_insensitively(membership_plan, open_registration):
            User.objects.create_user(username="ada@example.com", email="ada@example.com")
            reg = RegistrationFactory(email="ADA@example.com", create_account=True, member=None)
            ensure_account_for_registration(reg)
            assert User.objects.filter(email__iexact="ada@example.com").count() == 1

        def it_matches_a_secondary_verified_email_address(membership_plan, open_registration):
            # The registrant typed a verified alias, not their primary email, so
            # only the EmailAddress lookup (not User.email) can find the account.
            user = User.objects.create_user(username="primary@example.com", email="primary@example.com")
            EmailAddress.objects.create(user=user, email="alias@example.com", verified=True, primary=False)
            member = Member.objects.get(user=user)
            reg = RegistrationFactory(email="alias@example.com", create_account=True, member=None)
            ensure_account_for_registration(reg)
            assert User.objects.count() == 1
            reg.refresh_from_db()
            assert reg.member_id == member.pk

    def describe_registration_already_linked_to_a_member():
        def it_links_only_and_creates_no_user(membership_plan, open_registration):
            user = User.objects.create_user(username="bea@example.com", email="bea@example.com")
            member = Member.objects.get(user=user)
            reg = RegistrationFactory(email="bea@example.com", create_account=True, member=member)
            ensure_account_for_registration(reg)
            assert User.objects.filter(email__iexact="bea@example.com").count() == 1
            reg.refresh_from_db()
            assert reg.member_id == member.pk

    def describe_invite_only_mode():
        def it_creates_no_account(membership_plan):
            site = SiteConfiguration.load()
            site.registration_mode = SiteConfiguration.RegistrationMode.INVITE_ONLY
            site.save()
            reg = RegistrationFactory(email="ada@example.com", create_account=True, member=None)
            ensure_account_for_registration(reg)
            assert not User.objects.filter(email="ada@example.com").exists()

    def describe_idempotency():
        def it_is_safe_to_call_twice(membership_plan, open_registration):
            reg = RegistrationFactory(email="ada@example.com", create_account=True, member=None)
            ensure_account_for_registration(reg)
            ensure_account_for_registration(reg)
            assert User.objects.filter(email__iexact="ada@example.com").count() == 1

    def describe_failure_path():
        def it_never_raises_when_account_creation_fails(open_registration, monkeypatch):
            # Force the inner worker to blow up; the public entrypoint must swallow it.
            import core.services.guest_account as svc

            def boom(_registration):
                raise RuntimeError("mailchimp/down/etc")

            monkeypatch.setattr(svc, "_ensure_account_for_registration", boom)
            reg = RegistrationFactory(email="ada@example.com", create_account=True, member=None)
            ensure_account_for_registration(reg)  # must not raise

        def it_links_nothing_when_the_user_has_no_member(membership_plan):
            # A user with no linked Member (e.g. the auto-create signal couldn't
            # find a MembershipPlan when it was created). The booking stays
            # unlinked but the call doesn't error.
            from core.services.guest_account import _link_registrations_to_user

            reg = RegistrationFactory(email="ada@example.com", create_account=True, member=None)
            user = User.objects.create_user(username="ada@example.com", email="ada@example.com")
            Member.objects.filter(user=user).delete()
            _link_registrations_to_user(user, "ada@example.com")
            reg.refresh_from_db()
            assert reg.member_id is None

    def describe_blank_email():
        def it_creates_no_account(membership_plan, open_registration):
            reg = RegistrationFactory(email="", create_account=True, member=None)
            ensure_account_for_registration(reg)
            assert User.objects.count() == 0


def describe_default_create_account_is_false():
    def it_defaults_to_false_on_a_plain_registration(db):
        reg = RegistrationFactory()
        assert reg.create_account is False
