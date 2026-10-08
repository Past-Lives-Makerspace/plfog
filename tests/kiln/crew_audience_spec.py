"""Who hears a maker's kiln message, and who sees its switch on the settings page (#691 part 2).

The settings page must show the "Replies on kiln tickets" row to exactly the people the send
path delivers it to: the kiln guild's lead and staff, and nobody else's leadership.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.utils import timezone

from core.events import resolvers
from core.events.registry import KILN_MAKER_REPLIED, Recipients, get_event
from core.events.settings_matrix import ADMIN_SECTION, _staff_profile, build_matrix
from core.models import Notification
from kiln.models import KilnTicket
from kiln.services import post_reply
from membership.models import Member
from tests.kiln.factories import KilnTicketFactory
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory

pytestmark = pytest.mark.django_db

ROW = "Replies on kiln tickets"


def _rows(member: Member) -> set[tuple[str, str]]:
    return {
        (section.title, row.label)
        for section in build_matrix(member.user)
        for block in section.blocks
        for row in block.rows
    }


def _ticket(maker: Member) -> KilnTicket:
    return KilnTicketFactory(maker=maker, status="submitted", firing_type="glaze", submitted_at=timezone.now())


@pytest.fixture
def wood_lead(make_member) -> Member:
    lead = make_member()
    GuildFactory(name="Wood Guild", slug="wood-guild", guild_lead=lead)
    return lead


@pytest.fixture
def wood_staff(make_member) -> Member:
    staff = make_member()
    GuildStaffMembershipFactory(guild=GuildFactory(name="Wood Two", slug="wood-two"), member=staff)
    return staff


def describe_the_settings_row():
    def it_rides_its_own_kiln_crew_audience():
        assert get_event(KILN_MAKER_REPLIED).recipient is Recipients.KILN_CREW

    def it_is_hidden_from_another_guilds_lead_and_staff(wood_lead, wood_staff, kiln_guild):
        assert (ADMIN_SECTION, ROW) not in _rows(wood_lead)
        assert (ADMIN_SECTION, ROW) not in _rows(wood_staff)

    def it_shows_to_kiln_staff_and_the_kiln_lead(crew, make_member, kiln_guild):
        lead = make_member()
        kiln_guild.guild_lead = lead
        kiln_guild.save(update_fields=["guild_lead"])

        assert (ADMIN_SECTION, ROW) in _rows(crew)
        assert (ADMIN_SECTION, ROW) in _rows(lead)

    def it_is_hidden_from_a_plain_member_without_asking_about_the_kiln(maker, kiln_guild):
        assert (ADMIN_SECTION, ROW) not in _rows(maker)
        # A plain member leads and staffs nothing, so the kiln crew lookup is never made.
        with patch("kiln.access.is_crew") as is_crew:
            assert not _staff_profile(maker.user).is_kiln_crew
        is_crew.assert_not_called()

    def it_hides_the_row_from_everyone_when_there_is_no_kiln_guild(wood_lead):
        assert not _staff_profile(wood_lead.user).is_kiln_crew


def describe_the_send_path():
    def it_reaches_exactly_the_kiln_crew_the_page_shows_it_to(crew, wood_lead, maker, kiln_guild):
        post_reply(_ticket(maker), maker, "Yes, cone 6.")

        reached = set(Notification.objects.filter(trigger=KILN_MAKER_REPLIED).values_list("user_id", flat=True))
        assert reached == {crew.user_id}
        assert {user.pk for user, _ in resolvers.kiln_crew({})} == {crew.user_id}

    def it_resolves_nobody_without_a_kiln_guild():
        assert resolvers.kiln_crew({}) == []
