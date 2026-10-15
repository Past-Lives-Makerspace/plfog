"""BDD specs for the Staff tab's Class Reviewer switch (#816).

Every staff row on guild edit > Staff carries a "Class Reviewer" switch. The guild lead and
admins flip it over HTMX; other staff see its state but cannot change it, and a POST from
anyone else is refused. Flipping it is quiet: nobody is emailed or notified.
"""

from __future__ import annotations

import json
import re

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse

from core.models import Notification
from membership.models import Guild, GuildStaffMembership, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

Role = GuildStaffMembership.Role


def _user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.full_legal_name = username.title()
    member.save(update_fields=["fog_role", "full_legal_name"])
    member.sync_user_permissions()
    return user


class _Guild:
    def __init__(self) -> None:
        self.lead = _user("crlead")
        self.staff = _user("crstaff")
        self.other_staff = _user("crother")
        self.guild = GuildFactory(name="Reviewer Guild", guild_lead=self.lead.member)
        GuildStaffMembershipFactory(guild=self.guild, member=self.staff.member, role=Role.SECRETARY)
        GuildStaffMembershipFactory(guild=self.guild, member=self.staff.member, role=Role.ORIENTER)
        GuildStaffMembershipFactory(guild=self.guild, member=self.other_staff.member, role=Role.TREASURER)

    def url(self, member: Member) -> str:
        return reverse("hub_guild_staff_class_reviewer", args=[self.guild.pk, member.pk])

    def flags(self, user: User) -> set[bool]:
        return set(
            GuildStaffMembership.objects.filter(guild=self.guild, member=user.member).values_list(
                "can_review_classes", flat=True
            )
        )


@pytest.fixture
def setup() -> _Guild:
    return _Guild()


def _toast(response) -> str:
    return json.loads(response["HX-Trigger"])["showToast"]["message"]


def describe_the_switch_endpoint():
    def describe_as_the_guild_lead():
        def it_turns_class_reviewer_on_for_every_row_the_member_holds(client: Client, setup: _Guild):
            client.login(username="crlead", password="pass")
            response = client.post(setup.url(setup.staff.member), {"on": "on"})
            assert response.status_code == 204
            assert setup.flags(setup.staff) == {True}
            assert _toast(response) == "Crstaff now reviews Reviewer Guild classes."

        def it_turns_it_off_when_the_box_is_unchecked(client: Client, setup: _Guild):
            setup.guild.set_class_reviewer(setup.staff.member, True)
            client.login(username="crlead", password="pass")
            response = client.post(setup.url(setup.staff.member), {})
            assert response.status_code == 204
            assert setup.flags(setup.staff) == {False}
            assert _toast(response) == "Crstaff no longer reviews Reviewer Guild classes."

        def it_leaves_other_staff_alone(client: Client, setup: _Guild):
            client.login(username="crlead", password="pass")
            client.post(setup.url(setup.staff.member), {"on": "on"})
            assert setup.flags(setup.other_staff) == {False}

        def it_sends_nobody_anything(client: Client, setup: _Guild):
            mail.outbox = []
            client.login(username="crlead", password="pass")
            client.post(setup.url(setup.staff.member), {"on": "on"})
            assert mail.outbox == []
            assert Notification.objects.count() == 0

        def it_answers_404_for_a_member_not_on_this_guilds_staff(client: Client, setup: _Guild):
            outsider = _user("croutsider")
            client.login(username="crlead", password="pass")
            assert client.post(setup.url(outsider.member), {"on": "on"}).status_code == 404

        def it_refuses_a_get(client: Client, setup: _Guild):
            client.login(username="crlead", password="pass")
            assert client.get(setup.url(setup.staff.member)).status_code == 405

    def it_lets_an_admin_flip_it(client: Client, setup: _Guild):
        _user("cradmin", fog_role=Member.FogRole.ADMIN)
        client.login(username="cradmin", password="pass")
        assert client.post(setup.url(setup.staff.member), {"on": "on"}).status_code == 204
        assert setup.flags(setup.staff) == {True}

    def it_refuses_a_staff_member_who_is_not_the_lead(client: Client, setup: _Guild):
        client.login(username="crother", password="pass")
        assert client.post(setup.url(setup.staff.member), {"on": "on"}).status_code == 403
        assert setup.flags(setup.staff) == {False}

    def it_refuses_a_staff_member_turning_themselves_on(client: Client, setup: _Guild):
        client.login(username="crstaff", password="pass")
        assert client.post(setup.url(setup.staff.member), {"on": "on"}).status_code == 403
        assert setup.flags(setup.staff) == {False}

    def it_refuses_a_guild_officer(client: Client, setup: _Guild):
        _user("crofficer", fog_role=Member.FogRole.GUILD_OFFICER)
        client.login(username="crofficer", password="pass")
        assert client.post(setup.url(setup.staff.member), {"on": "on"}).status_code == 403

    def it_refuses_an_outsider(client: Client, setup: _Guild):
        _user("crstranger")
        client.login(username="crstranger", password="pass")
        assert client.post(setup.url(setup.staff.member), {"on": "on"}).status_code == 403
        assert setup.flags(setup.staff) == {False}

    def it_refuses_the_lead_of_another_guild(client: Client, setup: _Guild):
        other_lead = _user("crotherlead")
        GuildFactory(name="Another Guild", guild_lead=other_lead.member)
        client.login(username="crotherlead", password="pass")
        assert client.post(setup.url(setup.staff.member), {"on": "on"}).status_code == 403

    def it_sends_a_signed_out_visitor_to_log_in(client: Client, setup: _Guild):
        response = client.post(setup.url(setup.staff.member), {"on": "on"})
        assert response.status_code == 302
        assert setup.flags(setup.staff) == {False}


def _has_attribute(markup: str, name: str) -> bool:
    """Whether ``name`` appears as a bare attribute (``checked``, ``disabled``), not inside another one."""
    return re.search(rf"\s{name}(\s|>)", markup) is not None


def describe_the_staff_tab():
    def _staff_tab(client: Client, username: str, guild: Guild) -> str:
        client.login(username=username, password="pass")
        response = client.get(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=staff")
        assert response.status_code == 200
        return response.content.decode()

    def _switch(html: str, member: Member) -> str:
        """The markup of one staff row's Class Reviewer switch."""
        start = html.index(f'data-class-reviewer-row="{member.pk}"')
        return html[start : html.index("</label>", start)]

    def it_shows_the_lead_a_live_switch_on_every_staff_row(client: Client, setup: _Guild):
        html = _staff_tab(client, "crlead", setup.guild)
        for user in (setup.staff, setup.other_staff):
            switch = _switch(html, user.member)
            assert "Class Reviewer" in switch
            assert f'hx-post="{setup.url(user.member)}"' in switch
            assert not _has_attribute(switch, "disabled")

    def it_shows_the_current_state(client: Client, setup: _Guild):
        setup.guild.set_class_reviewer(setup.staff.member, True)
        html = _staff_tab(client, "crlead", setup.guild)
        assert _has_attribute(_switch(html, setup.staff.member), "checked")
        assert not _has_attribute(_switch(html, setup.other_staff.member), "checked")

    def it_shows_other_staff_the_state_read_only(client: Client, setup: _Guild):
        setup.guild.set_class_reviewer(setup.staff.member, True)
        html = _staff_tab(client, "crother", setup.guild)
        switch = _switch(html, setup.staff.member)
        assert _has_attribute(switch, "checked")
        assert _has_attribute(switch, "disabled")
        assert "hx-post" not in switch

    def it_says_class_review_is_the_exception_to_shared_access(client: Client, setup: _Guild):
        html = _staff_tab(client, "crlead", setup.guild)
        assert "same access as the\n      guild lead except class review" in html
        assert "Every staff member gets the same access as the\n      guild lead —" not in html
