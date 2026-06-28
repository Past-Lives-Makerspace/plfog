"""Taking a snapshot no longer auto-emails members (admin-confirmed results model).

``FundingSnapshot.take()`` freezes the votes and pings admins via
``voting.results_ready`` — it does NOT emit ``voting.results_published`` to members.
That now fires only on the admin's ``send_results()`` click (covered in
``tests/core/events/new_events_spec.py`` + ``funding_snapshot_results_spec.py``).
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
    def it_does_not_notify_members_when_a_snapshot_is_taken():
        g1 = GuildFactory(name="G1")
        g2 = GuildFactory(name="G2")
        g3 = GuildFactory(name="G3")
        member = MemberFactory()  # ACTIVE + STANDARD (paying) → an eligible voter
        user = _user_for_member(member)
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)

        FundingSnapshot.take()

        assert not Notification.objects.filter(user=user, trigger="voting.results_published").exists()

    def it_does_not_notify_when_no_votes():
        FundingSnapshot.take()

        assert not Notification.objects.filter(trigger="voting.results_published").exists()
