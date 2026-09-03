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

    def it_sets_the_csrftoken_cookie_for_the_native_push_registration_post(client: Client):
        # The native app lands here after login; static/js/native-push.js reads the
        # csrftoken cookie to POST the device's FCM token to /push/fcm/register/. Django
        # only sets that cookie when a view uses it, so @ensure_csrf_cookie must force it
        # here or every registration POST is rejected ("CSRF cookie not set").
        _member_user("csrf")
        client.login(username="csrf", password="pass")

        response = client.get(reverse("hub_home"))

        assert response.status_code == 200
        assert "csrftoken" in response.cookies

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

        def it_renders_a_join_online_link_for_a_site_wide_event_with_a_video_url(client: Client):
            _member_user("vid_up")
            CommunityEventFactory(community=True, title="Streamed Talk", video_url="https://meet.google.com/x")
            client.login(username="vid_up", password="pass")

            resp = client.get(reverse("hub_home"))
            upcoming = resp.context["upcoming"]

            # The data carries through to the row...
            item = next(i for i in upcoming if i.title == "Streamed Talk")
            assert item.video_url == "https://meet.google.com/x"
            # ...and the dashboard actually renders the join link, not just carries the data
            # (dead field bug: a populated video_url that never reaches the page). Scoped to
            # the exact anchor markup — its own class, not a bare "Join online" substring —
            # since the site-wide changelog widget on every hub page legitimately mentions
            # "Join online" in this release's own notes.
            content = resp.content.decode()
            assert 'href="https://meet.google.com/x" class="pl-upcoming__join"' in content

        def it_omits_the_join_online_link_for_an_event_with_no_video_url(client: Client):
            _member_user("no_vid_up")
            CommunityEventFactory(community=True, title="In Person Talk", video_url="")
            client.login(username="no_vid_up", password="pass")

            resp = client.get(reverse("hub_home"))

            assert "pl-upcoming__join" not in resp.content.decode()

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
            assert b"aren't following any guilds yet" in response.content

    def describe_onboarding_card():
        def _onboard(member: Member) -> None:
            """Bring a member to onboarded: profile essentials filled + one joined guild."""
            member.profile_photo = "members/profile/a.png"
            member.about_me = "Maker."
            member.pronouns = "they/them"
            member.discord_user_id = "123456789012345678"
            member.save()
            GuildMembershipFactory(guild=GuildFactory(name="Ceramics"), member=member)

        def it_shows_the_get_started_card_for_a_new_member(client: Client):
            _member_user("newbie")
            client.login(username="newbie", password="pass")

            response = client.get(reverse("hub_home"))

            assert response.context["show_onboarding"] is True
            assert b"Get Started at Past Lives" in response.content

        def it_opts_step_links_out_of_hx_boost(client: Client):
            # Regression: the Discord step 302s to discord.com; a boosted XHR follows the
            # cross origin redirect, hits CORS, and the click silently does nothing.
            _member_user("boostoff")
            client.login(username="boostoff", password="pass")

            content = client.get(reverse("hub_home")).content

            assert content.count(b'hx-boost="false" class="pl-onboarding-step') == 4

        def it_replaces_the_old_profile_nudge(client: Client):
            _member_user("nonudge")
            client.login(username="nonudge", password="pass")

            content = client.get(reverse("hub_home")).content

            # The standalone nudge is folded into the card — it must not also ship.
            assert b"Finish setting up your profile" not in content

        def it_renders_three_rows_linking_to_each_page(client: Client):
            _member_user("rows")
            client.login(username="rows", password="pass")

            content = client.get(reverse("hub_home")).content.decode()

            assert f"{reverse('hub_user_settings')}?tab=profile" in content
            assert f"{reverse('hub_user_settings')}?tab=guilds" in content
            assert reverse("hub_guild_voting") in content

        def it_tags_the_voting_row_optional(client: Client):
            _member_user("optrow")
            client.login(username="optrow", password="pass")

            content = client.get(reverse("hub_home")).content

            assert b"Optional" in content

        def it_shows_required_only_progress(client: Client):
            _member_user("progress")
            client.login(username="progress", password="pass")

            content = client.get(reverse("hub_home")).content

            assert b"0 of 2 done" in content

        def it_renders_the_dismiss_control(client: Client):
            _member_user("dismisser")
            client.login(username="dismisser", password="pass")

            content = client.get(reverse("hub_home")).content.decode()

            assert reverse("hub_onboarding_dismiss") in content
            assert 'hx-swap="outerHTML"' in content

        def it_is_absent_once_onboarded(client: Client):
            user = _member_user("done")
            _onboard(user.member)
            client.login(username="done", password="pass")

            response = client.get(reverse("hub_home"))

            assert response.context["show_onboarding"] is False
            assert b"Get Started at Past Lives" not in response.content

        def it_is_absent_once_dismissed(client: Client):
            user = _member_user("hid")
            user.member.dismiss_onboarding()
            client.login(username="hid", password="pass")

            response = client.get(reverse("hub_home"))

            assert response.context["show_onboarding"] is False
            assert b"Get Started at Past Lives" not in response.content

    def describe_admin_tools_quicklink():
        def it_shows_the_admin_tools_quicklink_for_an_admin(client: Client):
            user = _member_user("admhome")
            user.member.fog_role = Member.FogRole.ADMIN
            user.member.save(update_fields=["fog_role"])
            client.login(username="admhome", password="pass")

            response = client.get(reverse("hub_home"))

            content = response.content.decode()
            assert '<span class="pl-quicklink__label">Admin Tools</span>' in content
            assert f'href="{reverse("hub_admin_tools")}"' in content

        def it_hides_the_admin_tools_quicklink_for_a_plain_member(client: Client):
            _member_user("plainhome")
            client.login(username="plainhome", password="pass")

            response = client.get(reverse("hub_home"))

            assert '<span class="pl-quicklink__label">Admin Tools</span>' not in response.content.decode()


def describe_build_home_context():
    def it_includes_the_onboarding_checklist_and_gate():
        from hub.home import build_home_context

        MembershipPlanFactory()
        member = MemberFactory()

        ctx = build_home_context(member)

        assert [step.key for step in ctx["onboarding"].steps] == ["profile", "guilds", "discord", "voting"]
        assert ctx["show_onboarding"] is True

    def it_hides_the_gate_for_an_onboarded_member():
        from hub.home import build_home_context

        MembershipPlanFactory()
        member = MemberFactory(
            profile_photo="members/profile/a.png",
            about_me="Maker.",
            pronouns="they/them",
            discord_user_id="123456789012345678",
        )
        GuildMembershipFactory(guild=GuildFactory(name="Metals"), member=member)

        ctx = build_home_context(member)

        assert ctx["show_onboarding"] is False
        assert ctx["onboarding"].complete is True

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
