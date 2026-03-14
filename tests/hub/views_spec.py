"""BDD specs for hub views."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import Client, RequestFactory

from hub.views import _get_hub_context, _get_member
from tests.membership.factories import GuildFactory, MemberFactory


@pytest.mark.django_db
def describe_get_hub_context():
    """Tests for _get_hub_context helper via the guild_voting view."""

    def it_includes_guilds_in_context(client: Client):
        User.objects.create_user(username="u1", password="pass")
        g1 = GuildFactory(name="Alpha")
        g2 = GuildFactory(name="Beta")
        client.login(username="u1", password="pass")

        response = client.get("/guilds/voting/")

        assert list(response.context["guilds"]) == [g1, g2]

    def it_returns_initials_from_member(client: Client):
        user = User.objects.create_user(
            username="u2", password="pass", first_name="Jane", last_name="Doe"
        )
        MemberFactory(user=user, full_legal_name="Jane Doe")
        client.login(username="u2", password="pass")

        response = client.get("/guilds/voting/")

        assert response.context["user_initials"] == "JD"

    def it_returns_empty_initials_when_no_member_linked(client: Client):
        User.objects.create_user(username="u3", password="pass", first_name="Jane")
        client.login(username="u3", password="pass")

        response = client.get("/guilds/voting/")

        assert response.context["user_initials"] == ""

    def it_returns_empty_initials_for_unauthenticated_request(rf: RequestFactory):
        """Calling _get_hub_context directly with an anonymous user covers the
        is_authenticated=False branch."""
        request = rf.get("/guilds/voting/")
        request.user = AnonymousUser()

        ctx = _get_hub_context(request)

        assert ctx["user_initials"] == ""


def describe_get_member():
    """Tests for _get_member helper."""

    def it_returns_none_for_unauthenticated_user(rf: RequestFactory):
        request = rf.get("/settings/profile/")
        request.user = AnonymousUser()

        result = _get_member(request)

        assert result is None


@pytest.mark.django_db
def describe_guild_voting():
    def it_requires_login(client: Client):
        response = client.get("/guilds/voting/")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_renders_voting_page_with_state_closed(client: Client):
        User.objects.create_user(username="voter", password="pass")
        client.login(username="voter", password="pass")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200
        assert response.context["state"] == "closed"


@pytest.mark.django_db
def describe_guild_detail():
    def it_requires_login(client: Client):
        guild = GuildFactory()
        response = client.get(f"/guilds/{guild.pk}/")
        assert response.status_code == 302

    def it_renders_guild_detail(client: Client):
        User.objects.create_user(username="viewer", password="pass")
        guild = GuildFactory(name="Ceramics")
        client.login(username="viewer", password="pass")

        response = client.get(f"/guilds/{guild.pk}/")

        assert response.status_code == 200
        assert response.context["guild"] == guild

    def it_returns_404_for_nonexistent_guild(client: Client):
        User.objects.create_user(username="viewer2", password="pass")
        client.login(username="viewer2", password="pass")

        response = client.get("/guilds/99999/")

        assert response.status_code == 404


@pytest.mark.django_db
def describe_profile_settings():
    def it_requires_login(client: Client):
        response = client.get("/settings/profile/")
        assert response.status_code == 302

    def it_renders_with_no_member_linked(client: Client):
        User.objects.create_user(username="nomember", password="pass")
        client.login(username="nomember", password="pass")

        response = client.get("/settings/profile/")

        assert response.status_code == 200
        assert response.context["member"] is None

    def it_renders_with_member_linked(client: Client):
        user = User.objects.create_user(username="withmember", password="pass")
        member = MemberFactory(user=user, full_legal_name="Test User")
        client.login(username="withmember", password="pass")

        response = client.get("/settings/profile/")

        assert response.status_code == 200
        assert response.context["member"] == member

    def it_updates_member_profile_on_post(client: Client):
        user = User.objects.create_user(username="editor", password="pass")
        member = MemberFactory(user=user, full_legal_name="Editor")
        client.login(username="editor", password="pass")

        response = client.post(
            "/settings/profile/",
            {"preferred_name": "Ed", "phone": "555-1234"},
            follow=True,
        )

        assert response.status_code == 200
        member.refresh_from_db()
        assert member.preferred_name == "Ed"
        assert member.phone == "555-1234"
        messages_list = list(response.context["messages"])
        assert len(messages_list) == 1
        assert "updated" in str(messages_list[0])

    def it_strips_whitespace_from_post_data(client: Client):
        user = User.objects.create_user(username="stripper", password="pass")
        member = MemberFactory(user=user, full_legal_name="Whitespace")
        client.login(username="stripper", password="pass")

        client.post(
            "/settings/profile/",
            {"preferred_name": "  Trimmed  ", "phone": "  555-0000  "},
        )

        member.refresh_from_db()
        assert member.preferred_name == "Trimmed"
        assert member.phone == "555-0000"

    def it_shows_info_message_when_no_member(client: Client):
        User.objects.create_user(username="nolink", password="pass")
        client.login(username="nolink", password="pass")

        response = client.get("/settings/profile/")

        messages_list = list(response.context["messages"])
        assert len(messages_list) == 1
        assert "not linked" in str(messages_list[0])


@pytest.mark.django_db
def describe_email_preferences():
    def it_requires_login(client: Client):
        response = client.get("/settings/emails/")
        assert response.status_code == 302

    def it_renders_email_preferences_page(client: Client):
        User.objects.create_user(username="emailuser", password="pass")
        client.login(username="emailuser", password="pass")

        response = client.get("/settings/emails/")

        assert response.status_code == 200

    def it_handles_post_and_redirects(client: Client):
        User.objects.create_user(username="emailposter", password="pass")
        client.login(username="emailposter", password="pass")

        response = client.post("/settings/emails/", {})

        assert response.status_code == 302

    def it_shows_success_message_on_post(client: Client):
        User.objects.create_user(username="emailmsg", password="pass")
        client.login(username="emailmsg", password="pass")

        response = client.post("/settings/emails/", {}, follow=True)

        messages_list = list(response.context["messages"])
        assert len(messages_list) == 1
        assert "updated" in str(messages_list[0])
