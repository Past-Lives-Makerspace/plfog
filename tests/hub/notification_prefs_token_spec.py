"""BDD specs for the logged-out, token-authorized notification preferences page.

``hub_user_settings`` (``/settings/``) no longer requires login: a logged-out
visitor without a valid ``t`` token is bounced to login exactly as before, but one
carrying a valid per-recipient token (minted by ``core.email_prefs`` and injected
into the email footer) reaches a scoped view of ONLY the notification matrix —
``core.email_prefs.read_prefs_token`` resolves the token to the User whose
preferences get rendered/saved. A logged-in visitor still gets the full tabbed page.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.email_prefs import make_prefs_token
from core.models import NotificationPreference
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _member_user(username: str, email: str | None = None) -> User:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, email=email or f"{username}@example.com")


def describe_user_settings_when_logged_out():
    def describe_with_a_valid_token():
        def it_renders_the_token_only_notifications_page(client):
            user = _member_user("token_get", email="token_get@example.com")
            token = make_prefs_token(user)

            response = client.get(f"{reverse('hub_user_settings')}?tab=notifications&t={token}")

            assert response.status_code == 200
            assert response.templates[0].name == "hub/settings_notifications_token.html"
            assert response.context["prefs_email"] == "token_get@example.com"
            assert response.context["notif_matrix"]  # the matrix has at least one category/row
            assert b"token_get@example.com" in response.content

        def it_does_not_redirect_to_login(client):
            user = _member_user("token_noredirect", email="token_noredirect@example.com")
            token = make_prefs_token(user)

            response = client.get(f"{reverse('hub_user_settings')}?tab=notifications&t={token}")

            assert response.status_code != 302

        def it_saves_a_posted_preference_for_the_tokens_user_and_redirects(client):
            user = _member_user("token_post", email="token_post@example.com")
            token = make_prefs_token(user)

            response = client.post(
                reverse("hub_user_settings"),
                {"t": token, "form_id": "notifications", "pref__class_published__push": "on"},
            )

            assert response.status_code == 302
            assert (
                NotificationPreference.objects.get(user=user, event_key="class_published", channel="push").enabled
                is True
            )

        def it_redirects_back_to_the_token_page_after_saving(client):
            user = _member_user("token_post_redirect", email="token_post_redirect@example.com")
            token = make_prefs_token(user)

            response = client.post(
                reverse("hub_user_settings"),
                {"t": token, "form_id": "notifications", "pref__class_published__push": "on"},
            )

            assert response["Location"] == f"{reverse('hub_user_settings')}?tab=notifications&t={token}"

        def it_only_saves_preferences_for_the_tokens_user_not_some_other_member(client):
            owner = _member_user("token_owner", email="token_owner@example.com")
            bystander = _member_user("token_bystander", email="token_bystander@example.com")
            token = make_prefs_token(owner)

            client.post(
                reverse("hub_user_settings"),
                {"t": token, "form_id": "notifications", "pref__class_published__push": "on"},
            )

            assert not NotificationPreference.objects.filter(
                user=bystander, event_key="class_published", channel="push"
            ).exists()

    def describe_without_a_valid_token():
        def it_redirects_to_login_when_no_token_is_present(client):
            response = client.get(reverse("hub_user_settings"))

            assert response.status_code == 302
            assert "/accounts/login/" in response["Location"]

        def it_redirects_to_login_when_the_token_is_garbage(client):
            response = client.get(f"{reverse('hub_user_settings')}?t=garbage-not-a-real-token")

            assert response.status_code == 302
            assert "/accounts/login/" in response["Location"]

        def it_redirects_to_login_on_post_with_a_missing_token(client):
            response = client.post(reverse("hub_user_settings"), {"form_id": "notifications"})

            assert response.status_code == 302
            assert "/accounts/login/" in response["Location"]


def describe_user_settings_when_logged_in():
    def it_still_renders_the_full_tabbed_settings_page(client):
        MembershipPlanFactory()
        User.objects.create_user(username="full_page", email="full_page@example.com", password="pw12345!")
        client.login(username="full_page", password="pw12345!")

        response = client.get(reverse("hub_user_settings"))

        assert response.status_code == 200
        template_names = [t.name for t in response.templates if t.name]
        assert "hub/user_settings.html" in template_names
        assert "profile_form" in response.context
