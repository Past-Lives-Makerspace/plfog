"""BDD specs for the instructor-orientation teaching unlock (Spec D §5/§4).

Covers the ``Member`` unlock field/property/methods, the scoped
``apply_admin_role`` promotion hook (including the revoked-instructor loophole),
and the 0110 backfill migration's forward/reverse querysets — exercised through
the migration module's own functions via ``apps.get_model``.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.utils import timezone

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering, Registration
from core.models import SiteActivity, TourState
from membership.models import Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

_backfill = importlib.import_module("membership.migrations.0110_backfill_instructor_oriented")


def _linked_member(username: str) -> Member:
    """An ACTIVE member with a real linked User (via the auto-provision signal)."""
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pw")
    return Member.objects.get(user=user)


def describe_Member_can_create_classes():
    def it_is_false_while_the_field_is_null():
        member = MemberFactory()
        assert member.can_create_classes is False

    def it_is_true_once_the_field_is_set():
        member = MemberFactory(instructor_oriented_at=timezone.now())
        assert member.can_create_classes is True


def describe_complete_instructor_orientation():
    def it_sets_the_timestamp_and_logs_the_activity():
        member = _linked_member("orient-done")
        member.complete_instructor_orientation()
        member.refresh_from_db()
        assert member.instructor_oriented_at is not None
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.INSTRUCTOR_ORIENTED)
        assert row.actor == member.user

    def it_writes_no_tour_state_row():
        member = _linked_member("orient-notour")
        member.complete_instructor_orientation()
        assert TourState.objects.count() == 0

    def it_is_idempotent_on_a_double_submit():
        member = _linked_member("orient-twice")
        member.complete_instructor_orientation()
        first_stamp = Member.objects.get(pk=member.pk).instructor_oriented_at
        member.complete_instructor_orientation()
        member.refresh_from_db()
        assert member.instructor_oriented_at == first_stamp
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.INSTRUCTOR_ORIENTED).count() == 1

    def it_raises_for_an_inactive_member():
        member = MemberFactory(status=Member.Status.FORMER)
        with pytest.raises(ValueError):
            member.complete_instructor_orientation()
        member.refresh_from_db()
        assert member.instructor_oriented_at is None


def describe_grant_teaching():
    def it_sets_the_timestamp_and_logs_with_the_admin_actor():
        admin = _linked_member("grant-admin")
        member = MemberFactory()
        member.grant_teaching(granted_by=admin)
        member.refresh_from_db()
        assert member.instructor_oriented_at is not None
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.TEACHING_GRANTED)
        assert row.actor == admin.user

    def it_is_a_no_op_when_already_unlocked():
        admin = _linked_member("grant-admin2")
        original = timezone.now()
        member = MemberFactory(instructor_oriented_at=original)
        member.grant_teaching(granted_by=admin)
        member.refresh_from_db()
        assert member.instructor_oriented_at == original
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.TEACHING_GRANTED).count() == 0


def describe_revoke_teaching():
    def it_clears_the_timestamp_and_logs_with_the_admin_actor():
        admin = _linked_member("revoke-admin")
        member = MemberFactory(instructor_oriented_at=timezone.now())
        member.revoke_teaching(revoked_by=admin)
        member.refresh_from_db()
        assert member.instructor_oriented_at is None
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.TEACHING_REVOKED)
        assert row.actor == admin.user

    def it_is_a_no_op_with_no_log_when_already_locked():
        admin = _linked_member("revoke-admin2")
        member = MemberFactory()
        member.revoke_teaching(revoked_by=admin)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.TEACHING_REVOKED).count() == 0

    def it_leaves_the_members_existing_classes_untouched():
        admin = _linked_member("revoke-admin3")
        member = MemberFactory(instructor_oriented_at=timezone.now())
        offering = ClassOfferingFactory(instructor=member, status=ClassOffering.Status.PUBLISHED)
        member.revoke_teaching(revoked_by=admin)
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED
        assert offering.instructor_id == member.pk
        assert Registration.objects.filter(class_offering=offering).count() == 0  # nothing deleted or altered


def describe_apply_admin_role():
    def it_unlocks_teaching_on_a_first_time_instructor_promotion():
        member = MemberFactory(full_legal_name="Fresh Teacher")
        member.apply_admin_role("instructor")
        member.refresh_from_db()
        assert member.instructor_slug
        assert member.instructor_oriented_at is not None
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.TEACHING_GRANTED)
        assert row.actor is None
        assert row.payload == {"via": "instructor_promotion"}

    def it_does_not_regrant_a_revoked_instructor_on_a_routine_member_edit_save():
        # The loophole walk: promote → revoke → the plain member-edit save path.
        # MemberAdminEditForm._derive_initial_role pre-fills "Instructor" for any
        # slug holder, and admin_member_edit re-applies the role on EVERY save —
        # so an unscoped hook would silently re-grant here. It must not.
        admin = _linked_member("loophole-admin")
        member = MemberFactory(full_legal_name="Revoked Teacher")
        member.apply_admin_role("instructor")
        member.refresh_from_db()
        member.revoke_teaching(revoked_by=admin)
        member.refresh_from_db()
        assert member.instructor_slug  # revoke does NOT clear the public-page slug

        member.apply_admin_role("instructor")  # what admin_member_edit does on any save
        member.refresh_from_db()
        assert member.instructor_oriented_at is None
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.TEACHING_GRANTED).count() == 1

    def it_keeps_the_unlock_when_demoting_an_instructor():
        member = MemberFactory(full_legal_name="Demoted Teacher")
        member.apply_admin_role("instructor")
        member.refresh_from_db()
        stamp = member.instructor_oriented_at
        member.apply_admin_role("member")
        member.refresh_from_db()
        assert member.instructor_oriented_at == stamp


def describe_backfill_migration():
    def it_unlocks_a_member_with_a_draft_offering():
        member = MemberFactory()
        ClassOfferingFactory(instructor=member, status=ClassOffering.Status.DRAFT)
        _backfill._unlock_grandfathered(django_apps, None)
        member.refresh_from_db()
        assert member.instructor_oriented_at is not None

    def it_unlocks_a_member_with_only_an_archived_offering():
        member = MemberFactory()
        ClassOfferingFactory(instructor=member, status=ClassOffering.Status.ARCHIVED)
        _backfill._unlock_grandfathered(django_apps, None)
        member.refresh_from_db()
        assert member.instructor_oriented_at is not None

    def it_unlocks_a_slug_holder_with_zero_offerings():
        member = MemberFactory(instructor_slug="sluggy-no-classes")
        _backfill._unlock_grandfathered(django_apps, None)
        member.refresh_from_db()
        assert member.instructor_oriented_at is not None

    def it_leaves_a_plain_member_locked():
        member = MemberFactory()
        _backfill._unlock_grandfathered(django_apps, None)
        member.refresh_from_db()
        assert member.instructor_oriented_at is None

    def it_never_stomps_an_existing_timestamp():
        original = timezone.now()
        member = MemberFactory(instructor_oriented_at=original)
        ClassOfferingFactory(instructor=member)
        _backfill._unlock_grandfathered(django_apps, None)
        member.refresh_from_db()
        assert member.instructor_oriented_at == original

    def it_reverse_clears_exactly_the_base_predicate_members():
        # One grandfathered-by-offering, one unlocked by other means WITH a slug
        # (the documented over-clear), one plain unlocked member (kept).
        by_offering = MemberFactory()
        ClassOfferingFactory(instructor=by_offering)
        slugged_by_other_means = MemberFactory(instructor_slug="other-means", instructor_oriented_at=timezone.now())
        plain_unlocked = MemberFactory(instructor_oriented_at=timezone.now())

        _backfill._unlock_grandfathered(django_apps, None)
        _backfill._relock_grandfathered(django_apps, None)

        by_offering.refresh_from_db()
        slugged_by_other_means.refresh_from_db()
        plain_unlocked.refresh_from_db()
        assert by_offering.instructor_oriented_at is None
        assert slugged_by_other_means.instructor_oriented_at is None  # the honest over-clear
        assert plain_unlocked.instructor_oriented_at is not None  # not in the base predicate
