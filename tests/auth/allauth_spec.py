import pytest
from django.contrib.auth.models import User
from django.template.loader import render_to_string

from core.models import Invite, SiteConfiguration
from membership.models import Member
from plfog.version import VERSION
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def describe_allauth_urls():
    def it_login_page_returns_200(client):
        response = client.get("/accounts/login/")
        assert response.status_code == 200

    def it_logout_page_uses_custom_template(client):
        user = User.objects.create_user(username="logoutuser", email="logout@example.com", password="pass")
        client.force_login(user)

        response = client.get("/accounts/logout/")

        assert response.status_code == 200
        template_names = [t.name for t in response.templates]
        assert "account/logout.html" in template_names
        assert "Are you sure you want to end your session?" in response.content.decode()

    def it_signup_page_returns_200(client):
        # Default is invite_only, so signup_closed is rendered
        response = client.get("/accounts/signup/")
        assert response.status_code == 200

    def it_login_page_uses_custom_template(client):
        response = client.get("/accounts/login/")
        template_names = [t.name for t in response.templates]
        assert "account/login.html" in template_names

    def it_signup_page_uses_custom_template_when_open(client):
        config = SiteConfiguration.load()
        config.registration_mode = SiteConfiguration.RegistrationMode.OPEN
        config.save()

        response = client.get("/accounts/signup/")
        template_names = [t.name for t in response.templates]
        assert "account/signup.html" in template_names

    def it_login_page_does_not_contain_google_button(client):
        response = client.get("/accounts/login/")
        content = response.content.decode()
        assert "Continue with Google" not in content
        assert "accounts/google" not in content

    def it_login_page_shows_version_badge(client):
        response = client.get("/accounts/login/")
        content = response.content.decode()
        assert f"v{VERSION}" in content
        assert f"BETA v{VERSION}" not in content

    def it_login_page_includes_changelog_modal(client):
        response = client.get("/accounts/login/")
        content = response.content.decode()
        assert "changelog-modal" in content
        assert "Launch Day" in content


def describe_signup_gating():
    def it_shows_signup_closed_page_in_invite_only_mode(client):
        config = SiteConfiguration.load()
        config.registration_mode = SiteConfiguration.RegistrationMode.INVITE_ONLY
        config.save()

        response = client.get("/accounts/signup/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "invitation only" in content.lower()

    def it_shows_signup_form_in_open_mode(client):
        config = SiteConfiguration.load()
        config.registration_mode = SiteConfiguration.RegistrationMode.OPEN
        config.save()

        response = client.get("/accounts/signup/")
        assert response.status_code == 200
        template_names = [t.name for t in response.templates]
        assert "account/signup.html" in template_names

    def it_hides_signup_link_on_login_page_when_invite_only(client):
        # Assert the actual <a href="/accounts/signup/"> link is absent on the
        # members-surface login page. The substring "Sign up" alone leaks via
        # the changelog modal (which quotes button names) and is unreliable.
        config = SiteConfiguration.load()
        config.registration_mode = SiteConfiguration.RegistrationMode.INVITE_ONLY
        config.save()

        response = client.get("/accounts/login/")
        content = response.content.decode()
        assert 'href="/accounts/signup/"' not in content

    def it_shows_signup_link_on_login_page_when_open(client):
        config = SiteConfiguration.load()
        config.registration_mode = SiteConfiguration.RegistrationMode.OPEN
        config.save()

        response = client.get("/accounts/login/")
        content = response.content.decode()
        assert 'href="/accounts/signup/"' in content

    def it_allows_signup_with_valid_invite_in_invite_only_mode(client):
        config = SiteConfiguration.load()
        config.registration_mode = SiteConfiguration.RegistrationMode.INVITE_ONLY
        config.save()

        admin_user = User.objects.create_user(username="inviter", email="inviter@example.com", password="pass")
        Invite.objects.create(email="invited@example.com", invited_by=admin_user)

        response = client.get("/accounts/signup/?email=invited@example.com")
        assert response.status_code == 200
        template_names = [t.name for t in response.templates]
        assert "account/signup.html" in template_names


def describe_auto_create_user_on_login():
    def it_creates_user_when_member_exists_without_user(client):
        MemberFactory(_pre_signup_email="synced@example.com", user=None)

        assert not User.objects.filter(email__iexact="synced@example.com").exists()

        client.post("/accounts/login/code/", {"email": "synced@example.com"})

        assert User.objects.filter(email__iexact="synced@example.com").exists()
        user = User.objects.get(email__iexact="synced@example.com")
        member = Member.objects.get(_pre_signup_email="synced@example.com")
        assert member.user == user

    def it_does_not_create_user_when_no_member_exists(client):
        client.post("/accounts/login/code/", {"email": "nobody@example.com"})

        assert not User.objects.filter(email__iexact="nobody@example.com").exists()

    def it_handles_empty_email(client):
        resp = client.post("/accounts/login/code/", {"email": ""})
        assert resp.status_code == 200  # re-renders form with errors

    def it_does_not_duplicate_user_when_user_already_exists(client):
        MemberFactory(_pre_signup_email="existing@example.com")
        User.objects.create_user(username="existing@example.com", email="existing@example.com")

        client.post("/accounts/login/code/", {"email": "existing@example.com"})

        assert User.objects.filter(email__iexact="existing@example.com").count() == 1


def describe_email_templates():
    def it_base_message_uses_past_lives_branding():
        content = render_to_string("account/email/base_message.txt", {"current_site": None})
        assert "Past Lives Makerspace" in content
        assert "example.com" not in content

    def it_unknown_account_txt_is_branded():
        content = render_to_string(
            "account/email/unknown_account_message.txt",
            {"email": "test@test.com", "signup_url": "https://example.com/signup/"},
        )
        assert "Past Lives Makerspace" in content
        assert "example.com" not in content.replace("https://example.com/signup/", "")

    def it_unknown_account_html_is_branded():
        content = render_to_string(
            "account/email/unknown_account_message.html",
            {"email": "test@test.com", "signup_url": "https://example.com/signup/"},
        )
        assert "Past Lives" in content
        assert "No Account Found" in content

    def it_unknown_account_subject_is_branded():
        content = render_to_string("account/email/unknown_account_subject.txt")
        assert "Past Lives" in content

    def it_account_already_exists_txt_is_branded():
        content = render_to_string(
            "account/email/account_already_exists_message.txt",
            {"email": "test@test.com", "password_reset_url": "#", "signup_url": "#"},
        )
        assert "Past Lives Makerspace" in content
        assert "example.com" not in content

    def it_account_already_exists_html_is_branded():
        content = render_to_string(
            "account/email/account_already_exists_message.html",
            {"email": "test@test.com"},
        )
        assert "Past Lives" in content
        assert "Account Already Exists" in content

    def it_account_already_exists_subject_is_branded():
        content = render_to_string("account/email/account_already_exists_subject.txt")
        assert "Past Lives" in content
