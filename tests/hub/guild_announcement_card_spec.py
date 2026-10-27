"""BDD specs for #952: the guild page shows one announcement card, and a Past Announcements page lists them all.

Assertions anchor on markup (data attributes, URLs) and factory strings no changelog entry could
contain, because the changelog renders on every page (STANDARDS.md 8).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import dateformat, timezone

from membership.models import GuildAnnouncement, Member
from tests.hub.guild_guest_surface_spec import GUILDS_HOST, GUILDS_SETTINGS
from tests.membership.factories import GuildAnnouncementFactory, GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

LONG_BODY = "Hello Writers!\n\n" + "A line about the next meetup.\n\n" * 12 + "- Penina"


def _posted(days_ago: int, **kwargs: object) -> GuildAnnouncement:
    """An announcement published ``days_ago`` days before now (published_at is auto_now_add)."""
    announcement = GuildAnnouncementFactory(**kwargs)
    GuildAnnouncement.objects.filter(pk=announcement.pk).update(published_at=timezone.now() - timedelta(days=days_ago))
    announcement.refresh_from_db()
    return announcement


def _sign_in_admin(client: Client) -> None:
    """An admin, who can edit every guild, so the page shows a lead's empty state."""
    MembershipPlanFactory()
    user = User.objects.create_user(username="zqadmin", email="zqadmin@example.com", password="pw")
    user.member.fog_role = Member.FogRole.ADMIN
    user.member.save(update_fields=["fog_role"])
    user.member.sync_user_permissions()
    client.force_login(user)


def _guild_page(client: Client, guild: object) -> str:
    return client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()


def _past_page(client: Client, guild: object, query: str = "") -> str:
    return client.get(reverse("hub_guild_announcements", args=[guild.slug]) + query).content.decode()


def describe_guild_page_announcement():
    def it_shows_only_the_newest_published_unexpired_one(client: Client):
        guild = GuildFactory()
        older = _posted(9, guild=guild, title="Zq older live post")
        newest = _posted(2, guild=guild, title="Zq newest live post")
        _posted(1, guild=guild, title="Zq newer but expired", expires_at=timezone.localdate() - timedelta(days=1))
        _posted(0, guild=guild, title="Zq newer but pending", pending=True)

        html = _guild_page(client, guild)

        assert html.count("data-guild-announcement=") == 1
        assert f'data-guild-announcement="{newest.pk}"' in html
        assert "Zq newest live post" in html
        # The title can still appear in the guild's activity feed; the card is what moved.
        assert f'data-guild-announcement="{older.pk}"' not in html

    def it_renders_the_body_as_paragraphs_and_a_list(client: Client):
        guild = GuildFactory()
        _posted(1, guild=guild, body="Zq hello\n\n- Zq one\n- Zq two\n\nZq bye")

        html = _guild_page(client, guild)

        assert "<p>Zq hello</p><ul><li>Zq one</li><li>Zq two</li></ul><p>Zq bye</p>" in html

    def it_folds_a_long_body_behind_read_more(client: Client):
        guild = GuildFactory()
        _posted(1, guild=guild, body=LONG_BODY)

        html = _guild_page(client, guild)

        assert "pl-guild-announcement__body--clamp" in html
        assert "data-announcement-more" in html

    def it_shows_a_short_body_whole_with_no_toggle(client: Client):
        guild = GuildFactory()
        _posted(1, guild=guild, body="Zq short news")

        html = _guild_page(client, guild)

        assert "pl-guild-announcement__body--clamp" not in html
        assert "data-announcement-more" not in html

    def it_has_no_past_link_with_a_single_announcement(client: Client):
        guild = GuildFactory()
        _posted(1, guild=guild)
        _posted(2, guild=guild, pending=True)

        assert "data-past-announcements" not in _guild_page(client, guild)

    def it_links_to_past_announcements_when_an_expired_one_makes_two(client: Client):
        guild = GuildFactory()
        _posted(1, guild=guild)
        _posted(30, guild=guild, expires_at=timezone.localdate() - timedelta(days=20))

        html = _guild_page(client, guild)

        assert f'href="{reverse("hub_guild_announcements", args=[guild.slug])}"' in html
        assert "data-past-announcements" in html

    def it_shows_a_guest_no_current_announcements_and_the_past_link_when_all_expired(client: Client):
        guild = GuildFactory()
        expired = timezone.localdate() - timedelta(days=5)
        _posted(10, guild=guild, expires_at=expired)
        _posted(20, guild=guild, expires_at=expired)

        html = _guild_page(client, guild)

        assert "data-guild-announcement=" not in html
        assert "data-no-current-announcements" in html
        assert "No announcements yet." not in html
        assert html.index("data-no-current-announcements") < html.index("data-past-announcements")

    def it_says_no_current_announcements_to_a_lead_when_the_only_post_expired(client: Client):
        guild = GuildFactory()
        _posted(10, guild=guild, expires_at=timezone.localdate() - timedelta(days=5))
        _sign_in_admin(client)

        html = _guild_page(client, guild)

        assert "data-no-current-announcements" in html
        assert "Use Email Members to post one." not in html
        assert "data-past-announcements" not in html

    def it_keeps_the_empty_copy_when_the_guild_has_never_posted(client: Client):
        guild = GuildFactory()
        _posted(1, guild=guild, pending=True)
        _sign_in_admin(client)

        html = _guild_page(client, guild)

        assert "No announcements yet. Use Email Members to post one." in html
        assert "data-no-current-announcements" not in html

    def it_hides_the_section_from_a_guest_when_the_guild_has_none(client: Client):
        guild = GuildFactory()

        html = _guild_page(client, guild)

        assert "data-past-announcements" not in html
        assert "No announcements yet." not in html


def describe_body_is_long():
    def it_is_long_over_the_character_limit():
        assert GuildAnnouncement(body="x" * (GuildAnnouncement.LONG_BODY_CHARS + 1)).body_is_long

    def it_is_long_at_the_line_limit():
        assert GuildAnnouncement(body="a\n" * GuildAnnouncement.LONG_BODY_LINES).body_is_long

    def it_is_short_under_both():
        assert not GuildAnnouncement(body="One line of news.\nAnd a second.").body_is_long


def describe_past_announcements_page():
    def it_lists_every_published_one_newest_first_with_expired_ones(client: Client):
        guild = GuildFactory()
        _posted(30, guild=guild, title="Zq oldest", expires_at=timezone.localdate() - timedelta(days=20))
        _posted(10, guild=guild, title="Zq middle")
        _posted(1, guild=guild, title="Zq newest")

        html = _past_page(client, guild)

        assert html.index("Zq newest") < html.index("Zq middle") < html.index("Zq oldest")
        assert html.count("data-guild-announcement=") == 3

    def it_shows_each_title_and_date(client: Client):
        guild = GuildFactory()
        announcement = _posted(3, guild=guild, title="Zq dated post")

        html = _past_page(client, guild)

        assert "Zq dated post" in html
        assert dateformat.format(timezone.localtime(announcement.published_at), "M j, Y") in html

    def it_never_shows_proposals_that_are_not_published(client: Client):
        guild = GuildFactory()
        _posted(1, guild=guild, title="Zq pending one", pending=True)
        _posted(2, guild=guild, title="Zq sent back", changes_requested=True)
        _posted(3, guild=guild, title="Zq turned down", declined=True)
        _posted(4, guild=guild, title="Zq live one")

        html = _past_page(client, guild)

        assert "Zq live one" in html
        for title in ("Zq pending one", "Zq sent back", "Zq turned down"):
            assert title not in html

    def it_leaves_out_other_guilds_posts(client: Client):
        guild = GuildFactory()
        _posted(1, guild=GuildFactory(), title="Zq other guild post")

        html = _past_page(client, guild)

        assert "Zq other guild post" not in html
        assert "data-announcements-empty" in html

    def it_renders_bodies_with_lists(client: Client):
        guild = GuildFactory()
        _posted(1, guild=guild, body="- Zq a\n- Zq b")

        assert "<ul><li>Zq a</li><li>Zq b</li></ul>" in _past_page(client, guild)

    def it_opens_for_an_anonymous_visitor_on_the_members_host(client: Client):
        guild = GuildFactory()

        response = client.get(reverse("hub_guild_announcements", args=[guild.slug]))

        assert response.status_code == 200
        assert f'href="{reverse("hub_guild_detail", args=[guild.slug])}"' in response.content.decode()

    def it_opens_on_the_guilds_surface_like_the_guild_page(client: Client):
        guild = GuildFactory()
        _posted(1, guild=guild, title="Zq guest readable")

        with override_settings(**GUILDS_SETTINGS):
            response = client.get(f"/guilds/{guild.slug}/announcements/", HTTP_HOST=GUILDS_HOST)

        assert response.status_code == 200
        assert "Zq guest readable" in response.content.decode()

    def it_is_404_for_an_unknown_guild(client: Client):
        assert client.get(reverse("hub_guild_announcements", args=["no-such-guild"])).status_code == 404

    def it_breaks_a_tie_on_published_time_by_newest_row(client: Client):
        guild = GuildFactory()
        first = _posted(3, guild=guild, title="Zq tie first")
        second = _posted(3, guild=guild, title="Zq tie second")
        GuildAnnouncement.objects.filter(pk=second.pk).update(published_at=first.published_at)

        html = _past_page(client, guild)

        assert html.index("Zq tie second") < html.index("Zq tie first")

    def it_pages_ten_at_a_time(client: Client):
        guild = GuildFactory()
        for days in range(11):
            _posted(days + 1, guild=guild, title=f"Zq post {days:02d}")

        first = _past_page(client, guild)
        second = _past_page(client, guild, "?page=2")

        assert first.count("data-guild-announcement=") == 10
        assert "data-announcement-pages" in first
        assert "?page=2" in first
        assert second.count("data-guild-announcement=") == 1
        assert "Zq post 10" in second
        assert "?page=1" in second
