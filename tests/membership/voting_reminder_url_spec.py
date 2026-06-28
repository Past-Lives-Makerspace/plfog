"""The voting closing-reminder sources now emit ABSOLUTE member-hub URLs.

Both ``voting.closing_soon`` (voted members) and ``voting.vote_soon`` (signed-in
non-voters) used to carry a relative ``/guilds/voting/`` link, which renders as a dead
link in an email/Discord embed. They now build the URL through MEMBER_BASE_URL.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from membership.voting import closing_soon_occurrences, vote_soon_occurrences
from tests.membership.factories import MemberFactory, VotePreferenceFactory

pytestmark = pytest.mark.django_db


def _aware(year: int, month: int, day: int):
    return timezone.make_aware(datetime(year, month, day, 0, 0))


def _link(member, email: str):
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u{member.pk}", email=email)
    user.last_login = timezone.now()
    user.save(update_fields=["last_login"])
    member.user = user
    member.save(update_fields=["user"])
    return user


def _assert_absolute_voting_url(occurrence) -> None:
    assert occurrence.url.startswith(settings.MEMBER_BASE_URL)
    assert not occurrence.url.startswith("/")
    assert occurrence.context["voting_url"].startswith(settings.MEMBER_BASE_URL)


def describe_closing_soon_occurrences_url():
    def it_emits_an_absolute_member_hub_voting_url():
        member = MemberFactory()  # ACTIVE + STANDARD (paying)
        _link(member, "voted@example.com")
        VotePreferenceFactory(member=member, signed_up=False)

        occurrences = list(closing_soon_occurrences(_aware(2026, 6, 1)))

        assert len(occurrences) == 1
        _assert_absolute_voting_url(occurrences[0])


def describe_vote_soon_occurrences_url():
    def it_emits_an_absolute_member_hub_voting_url():
        member = MemberFactory()  # paying, no vote preference
        _link(member, "nonvoter@example.com")

        occurrences = list(vote_soon_occurrences(_aware(2026, 6, 1)))

        assert len(occurrences) == 1
        _assert_absolute_voting_url(occurrences[0])
