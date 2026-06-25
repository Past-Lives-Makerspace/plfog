"""Notification emission at funding snapshot — voting.results_published.

FundingSnapshot.take() now emits the ``voting.results_published`` event (one
vocabulary) to all voters, superseding the old ``notifications.dispatch(
"funding_results_published")``. The recipient is ``all_voters`` (paying active
members), not the broad active-member dispatch.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from factory.django import mute_signals

from core.models import Notification
from membership.models import FundingSnapshot
from tests.membership.factories import GuildFactory, MemberFactory, VotePreferenceFactory

pytestmark = pytest.mark.django_db


def _user_for_member(member):
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"fm{member.pk}", email=f"fm{member.pk}@example.com")
    member.user = user
    member.save(update_fields=["user"])
    return user


def describe_voting_results_published_emission():
    def it_notifies_voters_after_snapshot():
        g1 = GuildFactory(name="G1")
        g2 = GuildFactory(name="G2")
        g3 = GuildFactory(name="G3")
        member = MemberFactory()  # ACTIVE + STANDARD (paying) → an eligible voter
        user = _user_for_member(member)
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)

        FundingSnapshot.take()

        assert Notification.objects.filter(user=user, trigger="voting.results_published").exists()

    def it_does_not_notify_when_no_votes():
        FundingSnapshot.take()

        assert not Notification.objects.filter(trigger="voting.results_published").exists()
