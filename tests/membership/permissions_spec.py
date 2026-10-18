"""BDD specs for guild-lead edit permissions — the single FK-based source of truth.

Covers ``Member.can_edit_class`` (role-based) and the request-level helpers in
``membership/permissions.py`` (``view_as``-aware). The positive request-helper
paths are exercised through the views (see the detail-page and hero-adjust
specs); here we lock the model rule and the defensive request branches.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory

from classes.factories import CategoryFactory, ClassOfferingFactory, UserFactory
from hub.view_as import ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER, ViewAs
from membership.models import AdminCapability, CommunityEvent, GuildStaffMembership, Member
from membership.permissions import (
    EventCreateMode,
    can_administer_events,
    can_create_events,
    can_edit_category,
    can_edit_class,
    can_edit_event,
    can_edit_guild,
    can_manage_orientations,
    can_print_class_marketing,
    editable_meeting_scopes,
    event_create_mode,
    is_effective_staff,
)
from tests.membership.factories import (
    CommunityEventFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def describe_member_can_edit_class():
    def it_allows_the_instructor():
        me = MemberFactory()
        offering = ClassOfferingFactory(instructor=me)
        assert me.can_edit_class(offering) is True

    def it_allows_a_second_instructor_and_not_a_bystander():
        # #686: every listed instructor edits; being the payee is not the test.
        lead, me, other = MemberFactory(), MemberFactory(), MemberFactory()
        offering = ClassOfferingFactory(instructor=lead, instructors=[lead, me])
        assert me.can_edit_class(offering) is True
        assert other.can_edit_class(offering) is False

    def it_allows_the_lead_of_the_categorys_guild():
        lead = MemberFactory()
        guild = GuildFactory(guild_lead=lead)
        offering = ClassOfferingFactory(category=CategoryFactory(guild=guild))
        assert lead.can_edit_class(offering) is True

    def it_allows_admins_and_officers():
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        officer = MemberFactory(fog_role=Member.FogRole.GUILD_OFFICER)
        offering = ClassOfferingFactory()
        assert admin.can_edit_class(offering) is True
        assert officer.can_edit_class(offering) is True

    def it_denies_a_lead_of_another_guild():
        lead = MemberFactory()
        GuildFactory(guild_lead=lead)
        offering = ClassOfferingFactory(category=CategoryFactory(guild=GuildFactory()))
        assert lead.can_edit_class(offering) is False

    def it_allows_a_staff_member_of_the_categorys_guild():
        staff = MemberFactory()
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=staff, role=GuildStaffMembership.Role.CO_LEAD)
        offering = ClassOfferingFactory(category=CategoryFactory(guild=guild))
        assert staff.can_edit_class(offering) is True

    def it_denies_a_plain_member_even_when_the_category_has_no_guild():
        plain = MemberFactory()
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None))
        assert plain.can_edit_class(offering) is False


def describe_member_can_manage_orientations():
    def it_allows_an_editor():
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        lead = MemberFactory()
        guild = GuildFactory(guild_lead=lead)
        assert admin.can_manage_orientations(guild) is True
        assert lead.can_manage_orientations(guild) is True

    def it_allows_a_staff_orienter():
        orienter = MemberFactory()
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=orienter, role=GuildStaffMembership.Role.ORIENTER)
        assert orienter.can_manage_orientations(guild) is True

    def it_allows_any_staff_role_not_just_orienters():
        secretary = MemberFactory()
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=secretary, role=GuildStaffMembership.Role.SECRETARY)
        assert secretary.can_manage_orientations(guild) is True

    def it_denies_an_unrelated_member():
        stranger = MemberFactory()
        guild = GuildFactory()
        assert stranger.can_manage_orientations(guild) is False

    def it_reports_staff_membership_via_is_guild_staff():
        member = MemberFactory()
        assert member.is_guild_staff is False
        GuildStaffMembershipFactory(member=member)
        assert member.is_guild_staff is True


def describe_member_can_edit_guild_via_staff():
    def it_allows_a_staff_member_to_edit_the_guild():
        staff = MemberFactory()
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=staff, role=GuildStaffMembership.Role.SECRETARY)
        assert staff.can_edit_guild(guild) is True
        assert guild.is_staffed_by(staff) is True

    def it_lists_led_and_staffed_guilds_in_staffed_guilds():
        member = MemberFactory()
        led = GuildFactory(guild_lead=member)
        staffed = GuildFactory()
        GuildStaffMembershipFactory(guild=staffed, member=member, role=GuildStaffMembership.Role.TREASURER)
        unrelated = GuildFactory()
        ids = set(member.staffed_guilds.values_list("pk", flat=True))
        assert led.pk in ids
        assert staffed.pk in ids
        assert unrelated.pk not in ids


def describe_request_helpers_without_view_as():
    def it_denies_an_anonymous_request():
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        assert is_effective_staff(request) is False
        assert can_edit_guild(request, GuildFactory()) is False
        assert can_edit_category(request, CategoryFactory(guild=None)) is False
        # A category with a guild defers to can_edit_guild — still denied here.
        assert can_edit_category(request, CategoryFactory(guild=GuildFactory())) is False

    def it_denies_orientation_management_for_an_anonymous_request():
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        assert can_manage_orientations(request, GuildFactory()) is False

    def it_denies_an_authenticated_request_with_no_view_as():
        request = RequestFactory().get("/")
        request.user = UserFactory(username="no-viewas@example.com")
        assert can_edit_class(request, ClassOfferingFactory()) is False

    def it_locks_class_marketing_for_a_request_with_no_view_as_unless_published():
        # The admin override needs view_as; the model rule (published unlocks) needs nothing.
        from classes.models import ClassOffering

        request = RequestFactory().get("/")
        request.user = UserFactory(username="no-viewas-marketing@example.com")
        assert can_print_class_marketing(request, ClassOfferingFactory()) is False
        assert can_print_class_marketing(request, ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)) is True


def describe_request_helpers_via_views():
    def it_lets_a_guild_officer_edit_any_guilds_class(client):
        user = UserFactory(username="officer@example.com")
        member = user.member
        member.fog_role = Member.FogRole.GUILD_OFFICER
        member.save(update_fields=["fog_role"])
        member.sync_user_permissions()
        offering = ClassOfferingFactory(category=CategoryFactory(guild=GuildFactory()))
        ct = ContentType.objects.get_for_model(offering)

        client.force_login(user)
        response = client.post(
            "/hero-adjust/",
            {"content_type_id": ct.id, "object_id": offering.id, "crop": {"x": 1, "y": 1, "w": 1, "h": 1}},
            content_type="application/json",
        )

        assert response.status_code == 200


def _granted_user(capability: str):
    """A plain member's user holding one admin capability."""
    MembershipPlanFactory()
    user = UserFactory()
    user.member.admin_capabilities.create(capability=capability)
    return user


def _request_as(user, *, roles: set[str] | None = None, picked: str | None = None):
    """A request whose effective role is ``roles`` narrowed to ``picked``; no view_as when roles is None."""
    request = RequestFactory().get("/")
    request.user = user
    if roles is not None:
        request.view_as = ViewAs(actual=frozenset(roles), picked=picked)
    return request


def describe_can_administer_events():
    """Who runs the Calendar's events site-wide: an admin (view_as-aware) or a Calendar
    Administrator (read off the actual member, so a preview neither grants nor hides it)."""

    def it_allows_an_admin():
        assert can_administer_events(_request_as(UserFactory(), roles={ROLE_ADMIN, ROLE_MEMBER})) is True

    def it_denies_an_admin_previewing_as_a_member():
        request = _request_as(UserFactory(), roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_MEMBER)
        assert can_administer_events(request) is False

    def it_allows_a_calendar_administrator():
        user = _granted_user(AdminCapability.Capability.EVENTS_APPROVER)
        assert can_administer_events(_request_as(user, roles={ROLE_MEMBER})) is True

    def it_keeps_a_calendar_administrators_grant_through_a_preview():
        # The capability leg is preview-independent, like the house capability gates.
        user = _granted_user(AdminCapability.Capability.EVENTS_APPROVER)
        user.member.fog_role = Member.FogRole.ADMIN
        user.member.save(update_fields=["fog_role"])
        request = _request_as(user, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_MEMBER)
        assert can_administer_events(request) is True

    def it_denies_a_can_create_events_holder():
        user = _granted_user(AdminCapability.Capability.EVENT_CREATOR)
        assert can_administer_events(_request_as(user, roles={ROLE_MEMBER})) is False

    def it_denies_a_plain_member_and_a_guild_officer():
        MembershipPlanFactory()
        assert can_administer_events(_request_as(UserFactory(), roles={ROLE_MEMBER})) is False
        assert can_administer_events(_request_as(UserFactory(), roles={ROLE_GUILD_OFFICER, ROLE_MEMBER})) is False

    def it_denies_a_user_with_no_member_and_an_anonymous_request():
        user = UserFactory(username="no-member-events@example.com")
        user.member.delete()
        user.refresh_from_db()
        assert can_administer_events(_request_as(user, roles={ROLE_MEMBER})) is False
        assert can_administer_events(_request_as(AnonymousUser())) is False


def describe_event_create_mode():
    """What saving a new Calendar event does: DIRECT for an administrator, REVIEW for a
    "Can create events" holder, None for everyone else; ``can_create_events`` is its bool."""

    def it_publishes_directly_for_an_admin():
        request = _request_as(UserFactory(), roles={ROLE_ADMIN, ROLE_MEMBER})
        assert event_create_mode(request) == EventCreateMode.DIRECT
        assert can_create_events(request) is True

    def it_publishes_directly_for_a_calendar_administrator():
        user = _granted_user(AdminCapability.Capability.EVENTS_APPROVER)
        assert event_create_mode(_request_as(user, roles={ROLE_MEMBER})) == EventCreateMode.DIRECT

    def it_reviews_first_for_a_can_create_events_holder():
        user = _granted_user(AdminCapability.Capability.EVENT_CREATOR)
        request = _request_as(user, roles={ROLE_MEMBER})
        assert event_create_mode(request) == EventCreateMode.REVIEW
        assert can_create_events(request) is True

    def it_gives_a_holder_who_also_administers_the_direct_mode():
        user = _granted_user(AdminCapability.Capability.EVENT_CREATOR)
        user.member.admin_capabilities.create(capability=AdminCapability.Capability.EVENTS_APPROVER)
        assert event_create_mode(_request_as(user, roles={ROLE_MEMBER})) == EventCreateMode.DIRECT

    def it_gives_a_plain_member_nothing():
        MembershipPlanFactory()
        request = _request_as(UserFactory(), roles={ROLE_MEMBER})
        assert event_create_mode(request) is None
        assert can_create_events(request) is False

    def it_gives_an_admin_previewing_as_a_member_nothing():
        request = _request_as(UserFactory(), roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_MEMBER)
        assert event_create_mode(request) is None

    def it_gives_a_user_with_no_member_nothing():
        user = UserFactory(username="no-member-create@example.com")
        user.member.delete()
        user.refresh_from_db()
        assert event_create_mode(_request_as(user, roles={ROLE_MEMBER})) is None


def describe_can_edit_event():
    """The request-level ``can_edit_event`` (view_as-aware): the author of their own open
    proposal always may; otherwise site-wide events take ``can_administer_events`` and guild
    events defer to ``can_edit_guild`` (lead / staff / admin / officer)."""

    def _member_user() -> Member:
        MembershipPlanFactory()
        return UserFactory().member

    def _request(user, *, roles: set[str], picked: str | None = None):
        return _request_as(user, roles=roles, picked=picked)

    def it_lets_an_admin_edit_a_site_wide_event(db):
        event = CommunityEventFactory(community=True)  # guild is None
        request = _request(UserFactory(), roles={ROLE_ADMIN, ROLE_MEMBER})
        assert can_edit_event(request, event) is True

    def it_lets_a_calendar_administrator_edit_a_site_wide_event(db):
        event = CommunityEventFactory(community=True)
        user = _granted_user(AdminCapability.Capability.EVENTS_APPROVER)
        assert can_edit_event(_request(user, roles={ROLE_MEMBER}), event) is True

    def it_denies_a_calendar_administrator_on_a_guild_event_they_do_not_staff(db):
        event = CommunityEventFactory(guild=GuildFactory())
        user = _granted_user(AdminCapability.Capability.EVENTS_APPROVER)
        assert can_edit_event(_request(user, roles={ROLE_MEMBER}), event) is False

    def it_denies_a_can_create_events_holder_on_someone_elses_site_wide_event(db):
        event = CommunityEventFactory(community=True)
        user = _granted_user(AdminCapability.Capability.EVENT_CREATOR)
        assert can_edit_event(_request(user, roles={ROLE_MEMBER}), event) is False

    def it_lets_the_author_edit_their_own_open_proposal(db):
        MembershipPlanFactory()
        author = UserFactory()
        pending = CommunityEventFactory(community=True, pending=True, submitted_by=author)
        sent_back = CommunityEventFactory(
            community=True, submitted_by=author, moderation_state=CommunityEvent.ModerationState.CHANGES_REQUESTED
        )
        request = _request(author, roles={ROLE_MEMBER})
        assert can_edit_event(request, pending) is True
        assert can_edit_event(request, sent_back) is True

    def it_denies_the_author_once_their_proposal_is_published_or_declined(db):
        MembershipPlanFactory()
        author = UserFactory()
        published = CommunityEventFactory(community=True, submitted_by=author)  # PUBLISHED
        declined = CommunityEventFactory(community=True, declined=True, submitted_by=author)
        request = _request(author, roles={ROLE_MEMBER})
        assert can_edit_event(request, published) is False
        assert can_edit_event(request, declined) is False

    def it_denies_another_members_open_proposal(db):
        MembershipPlanFactory()
        event = CommunityEventFactory(community=True, pending=True)
        assert can_edit_event(_request(UserFactory(), roles={ROLE_MEMBER}), event) is False

    def it_never_treats_an_unsubmitted_row_as_an_anonymous_visitors_own(db):
        # submitted_by is NULL on an admin-authored row and an anonymous user has no pk, so a
        # naive "submitted_by_id == user.pk" would read None == None as the author's own.
        event = CommunityEventFactory(community=True, moderation_state=CommunityEvent.ModerationState.PENDING)
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        assert event.submitted_by_id is None
        assert can_edit_event(request, event) is False

    def it_denies_a_guild_officer_on_a_site_wide_event(db):
        # Site-wide authoring belongs to admins and Calendar Administrators, so an officer,
        # who CAN edit guild content, cannot edit a site-wide event.
        event = CommunityEventFactory(community=True)
        request = _request(UserFactory(), roles={ROLE_GUILD_OFFICER, ROLE_MEMBER})
        assert can_edit_event(request, event) is False

    def it_denies_an_admin_previewing_as_member_on_a_site_wide_event(db):
        event = CommunityEventFactory(community=True)
        request = _request(UserFactory(), roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_MEMBER)
        assert can_edit_event(request, event) is False

    def it_allows_the_lead_of_the_events_guild(db):
        lead = _member_user()
        guild = GuildFactory(guild_lead=lead)
        event = CommunityEventFactory(guild=guild)
        request = _request(lead.user, roles={ROLE_MEMBER})
        assert can_edit_event(request, event) is True

    def it_allows_a_staff_member_of_the_events_guild(db):
        staff = _member_user()
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=staff, role=GuildStaffMembership.Role.SECRETARY)
        event = CommunityEventFactory(guild=guild)
        request = _request(staff.user, roles={ROLE_MEMBER})
        assert can_edit_event(request, event) is True

    def it_denies_a_lead_of_another_guild(db):
        lead = _member_user()
        GuildFactory(guild_lead=lead)
        event = CommunityEventFactory(guild=GuildFactory())  # a different guild
        request = _request(lead.user, roles={ROLE_MEMBER})
        assert can_edit_event(request, event) is False

    def it_denies_a_plain_member_on_a_guild_event(db):
        plain = _member_user()
        event = CommunityEventFactory(guild=GuildFactory())
        request = _request(plain.user, roles={ROLE_MEMBER})
        assert can_edit_event(request, event) is False

    def it_denies_an_anonymous_request(db):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        assert can_edit_event(request, CommunityEventFactory(community=True)) is False
        assert can_edit_event(request, CommunityEventFactory(guild=GuildFactory())) is False


def describe_editable_meeting_scopes():
    """The bulk scope helper behind the Meetings home + create modal (§6.2) — the
    same answers as can_edit_guild / can_edit_meeting, two queries total."""

    def _request(user, *, roles: set[str] | None = None, picked: str | None = None):
        request = RequestFactory().get("/")
        request.user = user
        if roles is not None:
            request.view_as = ViewAs(actual=frozenset(roles), picked=picked)
        return request

    def it_returns_nothing_for_an_anonymous_request():
        GuildFactory()
        request = _request(AnonymousUser())
        assert editable_meeting_scopes(request) == ([], False)

    def it_returns_nothing_for_an_authenticated_request_with_no_member():
        GuildFactory()
        request = _request(UserFactory(username="scopeless@example.com"), roles={ROLE_MEMBER})
        request.user.member.delete()
        request.user.refresh_from_db()
        assert editable_meeting_scopes(request) == ([], False)

    def it_gives_effective_staff_every_guild_plus_council():
        beta = GuildFactory(name="Beta")
        alpha = GuildFactory(name="Alpha")
        request = _request(UserFactory(username="scopeadmin@example.com"), roles={ROLE_ADMIN, ROLE_MEMBER})
        guilds, council = editable_meeting_scopes(request)
        assert guilds == [alpha, beta]  # name-ordered
        assert council is True

    def it_gives_a_lead_their_guilds_plus_council():
        MembershipPlanFactory()
        member = UserFactory().member
        led = GuildFactory(name="Led", guild_lead=member)
        staffed = GuildFactory(name="Staffed")
        GuildStaffMembershipFactory(guild=staffed, member=member)
        GuildFactory(name="Unrelated")
        request = _request(member.user, roles={ROLE_MEMBER})
        guilds, council = editable_meeting_scopes(request)
        assert guilds == [led, staffed]
        assert council is True

    def it_denies_council_to_a_member_with_no_authority_anywhere():
        MembershipPlanFactory()
        GuildFactory()
        request = _request(UserFactory().member.user, roles={ROLE_MEMBER})
        assert editable_meeting_scopes(request) == ([], False)
