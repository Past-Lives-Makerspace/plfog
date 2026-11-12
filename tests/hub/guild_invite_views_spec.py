"""BDD specs for Share and Invite on the guild page (#1137): hub/guild_invite_views.py and the guild page.

Share renders for every viewer of a guild members can see, on the members site and on the guilds
site, carrying the guild's public link. Invite (the button, the panel and Send Invites) is the
guild's lead, its staff and admins only: another guild's lead, a plain member, a guest and an admin
previewing as a member are refused with nothing sent, and neither button shows on a hidden guild.
An invite is accepted only by its invitee, from the banner's Join, which joins through the hero
Join's path; opening the guild page never joins anyone. Assertions anchor on markup and factory strings.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, override_settings
from django.urls import reverse

from core.models import Notification
from membership.models import Guild, GuildInvite, GuildMembership, Member
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

GUILDS_HOST = "guilds.pastlives.space"
GUILDS_SETTINGS = dict(
    ALLOWED_HOSTS=["guilds.pastlives.space", "members.pastlives.space", "book.pastlives.space", "testserver"],
    GUILDS_HOSTS=["guilds.pastlives.space"],
    GUILDS_BASE_URL="https://guilds.pastlives.space",
    MEMBER_BASE_URL="https://members.pastlives.space",
    BOOK_BASE_URL="https://book.pastlives.space",
)


def _account(username: str, name: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    member = user.member
    member.fog_role = fog_role
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save(update_fields=["fog_role", "status", "full_legal_name"])
    member.sync_user_permissions()
    return member


def _login(client: Client, member: Member) -> None:
    client.force_login(member.user)


def _guild(**fields: object) -> Guild:
    return GuildFactory(name="Thistledown Letterpress Guild", **fields)


def _panel_url(guild: Guild) -> str:
    return reverse("hub_guild_invite_panel", args=[guild.pk])


def _send_url(guild: Guild) -> str:
    return reverse("hub_guild_invite_send", args=[guild.pk])


def _trigger(response: object) -> dict:
    return json.loads(response["HX-Trigger"])


def describe_invite_permissions():
    def it_opens_the_panel_and_sends_for_the_lead_staff_and_an_admin(
        client: Client, django_capture_on_commit_callbacks
    ):
        lead = _account("lead", "Orla Thistledown")
        staff = _account("staff", "Staff Person")
        admin = _account("admin", "Admin Person", fog_role=Member.FogRole.ADMIN)
        guild = _guild(guild_lead=lead)
        GuildStaffMembershipFactory(guild=guild, member=staff)
        for sender, invitee in ((lead, "first"), (staff, "second"), (admin, "third")):
            pick = _account(invitee, f"Invitee {invitee.title()}")
            _login(client, sender)
            assert client.get(_panel_url(guild)).status_code == 200
            with django_capture_on_commit_callbacks(execute=True):
                response = client.post(_send_url(guild), {"members": [pick.pk]})
            assert response.status_code == 200
            assert GuildInvite.objects.filter(guild=guild, member=pick, invited_by=sender).exists()
        assert len(mail.outbox) == 3

    def it_refuses_another_guilds_lead_a_member_and_an_admin_previewing_as_a_member(client: Client):
        guild = _guild()
        pick = _account("pick", "Pick Person")
        other_lead = _account("other_lead", "Other Lead Person")
        GuildFactory(name="Elsewhere Guild", guild_lead=other_lead)
        plain = _account("plain", "Plain Member")
        admin = _account("admin", "Admin Person", fog_role=Member.FogRole.ADMIN)
        for viewer in (other_lead, plain, admin):
            _login(client, viewer)
            if viewer is admin:
                session = client.session
                session["view_as_role"] = "member"
                session.save()
            assert client.get(_panel_url(guild)).status_code == 404
            assert client.post(_send_url(guild), {"members": [pick.pk]}).status_code == 404
        assert not GuildInvite.objects.exists()
        assert mail.outbox == []

    def it_sends_a_guest_to_sign_in_and_sends_nothing(client: Client):
        guild = _guild()
        pick = _account("pick", "Pick Person")
        assert client.get(_panel_url(guild)).status_code == 302
        assert client.post(_send_url(guild), {"members": [pick.pk]}).status_code == 302
        assert not GuildInvite.objects.exists()

    def it_refuses_even_the_lead_and_an_admin_on_a_hidden_guild(client: Client):
        lead = _account("lead", "Orla Thistledown")
        admin = _account("admin", "Admin Person", fog_role=Member.FogRole.ADMIN)
        guild = _guild(guild_lead=lead, is_active=False)
        pick = _account("pick", "Pick Person")
        for viewer in (lead, admin):
            _login(client, viewer)
            assert client.get(_panel_url(guild)).status_code == 404
            assert client.post(_send_url(guild), {"members": [pick.pk]}).status_code == 404
        assert not GuildInvite.objects.exists()


def describe_invite_panel():
    def it_lists_members_not_in_the_guild_and_not_invited(client: Client):
        lead = _account("lead", "Orla Thistledown")
        guild = _guild(guild_lead=lead)
        _account("candidate", "Candidate Quenby")
        joined = _account("joined", "Joined Rookwood")
        GuildMembership.objects.create(guild=guild, member=joined)
        invited = _account("invited", "Invited Marchbanks")
        GuildInvite.objects.create(guild=guild, member=invited)
        _login(client, lead)
        content = client.get(_panel_url(guild)).content.decode()
        assert "Candidate Quenby · candidate@example.com" in content
        assert 'data-search="candidate quenby · candidate@example.com"' in content
        assert "Joined Rookwood" not in content
        assert "Invited Marchbanks" not in content
        assert f'hx-post="{_send_url(guild)}"' in content
        assert "data-guild-invite-filter" in content

    def it_says_so_when_nobody_is_left_to_invite(client: Client):
        lead = _account("lead", "Orla Thistledown")
        guild = _guild(guild_lead=lead)
        GuildMembership.objects.create(guild=guild, member=lead)
        _login(client, lead)
        content = client.get(_panel_url(guild)).content.decode()
        assert "data-guild-invite-empty" in content
        assert "data-guild-invite-send" not in content


def describe_send_invites_view():
    def it_invites_three_and_tells_each_once(client: Client, django_capture_on_commit_callbacks):
        lead = _account("lead", "Orla Thistledown")
        guild = _guild(guild_lead=lead)
        picks = [_account(f"pick{n}", f"Pick Number{n}") for n in range(3)]
        _login(client, lead)
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(_send_url(guild), {"members": [pick.pk for pick in picks]})
        trigger = _trigger(response)
        assert trigger["showToast"] == {"message": "Sent 3 invites.", "type": "success"}
        assert trigger["guild-invites-sent"] is True
        assert GuildInvite.objects.open().filter(guild=guild).count() == 3
        assert len(mail.outbox) == 3
        assert Notification.objects.filter(trigger="guild.invited").count() == 3
        # The panel comes back without the people just invited.
        assert "Pick Number0" not in response.content.decode()

        with django_capture_on_commit_callbacks(execute=True):
            again = client.post(_send_url(guild), {"members": [pick.pk for pick in picks]})
        assert _trigger(again)["showToast"] == {
            "message": "No invites sent. 3 people were already in the guild or already invited.",
            "type": "info",
        }
        assert "guild-invites-sent" not in _trigger(again)
        assert len(mail.outbox) == 3
        assert Notification.objects.filter(trigger="guild.invited").count() == 3

    def it_adds_nobody_to_the_guild_until_they_join(client: Client, django_capture_on_commit_callbacks):
        lead = _account("lead", "Orla Thistledown")
        guild = _guild(guild_lead=lead)
        pick = _account("pick", "Pick Person")
        _login(client, lead)
        with django_capture_on_commit_callbacks(execute=True):
            client.post(_send_url(guild), {"members": [pick.pk]})
        assert guild.memberships.count() == 0
        assert not Notification.objects.filter(user=lead.user, trigger="guild_joined").exists()

    def it_refuses_a_send_with_nobody_picked(client: Client):
        lead = _account("lead", "Orla Thistledown")
        guild = _guild(guild_lead=lead)
        _login(client, lead)
        response = client.post(_send_url(guild), {})
        assert response.status_code == 200
        assert _trigger(response)["showToast"] == {"message": "Pick at least one member to invite.", "type": "error"}
        assert not GuildInvite.objects.exists()

    def it_refuses_a_crafted_send_naming_a_former_member(client: Client, django_capture_on_commit_callbacks):
        lead = _account("lead", "Orla Thistledown")
        guild = _guild(guild_lead=lead)
        former = _account("former", "Former Fairweather")
        Member.objects.filter(pk=former.pk).update(status=Member.Status.FORMER)
        _login(client, lead)
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(_send_url(guild), {"members": [former.pk]})
        assert response.status_code == 200
        assert _trigger(response)["showToast"]["type"] == "error"
        assert not GuildInvite.objects.exists()
        assert mail.outbox == []
        assert not Notification.objects.filter(trigger="guild.invited").exists()

    def it_answers_a_get_with_405(client: Client):
        lead = _account("lead", "Orla Thistledown")
        guild = _guild(guild_lead=lead)
        _login(client, lead)
        assert client.get(_send_url(guild)).status_code == 405


def describe_accept_invite():
    def _invite(guild: Guild, invitee: Member, sender: Member | None = None) -> GuildInvite:
        return GuildInvite.objects.create(guild=guild, member=invitee, invited_by=sender)

    def it_joins_through_the_hero_joins_path_and_clears_the_banner(client: Client):
        lead = _account("lead", "Orla Thistledown")
        guild = _guild(guild_lead=lead)
        GuildOrientationSettingsFactory(guild=guild)
        invitee = _account("invitee", "Wren Invitee")
        invite = _invite(guild, invitee, lead)
        _login(client, invitee)
        with mock.patch("core.events.discord_roles.on_membership_changed") as role:
            response = client.post(reverse("hub_guild_invite_accept", args=[invite.pk]))
        assert response.status_code == 200
        membership = GuildMembership.objects.get(guild=guild, member=invitee)
        assert membership.source == GuildMembership.Source.INVITE
        invite.refresh_from_db()
        assert not invite.is_open
        role.assert_called_once_with(guild, invitee, joined=True)
        assert Notification.objects.filter(user=lead.user, trigger="guild_joined").count() == 1
        assert [message.to for message in mail.outbox] == [[invitee.primary_email]]  # the welcome email
        content = response.content.decode()
        assert 'id="guild-join-cta"' in content
        assert '<div id="guild-invite-banner" hx-swap-oob="true">' in content
        assert "data-guild-invite-banner" not in content
        assert _trigger(response)["showToast"]["message"] == (
            "You joined Thistledown Letterpress Guild! Check your inbox for your welcome email."
        )

    def it_is_a_404_to_anyone_but_the_invitee(client: Client):
        guild = _guild()
        invite = _invite(guild, _account("invitee", "Wren Invitee"))
        other = _account("other", "Someone Else")
        _login(client, other)
        assert client.post(reverse("hub_guild_invite_accept", args=[invite.pk])).status_code == 404
        assert not GuildMembership.objects.exists()
        invite.refresh_from_db()
        assert invite.is_open

    def it_is_a_404_for_an_invite_already_closed(client: Client):
        # A closed invite cannot be accepted again, even by its invitee who has since left the guild.
        guild = _guild()
        invitee = _account("invitee", "Wren Invitee")
        invite = _invite(guild, invitee)
        invitee.subscribe_to_guild(guild)
        invitee.unsubscribe_from_guild(guild)
        _login(client, invitee)
        assert client.post(reverse("hub_guild_invite_accept", args=[invite.pk])).status_code == 404
        assert not GuildMembership.objects.filter(guild=guild, member=invitee).exists()

    def it_is_a_404_to_an_account_with_no_membership(client: Client):
        guild = _guild()
        invite = _invite(guild, _account("invitee", "Wren Invitee"))
        MembershipPlanFactory()
        loose = User.objects.create_user(username="loose", password="pass", email="loose@example.com")
        Member.objects.filter(user=loose).update(user=None)
        client.force_login(loose)
        assert client.post(reverse("hub_guild_invite_accept", args=[invite.pk])).status_code == 404

    def it_is_a_404_on_a_guild_hidden_since(client: Client):
        guild = _guild()
        invitee = _account("invitee", "Wren Invitee")
        invite = _invite(guild, invitee)
        Guild.objects.filter(pk=guild.pk).update(is_active=False)
        _login(client, invitee)
        assert client.post(reverse("hub_guild_invite_accept", args=[invite.pk])).status_code == 404
        assert not GuildMembership.objects.exists()

    def it_leaves_someone_already_in_the_guild_as_they_were(client: Client):
        lead = _account("lead", "Orla Thistledown")
        guild = _guild(guild_lead=lead)
        invitee = _account("invitee", "Wren Invitee")
        GuildMembership.objects.create(guild=guild, member=invitee)
        invite = _invite(guild, invitee, lead)
        _login(client, invitee)
        response = client.post(reverse("hub_guild_invite_accept", args=[invite.pk]))
        assert response.status_code == 200
        assert GuildMembership.objects.filter(guild=guild, member=invitee).count() == 1
        assert GuildMembership.objects.get(guild=guild, member=invitee).source == GuildMembership.Source.APP
        assert not Notification.objects.filter(user=lead.user, trigger="guild_joined").exists()
        invite.refresh_from_db()
        assert not invite.is_open

    def it_answers_a_get_with_405(client: Client):
        guild = _guild()
        invitee = _account("invitee", "Wren Invitee")
        invite = _invite(guild, invitee)
        _login(client, invitee)
        assert client.get(reverse("hub_guild_invite_accept", args=[invite.pk])).status_code == 405


def describe_guild_page_share_and_invite():
    def it_shows_a_member_share_with_the_public_link_and_no_invite(client: Client):
        guild = _guild()
        _login(client, _account("plain", "Plain Member"))
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "data-guild-share" in content
        assert f'data-share-url="{guild.public_url}"' in content
        assert f'data-share-copy="{guild.public_url}"' in content
        assert "js/class_share" in content
        assert "data-guild-invite-open" not in content
        assert 'id="guild-invite-body"' not in content

    def it_shows_the_lead_invite_and_its_modal(client: Client):
        lead = _account("lead", "Orla Thistledown")
        guild = _guild(guild_lead=lead)
        _login(client, lead)
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "data-guild-share" in content
        assert "data-guild-invite-open" in content
        assert f'hx-get="{_panel_url(guild)}"' in content
        assert 'id="guild-invite-body"' in content

    def it_shows_neither_on_a_hidden_guild(client: Client):
        lead = _account("lead", "Orla Thistledown")
        guild = _guild(guild_lead=lead, is_active=False)
        _login(client, lead)
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "data-guild-share" not in content
        assert "data-guild-invite-open" not in content

    @override_settings(**GUILDS_SETTINGS)
    def it_shows_a_guest_on_the_guilds_site_share_with_the_public_link(client: Client):
        guild = _guild()
        content = client.get(f"/guilds/{guild.slug}/", HTTP_HOST=GUILDS_HOST).content.decode()
        assert f'data-share-url="https://guilds.pastlives.space/guilds/{guild.slug}/"' in content
        assert "data-guild-invite-open" not in content

    @override_settings(**GUILDS_SETTINGS)
    def it_keeps_invite_off_the_guilds_site_even_for_the_lead(client: Client):
        lead = _account("lead", "Orla Thistledown")
        guild = _guild(guild_lead=lead)
        _login(client, lead)
        content = client.get(f"/guilds/{guild.slug}/", HTTP_HOST=GUILDS_HOST).content.decode()
        assert "data-guild-share" in content
        assert "data-guild-invite-open" not in content
        assert "data-guild-invite-banner" not in content


def describe_invite_banner():
    def it_offers_the_invitee_join_and_opening_the_page_joins_nobody(client: Client):
        lead = _account("lead", "Orla Thistledown")
        guild = _guild(guild_lead=lead)
        invitee = _account("invitee", "Wren Invitee")
        invite = GuildInvite.objects.create(guild=guild, member=invitee, invited_by=lead)
        _login(client, invitee)
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "data-guild-invite-banner" in content
        assert "Orla Thistledown invited you to join Thistledown Letterpress Guild" in content
        assert f'hx-post="{reverse("hub_guild_invite_accept", args=[invite.pk])}"' in content
        assert not GuildMembership.objects.exists()
        invite.refresh_from_db()
        assert invite.is_open

    def it_words_the_banner_without_a_sender(client: Client):
        guild = _guild()
        invitee = _account("invitee", "Wren Invitee")
        GuildInvite.objects.create(guild=guild, member=invitee)
        _login(client, invitee)
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "You're invited to join Thistledown Letterpress Guild" in content

    def it_shows_no_banner_to_someone_who_joined_on_their_own_and_closes_the_invite(client: Client):
        guild = _guild()
        invitee = _account("invitee", "Wren Invitee")
        invite = GuildInvite.objects.create(guild=guild, member=invitee)
        GuildMembership.objects.record_discord_join(guild, invitee)
        _login(client, invitee)
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert '<div id="guild-invite-banner">' in content
        assert "data-guild-invite-banner" not in content
        invite.refresh_from_db()
        assert not invite.is_open

    def it_shows_no_banner_to_someone_else(client: Client):
        guild = _guild()
        GuildInvite.objects.create(guild=guild, member=_account("invitee", "Wren Invitee"))
        _login(client, _account("other", "Someone Else"))
        content = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "data-guild-invite-banner" not in content

    def it_goes_when_the_hero_join_is_used_instead(client: Client):
        guild = _guild()
        invitee = _account("invitee", "Wren Invitee")
        invite = GuildInvite.objects.create(guild=guild, member=invitee)
        _login(client, invitee)
        response = client.post(reverse("hub_guild_join", args=[guild.pk]), {})
        assert '<div id="guild-invite-banner" hx-swap-oob="true">' in response.content.decode()
        invite.refresh_from_db()
        assert not invite.is_open
        assert GuildMembership.objects.get(guild=guild, member=invitee).source == GuildMembership.Source.APP
