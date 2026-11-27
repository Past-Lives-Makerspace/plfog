"""BDD specs for the per-orientor Orientations tab: the Orientation Schedule overview (each person's
Edit Hours opens My Hours since #1300, where hours are set), the legacy Guild Hours card, the
Upcoming Times + Add a one off Slot card, rule-delete retirement, and the staff-remove hook."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import (
    GuildStaffMembership,
    Member,
    OrientationAvailability,
    OrientationSlot,
)
from tests.membership.factories import (
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


def _member_user(username: str, *, name: str, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.full_legal_name = name
    member.save()
    member.sync_user_permissions()
    return user


def _tab_url(guild: object) -> str:
    return reverse("hub_guild_orientations", args=[guild.pk])


def _hours_url(guild: object) -> str:
    return reverse("hub_guild_orientation_hours_save", args=[guild.pk])


def describe_non_leadership_admin_self_scope():
    def it_hides_the_my_hours_card_and_shows_the_overview(client: Client):
        # An admin off this guild's leadership would only make rules generate_slots
        # silently skips — no self-scoped card for them, just the overview.
        _member_user("nl_admin", name="Site Admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="nl_admin", password="pass")
        response = client.get(_tab_url(guild))
        assert response.status_code == 200
        assert b"My Orientation Hours" not in response.content
        assert b"Orientation Schedule" in response.content
        assert b"+ Add a one off" in response.content  # the rest of the tab is intact

    def it_shows_their_own_editable_row_when_the_admin_is_also_on_staff(client: Client):
        # A staffed admin gets no inline card either — their own row shows in the Orientation
        # Schedule with an Edit Hours trigger scoped to themselves.
        guild = GuildFactory()
        user = _member_user("nl_staffed", name="Staffed Admin", fog_role=Member.FogRole.ADMIN)
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="nl_staffed", password="pass")
        response = client.get(_tab_url(guild))
        assert b"My Orientation Hours" not in response.content
        assert b"Orientation Schedule" in response.content
        # Their own Edit Hours opens My Hours (#1300).
        assert (
            f'href="{reverse("hours:my_hours")}" class="hub-btn hub-btn--sm" data-edit-hours-link'.encode()
            in response.content
        )


def describe_upcoming_slots_query_count():
    def it_stays_constant_as_the_slot_list_grows(client: Client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        user = _member_user("qc_lead", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        OrientationSlotFactory(guild=guild)
        booked = OrientationSlotFactory(guild=guild)
        OrientationBookingFactory(slot=booked)
        client.login(username="qc_lead", password="pass")
        client.get(_tab_url(guild))  # warm-up (content types, sessions)
        with CaptureQueriesContext(connection) as small:
            assert client.get(_tab_url(guild)).status_code == 200
        for _ in range(6):
            OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))
        with CaptureQueriesContext(connection) as large:
            assert client.get(_tab_url(guild)).status_code == 200
        # The booked-seat counts come from one annotated query, not a COUNT per row.
        assert len(large) == len(small)


def describe_rule_delete_retirement():
    def it_uses_the_plain_delete_message_when_shared_rules_remain(client: Client):
        user = _member_user("del_shared", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        rule = OrientationAvailabilityFactory(guild=guild)
        OrientationAvailabilityFactory(guild=guild, weekday=OrientationAvailability.Weekday.FRIDAY)
        client.login(username="del_shared", password="pass")
        response = client.post(
            _hours_url(guild),
            {
                "orienter_scope": "",
                "guild_rules-TOTAL_FORMS": "2",
                "guild_rules-INITIAL_FORMS": "2",
                "guild_rules-MIN_NUM_FORMS": "0",
                "guild_rules-MAX_NUM_FORMS": "1000",
                "guild_rules-0-id": str(rule.pk),
                "guild_rules-0-weekday": str(rule.weekday),
                "guild_rules-0-start_time": "18:00",
                "guild_rules-0-end_time": "19:00",
                "guild_rules-0-seats": str(rule.seats),
                "guild_rules-0-DELETE": "on",
                "guild_rules-1-id": str(OrientationAvailability.objects.guild_level().exclude(pk=rule.pk).get().pk),
                "guild_rules-1-orientation_type": str(
                    OrientationAvailability.objects.guild_level().exclude(pk=rule.pk).get().orientation_type_id
                ),
                "guild_rules-1-weekday": "4",
                "guild_rules-1-start_time": "18:00",
                "guild_rules-1-end_time": "19:00",
                "guild_rules-1-seats": "4",
                "guild_rules-1-is_active": "on",
            },
            follow=True,
        )
        assert response.status_code == 200
        joined = " ".join(str(m) for m in response.context["messages"])
        assert "Hours deleted." in joined
        assert "Removed" not in joined  # nothing was retired, so no zero count is announced
        assert "Shared hours deleted." not in joined


def describe_orientation_schedule_overview():
    def it_shows_each_leadership_member_with_rules_or_the_empty_line(client: Client):
        user = _member_user("ov_lead", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        OrientationAvailabilityFactory(guild=guild, orienter=bob)
        client.login(username="ov_lead", password="pass")
        response = client.get(_tab_url(guild))
        assert response.status_code == 200
        assert b"Orientation Schedule" in response.content
        assert b"Bob Placeholder" in response.content
        assert b"No hours published" in response.content  # the lead has none
        # A lead reads Bob's hours but edits only their own (#1300, Felix 3).
        assert f"?member={bob.pk}".encode() not in response.content

    def it_shows_the_edit_hours_link_on_the_viewers_own_row(client: Client):
        # The viewer's own row opens their My Hours (#1300); an admin's rows open anyone's.
        user = _member_user("ov_ownrow", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="ov_ownrow", password="pass")
        content = client.get(_tab_url(guild)).content
        hours = reverse("hours:my_hours")
        assert f'href="{hours}" class="hub-btn hub-btn--sm" data-edit-hours-link'.encode() in content
        assert f'href="{hours}#days-off"'.encode() in content
        assert f"?member={bob.pk}".encode() not in content

    def it_shows_a_plain_staffer_only_their_own_row(client: Client):
        # A plain orientor (no authority over others) now sees the Orientation Schedule scoped
        # to their own row with its Edit Hours trigger — never another staffer's row.
        guild = GuildFactory()
        user = _member_user("ov_staff", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        other = MemberFactory(full_legal_name="Other Orientor")
        GuildStaffMembershipFactory(guild=guild, member=other, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="ov_staff", password="pass")
        response = client.get(_tab_url(guild))
        assert response.status_code == 200
        assert b"Orientation Schedule" in response.content
        assert (
            f'href="{reverse("hours:my_hours")}" class="hub-btn hub-btn--sm" data-edit-hours-link'.encode()
            in response.content
        )
        # The other staffer's row and its Edit Hours are not rendered.
        assert f"?member={other.pk}".encode() not in response.content
        # Changelog guard (§10): the retired heading must never resurface on a hub page —
        # the changelog text renders into every page's context, so keep asserting its absence.
        assert b"All Orientation Hours" not in response.content

    def it_lists_orphan_rules_under_former_staff(client: Client):
        user = _member_user("ov_former", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        gone = MemberFactory(full_legal_name="Gone Person")
        OrientationAvailabilityFactory(guild=guild, orienter=gone)
        client.login(username="ov_former", password="pass")
        response = client.get(_tab_url(guild))
        assert b"Former Staff" in response.content
        assert b"Gone Person" in response.content

    def it_omits_the_former_staff_group_without_orphans(client: Client):
        user = _member_user("ov_clean", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="ov_clean", password="pass")
        response = client.get(_tab_url(guild))
        assert b"Former Staff" not in response.content


def describe_guild_hours_legacy_card():
    def it_is_absent_when_no_guild_level_rules_exist(client: Client):
        user = _member_user("gh_none", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="gh_none", password="pass")
        response = client.get(_tab_url(guild))
        assert b"Guild Hours (Any Orientor)" not in response.content

    def it_gives_the_lead_the_editable_formset(client: Client):
        user = _member_user("gh_lead", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        OrientationAvailabilityFactory(guild=guild)
        client.login(username="gh_lead", password="pass")
        response = client.get(_tab_url(guild))
        assert b"Guild Hours (Any Orientor)" in response.content
        assert b"guild_rules-TOTAL_FORMS" in response.content
        assert b"cannot be recreated" in response.content

    def it_shows_plain_staff_a_read_only_card(client: Client):
        guild = GuildFactory()
        user = _member_user("gh_staff", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        OrientationAvailabilityFactory(guild=guild)
        client.login(username="gh_staff", password="pass")
        response = client.get(_tab_url(guild))
        assert b"Guild Hours (Any Orientor)" in response.content
        assert b"Only the guild lead can change them." in response.content
        assert b"guild_rules-TOTAL_FORMS" not in response.content

    def it_re_renders_an_invalid_guild_scope_post_on_the_orientations_page(client: Client):
        # A guild-scope (legacy shared) hours POST that fails validation re-renders the full
        # page with the bound guild_rules formset and the Orientations tab kept open; the
        # personal path is modal-only, so this full-page arm is guild scope alone now.
        user = _member_user("gh_invalid", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        rule = OrientationAvailabilityFactory(guild=guild)  # a guild-level (orienter=None) row
        client.login(username="gh_invalid", password="pass")
        response = client.post(
            _hours_url(guild),
            {
                "orienter_scope": "",
                "guild_rules-TOTAL_FORMS": "1",
                "guild_rules-INITIAL_FORMS": "1",
                "guild_rules-MIN_NUM_FORMS": "0",
                "guild_rules-MAX_NUM_FORMS": "1000",
                "guild_rules-0-id": str(rule.pk),
                "guild_rules-0-weekday": str(rule.weekday),
                "guild_rules-0-start_time": "19:00",
                "guild_rules-0-end_time": "18:00",
                "guild_rules-0-seats": str(rule.seats),
                "guild_rules-0-is_active": "on",
            },
        )
        assert response.status_code == 200
        assert "hub/guild_orientations.html" in [t.name for t in response.templates]
        assert response.context["guild_rule_formset"].errors
        rule.refresh_from_db()
        assert rule.start_time.hour == 18  # nothing saved


def describe_upcoming_slots_card():
    def it_lists_slots_with_chip_seats_and_source(client: Client):
        user = _member_user("up_lead", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        rule = OrientationAvailabilityFactory(guild=guild, orienter=bob)
        OrientationSlotFactory(
            guild=guild, orienter=bob, availability=rule, source=OrientationSlot.Source.GENERATED, seats=4
        )
        OrientationSlotFactory(guild=guild, seats=2)
        client.login(username="up_lead", password="pass")
        response = client.get(_tab_url(guild))
        assert b"Upcoming Times" in response.content
        assert b"Bob Placeholder" in response.content
        assert b"Any orientor" in response.content
        assert b"0 of 4 booked" in response.content
        assert b"recurring" in response.content
        assert b"one off" in response.content
        # Per-row Cancel goes through the confirm modal to the existing endpoint.
        assert b"Cancel this open slot?" in response.content
        slot = guild.orientation_slots.first()
        assert reverse("hub_guild_orientation_slot_cancel", args=[guild.pk, slot.pk]).encode() in response.content

    def it_uses_the_heavier_copy_for_a_booked_slot(client: Client):
        user = _member_user("up_booked", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        slot = OrientationSlotFactory(guild=guild)
        OrientationBookingFactory(slot=slot)
        client.login(username="up_booked", password="pass")
        response = client.get(_tab_url(guild))
        assert b"Anyone booked on it will be emailed that it is off." in response.content

    def it_shows_the_empty_state(client: Client):
        user = _member_user("up_empty", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="up_empty", password="pass")
        response = client.get(_tab_url(guild))
        assert b"No upcoming times yet." in response.content


def describe_add_a_slot():
    def _slot_payload(guild: object, **overrides: str) -> dict[str, str]:
        data = {
            "orientation_type": str(OrientationTypeFactory(guild=guild).pk),
            "date": (timezone.localtime() + timedelta(days=3)).strftime("%Y-%m-%d"),
            "start_time": "18:00",
            "duration_minutes": "60",
            "seats": "3",
            "location": "Lobby",
        }
        data.update(overrides)
        return data

    def it_shows_the_lead_the_orienter_select(client: Client):
        user = _member_user("as_lead", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="as_lead", password="pass")
        response = client.get(_tab_url(guild))
        assert b"Any orientor (guild slot)" in response.content
        assert b"Runs with: you" not in response.content

    def it_locks_plain_staff_to_themselves(client: Client):
        guild = GuildFactory()
        user = _member_user("as_staff", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="as_staff", password="pass")
        response = client.get(_tab_url(guild))
        assert b"Runs with: you" in response.content
        assert b"Any orientor (guild slot)" not in response.content

    def it_lets_the_lead_pick_an_orienter(client: Client):
        user = _member_user("as_pick", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="as_pick", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]), _slot_payload(guild, orienter=str(bob.pk))
        )
        assert response.status_code == 302
        assert guild.orientation_slots.get().orienter == bob

    def it_saves_an_any_orienter_slot_when_left_blank(client: Client):
        user = _member_user("as_any", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="as_any", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]), _slot_payload(guild, orienter="")
        )
        assert response.status_code == 302
        assert guild.orientation_slots.get().orienter is None

    def it_rejects_a_non_leadership_orienter(client: Client):
        user = _member_user("as_reject", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        outsider = MemberFactory(full_legal_name="Out Sider")
        client.login(username="as_reject", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]),
            _slot_payload(guild, orienter=str(outsider.pk)),
            follow=True,
        )
        assert response.status_code == 200
        assert not guild.orientation_slots.exists()
        joined = " ".join(str(m) for m in response.context["messages"])
        assert "Pick someone on this guild's staff." in joined

    def it_forces_self_on_a_crafted_staff_post(client: Client):
        guild = GuildFactory()
        user = _member_user("as_forced", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        alice = MemberFactory(full_legal_name="Alice Ash")
        GuildStaffMembershipFactory(guild=guild, member=alice, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="as_forced", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]), _slot_payload(guild, orienter=str(alice.pk))
        )
        assert response.status_code == 302
        assert guild.orientation_slots.get().orienter == user.member


def describe_staff_remove_retirement():
    def it_keeps_hours_while_another_staff_row_stands(client: Client):
        # Bob is Treasurer AND Orientor — removing the Treasurer row must not nuke his hours.
        user = _member_user("rm_multi", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        treasurer_row = GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.TREASURER)
        rule = OrientationAvailabilityFactory(guild=guild, orienter=bob)
        client.login(username="rm_multi", password="pass")
        response = client.post(reverse("hub_guild_staff_remove", args=[guild.pk, treasurer_row.pk]))
        assert response.status_code == 302
        assert OrientationAvailability.objects.filter(pk=rule.pk).exists()

    def it_retires_hours_when_the_last_row_goes_and_flags_booked_slots(client: Client):
        user = _member_user("rm_last", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        staff_row = GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        rule = OrientationAvailabilityFactory(guild=guild, orienter=bob)
        booked = OrientationSlotFactory(
            guild=guild, orienter=bob, availability=rule, source=OrientationSlot.Source.GENERATED
        )
        OrientationBookingFactory(slot=booked)
        client.login(username="rm_last", password="pass")
        response = client.post(reverse("hub_guild_staff_remove", args=[guild.pk, staff_row.pk]), follow=True)
        assert response.status_code == 200
        assert not OrientationAvailability.objects.filter(pk=rule.pk).exists()
        assert OrientationSlot.objects.filter(pk=booked.pk).exists()  # booked slots stay theirs
        joined = " ".join(str(m) for m in response.context["messages"])
        assert "They still have 1 upcoming booked orientation." in joined
        assert "Upcoming Times card" in joined


def describe_rule_pause_retirement():
    def it_retires_open_slots_and_keeps_booked_ones_when_a_rule_is_paused(client: Client):
        user = _member_user("pause_lead", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        rule = OrientationAvailabilityFactory(guild=guild)
        open_slot = OrientationSlotFactory(guild=guild, availability=rule, source=OrientationSlot.Source.GENERATED)
        booked = OrientationSlotFactory(guild=guild, availability=rule, source=OrientationSlot.Source.GENERATED)
        OrientationBookingFactory(slot=booked)
        client.login(username="pause_lead", password="pass")
        response = client.post(
            _hours_url(guild),
            {
                "orienter_scope": "",
                "guild_rules-TOTAL_FORMS": "1",
                "guild_rules-INITIAL_FORMS": "1",
                "guild_rules-MIN_NUM_FORMS": "0",
                "guild_rules-MAX_NUM_FORMS": "1000",
                "guild_rules-0-id": str(rule.pk),
                "guild_rules-0-orientation_type": str(rule.orientation_type_id),
                "guild_rules-0-weekday": str(rule.weekday),
                "guild_rules-0-start_time": "18:00",
                "guild_rules-0-end_time": "19:00",
                "guild_rules-0-seats": str(rule.seats),
                # is_active deliberately absent: the rule is paused, not deleted.
            },
            follow=True,
        )
        assert response.status_code == 200
        joined = " ".join(str(m) for m in response.context["messages"])
        assert "Hours saved. Removed 1 upcoming open slot." in joined
        assert "1 booked slot kept." in joined
        assert "Hours deleted." not in joined
        rule.refresh_from_db()
        assert rule.is_active is False
        assert not OrientationSlot.objects.filter(pk=open_slot.pk).exists()
        booked.refresh_from_db()
        assert booked.seats == 1
        assert booked.is_bookable is False

    def it_says_hours_saved_alone_when_a_pause_retires_nothing(client: Client):
        user = _member_user("pause_quiet", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        rule = OrientationAvailabilityFactory(guild=guild)
        client.login(username="pause_quiet", password="pass")
        response = client.post(
            _hours_url(guild),
            {
                "orienter_scope": "",
                "guild_rules-TOTAL_FORMS": "1",
                "guild_rules-INITIAL_FORMS": "1",
                "guild_rules-MIN_NUM_FORMS": "0",
                "guild_rules-MAX_NUM_FORMS": "1000",
                "guild_rules-0-id": str(rule.pk),
                "guild_rules-0-orientation_type": str(rule.orientation_type_id),
                "guild_rules-0-weekday": str(rule.weekday),
                "guild_rules-0-start_time": "18:00",
                "guild_rules-0-end_time": "19:00",
                "guild_rules-0-seats": str(rule.seats),
            },
            follow=True,
        )
        joined = " ".join(str(m) for m in response.context["messages"])
        assert joined == "Hours saved."

    def it_ignores_a_deleted_unsaved_row(client: Client):
        user = _member_user("pause_ghost", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        rule = OrientationAvailabilityFactory(guild=guild)
        client.login(username="pause_ghost", password="pass")
        response = client.post(
            _hours_url(guild),
            {
                "orienter_scope": "",
                "guild_rules-TOTAL_FORMS": "1",
                "guild_rules-INITIAL_FORMS": "0",
                "guild_rules-MIN_NUM_FORMS": "0",
                "guild_rules-MAX_NUM_FORMS": "1000",
                "guild_rules-0-orientation_type": str(rule.orientation_type_id),
                "guild_rules-0-weekday": "4",
                "guild_rules-0-start_time": "18:00",
                "guild_rules-0-end_time": "19:00",
                "guild_rules-0-seats": "4",
                "guild_rules-0-is_active": "on",
                "guild_rules-0-DELETE": "on",
            },
            follow=True,
        )
        assert response.status_code == 200
        joined = " ".join(str(m) for m in response.context["messages"])
        assert joined == "Hours saved."
        assert guild.orientation_rules.count() == 1


def describe_shared_rule_form_owner_resolution():
    """The one rule form serves both owners; with no owner passed it falls back to the row's guild."""

    def it_infers_the_guild_from_an_existing_row():
        from hub.forms import OrientationAvailabilityForm

        rule = OrientationAvailabilityFactory()
        OrientationTypeFactory(guild=GuildFactory(), name="Elsewhere")
        form = OrientationAvailabilityForm(instance=rule)
        assert list(form.fields["orientation_type"].queryset) == [rule.orientation_type]

    def it_offers_no_types_with_no_owner_at_all():
        from hub.forms import OrientationAvailabilityForm

        form = OrientationAvailabilityForm()
        assert list(form.fields["orientation_type"].queryset) == []
        assert (
            form.fields["orientation_type"].error_messages["invalid_choice"] == "Pick one of this guild's orientations."
        )


def describe_invalid_modal_re_render_keeps_the_save_url():
    """Edit Shared Hours posts to hours_save_url; an error re-render must still carry it."""

    def it_keeps_the_equipment_save_url(client: Client):
        from tests.membership.factories import EquipmentFactory

        _member_user("url_equip", name="Ada Admin", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory()
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Basics")
        rule = OrientationAvailabilityFactory(equipment_owned=True, orientation_type=orientation_type)
        client.login(username="url_equip", password="pass")
        save_url = reverse("hub_equipment_orientation_hours_save", args=[equipment.slug])
        response = client.post(
            save_url,
            {
                "orienter_scope": "",
                "formset_prefix": "modal_rules",
                "modal_rules-TOTAL_FORMS": "1",
                "modal_rules-INITIAL_FORMS": "1",
                "modal_rules-MIN_NUM_FORMS": "0",
                "modal_rules-MAX_NUM_FORMS": "1000",
                "modal_rules-0-id": str(rule.pk),
                "modal_rules-0-orientation_type": str(orientation_type.pk),
                "modal_rules-0-weekday": "1",
                "modal_rules-0-start_time": "18:00",
                "modal_rules-0-end_time": "17:00",
                "modal_rules-0-seats": "1",
                "modal_rules-0-cadence": "weekly",
                "modal_rules-0-is_active": "on",
            },
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "End time must be after the start time." in content
        assert f'hx-post="{save_url}"' in content
        assert 'hx-post=""' not in content
