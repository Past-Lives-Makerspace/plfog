"""BDD specs for the Overview "review & send" banner + the send_results HTMX view."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User as DjangoUser
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from django.core.management import call_command

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


def _drain_send_queue():
    """Run the scheduler job that performs whatever the admin's click queued."""
    call_command("send_pending_funding_results")


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

    def it_queues_the_send_and_returns_the_queued_state(admin_client):
        snap = _pending_snapshot()
        resp = admin_client.post(reverse("hub_admin_voting_send_results", args=[snap.pk]))
        assert resp.status_code == 200
        assert b"Sending results now" in resp.content
        trigger = json.loads(resp["HX-Trigger"])
        assert "on their way" in trigger["showToast"]["message"]
        snap.refresh_from_db()
        assert snap.results_send_requested_at is not None

    def it_does_not_email_anyone_on_the_request_thread(admin_client):
        """The whole point of queueing: the click must not run the fan-out itself."""
        snap = _pending_snapshot()
        admin_client.post(reverse("hub_admin_voting_send_results", args=[snap.pk]))
        snap.refresh_from_db()
        assert snap.results_sent_at is None
        assert TransactionalEmailLog.objects.filter(trigger_kind="voting.results_published").count() == 0

    def it_emails_once_the_scheduler_drains_the_queue(admin_client):
        snap = _pending_snapshot()
        admin_client.post(reverse("hub_admin_voting_send_results", args=[snap.pk]))
        _drain_send_queue()
        snap.refresh_from_db()
        assert snap.results_sent_at is not None
        assert snap.results_send_requested_at is None
        assert TransactionalEmailLog.objects.filter(trigger_kind="voting.results_published").count() == 1

    def it_returns_an_error_toast_and_does_not_resend_a_second_time(admin_client):
        snap = _pending_snapshot()
        admin_client.post(reverse("hub_admin_voting_send_results", args=[snap.pk]))
        _drain_send_queue()
        resp = admin_client.post(reverse("hub_admin_voting_send_results", args=[snap.pk]))
        assert resp.status_code == 200
        trigger = json.loads(resp["HX-Trigger"])
        assert trigger["showToast"]["type"] == "error"
        assert "already sent" in trigger["showToast"]["message"]
        assert TransactionalEmailLog.objects.filter(trigger_kind="voting.results_published").count() == 1

    def it_resends_when_resend_flag_is_set(admin_client):
        snap = _pending_snapshot()
        admin_client.post(reverse("hub_admin_voting_send_results", args=[snap.pk]))
        _drain_send_queue()
        resp = admin_client.post(reverse("hub_admin_voting_send_results", args=[snap.pk]), {"resend": "1"})
        assert resp.status_code == 200
        trigger = json.loads(resp["HX-Trigger"])
        assert trigger["showToast"]["type"] == "success"
        _drain_send_queue()
        snap.refresh_from_db()
        assert snap.results_send_count == 2
        assert TransactionalEmailLog.objects.filter(trigger_kind="voting.results_published").count() == 2


def describe_send_results_when_another_snapshot_is_still_pending():
    """The out-of-band banner refresh used to recurse until the stack blew.

    ``_results_send_control.html`` renders the banner when ``oob`` is set, and the
    banner renders the send control for whatever is still pending. ``{% include %}``
    passes the parent context down, and ``with`` only ADDS names, so ``oob`` stayed
    true inside the banner's copy of the control, which rendered the banner again.

    Nothing caught it because the cycle needs a second pending snapshot to keep the
    banner non-empty, and every other spec here has exactly one. On production it
    took the September results 500 after the emails had already gone out.
    """

    def _two_pending_snapshots():
        older = _pending_snapshot()
        newer = FundingSnapshot.take()
        assert newer is not None
        # Pin the order so most_recent_pending() cannot pick by insertion luck.
        FundingSnapshot.objects.filter(pk=older.pk).update(snapshot_at=timezone.now() - timedelta(days=1))
        older.refresh_from_db()
        return older, newer

    def it_sends_without_blowing_the_stack(admin_client):
        older, newer = _two_pending_snapshots()
        resp = admin_client.post(reverse("hub_admin_voting_send_results", args=[newer.pk]))
        assert resp.status_code == 200
        newer.refresh_from_db()
        assert newer.results_send_requested_at is not None

    def it_refreshes_the_banner_onto_the_snapshot_still_pending(admin_client):
        older, newer = _two_pending_snapshots()
        body = admin_client.post(reverse("hub_admin_voting_send_results", args=[newer.pk])).content.decode()
        assert 'hx-swap-oob="outerHTML"' in body
        # On the pk, not the cycle_label. take() with no title stamps "%B %Y", so both
        # snapshots here are labelled the same month and a label assertion would be
        # satisfied by either one — leaving the snapshot_at ordering above unproven.
        assert f'id="results-send-control-{older.pk}"' in body
        assert f'id="results-send-control-{newer.pk}"' in body

    def it_renders_the_banner_exactly_once(admin_client):
        older, newer = _two_pending_snapshots()
        body = admin_client.post(reverse("hub_admin_voting_send_results", args=[newer.pk])).content.decode()
        assert body.count('id="results-review-region"') == 1
        assert body.count("pl-results-banner__title") == 1
        # The banner's own Send button keeps its CSRF token. This is what rejects the
        # other obvious fix: `{% include ... only %}` also stops the recursion and also
        # passes every other assertion here, but it cuts csrf_token out of the child
        # context, so the banner's Send button would 403. Without this line the suite
        # cannot tell the two fixes apart. One token, not two: the posted-for snapshot
        # now renders the queued status text, which carries no form.
        assert body.count('name="csrfmiddlewaretoken"') == 1

    def it_still_reports_already_sent_without_blowing_the_stack(admin_client):
        older, newer = _two_pending_snapshots()
        admin_client.post(reverse("hub_admin_voting_send_results", args=[newer.pk]))
        _drain_send_queue()
        resp = admin_client.post(reverse("hub_admin_voting_send_results", args=[newer.pk]))
        assert resp.status_code == 200
        assert json.loads(resp["HX-Trigger"])["showToast"]["type"] == "error"
