"""BDD specs for the Member Home / Dashboard view and its ``hub.home`` aggregation service."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import CalendarEvent, CommunityEvent, Member
from tests.membership.factories import (
    CommunityEventFactory,
    GuildAnnouncementFactory,
    GuildFactory,
    GuildMembershipFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db


def _member_user(username: str = "homer") -> User:
    """A logged-in-ready User whose auto-created Member is linked (needs a plan first)."""
    MembershipPlanFactory()
    return User.objects.create_user(username=username, password="pass")


def _future_slot(guild: object, *, days: int = 3) -> object:
    start = timezone.now() + timedelta(days=days)
    return OrientationSlotFactory(guild=guild, starts_at=start, ends_at=start + timedelta(hours=1))


def _class_event(title: str, *, days: int = 4) -> CalendarEvent:
    start = timezone.now() + timedelta(days=days)
    return CalendarEvent.objects.create(
        source=CalendarEvent.Source.CLASSES,
        uid=f"local-class-{title}",
        title=title,
        url="/classes/intro-welding/",
        start_dt=start,
        end_dt=start + timedelta(hours=2),
        fetched_at=timezone.now(),
    )


def describe_hub_home_view():
    def it_requires_login(client: Client):
        response = client.get(reverse("hub_home"))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_renders_the_dashboard_for_a_linked_member(client: Client):
        user = _member_user("linked")
        user.member.preferred_name = "Ada"
        user.member.save(update_fields=["preferred_name"])
        client.login(username="linked", password="pass")

        response = client.get(reverse("hub_home"))

        assert response.status_code == 200
        assert "hub/home.html" in [t.name for t in response.templates]
        assert b"Welcome back, Ada" in response.content

    def it_handles_a_user_with_no_member(client: Client):
        # A User with no linked Member renders the friendly not-linked state, no crash.
        user = User.objects.create_user(username="orphan", password="pass")
        Member.objects.filter(user=user).delete()
        client.login(username="orphan", password="pass")

        response = client.get(reverse("hub_home"))

        assert response.status_code == 200
        assert b"isn't linked to a membership" in response.content

    def describe_upcoming():
        def it_lists_soonest_first_capped(client: Client):
            user = _member_user("soon")
            member = user.member
            guild = GuildFactory(name="Ceramics")
            GuildMembershipFactory(guild=guild, member=member)
            # Seven community events at increasing offsets — only the 5 soonest show.
            for i in range(7):
                start = timezone.now() + timedelta(days=i + 1)
                CommunityEventFactory(
                    guild=guild, title=f"Meet {i}", starts_at=start, ends_at=start + timedelta(hours=1)
                )
            client.login(username="soon", password="pass")

            upcoming = client.get(reverse("hub_home")).context["upcoming"]

            assert len(upcoming) == 5
            starts = [item.start for item in upcoming]
            assert starts == sorted(starts)

        def it_includes_the_members_orientation_bookings(client: Client):
            user = _member_user("orient")
            member = user.member
            guild = GuildFactory(name="Woodshop")
            slot = _future_slot(guild)
            OrientationBookingFactory(slot=slot, member=member)
            client.login(username="orient", password="pass")

            upcoming = client.get(reverse("hub_home")).context["upcoming"]

            assert any(item.kind == "Orientation" and "Woodshop" in item.title for item in upcoming)

        def it_includes_their_guilds_meetings_and_site_wide_events(client: Client):
            user = _member_user("mix")
            member = user.member
            guild = GuildFactory(name="Metals")
            GuildMembershipFactory(guild=guild, member=member)
            CommunityEventFactory(guild=guild, title="Metals Meeting")
            CommunityEventFactory(community=True, title="Potluck")
            _class_event("Intro to Welding")
            client.login(username="mix", password="pass")

            upcoming = client.get(reverse("hub_home")).context["upcoming"]
            titles = {item.title for item in upcoming}

            assert "Metals Meeting" in titles
            assert "Potluck" in titles
            assert "Intro to Welding" in titles

        def it_excludes_meetings_of_guilds_the_member_has_not_joined(client: Client):
            _member_user("scoped")
            other_guild = GuildFactory(name="Textiles")
            CommunityEventFactory(guild=other_guild, title="Textiles Meeting")
            client.login(username="scoped", password="pass")

            upcoming = client.get(reverse("hub_home")).context["upcoming"]

            assert all("Textiles Meeting" != item.title for item in upcoming)

        def it_shows_the_empty_state_when_nothing_upcoming(client: Client):
            _member_user("empty_up")
            client.login(username="empty_up", password="pass")

            response = client.get(reverse("hub_home"))

            assert response.context["upcoming"] == []
            assert b"Nothing on your calendar yet" in response.content

    def describe_announcements():
        def it_shows_active_guild_announcements_for_joined_guilds(client: Client):
            user = _member_user("ann")
            member = user.member
            guild = GuildFactory(name="Print")
            GuildMembershipFactory(guild=guild, member=member)
            GuildAnnouncementFactory(guild=guild, title="Kiln down this week")
            client.login(username="ann", password="pass")

            announcements = client.get(reverse("hub_home")).context["announcements"]

            assert [a.title for a in announcements] == ["Kiln down this week"]

        def it_excludes_expired_announcements(client: Client):
            user = _member_user("expann")
            member = user.member
            guild = GuildFactory(name="Glass")
            GuildMembershipFactory(guild=guild, member=member)
            GuildAnnouncementFactory(guild=guild, title="Old news", expires_at=timezone.localdate() - timedelta(days=1))
            client.login(username="expann", password="pass")

            announcements = client.get(reverse("hub_home")).context["announcements"]

            assert announcements == []

        def it_excludes_announcements_from_guilds_the_member_has_not_joined(client: Client):
            _member_user("otherann")
            other = GuildFactory(name="Leather")
            GuildAnnouncementFactory(guild=other, title="Not yours")
            client.login(username="otherann", password="pass")

            announcements = client.get(reverse("hub_home")).context["announcements"]

            assert announcements == []

    def describe_my_guilds():
        def it_lists_joined_guilds(client: Client):
            user = _member_user("myg")
            member = user.member
            guild = GuildFactory(name="Ceramics")
            GuildMembershipFactory(guild=guild, member=member)
            client.login(username="myg", password="pass")

            my_guilds = client.get(reverse("hub_home")).context["my_guilds"]

            assert [entry.guild.name for entry in my_guilds] == ["Ceramics"]
            assert my_guilds[0].is_staff is False

        def it_flags_led_or_staffed_guilds(client: Client):
            user = _member_user("leadg")
            member = user.member
            guild = GuildFactory(name="Metals")
            GuildMembershipFactory(guild=guild, member=member)
            GuildStaffMembershipFactory(guild=guild, member=member)
            client.login(username="leadg", password="pass")

            my_guilds = client.get(reverse("hub_home")).context["my_guilds"]

            assert my_guilds[0].is_staff is True

        def it_shows_the_empty_state_with_no_guilds(client: Client):
            _member_user("nog")
            client.login(username="nog", password="pass")

            response = client.get(reverse("hub_home"))

            assert response.context["my_guilds"] == []
            assert b"haven't joined any guilds yet" in response.content

    def describe_profile_nudge():
        def it_shows_when_incomplete(client: Client):
            _member_user("incomplete")
            client.login(username="incomplete", password="pass")

            response = client.get(reverse("hub_home"))

            assert response.context["profile"].complete is False
            assert b"Finish setting up your profile" in response.content

        def it_hides_when_complete(client: Client):
            user = _member_user("complete")
            member = user.member
            member.profile_photo = "members/profile/a.png"
            member.about_me = "Maker."
            member.pronouns = Member.Pronouns.THEY_THEM
            member.discord_user_id = "123456789012345678"
            member.show_in_directory = True
            member.save()
            client.login(username="complete", password="pass")

            response = client.get(reverse("hub_home"))

            assert response.context["profile"].complete is True
            assert b"Finish setting up your profile" not in response.content

        def it_links_to_profile_settings(client: Client):
            _member_user("nudgelink")
            client.login(username="nudgelink", password="pass")

            response = client.get(reverse("hub_home"))

            assert reverse("hub_user_settings") in response.content.decode()


def describe_build_home_context():
    def it_caps_each_section():
        from hub.home import ANNOUNCEMENTS_CAP, UPCOMING_CAP, build_home_context

        MembershipPlanFactory()
        member = MemberFactory()
        guild = GuildFactory(name="Ceramics")
        GuildMembershipFactory(guild=guild, member=member)
        for i in range(UPCOMING_CAP + 3):
            start = timezone.now() + timedelta(days=i + 1)
            CommunityEventFactory(guild=guild, title=f"E{i}", starts_at=start, ends_at=start + timedelta(hours=1))
        for i in range(ANNOUNCEMENTS_CAP + 2):
            GuildAnnouncementFactory(guild=guild, title=f"A{i}")

        ctx = build_home_context(member)

        assert len(ctx["upcoming"]) == UPCOMING_CAP
        assert len(ctx["announcements"]) == ANNOUNCEMENTS_CAP

    def it_orders_upcoming_by_start():
        from hub.home import build_home_context

        MembershipPlanFactory()
        member = MemberFactory()
        guild = GuildFactory(name="Woodshop")
        GuildMembershipFactory(guild=guild, member=member)
        late = timezone.now() + timedelta(days=10)
        early = timezone.now() + timedelta(days=2)
        CommunityEventFactory(guild=guild, title="Later", starts_at=late, ends_at=late + timedelta(hours=1))
        CommunityEventFactory(guild=guild, title="Sooner", starts_at=early, ends_at=early + timedelta(hours=1))

        ctx = build_home_context(member)

        assert [item.title for item in ctx["upcoming"]] == ["Sooner", "Later"]

    def it_scopes_announcements_to_joined_guilds():
        from hub.home import build_home_context

        MembershipPlanFactory()
        member = MemberFactory()
        mine = GuildFactory(name="Mine")
        theirs = GuildFactory(name="Theirs")
        GuildMembershipFactory(guild=mine, member=member)
        GuildAnnouncementFactory(guild=mine, title="Mine post")
        GuildAnnouncementFactory(guild=theirs, title="Theirs post")

        ctx = build_home_context(member)

        assert [a.title for a in ctx["announcements"]] == ["Mine post"]

    def it_skips_events_whose_occurrences_fall_outside_the_horizon():
        from hub.home import UPCOMING_HORIZON_DAYS, build_home_context

        MembershipPlanFactory()
        member = MemberFactory()
        guild = GuildFactory(name="Faraway")
        GuildMembershipFactory(guild=guild, member=member)
        # Non-recurring and still "upcoming" (ends in the future), but its start is
        # past the horizon window — it has no occurrence to surface, so it's skipped.
        start = timezone.now() + timedelta(days=UPCOMING_HORIZON_DAYS + 30)
        CommunityEventFactory(guild=guild, title="Way Out", starts_at=start, ends_at=start + timedelta(hours=1))

        ctx = build_home_context(member)

        assert all(item.title != "Way Out" for item in ctx["upcoming"])

    def it_returns_the_next_future_occurrence_of_a_recurring_event():
        from hub.home import build_home_context

        MembershipPlanFactory()
        member = MemberFactory()
        guild = GuildFactory(name="Recur")
        GuildMembershipFactory(guild=guild, member=member)
        # A monthly series anchored last month still recurs; the home block should
        # surface its next future occurrence, not the stale anchor.
        anchor = timezone.now() - timedelta(days=28)
        CommunityEventFactory(
            guild=guild,
            title="Monthly Meet",
            starts_at=anchor,
            ends_at=anchor + timedelta(hours=1),
            recurrence=CommunityEvent.Recurrence.MONTHLY,
        )

        ctx = build_home_context(member)

        monthly = [item for item in ctx["upcoming"] if item.title == "Monthly Meet"]
        assert monthly
        assert monthly[0].start >= timezone.now()
