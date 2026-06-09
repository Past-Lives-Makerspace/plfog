"""End-to-end checks that saves surface as toasts.

These guard the two halves of the toast pipeline that unit tests miss:
the server must render a Django message as a `data-toast-message` element,
and the self-contained toast component (inline CSS + JS) must ship on the page.
The client-side draining of those attributes is covered separately.
"""

from __future__ import annotations

from django.test import Client

from membership.models import Member


def _login(client: Client, django_user_model, email: str) -> None:
    user = django_user_model.objects.create_user(username=email, email=email, password="pw")
    # A post_save signal auto-creates the linked Member; make sure one exists.
    Member.objects.get_or_create(user=user, defaults={"full_legal_name": "Test Member"})
    client.force_login(user)


def describe_toast_pipeline():
    def it_renders_a_save_message_as_a_toast_data_attribute(client, django_user_model, db):
        _login(client, django_user_model, "saver@example.com")
        resp = client.post(
            "/settings/",
            {"form_id": "profile", "pronouns": "", "about_me": "hi", "discord_handle": ""},
            follow=True,
        )
        html = resp.content.decode()
        assert resp.status_code == 200
        assert 'data-toast-message="Profile updated."' in html
        assert 'data-toast-type="success"' in html

    def it_ships_the_self_contained_toast_component_on_hub_pages(client, django_user_model, db):
        _login(client, django_user_model, "viewer@example.com")
        html = client.get("/settings/").content.decode()
        assert "window.showToast" in html  # inline JS, never depends on a cached asset
        assert ".plt-toast" in html        # inline CSS, never depends on a cached asset
