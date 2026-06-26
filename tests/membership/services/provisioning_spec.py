"""BDD specs for the member-user provisioning service.

The single source of truth for the "every member is a user" invariant. These specs
prove it is idempotent, silent (Risk R1 — ``mail.outbox`` stays empty), preserves
status (Review fix #1), suppresses the User signal so no duplicate Member is minted
(Review fix #3), and handles the duplicate-email + long-username edge cases gracefully
(Review fixes #2 / #4).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.db.models.signals import post_save
from factory.django import mute_signals

from membership.models import Member, MemberEmail, MembershipPlan
from membership.services.provisioning import provision_user_for_member
from tests.membership.factories import MemberEmailFactory, MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def describe_provision_user_for_member():
    def it_creates_a_user_with_a_verified_primary_email(mailoutbox):
        member = MemberFactory(_pre_signup_email="newbie@example.com")

        user = provision_user_for_member(member)

        assert user is not None
        member.refresh_from_db()
        assert member.user_id == user.pk
        assert user.email == "newbie@example.com"
        assert not user.has_usable_password()
        primary = EmailAddress.objects.get(user=user, primary=True)
        assert primary.email == "newbie@example.com"
        assert primary.verified is True

    def it_sends_no_email(mailoutbox):
        member = MemberFactory(_pre_signup_email="silent@example.com")

        provision_user_for_member(member)

        assert mailoutbox == []

    def it_is_idempotent_and_returns_the_existing_user_when_already_linked():
        member = MemberFactory(_pre_signup_email="already@example.com")
        first = provision_user_for_member(member)

        second = provision_user_for_member(member)

        assert second == first
        assert User.objects.count() == 1
        assert Member.objects.count() == 1

    def it_returns_none_and_provisions_nothing_for_a_blank_email():
        member = MemberFactory(_pre_signup_email="")

        result = provision_user_for_member(member)

        assert result is None
        member.refresh_from_db()
        assert member.user_id is None
        assert User.objects.count() == 0

    def it_lowercases_the_email_for_the_account():
        member = MemberFactory(_pre_signup_email="MixedCase@Example.com")

        user = provision_user_for_member(member)

        assert user.email == "mixedcase@example.com"
        assert EmailAddress.objects.filter(user=user, email="mixedcase@example.com", primary=True).exists()

    def it_promotes_staged_member_emails_into_allauth(mailoutbox):
        member = MemberFactory(_pre_signup_email="primary@example.com")
        MemberEmailFactory(member=member, email="alias@example.com")

        user = provision_user_for_member(member)

        # The alias is promoted (verified, non-primary) and the staging row is gone.
        assert EmailAddress.objects.filter(user=user, email="alias@example.com", verified=True, primary=False).exists()
        assert not MemberEmail.objects.filter(member=member).exists()
        assert mailoutbox == []

    def describe_status_preservation():
        # Review fix #1: the link path must NOT flip a non-ACTIVE member to ACTIVE.
        @pytest.mark.parametrize("status", [Member.Status.FORMER, Member.Status.SUSPENDED])
        def it_preserves_the_existing_status(status):
            member = MemberFactory(_pre_signup_email="kept@example.com", status=status)

            provision_user_for_member(member)

            member.refresh_from_db()
            assert member.status == status

    def describe_signal_suppression():
        # Review fix #3: while provisioning runs, ensure_user_has_member is a complete
        # no-op, so exactly one User + one Member + one primary EmailAddress result.
        def it_yields_exactly_one_user_and_member_with_a_spare_plan_present():
            # A spare plan exists (on top of the migration-seeded one), so the User
            # signal's create branch WOULD succeed if it ran — proving suppression is
            # what keeps a second Member from appearing.
            MembershipPlanFactory()  # a spare the unsuppressed create-branch could use
            member = MemberFactory(_pre_signup_email="one@example.com")

            provision_user_for_member(member)

            assert User.objects.count() == 1
            assert Member.objects.count() == 1
            assert EmailAddress.objects.filter(primary=True).count() == 1

        def it_yields_exactly_one_user_and_member_when_plans_are_pruned():
            # Remove every spare plan, keeping only the member's own (PROTECTed) FK.
            member = MemberFactory(_pre_signup_email="noplan@example.com")
            MembershipPlan.objects.exclude(pk=member.membership_plan_id).delete()

            provision_user_for_member(member)

            assert User.objects.count() == 1
            assert Member.objects.filter(_pre_signup_email="noplan@example.com").count() == 1

    def describe_existing_user():
        def it_links_an_existing_non_member_user_by_email():
            with mute_signals(post_save):
                existing = User.objects.create_user(username="prior@example.com", email="prior@example.com")
            member = MemberFactory(_pre_signup_email="prior@example.com")

            user = provision_user_for_member(member)

            assert user == existing
            member.refresh_from_db()
            assert member.user_id == existing.pk
            assert User.objects.count() == 1

        def it_makes_an_existing_unverified_address_the_verified_primary():
            with mute_signals(post_save):
                existing = User.objects.create_user(username="up@example.com", email="up@example.com")
            EmailAddress.objects.create(user=existing, email="up@example.com", verified=False, primary=False)
            member = MemberFactory(_pre_signup_email="up@example.com")

            provision_user_for_member(member)

            ea = EmailAddress.objects.get(user=existing, email="up@example.com")
            assert ea.primary is True
            assert ea.verified is True

    def describe_duplicate_email():
        # Review fix #2: two userless members share an email; the second must not steal
        # the first's User (a OneToOne collision) — it skips gracefully (returns None).
        def it_refuses_to_claim_a_user_already_owned_by_another_member():
            first = MemberFactory(_pre_signup_email="dup@example.com")
            second = MemberFactory(_pre_signup_email="dup@example.com")
            provision_user_for_member(first)

            result = provision_user_for_member(second)

            assert result is None
            second.refresh_from_db()
            assert second.user_id is None
            assert User.objects.count() == 1

    def describe_unverified():
        def it_creates_an_unverified_primary_when_verified_is_false():
            member = MemberFactory(_pre_signup_email="unverified@example.com")

            user = provision_user_for_member(member, verified=False)

            primary = EmailAddress.objects.get(user=user, primary=True)
            assert primary.verified is False

        def it_reuses_an_existing_unverified_primary_when_verified_is_false():
            # The existing-address branch of the verified=False path: no duplicate
            # EmailAddress is created, and the address stays unverified.
            with mute_signals(post_save):
                existing = User.objects.create_user(username="reuse@example.com", email="reuse@example.com")
            EmailAddress.objects.create(user=existing, email="reuse@example.com", verified=False, primary=True)
            member = MemberFactory(_pre_signup_email="reuse@example.com")

            provision_user_for_member(member, verified=False)

            addresses = EmailAddress.objects.filter(user=existing, email="reuse@example.com")
            assert addresses.count() == 1
            assert addresses.first().verified is False

    def describe_robustness():
        def it_truncates_an_over_long_username_to_fit_the_column():
            long_email = ("a" * 150) + "@example.com"
            member = MemberFactory(_pre_signup_email=long_email)

            user = provision_user_for_member(member)

            assert user is not None
            assert len(user.username) <= 150
            assert user.email == long_email.lower()

        def it_skips_and_returns_none_when_the_user_create_fails(mailoutbox):
            member = MemberFactory(_pre_signup_email="boom@example.com")

            with patch.object(User.objects, "create_user", side_effect=IntegrityError("dup")):
                result = provision_user_for_member(member)

            assert result is None
            member.refresh_from_db()
            assert member.user_id is None
            assert mailoutbox == []
