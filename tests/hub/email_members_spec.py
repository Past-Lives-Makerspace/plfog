"""Email Members (#920) and its Release Update card (#922): the Admin Tools cards, the Mailing Lists tab."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from hub.view_as import ROLE_MEMBER, SESSION_ROLE_KEY
from membership.models import Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory

pytestmark = pytest.mark.django_db


def _login_user(client: Client, username: str) -> User:
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user


def _login_admin(client: Client, username: str = "em_admin") -> User:
    user = User.objects.create_superuser(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user


def _preview_as_member(client: Client) -> None:
    session = client.session
    session[SESSION_ROLE_KEY] = ROLE_MEMBER
    session.save()


def _tab_url() -> str:
    return f"{reverse('hub_announcements')}?tab=mailing-lists"


def _list_url(guild) -> str:
    return f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=announcements#mailing-list"


def describe_the_admin_tools_cards():
    def it_names_the_email_members_card_with_its_formerly_badge(client: Client):
        _login_admin(client)
        html = client.get(reverse("hub_admin_tools")).content.decode()
        assert '<span class="pl-tool-card__title">Email Members' in html
        assert "Email a class, a guild or everyone, post it to Discord and send an app notification." in html
        assert "data-formerly-announcements>Formerly called Announcements</span>" in html

    def it_shows_manage_mailing_lists_to_a_guild_lead(client: Client):
        user = _login_user(client, "em_lead")
        GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]
        html = client.get(reverse("hub_admin_tools")).content.decode()
        assert f'href="{_tab_url()}" data-tool-mailing-lists' in html

    def it_hides_manage_mailing_lists_from_an_instructor_who_runs_no_guild(client: Client):
        user = _login_user(client, "em_teacher")
        member = user.member  # type: ignore[attr-defined]
        member.instructor_slug = "em-teacher"
        member.save(update_fields=["instructor_slug"])
        html = client.get(reverse("hub_admin_tools")).content.decode()
        assert "data-tool-email-members" in html
        assert "data-tool-mailing-lists" not in html

    def it_shows_manage_mailing_lists_to_a_guild_officer_by_their_tier(client: Client):
        user = _login_user(client, "em_officer")
        member = user.member  # type: ignore[attr-defined]
        member.instructor_slug = "em-officer"
        member.fog_role = Member.FogRole.GUILD_OFFICER
        member.save(update_fields=["instructor_slug", "fog_role"])
        member.sync_user_permissions()
        GuildFactory()  # an officer may edit every active guild, though they lead none
        html = client.get(reverse("hub_admin_tools")).content.decode()
        assert "data-tool-mailing-lists" in html


def describe_the_mailing_lists_tab():
    def it_lists_the_one_guild_a_lead_runs(client: Client):
        user = _login_user(client, "ml_one")
        guild = GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]
        response = client.get(_tab_url())
        assert response.status_code == 200
        html = response.content.decode()
        assert response.context["mailing_lists"] == [(guild, _list_url(guild))]
        assert 'aria-current="page" data-announcements-tab="mailing-lists">Mailing Lists' in html
        assert f'href="{_list_url(guild)}" class="pl-mailing-lists__link" data-mailing-list-guild="{guild.pk}"' in html

    def it_lists_a_staffers_two_guilds_by_name(client: Client):
        user = _login_user(client, "ml_two")
        led = GuildFactory(name="Bronze Guild", guild_lead=user.member)  # type: ignore[attr-defined]
        staffed = GuildFactory(name="Alder Guild")
        GuildStaffMembershipFactory(guild=staffed, member=user.member)  # type: ignore[attr-defined]
        GuildFactory(name="Copper Guild")  # someone else's
        response = client.get(_tab_url())
        html = response.content.decode()
        assert [guild for guild, _url in response.context["mailing_lists"]] == [staffed, led]
        assert html.count("data-mailing-list-guild=") == 2
        assert 'data-announcements-count="mailing-lists">2</span>' in html

    def it_lists_every_active_guild_for_an_admin(client: Client):
        _login_admin(client)
        first = GuildFactory(name="Alpha Guild")
        second = GuildFactory(name="Beta Guild")
        GuildFactory(name="Gone Guild", is_active=False)
        response = client.get(_tab_url())
        assert [guild for guild, _url in response.context["mailing_lists"]] == [first, second]

    def it_keeps_drafts_and_sent_beside_it(client: Client):
        user = _login_user(client, "ml_tabs")
        GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]
        html = client.get(_tab_url()).content.decode()
        assert 'data-announcements-tab="drafts"' in html
        assert 'data-announcements-tab="sent"' in html
        assert "data-announcements-table" not in html

    def it_is_not_offered_to_an_instructor_who_runs_no_guild(client: Client):
        user = _login_user(client, "ml_teacher")
        member = user.member  # type: ignore[attr-defined]
        member.instructor_slug = "ml-teacher"
        member.save(update_fields=["instructor_slug"])
        response = client.get(_tab_url())
        html = response.content.decode()
        assert response.context["tab"] == "drafts"
        assert 'data-announcements-tab="mailing-lists"' not in html
        assert "data-mailing-lists" not in html

    def it_gives_an_officer_on_no_guild_staff_the_tab_alone(client: Client):
        user = _login_user(client, "ml_officer")
        member = user.member  # type: ignore[attr-defined]
        member.fog_role = Member.FogRole.GUILD_OFFICER
        member.save(update_fields=["fog_role"])
        member.sync_user_permissions()
        guild = GuildFactory()
        response = client.get(reverse("hub_announcements"))
        html = response.content.decode()
        assert response.status_code == 200
        assert response.context["tab"] == "mailing-lists"
        assert f'data-mailing-list-guild="{guild.pk}"' in html
        assert 'data-announcements-tab="drafts"' not in html
        assert "New email" not in html

    def it_still_refuses_someone_who_runs_no_guild_and_cannot_email(client: Client):
        _login_user(client, "ml_none")
        response = client.get(_tab_url())
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_guild_announcement_propose")

    def it_scopes_an_admin_previewing_as_a_member_to_the_guild_they_lead(client: Client):
        user = _login_admin(client, "ml_preview")
        led = GuildFactory(name="Own Guild", guild_lead=user.member)  # type: ignore[attr-defined]
        GuildFactory(name="Other Guild")
        _preview_as_member(client)
        response = client.get(_tab_url())
        assert response.context["mailing_lists"] == [(led, _list_url(led))]

    def it_hides_the_release_update_card_on_this_tab(client: Client):
        _login_admin(client)
        GuildFactory()
        assert "data-release-entry" not in client.get(_tab_url()).content.decode()

    def it_sends_the_old_manage_mailing_lists_address_to_the_tab(client: Client):
        _login_user(client, "ml_old")
        response = client.get(reverse("hub_mailing_lists"))
        assert response.status_code == 302
        assert response["Location"] == _tab_url()

    def it_gives_the_list_an_anchor_in_guild_settings(client: Client):
        user = _login_user(client, "ml_anchor")
        guild = GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]
        html = client.get(reverse("hub_guild_edit", args=[guild.pk])).content.decode()
        assert '<div class="hub-card" id="mailing-list"' in html


def describe_the_release_update_card():
    def it_shows_admins_the_draft_from_latest_release_link(client: Client):
        _login_admin(client)
        html = client.get(reverse("hub_announcements")).content.decode()
        assert f'href="{reverse("hub_email_members_release")}" class="hub-btn hub-btn--sm" data-draft-release' in html

    def it_is_not_shown_to_a_guild_lead(client: Client):
        user = _login_user(client, "rel_lead")
        GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]
        response = client.get(reverse("hub_announcements"))
        assert response.status_code == 200
        assert "data-draft-release" not in response.content.decode()

    def it_hides_the_card_from_an_admin_previewing_as_a_member(client: Client):
        user = _login_admin(client, "rel_preview")
        GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]  # so the preview may still open the page
        _preview_as_member(client)
        response = client.get(reverse("hub_announcements"))
        assert response.status_code == 200
        assert "data-draft-release" not in response.content.decode()

    def it_titles_the_release_page_with_the_page_header(client: Client):
        _login_admin(client, "rel_header")
        html = client.get(reverse("hub_email_members_release")).content.decode()
        assert '<h1 class="hub-page-title"' in html
        assert "Release Update</h1>" in html

    def it_refuses_the_release_page_to_a_guild_lead(client: Client):
        user = _login_user(client, "rel_lead2")
        GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]
        assert client.get(reverse("hub_email_members_release")).status_code == 403
