"""BDD specs for the Days Off modal (#767) on both owners' Orientation Schedule cards: the modal body
and its rows, the impact preview (the count line and the Save label following the date), saving (the
cancel, the toast, the redirect, the two refusals), deleting, who reaches whose (own only for a plain
orientor or manager, anyone for a lead or an admin, a crafted POST refused), and the "Off Nov 26,
Dec 25." line under each person's hours on each card."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import (
    Equipment,
    GuildStaffMembership,
    Member,
    OrientationBooking,
    OrientationDayOff,
    OrientationSlot,
    OrientationType,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _member_user(username: str, *, name: str, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.full_legal_name = name
    member.status = Member.Status.ACTIVE
    member.save()
    member.sync_user_permissions()
    return user


def _login(client: Client, user: User) -> None:
    assert client.login(username=user.username, password="pass")


def _day(offset: int) -> date:
    return timezone.localdate() + timedelta(days=offset)


def _at(day: date, hour: int) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


def _guild_setup() -> tuple[object, User, User, User]:
    """An orientation guild with a lead and two orientors, Bob and Alice, who can all log in."""
    lead = _member_user("do_lead", name="Lead Person")
    guild = GuildFactory(guild_lead=lead.member)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    OrientationTypeFactory(guild=guild)
    bob = _member_user("do_bob", name="Bob Placeholder")
    alice = _member_user("do_alice", name="Alice Ash")
    for user in (bob, alice):
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
    return guild, lead, bob, alice


def _slot(guild: object, orienter: Member, day: date, hour: int) -> OrientationSlot:
    return OrientationSlotFactory(
        guild=guild,
        orientation_type=guild.orientation_types.first(),
        orienter=orienter,
        starts_at=_at(day, hour),
        ends_at=_at(day, hour + 1),
    )


def _modal_url(guild: object, member: Member) -> str:
    return f"{reverse('hub_guild_orientation_days_off', args=[guild.pk])}?orienter={member.pk}"


def _preview_url(guild: object, member: Member, day: date) -> str:
    base = reverse("hub_guild_orientation_day_off_preview", args=[guild.pk])
    return f"{base}?orienter={member.pk}&day_off-date={day.isoformat()}"


def _save_url(guild: object) -> str:
    return reverse("hub_guild_orientation_day_off_save", args=[guild.pk])


def _delete_url(guild: object, day_off: OrientationDayOff) -> str:
    return reverse("hub_guild_orientation_day_off_delete", args=[guild.pk, day_off.pk])


def _page_url(guild: object) -> str:
    return reverse("hub_guild_orientations", args=[guild.pk])


def _messages(response: object) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def describe_the_modal_body():
    def it_opens_the_orienters_own(client: Client):
        guild, _lead, bob, _alice = _guild_setup()
        _login(client, bob)
        response = client.get(_modal_url(guild, bob.member), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="days-off-modal-form"' in content
        assert f'hx-post="{_save_url(guild)}"' in content
        assert f'name="orienter" value="{bob.member.pk}"' in content
        assert 'hx-target="#day-off-impact"' in content  # the date input asks for the impact on change
        assert "data-days-off-empty" in content and "No days off yet." in content
        assert "data-day-off-impact" not in content  # no date chosen yet, so no count line
        assert "data-day-off-save>Save</button>" in content

    def it_lists_their_upcoming_days_off_with_a_delete_each(client: Client):
        guild, _lead, bob, _alice = _guild_setup()
        soon = OrientationDayOff.objects.create(orienter=bob.member, date=_day(5), note="Dentist")
        later = OrientationDayOff.objects.create(orienter=bob.member, date=_day(30))
        gone = OrientationDayOff.objects.create(orienter=bob.member, date=_day(-1))
        _login(client, bob)
        content = client.get(_modal_url(guild, bob.member), HTTP_HX_REQUEST="true").content.decode()
        assert f'data-day-off="{soon.pk}"' in content and f'data-day-off="{later.pk}"' in content
        assert f'data-day-off="{gone.pk}"' not in content
        assert "Dentist" in content
        assert content.index(f'data-day-off="{soon.pk}"') < content.index(f'data-day-off="{later.pk}"')
        assert f'action="{_delete_url(guild, soon)}"' in content
        assert "Their hours that day generate again." in content
        assert "data-days-off-empty" not in content

    def it_refuses_another_persons_to_a_plain_orienter(client: Client):
        guild, _lead, bob, alice = _guild_setup()
        _login(client, bob)
        assert client.get(_modal_url(guild, alice.member), HTTP_HX_REQUEST="true").status_code == 403

    def it_opens_anyones_for_the_lead_and_an_admin(client: Client):
        guild, lead, bob, _alice = _guild_setup()
        _login(client, lead)
        assert client.get(_modal_url(guild, bob.member), HTTP_HX_REQUEST="true").status_code == 200
        client.logout()
        _login(client, _member_user("do_admin", name="Site Admin", fog_role=Member.FogRole.ADMIN))
        assert client.get(_modal_url(guild, bob.member), HTTP_HX_REQUEST="true").status_code == 200

    def it_404s_on_a_missing_or_garbage_orienter(client: Client):
        guild, lead, _bob, _alice = _guild_setup()
        _login(client, lead)
        base = reverse("hub_guild_orientation_days_off", args=[guild.pk])
        assert client.get(base, HTTP_HX_REQUEST="true").status_code == 404
        assert client.get(f"{base}?orienter=bob", HTTP_HX_REQUEST="true").status_code == 404
        assert client.get(f"{base}?orienter=999999", HTTP_HX_REQUEST="true").status_code == 404


def describe_the_impact_preview():
    def it_counts_that_days_times_and_names_the_email(client: Client):
        guild, _lead, bob, _alice = _guild_setup()
        day = _day(4)
        booked, _empty = _slot(guild, bob.member, day, 10), _slot(guild, bob.member, day, 12)
        OrientationBookingFactory(slot=booked, status=OrientationBooking.Status.CONFIRMED)
        _login(client, bob)
        content = client.get(_preview_url(guild, bob.member, day), HTTP_HX_REQUEST="true").content.decode()
        assert 'id="day-off-impact"' in content
        assert "2 times that day will be cancelled." in content
        assert "Save and email 1 member</button>" in content

    def it_says_save_alone_when_nobody_is_booked(client: Client):
        guild, _lead, bob, _alice = _guild_setup()
        day = _day(4)
        _slot(guild, bob.member, day, 10)
        _login(client, bob)
        content = client.get(_preview_url(guild, bob.member, day), HTTP_HX_REQUEST="true").content.decode()
        assert "1 time that day will be cancelled." in content
        assert "data-day-off-save>Save</button>" in content
        assert "Save and email" not in content

    def it_shows_no_line_for_a_date_it_would_refuse(client: Client):
        guild, _lead, bob, _alice = _guild_setup()
        _login(client, bob)
        content = client.get(_preview_url(guild, bob.member, _day(-1)), HTTP_HX_REQUEST="true").content.decode()
        assert "data-day-off-impact" not in content
        assert "data-day-off-save>Save</button>" in content

    def it_refuses_another_persons_to_a_plain_orienter(client: Client):
        guild, _lead, bob, alice = _guild_setup()
        _login(client, bob)
        assert client.get(_preview_url(guild, alice.member, _day(4)), HTTP_HX_REQUEST="true").status_code == 403


def describe_saving():
    def it_saves_the_day_cancels_its_times_and_redirects_over_htmx(client: Client):
        guild, _lead, bob, _alice = _guild_setup()
        day = _day(4)
        booked, empty = _slot(guild, bob.member, day, 10), _slot(guild, bob.member, day, 12)
        booking = OrientationBookingFactory(slot=booked, status=OrientationBooking.Status.CONFIRMED)
        _login(client, bob)
        response = client.post(
            _save_url(guild),
            {"orienter": str(bob.member.pk), "day_off-date": day.isoformat(), "day_off-note": "Portland Open Studios"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 204
        assert response["HX-Redirect"] == _page_url(guild)
        day_off = OrientationDayOff.objects.get(orienter=bob.member, date=day)
        assert day_off.note == "Portland Open Studios"
        for slot in (booked, empty):
            slot.refresh_from_db()
            assert slot.is_cancelled is True
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CANCELLED
        assert _messages(response) == ["Day off saved. 2 times cancelled."]

    def it_redirects_on_a_plain_post(client: Client):
        guild, _lead, bob, _alice = _guild_setup()
        _login(client, bob)
        response = client.post(_save_url(guild), {"orienter": str(bob.member.pk), "day_off-date": _day(4).isoformat()})
        assert response.status_code == 302
        assert response["Location"] == _page_url(guild)
        assert OrientationDayOff.objects.filter(orienter=bob.member, date=_day(4)).exists()
        assert _messages(response) == ["Day off saved. 0 times cancelled."]

    def it_refuses_a_past_date(client: Client):
        guild, _lead, bob, _alice = _guild_setup()
        _login(client, bob)
        response = client.post(
            _save_url(guild),
            {"orienter": str(bob.member.pk), "day_off-date": _day(-1).isoformat()},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Pick today or a day ahead." in content
        assert 'id="days-off-modal-form"' in content  # the modal body comes back with the error inside it
        assert not OrientationDayOff.objects.exists()

    def it_refuses_a_repeated_date(client: Client):
        guild, _lead, bob, _alice = _guild_setup()
        OrientationDayOff.objects.create(orienter=bob.member, date=_day(4))
        _login(client, bob)
        response = client.post(
            _save_url(guild),
            {"orienter": str(bob.member.pk), "day_off-date": _day(4).isoformat()},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "That day is already off." in response.content.decode()
        assert OrientationDayOff.objects.count() == 1

    def it_refuses_a_crafted_post_for_another_person(client: Client):
        guild, _lead, bob, alice = _guild_setup()
        _login(client, bob)
        response = client.post(
            _save_url(guild),
            {"orienter": str(alice.member.pk), "day_off-date": _day(4).isoformat()},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 403
        assert not OrientationDayOff.objects.exists()

    def it_lets_the_lead_save_for_an_orienter(client: Client):
        guild, lead, bob, _alice = _guild_setup()
        _login(client, lead)
        response = client.post(
            _save_url(guild),
            {"orienter": str(bob.member.pk), "day_off-date": _day(4).isoformat()},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 204
        assert OrientationDayOff.objects.filter(orienter=bob.member, date=_day(4)).exists()

    def it_refuses_a_lead_a_member_who_orients_only_in_another_guild(client: Client):
        guild, lead, bob, _alice = _guild_setup()
        other_guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=other_guild, is_enabled=True)
        OrientationTypeFactory(guild=other_guild)
        stranger = _member_user("do_stranger", name="Sam Stranger")
        GuildStaffMembershipFactory(guild=other_guild, member=stranger.member, role=GuildStaffMembership.Role.ORIENTER)
        day = _day(4)
        theirs = _slot(other_guild, stranger.member, day, 10)
        _login(client, lead)
        response = client.post(
            _save_url(guild),
            {"orienter": str(stranger.member.pk), "day_off-date": day.isoformat()},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 403
        assert not OrientationDayOff.objects.exists()
        theirs.refresh_from_db()
        assert theirs.is_cancelled is False
        assert client.get(_modal_url(guild, stranger.member), HTTP_HX_REQUEST="true").status_code == 403
        # The same lead reaches their own orientor as before.
        response = client.post(
            _save_url(guild), {"orienter": str(bob.member.pk), "day_off-date": day.isoformat()}, HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 204
        assert OrientationDayOff.objects.filter(orienter=bob.member, date=day).exists()

    def it_takes_only_posts(client: Client):
        guild, _lead, bob, _alice = _guild_setup()
        _login(client, bob)
        assert client.get(_save_url(guild)).status_code == 405


def describe_deleting():
    def it_removes_the_day_off_and_lands_back_on_the_page(client: Client):
        guild, _lead, bob, _alice = _guild_setup()
        day_off = OrientationDayOff.objects.create(orienter=bob.member, date=_day(4))
        _login(client, bob)
        response = client.post(_delete_url(guild, day_off))
        assert response.status_code == 302
        assert response["Location"] == _page_url(guild)
        assert not OrientationDayOff.objects.filter(pk=day_off.pk).exists()
        assert _messages(response) == ["Day off removed. Hours that day generate again."]

    def it_refuses_another_persons_to_a_plain_orienter(client: Client):
        guild, _lead, bob, alice = _guild_setup()
        day_off = OrientationDayOff.objects.create(orienter=alice.member, date=_day(4))
        _login(client, bob)
        assert client.post(_delete_url(guild, day_off)).status_code == 403
        assert OrientationDayOff.objects.filter(pk=day_off.pk).exists()

    def it_lets_the_lead_remove_anyones(client: Client):
        guild, lead, bob, _alice = _guild_setup()
        day_off = OrientationDayOff.objects.create(orienter=bob.member, date=_day(4))
        _login(client, lead)
        assert client.post(_delete_url(guild, day_off)).status_code == 302
        assert not OrientationDayOff.objects.filter(pk=day_off.pk).exists()

    def it_404s_on_an_unknown_day_off(client: Client):
        guild, lead, _bob, _alice = _guild_setup()
        _login(client, lead)
        assert client.post(reverse("hub_guild_orientation_day_off_delete", args=[guild.pk, 999999])).status_code == 404


def describe_the_card():
    def it_lists_each_persons_upcoming_days_off_under_their_hours(client: Client):
        guild, lead, bob, _alice = _guild_setup()
        first, second = _day(10), _day(40)
        OrientationDayOff.objects.create(orienter=bob.member, date=first)
        OrientationDayOff.objects.create(orienter=bob.member, date=second)
        OrientationDayOff.objects.create(orienter=bob.member, date=_day(-3))  # over, so not listed
        _login(client, lead)
        content = client.get(_page_url(guild)).content.decode()
        assert content.count("data-days-off-line") == 1  # Bob's line only; Alice and the lead have none
        assert f"Off {first:%b} {first.day}, {second:%b} {second.day}." in content
        assert f'hx-get="{_modal_url(guild, bob.member)}"' in content  # the Days Off button on Bob's row
        assert content.count("data-days-off-button") == 3  # the lead, Bob and Alice each have one
        assert 'id="days-off-modal-body"' in content

    def it_shows_a_plain_orienter_only_their_own_button(client: Client):
        guild, lead, bob, alice = _guild_setup()
        _login(client, bob)
        content = client.get(_page_url(guild)).content.decode()
        assert f'hx-get="{_modal_url(guild, bob.member)}"' in content
        assert f'hx-get="{_modal_url(guild, alice.member)}"' not in content
        assert f'hx-get="{_modal_url(guild, lead.member)}"' not in content


def describe_on_equipment():
    def _equipment_setup() -> tuple[Equipment, User, User]:
        """A tool with an owned orientation, managed by Dana and Quinn, who can both log in."""
        equipment = EquipmentFactory(name="Etching Press")
        OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
        dana = _member_user("do_dana", name="Dana Reyes")
        quinn = _member_user("do_quinn", name="Quinn Quiet")
        for user in (dana, quinn):
            EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        return equipment, dana, quinn

    def _tool_slot(equipment: Equipment, orienter: Member, day: date, hour: int) -> OrientationSlot:
        return OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=OrientationType.objects.get(equipment=equipment),
            orienter=orienter,
            starts_at=_at(day, hour),
            ends_at=_at(day, hour + 1),
        )

    def _tool_modal_url(equipment: Equipment, member: Member) -> str:
        return f"{reverse('hub_equipment_orientation_days_off', args=[equipment.slug])}?orienter={member.pk}"

    def _tab_url(equipment: Equipment) -> str:
        return f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation"

    def it_opens_a_managers_own_modal_and_refuses_anothers(client: Client):
        equipment, dana, quinn = _equipment_setup()
        _login(client, dana)
        response = client.get(_tool_modal_url(equipment, dana.member), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        save_url = reverse("hub_equipment_orientation_day_off_save", args=[equipment.slug])
        assert f'hx-post="{save_url}"' in response.content.decode()
        assert client.get(_tool_modal_url(equipment, quinn.member), HTTP_HX_REQUEST="true").status_code == 403

    def it_previews_saves_and_lands_on_the_orientation_tab(client: Client):
        equipment, dana, _quinn = _equipment_setup()
        day = _day(4)
        slot = _tool_slot(equipment, dana.member, day, 10)
        _login(client, dana)
        preview = reverse("hub_equipment_orientation_day_off_preview", args=[equipment.slug])
        content = client.get(
            f"{preview}?orienter={dana.member.pk}&day_off-date={day.isoformat()}", HTTP_HX_REQUEST="true"
        ).content.decode()
        assert "1 time that day will be cancelled." in content
        response = client.post(
            reverse("hub_equipment_orientation_day_off_save", args=[equipment.slug]),
            {"orienter": str(dana.member.pk), "day_off-date": day.isoformat()},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 204
        assert response["HX-Redirect"] == _tab_url(equipment)
        slot.refresh_from_db()
        assert slot.is_cancelled is True
        assert OrientationDayOff.objects.filter(orienter=dana.member, date=day).exists()

    def it_refuses_a_crafted_post_for_another_manager(client: Client):
        equipment, dana, quinn = _equipment_setup()
        _login(client, dana)
        response = client.post(
            reverse("hub_equipment_orientation_day_off_save", args=[equipment.slug]),
            {"orienter": str(quinn.member.pk), "day_off-date": _day(4).isoformat()},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 403
        assert not OrientationDayOff.objects.exists()

    def it_refuses_the_owning_guilds_lead_a_member_who_does_not_orient_on_the_tool(client: Client):
        equipment, dana, _quinn = _equipment_setup()
        lead = _member_user("do_tool_lead", name="Lead Person")
        equipment.guild = GuildFactory(guild_lead=lead.member)
        equipment.save(update_fields=["guild"])
        outsider = _member_user("do_outsider", name="Olive Outsider")
        _login(client, lead)
        response = client.post(
            reverse("hub_equipment_orientation_day_off_save", args=[equipment.slug]),
            {"orienter": str(outsider.member.pk), "day_off-date": _day(4).isoformat()},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 403
        assert not OrientationDayOff.objects.exists()
        response = client.post(
            reverse("hub_equipment_orientation_day_off_save", args=[equipment.slug]),
            {"orienter": str(dana.member.pk), "day_off-date": _day(4).isoformat()},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 204

    def it_lists_the_days_off_on_the_card_for_an_admin(client: Client):
        equipment, dana, _quinn = _equipment_setup()
        first, second = _day(10), _day(40)
        OrientationDayOff.objects.create(orienter=dana.member, date=first)
        OrientationDayOff.objects.create(orienter=dana.member, date=second)
        _login(client, _member_user("do_tool_admin", name="Site Admin", fog_role=Member.FogRole.ADMIN))
        content = client.get(_tab_url(equipment)).content.decode()
        assert f"Off {first:%b} {first.day}, {second:%b} {second.day}." in content
        assert content.count("data-days-off-line") == 1
        assert f'hx-get="{_tool_modal_url(equipment, dana.member)}"' in content
        assert 'id="days-off-modal-body"' in content

    def it_removes_a_day_off_back_to_the_tab(client: Client):
        equipment, dana, quinn = _equipment_setup()
        own = OrientationDayOff.objects.create(orienter=dana.member, date=_day(4))
        theirs = OrientationDayOff.objects.create(orienter=quinn.member, date=_day(4))
        _login(client, dana)
        response = client.post(reverse("hub_equipment_orientation_day_off_delete", args=[equipment.slug, own.pk]))
        assert response.status_code == 302
        assert response["Location"] == _tab_url(equipment)
        assert not OrientationDayOff.objects.filter(pk=own.pk).exists()
        assert (
            client.post(
                reverse("hub_equipment_orientation_day_off_delete", args=[equipment.slug, theirs.pk])
            ).status_code
            == 403
        )
        assert OrientationDayOff.objects.filter(pk=theirs.pk).exists()
