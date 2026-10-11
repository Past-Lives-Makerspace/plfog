"""BDD specs for the Manage Bookings home (#768) at ``/manage/bookings/``.

Who may open it, the header's "+ Add an Orientation", Needs Attention (the Bookings tabs' own rows
in the viewer's scope, a preview honoured, capped, with every action landing back here and a
crafted ``next`` ignored), What You Run (exactly the guilds and equipment in scope with their
chips, lines and links), Your Hours (the viewer's own rules by owner with a working Edit Hours),
and the old dashboard URL's redirect with its siblings untouched. Member names are factory
strings no changelog could contain (STANDARDS.md §8).
"""

from __future__ import annotations

from datetime import time, timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from membership.models import (
    EquipmentReservation,
    GuildStaffMembership,
    Member,
    OrientationAvailability,
    OrientationBooking,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

PAGE = "/manage/bookings/"


def _user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _login(client: Client, username: str, **kwargs: Any) -> Member:
    user = _user(username, **kwargs)
    client.login(username=username, password="pass")
    return user.member


def _preview_as(client: Client, role: str) -> None:
    """Put an admin into the view_as preview for ``role``, the shipped session idiom."""
    session = client.session
    session["view_as_role"] = role
    session.save()


def _page(client: Client) -> str:
    response = client.get(PAGE)
    assert response.status_code == 200
    return response.content.decode()


def _what_you_run(content: str) -> str:
    """The What You Run section alone: the sidebar lists every active guild, so a page wide "not in" proves nothing."""
    start = content.index("data-what-you-run")
    end = content.index("data-your-hours", start) if "data-your-hours" in content[start:] else len(content)
    return content[start:end]


def _request(guild: Any, *, name: str, days: float = 2, waited_hours: int = 0) -> OrientationBooking:
    """A requested booking on ``guild``, by a member called ``name``, made ``waited_hours`` ago."""
    starts = timezone.now() + timedelta(days=days)
    slot = OrientationSlotFactory(guild=guild, starts_at=starts, ends_at=starts + timedelta(hours=1))
    booking = OrientationBookingFactory(slot=slot, member=MemberFactory(full_legal_name=name))
    OrientationBooking.objects.filter(pk=booking.pk).update(requested_at=timezone.now() - timedelta(hours=waited_hours))
    return booking


def _awaiting(tool: Any, *, name: str) -> EquipmentReservation:
    return EquipmentReservationFactory(
        equipment=tool,
        member=MemberFactory(full_legal_name=name),
        status=EquipmentReservation.Status.PENDING_APPROVAL,
    )


def _led_guild(member: Member, name: str = "Led Guild", **settings: Any) -> Any:
    guild = GuildFactory(name=name, guild_lead=member)
    GuildOrientationSettingsFactory(guild=guild, **settings)
    return guild


def describe_who_may_open_it():
    def it_sends_a_member_who_runs_nothing_home(client: Client):
        _login(client, "mb_nobody")
        response = client.get(PAGE)
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_home")

    def it_opens_for_a_guild_lead(client: Client):
        _led_guild(_login(client, "mb_lead"))
        assert "data-manage-bookings" in _page(client)

    def it_opens_for_an_equipment_manager_on_no_guild(client: Client):
        EquipmentStaffMembershipFactory(member=_login(client, "mb_manager"))
        assert "data-manage-bookings" in _page(client)

    def it_opens_for_an_admin_who_runs_nothing(client: Client):
        _login(client, "mb_admin", fog_role=Member.FogRole.ADMIN)
        assert "data-manage-bookings" in _page(client)

    def it_requires_login(client: Client):
        response = client.get(PAGE)
        assert response.status_code == 302
        assert "login" in response["Location"]


def describe_the_header():
    def it_offers_add_an_orientation_to_a_lead(client: Client):
        _led_guild(_login(client, "mb_head_lead"))
        content = _page(client)
        assert "What needs your reply, what you run, and your hours." in content
        assert f'href="{reverse("hub_orientation_add")}"' in content

    def it_offers_no_add_to_an_equipment_manager_on_no_guild(client: Client):
        EquipmentStaffMembershipFactory(member=_login(client, "mb_head_manager"))
        assert f'href="{reverse("hub_orientation_add")}"' not in _page(client)


def describe_needs_attention():
    def it_lists_the_requests_waiting_on_the_viewer_oldest_first(client: Client):
        guild = _led_guild(_login(client, "mb_na_lead"))
        newer = _request(guild, name="Newer Requester", waited_hours=1)
        older = _request(guild, name="Older Requester", waited_hours=5)
        content = _page(client)
        assert content.index(f'data-booking-row="{older.pk}"') < content.index(f'data-booking-row="{newer.pk}"')
        assert "Needs Attention <span" in content
        assert "(2)</span>" in content
        assert f'href="{reverse("hub_orientations")}?view=bookings&amp;show=reply"' in content
        assert 'data-view-all="orientations"' in content
        # The row's menu is the tab's: Confirm and Decline post back to this page.
        assert "Confirm Request" in content
        assert "Decline Request" in content
        assert f'name="next" value="{PAGE}"' in content

    def it_lists_the_reservations_awaiting_approval_on_equipment_the_viewer_manages(client: Client):
        tool = EquipmentFactory(name="Managed Lathe")
        EquipmentStaffMembershipFactory(equipment=tool, member=_login(client, "mb_na_manager"))
        waiting = _awaiting(tool, name="Waiting Reserver")
        _awaiting(EquipmentFactory(name="Somebody Else's Press"), name="Elsewhere Reserver")
        content = _page(client)
        assert f'data-reservation-row="{waiting.pk}"' in content
        assert "Elsewhere Reserver" not in content
        assert "data-approve-menu-item" in content
        assert "Decline Reservation" in content
        assert f'href="{reverse("hub_equipment_index")}?view=bookings&amp;show=needs_approval"' in content
        assert 'data-view-all="reservations"' in content

    def it_never_lists_a_request_outside_the_viewers_scope(client: Client):
        member = _login(client, "mb_na_scope")
        _led_guild(member)
        other = GuildFactory(name="Other Guild")
        GuildOrientationSettingsFactory(guild=other)
        outside = _request(other, name="Outside Requester")
        # A confirmed booking on the viewer's own guild is not waiting on anyone either.
        confirmed = _request(_led_guild(member, name="Second Led Guild"), name="Confirmed Member")
        OrientationBooking.objects.filter(pk=confirmed.pk).update(status=OrientationBooking.Status.CONFIRMED)
        content = _page(client)
        assert f'data-booking-row="{outside.pk}"' not in content
        assert f'data-booking-row="{confirmed.pk}"' not in content
        assert "pl-attention-clear" in content

    def it_caps_each_group_at_ten_and_counts_them_all(client: Client):
        guild = _led_guild(_login(client, "mb_na_cap"))
        for index in range(12):
            _request(guild, name=f"Capped Requester {index}", waited_hours=index)
        content = _page(client)
        assert content.count("data-booking-row=") == 10
        assert "(12)</span>" in content

    def it_collapses_to_all_clear_with_nothing_waiting(client: Client):
        _led_guild(_login(client, "mb_na_clear"))
        content = _page(client)
        assert "pl-attention-clear" in content
        assert "data-needs-attention" not in content

    def it_honours_an_admins_preview_as_a_member(client: Client):
        admin = _login(client, "mb_na_preview", fog_role=Member.FogRole.ADMIN)
        own = _request(_led_guild(admin, name="Admin's Own Guild"), name="Own Guild Requester")
        other = GuildFactory(name="Unled Guild")
        GuildOrientationSettingsFactory(guild=other)
        elsewhere = _request(other, name="Unled Guild Requester")
        content = _page(client)
        assert f'data-booking-row="{own.pk}"' in content
        assert f'data-booking-row="{elsewhere.pk}"' in content
        _preview_as(client, "member")
        content = _page(client)
        assert f'data-booking-row="{own.pk}"' in content
        assert f'data-booking-row="{elsewhere.pk}"' not in content

    def it_keeps_the_query_count_flat_as_rows_grow(client: Client):
        guild = _led_guild(_login(client, "mb_na_queries"))
        tool = EquipmentFactory(name="Counted Lathe", guild=guild)
        _request(guild, name="First Requester")
        _awaiting(tool, name="First Reserver")
        _page(client)  # warm the per process caches (site settings, session) first
        with CaptureQueriesContext(connection) as one:
            _page(client)
        for index in range(4):
            _request(guild, name=f"More Requester {index}")
            _awaiting(tool, name=f"More Reserver {index}")
        with CaptureQueriesContext(connection) as five:
            _page(client)
        assert len(five.captured_queries) == len(one.captured_queries)


def describe_acting_from_a_row():
    def it_confirms_a_request_and_returns_here(client: Client):
        booking = _request(_led_guild(_login(client, "mb_act_confirm")), name="Confirm Me")
        response = client.post(
            reverse("hub_orientation_respond", args=[booking.pk]), {"action": "confirm", "next": PAGE}
        )
        assert response.status_code == 302
        assert response["Location"] == PAGE
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CONFIRMED

    def it_declines_a_request_and_returns_here(client: Client):
        booking = _request(_led_guild(_login(client, "mb_act_decline")), name="Decline Me")
        response = client.post(
            reverse("hub_orientation_respond", args=[booking.pk]),
            {"action": "decline", "note": "Try next week", "next": PAGE},
        )
        assert response["Location"] == PAGE
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.DECLINED

    def it_approves_a_reservation_and_returns_here(client: Client):
        tool = EquipmentFactory(name="Approve Lathe")
        EquipmentStaffMembershipFactory(equipment=tool, member=_login(client, "mb_act_approve"))
        waiting = _awaiting(tool, name="Approve Me")
        response = client.post(
            reverse("hub_equipment_reservation_approve", args=[tool.slug, waiting.pk]), {"next": PAGE}
        )
        assert response.status_code == 302
        assert response["Location"] == PAGE
        waiting.refresh_from_db()
        assert waiting.status == EquipmentReservation.Status.CONFIRMED

    def it_declines_a_reservation_and_returns_here(client: Client):
        tool = EquipmentFactory(name="Decline Lathe")
        EquipmentStaffMembershipFactory(equipment=tool, member=_login(client, "mb_act_decline_res"))
        waiting = _awaiting(tool, name="Decline Me Too")
        response = client.post(
            reverse("hub_equipment_reservation_decline", args=[tool.slug, waiting.pk]),
            {"reason": "The tool is down that day.", "next": PAGE},
        )
        assert response["Location"] == PAGE
        waiting.refresh_from_db()
        assert waiting.status == EquipmentReservation.Status.DECLINED

    def it_ignores_a_next_to_another_host(client: Client):
        member = _login(client, "mb_act_evil")
        booking = _request(_led_guild(member), name="Evil Next")
        response = client.post(
            reverse("hub_orientation_respond", args=[booking.pk]),
            {"action": "confirm", "next": "https://evil.example/steal"},
        )
        assert response["Location"] == reverse("hub_orientation_respond", args=[booking.pk])
        tool = EquipmentFactory(name="Evil Lathe")
        EquipmentStaffMembershipFactory(equipment=tool, member=member)
        waiting = _awaiting(tool, name="Evil Reserver")
        response = client.post(
            reverse("hub_equipment_reservation_approve", args=[tool.slug, waiting.pk]),
            {"next": "https://evil.example/steal"},
        )
        assert response["Location"] == f"{reverse('hub_equipment_manage', args=[tool.slug])}?tab=reservations"


def describe_what_you_run():
    def it_lists_the_guilds_and_equipment_the_viewer_runs_with_their_chips_lines_and_links(client: Client):
        member = _login(client, "mb_run_lead")
        guild = _led_guild(member, name="Running Guild")
        OrientationTypeFactory(guild=guild, name="Shop Basics", duration_minutes=60, price_cents=0, sort_order=1)
        OrientationTypeFactory(guild=guild, name="Lathe Lesson", duration_minutes=45, price_cents=1500, sort_order=2)
        OrientationTypeFactory(guild=guild, name="Retired Lesson", is_active=False)
        OrientationAvailabilityFactory(guild=guild, orienter=member)
        OrientationAvailabilityFactory(guild=guild, orienter=MemberFactory())
        OrientationAvailabilityFactory(guild=guild, orienter=member, weekday=OrientationAvailability.Weekday.MONDAY)
        tool = EquipmentFactory(name="Running Lathe", guild=guild)
        _awaiting(tool, name="Lathe Waiter")
        GuildFactory(name="Not My Guild")
        EquipmentFactory(name="Not My Tool")
        content = _what_you_run(_page(client))
        card = content[content.index(f'data-run-guild="{guild.pk}"') : content.index("</article>")]
        assert "Running Guild" in card
        assert 'data-run-state="on">On<' in card
        assert "Shop Basics · 60 min · Free" in card
        assert "Lathe Lesson · 45 min · $15" in card
        assert "Retired Lesson" not in card
        assert card.index("Shop Basics") < card.index("Lathe Lesson")
        assert "2 people with published hours" in card
        assert f'href="{reverse("hub_guild_orientations", args=[guild.pk])}"' in card
        assert f'href="{reverse("hub_orientations")}?view=bookings&amp;guild={guild.pk}"' in card
        tool_card = content[content.index(f'data-run-equipment="{tool.pk}"') :]
        tool_card = tool_card[: tool_card.index("</article>")]
        assert "Running Lathe" in tool_card
        assert 'data-run-state="open">Open<' in tool_card
        assert "1 awaiting approval" in tool_card
        assert f'href="{reverse("hub_equipment_manage", args=[tool.slug])}"' in tool_card
        assert f'href="{reverse("hub_equipment_index")}?view=bookings&amp;equipment={tool.pk}"' in tool_card
        assert "Not My Guild" not in content
        assert "Not My Tool" not in content

    def it_shows_paused_with_the_closed_message_and_off(client: Client):
        member = _login(client, "mb_run_states")
        _led_guild(member, name="Paused Guild", is_closed=True, closed_message="Back after the kiln rebuild")
        _led_guild(member, name="Switched Off Guild", is_enabled=False)
        GuildFactory(name="No Settings Guild", guild_lead=member)
        content = _page(client)
        assert 'data-run-state="paused">Paused<' in content
        assert "Back after the kiln rebuild" in content
        assert content.count('data-run-state="off">Off<') == 2
        assert "No orientations yet." in content
        assert "0 people with published hours" in content

    def it_shows_closed_for_maintenance_and_retired_equipment_to_its_manager(client: Client):
        member = _login(client, "mb_run_equipment_states")
        EquipmentStaffMembershipFactory(equipment=EquipmentFactory(name="Down Saw", is_closed=True), member=member)
        EquipmentStaffMembershipFactory(equipment=EquipmentFactory(name="Gone Saw", is_active=False), member=member)
        content = _page(client)
        assert 'data-run-state="closed">Closed for maintenance<' in content
        assert 'data-run-state="retired">Retired<' in content
        assert "0 awaiting approval" in content

    def it_gives_an_admin_every_active_guild_with_settings_and_every_active_equipment(client: Client):
        _login(client, "mb_run_admin", fog_role=Member.FogRole.ADMIN)
        with_settings = GuildFactory(name="Settings Guild")
        GuildOrientationSettingsFactory(guild=with_settings, is_enabled=False)
        GuildFactory(name="Bare Guild")
        hidden = GuildFactory(name="Hidden Guild", is_active=False)
        GuildOrientationSettingsFactory(guild=hidden)
        EquipmentFactory(name="Live Tool")
        EquipmentFactory(name="Retired Tool", is_active=False)
        content = _what_you_run(_page(client))
        assert "Settings Guild" in content
        assert "Bare Guild" not in content
        assert "Hidden Guild" not in content
        assert "Live Tool" in content
        assert "Retired Tool" not in content

    def it_tells_an_admin_with_nothing_running_to_add_one(client: Client):
        _login(client, "mb_run_admin_empty", fog_role=Member.FogRole.ADMIN)
        GuildFactory(name="Bare Guild")
        content = _page(client)
        assert "data-run-empty" in content
        assert "Nothing is running orientations yet. Add one above." in content

    def it_keeps_the_query_count_flat_as_cards_grow(client: Client):
        member = _login(client, "mb_run_queries")
        _led_guild(member, name="First Card Guild")
        EquipmentStaffMembershipFactory(equipment=EquipmentFactory(name="First Card Tool"), member=member)
        _page(client)  # warm the per process caches (site settings, session) first
        with CaptureQueriesContext(connection) as one:
            _page(client)
        for index in range(3):
            guild = _led_guild(member, name=f"Card Guild {index}")
            OrientationTypeFactory(guild=guild, name=f"Lesson {index}")
            OrientationAvailabilityFactory(guild=guild, orienter=member)
            EquipmentStaffMembershipFactory(equipment=EquipmentFactory(name=f"Card Tool {index}"), member=member)
        with CaptureQueriesContext(connection) as four:
            _page(client)
        assert len(four.captured_queries) == len(one.captured_queries)


def describe_your_hours():
    def it_lists_the_viewers_rules_by_owner_with_edit_hours(client: Client):
        member = _login(client, "mb_hours_lead")
        guild = _led_guild(member, name="Hours Guild")
        laser = OrientationTypeFactory(guild=guild, name="Laser Basics")
        OrientationAvailabilityFactory(guild=guild, orientation_type=laser, orienter=member)
        tool = EquipmentFactory(name="Hours Lathe")
        EquipmentStaffMembershipFactory(equipment=tool, member=member)
        lathe_type = OrientationTypeFactory(equipment_owned=True, equipment=tool, name="Lathe Checkout")
        OrientationAvailabilityFactory(
            equipment_owned=True,
            orientation_type=lathe_type,
            orienter=member,
            weekday=OrientationAvailability.Weekday.THURSDAY,
            start_time=time(10, 0),
            end_time=time(12, 0),
        )
        # Somebody else's hours on the same guild are theirs, not the viewer's.
        OrientationAvailabilityFactory(guild=guild, orientation_type=laser, orienter=MemberFactory())
        content = _page(client)
        assert "data-your-hours" in content
        assert content.count("data-hours-owner=") == 2
        assert content.index('data-hours-owner="guild-') < content.index('data-hours-owner="equipment-')
        assert "Laser Basics · Every Tuesday · 6:00 p.m. to 7:00 p.m. · 4 seats" in content
        assert "Lathe Checkout · Every Thursday · 10:00 a.m. to 12:00 p.m. · 4 seats" in content
        guild_form = f"{reverse('hub_guild_orientation_hours_form', args=[guild.pk])}?orienter={member.pk}"
        tool_form = f"{reverse('hub_equipment_orientation_hours_form', args=[tool.slug])}?orienter={member.pk}"
        assert f'hx-get="{guild_form}"' in content
        assert f'hx-get="{tool_form}"' in content
        assert content.count("data-edit-hours") == 2
        assert 'id="edit-hours-modal-body"' in content
        # Edit Hours opens the owner's existing modal form for the viewer's own rows.
        assert client.get(guild_form).status_code == 200
        assert client.get(tool_form).status_code == 200

    def it_says_when_none_are_published(client: Client):
        _led_guild(_login(client, "mb_hours_none"))
        content = _page(client)
        assert "data-hours-empty" in content
        assert "No hours published yet. Open a guild's Orientations page to publish them." in content

    def it_is_absent_for_an_admin_on_no_leadership(client: Client):
        _login(client, "mb_hours_admin", fog_role=Member.FogRole.ADMIN)
        assert "data-your-hours" not in _page(client)

    def it_shows_for_a_staffer_on_the_orienter_role(client: Client):
        member = _login(client, "mb_hours_orienter")
        guild = GuildFactory(name="Orienter Guild")
        GuildStaffMembershipFactory(guild=guild, member=member, role=GuildStaffMembership.Role.ORIENTER)
        assert "data-your-hours" in _page(client)


def describe_the_old_dashboard_url():
    def it_redirects_to_the_home(client: Client):
        _led_guild(_login(client, "mb_old_lead"))
        response = client.get("/orientations/manage/?status=requested")
        assert response.status_code == 302
        assert response["Location"] == PAGE

    def it_leaves_its_siblings_answering_as_today(client: Client):
        _led_guild(_login(client, "mb_old_siblings"))
        assert reverse("hub_orientations_export") == "/orientations/manage/export/"
        assert reverse("hub_orientation_add_member") == "/orientations/manage/add-member/"
        assert reverse("hub_orientation_record") == "/orientations/manage/records/"
        assert (
            reverse("hub_orientation_toggle_completed", args=[7]) == "/orientations/manage/bookings/7/toggle-completed/"
        )
        assert client.get("/orientations/manage/export/").status_code == 200
