"""BDD specs for the Overview "review & send" banner + the send_results HTMX view."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User as DjangoUser
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from factory.django import mute_signals

from core.models import TransactionalEmailLog
from membership.models import FundingSnapshot
from tests.membership.factories import GuildFactory, MemberFactory, MembershipPlanFactory, VotePreferenceFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture()
def admin_client():
    with mute_signals(post_save):
        user = User.objects.create_superuser("rsadmin", "rsadmin@x.com", "p")
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture()
def member_client():
    MembershipPlanFactory()
    user = User.objects.create_user("rsmember", email="rsmember@x.com", password="p")
    client = Client()
    client.force_login(user)
    return client


def _pending_snapshot():
    """Take a snapshot with one active email-bearing voter → results pending send."""
    member = MemberFactory()
    with mute_signals(post_save):
        voter_user = DjangoUser.objects.create_user(username="rsvoter", email="rsvoter@x.com")
    member.user = voter_user
    member.save(update_fields=["user"])
    g1, g2, g3 = GuildFactory(name="Metal"), GuildFactory(name="Fiber"), GuildFactory(name="Wood")
    VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)
    snap = FundingSnapshot.take()
    assert snap is not None
    return snap


def describe_overview_banner():
    def it_shows_the_banner_when_a_snapshot_is_pending(admin_client):
        snap = _pending_snapshot()
        resp = admin_client.get(reverse("hub_admin_voting_overview"))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert f"Results are in for {snap.cycle_label}" in body
        assert "Send results" in body

    def it_hides_the_banner_when_nothing_is_pending(admin_client):
        resp = admin_client.get(reverse("hub_admin_voting_overview"))
        assert resp.status_code == 200
        assert "Results are in for" not in resp.content.decode()


def describe_send_results():
    def it_is_forbidden_for_non_admins(member_client):
        snap = _pending_snapshot()
        assert member_client.post(reverse("hub_admin_voting_send_results", args=[snap.pk])).status_code == 403

    def it_sends_and_returns_the_sent_state_with_a_success_toast(admin_client):
        snap = _pending_snapshot()
        resp = admin_client.post(reverse("hub_admin_voting_send_results", args=[snap.pk]))
        assert resp.status_code == 200
        assert b"Resend" in resp.content
        trigger = json.loads(resp["HX-Trigger"])
        assert "Results sent to 1 member" in trigger["showToast"]["message"]
        snap.refresh_from_db()
        assert snap.results_sent_at is not None
        assert TransactionalEmailLog.objects.filter(trigger_kind="voting.results_published").count() == 1

    def it_returns_an_error_toast_and_does_not_resend_a_second_time(admin_client):
        snap = _pending_snapshot()
        admin_client.post(reverse("hub_admin_voting_send_results", args=[snap.pk]))
        resp = admin_client.post(reverse("hub_admin_voting_send_results", args=[snap.pk]))
        assert resp.status_code == 200
        trigger = json.loads(resp["HX-Trigger"])
        assert trigger["showToast"]["type"] == "error"
        assert "already sent" in trigger["showToast"]["message"]
        assert TransactionalEmailLog.objects.filter(trigger_kind="voting.results_published").count() == 1

    def it_resends_when_resend_flag_is_set(admin_client):
        snap = _pending_snapshot()
        admin_client.post(reverse("hub_admin_voting_send_results", args=[snap.pk]))
        resp = admin_client.post(reverse("hub_admin_voting_send_results", args=[snap.pk]), {"resend": "1"})
        assert resp.status_code == 200
        trigger = json.loads(resp["HX-Trigger"])
        assert trigger["showToast"]["type"] == "success"
        snap.refresh_from_db()
        assert snap.results_send_count == 2
        assert TransactionalEmailLog.objects.filter(trigger_kind="voting.results_published").count() == 2
