"""BDD specs for Member.ensure_instructor_slug and the two admin paths that share it."""

from __future__ import annotations

import pytest
from django.utils import timezone

from classes.factories import ClassOfferingFactory, UserFactory
from classes.models import ClassOffering
from core.models import SiteActivity
from membership.models import Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def describe_ensure_instructor_slug():
    def it_mints_a_slug_from_the_display_name_and_reports_it():
        member = MemberFactory(full_legal_name="Robin Vale", preferred_name="")
        assert member.ensure_instructor_slug() is True
        member.refresh_from_db()
        assert member.instructor_slug == "robin-vale"

    def it_suffixes_until_unique():
        MemberFactory(full_legal_name="Taken Name", instructor_slug="taken-name")
        MemberFactory(full_legal_name="Taken Name", instructor_slug="taken-name-2")
        member = MemberFactory(full_legal_name="Taken Name")
        member.ensure_instructor_slug()
        assert member.instructor_slug == "taken-name-3"

    def it_is_idempotent_and_keeps_an_existing_slug():
        member = MemberFactory(full_legal_name="Kept Name", instructor_slug="custom-slug")
        assert member.ensure_instructor_slug() is False
        member.refresh_from_db()
        assert member.instructor_slug == "custom-slug"

    def it_never_touches_the_teaching_unlock():
        member = MemberFactory(full_legal_name="Locked Person", instructor_oriented_at=None)
        member.ensure_instructor_slug()
        member.refresh_from_db()
        assert member.instructor_oriented_at is None

    def it_falls_back_to_the_pk_when_the_name_has_no_slug():
        member = MemberFactory(full_legal_name="???", preferred_name="")
        member.ensure_instructor_slug()
        assert member.instructor_slug == f"instructor-{member.pk}"


def describe_minting_on_first_publish():
    def it_mints_when_the_instructor_can_create_classes(admin_user):
        MembershipPlanFactory()
        user = UserFactory(username="first-publish@example.com")
        member = Member.objects.get(user=user)
        member.full_legal_name = "First Publisher"
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["full_legal_name", "instructor_oriented_at"])
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.PENDING, instructor=member)
        offering.publish(admin_user)
        member.refresh_from_db()
        assert member.instructor_slug == "first-publisher"

    def it_never_mints_for_a_revoked_instructor(admin_user):
        MembershipPlanFactory()
        user = UserFactory(username="revoked@example.com")
        member = Member.objects.get(user=user)
        member.instructor_oriented_at = None
        member.save(update_fields=["instructor_oriented_at"])
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.PENDING, instructor=member)
        offering.publish(admin_user)
        member.refresh_from_db()
        assert member.instructor_slug == ""
        assert member.instructor_oriented_at is None

    def it_keeps_an_existing_slug_on_a_second_publish(admin_user):
        MembershipPlanFactory()
        user = UserFactory(username="second-publish@example.com")
        member = Member.objects.get(user=user)
        member.instructor_oriented_at = timezone.now()
        member.instructor_slug = "already-here"
        member.save(update_fields=["instructor_oriented_at", "instructor_slug"])
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.PENDING, instructor=member)
        offering.publish(admin_user)
        member.refresh_from_db()
        assert member.instructor_slug == "already-here"

    def it_publishes_an_instructorless_class_without_error(admin_user):
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.PENDING, instructor=None)
        offering.publish(admin_user)
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED


def describe_admin_paths_still_mint_through_the_shared_helper():
    def it_grant_instructor_mints_and_unlocks_exactly_as_before():
        granter = MemberFactory(full_legal_name="Granter")
        member = MemberFactory(full_legal_name="New Teacher", instructor_oriented_at=None)
        member.grant_instructor(granted_by=granter)
        member.refresh_from_db()
        assert member.instructor_slug == "new-teacher"
        assert member.instructor_oriented_at is not None
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.TEACHING_GRANTED).count() == 1

    def it_grant_instructor_logs_the_page_going_live_for_an_already_teaching_member():
        member = MemberFactory(full_legal_name="Already Teaching", instructor_oriented_at=timezone.now())
        member.grant_instructor(granted_by=None)
        member.refresh_from_db()
        assert member.instructor_slug == "already-teaching"
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.TEACHING_GRANTED).count() == 1

    def it_apply_admin_role_mints_and_unlocks_on_first_promotion():
        member = MemberFactory(full_legal_name="Promoted Person", instructor_oriented_at=None)
        member.apply_admin_role(Member.ADMIN_ROLE_INSTRUCTOR)
        member.refresh_from_db()
        assert member.instructor_slug == "promoted-person"
        assert member.instructor_oriented_at is not None
        assert member.status == Member.Status.ACTIVE

    def it_apply_admin_role_does_not_regrant_a_revoked_instructor_on_a_routine_edit():
        member = MemberFactory(
            full_legal_name="Revoked Person", instructor_slug="revoked-person", instructor_oriented_at=None
        )
        member.apply_admin_role(Member.ADMIN_ROLE_INSTRUCTOR)
        member.refresh_from_db()
        assert member.instructor_slug == "revoked-person"
        assert member.instructor_oriented_at is None
