"""BDD spec for the guest ?next= login handoff on the guilds surface (MUST-FIX #2).

Proves a guest who clicks "Log in to join" on a guild page lands back on that exact
guild page after the allauth email-code login — with the hidden ``next`` wired on both
the request-code and confirm-code forms, not merely relying on allauth carrying it.
"""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, override_settings

from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

GUILDS_HOST = "guilds.pastlives.app"
GUILDS_SETTINGS = dict(
    ALLOWED_HOSTS=["guilds.pastlives.app", "testserver"],
    GUILDS_HOSTS=["guilds.pastlives.app"],
    GUILDS_BASE_URL="https://guilds.pastlives.app",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)


def describe_guest_login_flow():
    @pytest.fixture(autouse=True)
    def _guilds_settings():
        with override_settings(**GUILDS_SETTINGS):
            yield

    def it_offers_a_login_link_carrying_the_guild_page_as_next(client: Client):
        guild = GuildFactory(name="Join Guild")
        page = client.get(f"/guilds/{guild.slug}/", HTTP_HOST=GUILDS_HOST).content.decode()
        assert f"/accounts/login/?next=/guilds/{guild.slug}/" in page

    def it_renders_a_hidden_next_on_the_login_form(client: Client):
        guild = GuildFactory(name="Join Guild")
        next_path = f"/guilds/{guild.slug}/"
        page = client.get(f"/accounts/login/?next={next_path}", HTTP_HOST=GUILDS_HOST).content.decode()
        assert f'name="next" value="{next_path}"' in page

    def it_carries_next_through_both_steps_back_to_the_guild(client: Client):
        MembershipPlanFactory()
        user = User.objects.create_user(username="guest", email="guest@example.com")
        # The auto-provision signal already staged a primary EmailAddress; replace it
        # with a verified one so login-by-code finds the account.
        EmailAddress.objects.filter(user=user).delete()
        EmailAddress.objects.create(user=user, email="guest@example.com", verified=True, primary=True)
        guild = GuildFactory(name="Join Guild")
        next_path = f"/guilds/{guild.slug}/"

        # Request a login code, carrying next → allauth redirects to the confirm page.
        mail.outbox.clear()
        resp = client.post(
            "/accounts/login/code/",
            {"email": "guest@example.com", "next": next_path},
            HTTP_HOST=GUILDS_HOST,
        )
        assert resp.status_code == 302

        # The confirm page carries the hidden next too (belt-and-suspenders wiring).
        confirm_page = client.get(resp["Location"], HTTP_HOST=GUILDS_HOST).content.decode()
        assert f'name="next" value="{next_path}"' in confirm_page

        # Submit the emailed code with next → land back on the exact guild page.
        code = mail.outbox[-1].body.split("Past Lives Makerspace is:")[1].split()[0]
        done = client.post(
            "/accounts/login/code/confirm/",
            {"code": code, "next": next_path},
            HTTP_HOST=GUILDS_HOST,
        )
        assert done.status_code == 302
        assert done["Location"] == next_path
