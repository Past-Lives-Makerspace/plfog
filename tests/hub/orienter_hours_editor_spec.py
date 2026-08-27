"""BDD specs for the per-orienter Orientations tab — My Hours scope binding, the lead
overview + edit-on-behalf, the legacy Guild Hours card, the Upcoming Slots + Add A
Slot card, rule-delete retirement, and the staff-remove hook."""

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


def _rule_payload(scope: str, **overrides: str) -> dict[str, str]:
    data = {
        "orienter_scope": scope,
        "rules-TOTAL_FORMS": "1",
        "rules-INITIAL_FORMS": "0",
        "rules-MIN_NUM_FORMS": "0",
        "rules-MAX_NUM_FORMS": "1000",
        "rules-0-weekday": "1",
        "rules-0-start_time": "18:00",
        "rules-0-end_time": "19:00",
        "rules-0-seats": "4",
        "rules-0-is_active": "on",
    }
    data.update(overrides)
    return data


def _tab_url(guild: object) -> str:
    return f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations"


def _hours_url(guild: object) -> str:
    return reverse("hub_guild_orientation_hours_save", args=[guild.pk])


def describe_my_hours_scope_binding():
    def it_lets_a_staffer_save_their_own_hours(client: Client):
        guild = GuildFactory()
        user = _member_user("sc_own", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="sc_own", password="pass")
        response = client.post(_hours_url(guild), _rule_payload(str(user.member.pk)))
        assert response.status_code == 302
        rule = OrientationAvailability.objects.get(guild=guild)
        assert rule.orienter == user.member

    def it_keeps_the_orienter_on_an_edited_existing_rule(client: Client):
        guild = GuildFactory()
        user = _member_user("sc_edit", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        rule = OrientationAvailabilityFactory(guild=guild, orienter=user.member, seats=2)
        client.login(username="sc_edit", password="pass")
        response = client.post(
            _hours_url(guild),
            _rule_payload(
                str(user.member.pk),
                **{
                    "rules-INITIAL_FORMS": "1",
                    "rules-0-id": str(rule.pk),
                    "rules-0-seats": "6",
                },
            ),
        )
        assert response.status_code == 302
        rule.refresh_from_db()
        assert rule.seats == 6
        assert rule.orienter == user.member

    def it_forbids_a_staffer_posting_someone_elses_scope(client: Client):
        guild = GuildFactory()
        user = _member_user("sc_other", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        alice = MemberFactory(full_legal_name="Alice Ash")
        GuildStaffMembershipFactory(guild=guild, member=alice, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="sc_other", password="pass")
        response = client.post(_hours_url(guild), _rule_payload(str(alice.pk)))
        assert response.status_code == 403
        assert not OrientationAvailability.objects.filter(guild=guild).exists()

    def it_lets_the_lead_save_on_someones_behalf(client: Client):
        user = _member_user("sc_lead", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="sc_lead", password="pass")
        response = client.post(_hours_url(guild), _rule_payload(str(bob.pk)))
        assert response.status_code == 302
        assert OrientationAvailability.objects.get(guild=guild).orienter == bob

    def it_forbids_non_lead_staff_posting_the_guild_scope(client: Client):
        guild = GuildFactory()
        user = _member_user("sc_gscope", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        OrientationAvailabilityFactory(guild=guild)
        client.login(username="sc_gscope", password="pass")
        response = client.post(
            _hours_url(guild),
            {
                "orienter_scope": "",
                "guild_rules-TOTAL_FORMS": "0",
                "guild_rules-INITIAL_FORMS": "0",
                "guild_rules-MIN_NUM_FORMS": "0",
                "guild_rules-MAX_NUM_FORMS": "1000",
            },
        )
        assert response.status_code == 403

    def it_binds_only_the_posted_scopes_prefix(client: Client):
        # A guild-scope POST carries no rules- management form at all — the view must
        # never construct the personal formset for it (that would crash, not validate).
        user = _member_user("sc_prefix", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        OrientationAvailabilityFactory(guild=guild)
        client.login(username="sc_prefix", password="pass")
        response = client.post(
            _hours_url(guild),
            {
                "orienter_scope": "",
                "guild_rules-TOTAL_FORMS": "0",
                "guild_rules-INITIAL_FORMS": "0",
                "guild_rules-MIN_NUM_FORMS": "0",
                "guild_rules-MAX_NUM_FORMS": "1000",
            },
        )
        assert response.status_code == 302

    def it_rejects_a_malformed_scope(client: Client):
        user = _member_user("sc_bad", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="sc_bad", password="pass")
        response = client.post(_hours_url(guild), _rule_payload("bogus"))
        assert response.status_code == 404

    def it_re_renders_an_invalid_edit_on_behalf_under_the_right_heading(client: Client):
        user = _member_user("sc_invalid", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="sc_invalid", password="pass")
        response = client.post(
            _hours_url(guild),
            _rule_payload(str(bob.pk), **{"rules-0-start_time": "19:00", "rules-0-end_time": "18:00"}),
        )
        assert response.status_code == 200
        assert b"Editing Bob Placeholder" in response.content
        assert response.context["active_tab"] == "orientations"  # tab stays open on the error
        assert response.context["rule_formset"].errors
        assert not OrientationAvailability.objects.filter(guild=guild).exists()


def describe_my_hours_rendering():
    def it_renders_a_saved_rule_with_its_delete_confirm_modal(client: Client):
        guild = GuildFactory()
        user = _member_user("rw_row", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        rule = OrientationAvailabilityFactory(guild=guild, orienter=user.member)
        client.login(username="rw_row", password="pass")
        response = client.get(_tab_url(guild))
        assert response.status_code == 200
        assert b"Delete these hours?" in response.content
        assert f"rule-del-{rule.pk}".encode() in response.content
        assert b"+ Add hours" in response.content

    def it_shows_the_empty_state_for_a_staffer_with_no_hours(client: Client):
        guild = GuildFactory()
        user = _member_user("rw_empty", name="Empty Staffer")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="rw_empty", password="pass")
        response = client.get(_tab_url(guild))
        assert b"No hours yet. Add your first window and members can start booking you." in response.content


def describe_edit_on_behalf_via_the_orienter_param():
    def it_scopes_the_editor_for_the_lead(client: Client):
        user = _member_user("ob_lead", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="ob_lead", password="pass")
        response = client.get(f"{_tab_url(guild)}&orienter={bob.pk}")
        assert response.status_code == 200
        assert b"Editing Bob Placeholder" in response.content
        assert "← Back To My Hours".encode() in response.content
        assert f'name="orienter_scope" value="{bob.pk}"'.encode() in response.content

    def it_falls_back_to_self_without_permission(client: Client):
        guild = GuildFactory()
        user = _member_user("ob_staff", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        alice = MemberFactory(full_legal_name="Alice Ash")
        GuildStaffMembershipFactory(guild=guild, member=alice, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="ob_staff", password="pass")
        response = client.get(f"{_tab_url(guild)}&orienter={alice.pk}")
        assert response.status_code == 200
        assert b"My Orientation Hours" in response.content
        assert b"Editing Alice" not in response.content

    def it_ignores_a_bogus_pk(client: Client):
        user = _member_user("ob_bogus", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="ob_bogus", password="pass")
        response = client.get(f"{_tab_url(guild)}&orienter=999999")
        assert response.status_code == 200
        assert b"My Orientation Hours" in response.content

    def it_ignores_a_non_numeric_param(client: Client):
        user = _member_user("ob_nondigit", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="ob_nondigit", password="pass")
        response = client.get(f"{_tab_url(guild)}&orienter=abc")
        assert response.status_code == 200
        assert b"My Orientation Hours" in response.content


def describe_rule_delete_retirement():
    def it_reports_the_removed_and_kept_slot_counts(client: Client):
        user = _member_user("del_lead", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        rule = OrientationAvailabilityFactory(guild=guild, orienter=bob)
        open_slot = OrientationSlotFactory(
            guild=guild, orienter=bob, availability=rule, source=OrientationSlot.Source.GENERATED
        )
        booked = OrientationSlotFactory(
            guild=guild, orienter=bob, availability=rule, source=OrientationSlot.Source.GENERATED
        )
        OrientationBookingFactory(slot=booked)
        client.login(username="del_lead", password="pass")
        response = client.post(
            _hours_url(guild),
            _rule_payload(
                str(bob.pk),
                **{
                    "rules-INITIAL_FORMS": "1",
                    "rules-0-id": str(rule.pk),
                    "rules-0-weekday": str(rule.weekday),
                    "rules-0-start_time": "18:00",
                    "rules-0-end_time": "19:00",
                    "rules-0-seats": str(rule.seats),
                    "rules-0-DELETE": "on",
                },
            ),
            follow=True,
        )
        assert response.status_code == 200
        joined = " ".join(str(m) for m in response.context["messages"])
        assert "Hours deleted." in joined
        assert "Removed 1 upcoming open slot." in joined
        assert "1 booked slot kept." in joined
        assert not OrientationSlot.objects.filter(pk=open_slot.pk).exists()
        assert OrientationSlot.objects.filter(pk=booked.pk).exists()
        assert not OrientationAvailability.objects.filter(pk=rule.pk).exists()

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
        assert "Hours deleted. Removed 0 upcoming open slots." in joined
        assert "Shared hours deleted." not in joined


def describe_all_orientation_hours_overview():
    def it_shows_each_leadership_member_with_rules_or_the_empty_line(client: Client):
        user = _member_user("ov_lead", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        OrientationAvailabilityFactory(guild=guild, orienter=bob)
        client.login(username="ov_lead", password="pass")
        response = client.get(_tab_url(guild))
        assert response.status_code == 200
        assert b"All Orientation Hours" in response.content
        assert b"Bob Placeholder" in response.content
        assert b"No hours published" in response.content  # the lead has none
        assert f"orienter={bob.pk}".encode() in response.content  # Edit Hours link

    def it_hides_the_overview_from_plain_staff(client: Client):
        guild = GuildFactory()
        user = _member_user("ov_staff", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="ov_staff", password="pass")
        response = client.get(_tab_url(guild))
        assert response.status_code == 200
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
        assert b"Guild Hours (Any Orienter)" not in response.content

    def it_gives_the_lead_the_editable_formset(client: Client):
        user = _member_user("gh_lead", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        OrientationAvailabilityFactory(guild=guild)
        client.login(username="gh_lead", password="pass")
        response = client.get(_tab_url(guild))
        assert b"Guild Hours (Any Orienter)" in response.content
        assert b"guild_rules-TOTAL_FORMS" in response.content
        assert b"cannot be recreated" in response.content

    def it_shows_plain_staff_a_read_only_card(client: Client):
        guild = GuildFactory()
        user = _member_user("gh_staff", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        OrientationAvailabilityFactory(guild=guild)
        client.login(username="gh_staff", password="pass")
        response = client.get(_tab_url(guild))
        assert b"Guild Hours (Any Orienter)" in response.content
        assert b"Only the guild lead can change them." in response.content
        assert b"guild_rules-TOTAL_FORMS" not in response.content


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
        assert b"Upcoming Slots" in response.content
        assert b"Bob Placeholder" in response.content
        assert b"Any orienter" in response.content
        assert b"0 of 4 booked" in response.content
        assert b"recurring" in response.content
        assert b"one-off" in response.content
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
        assert b"No upcoming slots yet." in response.content


def describe_add_a_slot():
    def _slot_payload(**overrides: str) -> dict[str, str]:
        data = {
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
        assert b"Any orienter (guild slot)" in response.content
        assert b"Runs with: you" not in response.content

    def it_locks_plain_staff_to_themselves(client: Client):
        guild = GuildFactory()
        user = _member_user("as_staff", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="as_staff", password="pass")
        response = client.get(_tab_url(guild))
        assert b"Runs with: you" in response.content
        assert b"Any orienter (guild slot)" not in response.content

    def it_lets_the_lead_pick_an_orienter(client: Client):
        user = _member_user("as_pick", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="as_pick", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]), _slot_payload(orienter=str(bob.pk))
        )
        assert response.status_code == 302
        assert guild.orientation_slots.get().orienter == bob

    def it_saves_an_any_orienter_slot_when_left_blank(client: Client):
        user = _member_user("as_any", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="as_any", password="pass")
        response = client.post(reverse("hub_guild_orientation_slot_add", args=[guild.pk]), _slot_payload(orienter=""))
        assert response.status_code == 302
        assert guild.orientation_slots.get().orienter is None

    def it_rejects_a_non_leadership_orienter(client: Client):
        user = _member_user("as_reject", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        outsider = MemberFactory(full_legal_name="Out Sider")
        client.login(username="as_reject", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]),
            _slot_payload(orienter=str(outsider.pk)),
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
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]), _slot_payload(orienter=str(alice.pk))
        )
        assert response.status_code == 302
        assert guild.orientation_slots.get().orienter == user.member


def describe_staff_remove_retirement():
    def it_keeps_hours_while_another_staff_row_stands(client: Client):
        # Bob is Treasurer AND Orienter — removing the Treasurer row must not nuke his hours.
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
        assert "Upcoming Slots card" in joined
