"""BDD specs for hub views."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import Client, RequestFactory

from hub.views import _get_hub_context, _get_member
from membership.models import Member
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

        assert g1 in list(response.context["guilds"])
        assert g2 in list(response.context["guilds"])

    def it_returns_initials_from_member(client: Client):
        User.objects.create_user(username="u2", password="pass", first_name="Jane", last_name="Doe")
        client.login(username="u2", password="pass")

        response = client.get("/guilds/voting/")

        assert response.context["user_initials"] == "JD"

    def it_returns_empty_initials_when_no_member_linked(client: Client):
        user = User.objects.create_user(username="u3", password="pass", first_name="Jane")
        client.login(username="u3", password="pass")
        Member.objects.filter(user=user).delete()

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
        request = rf.get("/settings/profile/")
        request.user = user

        result = _get_member(request)

        assert result == user.member

    @pytest.mark.django_db
    def it_returns_none_when_no_member_linked(rf: RequestFactory):
        user = User.objects.create_user(username="no_member", password="pass")
        Member.objects.filter(user=user).delete()
        user = User.objects.get(pk=user.pk)  # Refresh to clear cached .member
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
    def it_is_accessible_to_anonymous_guests(client: Client):
        response = client.get("/members/")
        assert response.status_code == 200

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

    def it_shows_pronouns_in_directory(client: Client):
        User.objects.create_user(username="viewer", password="pass")
        MemberFactory(full_legal_name="Sam", show_in_directory=True, pronouns=Member.Pronouns.THEY_THEM)
        client.login(username="viewer", password="pass")

        response = client.get("/members/")

        assert "they/them" in response.content.decode()

    def it_hides_prefer_not_to_share_pronouns(client: Client):
        User.objects.create_user(username="viewer2", password="pass")
        MemberFactory(full_legal_name="Alex", show_in_directory=True, pronouns=Member.Pronouns.PREFER_NOT)
        client.login(username="viewer2", password="pass")

        response = client.get("/members/")

        assert "prefer not to share" not in response.content.decode()

    def it_does_not_trigger_n_plus_1_on_primary_email(client: Client):
        """Regression guard for the member.primary_email N+1.

        The template accesses ``member.primary_email`` several times per row. Without
        a ``Prefetch`` of the primary allauth EmailAddress, this would fire N queries
        per member. See ``hub.views.member_directory`` and
        docs/superpowers/specs/2026-04-07-user-email-aliases-design.md.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        # Five members with linked users → each has an auto-created primary EmailAddress.
        for i in range(5):
            user = User.objects.create_user(username=f"m{i}", email=f"m{i}@example.com")
            member = user.member
            member.show_in_directory = True
            member.full_legal_name = f"Member {i}"
            member.save(update_fields=["show_in_directory", "full_legal_name"])

        viewer = User.objects.create_user(username="viewer-n1", password="pass")
        client.force_login(viewer)

        with CaptureQueriesContext(connection) as ctx:
            response = client.get("/members/")

        assert response.status_code == 200
        # N+1 would show ~4 queries per member. Prefetch should give us 1 query total
        # for the primary EmailAddress rows, regardless of member count.
        email_q = [q for q in ctx.captured_queries if "account_emailaddress" in q["sql"].lower()]
        assert len(email_q) <= 2, f"N+1 on EmailAddress: {len(email_q)} queries for 5 members"


@pytest.mark.django_db
def describe_guild_detail():
    def it_is_accessible_to_anonymous_guests(client: Client):
        guild = GuildFactory()
        response = client.get(f"/guilds/{guild.pk}/")
        assert response.status_code == 200

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
def describe_user_settings():
    def it_requires_login(client: Client):
        response = client.get("/settings/")
        assert response.status_code == 302

    def it_renders_both_profile_and_email_prefs_forms(client: Client):
        user = User.objects.create_user(username="withmember", password="pass")
        client.login(username="withmember", password="pass")

        response = client.get("/settings/")

        assert response.status_code == 200
        assert response.context["member"] == user.member
        assert response.context["profile_form"] is not None
        assert response.context["prefs_form"] is not None
        assert "add_email_form" in response.context
        assert "email_addresses" in response.context

    def it_defaults_to_profile_tab(client: Client):
        User.objects.create_user(username="tabdefault", password="pass")
        client.login(username="tabdefault", password="pass")

        response = client.get("/settings/")

        assert response.context["active_tab"] == "profile"

    def it_honors_tab_query_param(client: Client):
        User.objects.create_user(username="tabemails", password="pass")
        client.login(username="tabemails", password="pass")

        response = client.get("/settings/?tab=emails")

        assert response.context["active_tab"] == "emails"

    def it_falls_back_to_profile_when_tab_param_is_not_whitelisted(client: Client):
        """Regression: ``active_tab`` flows into an Alpine x-data JS expression,
        so raw user input must not reach the template — arbitrary values are
        coerced back to ``profile`` to prevent XSS."""
        User.objects.create_user(username="xssguard", password="pass")
        client.login(username="xssguard", password="pass")

        response = client.get("/settings/?tab=%27%2Balert(1)%2B%27")

        assert response.context["active_tab"] == "profile"
        # And the raw payload never lands in the rendered HTML.
        assert b"alert(1)" not in response.content

    def it_renders_with_no_member_linked(client: Client):
        user = User.objects.create_user(username="nomember", password="pass")
        client.login(username="nomember", password="pass")
        Member.objects.filter(user=user).delete()

        response = client.get("/settings/")

        assert response.status_code == 200
        assert response.context["member"] is None
        assert response.context["profile_form"] is None

    def it_shows_info_message_when_no_member(client: Client):
        user = User.objects.create_user(username="nolink", password="pass")
        client.login(username="nolink", password="pass")
        Member.objects.filter(user=user).delete()

        response = client.get("/settings/")

        messages_list = list(response.context["messages"])
        assert any("not linked" in str(m) for m in messages_list)

    def it_updates_member_profile_on_post_with_profile_form_id(client: Client):
        user = User.objects.create_user(username="editor", password="pass")
        member = user.member
        client.login(username="editor", password="pass")

        response = client.post(
            "/settings/",
            {"form_id": "profile", "preferred_name": "Ed", "phone": "555-1234"},
            follow=True,
        )

        assert response.status_code == 200
        member.refresh_from_db()
        assert member.preferred_name == "Ed"
        assert member.phone == "555-1234"
        assert any("updated" in str(m) for m in response.context["messages"])

    def it_logs_site_activity_on_successful_profile_update(client: Client):
        from core.models import SiteActivity

        user = User.objects.create_user(username="activityeditor", password="pass")
        member = user.member
        client.login(username="activityeditor", password="pass")

        client.post(
            "/settings/",
            {"form_id": "profile", "preferred_name": "Ed", "phone": "555-1234"},
            follow=True,
        )

        assert SiteActivity.objects.filter(
            kind=SiteActivity.Kind.PROFILE_UPDATED,
            actor=user,
            target_id=member.pk,
        ).exists()

    def it_strips_whitespace_from_post_data(client: Client):
        user = User.objects.create_user(username="stripper", password="pass")
        member = user.member
        client.login(username="stripper", password="pass")

        client.post(
            "/settings/",
            {"form_id": "profile", "preferred_name": "  Trimmed  ", "phone": "  555-0000  "},
        )

        member.refresh_from_db()
        assert member.preferred_name == "Trimmed"
        assert member.phone == "555-0000"

    def it_rejects_phone_exceeding_max_length(client: Client):
        User.objects.create_user(username="longphone", password="pass")
        client.login(username="longphone", password="pass")

        response = client.post(
            "/settings/",
            {"form_id": "profile", "preferred_name": "Ok", "phone": "x" * 21},
        )

        assert response.status_code == 200
        assert response.context["profile_form"].errors

    def it_saves_pronouns(client: Client):
        user = User.objects.create_user(username="pronounuser", password="pass")
        member = user.member
        client.login(username="pronounuser", password="pass")

        client.post(
            "/settings/",
            {
                "form_id": "profile",
                "preferred_name": "",
                "pronouns": "she/her",
                "phone": "",
                "discord_handle": "",
                "other_contact_info": "",
                "about_me": "",
                "show_in_directory": False,
            },
        )

        member.refresh_from_db()
        assert member.pronouns == "she/her"

    def it_errors_and_redirects_when_profile_post_has_no_member(client: Client):
        user = User.objects.create_user(username="profilenolink", password="pass")
        client.login(username="profilenolink", password="pass")
        Member.objects.filter(user=user).delete()

        response = client.post("/settings/", {"form_id": "profile", "preferred_name": "X"}, follow=True)

        assert response.status_code == 200
        assert any("not linked" in str(m) for m in response.context["messages"])

    def it_re_renders_email_prefs_form_on_validation_error(client: Client, monkeypatch: pytest.MonkeyPatch):
        User.objects.create_user(username="emailinvalid2", password="pass")
        client.login(username="emailinvalid2", password="pass")

        from hub import forms

        original_init = forms.EmailPreferencesForm.__init__

        def patched_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            if args:
                self._errors = {"voting_results": ["Forced error"]}

        monkeypatch.setattr(forms.EmailPreferencesForm, "__init__", patched_init)

        response = client.post("/settings/", {"form_id": "email_prefs"})

        assert response.status_code == 200
        assert response.context["prefs_form"].errors

    def it_handles_email_prefs_post_and_redirects_to_emails_tab(client: Client):
        User.objects.create_user(username="emailposter", password="pass")
        client.login(username="emailposter", password="pass")

        response = client.post("/settings/", {"form_id": "email_prefs"})

        assert response.status_code == 302
        assert "tab=emails" in response.url

    def it_shows_success_message_on_email_prefs_post(client: Client):
        User.objects.create_user(username="emailmsg", password="pass")
        client.login(username="emailmsg", password="pass")

        response = client.post("/settings/", {"form_id": "email_prefs"}, follow=True)

        assert any("preferences updated" in str(m).lower() for m in response.context["messages"])

    def it_seeds_primary_verified_state_from_primary_email(client: Client):
        from allauth.account.models import EmailAddress

        user = User.objects.create_user(username="primaryverified", email="v@example.com", password="pass")
        EmailAddress.objects.filter(user=user).delete()
        EmailAddress.objects.create(user=user, email="v@example.com", verified=True, primary=True)
        client.login(username="primaryverified", password="pass")

        response = client.get("/settings/?tab=emails")

        assert response.context["primary_verified_json"] == "true"

    def it_flags_unverified_primary_so_resend_button_shows(client: Client):
        from allauth.account.models import EmailAddress

        user = User.objects.create_user(username="unverifiedprimary", email="u@example.com", password="pass")
        EmailAddress.objects.filter(user=user).delete()
        EmailAddress.objects.create(user=user, email="u@example.com", verified=False, primary=True)
        client.login(username="unverifiedprimary", password="pass")

        response = client.get("/settings/?tab=emails")

        assert response.context["primary_verified_json"] == "false"

    def it_lists_user_email_addresses_in_context(client: Client):
        from allauth.account.models import EmailAddress

        user = User.objects.create_user(username="emaillist", email="primary@example.com", password="pass")
        # The signup signal may auto-create an EmailAddress row; clear to start deterministic.
        EmailAddress.objects.filter(user=user).delete()
        EmailAddress.objects.create(user=user, email="primary@example.com", verified=True, primary=True)
        EmailAddress.objects.create(user=user, email="alias@example.com", verified=True, primary=False)
        client.login(username="emaillist", password="pass")

        response = client.get("/settings/?tab=emails")

        addrs = list(response.context["email_addresses"])
        assert {a.email for a in addrs} == {"primary@example.com", "alias@example.com"}


@pytest.mark.django_db
def describe_legacy_settings_redirects():
    def it_redirects_old_profile_path_to_user_settings(client: Client):
        User.objects.create_user(username="legacyprofile", password="pass")
        client.login(username="legacyprofile", password="pass")

        response = client.get("/settings/profile/")

        assert response.status_code == 302
        assert response.url == "/settings/"

    def it_redirects_old_emails_path_to_emails_tab(client: Client):
        User.objects.create_user(username="legacyemails", password="pass")
        client.login(username="legacyemails", password="pass")

        response = client.get("/settings/emails/")

        assert response.status_code == 302
        assert response.url == "/settings/?tab=emails"

    def it_redirects_allauth_account_email_get_to_emails_tab(client: Client):
        User.objects.create_user(username="legacyallauth", password="pass")
        client.login(username="legacyallauth", password="pass")

        response = client.get("/accounts/email/")

        assert response.status_code == 302
        assert response.url == "/settings/?tab=emails"

    def it_sends_email_action_post_back_to_emails_tab(client: Client):
        """After allauth's EmailView handles add/remove/resend/primary, the user
        should land on the Emails tab — not the Profile tab."""
        from allauth.account.models import EmailAddress

        user = User.objects.create_user(username="emailaction", email="me@example.com", password="pass")
        EmailAddress.objects.filter(user=user).delete()
        EmailAddress.objects.create(user=user, email="me@example.com", verified=True, primary=True)
        client.login(username="emailaction", password="pass")

        response = client.post("/accounts/email/", {"action_add": "", "email": "alias@example.com"})

        assert response.status_code == 302
        assert response.url == "/settings/?tab=emails"


_PROFILE_PHOTO_DELETE_URL = "/settings/profile-photo/delete/"


def _tiny_png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf"
        b"\xc0\x00\x00\x00\x03\x00\x01\x5b\x0d\xc1\x6a\x00\x00\x00\x00IEND\xae"
        b"B`\x82"
    )


@pytest.mark.django_db
def describe_profile_photo_delete():
    """Tests for the POST-only profile photo clearing endpoint."""

    def it_requires_login(client: Client):
        response = client.post(_PROFILE_PHOTO_DELETE_URL)

        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_rejects_non_POST_requests(client: Client):
        User.objects.create_user(username="gets", password="pass")
        client.login(username="gets", password="pass")

        response = client.get(_PROFILE_PHOTO_DELETE_URL)

        assert response.status_code == 405

    def it_errors_when_user_has_no_linked_member(client: Client):
        user = User.objects.create_user(username="unlinked", password="pass")
        Member.objects.filter(user=user).delete()
        # Refresh so the cached .member attribute on user is cleared.
        User.objects.get(pk=user.pk)
        client.login(username="unlinked", password="pass")

        response = client.post(_PROFILE_PHOTO_DELETE_URL, follow=True)

        assert response.status_code == 200
        msgs = [str(m) for m in response.context["messages"]]
        assert any("not linked" in m for m in msgs)

    def it_clears_the_profile_photo_and_redirects_to_profile_tab(client: Client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        user = User.objects.create_user(username="hasphoto", password="pass")
        member = user.member
        member.profile_photo = SimpleUploadedFile("me.png", _tiny_png_bytes(), content_type="image/png")
        member.save()
        assert member.profile_photo
        client.login(username="hasphoto", password="pass")

        response = client.post(_PROFILE_PHOTO_DELETE_URL)

        assert response.status_code == 302
        assert response.url.endswith("/settings/?tab=profile")
        member.refresh_from_db()
        assert not member.profile_photo

    def it_is_a_noop_when_no_photo_is_set(client: Client):
        User.objects.create_user(username="nophoto", password="pass")
        client.login(username="nophoto", password="pass")

        response = client.post(_PROFILE_PHOTO_DELETE_URL)

        assert response.status_code == 302
        assert response.url.endswith("/settings/?tab=profile")
