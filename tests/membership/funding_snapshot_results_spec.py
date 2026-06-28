"""BDD specs for the admin-confirmed results model — take() + send_results().

take() freezes votes, logs ONE activity, pings admins (voting.results_ready), and
does NOT email members. send_results() is the admin's explicit, idempotent send.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db.models.signals import post_save
from factory.django import mute_signals

from core.models import Notification, NotificationPreference, SiteActivity, TransactionalEmailLog
from membership.models import FundingSnapshot, Member, ResultsAlreadySentError, VotingSettings
from tests.membership.factories import GuildFactory, MemberFactory, VotePreferenceFactory

pytestmark = pytest.mark.django_db


def _linked(member, email):
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u{member.pk}", email=email)
    member.user = user
    member.save(update_fields=["user"])
    return user


def _voter(email, *, member_type=Member.MemberType.STANDARD, status=Member.Status.ACTIVE, picks=None):
    """An active paying member with a linked, email-bearing user and a vote."""
    member = MemberFactory(member_type=member_type, status=status)
    _linked(member, email)
    g1, g2, g3 = picks or (GuildFactory(), GuildFactory(), GuildFactory())
    VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)
    return member


def _admin(email):
    member = MemberFactory(fog_role=Member.FogRole.ADMIN)
    return _linked(member, email)


def describe_take():
    def it_does_not_email_members_but_pings_admins_and_logs_one_activity():
        _voter("voter@x.com")
        admin = _admin("admin@x.com")

        snap = FundingSnapshot.take()

        assert snap is not None
        # No member results email/in-app from taking a snapshot.
        assert not TransactionalEmailLog.objects.filter(trigger_kind="voting.results_published").exists()
        assert not Notification.objects.filter(trigger="voting.results_published").exists()
        # Admins are pinged to review & send.
        assert Notification.objects.filter(user=admin, trigger="voting.results_ready").exists()
        # Exactly one activity row, targeting the snapshot.
        acts = SiteActivity.objects.filter(kind=SiteActivity.Kind.FUNDING_SNAPSHOT_TAKEN)
        assert acts.count() == 1
        assert acts.first().target_id == snap.pk

    def it_uses_the_settings_floor_when_minimum_pool_is_none():
        settings = VotingSettings.load()
        settings.minimum_pool_floor = Decimal("750.00")
        settings.save()
        _voter("v@x.com")  # 1 paying → $10 contributed, below the floor

        snap = FundingSnapshot.take()  # minimum_pool defaults to None → the setting

        assert snap is not None
        assert snap.minimum_pool == Decimal("750.00")
        assert snap.funding_pool == Decimal("750.00")

    def it_lets_an_explicit_minimum_pool_override_the_setting():
        settings = VotingSettings.load()
        settings.minimum_pool_floor = Decimal("750.00")
        settings.save()
        _voter("v@x.com")

        snap = FundingSnapshot.take(minimum_pool=Decimal("200"))

        assert snap is not None
        assert snap.minimum_pool == Decimal("200.00")
        assert snap.funding_pool == Decimal("200.00")

    def it_flags_is_auto_and_drives_the_source_label():
        _voter("v@x.com")
        auto = FundingSnapshot.take(is_auto=True)
        manual = FundingSnapshot.take()
        assert auto is not None and manual is not None
        assert auto.is_auto is True
        assert auto.source_label == "Automatic"
        assert manual.is_auto is False
        assert manual.source_label == "Manual"

    def it_pings_admins_with_absolute_review_urls():
        # The spine never absolutizes — a bare "/manage/..." path is a dead link in an
        # admin's inbox/Discord, so both the emit url and the context review_url must
        # carry the full host.
        from unittest.mock import patch

        from django.conf import settings as dj_settings

        _voter("v@x.com")
        with patch("core.events.emit.emit") as mock_emit:
            snap = FundingSnapshot.take()
        assert snap is not None
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["url"].startswith(dj_settings.MEMBER_BASE_URL)
        assert kwargs["context"]["review_url"].startswith(dj_settings.MEMBER_BASE_URL)
        assert kwargs["url"].endswith(f"/manage/voting/history/{snap.pk}/")


def describe_results_pending():
    def it_is_true_for_a_fresh_snapshot_with_allocation():
        _voter("v@x.com")
        snap = FundingSnapshot.take()
        assert snap is not None
        assert snap.results_pending is True

    def it_is_false_after_results_are_sent():
        _voter("v@x.com")
        snap = FundingSnapshot.take()
        assert snap is not None
        snap.send_results()
        snap.refresh_from_db()
        assert snap.results_pending is False

    def it_is_false_for_a_legacy_voteless_snapshot():
        snap = FundingSnapshot.objects.create(
            cycle_label="Old", contributor_count=0, funding_pool=Decimal("0"), results={}
        )
        assert snap.results_pending is False


def describe_most_recent_pending():
    def it_returns_the_newest_pending_with_allocation_skipping_sent_and_legacy():
        _voter("v1@x.com")
        first = FundingSnapshot.take()
        _voter("v2@x.com")
        second = FundingSnapshot.take()
        assert first is not None and second is not None
        # A legacy (no-allocation) row that is also unsent must be skipped.
        FundingSnapshot.objects.create(cycle_label="Legacy", contributor_count=0, funding_pool=Decimal("0"), results={})

        assert FundingSnapshot.most_recent_pending() == second
        second.send_results()
        assert FundingSnapshot.most_recent_pending() == first

    def it_returns_none_when_nothing_is_pending():
        assert FundingSnapshot.most_recent_pending() is None


def describe_send_results():
    def it_sends_once_per_active_voter_with_personalized_votes_and_stamps():
        _voter("a@x.com", picks=(GuildFactory(name="Metal"), GuildFactory(name="Fiber"), GuildFactory(name="Wood")))
        _voter("b@x.com", picks=(GuildFactory(name="Clay"), GuildFactory(name="Glass"), GuildFactory(name="Stone")))
        snap = FundingSnapshot.take()
        assert snap is not None
        mail.outbox.clear()

        n = snap.send_results()

        assert n == 2
        snap.refresh_from_db()
        assert snap.results_sent_at is not None
        assert snap.results_send_count == 1
        assert TransactionalEmailLog.objects.filter(trigger_kind="voting.results_published").count() == 2
        bodies = {m.to[0]: m.body for m in mail.outbox}
        assert "1st: Metal" in bodies["a@x.com"]
        assert "1st: Clay" in bodies["b@x.com"]
        assert "1st: Clay" not in bodies["a@x.com"]

    def it_raises_when_sent_twice_without_resend():
        _voter("v@x.com")
        snap = FundingSnapshot.take()
        assert snap is not None
        snap.send_results()

        with pytest.raises(ResultsAlreadySentError):
            snap.send_results()

        snap.refresh_from_db()
        assert snap.results_send_count == 1
        assert TransactionalEmailLog.objects.filter(trigger_kind="voting.results_published").count() == 1

    def it_resends_with_resend_true_and_bumps_the_count():
        voter = _voter("v@x.com")
        snap = FundingSnapshot.take()
        assert snap is not None
        snap.send_results()

        n = snap.send_results(resend=True)

        assert n == 1
        snap.refresh_from_db()
        assert snap.results_send_count == 2
        # A fresh period re-delivers → a second in-app row for the voter.
        assert Notification.objects.filter(trigger="voting.results_published", user=voter.user).count() == 2

    def it_emits_absolute_voting_urls_to_each_voter():
        # The results email's "See the full breakdown" link must carry the full host,
        # not a bare "/guilds/..." path (the spine never absolutizes it).
        from unittest.mock import patch

        from django.conf import settings as dj_settings

        _voter("v@x.com")
        snap = FundingSnapshot.take()
        assert snap is not None
        with patch("core.events.emit.emit") as mock_emit:
            snap.send_results()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["url"].startswith(dj_settings.MEMBER_BASE_URL)
        assert kwargs["context"]["voting_url"].startswith(dj_settings.MEMBER_BASE_URL)
        assert kwargs["url"].endswith("/guilds/voting/history/")

    def it_renders_an_intro_note_at_the_top_when_provided():
        _voter("a@x.com")
        snap = FundingSnapshot.take()
        assert snap is not None
        mail.outbox.clear()

        snap.send_results(intro_note="Sorry this is overdue — results are automated from here.")

        assert "Sorry this is overdue — results are automated from here." in mail.outbox[0].body

    def it_omits_the_note_and_still_renders_when_no_intro_note_is_given():
        _voter("a@x.com")
        snap = FundingSnapshot.take()
        assert snap is not None
        mail.outbox.clear()

        snap.send_results()  # default intro_note=""

        body = mail.outbox[0].body
        assert "overdue" not in body
        assert "The votes for" in body  # the email still renders normally without a note


def describe_send_results_audience_safety():
    def it_excludes_former_suspended_and_emailless_and_respects_email_opt_out():
        active = _voter("active@x.com")
        _voter("former@x.com", status=Member.Status.FORMER)
        _voter("susp@x.com", status=Member.Status.SUSPENDED)
        # An active voter with a linked user but NO usable email.
        no_email = MemberFactory(member_type=Member.MemberType.STANDARD)
        with mute_signals(post_save):
            ne_user = User.objects.create_user(username="noemail", email="")
        no_email.user = ne_user
        no_email.save(update_fields=["user"])
        g1, g2, g3 = GuildFactory(), GuildFactory(), GuildFactory()
        VotePreferenceFactory(member=no_email, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)
        # An active voter who opted out of the results EMAIL (in-app stays on).
        opted_out = _voter("optout@x.com")
        NotificationPreference.objects.create(
            user=opted_out.user, event_key="voting.results_published", channel="email", enabled=False
        )

        snap = FundingSnapshot.take()
        assert snap is not None
        n = snap.send_results()

        # Only the active voter gets an email; the opted-out voter does not.
        email_to = set(
            TransactionalEmailLog.objects.filter(trigger_kind="voting.results_published").values_list(
                "to_email", flat=True
            )
        )
        assert email_to == {"active@x.com"}
        # In-app reaches the active voter AND the email-opted-out voter (in-app isn't opted out).
        inapp_users = set(
            Notification.objects.filter(trigger="voting.results_published").values_list("user_id", flat=True)
        )
        assert inapp_users == {active.user_id, opted_out.user_id}
        assert n == 2  # active + opted-out each had at least one fresh delivery
