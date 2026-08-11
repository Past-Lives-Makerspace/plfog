"""BDD-style tests for Member's guided-tour surface (Spec C §4): the toggle + completion contract."""

import pytest
from django.contrib.auth.models import User

from core.models import TourState
from membership.models import Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _linked_member(name: str) -> Member:
    # Creating the User auto-provisions a linked ACTIVE Member (ensure_user_has_member).
    user = User.objects.create_user(username=name, email=f"{name}@example.com")
    return Member.objects.get(user=user)


def describe_Member_guided_tours_enabled():
    def it_defaults_on():
        assert MemberFactory().guided_tours_enabled is True


def describe_Member_has_completed_tour():
    def it_is_false_when_no_row_exists():
        member = _linked_member("mt-none")
        assert member.has_completed_tour("member-welcome") is False

    def it_is_false_while_only_offered():
        member = _linked_member("mt-offered")
        TourState.objects.mark_offered(member.user, "member-welcome")
        assert member.has_completed_tour("member-welcome") is False

    def it_is_false_when_dismissed():
        member = _linked_member("mt-dismissed")
        TourState.objects.mark_dismissed(member.user, "member-welcome")
        assert member.has_completed_tour("member-welcome") is False

    def it_is_true_only_for_completed():
        member = _linked_member("mt-done")
        TourState.objects.mark_completed(member.user, "member-welcome")
        assert member.has_completed_tour("member-welcome") is True

    def it_is_false_for_an_unlinked_member():
        member = MemberFactory(user=None)
        assert member.has_completed_tour("member-welcome") is False

    def it_raises_on_an_unregistered_key():
        member = _linked_member("mt-bad")
        with pytest.raises(ValueError, match="Unknown tour key"):
            member.has_completed_tour("instructor-orientation")
