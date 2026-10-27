"""Member.finance_name: the name billing shows for a payer or payee (#983)."""

from __future__ import annotations

import pytest
from django.utils import timezone

from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def describe_finance_name():
    def it_is_the_display_name_for_a_member_who_never_deleted():
        member = MemberFactory(full_legal_name="Rowan Kiln", preferred_name="Ro")
        assert member.finance_name == "Ro"

    def it_marks_a_deleted_member_with_their_kept_name():
        member = MemberFactory(
            full_legal_name="Deleted Member", name_at_deletion="Rowan Kiln", deleted_at=timezone.now()
        )
        assert member.finance_name == "Rowan Kiln (account deleted)"

    def it_stays_deleted_member_when_deleted_before_names_were_kept():
        member = MemberFactory(full_legal_name="Deleted Member", name_at_deletion="", deleted_at=timezone.now())
        assert member.finance_name == "Deleted Member"
