"""BDD specs for classes.import_service._find_instructor (instructor name matching)."""

import pytest

from classes.import_service import _find_instructor
from membership.models import Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def describe_find_instructor():
    def it_returns_a_unique_exact_match():
        billy = MemberFactory(preferred_name="Billy", full_legal_name="William Ortega")
        assert _find_instructor("Billy") == billy

    def it_matches_case_insensitively():
        billy = MemberFactory(preferred_name="Billy", full_legal_name="William Ortega")
        assert _find_instructor("billy") == billy

    def it_prefers_a_unique_exact_match_over_a_fuzzy_namesake():
        real = MemberFactory(preferred_name="Billy", full_legal_name="William Ortega")
        MemberFactory(preferred_name="", full_legal_name="Billy Bims")  # only a substring match
        assert _find_instructor("Billy") == real

    def it_refuses_to_guess_when_a_fragment_is_ambiguous():
        # The original bug: "Billy" matched two members and silently picked one.
        MemberFactory(preferred_name="", full_legal_name="Billy O'Brien")
        MemberFactory(preferred_name="", full_legal_name="Billy Bims")
        assert _find_instructor("Billy") is None

    def it_links_a_unique_substring_match():
        only = MemberFactory(preferred_name="", full_legal_name="Billy O'Brien")
        assert _find_instructor("Billy") == only

    def it_returns_none_when_nothing_matches():
        MemberFactory(full_legal_name="Jacqueline Sowell")
        assert _find_instructor("Billy") is None

    def it_ignores_inactive_members_entirely():
        MemberFactory(preferred_name="Billy", full_legal_name="William Ortega", status=Member.Status.FORMER)
        assert _find_instructor("Billy") is None

    def it_disambiguates_through_the_active_filter():
        # Two namesakes, but only one is active → unambiguous → linked.
        active = MemberFactory(preferred_name="", full_legal_name="Billy O'Brien")
        MemberFactory(preferred_name="", full_legal_name="Billy Bims", status=Member.Status.SUSPENDED)
        assert _find_instructor("Billy") == active
