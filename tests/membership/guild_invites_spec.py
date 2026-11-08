"""BDD specs for guild invites (#1137): membership/guild_invites.py and GuildInvite.

Who the Invite panel lists, opening invites once per member (a repeat send is a no op), the bell
and email each invitee gets once, the banner's invite (closed when the invitee already joined),
joining with source "invite" closing the invite, and the rule for who may invite.
"""

from __future__ import annotations


import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db import IntegrityError
from django.test import override_settings

from core.models import Notification
from membership import guild_invites
from membership.models import Guild, GuildInvite, GuildMembership, Member
from tests.membership.factories import GuildFactory, MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _account(username: str, name: str, **fields: object) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    member = user.member
    member.full_legal_name = name
    member.status = Member.Status.ACTIVE
    for key, value in fields.items():
        setattr(member, key, value)
    member.save()
    member.sync_user_permissions()
    return member


def _guild(**fields: object) -> Guild:
    return GuildFactory(name="Quillmere Bookbinding Guild", **fields)


def describe_invite_candidates():
    def it_lists_active_members_by_name_less_members_and_open_invitees():
        guild = _guild()
        zed = _account("zed", "Zed Applewhite")
        amy = _account("amy", "amy Brightwater")
        joined = _account("joined", "Joined Already")
        GuildMembership.objects.create(guild=guild, member=joined)
        invited = _account("invited", "Invited Already")
        GuildInvite.objects.create(guild=guild, member=invited)
        assert [member.pk for member in guild_invites.invite_candidates(guild)] == [amy.pk, zed.pk]

    def it_leaves_out_inactive_hidden_and_accountless_members():
        guild = _guild()
        _account("former", "Former Member", status=Member.Status.FORMER)
        _account("hidden", "Hidden Member", hide_from_directory=True)
        gone = _account("gone", "Gone Account")
        gone.user.is_active = False
        gone.user.save(update_fields=["is_active"])
        MemberFactory(full_legal_name="No Account Yet", user=None)
        assert guild_invites.invite_candidates(guild) == []

    def it_lists_again_someone_whose_invite_closed_and_who_left_since():
        guild = _guild()
        returning = _account("returning", "Returning Member")
        GuildInvite.objects.create(guild=guild, member=returning, accepted_at="2026-11-01T12:00:00Z")
        assert guild_invites.invite_candidates(guild) == [returning]

    def it_labels_each_row_with_the_composers_name_and_email():
        guild = _guild()
        member = _account("labelled", "Labelled Person")
        assert guild_invites.candidate_rows(guild) == [(member.pk, "Labelled Person · labelled@example.com")]


def describe_send_invites():
    def it_opens_one_invite_per_member_and_tells_each_once(django_capture_on_commit_callbacks):
        lead = _account("lead", "Orla Quillmere")
        guild = _guild(guild_lead=lead)
        picks = [_account(f"pick{n}", f"Pick Number{n}") for n in range(3)]
        with django_capture_on_commit_callbacks(execute=True):
            result = guild_invites.send_invites(guild, picks, lead)
        assert result.sent == picks
        assert result.skipped == []
        assert GuildInvite.objects.open().filter(guild=guild, invited_by=lead).count() == 3
        assert sorted(message.to[0] for message in mail.outbox) == [
            "pick0@example.com",
            "pick1@example.com",
            "pick2@example.com",
        ]
        assert mail.outbox[0].subject == "Orla Quillmere invited you to join Quillmere Bookbinding Guild"
        for pick in picks:
            bell = Notification.objects.get(user=pick.user, trigger="guild.invited")
            assert bell.title == "Orla Quillmere invited you to join Quillmere Bookbinding Guild"
            assert bell.url == f"/guilds/{guild.slug}/"

    def it_sends_nothing_more_to_someone_already_invited(django_capture_on_commit_callbacks):
        lead = _account("lead", "Orla Quillmere")
        guild = _guild(guild_lead=lead)
        pick = _account("pick", "Pick Person")
        with django_capture_on_commit_callbacks(execute=True):
            guild_invites.send_invites(guild, [pick], lead)
        with django_capture_on_commit_callbacks(execute=True):
            again = guild_invites.send_invites(guild, [pick, pick], lead)
        assert again.sent == []
        assert again.skipped == [pick]
        assert GuildInvite.objects.filter(guild=guild, member=pick).count() == 1
        assert len(mail.outbox) == 1
        assert Notification.objects.filter(user=pick.user, trigger="guild.invited").count() == 1

    def it_skips_a_member_already_in_the_guild():
        guild = _guild()
        joined = _account("joined", "Joined Already")
        GuildMembership.objects.create(guild=guild, member=joined)
        result = guild_invites.send_invites(guild, [joined], None)
        assert result.skipped == [joined]
        assert not GuildInvite.objects.exists()

    def it_skips_a_member_a_concurrent_send_just_invited():
        guild = _guild()
        pick = _account("pick", "Pick Person")

        def picks_while_another_send_lands():
            # The other send opens its invite after this one took its snapshot of open invites.
            GuildInvite.objects.create(guild=guild, member=pick)
            yield pick

        result = guild_invites.send_invites(guild, picks_while_another_send_lands(), None)
        assert result.sent == []
        assert result.skipped == [pick]
        assert GuildInvite.objects.filter(guild=guild, member=pick).count() == 1

    def it_refuses_a_second_open_invite_in_the_database():
        guild = _guild()
        pick = _account("pick", "Pick Person")
        GuildInvite.objects.create(guild=guild, member=pick)
        with pytest.raises(IntegrityError):
            GuildInvite.objects.create(guild=guild, member=pick)


def describe_invite_result_toast():
    def it_counts_what_went_out():
        one = Member(full_legal_name="One")
        two = Member(full_legal_name="Two")
        assert guild_invites.InviteResult(sent=[one]).toast() == "Sent 1 invite."
        assert guild_invites.InviteResult(sent=[one, two]).toast() == "Sent 2 invites."

    def it_says_why_some_were_skipped():
        one = Member(full_legal_name="One")
        two = Member(full_legal_name="Two")
        assert (
            guild_invites.InviteResult(sent=[one], skipped=[two]).toast()
            == "Sent 1 invite. 1 person was already in the guild or already invited."
        )
        assert (
            guild_invites.InviteResult(skipped=[one, two]).toast()
            == "No invites sent. 2 people were already in the guild or already invited."
        )


def describe_invite_email():
    def it_words_it_as_the_banner_does_when_the_sender_is_gone():
        guild = _guild()
        invite = GuildInvite.objects.create(guild=guild, member=_account("pick", "Pick Person"))
        assert guild_invites.invited_subject(invite) == "You're invited to join Quillmere Bookbinding Guild"
        context = guild_invites.email_context(invite)
        assert context["headline"] == "You're invited to join Quillmere Bookbinding Guild"
        assert context["inviter_name"] == ""

    @override_settings(MEMBER_BASE_URL="https://members.example.test")
    def it_links_join_to_the_guild_page_and_greets_by_first_name():
        guild = _guild(about="<p>We fold, sew and glue books by hand.</p>")
        invite = GuildInvite.objects.create(guild=guild, member=_account("pick", "Wren Pickering"))
        context = guild_invites.email_context(invite)
        assert context["first_name"] == "Wren"
        assert context["join_url"] == f"https://members.example.test/guilds/{guild.slug}/"

    def it_renders_both_bodies_with_the_join_link(django_capture_on_commit_callbacks):
        lead = _account("lead", "Orla Quillmere")
        guild = _guild(guild_lead=lead, about="<p>We fold, sew and glue books by hand.</p>")
        with django_capture_on_commit_callbacks(execute=True):
            guild_invites.send_invites(guild, [_account("pick", "Wren Pickering")], lead)
        message = mail.outbox[0]
        html = message.alternatives[0][0]
        assert f"/guilds/{guild.slug}/" in message.body
        assert "We fold, sew and glue books by hand." in message.body
        assert f"/guilds/{guild.slug}/" in html
        assert "Join Quillmere Bookbinding Guild" in html


def describe_banner_invite():
    def it_is_none_without_an_open_invite():
        assert guild_invites.banner_invite(_guild(), _account("m", "Some Member"), is_member=False) is None

    def it_offers_the_open_invite():
        guild = _guild()
        member = _account("m", "Some Member")
        invite = GuildInvite.objects.create(guild=guild, member=member)
        assert guild_invites.banner_invite(guild, member, is_member=False) == invite

    def it_closes_the_invite_of_someone_who_joined_another_way():
        guild = _guild()
        member = _account("m", "Some Member")
        invite = GuildInvite.objects.create(guild=guild, member=member)
        assert guild_invites.banner_invite(guild, member, is_member=True) is None
        invite.refresh_from_db()
        assert not invite.is_open


def describe_subscribe_to_guild_with_an_invite():
    def it_joins_as_invite_and_closes_the_open_invite():
        guild = _guild()
        member = _account("m", "Some Member")
        invite = GuildInvite.objects.create(guild=guild, member=member)
        assert member.subscribe_to_guild(guild, GuildMembership.Source.INVITE) is True
        assert GuildMembership.objects.get(guild=guild, member=member).source == GuildMembership.Source.INVITE
        invite.refresh_from_db()
        assert invite.accepted_at is not None

    def it_closes_the_invite_on_an_ordinary_join_too():
        guild = _guild()
        member = _account("m", "Some Member")
        invite = GuildInvite.objects.create(guild=guild, member=member)
        member.subscribe_to_guild(guild)
        invite.refresh_from_db()
        assert not invite.is_open
        assert GuildMembership.objects.get(guild=guild, member=member).source == GuildMembership.Source.APP

    def it_never_rewrites_an_invite_row_or_fires_the_join_again():
        guild = _guild()
        member = _account("m", "Some Member")
        member.subscribe_to_guild(guild, GuildMembership.Source.INVITE)
        assert member.subscribe_to_guild(guild) is False
        assert GuildMembership.objects.get(guild=guild, member=member).source == GuildMembership.Source.INVITE

    def it_upgrades_a_discord_row_to_invite():
        guild = _guild()
        member = _account("m", "Some Member")
        GuildMembership.objects.record_discord_join(guild, member)
        assert member.subscribe_to_guild(guild, GuildMembership.Source.INVITE) is True
        assert GuildMembership.objects.get(guild=guild, member=member).source == GuildMembership.Source.INVITE


def describe_guild_invite_model():
    def it_reads_as_who_was_invited_where():
        guild = _guild()
        invite = GuildInvite.objects.create(guild=guild, member=_account("m", "Some Member"))
        assert str(invite) == "Some Member invited to Quillmere Bookbinding Guild"

    @override_settings(GUILDS_BASE_URL="https://guilds.example.test")
    def it_gives_the_guild_its_public_link():
        guild = _guild()
        assert guild.public_url == f"https://guilds.example.test/guilds/{guild.slug}/"
