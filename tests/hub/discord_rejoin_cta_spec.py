"""A member the bot removed from the Discord is offered Rejoin the Discord when they come back (#898)."""

from __future__ import annotations

from datetime import date

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import DiscordRemoval, Member

pytestmark = pytest.mark.django_db

SETTINGS = f"{reverse('hub_user_settings')}?tab=notifications"


def _signed_in_member(client: Client) -> Member:
    user = User.objects.create_user(username="returner", email="returner@example.com", password="pw12345!")
    Member.objects.filter(user=user).update(status=Member.Status.ACTIVE, discord_user_id="42")
    client.login(username="returner", password="pw12345!")
    return Member.objects.get(user=user)


def _kicked(member: Member) -> DiscordRemoval:
    return DiscordRemoval.objects.create(
        member=member,
        discord_user_id="42",
        source=DiscordRemoval.Source.FORMER,
        status=DiscordRemoval.Status.KICKED,
        warn_on=date(2026, 10, 1),
        kicked_at=timezone.now(),
    )


def describe_discord_cta():
    def it_offers_rejoin_to_a_returning_removed_member(client: Client):
        _kicked(_signed_in_member(client))
        html = client.get(SETTINGS).content.decode()
        assert "data-discord-rejoin" in html
        assert "Rejoin the Discord" in html
        assert f'href="{reverse("hub_discord_connect")}"' in html

    def it_keeps_disconnect_beside_rejoin(client: Client):
        # A member back on a different Discord account must disconnect the old one to connect.
        _kicked(_signed_in_member(client))
        html = client.get(SETTINGS).content.decode()
        actions = html.split('class="pl-discord-link__actions"', 1)[1].split("</div>", 1)[0]
        assert "data-discord-rejoin" in actions
        assert "open-confirm', 'disconnect-discord-" in actions
        assert actions.index("data-discord-rejoin") < actions.index("Disconnect")

    def it_keeps_disconnect_for_everyone_else(client: Client):
        _signed_in_member(client)
        html = client.get(SETTINGS).content.decode()
        assert "data-discord-rejoin" not in html
        assert "open-confirm', 'disconnect-discord-notifications" in html

    def it_stops_offering_once_they_are_back(client: Client):
        removal = _kicked(_signed_in_member(client))
        removal.rejoined_at = timezone.now()
        removal.save()
        assert "data-discord-rejoin" not in client.get(SETTINGS).content.decode()
