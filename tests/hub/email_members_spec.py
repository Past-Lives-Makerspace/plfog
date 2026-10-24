"""Email Members (#920) and its Release Update card (#922): the Admin Tools cards, Manage Mailing Lists."""

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


def _list_url(guild) -> str:
    return f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=announcements#mailing-list"


def describe_the_admin_tools_cards():
    def it_names_the_email_members_card_with_its_formerly_badge(client: Client):
        _login_admin(client)
        html = client.get(reverse("hub_admin_tools")).content.decode()
        assert '<span class="pl-tool-card__title">Email Members</span>' in html
        assert "Email a class, a guild or everyone, post it to Discord and send an app notification." in html
        assert "data-formerly-announcements>Formerly called Announcements</span>" in html

    def it_shows_manage_mailing_lists_to_a_guild_lead(client: Client):
        user = _login_user(client, "em_lead")
        GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]
        html = client.get(reverse("hub_admin_tools")).content.decode()
        assert f'href="{reverse("hub_mailing_lists")}" data-tool-mailing-lists' in html

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


def describe_manage_mailing_lists():
    def it_sends_someone_who_runs_one_guild_straight_to_its_list(client: Client):
        user = _login_user(client, "ml_one")
        guild = GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]
        response = client.get(reverse("hub_mailing_lists"))
        assert response.status_code == 302
        assert response["Location"] == _list_url(guild)

    def it_offers_a_picker_to_a_staffer_with_two_guilds(client: Client):
        user = _login_user(client, "ml_two")
        led = GuildFactory(name="Bronze Guild", guild_lead=user.member)  # type: ignore[attr-defined]
        staffed = GuildFactory(name="Alder Guild")
        GuildStaffMembershipFactory(guild=staffed, member=user.member)  # type: ignore[attr-defined]
        GuildFactory(name="Copper Guild")  # someone else's
        response = client.get(reverse("hub_mailing_lists"))
        assert response.status_code == 200
        html = response.content.decode()
        assert [guild for guild, _url in response.context["rows"]] == [staffed, led]
        assert (
            f'href="{_list_url(staffed)}" class="pl-mailing-lists__link" data-mailing-list-guild="{staffed.pk}"' in html
        )
        assert html.count("data-mailing-list-guild=") == 2

    def it_lists_every_active_guild_for_an_admin(client: Client):
        _login_admin(client)
        first = GuildFactory(name="Alpha Guild")
        second = GuildFactory(name="Beta Guild")
        GuildFactory(name="Gone Guild", is_active=False)
        response = client.get(reverse("hub_mailing_lists"))
        assert [guild for guild, _url in response.context["rows"]] == [first, second]

    def it_sends_someone_who_runs_no_guild_home_with_a_message(client: Client):
        _login_user(client, "ml_none")
        response = client.get(reverse("hub_mailing_lists"), follow=True)
        assert response.redirect_chain[-1][0] == reverse("hub_home")
        assert [str(m) for m in response.context["messages"]] == [
            "You don't help run a guild, so there is no mailing list to manage."
        ]

    def it_scopes_an_admin_previewing_as_a_member_to_the_guild_they_lead(client: Client):
        user = _login_admin(client, "ml_preview")
        led = GuildFactory(name="Own Guild", guild_lead=user.member)  # type: ignore[attr-defined]
        GuildFactory(name="Other Guild")
        _preview_as_member(client)
        response = client.get(reverse("hub_mailing_lists"))
        assert response.status_code == 302
        assert response["Location"] == _list_url(led)

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
