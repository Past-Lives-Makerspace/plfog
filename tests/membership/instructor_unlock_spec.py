"""BDD specs for the teaching unlock and the apply-to-teach application.

Covers the ``Member`` unlock field/property, the derived
``teaching_application_state`` in every branch, ``apply_to_teach`` /
``decline_teaching`` / ``grant_teaching`` / ``revoke_teaching``, the scoped
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
from core.models import Notification, SiteActivity
from membership.models import AdminCapability, Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

_backfill = importlib.import_module("membership.migrations.0110_backfill_instructor_oriented")


def _linked_member(username: str) -> Member:
    """An ACTIVE member with a real linked User (via the auto-provision signal)."""
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pw")
    return Member.objects.get(user=user)


def _fog_admin(username: str) -> Member:
    """A linked member holding CLASS_APPROVER — who instructor_application_received resolves to.

    The capability, not the admin tier, is the audience: a plain admin without the CMS
    Administrator duty hears nothing (see ``core.events.resolvers._capability_recipients``).
    The member is also given the ADMIN role so the fixture still stands in for the person
    who acts on the queue, which is still admin-gated.
    """
    member = _linked_member(username)
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    AdminCapability.objects.create(member=member, capability=AdminCapability.Capability.CLASS_APPROVER)
    return member


def describe_Member_can_create_classes():
    def it_is_false_while_the_field_is_null():
        member = MemberFactory()
        assert member.can_create_classes is False

    def it_is_true_once_the_field_is_set():
        member = MemberFactory(instructor_oriented_at=timezone.now())
        assert member.can_create_classes is True


def describe_teaching_application_state():
    def it_reads_none_for_a_member_who_never_asked():
        member = MemberFactory()
        assert member.teaching_application_state == Member.TeachingApplicationState.NONE

    def it_reads_pending_once_they_apply():
        member = _linked_member("state-pending")
        member.apply_to_teach("Wheel throwing.")
        assert member.teaching_application_state == Member.TeachingApplicationState.PENDING

    def it_reads_declined_once_an_admin_says_no():
        member = _linked_member("state-declined")
        member.apply_to_teach("Wheel throwing.")
        member.decline_teaching(decided_by=None, reason="Do the orientation first.")
        assert member.teaching_application_state == Member.TeachingApplicationState.DECLINED

    def it_reads_approved_once_teaching_is_granted():
        member = _linked_member("state-approved")
        member.apply_to_teach("Wheel throwing.")
        member.grant_teaching(granted_by=None)
        assert member.teaching_application_state == Member.TeachingApplicationState.APPROVED

    def it_reads_approved_for_a_grandfathered_instructor_who_never_applied():
        # The whole reason can_create_classes resolves first: every instructor granted
        # before applications existed carries no application row at all.
        member = MemberFactory(instructor_oriented_at=timezone.now())
        assert member.teaching_applied_at is None
        assert member.teaching_application_state == Member.TeachingApplicationState.APPROVED

    def it_stays_pending_when_a_decision_stamp_carries_no_reason():
        # A half-written decline (stamp, no reason) must never mask a live application.
        member = _linked_member("state-halfdecline")
        member.apply_to_teach("Wheel throwing.")
        member.teaching_decided_at = timezone.now()
        member.save(update_fields=["teaching_decided_at"])
        assert member.teaching_application_state == Member.TeachingApplicationState.PENDING

    def it_stays_pending_when_a_reason_carries_no_decision_stamp():
        member = _linked_member("state-halfreason")
        member.apply_to_teach("Wheel throwing.")
        member.teaching_decline_reason = "Not yet."
        member.save(update_fields=["teaching_decline_reason"])
        assert member.teaching_application_state == Member.TeachingApplicationState.PENDING


def describe_apply_to_teach():
    def it_stamps_the_note_logs_the_activity_and_notifies_the_admins():
        _fog_admin("apply-admin")
        member = _linked_member("apply-ok")
        member.apply_to_teach("  Intro to wheel throwing.  ")
        member.refresh_from_db()
        assert member.teaching_applied_at is not None
        assert member.teaching_application_note == "Intro to wheel throwing."
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.TEACHING_APPLIED)
        assert row.actor == member.user
        assert Notification.objects.filter(trigger="instructor_application_received").count() == 1

    def it_raises_on_a_blank_note():
        member = _linked_member("apply-blank")
        with pytest.raises(ValueError):
            member.apply_to_teach("   ")
        member.refresh_from_db()
        assert member.teaching_applied_at is None

    def it_raises_for_an_inactive_member():
        member = MemberFactory(status=Member.Status.FORMER)
        with pytest.raises(ValueError):
            member.apply_to_teach("Let me in.")
        member.refresh_from_db()
        assert member.teaching_applied_at is None

    def it_raises_for_a_member_who_can_already_teach():
        """The page hides the button from instructors; this is the crafted POST backstop."""
        _fog_admin("apply-admin-instructor")
        member = _linked_member("apply-instructor")
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["instructor_oriented_at"])
        with pytest.raises(ValueError):
            member.apply_to_teach("Let me apply again.")
        member.refresh_from_db()
        assert member.teaching_applied_at is None
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.TEACHING_APPLIED).exists()
        assert Notification.objects.filter(trigger="instructor_application_received").count() == 0

    def it_raises_rather_than_overwriting_a_pending_application():
        member = _linked_member("apply-twice")
        member.apply_to_teach("First ask.")
        first_stamp = Member.objects.get(pk=member.pk).teaching_applied_at
        with pytest.raises(ValueError):
            member.apply_to_teach("Second ask.")
        member.refresh_from_db()
        assert member.teaching_applied_at == first_stamp
        assert member.teaching_application_note == "First ask."

    def it_clears_a_previous_decline_so_a_re_application_is_clean():
        member = _linked_member("apply-again")
        member.apply_to_teach("First ask.")
        member.decline_teaching(decided_by=None, reason="Not yet.")
        member.apply_to_teach("Second ask, with the orientation done.")
        member.refresh_from_db()
        assert member.teaching_decline_reason == ""
        assert member.teaching_decided_at is None
        assert member.teaching_application_state == Member.TeachingApplicationState.PENDING


def describe_decline_teaching():
    def it_stamps_the_reason_logs_the_activity_and_emails_the_member():
        admin = _linked_member("decline-admin")
        member = _linked_member("decline-ok")
        member.apply_to_teach("Wheel throwing.")
        member.decline_teaching(decided_by=admin, reason="  Do the wheel orientation first.  ")
        member.refresh_from_db()
        assert member.teaching_decline_reason == "Do the wheel orientation first."
        assert member.teaching_decided_at is not None
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.TEACHING_APPLICATION_DECLINED)
        assert row.actor == admin.user
        assert Notification.objects.filter(trigger="instructor_application_declined", user=member.user).count() == 1

    def it_raises_on_a_blank_reason():
        member = _linked_member("decline-blank")
        member.apply_to_teach("Wheel throwing.")
        with pytest.raises(ValueError):
            member.decline_teaching(decided_by=None, reason="   ")
        member.refresh_from_db()
        assert member.teaching_decided_at is None
        assert member.teaching_application_state == Member.TeachingApplicationState.PENDING

    def it_attributes_the_activity_to_the_system_for_a_superuser_with_no_member():
        member = _linked_member("decline-system")
        member.apply_to_teach("Wheel throwing.")
        member.decline_teaching(decided_by=None, reason="Not yet.")
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.TEACHING_APPLICATION_DECLINED)
        assert row.actor is None

    def it_raises_when_nobody_applied():
        member = _linked_member("decline-unasked")
        with pytest.raises(ValueError):
            member.decline_teaching(decided_by=None, reason="No.")
        member.refresh_from_db()
        assert member.teaching_decided_at is None
        assert Notification.objects.filter(trigger="instructor_application_declined").count() == 0

    def it_raises_for_a_member_who_can_already_teach():
        member = _linked_member("decline-instructor")
        member.apply_to_teach("Wheel throwing.")
        member.grant_teaching(granted_by=None)
        with pytest.raises(ValueError):
            member.decline_teaching(decided_by=None, reason="Changed my mind.")

    def it_truncates_an_over_long_reason_to_the_field_width():
        member = _linked_member("decline-long")
        member.apply_to_teach("Wheel throwing.")
        member.decline_teaching(decided_by=None, reason="x" * 400)
        member.refresh_from_db()
        assert len(member.teaching_decline_reason) == 300


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

    def it_emails_the_applicant_when_answering_a_real_application():
        member = _linked_member("grant-applied")
        member.apply_to_teach("Wheel throwing.")
        member.grant_teaching(granted_by=None)
        member.refresh_from_db()
        assert member.teaching_decided_at is not None
        assert Notification.objects.filter(trigger="instructor_application_approved", user=member.user).count() == 1

    def it_sends_nothing_to_someone_who_never_applied():
        member = _linked_member("grant-unasked")
        member.grant_teaching(granted_by=None)
        assert Notification.objects.filter(trigger="instructor_application_approved").count() == 0

    def it_clears_a_previous_decline():
        member = _linked_member("grant-after-decline")
        member.apply_to_teach("Wheel throwing.")
        member.decline_teaching(decided_by=None, reason="Not yet.")
        member.grant_teaching(granted_by=None)
        member.refresh_from_db()
        assert member.teaching_decline_reason == ""
        assert member.teaching_application_state == Member.TeachingApplicationState.APPROVED


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

    def it_clears_the_application_stamp_so_they_can_ask_again():
        # Without this they would sit forever on "your application is in" for an
        # application that was already answered.
        admin = _linked_member("revoke-applicant")
        member = _linked_member("revoke-applied")
        member.apply_to_teach("Wheel throwing.")
        member.grant_teaching(granted_by=admin)
        member.revoke_teaching(revoked_by=admin)
        member.refresh_from_db()
        assert member.teaching_applied_at is None
        assert member.teaching_application_note == "Wheel throwing."  # kept as history
        assert member.teaching_application_state == Member.TeachingApplicationState.NONE

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
