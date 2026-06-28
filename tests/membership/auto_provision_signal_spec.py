"""BDD specs for the going-forward Member auto-provision signal (spec §1b).

Creating a new ACTIVE Member (the Airtable-import path) auto-provisions a linked
User — silently, idempotently, with no recursion and no duplicate Member. Non-ACTIVE
members are left alone (Review fix #1).

These specs create members via ``Member.objects.create`` so the signal actually fires
(``MemberFactory`` mutes ``post_save`` to keep its members unlinked by default).
"""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User

from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _create_member(**kwargs) -> Member:
    plan = kwargs.pop("membership_plan", None) or MembershipPlanFactory()
    defaults = {
        "full_legal_name": "Auto Person",
        "membership_plan": plan,
        "status": Member.Status.ACTIVE,
    }
    defaults.update(kwargs)
    return Member.objects.create(**defaults)


def describe_auto_provision_member_user():
    def it_provisions_one_user_for_a_new_active_member(mailoutbox):
        member = _create_member(_pre_signup_email="auto@example.com")

        member.refresh_from_db()
        assert member.user_id is not None
        assert User.objects.count() == 1
        assert Member.objects.count() == 1
        assert EmailAddress.objects.filter(user_id=member.user_id, primary=True, verified=True).count() == 1
        assert mailoutbox == []

    def it_does_not_create_a_duplicate_member_even_with_a_spare_plan(mailoutbox):
        # A spare plan means the User signal's create branch WOULD succeed if it ran;
        # suppression inside provisioning keeps exactly one Member from existing.
        MembershipPlanFactory()  # spare
        member = _create_member(_pre_signup_email="nodupe@example.com")

        assert Member.objects.count() == 1
        member.refresh_from_db()
        assert member.user_id is not None
        assert mailoutbox == []

    def it_provisions_with_only_the_required_plan(mailoutbox):
        member = _create_member(_pre_signup_email="solo@example.com")

        member.refresh_from_db()
        assert member.user_id is not None
        assert User.objects.count() == 1

    def it_does_not_re_provision_on_a_later_update(mailoutbox):
        member = _create_member(_pre_signup_email="stable@example.com")
        member.refresh_from_db()
        original_user_id = member.user_id

        member.full_legal_name = "Renamed"
        member.save(update_fields=["full_legal_name"])

        member.refresh_from_db()
        assert member.user_id == original_user_id
        assert User.objects.count() == 1

    def describe_non_active_members_are_skipped():
        @pytest.mark.parametrize(
            "status",
            [Member.Status.INVITED, Member.Status.FORMER, Member.Status.SUSPENDED],
        )
        def it_does_not_provision(status):
            member = _create_member(_pre_signup_email="skip@example.com", status=status)

            member.refresh_from_db()
            assert member.user_id is None
            assert User.objects.count() == 0

    def it_does_not_provision_an_active_member_with_a_blank_email():
        member = _create_member(_pre_signup_email="")

        member.refresh_from_db()
        assert member.user_id is None
        assert User.objects.count() == 0
