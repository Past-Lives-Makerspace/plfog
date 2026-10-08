"""Subscribe/unsubscribe to a guild's updates + gallery image delete."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from membership.models import GuildImage, GuildMembership, Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _member_user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    """Create a user (auto-linked to a Member via signal) with the given fog_role."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pw")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


@pytest.mark.django_db
def describe_subscribe_unsubscribe():
    def it_lets_a_member_subscribe_and_unsubscribe(client: Client):
        user = _member_user("m")
        client.login(username="m", password="pw")
        guild = GuildFactory()

        client.post(reverse("hub_guild_membership_set", args=[guild.pk]), {"joined": "on"})
        assert GuildMembership.objects.filter(guild=guild, member__user=user).exists()

        client.post(reverse("hub_guild_membership_set", args=[guild.pk]))
        assert not GuildMembership.objects.filter(guild=guild, member__user=user).exists()

    def it_routes_the_revived_join_and_leave_urls():
        # Revived with the "Join This Guild" front door (they were briefly removed).
        for name in ("hub_guild_join", "hub_guild_leave"):
            assert reverse(name, args=[1]) != ""


@pytest.mark.django_db
def describe_image_delete():
    def it_deletes_an_image_for_an_editor(client: Client):
        _member_user("a", fog_role=Member.FogRole.ADMIN)
        client.login(username="a", password="pw")
        guild = GuildFactory()
        img = GuildImage.objects.create(guild=guild, image=SimpleUploadedFile("x.png", _PNG))
        client.post(reverse("hub_guild_image_delete", args=[guild.pk, img.pk]))
        assert not GuildImage.objects.filter(pk=img.pk).exists()


@pytest.mark.django_db
def describe_subscribe_without_member():
    def it_is_a_noop_when_the_user_has_no_member(client: Client):
        user = User.objects.create_user(username="nomem", password="pw")
        Member.objects.filter(user=user).delete()
        client.login(username="nomem", password="pw")
        guild = GuildFactory()
        resp_on = client.post(reverse("hub_guild_membership_set", args=[guild.pk]), {"joined": "on"})
        assert resp_on.status_code == 204
        assert not GuildMembership.objects.filter(guild=guild).exists()
        # unsubscribe is likewise a no-op (and must not error)
        resp_off = client.post(reverse("hub_guild_membership_set", args=[guild.pk]))
        assert resp_off.status_code == 204


@pytest.mark.django_db
def describe_delete_permissions():
    def it_forbids_non_editors_from_deleting_an_image(client: Client):
        _member_user("plain_img")
        client.login(username="plain_img", password="pw")
        guild = GuildFactory()
        img = GuildImage.objects.create(guild=guild, image=SimpleUploadedFile("p.png", _PNG))
        resp = client.post(reverse("hub_guild_image_delete", args=[guild.pk, img.pk]))
        assert resp.status_code == 403
        assert GuildImage.objects.filter(pk=img.pk).exists()


@pytest.mark.django_db
def describe_members_card():
    """The guild page's Members card shows eight and folds the rest behind "Show all"."""

    def _page(client: Client, joined: int) -> str:
        from tests.membership.factories import GuildMembershipFactory, MemberFactory

        _member_user("viewer")
        client.login(username="viewer", password="pw")
        guild = GuildFactory(show_members=True)
        for i in range(joined):
            member = MemberFactory(preferred_name=f"Rosterperson{i:02d}", show_in_directory=True)
            GuildMembershipFactory(guild=guild, member=member)
        body = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        return body.split('id="guild-roster-card"')[1].split('<div class="hub-card">', 2)[1]

    def it_shows_eight_and_folds_the_rest_of_twelve(client: Client):
        card = _page(client, 12)
        shown, folded = card.split('<details class="pl-guild-roster__more">')

        assert '<span class="pl-guild-roster__count">12</span>' in shown
        assert shown.count("hub-member-row") == 8
        assert folded.split("</details>")[0].count("hub-member-row") == 4
        assert "Show all 12 members" in folded

    def it_has_nothing_to_fold_for_three(client: Client):
        card = _page(client, 3)

        assert card.count("hub-member-row") == 3
        assert "<details" not in card
        assert '<span class="pl-guild-roster__count">3</span>' in card

    def it_reads_the_roster_in_one_query_however_many_fold(client: Client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def _roster_queries(joined: int) -> int:
            from tests.membership.factories import GuildMembershipFactory, MemberFactory

            guild = GuildFactory(show_members=True)
            for _ in range(joined):
                GuildMembershipFactory(guild=guild, member=MemberFactory(show_in_directory=True))
            with CaptureQueriesContext(connection) as captured:
                client.get(reverse("hub_guild_detail", args=[guild.slug]))
            return sum(
                1
                for q in captured.captured_queries
                if "membership_guildmembership" in q["sql"] and "show_in_directory" in q["sql"]
            )

        _member_user("counter")
        client.login(username="counter", password="pw")

        assert _roster_queries(3) == 1
        assert _roster_queries(12) == 1
