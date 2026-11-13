"""BDD specs for the seven capability Get Set Up cards (#1167, part 2) and their seeded Guide pages.

Each card's done-by-doing step ticks from a row the member's own work writes (an admin side class
review, a space request decision, a reviewed or added event, a space they manage); Billing and
Equipment Administrator are reading steps only. A page asks for its cards in one query
(:meth:`SetupState.for_page`). Migration 0279 seeds one Guide page per card, idempotently.

Names are factory strings no changelog could contain (STANDARDS.md section 8).
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as django_apps
from django.urls import reverse

from classes.factories import ClassOfferingFactory
from classes.models import ClassApproval
from membership import capability_setup as cs
from membership.models import AdminCapability, Equipment, LeadershipBadge, Member, WikiPage
from membership.permission_setup import SetupState, spec_for
from membership.services.provisioning import provision_user_for_member
from tests.membership.factories import (
    CommunityEventFactory,
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    MemberFactory,
    SpaceRequestFactory,
)

pytestmark = pytest.mark.django_db

Cap = AdminCapability.Capability
SEED = importlib.import_module("membership.migrations.0279_seed_capability_guides")


class _Editor:
    """The one thing ``unseed`` reads from a schema editor: its connection's alias."""

    class connection:  # noqa: N801
        alias = "default"


def _holder(capability: str, *, with_user: bool = True) -> Member:
    member = MemberFactory(full_legal_name="Thessaly Varnum")
    if with_user:
        provision_user_for_member(member)
        member.refresh_from_db()
    member.admin_capabilities.create(capability=capability)
    return member


def _state(member: Member, capability: str) -> SetupState:
    state = SetupState.load(member, capability)
    assert state is not None
    return state


def _done(member: Member, capability: str) -> set[str]:
    return set(spec_for(capability).facts(member).done)


def describe_the_registry():
    def it_has_a_card_for_each_capability_with_a_page_of_its_own():
        for spec in cs.CAPABILITY_SETUPS:
            assert spec_for(spec.permission) is spec
        assert spec_for(Cap.REFUNDS) is None

    def it_names_the_event_creator_badge():
        assert cs.EVENT_CREATOR_SETUP.badge_name == "Event Creator"
        assert cs.CMS_ADMIN_SETUP.badge_name == "CMS Administrator"

    def it_sends_open_the_guide_to_the_shared_guide_page():
        assert cs.CMS_ADMIN_SETUP.guide_url == reverse("hub_permission_guide", args=[Cap.CLASS_APPROVER])

    def it_lands_a_step_on_its_filter_chip():
        step = cs.CMS_ADMIN_SETUP.step(cs.FIRST_REVIEW)
        assert step.action_url == f"{reverse('classes:admin_classes')}?status=awaiting_admin"

    def it_opens_the_page_the_card_lives_on():
        assert cs.EQUIPMENT_ADMIN_SETUP.page_url == reverse("hub_equipment_index")


def describe_tick_sources():
    def it_counts_admin_side_class_reviews_the_member_decided():
        member = _holder(Cap.CLASS_APPROVER)
        offering = ClassOfferingFactory()
        ClassApproval.objects.create(
            class_offering=offering, role=ClassApproval.Role.GUILD_LEAD, decided_by=member.user, decision="approved"
        )
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN, decided_by=member.user)
        assert _done(member, Cap.CLASS_APPROVER) == set()
        ClassApproval.objects.create(
            class_offering=ClassOfferingFactory(),
            role=ClassApproval.Role.ADMIN,
            decided_by=member.user,
            decision="changes_requested",
        )
        facts = cs.class_review_facts(member)
        assert facts.done == {cs.FIRST_REVIEW}
        assert [(stat.value, stat.label) for stat in facts.stats] == [(1, "Classes Reviewed")]

    def it_counts_space_requests_the_member_decided():
        member = _holder(Cap.SPACE_APPROVER)
        SpaceRequestFactory()
        assert _done(member, Cap.SPACE_APPROVER) == set()
        SpaceRequestFactory(reviewed_by=member.user)
        assert _done(member, Cap.SPACE_APPROVER) == {cs.FIRST_DECISION}

    def it_ticks_a_review_and_an_own_event_apart_for_a_calendar_admin():
        member = _holder(Cap.EVENTS_APPROVER)
        CommunityEventFactory(community=True, created_by=member.user)
        assert _done(member, Cap.EVENTS_APPROVER) == {cs.OWN_EVENT}
        CommunityEventFactory(community=True, reviewed_by=member.user)
        assert _done(member, Cap.EVENTS_APPROVER) == {cs.OWN_EVENT, cs.FIRST_REVIEW}

    def it_counts_an_event_the_creator_proposed():
        member = _holder(Cap.EVENT_CREATOR)
        assert _done(member, Cap.EVENT_CREATOR) == set()
        CommunityEventFactory(community=True, submitted_by=member.user)
        assert _done(member, Cap.EVENT_CREATOR) == {cs.FIRST_EVENT}

    def it_counts_a_room_or_space_the_member_added_but_not_a_tool():
        member = _holder(Cap.SPACE_MANAGER)
        EquipmentFactory(kind=Equipment.Kind.TOOL, added_by=member)
        assert _done(member, Cap.SPACE_MANAGER) == set()
        EquipmentFactory(kind=Equipment.Kind.ROOM, added_by=member)
        facts = cs.space_manager_facts(member)
        assert facts.done == {cs.FIRST_SPACE}
        assert [(stat.value, stat.label) for stat in facts.stats] == [(1, "Spaces Added")]

    def it_counts_a_space_added_before_added_by_by_its_self_granted_staff_row_once():
        member = _holder(Cap.SPACE_MANAGER)
        space = EquipmentFactory(kind=Equipment.Kind.SPACE, added_by=member)
        EquipmentStaffMembershipFactory(member=member, equipment=space, granted_by=member)
        older = EquipmentFactory(kind=Equipment.Kind.SPACE)
        EquipmentStaffMembershipFactory(member=member, equipment=older, granted_by=member)
        assert cs.space_manager_facts(member).stats[0].value == 2

    def it_does_not_count_a_space_someone_else_made_them_manager_of():
        member = _holder(Cap.SPACE_MANAGER)
        EquipmentStaffMembershipFactory(
            member=member, equipment=EquipmentFactory(kind=Equipment.Kind.ROOM), granted_by=MemberFactory()
        )
        assert _done(member, Cap.SPACE_MANAGER) == set()

    @pytest.mark.parametrize(
        "capability", [Cap.CLASS_APPROVER, Cap.SPACE_APPROVER, Cap.EVENTS_APPROVER, Cap.EVENT_CREATOR]
    )
    def it_ticks_nothing_for_a_member_with_no_account(capability: str):
        member = _holder(capability, with_user=False)
        CommunityEventFactory(community=True)  # an event with no creator must not count as theirs
        SpaceRequestFactory()
        assert _done(member, capability) == set()


def describe_finishing():
    @pytest.mark.parametrize("setup", [cs.BILLING_ADMIN_SETUP, cs.EQUIPMENT_ADMIN_SETUP])
    def it_gives_a_reading_only_card_its_badge_on_the_last_got_it(setup):
        member = _holder(setup.permission)
        state = _state(member, setup.permission)
        state.row.mark_read(setup.steps[0].key)
        assert not _state(member, setup.permission).finished
        state.row.mark_read(setup.steps[1].key)
        assert _state(member, setup.permission).finished
        assert LeadershipBadge.objects.get(permission=setup.permission).members.filter(pk=member.pk).exists()

    def it_finishes_calendar_admin_without_the_optional_own_event():
        member = _holder(Cap.EVENTS_APPROVER)
        _state(member, Cap.EVENTS_APPROVER).row.mark_read(cs.LEARN)
        CommunityEventFactory(community=True, reviewed_by=member.user)
        assert _state(member, Cap.EVENTS_APPROVER).finished

    def it_names_the_badge_after_the_spec():
        assert LeadershipBadge.for_permission(Cap.EVENT_CREATOR).label == "Event Creator"
        assert LeadershipBadge.for_permission(Cap.REFUNDS).label == "Refunds"


def describe_SetupState_for_page():
    def it_is_empty_without_a_member():
        assert SetupState.for_page(None, [Cap.EQUIPMENT]) == []

    def it_costs_one_query_for_someone_holding_none(django_assert_num_queries):
        member = MemberFactory()
        with django_assert_num_queries(1):
            assert SetupState.for_page(member, [Cap.EQUIPMENT, Cap.SPACE_MANAGER]) == []

    def it_lists_held_cards_in_the_order_asked_and_skips_permissions_with_none():
        member = _holder(Cap.SPACE_MANAGER)
        member.admin_capabilities.create(capability=Cap.EQUIPMENT)
        member.admin_capabilities.create(capability=Cap.REFUNDS)
        states = SetupState.for_page(member, [Cap.EQUIPMENT, Cap.REFUNDS, Cap.SPACE_MANAGER])
        assert [state.spec.permission for state in states] == [Cap.EQUIPMENT, Cap.SPACE_MANAGER]


def describe_the_seeded_guide_pages():
    def it_seeds_one_official_page_per_capability_and_is_idempotent():
        SEED.seed(django_apps, None)
        SEED.seed(django_apps, None)
        for permission, title, _body in SEED.PAGES:
            page = WikiPage.guide_page_for(permission)
            assert page is not None and page.title == title
            assert page.status == WikiPage.Status.OFFICIAL and page.guild_id is None
            assert page.revisions.count() == 1
        assert WikiPage.objects.exclude(guide_for="").count() == len(SEED.PAGES)

    def it_covers_every_part_two_card_and_each_step_section():
        seeded = {permission: body for permission, _title, body in SEED.PAGES}
        assert set(seeded) == {spec.permission for spec in cs.CAPABILITY_SETUPS}
        for spec in cs.CAPABILITY_SETUPS:
            for step in spec.steps:
                assert f"<h2>{step.guide_section}</h2>" in seeded[spec.permission].replace("&amp;", "&"), step.key

    def it_links_a_page_with_the_same_title_instead_of_making_one():
        existing = WikiPage.objects.create(title="Space Manager Guide", kind=WikiPage.Kind.HOWTO, body="<p>Ours.</p>")
        SEED.seed(django_apps, None)
        existing.refresh_from_db()
        assert existing.guide_for == Cap.SPACE_MANAGER
        assert WikiPage.objects.filter(title="Space Manager Guide").count() == 1

    def it_deletes_untouched_pages_and_unlinks_edited_ones_on_reverse():
        SEED.seed(django_apps, None)
        edited = WikiPage.objects.get(guide_for=Cap.EQUIPMENT)
        WikiPage.objects.filter(pk=edited.pk).update(body="<p>Rewritten by an admin.</p>")
        SEED.unseed(django_apps, _Editor())
        edited.refresh_from_db()
        assert edited.guide_for == ""
        assert not WikiPage.objects.filter(title="CMS Administrator Guide").exists()
