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
        user = User.objects.create_user(username="u2", password="pass", first_name="Jane", last_name="Doe")
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
    """Tests for _get_member helper (callers are @login_required)."""

    @pytest.mark.django_db
    def it_returns_member_when_linked(rf: RequestFactory):
        user = User.objects.create_user(username="has_member", password="pass")
        member = MemberFactory(user=user, full_legal_name="Has Member")
        request = rf.get("/settings/profile/")
        request.user = user

        result = _get_member(request)

        assert result == member

    @pytest.mark.django_db
    def it_returns_none_when_no_member_linked(rf: RequestFactory):
        user = User.objects.create_user(username="no_member", password="pass")
        request = rf.get("/settings/profile/")
        request.user = user

        result = _get_member(request)

        assert result is None


@pytest.mark.django_db
def describe_guild_voting():
    def it_requires_login(client: Client):
        response = client.get("/guilds/voting/")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_renders_voting_page(client: Client):
        User.objects.create_user(username="voter", password="pass")
        client.login(username="voter", password="pass")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200


@pytest.mark.django_db
def describe_member_directory():
    def it_requires_login(client: Client):
        response = client.get("/members/")
        assert response.status_code == 302

    def it_lists_active_opted_in_members(client: Client):
        User.objects.create_user(username="viewer", password="pass")
        m1 = MemberFactory(full_legal_name="Alice", status="active", show_in_directory=True)
        m2 = MemberFactory(full_legal_name="Bob", status="active", show_in_directory=True)
        MemberFactory(full_legal_name="Hidden", status="active", show_in_directory=False)
        MemberFactory(full_legal_name="Former", status="former", show_in_directory=True)
        client.login(username="viewer", password="pass")

        response = client.get("/members/")

        assert response.status_code == 200
        members = list(response.context["members"])
        assert m1 in members
        assert m2 in members
        assert len(members) == 2


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
        assert response.context["form"] is None

    def it_renders_with_member_linked(client: Client):
        user = User.objects.create_user(username="withmember", password="pass")
        member = MemberFactory(user=user, full_legal_name="Test User")
        client.login(username="withmember", password="pass")

        response = client.get("/settings/profile/")

        assert response.status_code == 200
        assert response.context["member"] == member
        assert response.context["form"] is not None

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

    def it_rejects_phone_exceeding_max_length(client: Client):
        user = User.objects.create_user(username="longphone", password="pass")
        MemberFactory(user=user, full_legal_name="Long Phone")
        client.login(username="longphone", password="pass")

        response = client.post(
            "/settings/profile/",
            {"preferred_name": "Ok", "phone": "x" * 21},
        )

        assert response.status_code == 200
        assert response.context["form"].errors


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
        assert response.context["form"] is not None

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

    def it_re_renders_form_on_validation_error(client: Client, monkeypatch: pytest.MonkeyPatch):
        User.objects.create_user(username="emailinvalid", password="pass")
        client.login(username="emailinvalid", password="pass")

        from hub import forms

        original_init = forms.EmailPreferencesForm.__init__

        def patched_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            if args:  # Only when bound (POST data passed)
                self._errors = {"voting_results": ["Forced error"]}

        monkeypatch.setattr(forms.EmailPreferencesForm, "__init__", patched_init)

        response = client.post("/settings/emails/", {})

        assert response.status_code == 200
