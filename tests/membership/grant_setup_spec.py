"""BDD specs for Get Set Up part 3 (#1167): the three cards with no page of their own, and the three
grants that are not capabilities (Instructor, Admin, Class Reviewer).

Each grant says who holds it (``grant_of``), ticks from real work, gives its badge on finishing and
takes it back on every revoke path. Migration 0282 seeds their six Guide pages.

Names are factory strings no changelog could contain (STANDARDS.md section 8).
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as django_apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from billing.models import PaymentRefund
from classes.factories import ClassOfferingFactory, DiscountCodeRequestFactory
from classes.models import ClassApproval, ClassOffering
from membership import capability_setup as cs
from membership import grant_setup as gs
from membership.models import AdminCapability, LeadershipBadge, Member, PermissionSetup, SetupPermission, WikiPage
from membership.permission_setup import SetupState, spec_for
from membership.services.provisioning import provision_user_for_member
from tests.billing.factories import PaymentRefundFactory
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db

Cap = AdminCapability.Capability
SEED = importlib.import_module("membership.migrations.0282_seed_grant_guides")
#: A one pixel GIF, enough for an ImageField.
PIXEL = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def _member(name: str = "Odalys Fennimore", **fields: object) -> Member:
    member = MemberFactory(full_legal_name=name, **fields)
    provision_user_for_member(member)
    member.refresh_from_db()
    return member


def _teacher() -> Member:
    member = _member()
    member.grant_instructor(granted_by=None)
    member.refresh_from_db()
    return member


def _reviewer(guild=None) -> tuple[Member, object]:
    member = _member()
    guild = guild or GuildFactory(name="Quillmark Bindery Guild")
    GuildStaffMembershipFactory(guild=guild, member=member, can_review_classes=True)
    return member, guild


def _finish(member: Member, permission: str) -> None:
    """Every step done: reading steps pressed, the rest by the work each reads."""
    row = PermissionSetup.for_member(member, permission)
    for step in spec_for(permission).steps:
        if step.got_it:
            row.mark_read(step.key)
    state = SetupState.load(member, permission)
    assert state is not None and state.finished, [(v.step.key, v.done) for v in state.steps]


def _has_badge(member: Member, permission: str) -> bool:
    return LeadershipBadge.objects.filter(permission=permission, members=member).exists()


def describe_the_choices():
    def it_holds_every_capability_with_its_label_and_the_three_grants():
        for value, label in Cap.choices:
            assert SetupPermission(value).label == label
        assert {SetupPermission.INSTRUCTOR, SetupPermission.ADMIN_ROLE, SetupPermission.CLASS_REVIEWER} == {
            AdminCapability.INSTRUCTOR,
            AdminCapability.ADMIN_ROLE,
            AdminCapability.CLASS_REVIEWER,
        }

    def it_has_a_card_for_every_permission():
        assert all(spec_for(value) is not None for value in SetupPermission.values)


def describe_the_no_page_cards():
    @pytest.mark.parametrize("setup", [cs.DISCOUNT_ADMIN_SETUP, cs.REFUNDS_SETUP, cs.WEBMASTER_SETUP])
    def it_lives_on_notification_settings_and_finishes_on_got_it(setup):
        assert setup.page_url == f"{reverse('hub_user_settings')}?tab=notifications"
        member = _member()
        member.admin_capabilities.create(capability=setup.permission)
        _finish(member, setup.permission)
        assert _has_badge(member, setup.permission)

    def it_ticks_a_discount_request_the_member_answered():
        member = _member()
        assert cs.discount_request_facts(member).done == frozenset()
        DiscountCodeRequestFactory(decided_by=member.user, status="approved")
        assert cs.discount_request_facts(member).done == {cs.FIRST_DECISION}

    def it_ticks_a_refund_the_member_sent():
        member = _member()
        PaymentRefundFactory()
        assert cs.refund_facts(member).done == frozenset()
        PaymentRefundFactory(initiated_by=member.user, status=PaymentRefund.Status.SUCCEEDED)
        assert cs.refund_facts(member).done == {cs.FIRST_REFUND}

    def it_ticks_nothing_for_a_member_with_no_account():
        member = MemberFactory()
        assert cs.discount_request_facts(member).done == frozenset()
        assert cs.refund_facts(member).done == frozenset()


def describe_instructor():
    def it_is_held_while_the_teaching_portal_is_open():
        assert SetupState.load(_member(), SetupPermission.INSTRUCTOR) is None
        state = SetupState.load(_teacher(), SetupPermission.INSTRUCTOR)
        assert state is not None and state.granter_name == "an admin"

    def it_ticks_profile_class_and_review_from_what_they_did():
        member = _teacher()
        assert gs.instructor_facts(member).done == frozenset()
        member.instructor_bio = "I teach chain mail."
        member.profile_photo = SimpleUploadedFile("odalys.gif", PIXEL, content_type="image/gif")
        member.save()
        draft = ClassOfferingFactory(created_by=member, status=ClassOffering.Status.DRAFT)
        assert gs.instructor_facts(member).done == {gs.PROFILE, gs.FIRST_CLASS}
        ClassApproval.objects.create(class_offering=draft, role=ClassApproval.Role.ADMIN)
        facts = gs.instructor_facts(member)
        assert facts.done == {gs.PROFILE, gs.FIRST_CLASS, gs.SUBMITTED}
        assert facts.stats[0].value == 0
        ClassOfferingFactory(created_by=member, status=ClassOffering.Status.PUBLISHED)
        assert gs.instructor_facts(member).stats[0].value == 1

    def it_needs_both_photo_and_bio():
        member = _teacher()
        member.instructor_bio = "  "
        member.profile_photo = SimpleUploadedFile("odalys.gif", PIXEL, content_type="image/gif")
        member.save()
        assert gs.PROFILE not in gs.instructor_facts(member).done

    def it_takes_the_badge_back_when_instructor_is_revoked():
        member = _teacher()
        PermissionSetup.for_member(member, SetupPermission.INSTRUCTOR).finish()
        assert _has_badge(member, SetupPermission.INSTRUCTOR)
        member.revoke_instructor(revoked_by=None)
        assert not _has_badge(member, SetupPermission.INSTRUCTOR)
        assert PermissionSetup.for_member(member, SetupPermission.INSTRUCTOR).completed_at is None


def describe_admin():
    def it_is_held_while_the_role_is_admin():
        assert SetupState.load(_member(), SetupPermission.ADMIN_ROLE) is None
        assert SetupState.load(_member(fog_role=Member.FogRole.ADMIN), SetupPermission.ADMIN_ROLE) is not None

    def it_ticks_the_duty_step_once_they_hold_any_duty():
        admin = _member(fog_role=Member.FogRole.ADMIN)
        assert gs.admin_facts(admin).done == frozenset()
        admin.admin_capabilities.create(capability=Cap.WEBMASTER)
        assert gs.admin_facts(admin).done == {gs.DUTY}
        _finish(admin, SetupPermission.ADMIN_ROLE)

    def it_takes_the_badge_back_when_the_role_changes_by_either_door():
        changer = _member(name="Ysolde Marchbank", fog_role=Member.FogRole.ADMIN)
        for demote in (
            lambda m: m.set_fog_role(Member.FogRole.MEMBER, changed_by=changer),
            lambda m: m.apply_admin_role(Member.FogRole.MEMBER),
        ):
            admin = _member(fog_role=Member.FogRole.ADMIN)
            PermissionSetup.for_member(admin, SetupPermission.ADMIN_ROLE).finish()
            demote(admin)
            assert not _has_badge(admin, SetupPermission.ADMIN_ROLE)

    def it_keeps_the_badge_on_a_save_that_stays_admin():
        admin = _member(fog_role=Member.FogRole.ADMIN)
        PermissionSetup.for_member(admin, SetupPermission.ADMIN_ROLE).finish()
        admin.apply_admin_role(Member.FogRole.ADMIN)
        assert _has_badge(admin, SetupPermission.ADMIN_ROLE)


def describe_class_reviewer():
    def it_is_held_while_any_guild_has_them_reviewing_and_names_the_lead():
        assert SetupState.load(_member(), SetupPermission.CLASS_REVIEWER) is None
        lead = _member(name="Corentin Vale")
        member, guild = _reviewer(GuildFactory(name="Quillmark Bindery Guild", guild_lead=lead))
        state = SetupState.load(member, SetupPermission.CLASS_REVIEWER)
        assert state is not None and state.granter_name == "Corentin Vale"
        assert state.page_url == reverse("hub_guild_edit", args=[guild.pk])

    def it_ticks_a_guild_side_review_they_decided():
        member, _guild = _reviewer()
        offering = ClassOfferingFactory()
        ClassApproval.objects.create(
            class_offering=offering, role=ClassApproval.Role.ADMIN, decided_by=member.user, decision="approved"
        )
        assert gs.class_reviewer_facts(member).done == frozenset()
        ClassApproval.objects.create(
            class_offering=offering, role=ClassApproval.Role.GUILD_LEAD, decided_by=member.user, decision="approved"
        )
        assert gs.class_reviewer_facts(member).done == {cs.FIRST_REVIEW}

    def it_takes_the_badge_back_only_when_no_guild_has_them_reviewing():
        member, first = _reviewer()
        second = GuildFactory(name="Tarnwell Glass Guild")
        GuildStaffMembershipFactory(guild=second, member=member, can_review_classes=True)
        PermissionSetup.for_member(member, SetupPermission.CLASS_REVIEWER).finish()
        first.set_class_reviewer(member, False)
        assert _has_badge(member, SetupPermission.CLASS_REVIEWER)
        second.remove_staff(second.staff_memberships.get(member=member))
        assert not _has_badge(member, SetupPermission.CLASS_REVIEWER)


def describe_the_seeded_guide_pages():
    def it_seeds_one_official_page_per_permission_covering_each_step_section():
        SEED.seed(django_apps, None)
        SEED.seed(django_apps, None)
        for permission, title, body in SEED.PAGES:
            page = WikiPage.guide_page_for(permission)
            assert page is not None and page.title == title and page.status == WikiPage.Status.OFFICIAL
            for step in spec_for(permission).steps:
                assert f"<h2>{step.guide_section}</h2>" in body, (permission, step.key)
        part_three = {*(spec.permission for spec in cs.CAPABILITY_SETUPS[7:]), *(s.permission for s in gs.GRANT_SETUPS)}
        assert {permission for permission, _t, _b in SEED.PAGES} == part_three


def describe_other_revoke_paths():
    def it_takes_all_three_grant_badges_back_when_the_account_is_deleted():
        from membership.services.account_deletion import delete_own_account

        member = _teacher()
        member.fog_role = Member.FogRole.ADMIN
        member.save(update_fields=["fog_role"])
        GuildStaffMembershipFactory(
            guild=GuildFactory(name="Quillmark Bindery Guild"), member=member, can_review_classes=True
        )
        grants = [SetupPermission.INSTRUCTOR, SetupPermission.ADMIN_ROLE, SetupPermission.CLASS_REVIEWER]
        for permission in grants:
            PermissionSetup.for_member(member, permission).finish()
        delete_own_account(member)
        for permission in grants:
            assert not _has_badge(member, permission), permission
            assert PermissionSetup.for_member(member, permission).completed_at is None

    def it_takes_the_admin_badge_back_when_the_command_demotes():
        from django.core.management import call_command

        admin = MemberFactory(full_legal_name="Halvard Pine", _pre_signup_email="halvard-1167@example.com")
        admin.fog_role = Member.FogRole.ADMIN
        admin.save(update_fields=["fog_role"])
        PermissionSetup.for_member(admin, SetupPermission.ADMIN_ROLE).finish()
        call_command("set_fog_role", "halvard-1167@example.com", Member.FogRole.MEMBER)
        assert not _has_badge(admin, SetupPermission.ADMIN_ROLE)


def describe_admin_copy():
    def it_never_says_an_admin_hears_nothing_they_did_not_turn_on():
        """FOG_ADMINS and REFUND_AUTHORITY reach every admin with no capability (#1167 review)."""
        step = gs.ADMIN_SETUP.step(cs.LEARN)
        seeded = {permission: body for permission, _title, body in SEED.PAGES}[SetupPermission.ADMIN_ROLE]
        for text in (*step.how, seeded):
            assert "only once you turn it on" not in text
        for text in (" ".join(step.how), seeded):
            assert "A few notices reach every admin whatever you turn on" in text
            assert "a paid class an instructor cancels" in text
