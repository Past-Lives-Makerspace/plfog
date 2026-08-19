"""Spec for the linked-state slash-command nudge on the Discord link landing page.

The optional §6 nudge points a just-linked member back to Discord's slash commands. It lives
only inside the ``state="linked"`` block, so it must render after a successful link and be
absent from every other landing state. Both cases are driven through the real low-friction
``/discord/link/`` callback view (as the other landing specs do), so the assertion sees exactly
what a member would.

The linked-success path is arranged so the reaction/role sync is a no-op (no SiteConfiguration
Discord channel wired), leaving only the OAuth token + identity calls to mock — no reaction or
role HTTP is attempted.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from factory.django import mute_signals

from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

_TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
_IDENTITY_URL = "https://discord.com/api/v10/users/@me"
_LINK_STATE_KEY = "discord_link_state"


def _configure(settings):
    # Client id + secret only — no SiteConfiguration channel, so guild import is a no-op
    # (no reaction/role HTTP), keeping this a pure OAuth token+identity round-trip.
    settings.DISCORD_CLIENT_ID = "cid"
    settings.DISCORD_CLIENT_SECRET = "secret"


def _verified_member(email):
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u_{email}", email=email)
    member = MemberFactory(discord_user_id="")
    member.user = user
    member.save(update_fields=["user", "discord_user_id"])
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    return member


def _client_with_state():
    client = Client()
    session = client.session
    session[_LINK_STATE_KEY] = "s"
    session.save()
    return client


def describe_discord_link_landing_nudge():
    @respx.mock
    def it_shows_the_slash_command_nudge_on_the_linked_state(settings):
        _configure(settings)
        _verified_member("jo@example.com")
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
        respx.get(_IDENTITY_URL).mock(
            return_value=httpx.Response(
                200, json={"id": "42", "username": "jo", "email": "jo@example.com", "verified": True}
            )
        )
        response = _client_with_state().get(reverse("hub_discord_link_callback"), {"code": "c", "state": "s"})
        assert response.status_code == 200
        assert b"Discord Connected" in response.content  # sanity: we're on the linked state
        assert b"Back in Discord, try" in response.content
        assert b"/whats-on" in response.content
        assert b"/fog-ping" in response.content

    def it_hides_the_nudge_on_a_non_linked_state(settings):
        _configure(settings)
        response = _client_with_state().get(reverse("hub_discord_link_callback"), {"error": "access_denied"})
        assert response.status_code == 200
        assert b"Connection Cancelled" in response.content  # sanity: a non-linked state
        assert b"Back in Discord, try" not in response.content
