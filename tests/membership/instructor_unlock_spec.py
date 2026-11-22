"""BDD specs for the teaching unlock and becoming an instructor by self serve (#1295).

Covers the ``Member`` unlock field/property, ``grant_teaching`` / ``revoke_teaching``,
``become_instructor`` (the confirm at the end of Host a Class's tutorial), the scoped
``apply_admin_role`` promotion hook (including the revoked-instructor loophole), and the
0110 backfill migration's forward/reverse querysets, exercised through the migration
module's own functions via ``apps.get_model``.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.core import mail
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


def _granted_emails() -> list:
    return [email for email in mail.outbox if email.subject.startswith("You have been granted")]


def describe_Member_can_create_classes():
    def it_is_false_while_the_field_is_null():
        member = MemberFactory()
        assert member.can_create_classes is False

    def it_is_true_once_the_field_is_set():
        member = MemberFactory(instructor_oriented_at=timezone.now())
        assert member.can_create_classes is True


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

    def it_writes_none_of_the_retired_application_columns():
        # The application's columns stay one release, read and written by nothing (#1295).
        member = _linked_member("grant-retired-columns")
        member.grant_teaching(granted_by=None)
        member.refresh_from_db()
        assert (member.teaching_decided_at, member.teaching_decline_reason) == (None, "")


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

    def it_leaves_the_retired_application_columns_as_they_were():
        admin = _linked_member("revoke-applicant")
        stamp = timezone.now()
        member = MemberFactory(instructor_oriented_at=stamp, teaching_applied_at=stamp)
        member.revoke_teaching(revoked_by=admin)
        member.refresh_from_db()
        assert member.teaching_applied_at == stamp

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


def describe_become_instructor():
    def it_turns_teaching_on_with_the_member_as_granter(django_capture_on_commit_callbacks):
        member = _linked_member("become-wrenna")
        with django_capture_on_commit_callbacks(execute=True):
            assert member.become_instructor() is True
        assert member.can_create_classes  # the instance the caller holds reads the grant too
        member.refresh_from_db()
        assert member.instructor_oriented_at is not None
        assert member.instructor_slug  # the public instructor page, as an admin's grant gives
        granted = SiteActivity.objects.get(kind=SiteActivity.Kind.TEACHING_GRANTED)
        assert granted.actor == member.user

    def it_sends_the_grant_email_once_worded_as_their_own_doing(django_capture_on_commit_callbacks):
        member = _linked_member("become-emailed")
        with django_capture_on_commit_callbacks(execute=True):
            member.become_instructor()
        with django_capture_on_commit_callbacks(execute=True):
            member.become_instructor()
        [email] = _granted_emails()
        assert email.subject == "You have been granted Instructor"
        assert email.to == [member.primary_email]
        assert "You turned this on yourself" in email.body

    def it_logs_made_themselves_an_instructor_and_tells_no_admin(django_capture_on_commit_callbacks):
        admin = _linked_member("become-admin")
        admin.fog_role = Member.FogRole.ADMIN
        admin.save(update_fields=["fog_role"])
        AdminCapability.objects.create(member=admin, capability=AdminCapability.Capability.CLASS_APPROVER)
        member = _linked_member("become-logged")
        with django_capture_on_commit_callbacks(execute=True):
            member.become_instructor()
        [row] = SiteActivity.objects.filter(kind=SiteActivity.Kind.INSTRUCTOR_SELF_SERVE)
        assert (row.actor, row.target) == (member.user, member)
        assert row.line.headline == "Made themselves an Instructor"
        assert not Notification.objects.filter(user=admin.user).exists()
        assert all(email.to == [member.primary_email] for email in mail.outbox)

    def it_changes_nothing_for_a_member_who_can_already_teach(django_capture_on_commit_callbacks):
        stamp = timezone.now()
        member = MemberFactory(instructor_oriented_at=stamp)
        stale = Member.objects.get(pk=member.pk)
        stale.instructor_oriented_at = None  # a page loaded before an admin's grant landed
        with django_capture_on_commit_callbacks(execute=True):
            assert stale.become_instructor() is False
        assert stale.instructor_oriented_at == stamp
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.INSTRUCTOR_SELF_SERVE).exists()
        assert _granted_emails() == []

    @pytest.mark.parametrize("status", [Member.Status.GUEST, Member.Status.FORMER, Member.Status.INVITED])
    def it_refuses_a_member_who_is_not_active(status):
        member = MemberFactory(status=status)
        with pytest.raises(ValueError, match="not active"):
            member.become_instructor()
        member.refresh_from_db()
        assert member.instructor_oriented_at is None

    def it_lets_a_member_whose_teaching_was_turned_off_turn_it_on_again(django_capture_on_commit_callbacks):
        admin = _linked_member("become-revoker")
        member = _linked_member("become-again")
        member.become_instructor()
        member.revoke_instructor(revoked_by=admin)
        member.refresh_from_db()
        with django_capture_on_commit_callbacks(execute=True):
            assert member.become_instructor() is True
        member.refresh_from_db()
        assert member.can_create_classes
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.INSTRUCTOR_SELF_SERVE).count() == 2
