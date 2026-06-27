"""Tests for admin dashboard callback, snapshot view, and invite view."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from core.models import Invite
from membership.models import Member
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    VotePreferenceFactory,
)

User = get_user_model()


@pytest.fixture()
def admin_client():
    user = User.objects.create_superuser(
        username="dash-admin",
        password="pass",
        email="dash@example.com",
    )
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
def describe_dashboard_callback():
    def it_loads_admin_index(admin_client):
        resp = admin_client.get("/admin/")
        assert resp.status_code == 200
        assert "stats" in resp.context

    def it_shows_voting_stats(admin_client):
        plan = MembershipPlanFactory(monthly_price=Decimal("100.00"))
        g1, g2, g3 = GuildFactory(), GuildFactory(), GuildFactory()
        m1 = MemberFactory(membership_plan=plan)
        VotePreferenceFactory(member=m1, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        resp = admin_client.get("/admin/")
        stats = resp.context["stats"]

        assert stats["total_voters"] == 1
        assert stats["paying_voters"] == 1
        assert stats["contributed_pool"] == 10
        # 1 paying × $10 = $10 is below the $1000 floor, so projected_pool is floored.
        assert stats["projected_pool"] == 1000
        assert stats["floor_applied"] is True
        assert len(stats["top_guilds"]) > 0

    def it_excludes_non_standard_members_from_paying_voters(admin_client):
        g1, g2, g3 = GuildFactory(), GuildFactory(), GuildFactory()
        standard = MemberFactory(member_type=Member.MemberType.STANDARD)
        work_trade = MemberFactory(member_type=Member.MemberType.WORK_TRADE)
        VotePreferenceFactory(member=standard, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        VotePreferenceFactory(member=work_trade, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        resp = admin_client.get("/admin/")
        stats = resp.context["stats"]

        assert stats["total_voters"] == 2
        assert stats["paying_voters"] == 1
        assert stats["contributed_pool"] == 10
        # Still floored to $1000
        assert stats["projected_pool"] == 1000

    def it_does_not_apply_floor_when_contributed_exceeds_it(admin_client):
        plan = MembershipPlanFactory()
        g1, g2, g3 = GuildFactory(), GuildFactory(), GuildFactory()
        # 150 paying members × $10 = $1500, above the $1000 floor
        for _ in range(150):
            m = MemberFactory(membership_plan=plan, member_type=Member.MemberType.STANDARD)
            VotePreferenceFactory(member=m, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        resp = admin_client.get("/admin/")
        stats = resp.context["stats"]

        assert stats["paying_voters"] == 150
        assert stats["contributed_pool"] == 1500
        assert stats["projected_pool"] == 1500
        assert stats["floor_applied"] is False

    def it_handles_no_votes(admin_client):
        resp = admin_client.get("/admin/")
        stats = resp.context["stats"]

        assert stats["total_voters"] == 0
        assert stats["participation_pct"] == 0
        assert stats["top_guilds"] == []

    def it_excludes_guilds_with_zero_points(admin_client):
        GuildFactory()  # active guild with no votes
        resp = admin_client.get("/admin/")
        stats = resp.context["stats"]

        assert stats["active_guilds"] == GuildFactory._meta.model.objects.filter(is_active=True).count()
        assert stats["top_guilds"] == []


@pytest.mark.django_db
def describe_site_announcement_view():
    def it_requires_staff(client):
        resp = client.get("/admin/announcement/")
        assert resp.status_code == 302
        assert "/login" in resp.url or "/accounts/" in resp.url

    def it_renders_form_on_get(admin_client):
        resp = admin_client.get("/admin/announcement/")
        assert resp.status_code == 200
        assert "form" in resp.context

    def it_broadcasts_to_active_members_on_post(admin_client):
        from django.contrib.auth.models import User
        from django.db.models.signals import post_save
        from django.utils import timezone
        from factory.django import mute_signals

        from core.models import Notification

        member = MemberFactory()  # ACTIVE
        with mute_signals(post_save):
            # last_login set → "activated"; the spine skips never-signed-in members.
            recipient = User.objects.create_user(
                username="sa_recipient", email="sa@example.com", last_login=timezone.now()
            )
        member.user = recipient
        member.save(update_fields=["user"])

        resp = admin_client.post(
            "/admin/announcement/",
            data={"title": "Holiday hours", "body": "Closed July 4th."},
        )
        assert resp.status_code == 302
        assert Notification.objects.filter(user=recipient, trigger="site_announcement").exists()

    def it_delivers_every_announcement_not_just_the_first(admin_client):
        # Each announcement carries a unique idempotency period, so a second send
        # is NOT deduped against the first — both reach the member's bell. (Without
        # a per-send period they collapse onto one EventDelivery slot and the
        # second silently delivers nothing.)
        from django.contrib.auth.models import User
        from django.db.models.signals import post_save
        from django.utils import timezone
        from factory.django import mute_signals

        from core.models import Notification

        member = MemberFactory()  # ACTIVE
        with mute_signals(post_save):
            # last_login set → "activated"; the spine skips never-signed-in members.
            recipient = User.objects.create_user(
                username="sa_two", email="sa2@example.com", last_login=timezone.now()
            )
        member.user = recipient
        member.save(update_fields=["user"])

        admin_client.post("/admin/announcement/", data={"title": "First", "body": "One."})
        admin_client.post("/admin/announcement/", data={"title": "Second", "body": "Two."})

        titles = set(
            Notification.objects.filter(user=recipient, trigger="site_announcement").values_list("title", flat=True)
        )
        assert titles == {"First", "Second"}

    def it_re_renders_on_invalid_post(admin_client):
        resp = admin_client.post("/admin/announcement/", data={"title": "", "body": ""})
        assert resp.status_code == 200  # re-renders form with errors


@pytest.mark.django_db
def describe_invite_member_view():
    def it_requires_staff(client):
        resp = client.get("/admin/membership/member/invite/")
        assert resp.status_code == 302
        assert "/login" in resp.url or "/accounts/" in resp.url

    def it_renders_form_on_get(admin_client):
        resp = admin_client.get("/admin/membership/member/invite/")
        assert resp.status_code == 200
        assert "form" in resp.context

    def it_creates_invite_on_valid_post(admin_client):
        MembershipPlanFactory()
        with patch("core.email.send_mail"):
            resp = admin_client.post(
                "/admin/membership/member/invite/",
                data={"email": "new@example.com"},
            )
        assert resp.status_code == 302
        assert Invite.objects.filter(email="new@example.com").exists()
        member = Member.objects.get(_pre_signup_email="new@example.com")
        assert member.status == Member.Status.INVITED

    def it_shows_error_for_existing_member(admin_client):
        MembershipPlanFactory()
        MemberFactory(_pre_signup_email="exists@example.com", status=Member.Status.ACTIVE)
        resp = admin_client.post(
            "/admin/membership/member/invite/",
            data={"email": "exists@example.com"},
        )
        assert resp.status_code == 200  # re-renders form with errors

    def it_shows_error_when_no_plan_exists(admin_client):
        from membership.models import Member, MembershipPlan

        Member.objects.all().delete()
        MembershipPlan.objects.all().delete()
        resp = admin_client.post(
            "/admin/membership/member/invite/",
            data={"email": "noplan@example.com"},
        )
        assert resp.status_code == 200  # re-renders form with error message
