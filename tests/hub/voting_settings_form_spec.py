"""BDD specs for the Voting → Settings tab (VotingSettingsForm through the view)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

from membership.models import VotingSettings

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture()
def admin_client():
    user = User.objects.create_superuser("vsadmin", "vsadmin@x.com", "p")
    client = Client()
    client.force_login(user)
    return client


def _valid_post(**overrides):
    data = {
        "reminder_lead_days": "4",
        "minimum_pool_floor": "1200.00",
        "reminders_enabled": "on",
        "send_vote_soon_enabled": "on",
        "auto_snapshot_enabled": "on",
    }
    data.update(overrides)
    return data


def describe_voting_settings_view():
    def describe_get():
        def it_renders_the_three_toggles_and_copy_editor_links(admin_client):
            resp = admin_client.get(reverse("hub_admin_voting_settings"))
            assert resp.status_code == 200
            body = resp.content.decode()
            # Booleans render via the toggle component (pl-toggle), not raw checkboxes.
            assert "pl-toggle" in body
            # One "Edit wording" link per voting event copy-editor page.
            for key in ("voting.closing_soon", "voting.vote_soon", "voting.results_published", "voting.results_ready"):
                assert reverse("hub_admin_notification_edit", args=[key, "email"]) in body

    def describe_post():
        def it_saves_all_five_fields_and_redirects_with_a_message(admin_client):
            resp = admin_client.post(
                reverse("hub_admin_voting_settings"),
                _valid_post(reminder_lead_days="6", minimum_pool_floor="900.00", send_vote_soon_enabled=""),
            )
            assert resp.status_code == 302
            assert resp.url == reverse("hub_admin_voting_settings")
            msgs = [m.message for m in get_messages(resp.wsgi_request)]
            assert "Voting settings saved." in msgs

            settings = VotingSettings.load()
            assert settings.reminder_lead_days == 6
            assert settings.minimum_pool_floor == Decimal("900.00")
            assert settings.send_vote_soon_enabled is False
            assert settings.reminders_enabled is True
            assert settings.auto_snapshot_enabled is True

        def it_accepts_a_lead_of_one(admin_client):
            resp = admin_client.post(reverse("hub_admin_voting_settings"), _valid_post(reminder_lead_days="1"))
            assert resp.status_code == 302
            assert VotingSettings.load().reminder_lead_days == 1

        @pytest.mark.parametrize("bad_lead", ["0", "-1"])
        def it_rejects_a_lead_below_one(admin_client, bad_lead):
            resp = admin_client.post(reverse("hub_admin_voting_settings"), _valid_post(reminder_lead_days=bad_lead))
            assert resp.status_code == 200  # bound form re-rendered, no redirect
            assert resp.context["form"].errors
            # Unchanged from the default — nothing saved.
            assert VotingSettings.load().reminder_lead_days == 3

        def it_rejects_a_negative_floor(admin_client):
            resp = admin_client.post(reverse("hub_admin_voting_settings"), _valid_post(minimum_pool_floor="-5"))
            assert resp.status_code == 200
            assert "minimum_pool_floor" in resp.context["form"].errors
            assert VotingSettings.load().minimum_pool_floor == Decimal("1000.00")
