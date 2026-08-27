"""BDD specs for personal-slot request routing and the "with Bob" copy deltas.

A personal slot's request (email + in-app) goes to the slot's orienter + the guild
lead, deduped; guild slots keep the full leadership fan-out. Member-facing emails
and the ``.ics`` gain the orienter's name, guarded so guild-slot emails carry no
"with".
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core import mail

from core.models import Notification
from membership import orientations
from membership.models import GuildStaffMembership
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db


def _member_with_user(username: str, *, name: str = "") -> object:
    MembershipPlanFactory()
    member = User.objects.create_user(username=username, email=f"{username}@example.com").member
    if name:
        member.full_legal_name = name
        member.save(update_fields=["full_legal_name"])
    return member


def _routed_guild(username_prefix: str) -> tuple[object, object, object, object]:
    """Guild with a lead, ORIENTER staffer Bob, and a second staffer — all with users."""
    lead = _member_with_user(f"{username_prefix}_lead", name="Lead Person")
    guild = GuildFactory(guild_lead=lead)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    bob = _member_with_user(f"{username_prefix}_bob", name="Bob Placeholder")
    GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
    other = _member_with_user(f"{username_prefix}_other", name="Other Staffer")
    GuildStaffMembershipFactory(guild=guild, member=other, role=GuildStaffMembership.Role.CO_LEAD)
    return guild, lead, bob, other


def _staff_addresses(requester: object) -> set[str]:
    return {addr for m in mail.outbox for addr in m.to} - {requester.primary_email}


def describe_personal_slot_request_routing():
    def it_emails_exactly_the_orienter_and_the_lead():
        guild, lead, bob, other = _routed_guild("rt_p")
        requester = _member_with_user("rt_p_member")
        slot = OrientationSlotFactory(guild=guild, orienter=bob, enabled_settings=False)

        orientations.request_orientation(slot, requester)

        assert _staff_addresses(requester) == {bob.primary_email, lead.primary_email}
        assert other.primary_email not in _staff_addresses(requester)

    def it_dedupes_when_the_lead_is_the_orienter():
        guild, lead, _bob, _other = _routed_guild("rt_d")
        requester = _member_with_user("rt_d_member")
        slot = OrientationSlotFactory(guild=guild, orienter=lead, enabled_settings=False)

        orientations.request_orientation(slot, requester)

        lead_messages = [m for m in mail.outbox for addr in m.to if addr == lead.primary_email]
        assert len(lead_messages) == 1

    def it_scopes_the_in_app_notification_to_the_orienter_and_lead():
        guild, lead, bob, other = _routed_guild("rt_n")
        requester = _member_with_user("rt_n_member")
        slot = OrientationSlotFactory(guild=guild, orienter=bob, enabled_settings=False)

        orientations.request_orientation(slot, requester)

        notified = set(Notification.objects.filter(trigger="orientation_requested").values_list("user", flat=True))
        assert notified == {bob.user.pk, lead.user.pk}
        assert other.user.pk not in notified

    def it_routes_to_the_orienter_alone_when_the_guild_has_no_lead():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        bob = _member_with_user("rt_nl_bob", name="Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        requester = _member_with_user("rt_nl_member")
        slot = OrientationSlotFactory(guild=guild, orienter=bob, enabled_settings=False)

        orientations.request_orientation(slot, requester)

        assert _staff_addresses(requester) == {bob.primary_email}

    def it_keeps_the_full_leadership_fan_out_for_a_guild_slot():
        guild, lead, bob, other = _routed_guild("rt_g")
        requester = _member_with_user("rt_g_member")
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False)

        orientations.request_orientation(slot, requester)

        assert _staff_addresses(requester) == {lead.primary_email, bob.primary_email, other.primary_email}

    def it_skips_an_audience_member_with_no_email():
        from tests.membership.factories import MemberFactory

        guild, lead, bob, other = _routed_guild("rt_ne")
        silent = MemberFactory(full_legal_name="No Email", _pre_signup_email="")
        GuildStaffMembershipFactory(guild=guild, member=silent, role=GuildStaffMembership.Role.CO_LEAD)
        requester = _member_with_user("rt_ne_member")
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False)

        orientations.request_orientation(slot, requester)

        assert _staff_addresses(requester) == {lead.primary_email, bob.primary_email, other.primary_email}

    def it_scopes_the_cancel_ping_the_same_way():
        guild, lead, bob, other = _routed_guild("rt_c")
        requester = _member_with_user("rt_c_member")
        slot = OrientationSlotFactory(guild=guild, orienter=bob, enabled_settings=False)
        booking = OrientationBookingFactory(slot=slot, member=requester)
        Notification.objects.all().delete()

        orientations.cancel_orientation(booking, actor_label=requester.display_name)

        cancelled = set(Notification.objects.filter(trigger="orientation_requested").values_list("user", flat=True))
        assert bob.user.pk in cancelled
        assert lead.user.pk in cancelled
        assert other.user.pk not in cancelled


def describe_with_copy_deltas():
    def it_puts_the_orienter_in_the_members_request_email_and_ics():
        guild, _lead, bob, _other = _routed_guild("cp_r")
        requester = _member_with_user("cp_r_member")
        slot = OrientationSlotFactory(guild=guild, orienter=bob, enabled_settings=False)

        booking = orientations.request_orientation(slot, requester)

        member_email = next(m for m in mail.outbox if m.to == [requester.primary_email])
        html = member_email.alternatives[0][0]
        assert "with Bob" in member_email.body
        assert "With Bob" in html
        ics = orientations.build_ics(booking, method="REQUEST", status="TENTATIVE")
        assert b"With Bob." in ics

    def it_keeps_guild_slot_emails_and_ics_free_of_with():
        guild, _lead, _bob, _other = _routed_guild("cp_g")
        requester = _member_with_user("cp_g_member")
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False)

        booking = orientations.request_orientation(slot, requester)

        member_email = next(m for m in mail.outbox if m.to == [requester.primary_email])
        assert "with " not in member_email.body.lower()
        assert b"with" not in orientations.build_ics(booking, method="REQUEST", status="TENTATIVE").lower()

    def it_says_bob_confirmed_in_the_confirmation_email():
        guild, _lead, bob, _other = _routed_guild("cp_c")
        requester = _member_with_user("cp_c_member")
        slot = OrientationSlotFactory(guild=guild, orienter=bob, enabled_settings=False)
        booking = OrientationBookingFactory(slot=slot, member=requester)
        mail.outbox.clear()

        orientations.confirm_orientation(booking)

        member_email = next(m for m in mail.outbox if m.to == [requester.primary_email])
        assert "Bob confirmed your orientation" in member_email.body
        assert "Bob confirmed your orientation" in member_email.alternatives[0][0]

    def it_names_the_slot_owner_in_the_lead_request_email():
        guild, lead, bob, _other = _routed_guild("cp_l")
        requester = _member_with_user("cp_l_member")
        slot = OrientationSlotFactory(guild=guild, orienter=bob, enabled_settings=False)

        orientations.request_orientation(slot, requester)

        lead_email = next(m for m in mail.outbox if lead.primary_email in m.to)
        assert "one of Bob's orientation slots" in lead_email.body
        assert "one of Bob's orientation slots" in lead_email.alternatives[0][0]

    def it_names_the_orienter_in_the_cancellation_email():
        guild, _lead, bob, _other = _routed_guild("cp_x")
        requester = _member_with_user("cp_x_member")
        slot = OrientationSlotFactory(guild=guild, orienter=bob, enabled_settings=False)
        booking = OrientationBookingFactory(slot=slot, member=requester)
        mail.outbox.clear()

        orientations.cancel_orientation(booking, actor_label="the guild")

        member_email = next(m for m in mail.outbox if m.to == [requester.primary_email])
        assert "with Bob" in member_email.body
        assert "With Bob" in member_email.alternatives[0][0]
