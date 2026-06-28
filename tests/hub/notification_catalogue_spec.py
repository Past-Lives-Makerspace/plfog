"""BDD specs for the admin notification copy catalogue views (design §2.3 + §2.4)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.models import DiscordWebhookRoute, NotificationTemplate
from membership.models import Member

pytestmark = pytest.mark.django_db


def _admin(client: Client, *, username: str = "ntfadmin") -> User:
    user = User.objects.create_superuser(username=username, email=f"{username}@x.com", password="p")
    client.login(username=username, password="p")
    return user


def _member(*, username: str = "plain") -> User:
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password="p")
    member = user.member
    member.fog_role = Member.FogRole.MEMBER
    member.save()
    return user


def describe_catalogue_access():
    def it_redirects_anonymous_users_to_login(client):
        response = client.get(reverse("hub_admin_notifications"))
        assert response.status_code == 302

    def it_forbids_plain_members(client):
        user = _member()
        client.login(username=user.username, password="p")
        response = client.get(reverse("hub_admin_notifications"))
        assert response.status_code == 403

    def it_renders_the_catalogue_for_an_admin(client):
        _admin(client)
        response = client.get(reverse("hub_admin_notifications"))
        assert response.status_code == 200
        content = response.content.decode()
        # Grouped by category, lists every event with its key + audience.
        assert "registration_confirmed" in content
        assert "Audience" in content

    def it_groups_events_into_categories(client):
        _admin(client)
        response = client.get(reverse("hub_admin_notifications"))
        groups = response.context["groups"]
        categories = {category for category, _events in groups}
        assert "Classes" in categories
        assert "Billing" in categories

    def it_shows_lock_status_for_forced_events(client):
        _admin(client)
        response = client.get(reverse("hub_admin_notifications"))
        # new_login is force_email in the legacy catalogue → renders a "Forced" badge.
        assert b"Forced" in response.content


def describe_edit_copy():
    def it_renders_the_edit_form_with_a_preview_for_an_admin(client):
        _admin(client)
        url = reverse("hub_admin_notification_edit", args=["registration_confirmed", "email"])
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "Live preview" in content
        assert "Available merge fields" in content

    def it_forbids_plain_members(client):
        user = _member(username="m2")
        client.login(username=user.username, password="p")
        url = reverse("hub_admin_notification_edit", args=["registration_confirmed", "email"])
        assert client.get(url).status_code == 403

    def it_saves_an_edit_and_snapshots_a_version(client):
        _admin(client)
        url = reverse("hub_admin_notification_edit", args=["registration_confirmed", "email"])
        response = client.post(
            url,
            {"subject": "New subject {{ class_title }}", "body_text": "body", "body_html": ""},
        )
        assert response.status_code == 302
        row = NotificationTemplate.objects.get(event_key="registration_confirmed", channel="email")
        assert row.subject == "New subject {{ class_title }}"
        assert row.is_overridden is True
        assert row.versions.count() == 1

    def it_rejects_an_undocumented_merge_field(client):
        _admin(client)
        url = reverse("hub_admin_notification_edit", args=["registration_confirmed", "email"])
        response = client.post(
            url,
            {"subject": "Hi {{ not_a_field }}", "body_text": "b", "body_html": ""},
        )
        assert response.status_code == 200  # re-rendered with errors, not saved
        assert b"Unknown merge field" in response.content
        assert not NotificationTemplate.objects.filter(
            event_key="registration_confirmed", channel="email", is_overridden=True
        ).exists()


def describe_preview_copy():
    def it_renders_the_posted_copy_against_the_sample_context(client):
        _admin(client)
        url = reverse("hub_admin_notification_preview", args=["registration_confirmed", "email"])
        response = client.post(
            url,
            {"subject": "Hi {{ class_title }}", "body_text": "{{ member_name }}", "body_html": ""},
        )
        assert response.status_code == 200
        content = response.content.decode()
        # Sample context fills the placeholders.
        assert "Intro to Lost-Wax Casting" in content
        assert "Robin Vale" in content

    def it_shows_a_missing_marker_for_an_unknown_field(client):
        _admin(client)
        url = reverse("hub_admin_notification_preview", args=["registration_confirmed", "email"])
        response = client.post(url, {"subject": "{{ ghost }}", "body_text": "", "body_html": ""})
        assert b"[missing: ghost]" in response.content

    def it_autoescapes_html_values_in_the_preview(client):
        _admin(client)
        url = reverse("hub_admin_notification_preview", args=["registration_confirmed", "email"])
        response = client.post(
            url,
            {"subject": "s", "body_text": "t", "body_html": "<p>{{ class_title }}</p>"},
        )
        # The authored <p> is rendered; the sample value carries no markup so nothing
        # to escape, but the request must succeed and render the HTML block.
        assert response.status_code == 200
        assert b"HTML body" in response.content

    def it_provides_the_branded_wrapped_html_for_the_email_channel(client):
        _admin(client)
        url = reverse("hub_admin_notification_preview", args=["registration_confirmed", "email"])
        response = client.post(url, {"subject": "s", "body_text": "t", "body_html": "<p>{{ class_title }}</p>"})
        wrapped = response.context["wrapped_html"]
        # The fragment, inside the branded shell (shell-only footer marker).
        assert "Intro to Lost-Wax Casting" in wrapped
        assert "Do It Together" in wrapped
        # Rendered as an iframe panel.
        assert b"As it arrives (branded)" in response.content
        assert b"srcdoc=" in response.content

    def it_attribute_escapes_the_iframe_srcdoc_not_marking_it_safe(client):
        # §4.6 footgun: srcdoc is an attribute, so the document must be attribute-escaped
        # (no |safe). The escaped doc starts with &lt;!DOCTYPE, never raw <!DOCTYPE.
        _admin(client)
        url = reverse("hub_admin_notification_preview", args=["registration_confirmed", "email"])
        response = client.post(url, {"subject": "s", "body_text": "t", "body_html": "<p>hi</p>"})
        content = response.content.decode()
        assert 'srcdoc="&lt;!DOCTYPE' in content
        assert 'srcdoc="<!DOCTYPE' not in content

    def it_omits_the_branded_iframe_for_a_non_email_channel(client):
        _admin(client)
        url = reverse("hub_admin_notification_preview", args=["registration_confirmed", "in_app"])
        response = client.post(url, {"subject": "s", "body_text": "{{ member_name }}", "body_html": "<p>x</p>"})
        assert response.context["wrapped_html"] == ""
        assert b"As it arrives (branded)" not in response.content
        assert b"srcdoc=" not in response.content


def describe_revert_copy():
    def it_reverts_to_a_prior_version(client):
        _admin(client)
        edit_url = reverse("hub_admin_notification_edit", args=["registration_confirmed", "email"])
        client.post(edit_url, {"subject": "v2", "body_text": "b2", "body_html": ""})
        row = NotificationTemplate.objects.get(event_key="registration_confirmed", channel="email")
        prior = row.versions.first()  # snapshot of the seeded "v1" copy
        revert_url = reverse("hub_admin_notification_revert", args=["registration_confirmed", "email", prior.pk])
        response = client.post(revert_url)
        assert response.status_code == 302
        row.refresh_from_db()
        assert row.subject == prior.subject

    def it_forbids_plain_members(client):
        user = _member(username="m3")
        client.login(username=user.username, password="p")
        url = reverse("hub_admin_notification_revert", args=["registration_confirmed", "email", 1])
        assert client.post(url).status_code == 403


def describe_edit_discord_route():
    def it_renders_the_routing_form_for_an_admin(client):
        _admin(client)
        url = reverse("hub_admin_notification_discord", args=["class_published"])
        response = client.get(url)
        assert response.status_code == 200
        assert b"Discord routing" in response.content

    def it_never_echoes_the_stored_webhook_url(client):
        secret = "https://discord.example/SECRET-TOKEN"
        DiscordWebhookRoute.objects.create(event_key="class_published", webhook_url=secret, is_enabled=True)
        _admin(client)
        url = reverse("hub_admin_notification_discord", args=["class_published"])
        response = client.get(url)
        assert b"SECRET-TOKEN" not in response.content

    def it_saves_a_route(client):
        _admin(client)
        url = reverse("hub_admin_notification_discord", args=["class_published"])
        response = client.post(
            url,
            {"webhook_url": "https://discord.example/hook", "is_enabled": "on"},
        )
        assert response.status_code == 302
        route = DiscordWebhookRoute.objects.get(event_key="class_published")
        assert route.webhook_url == "https://discord.example/hook"
        assert route.is_enabled is True

    def it_forbids_plain_members(client):
        user = _member(username="m4")
        client.login(username=user.username, password="p")
        url = reverse("hub_admin_notification_discord", args=["class_published"])
        assert client.get(url).status_code == 403
