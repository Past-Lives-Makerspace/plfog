"""The settings matrix only surfaces channels an event actually offers."""

import pytest
from django.contrib.auth.models import User

from core.events import settings_matrix
from core.events.registry import Channel
from membership.models import AdminCapability, Member
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def _sections(user):
    return [section for section, _rows in settings_matrix.build_matrix(user)]


def _section_of(user, event_key):
    """Return the section a given event row renders under, or None if not shown."""
    for section, rows in settings_matrix.build_matrix(user):
        if any(row.event_key == event_key for row in rows):
            return section
    return None


def describe_visible_channels():
    def it_includes_channels_events_offer():
        user = User.objects.create_user(username="vc", email="vc@example.com")
        channels = settings_matrix.visible_channels(user)
        assert Channel.IN_APP in channels
        assert Channel.EMAIL in channels
        assert Channel.PUSH in channels

    def it_drops_channels_no_event_offers():
        # SCHEDULED_EMAIL and DIGEST are declared shells no event uses yet — they
        # must not render as dead, all-"—" columns on the settings page.
        user = User.objects.create_user(username="vc2", email="vc2@example.com")
        channels = settings_matrix.visible_channels(user)
        assert Channel.SCHEDULED_EMAIL not in channels
        assert Channel.DIGEST not in channels

    def it_keeps_user_channel_display_order():
        user = User.objects.create_user(username="vc3", email="vc3@example.com")
        channels = settings_matrix.visible_channels(user)
        assert channels == [c for c in settings_matrix.USER_CHANNELS if c in channels]


def describe_build_matrix():
    def it_emits_one_cell_per_visible_channel():
        user = User.objects.create_user(username="vc4", email="vc4@example.com")
        expected = len(settings_matrix.visible_channels(user))
        matrix = settings_matrix.build_matrix(user)
        assert matrix  # categories exist
        for _category, rows in matrix:
            for row in rows:
                assert len(row.cells) == expected


def describe_channel_labels():
    def it_labels_push_plainly():
        # Renamed from "Push (Browser)" now that native push is the primary carrier.
        assert settings_matrix.CHANNEL_LABELS[Channel.PUSH] == "Push"


def describe_push_defaults():
    def it_offers_push_on_every_row_with_a_mix_of_defaults():
        user = User.objects.create_user(username="pd", email="pd@example.com")
        push_cells = [
            cell
            for _section, rows in settings_matrix.build_matrix(user)
            for row in rows
            for cell in row.cells
            if cell.channel is Channel.PUSH and cell.present
        ]
        assert push_cells  # every in-app row offers a push toggle
        # important events default on, routine ones default off — both appear for a member
        assert any(cell.enabled for cell in push_cells)
        assert any(not cell.enabled for cell in push_cells)


def describe_staff_section():
    # A pure-capability approval event (routes to SPACE_APPROVERS): visible ONLY to a
    # holder of the Space capability, and grouped under Staff & leadership, not "Spaces".
    CAP_EVENT = "space.lease_requested"
    # An admin-only alert routed by role (FOG_ADMINS), not by a capability.
    ADMIN_ALERT_EVENT = "new_member_joined"
    # A composite event (GUILD_LEADERSHIP_OR_CLASS_APPROVERS): visible to guild leadership
    # OR a Class-capability holder.
    COMPOSITE_EVENT = "class_review_requested"

    def _member_user(username, **member_kwargs):
        # create_user auto-provisions a plain Member (fog_role=member); fetch and
        # mutate it rather than creating a second member for the same user.
        user = User.objects.create_user(username=username, email=f"{username}@example.com")
        member = Member.objects.get(user=user)
        if member_kwargs:
            for field, value in member_kwargs.items():
                setattr(member, field, value)
            member.save()
        return user

    def _grant(user, capability):
        Member.objects.get(user=user).admin_capabilities.create(capability=capability)

    def describe_a_plain_member():
        def it_never_sees_the_staff_section(db):
            user = _member_user("plain1")  # default fog_role=member, no capabilities/leadership
            assert settings_matrix.STAFF_SECTION not in _sections(user)

        def it_sees_none_of_the_staff_events(db):
            user = _member_user("plain2")
            assert _section_of(user, CAP_EVENT) is None
            assert _section_of(user, ADMIN_ALERT_EVENT) is None

        def it_still_sees_its_member_categories(db):
            user = _member_user("plain3")
            assert _sections(user)  # member-facing categories remain

    def describe_a_user_with_no_member():
        def it_never_sees_the_staff_section(db):
            user = User.objects.create_user(username="nomember", email="nomember@example.com")
            Member.objects.filter(user=user).delete()
            assert not Member.objects.filter(user=user).exists()
            assert settings_matrix.STAFF_SECTION not in _sections(user)

    def describe_a_fog_admin_without_capabilities():
        def it_sees_the_section_for_admin_alerts_rendered_first(db):
            user = _member_user("admin1", fog_role=Member.FogRole.ADMIN)
            sections = _sections(user)
            assert sections[0] == settings_matrix.STAFF_SECTION
            assert _section_of(user, ADMIN_ALERT_EVENT) == settings_matrix.STAFF_SECTION

        def it_does_not_see_a_capability_it_does_not_hold(db):
            user = _member_user("admin2", fog_role=Member.FogRole.ADMIN)
            assert _section_of(user, CAP_EVENT) is None

    def describe_a_capability_holder():
        def it_sees_and_groups_the_capabilitys_event(db):
            user = _member_user("cap1")  # plain member granted one duty
            _grant(user, AdminCapability.Capability.SPACE_APPROVER)
            assert _section_of(user, CAP_EVENT) == settings_matrix.STAFF_SECTION

        def it_does_not_see_a_capability_it_does_not_hold(db):
            user = _member_user("cap2")
            _grant(user, AdminCapability.Capability.SPACE_APPROVER)
            assert _section_of(user, "class_validation_requested") is None

    def describe_a_guild_lead():
        def it_sees_composite_leadership_events_but_not_unheld_capabilities(db):
            user = _member_user("lead1")
            GuildFactory(guild_lead=Member.objects.get(user=user))
            assert _section_of(user, COMPOSITE_EVENT) == settings_matrix.STAFF_SECTION
            assert _section_of(user, CAP_EVENT) is None

    def describe_a_guild_officer():
        # voting.officers_closing_soon routes to ALL_GUILD_LEADS, whose resolver filters to
        # active members — so the row must track Member.status, not just the role.
        ALL_LEADS_EVENT = "voting.officers_closing_soon"

        def it_shows_all_guild_leads_rows_to_an_active_officer(db):
            user = _member_user("officer1", fog_role=Member.FogRole.GUILD_OFFICER)
            assert _section_of(user, ALL_LEADS_EVENT) == settings_matrix.STAFF_SECTION

        def it_hides_all_guild_leads_rows_from_a_former_officer(db):
            user = _member_user("officer2", fog_role=Member.FogRole.GUILD_OFFICER, status=Member.Status.FORMER)
            assert _section_of(user, ALL_LEADS_EVENT) is None
