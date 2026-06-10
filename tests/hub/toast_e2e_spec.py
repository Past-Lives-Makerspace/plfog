"""End-to-end checks that saves surface as toasts.

Saves use Post/Redirect/Get, so the message is handed to the next page. Under
htmx hx-boost the message-bearing render is not reliably the one swapped into the
DOM, so ToastFlashMiddleware moves a redirect's messages into the `pl_toast`
cookie, which survives every navigation kind. These guard that wiring plus the
fact that the self-contained toast component ships on the page. The client-side
reading of the cookie / DOM attributes is covered separately (jsdom).
"""

from __future__ import annotations

import json
from urllib.parse import unquote

from django.test import Client

from membership.models import Member


def _login(client: Client, django_user_model, email: str) -> None:
    user = django_user_model.objects.create_user(username=email, email=email, password="pw")
    # A post_save signal auto-creates the linked Member; make sure one exists.
    Member.objects.get_or_create(user=user, defaults={"full_legal_name": "Test Member"})
    client.force_login(user)


def describe_toast_pipeline():
    def it_flashes_an_htmx_save_message_into_the_pl_toast_cookie(client, django_user_model, db):
        _login(client, django_user_model, "saver@example.com")
        resp = client.post(
            "/settings/",
            {"form_id": "profile", "pronouns": "", "about_me": "hi", "discord_handle": ""},
            HTTP_HX_REQUEST="true",  # boosted request: the path where the swap can drop the message
        )
        assert resp.status_code == 302
        cookie = resp.cookies.get("pl_toast")
        assert cookie is not None
        assert json.loads(unquote(cookie.value)) == [{"message": "Profile updated.", "type": "success"}]

    def it_does_not_also_leave_an_htmx_message_in_the_destination_dom(client, django_user_model, db):
        # Consumed once into the cookie, so the destination must not render it again.
        _login(client, django_user_model, "once@example.com")
        html = client.post(
            "/settings/",
            {"form_id": "profile", "pronouns": "", "about_me": "hi", "discord_handle": ""},
            follow=True,
            HTTP_HX_REQUEST="true",
        ).content.decode()
        assert 'data-toast-message="Profile updated."' not in html

    def it_leaves_non_htmx_messages_on_the_normal_django_path(client, django_user_model, db):
        # A plain (non-boosted) save keeps Django's standard message flow: rendered
        # in the destination DOM, not moved to the cookie.
        _login(client, django_user_model, "plain@example.com")
        resp = client.post(
            "/settings/",
            {"form_id": "profile", "pronouns": "", "about_me": "hi", "discord_handle": ""},
            follow=True,
        )
        assert resp.cookies.get("pl_toast") is None
        assert 'data-toast-message="Profile updated."' in resp.content.decode()

    def it_ships_the_self_contained_toast_component_on_hub_pages(client, django_user_model, db):
        _login(client, django_user_model, "viewer@example.com")
        html = client.get("/settings/").content.decode()
        assert "window.showToast" in html  # inline JS, never depends on a cached asset
        assert ".plt-toast" in html  # inline CSS, never depends on a cached asset
