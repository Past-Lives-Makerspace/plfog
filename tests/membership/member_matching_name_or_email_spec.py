"""``MemberQuerySet.matching_name_or_email``: the portal half of the New Members lookup (#1115)."""

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User

from membership.models import Member
from tests.membership.factories import MemberEmailFactory, MemberFactory

pytestmark = pytest.mark.django_db


def _found(text: str) -> set[int]:
    return set(Member.objects.matching_name_or_email(text).values_list("pk", flat=True))


def describe_matching_name_or_email():
    def it_matches_part_of_the_legal_or_preferred_name_ignoring_case():
        legal = MemberFactory(full_legal_name="Tamsin Okonkwo", preferred_name="")
        preferred = MemberFactory(full_legal_name="Unrelated Person", preferred_name="Tamsy")
        assert _found("TAMS") == {legal.pk, preferred.pk}

    def it_matches_the_airtable_address_and_a_staged_alias():
        member = MemberFactory(_pre_signup_email="main.address@example.com")
        MemberEmailFactory(member=member, email="side.alias@example.com")
        assert _found("Main.Address") == {member.pk}
        assert _found("side.alias") == {member.pk}

    def it_matches_a_linked_members_account_addresses_once():
        member = MemberFactory(_pre_signup_email="linkme@example.com")
        user = User.objects.create_user(username="linkme", email="linkme@example.com")
        EmailAddress.objects.create(user=user, email="second.box@example.com", primary=False, verified=True)
        assert Member.objects.get(pk=member.pk).user_id == user.pk
        assert list(Member.objects.matching_name_or_email("example.com").filter(pk=member.pk)) == [
            Member.objects.get(pk=member.pk)
        ]
        assert _found("second.box") == {member.pk}

    def it_matches_nobody_for_an_unknown_text():
        MemberFactory(full_legal_name="Someone Else")
        assert _found("zzz-nothing") == set()
