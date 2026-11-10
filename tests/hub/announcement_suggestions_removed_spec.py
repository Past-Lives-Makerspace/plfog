"""#1157: member announcement suggestions are gone; lead-posted guild announcements are not.

The Suggest button, the suggestion page, the lead review queue, the "my suggestions" list and the
Guild Settings switch are removed, their URLs 404, their four events and their settings row are
unregistered, and whoever the composer turns away now lands on Home. The columns stay, unread,
until a later release drops them.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import NoReverseMatch, reverse

from core.events.registry import EVENTS
from core.events.settings_matrix import ROW_GROUPS
from membership.models import GuildAnnouncement, Member
from tests.membership.factories import GuildAnnouncementFactory, GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

GONE_EVENTS = {
    "guild_announcement.submitted",
    "guild_announcement.approved",
    "guild_announcement.changes_requested",
    "guild_announcement.declined",
}


def _client(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> Client:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    client = Client()
    client.force_login(user)
    return client


def describe_the_guild_page():
    def it_offers_a_member_no_suggest_button_and_plain_empty_copy():
        guild = GuildFactory(name="Zorblax Guild")
        html = _client("zsr_member").get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert 'href="/announcements/propose/' not in html
        assert "be the first to suggest one" not in html
        assert "No announcements yet." in html

    def it_still_shows_an_editor_the_public_page_button():
        guild = GuildFactory(name="Zorblax Public Guild")
        html = (
            _client("zsr_admin", fog_role=Member.FogRole.ADMIN)
            .get(reverse("hub_guild_detail", args=[guild.slug]))
            .content.decode()
        )
        assert "public page as a guest" in html

    def it_still_shows_a_lead_post():
        guild = GuildFactory(name="Zorblax Posted Guild")
        GuildAnnouncementFactory(guild=guild, title="Zorblax shop night")
        html = _client("zsr_reader").get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "Zorblax shop night" in html


def describe_guild_settings():
    def it_has_no_member_suggestions_card():
        client = _client("zsr_lead", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Zorblax Settings Guild")
        html = client.get(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=announcements").content.decode()
        # Anchored on markup, never on copy: the changelog renders on every page (STANDARDS.md §8).
        assert f"/guilds/{guild.pk}/announcement-settings/save/" not in html
        assert 'name="allow_member_announcement_suggestions"' not in html
        assert 'href="/announcements/review/"' not in html
        assert f'href="/announcements/compose/?audience=guild:{guild.pk}"' in html


def describe_the_removed_urls():
    @pytest.mark.parametrize(
        "path",
        [
            "/announcements/propose/",
            "/announcements/propose/1/edit/",
            "/announcements/1/withdraw/",
            "/announcements/review/",
            "/announcements/review/1/decision/",
            "/guilds/1/announcement-settings/save/",
        ],
    )
    def it_answers_404(path):
        client = _client("zsr_404", fog_role=Member.FogRole.ADMIN)
        assert client.get(path).status_code == 404
        assert client.post(path).status_code == 404

    @pytest.mark.parametrize(
        "name",
        [
            "hub_guild_announcement_propose",
            "hub_guild_announcement_review_queue",
            "hub_guild_announcement_settings_save",
        ],
    )
    def it_no_longer_names_them(name):
        with pytest.raises(NoReverseMatch):
            reverse(name)


def describe_the_composer_refusal():
    def it_sends_a_member_who_cannot_compose_home_with_the_reason():
        client = _client("zsr_plain")
        response = client.get(reverse("hub_compose"))
        assert response.status_code == 302
        assert response.url == reverse("hub_home")
        follow = client.get(response.url)
        assert "Your account cannot use Email Members." in follow.content.decode()


def describe_the_events():
    def it_registers_no_suggestion_event_or_settings_row():
        assert not GONE_EVENTS & {event.key for event in EVENTS}
        assert all(not (GONE_EVENTS & set(group.event_keys)) for group in ROW_GROUPS)
        assert "group.announcement_decision" not in {group.group_id for group in ROW_GROUPS}


def describe_published():
    def it_reads_only_published_rows():
        published = GuildAnnouncementFactory()
        stray = GuildAnnouncementFactory(moderation_state=GuildAnnouncement.ModerationState.PENDING)
        assert list(GuildAnnouncement.objects.published()) == [published]
        assert stray not in GuildAnnouncement.objects.published()
