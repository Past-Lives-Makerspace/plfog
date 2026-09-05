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


def _modal_rule_payload(scope: str, guild: object = None, **overrides: str) -> dict[str, str]:
    """A modal (prefix ``modal_rules``) hours payload, posted with the HX-Request header.

    Pass ``guild`` on success-path posts: it fills the row's required
    ``orientation_type`` with the guild's shared "Orientation" type.
    """
    data = {
        "orienter_scope": scope,
        "formset_prefix": "modal_rules",
        "modal_rules-TOTAL_FORMS": "1",
        "modal_rules-INITIAL_FORMS": "0",
        "modal_rules-MIN_NUM_FORMS": "0",
        "modal_rules-MAX_NUM_FORMS": "1000",
        "modal_rules-0-weekday": "1",
        "modal_rules-0-start_time": "18:00",
        "modal_rules-0-end_time": "19:00",
        "modal_rules-0-seats": "4",
        "modal_rules-0-is_active": "on",
    }
    if guild is not None:
        data["modal_rules-0-orientation_type"] = str(OrientationTypeFactory(guild=guild).pk)
    data.update(overrides)
    return data


def _tab_url(guild: object) -> str:
    return f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations"


def _hours_url(guild: object) -> str:
    return reverse("hub_guild_orientation_hours_save", args=[guild.pk])


def _form_url(guild: object, orienter_pk: object) -> str:
    return f"{reverse('hub_guild_orientation_hours_form', args=[guild.pk])}?orienter={orienter_pk}"


def describe_my_hours_scope_binding():
    def it_lets_a_staffer_save_their_own_hours(client: Client):
        # Own hours now save through the Edit Hours modal (prefix modal_rules, HTMX): a valid
        # save answers 204 + HX-Redirect back to the tab, not a plain 302.
        guild = GuildFactory()
        user = _member_user("sc_own", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="sc_own", password="pass")
        response = client.post(
            _hours_url(guild), _modal_rule_payload(str(user.member.pk), guild=guild), HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 204
        assert response["HX-Redirect"] == _tab_url(guild)
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
            _modal_rule_payload(
                str(user.member.pk),
                guild=guild,
                **{
                    "modal_rules-INITIAL_FORMS": "1",
                    "modal_rules-0-id": str(rule.pk),
                    "modal_rules-0-seats": "6",
                },
            ),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 204
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
        response = client.post(_hours_url(guild), _modal_rule_payload(str(alice.pk)), HTTP_HX_REQUEST="true")
        assert response.status_code == 403
        assert not OrientationAvailability.objects.filter(guild=guild).exists()

    def it_lets_the_lead_save_on_someones_behalf(client: Client):
        user = _member_user("sc_lead", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="sc_lead", password="pass")
        response = client.post(_hours_url(guild), _modal_rule_payload(str(bob.pk), guild=guild), HTTP_HX_REQUEST="true")
        assert response.status_code == 204
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
        response = client.post(_hours_url(guild), _modal_rule_payload("bogus"), HTTP_HX_REQUEST="true")
        assert response.status_code == 404

    def it_re_renders_an_invalid_own_hours_modal_post(client: Client):
        # An own-hours modal POST that fails validation re-renders the bound modal partial with
        # the errors surfaced inside the open editor; nothing is saved.
        user = _member_user("sc_invalid", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="sc_invalid", password="pass")
        response = client.post(
            _hours_url(guild),
            _modal_rule_payload(
                str(user.member.pk), **{"modal_rules-0-start_time": "19:00", "modal_rules-0-end_time": "18:00"}
            ),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"Editing Lead Person" in response.content  # the bound modal partial
        assert not OrientationAvailability.objects.filter(guild=guild).exists()


def describe_my_hours_rendering():
    # The inline My Hours card is gone; a staffer edits their own hours through the same Edit
    # Hours modal partial as everyone else, opened from their row in the Orientation Schedule.
    def it_renders_a_saved_rule_with_its_delete_confirm_modal(client: Client):
        guild = GuildFactory()
        user = _member_user("rw_row", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        rule = OrientationAvailabilityFactory(guild=guild, orienter=user.member)
        client.login(username="rw_row", password="pass")
        response = client.get(_form_url(guild, user.member.pk))
        assert response.status_code == 200
        assert b"Editing Bob Placeholder" in response.content
        assert b"Delete these hours?" in response.content
        assert f"modal-rule-del-{rule.pk}".encode() in response.content
        assert b"+ Add hours" in response.content

    def it_shows_the_empty_state_for_a_staffer_with_no_hours(client: Client):
        guild = GuildFactory()
        user = _member_user("rw_empty", name="Empty Staffer")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="rw_empty", password="pass")
        response = client.get(_form_url(guild, user.member.pk))
        assert b"No hours yet. Add a window and members can start booking Empty Staffer." in response.content


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
        assert b"+ Add A Slot" in response.content  # the rest of the tab is intact

    def it_403s_a_self_scope_save(client: Client):
        user = _member_user("nl_save", name="Site Admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="nl_save", password="pass")
        response = client.post(_hours_url(guild), _rule_payload(str(user.member.pk)))
        assert response.status_code == 403
        assert not OrientationAvailability.objects.filter(guild=guild).exists()

    def it_still_lets_them_edit_on_behalf(client: Client):
        _member_user("nl_behalf", name="Site Admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="nl_behalf", password="pass")
        response = client.post(_hours_url(guild), _modal_rule_payload(str(bob.pk), guild=guild), HTTP_HX_REQUEST="true")
        assert response.status_code == 204
        assert OrientationAvailability.objects.get(guild=guild).orienter == bob

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
        assert f"orienter={user.member.pk}".encode() in response.content


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


def describe_orientation_hours_form_endpoint():
    def it_returns_the_targets_modal_formset_for_a_lead(client: Client):
        user = _member_user("hf_lead", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="hf_lead", password="pass")
        response = client.get(_form_url(guild, bob.pk))
        assert response.status_code == 200
        assert b"Editing Bob Placeholder" in response.content
        # Prefix is modal_rules (never rules) so it can't collide with the page's own formset.
        assert b'name="formset_prefix" value="modal_rules"' in response.content
        assert f'name="orienter_scope" value="{bob.pk}"'.encode() in response.content
        assert b"id_modal_rules-TOTAL_FORMS" in response.content

    def it_403s_a_non_privileged_staffer_editing_someone_else(client: Client):
        guild = GuildFactory()
        user = _member_user("hf_staff", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        alice = MemberFactory(full_legal_name="Alice Ash")
        GuildStaffMembershipFactory(guild=guild, member=alice, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="hf_staff", password="pass")
        assert client.get(_form_url(guild, alice.pk)).status_code == 403

    def it_404s_a_bogus_orienter(client: Client):
        user = _member_user("hf_bogus", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="hf_bogus", password="pass")
        assert client.get(_form_url(guild, 999999)).status_code == 404

    def it_404s_a_non_numeric_orienter(client: Client):
        user = _member_user("hf_nondigit", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="hf_nondigit", password="pass")
        assert client.get(_form_url(guild, "abc")).status_code == 404

    def it_404s_a_missing_orienter(client: Client):
        user = _member_user("hf_missing", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="hf_missing", password="pass")
        assert client.get(reverse("hub_guild_orientation_hours_form", args=[guild.pk])).status_code == 404

    def it_renders_the_form_for_a_former_staff_target(client: Client):
        # The Former Staff row's Edit Hours button opens the same modal (the gate is
        # can_edit_orienter_hours, which a lead/admin passes for a former staffer).
        user = _member_user("hf_former", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        gone = MemberFactory(full_legal_name="Gone Person")
        OrientationAvailabilityFactory(guild=guild, orienter=gone)
        client.login(username="hf_former", password="pass")
        response = client.get(_form_url(guild, gone.pk))
        assert response.status_code == 200
        assert b"Editing Gone Person" in response.content


def describe_modal_hours_save():
    def it_answers_204_and_hx_redirect_on_a_valid_modal_save(client: Client):
        user = _member_user("ms_lead", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="ms_lead", password="pass")
        response = client.post(_hours_url(guild), _modal_rule_payload(str(bob.pk), guild=guild), HTTP_HX_REQUEST="true")
        assert response.status_code == 204
        assert response["HX-Redirect"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations"
        assert OrientationAvailability.objects.get(guild=guild).orienter == bob

    def it_re_renders_the_modal_partial_with_errors_on_an_invalid_modal_save(client: Client):
        user = _member_user("ms_invalid", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="ms_invalid", password="pass")
        response = client.post(
            _hours_url(guild),
            _modal_rule_payload(
                str(bob.pk), **{"modal_rules-0-start_time": "19:00", "modal_rules-0-end_time": "18:00"}
            ),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"Editing Bob Placeholder" in response.content  # the bound partial, inside the modal
        assert not OrientationAvailability.objects.filter(guild=guild).exists()

    def it_404s_an_unlisted_formset_prefix(client: Client):
        user = _member_user("ms_badprefix", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="ms_badprefix", password="pass")
        response = client.post(
            _hours_url(guild),
            _modal_rule_payload(str(user.member.pk), formset_prefix="sneaky"),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 404

    def it_404s_modal_rules_without_the_htmx_header(client: Client):
        # A crafted plain POST with modal_rules must not fall through to the full-page
        # re-render (its management form would mismatch the page's rules formset).
        user = _member_user("ms_nohtmx", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="ms_nohtmx", password="pass")
        response = client.post(_hours_url(guild), _modal_rule_payload(str(user.member.pk)))
        assert response.status_code == 404


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
        # The lead deletes Bob's rule through the Edit Hours modal (modal_rules, HTMX). A valid
        # save answers 204 + HX-Redirect; the retirement flash renders on the reloaded tab.
        response = client.post(
            _hours_url(guild),
            _modal_rule_payload(
                str(bob.pk),
                **{
                    "modal_rules-INITIAL_FORMS": "1",
                    "modal_rules-0-id": str(rule.pk),
                    "modal_rules-0-weekday": str(rule.weekday),
                    "modal_rules-0-start_time": "18:00",
                    "modal_rules-0-end_time": "19:00",
                    "modal_rules-0-seats": str(rule.seats),
                    "modal_rules-0-DELETE": "on",
                },
            ),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 204
        assert response["HX-Redirect"] == _tab_url(guild)
        # Follow the HX-Redirect to consume the queued flash on the reloaded tab.
        followup = client.get(_tab_url(guild))
        joined = " ".join(str(m) for m in followup.context["messages"])
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
        assert f"orienter={bob.pk}".encode() in response.content  # Edit Hours modal trigger (hx-get)

    def it_shows_the_edit_hours_button_on_the_viewers_own_row(client: Client):
        # The viewer now edits their own hours through the same Edit Hours modal as everyone
        # else, so their own row carries the modal trigger. Every other staff row does too.
        user = _member_user("ov_ownrow", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="ov_ownrow", password="pass")
        content = client.get(_tab_url(guild)).content
        assert f"orienter={user.member.pk}".encode() in content
        assert f"orienter={bob.pk}".encode() in content

    def it_shows_a_plain_staffer_only_their_own_row(client: Client):
        # A plain orienter (no authority over others) now sees the Orientation Schedule scoped
        # to their own row with its Edit Hours trigger — never another staffer's row.
        guild = GuildFactory()
        user = _member_user("ov_staff", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        other = MemberFactory(full_legal_name="Other Orienter")
        GuildStaffMembershipFactory(guild=guild, member=other, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="ov_staff", password="pass")
        response = client.get(_tab_url(guild))
        assert response.status_code == 200
        assert b"Orientation Schedule" in response.content
        assert f"orienter={user.member.pk}".encode() in response.content
        # The other staffer's Orientation Schedule row + Edit Hours trigger are not rendered
        # (their name still appears on the Staff tab, so assert on the schedule trigger, not it).
        assert f"orienter={other.pk}".encode() not in response.content
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

    def it_re_renders_an_invalid_guild_scope_post_on_the_tab(client: Client):
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
        assert response.context["active_tab"] == "orientations"
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


def describe_slot_length_and_break_in_the_guild_modal():
    """The shared rule form's optional fields on the guild side: blank keeps one slot per window."""

    def _lead_with_rule(client: Client, username: str):
        user = _member_user(username, name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        rule = OrientationAvailabilityFactory(guild=guild, orienter=user.member)
        client.login(username=username, password="pass")
        return user, guild, rule

    def _existing_row(rule, **overrides) -> dict[str, str]:
        data = {
            "modal_rules-INITIAL_FORMS": "1",
            "modal_rules-0-id": str(rule.pk),
            "modal_rules-0-orientation_type": str(rule.orientation_type_id),
            "modal_rules-0-weekday": str(rule.weekday),
            "modal_rules-0-start_time": "18:00",
            "modal_rules-0-end_time": "20:00",
            "modal_rules-0-seats": str(rule.seats),
            "modal_rules-0-is_active": "on",
        }
        data.update(overrides)
        return data

    def it_shows_the_two_fields_in_the_modal(client: Client):
        user, guild, _rule = _lead_with_rule(client, "sl_show")
        response = client.get(_form_url(guild, user.member.pk))
        content = response.content.decode()
        assert "Slot length" in content
        assert "Break between slots" in content
        assert "Whole window" in content
        assert "Leave slot length blank to offer the whole window as one slot." in content

    def it_keeps_one_slot_per_window_when_slot_length_is_blank(client: Client):
        user, guild, rule = _lead_with_rule(client, "sl_blank")
        rule.end_time = __import__("datetime").time(20, 0)
        rule.save(update_fields=["end_time"])
        response = client.post(
            _hours_url(guild),
            _modal_rule_payload(str(user.member.pk), **_existing_row(rule)),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 204
        rule.refresh_from_db()
        assert rule.slot_minutes is None
        assert rule.buffer_minutes == 0
        first = rule.slots.order_by("starts_at").first()
        assert first is not None
        assert first.ends_at - first.starts_at == timedelta(hours=2)

    def it_carves_the_window_when_a_slot_length_is_set(client: Client):
        user, guild, rule = _lead_with_rule(client, "sl_carve")
        rule.end_time = __import__("datetime").time(20, 0)
        rule.save(update_fields=["end_time"])
        response = client.post(
            _hours_url(guild),
            _modal_rule_payload(
                str(user.member.pk),
                **_existing_row(rule, **{"modal_rules-0-slot_minutes": "60", "modal_rules-0-buffer_minutes": "0"}),
            ),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 204
        rule.refresh_from_db()
        assert rule.slot_minutes == 60
        starts = sorted(
            {timezone.localtime(s).strftime("%H:%M") for s in rule.slots.values_list("starts_at", flat=True)}
        )
        assert starts == ["18:00", "19:00"]

    def it_rejects_a_slot_length_longer_than_the_window(client: Client):
        user, guild, rule = _lead_with_rule(client, "sl_short")
        response = client.post(
            _hours_url(guild),
            _modal_rule_payload(
                str(user.member.pk),
                **_existing_row(rule, **{"modal_rules-0-end_time": "18:30", "modal_rules-0-slot_minutes": "60"}),
            ),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"This window is shorter than one slot." in response.content
        rule.refresh_from_db()
        assert rule.slot_minutes is None


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
    """The shared partial posts to hours_save_url; an error re-render must still carry it (both owners)."""

    def it_keeps_the_guild_save_url(client: Client):
        user = _member_user("url_guild", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        client.login(username="url_guild", password="pass")
        response = client.post(
            _hours_url(guild),
            _modal_rule_payload(str(user.member.pk), guild=guild, **{"modal_rules-0-end_time": "17:00"}),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert b"End time must be after the start time." in response.content
        assert f'hx-post="{_hours_url(guild)}"' in content
        assert 'hx-post=""' not in content

    def it_keeps_the_equipment_save_url(client: Client):
        from tests.membership.factories import EquipmentFactory, EquipmentStaffMembershipFactory

        user = _member_user("url_equip", name="Dana Reyes")
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Basics")
        client.login(username="url_equip", password="pass")
        save_url = reverse("hub_equipment_orientation_hours_save", args=[equipment.slug])
        response = client.post(
            save_url,
            {
                "orienter_scope": str(user.member.pk),
                "formset_prefix": "modal_rules",
                "modal_rules-TOTAL_FORMS": "1",
                "modal_rules-INITIAL_FORMS": "0",
                "modal_rules-MIN_NUM_FORMS": "0",
                "modal_rules-MAX_NUM_FORMS": "1000",
                "modal_rules-0-orientation_type": str(orientation_type.pk),
                "modal_rules-0-weekday": "1",
                "modal_rules-0-start_time": "18:00",
                "modal_rules-0-end_time": "17:00",
                "modal_rules-0-seats": "1",
                "modal_rules-0-is_active": "on",
            },
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "End time must be after the start time." in content
        assert f'hx-post="{save_url}"' in content
        assert 'hx-post=""' not in content
