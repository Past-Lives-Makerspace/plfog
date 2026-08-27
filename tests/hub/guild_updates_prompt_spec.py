"""BDD specs for the first-login guild updates interstitial (/welcome/guild-updates/).

Covers view eligibility (one-time; a bookmark never resurrects it), the Save / Skip /
tamper / zero-guilds / unlinked paths, the login-redirect routing through the allauth
adapter (including the accepted ``?next=`` bypass), and the rendered template states.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

from membership.models import GuildMembership, Member
from membership.services.provisioning import provision_user_for_member
from tests.membership.factories import (
    GuildFactory,
    GuildMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

URL_NAME = "hub_guild_updates_prompt"


def _linked_user(client: Client, username: str = "u1") -> tuple[User, Member]:
    """Create a user with an auto-linked Member (a plan must exist first) and log in."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user, user.member


def _unlinked_user(client: Client, username: str = "nomember") -> User:
    """Create a logged-in user with no linked Member (unlinked account)."""
    user = User.objects.create_user(username=username, password="pass")
    Member.objects.filter(user=user).delete()
    client.login(username=username, password="pass")
    return user


def _messages(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def _checkbox_tags(html: str) -> list[str]:
    import re

    return re.findall(r'<input\b[^>]*name="guilds"[^>]*>', html)


def describe_guild_updates_prompt_get():
    def it_renders_a_row_per_active_guild_with_unchecked_toggles(client: Client):
        _linked_user(client)
        GuildFactory(name="Ceramics")
        GuildFactory(name="Woodshop")
        GuildFactory(name="Sleeping Dragons", is_active=False)
        response = client.get(reverse(URL_NAME))
        assert response.status_code == 200
        html = response.content.decode()
        assert "Which Guilds Do You Want Updates From?" in html
        tags = _checkbox_tags(html)
        assert len(tags) == 2
        assert "Sleeping Dragons" not in html
        import re

        assert not any(re.search(r"\schecked", tag) for tag in tags)

    def it_renders_save_last_in_the_form_and_a_disableable_skip(client: Client):
        _linked_user(client)
        GuildFactory(name="Ceramics")
        html = client.get(reverse(URL_NAME)).content.decode()
        form = html.split("<form", 1)[1].split("</form>")[0]
        # Save is the last control in the form (Rule 21) and just says "Save".
        assert form.rindex(">Save<") > form.rindex("I'll Pick Later")
        # Skip POSTs (name="skip") and disables the moment anything is picked.
        assert 'name="skip"' in form
        assert ':disabled="picked > 0"' in form

    def it_redirects_an_already_answered_member_home(client: Client):
        _user, member = _linked_user(client)
        GuildFactory()
        member.mark_guild_updates_answered()
        response = client.get(reverse(URL_NAME))
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_home")

    def it_redirects_a_member_with_a_subscription_home(client: Client):
        _user, member = _linked_user(client)
        GuildMembershipFactory(member=member)
        response = client.get(reverse(URL_NAME))
        assert response["Location"] == reverse("hub_home")

    def it_redirects_anonymous_to_login(client: Client):
        response = client.get(reverse(URL_NAME))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_redirects_an_unlinked_account_home_with_an_info_message(client: Client):
        _unlinked_user(client)
        GuildFactory()
        response = client.get(reverse(URL_NAME))
        assert response["Location"] == reverse("hub_home")
        assert "Your account is not linked to a membership." in _messages(response)

    def it_stamps_and_redirects_when_zero_active_guilds_exist(client: Client):
        _user, member = _linked_user(client)
        GuildFactory(is_active=False)
        response = client.get(reverse(URL_NAME))
        assert response["Location"] == reverse("hub_home")
        member.refresh_from_db()
        assert member.guild_updates_prompt_answered_at is not None


def describe_guild_updates_prompt_post():
    def it_saves_the_picks_stamps_and_reports_the_count(client: Client):
        _user, member = _linked_user(client)
        one = GuildFactory(name="One")
        two = GuildFactory(name="Two")
        response = client.post(reverse(URL_NAME), {"guilds": [one.pk, two.pk]})
        assert response["Location"] == reverse("hub_home")
        assert member.guild_memberships.count() == 2
        member.refresh_from_db()
        assert member.guild_updates_prompt_answered_at is not None
        assert "You'll get updates from 2 guilds. Change your picks anytime in Settings." in _messages(response)

    def it_singularizes_the_count_message_for_one_pick(client: Client):
        _linked_user(client)
        one = GuildFactory(name="One")
        response = client.post(reverse(URL_NAME), {"guilds": [one.pk]})
        assert "You'll get updates from 1 guild. Change your picks anytime in Settings." in _messages(response)

    def it_fires_the_subscribe_side_effects_for_each_pick(client: Client):
        _user, member = _linked_user(client)
        one = GuildFactory(name="One")
        with patch("membership.orientations.member_joined_guild") as joined:
            client.post(reverse(URL_NAME), {"guilds": [one.pk]})
        joined.assert_called_once_with(one, member)

    def it_saves_with_no_picks_as_an_answered_empty_choice(client: Client):
        _user, member = _linked_user(client)
        GuildFactory()
        response = client.post(reverse(URL_NAME), {})
        assert response["Location"] == reverse("hub_home")
        member.refresh_from_db()
        assert member.guild_updates_prompt_answered_at is not None
        assert member.guild_memberships.count() == 0
        assert "You didn't pick any guilds. You can choose some anytime in Settings." in _messages(response)

    def it_skips_with_a_stamp_and_no_rows(client: Client):
        _user, member = _linked_user(client)
        GuildFactory()
        response = client.post(reverse(URL_NAME), {"skip": "1"})
        assert response["Location"] == reverse("hub_home")
        member.refresh_from_db()
        assert member.guild_updates_prompt_answered_at is not None
        assert member.guild_memberships.count() == 0
        assert "No problem. You can pick guilds anytime in Settings." in _messages(response)

    def it_discards_checked_picks_when_skip_is_posted(client: Client):
        # Pins the discard semantics even though the UI disables Skip once anything is picked.
        _user, member = _linked_user(client)
        guild = GuildFactory()
        response = client.post(reverse(URL_NAME), {"skip": "1", "guilds": [guild.pk]})
        assert response["Location"] == reverse("hub_home")
        member.refresh_from_db()
        assert member.guild_updates_prompt_answered_at is not None
        assert member.guild_memberships.count() == 0

    def it_rejects_a_tampered_inactive_guild_pk_without_stamping(client: Client):
        _user, member = _linked_user(client)
        GuildFactory(name="Active")
        inactive = GuildFactory(name="Sleeping", is_active=False)
        response = client.post(reverse(URL_NAME), {"guilds": [inactive.pk]})
        assert response.status_code == 200
        assert "Pick guilds from the list." in response.content.decode()
        member.refresh_from_db()
        assert member.guild_updates_prompt_answered_at is None
        assert GuildMembership.objects.count() == 0

    def it_preserves_valid_checks_on_a_tampered_re_render(client: Client):
        import re

        _linked_user(client)
        active = GuildFactory(name="Active")
        inactive = GuildFactory(name="Sleeping", is_active=False)
        response = client.post(reverse(URL_NAME), {"guilds": [active.pk, inactive.pk]})
        html = response.content.decode()
        tag = next(t for t in _checkbox_tags(html) if f'value="{active.pk}"' in t)
        assert re.search(r"\schecked", tag)
        # The Alpine picked counter seeds from the VALID rendered checks only (1, not
        # the raw POST's 2) — otherwise Skip would stay stuck disabled after the
        # member unchecks every visible box.
        assert 'x-data="{ picked: 1 }"' in html

    def it_redirects_an_ineligible_post_home_without_changes(client: Client):
        _user, member = _linked_user(client)
        guild = GuildFactory()
        member.mark_guild_updates_answered()
        response = client.post(reverse(URL_NAME), {"guilds": [guild.pk]})
        assert response["Location"] == reverse("hub_home")
        assert member.guild_memberships.count() == 0


def describe_login_routing():
    code = "ABCDEF"

    def _login_ready_member(username: str) -> Member:
        member = MemberFactory(_pre_signup_email=f"{username}@example.com")
        provision_user_for_member(member)
        return member

    def _log_in(client: Client, member: Member, *, next_url: str | None = None) -> object:
        request_url = reverse("account_request_login_code")
        if next_url is not None:
            request_url += f"?next={next_url}"
        with patch(
            "allauth.account.adapter.DefaultAccountAdapter.generate_login_code",
            return_value=code,
        ):
            sent = client.post(request_url, {"email": member.primary_email})
        # The request step 302s to the confirm URL, carrying ?next= through — POST
        # the code to that exact URL so the passthrough survives (as a browser would).
        return client.post(sent["Location"], {"code": code})

    def it_lands_an_unanswered_zero_subscription_member_on_the_prompt(client: Client):
        GuildFactory()
        member = _login_ready_member("prompt_me")
        response = _log_in(client, member)
        assert response["Location"] == reverse(URL_NAME)

    def it_lands_a_subscribed_member_on_hub_home(client: Client):
        member = _login_ready_member("already_in")
        GuildMembershipFactory(member=member)
        response = _log_in(client, member)
        assert response["Location"] == reverse("hub_home")

    def it_lands_a_stamped_member_on_hub_home(client: Client):
        member = _login_ready_member("stamped")
        member.mark_guild_updates_answered()
        response = _log_in(client, member)
        assert response["Location"] == reverse("hub_home")

    def it_documents_the_next_param_bypass(client: Client):
        # Accepted gap: allauth only consults get_login_redirect_url with no ?next=,
        # so a deep-linked login skips the prompt that session. The onboarding
        # checklist step is the backstop.
        GuildFactory()
        member = _login_ready_member("deep_link")
        response = _log_in(client, member, next_url="/members/")
        assert response["Location"] == "/members/"
