"""BDD specs for the meeting permission helper: who may edit a meeting (#1113)."""

from __future__ import annotations


import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory
from django.utils import timezone

from core.models import EventDelivery
from hub.view_as import ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER, ViewAs
from membership.models import GuildStaffMembership
from membership.permissions import can_edit_meeting
from tests.membership.factories import (
    GuildFactory,
    GuildMembershipFactory,
    GuildStaffMembershipFactory,
    MeetingFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _user(username: str) -> User:
    MembershipPlanFactory()
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password="x", last_login=timezone.now()
    )


def _deliveries(event_key: str) -> int:
    return EventDelivery.objects.filter(event_key=event_key, channel="in_app").count()


def _request(user: User | AnonymousUser, *, roles: set[str] | None = None, picked: str | None = None):
    request = RequestFactory().get("/")
    request.user = user
    if roles is not None:
        request.view_as = ViewAs(actual=frozenset(roles), picked=picked)
    return request


def describe_can_edit_meeting():
    def describe_guild_meetings():
        def it_allows_the_guilds_lead():
            lead = _user("lead")
            meeting = MeetingFactory(guild=GuildFactory(guild_lead=lead.member))
            assert can_edit_meeting(_request(lead, roles={ROLE_MEMBER}), meeting) is True

        def it_allows_guild_staff():
            staff = _user("staff")
            guild = GuildFactory()
            GuildStaffMembershipFactory(guild=guild, member=staff.member, role=GuildStaffMembership.Role.SECRETARY)
            assert can_edit_meeting(_request(staff, roles={ROLE_MEMBER}), MeetingFactory(guild=guild)) is True

        def it_allows_an_admin():
            admin = _user("admin")
            assert can_edit_meeting(_request(admin, roles={ROLE_ADMIN, ROLE_MEMBER}), MeetingFactory()) is True

        def it_denies_a_plain_member():
            plain = _user("plain")
            meeting = MeetingFactory()
            GuildMembershipFactory(guild=meeting.guild, member=plain.member)
            assert can_edit_meeting(_request(plain, roles={ROLE_MEMBER}), meeting) is False

    def describe_council_meetings():
        def it_allows_an_admin():
            admin = _user("admin2")
            assert (
                can_edit_meeting(_request(admin, roles={ROLE_ADMIN, ROLE_MEMBER}), MeetingFactory(guild=None)) is True
            )

        def it_allows_a_guild_officer():
            officer = _user("officer")
            request = _request(officer, roles={ROLE_GUILD_OFFICER, ROLE_MEMBER})
            assert can_edit_meeting(request, MeetingFactory(guild=None)) is True

        def it_allows_any_guilds_lead_or_staff():
            lead = _user("anylead")
            GuildFactory(guild_lead=lead.member)
            assert can_edit_meeting(_request(lead, roles={ROLE_MEMBER}), MeetingFactory(guild=None)) is True

        def it_denies_a_plain_member():
            plain = _user("plain2")
            assert can_edit_meeting(_request(plain, roles={ROLE_MEMBER}), MeetingFactory(guild=None)) is False

        def it_denies_an_admin_previewing_as_member():
            admin = _user("admin3")
            request = _request(admin, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_MEMBER)
            assert can_edit_meeting(request, MeetingFactory(guild=None)) is False

        def it_denies_an_anonymous_request():
            assert can_edit_meeting(_request(AnonymousUser()), MeetingFactory(guild=None)) is False
