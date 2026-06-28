"""Regression guard for the BLOCKER repoints in the voting-admin overhaul.

Removing the old ``admin_snapshot_*`` routes without repointing their four
consumers would 500 the FundingSnapshot changelist, the VotePreference change
page (Historical Votes), and the Unfold dashboard home. These specs prove every
consumer now resolves the new ``hub_admin_voting_*`` names, and that the old
names are gone.

See docs/superpowers/plans/2026-06-25-voting-admin-tabs-and-audit.md.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import NoReverseMatch, reverse

from membership.models import FundingSnapshot, Member, VotePreference
from tests.membership.factories import GuildFactory, MemberFactory, VotePreferenceFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture()
def admin_client():
    User.objects.create_superuser("linkadmin", "linkadmin@x.com", "p")
    client = Client()
    client.login(username="linkadmin", password="p")
    return client


def _snapshot_capturing(member: Member) -> FundingSnapshot:
    g1, g2, g3 = GuildFactory(name="A"), GuildFactory(name="B"), GuildFactory(name="C")
    VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
    snap = FundingSnapshot.take(minimum_pool=Decimal("0"))
    assert snap is not None
    return snap


def describe_repointed_consumers():
    def it_member_admin_change_page_loads_with_vote_history(admin_client):
        member = MemberFactory(full_legal_name="Voter Person")
        snap = _snapshot_capturing(member)
        vote = VotePreference.objects.get(member=member)
        resp = admin_client.get(reverse("admin:membership_votepreference_change", args=[vote.pk]))
        assert resp.status_code == 200
        # The "Historical Votes" rows now deep-link to the hub Funding History detail.
        assert reverse("hub_admin_voting_history_detail", args=[snap.pk]).encode() in resp.content

    def it_fundingsnapshot_changelist_loads(admin_client):
        member = MemberFactory(full_legal_name="Voter Two")
        _snapshot_capturing(member)
        resp = admin_client.get(reverse("admin:membership_fundingsnapshot_changelist"))
        assert resp.status_code == 200
        assert b"Open analyzer" in resp.content

    def it_admin_dashboard_home_resolves_repointed_urls(admin_client):
        member = MemberFactory(full_legal_name="Voter Three")
        snap = _snapshot_capturing(member)
        resp = admin_client.get(reverse("admin:index"))
        assert resp.status_code == 200
        assert reverse("hub_admin_voting_snapshots").encode() in resp.content
        assert reverse("hub_admin_voting_history_detail", args=[snap.pk]).encode() in resp.content


def describe_retired_routes():
    def it_no_longer_reverses_the_old_snapshot_names():
        for name in [
            "admin_snapshot_draft",
            "admin_snapshot_take",
            "admin_snapshot_detail",
            "admin_snapshot_delete",
        ]:
            with pytest.raises(NoReverseMatch):
                reverse(name, args=[1] if name in {"admin_snapshot_detail", "admin_snapshot_delete"} else [])
