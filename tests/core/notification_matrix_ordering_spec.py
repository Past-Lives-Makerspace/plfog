"""Section ordering + orientation re-tag for the settings notification matrix.

Covers the settings-restructure changes: the new CATEGORY_ORDER (Orientations, Guilds,
Events first; Staff & Leadership dead-last), the two orientation trigger re-tags, and the
event-key round-trip that proves the re-tag never touches stored preferences.
"""

import pytest
from django.contrib.auth.models import User

from core.events import settings_matrix
from core.events.registry import Channel
from core.models import NotificationPreference
from membership.models import GuildStaffMembership, Member
from tests.membership.factories import GuildStaffMembershipFactory

pytestmark = pytest.mark.django_db


def _member_user(username, **member_kwargs):
    user = User.objects.create_user(username=username, email=f"{username}@example.com")
    member = Member.objects.get(user=user)
    if member_kwargs:
        for field, value in member_kwargs.items():
            setattr(member, field, value)
        member.save()
    return user, member


def _sections(user):
    return [section for section, _rows in settings_matrix.build_matrix(user)]


def _section_of(user, event_key):
    for section, rows in settings_matrix.build_matrix(user):
        if any(row.event_key == event_key for row in rows):
            return section
    return None


def describe_section_order():
    def it_starts_with_orientations_guilds_events_for_a_plain_member():
        user, _member = _member_user("orderplain")
        sections = _sections(user)
        assert "Orientations" in sections
        assert "Guilds" in sections
        assert "Events" in sections
        # The three lead the page, in this relative order.
        assert sections.index("Orientations") < sections.index("Guilds") < sections.index("Events")

    def it_never_shows_the_staff_section_to_a_plain_member():
        user, _member = _member_user("orderplain2")
        assert settings_matrix.STAFF_SECTION not in _sections(user)

    def it_renders_the_staff_section_last_for_an_admin():
        user, _member = _member_user("orderadmin", fog_role=Member.FogRole.ADMIN)
        sections = _sections(user)
        assert sections[-1] == settings_matrix.STAFF_SECTION

    def it_sorts_an_unknown_category_alpha_before_staff():
        # _ordered_categories is the pure ordering rule: known categories in CATEGORY_ORDER,
        # then unknown extras alpha, then the staff section dead-last.
        ordered = settings_matrix._ordered_categories({"Zebra", "Guilds", settings_matrix.STAFF_SECTION, "Apple"})
        assert ordered == ["Guilds", "Apple", "Zebra", settings_matrix.STAFF_SECTION]


def describe_orientation_retag():
    def it_moves_orientation_update_under_orientations():
        user, _member = _member_user("retag1")
        assert _section_of(user, "orientation_update") == "Orientations"
        # And it is no longer grouped under Guilds.
        assert _section_of(user, "orientation_update") != "Guilds"

    def it_keeps_orientation_requested_in_staff_for_an_orienter():
        user, member = _member_user("retag2")
        GuildStaffMembershipFactory(member=member, role=GuildStaffMembership.Role.ORIENTER)
        assert _section_of(user, "orientation_requested") == settings_matrix.STAFF_SECTION

    def it_hides_orientation_requested_from_a_plain_member():
        user, _member = _member_user("retag3")
        assert _section_of(user, "orientation_requested") is None

    def it_round_trips_a_saved_orientation_update_preference():
        # Re-tag safety: preferences are stored per (user, event_key, channel); the category
        # never touches the DB, so a saved row survives the re-tag and round-trips unchanged.
        user, _member = _member_user("retag4")
        NotificationPreference.objects.create(user=user, event_key="orientation_update", channel="email", enabled=True)
        # build_matrix reflects the saved row as a checked email cell, under Orientations.
        row = next(
            r
            for section, rows in settings_matrix.build_matrix(user)
            if section == "Orientations"
            for r in rows
            if r.event_key == "orientation_update"
        )
        email_cell = next(c for c in row.cells if c.channel is Channel.EMAIL and c.present)
        assert email_cell.enabled is True
        # Saving that same checked box leaves the stored row untouched.
        settings_matrix.save_matrix(user, {email_cell.name: "on"})
        pref = NotificationPreference.objects.get(user=user, event_key="orientation_update", channel="email")
        assert pref.enabled is True
