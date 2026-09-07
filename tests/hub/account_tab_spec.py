"""The Account tab: relocated Manage Email Addresses card + the polished Danger Zone.

The email card's behaviour lives in tests/hub/views_spec.py (context) and
tests/hub/account_delete_spec.py (deletion flow); this file pins the restructure — the
card's new home above a dedicated .pl-danger-zone card.
"""

import pytest
from django.contrib.auth.models import User
from django.test import Client

pytestmark = pytest.mark.django_db


def _login(client, username="accttab"):
    User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    client.login(username=username, password="pass")


def describe_account_tab():
    def it_renders_the_email_card_and_add_form(client: Client):
        _login(client)
        content = client.get("/settings/?tab=account").content.decode()
        assert "Manage Email Addresses" in content
        assert "Add an Email Address" in content

    def it_wraps_the_delete_flow_in_a_dedicated_danger_zone_card(client: Client):
        _login(client)
        content = client.get("/settings/?tab=account").content.decode()
        assert 'class="hub-card pl-danger-zone"' in content
        assert "pl-danger-zone__head" in content
        assert "pl-danger-zone__footer" in content
        assert "Danger Zone" in content

    def it_uses_a_small_delete_button_in_the_footer(client: Client):
        _login(client)
        content = client.get("/settings/?tab=account").content.decode()
        # Destructive trigger drops to pl-btn--sm; the modal is the real guard.
        assert "pl-btn pl-btn--danger pl-btn--sm" in content
        assert "Delete My Account" in content

    def it_no_longer_shows_the_standalone_delete_your_account_heading(client: Client):
        _login(client)
        content = client.get("/settings/?tab=account").content.decode()
        assert "Delete Your Account" not in content  # each card carries its own heading now
