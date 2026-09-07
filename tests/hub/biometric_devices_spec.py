"""BDD specs for the "Signed In Devices" card on the settings Account tab.

The card's app-only half stays inert until ``static/js/biometric-auth.js`` reveals it inside
the Capacitor app, so what is testable here is the Django half: the server-rendered list of
this member's live credentials, and the per-row Revoke that sends one phone back to emailed
codes. The Keychain and the biometric prompt do not exist in this test run.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import BiometricCredential
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

_CARD_MARKER = 'id="biometric-devices"'
_SETTINGS_URL_TAB = "?tab=account"


def _signed_in(client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pw12345!")
    client.login(username=username, password="pw12345!")
    return user


def _credential(user: User, label: str = "iPhone") -> BiometricCredential:
    credential, _secret = BiometricCredential.objects.issue(
        user, device_label=label, platform=BiometricCredential.Platform.IOS
    )
    return credential


def _settings_html(client) -> str:
    response = client.get(f"{reverse('hub_user_settings')}{_SETTINGS_URL_TAB}")
    assert response.status_code == 200
    return response.content.decode()


def describe_the_card_on_the_settings_page():
    def it_renders_for_a_signed_in_member(client):
        _signed_in(client, "bio_card")

        assert _CARD_MARKER in _settings_html(client)

    def it_passes_this_members_live_credentials_into_the_page(client):
        user = _signed_in(client, "bio_ctx")
        credential = _credential(user)

        response = client.get(f"{reverse('hub_user_settings')}{_SETTINGS_URL_TAB}")

        assert response.context["biometric_credentials"] == [credential]

    def it_omits_a_revoked_credential(client):
        user = _signed_in(client, "bio_revoked")
        credential = _credential(user, label="Retired Phone")
        BiometricCredential.objects.revoke(credential)

        html = _settings_html(client)

        assert "Retired Phone" not in html
        assert "No devices yet." in html

    def it_lists_the_device_label(client):
        user = _signed_in(client, "bio_label")
        _credential(user, label="Jo's iPhone")

        assert "Jo&#x27;s iPhone" in _settings_html(client)

    def it_escapes_the_client_supplied_device_label(client):
        user = _signed_in(client, "bio_escape")
        _credential(user, label="<script>alert(1)</script>")

        html = _settings_html(client)

        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def it_says_when_a_device_has_never_been_used(client):
        user = _signed_in(client, "bio_unused")
        _credential(user)

        assert "not used yet" in _settings_html(client)

    def it_shows_no_devices_message_when_the_member_has_none(client):
        _signed_in(client, "bio_empty")

        assert "No devices yet." in _settings_html(client)

    def it_loads_the_script_that_reveals_the_app_only_controls(client):
        """hub/base.html is its own document, not a child of base.html, so it needs its own
        copy of the script. Without it the card can never offer to turn biometric sign in on."""
        _signed_in(client, "bio_script")

        assert "biometric-auth.js" in _settings_html(client)

    def it_marks_hub_pages_as_signed_in_for_the_script(client):
        _signed_in(client, "bio_authattr")

        assert 'data-pl-authenticated="1"' in _settings_html(client)

    def it_does_not_list_another_members_device(client):
        _signed_in(client, "bio_mine")
        other = User.objects.create_user(username="bio_theirs", email="bio_theirs@example.com")
        _credential(other, label="Their Phone")

        assert "Their Phone" not in _settings_html(client)


def describe_biometric_revoke():
    def it_revokes_the_credential(client):
        user = _signed_in(client, "revoke_one")
        credential = _credential(user)

        response = client.post(reverse("hub_biometric_revoke", args=[credential.pk]))

        assert response.status_code == 200
        credential.refresh_from_db()
        assert credential.revoked_at is not None

    def it_leaves_the_members_other_devices_alone(client):
        user = _signed_in(client, "revoke_other")
        phone = _credential(user, label="iPhone")
        tablet = _credential(user, label="iPad")

        client.post(reverse("hub_biometric_revoke", args=[phone.pk]))

        tablet.refresh_from_db()
        assert tablet.revoked_at is None

    def it_returns_the_card_without_the_revoked_row(client):
        user = _signed_in(client, "revoke_render")
        credential = _credential(user, label="Old Phone")

        response = client.post(reverse("hub_biometric_revoke", args=[credential.pk]))

        html = response.content.decode()
        assert _CARD_MARKER in html
        assert "Old Phone" not in html

    def it_reports_what_happens_next_in_a_toast(client):
        user = _signed_in(client, "revoke_toast")
        credential = _credential(user)

        response = client.post(reverse("hub_biometric_revoke", args=[credential.pk]))

        assert "emailed code" in response["HX-Trigger"]

    def it_refuses_to_revoke_another_members_credential(client):
        _signed_in(client, "revoke_mine")
        other = User.objects.create_user(username="revoke_theirs", email="revoke_theirs@example.com")
        theirs = _credential(other)

        response = client.post(reverse("hub_biometric_revoke", args=[theirs.pk]))

        assert response.status_code == 404
        theirs.refresh_from_db()
        assert theirs.revoked_at is None

    def it_refuses_a_GET(client):
        user = _signed_in(client, "revoke_get")
        credential = _credential(user)

        response = client.get(reverse("hub_biometric_revoke", args=[credential.pk]))

        assert response.status_code == 405

    def it_redirects_an_anonymous_caller_to_log_in(client):
        MembershipPlanFactory()
        user = User.objects.create_user(username="revoke_anon", email="revoke_anon@example.com")
        credential = _credential(user)

        response = client.post(reverse("hub_biometric_revoke", args=[credential.pk]))

        assert response.status_code == 302
        credential.refresh_from_db()
        assert credential.revoked_at is None
