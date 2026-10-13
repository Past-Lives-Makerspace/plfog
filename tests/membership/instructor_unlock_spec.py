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
        member.apply_to_teach("Wheel throwing.", contact_method="email", contact_detail="reach@example.com")
        assert member.teaching_application_state == Member.TeachingApplicationState.PENDING

    def it_reads_declined_once_an_admin_says_no():
        member = _linked_member("state-declined")
        member.apply_to_teach("Wheel throwing.", contact_method="email", contact_detail="reach@example.com")
        member.decline_teaching(decided_by=None, reason="Do the orientation first.")
        assert member.teaching_application_state == Member.TeachingApplicationState.DECLINED

    def it_reads_approved_once_teaching_is_granted():
        member = _linked_member("state-approved")
        member.apply_to_teach("Wheel throwing.", contact_method="email", contact_detail="reach@example.com")
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
        member.apply_to_teach("Wheel throwing.", contact_method="email", contact_detail="reach@example.com")
        member.teaching_decided_at = timezone.now()
        member.save(update_fields=["teaching_decided_at"])
        assert member.teaching_application_state == Member.TeachingApplicationState.PENDING

    def it_stays_pending_when_a_reason_carries_no_decision_stamp():
        member = _linked_member("state-halfreason")
        member.apply_to_teach("Wheel throwing.", contact_method="email", contact_detail="reach@example.com")
        member.teaching_decline_reason = "Not yet."
        member.save(update_fields=["teaching_decline_reason"])
        assert member.teaching_application_state == Member.TeachingApplicationState.PENDING


def describe_apply_to_teach():
    def it_stamps_the_note_logs_the_activity_and_notifies_the_admins():
        _fog_admin("apply-admin")
        member = _linked_member("apply-ok")
        member.apply_to_teach(
            "  Intro to wheel throwing.  ", contact_method="email", contact_detail="reach@example.com"
        )
        member.refresh_from_db()
        assert member.teaching_applied_at is not None
        assert member.teaching_application_note == "Intro to wheel throwing."
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.TEACHING_APPLIED)
        assert row.actor == member.user
        assert Notification.objects.filter(trigger="instructor_application_received").count() == 1

    def it_raises_on_a_blank_note():
        member = _linked_member("apply-blank")
        with pytest.raises(ValueError):
            member.apply_to_teach("   ", contact_method="email", contact_detail="reach@example.com")
        member.refresh_from_db()
        assert member.teaching_applied_at is None

    def it_raises_for_an_inactive_member():
        member = MemberFactory(status=Member.Status.FORMER)
        with pytest.raises(ValueError):
            member.apply_to_teach("Let me in.", contact_method="email", contact_detail="reach@example.com")
        member.refresh_from_db()
        assert member.teaching_applied_at is None

    def it_raises_for_a_member_who_can_already_teach():
        """The page hides the button from instructors; this is the crafted POST backstop."""
        _fog_admin("apply-admin-instructor")
        member = _linked_member("apply-instructor")
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["instructor_oriented_at"])
        with pytest.raises(ValueError):
            member.apply_to_teach("Let me apply again.", contact_method="email", contact_detail="reach@example.com")
        member.refresh_from_db()
        assert member.teaching_applied_at is None
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.TEACHING_APPLIED).exists()
        assert Notification.objects.filter(trigger="instructor_application_received").count() == 0

    def it_raises_rather_than_overwriting_a_pending_application():
        member = _linked_member("apply-twice")
        member.apply_to_teach("First ask.", contact_method="email", contact_detail="reach@example.com")
        first_stamp = Member.objects.get(pk=member.pk).teaching_applied_at
        with pytest.raises(ValueError):
            member.apply_to_teach("Second ask.", contact_method="email", contact_detail="reach@example.com")
        member.refresh_from_db()
        assert member.teaching_applied_at == first_stamp
        assert member.teaching_application_note == "First ask."

    def it_clears_a_previous_decline_so_a_re_application_is_clean():
        member = _linked_member("apply-again")
        member.apply_to_teach("First ask.", contact_method="email", contact_detail="reach@example.com")
        member.decline_teaching(decided_by=None, reason="Not yet.")
        member.apply_to_teach(
            "Second ask, with the orientation done.", contact_method="email", contact_detail="reach@example.com"
        )
        member.refresh_from_db()
        assert member.teaching_decline_reason == ""
        assert member.teaching_decided_at is None
        assert member.teaching_application_state == Member.TeachingApplicationState.PENDING

    def it_stores_how_to_reach_them_and_tells_the_admins():
        """Issue #536: the method and the detail are saved with the note and reach the admins."""
        admin = _fog_admin("apply-admin-contact")
        member = _linked_member("apply-contact")
        member.apply_to_teach("Wheel throwing.", contact_method="text", contact_detail="  503 555 0100  ")
        member.refresh_from_db()
        assert member.teaching_contact_method == Member.TeachingContactMethod.TEXT
        assert member.teaching_contact_detail == "503 555 0100"
        note = Notification.objects.get(trigger="instructor_application_received", user=admin.user)
        assert note.title == f"{member.display_name} wants to be an instructor"
        assert note.body.startswith(
            f"{member.display_name} wants to be an instructor. Contact them at 503 555 0100 (text message)."
        )
        assert '"Wheel throwing."' in note.body

    @pytest.mark.parametrize(
        ("method", "detail", "label"),
        [("email", "robin@example.com", "email"), ("phone", "503 555 0100", "phone call")],
    )
    def it_names_the_method_in_lower_case_prose_for_every_choice(method, detail, label):
        admin = _fog_admin(f"apply-admin-label-{method}")
        member = _linked_member(f"apply-label-{method}")
        member.apply_to_teach("Wheel throwing.", contact_method=method, contact_detail=detail)
        note = Notification.objects.get(trigger="instructor_application_received", user=admin.user)
        assert f"Contact them at {detail} ({label})." in note.body

    def it_raises_on_a_blank_contact_method():
        member = _linked_member("apply-no-method")
        with pytest.raises(ValueError):
            member.apply_to_teach("Wheel throwing.", contact_method="", contact_detail="503 555 0100")
        member.refresh_from_db()
        assert member.teaching_applied_at is None
        assert member.teaching_contact_method == ""

    def it_raises_on_an_unknown_contact_method():
        member = _linked_member("apply-fax")
        with pytest.raises(ValueError):
            member.apply_to_teach("Wheel throwing.", contact_method="fax", contact_detail="503 555 0100")
        member.refresh_from_db()
        assert member.teaching_applied_at is None

    def it_raises_on_a_blank_contact_detail():
        member = _linked_member("apply-no-detail")
        with pytest.raises(ValueError):
            member.apply_to_teach("Wheel throwing.", contact_method="email", contact_detail="   ")
        member.refresh_from_db()
        assert member.teaching_applied_at is None
        assert member.teaching_contact_detail == ""

    def it_overwrites_the_previous_contact_pair_when_they_apply_again():
        member = _linked_member("apply-again-contact")
        member.apply_to_teach("First ask.", contact_method="email", contact_detail="first@example.com")
        member.decline_teaching(decided_by=None, reason="Not yet.")
        member.apply_to_teach("Second ask.", contact_method="phone", contact_detail="503 555 0100")
        member.refresh_from_db()
        assert member.teaching_contact_method == Member.TeachingContactMethod.PHONE
        assert member.teaching_contact_detail == "503 555 0100"


def describe_teaching_contact_method_label():
    def it_is_blank_for_an_application_filed_before_the_question_existed():
        member = MemberFactory()
        assert member.teaching_contact_method_label == ""

    @pytest.mark.parametrize(
        ("method", "label"),
        [("email", "email"), ("text", "text message"), ("phone", "phone call")],
    )
    def it_reads_the_choice_label_in_lower_case(method, label):
        member = MemberFactory(teaching_contact_method=method)
        assert member.teaching_contact_method_label == label


def describe_decline_teaching():
    def it_stamps_the_reason_logs_the_activity_and_emails_the_member():
        admin = _linked_member("decline-admin")
        member = _linked_member("decline-ok")
        member.apply_to_teach("Wheel throwing.", contact_method="email", contact_detail="reach@example.com")
        member.decline_teaching(decided_by=admin, reason="  Do the wheel orientation first.  ")
        member.refresh_from_db()
        assert member.teaching_decline_reason == "Do the wheel orientation first."
        assert member.teaching_decided_at is not None
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.TEACHING_APPLICATION_DECLINED)
        assert row.actor == admin.user
        assert Notification.objects.filter(trigger="instructor_application_declined", user=member.user).count() == 1

    def it_raises_on_a_blank_reason():
        member = _linked_member("decline-blank")
        member.apply_to_teach("Wheel throwing.", contact_method="email", contact_detail="reach@example.com")
        with pytest.raises(ValueError):
            member.decline_teaching(decided_by=None, reason="   ")
        member.refresh_from_db()
        assert member.teaching_decided_at is None
        assert member.teaching_application_state == Member.TeachingApplicationState.PENDING

    def it_attributes_the_activity_to_the_system_for_a_superuser_with_no_member():
        member = _linked_member("decline-system")
        member.apply_to_teach("Wheel throwing.", contact_method="email", contact_detail="reach@example.com")
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
        member.apply_to_teach("Wheel throwing.", contact_method="email", contact_detail="reach@example.com")
        member.grant_teaching(granted_by=None)
        with pytest.raises(ValueError):
            member.decline_teaching(decided_by=None, reason="Changed my mind.")

    def it_truncates_an_over_long_reason_to_the_field_width():
        member = _linked_member("decline-long")
        member.apply_to_teach("Wheel throwing.", contact_method="email", contact_detail="reach@example.com")
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
        member.apply_to_teach("Wheel throwing.", contact_method="email", contact_detail="reach@example.com")
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
        member.apply_to_teach("Wheel throwing.", contact_method="email", contact_detail="reach@example.com")
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
        member.apply_to_teach("Wheel throwing.", contact_method="email", contact_detail="reach@example.com")
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


def describe_apply_to_teach_inquiry_answers():
    """#690: website, socials and experience ride along with the application."""

    def it_stores_the_three_answers_trimmed():
        member = _linked_member("apply-answers")
        member.apply_to_teach(
            "Wheel throwing.",
            contact_method="email",
            contact_detail="reach@example.com",
            website=" https://robin.example ",
            socials=" @robinmakes ",
            experience="a_few",
        )
        member.refresh_from_db()
        assert member.teaching_website == "https://robin.example"
        assert member.teaching_socials == "@robinmakes"
        assert member.teaching_experience == Member.TeachingExperience.A_FEW

    def it_refuses_an_unknown_experience_level():
        member = _linked_member("apply-bad-level")
        with pytest.raises(ValueError):
            member.apply_to_teach(
                "Wheel throwing.", contact_method="email", contact_detail="reach@example.com", experience="guru"
            )
        member.refresh_from_db()
        assert member.teaching_applied_at is None

    def it_replaces_the_previous_answers_when_they_apply_again():
        member = _linked_member("apply-answers-again")
        member.apply_to_teach(
            "First ask.",
            contact_method="email",
            contact_detail="reach@example.com",
            website="https://old.example",
            socials="@old",
            experience="first_time",
        )
        member.decline_teaching(decided_by=None, reason="Not yet.")
        member.apply_to_teach("Second ask.", contact_method="email", contact_detail="reach@example.com")
        member.refresh_from_db()
        assert (member.teaching_website, member.teaching_socials, member.teaching_experience) == ("", "", "")


def describe_apply_to_teach_application_answers():
    """#793: the application page's answers are stored beside the rest, latest ask only."""

    def _apply_with_everything(member: Member) -> None:
        member.apply_to_teach(
            "Wheel throwing.",
            contact_method="email",
            contact_detail="reach@example.com",
            experience="a_few",
            class_title=" Wheel Throwing 101 ",
            class_format="series",
            shop_area=" Ceramics ",
            practice_years="3_to_10",
            taught_where=" The library. ",
            certifications=" Kiln safety. ",
            space_needs=" Four wheels. ",
            class_size="5_to_8",
            availability=["weekday_evenings", "weekends"],
            anything_else=" Thanks. ",
        )

    def it_stores_every_answer_trimmed():
        member = _linked_member("apply-everything")
        _apply_with_everything(member)
        member.refresh_from_db()
        assert member.teaching_class_title == "Wheel Throwing 101"
        assert member.teaching_class_format == Member.TeachingClassFormat.SERIES
        assert member.teaching_shop_area == "Ceramics"
        assert member.teaching_practice_years == Member.TeachingPracticeYears.THREE_TO_TEN
        assert member.teaching_taught_where == "The library."
        assert member.teaching_certifications == "Kiln safety."
        assert member.teaching_space_needs == "Four wheels."
        assert member.teaching_class_size == Member.TeachingClassSize.FIVE_TO_EIGHT
        assert member.teaching_availability == ["weekday_evenings", "weekends"]
        assert member.teaching_anything_else == "Thanks."
        assert member.teaching_availability_display == "Weekday evenings, Weekends"

    @pytest.mark.parametrize(
        ("answer", "value"),
        [
            ("class_format", "weekly"),
            ("practice_years", "forever"),
            ("class_size", "50"),
            ("availability", ["midnight"]),
        ],
    )
    def it_refuses_an_unknown_choice_and_files_nothing(answer, value):
        member = _linked_member(f"apply-bad-{answer}")
        with pytest.raises(ValueError):
            member.apply_to_teach(
                "Wheel throwing.", contact_method="email", contact_detail="reach@example.com", **{answer: value}
            )
        member.refresh_from_db()
        assert member.teaching_applied_at is None

    def it_blanks_every_new_answer_a_later_ask_leaves_out():
        member = _linked_member("apply-everything-again")
        _apply_with_everything(member)
        member.decline_teaching(decided_by=None, reason="Not yet.")
        member.apply_to_teach("Second ask.", contact_method="email", contact_detail="reach@example.com")
        member.refresh_from_db()
        blank = MemberFactory.build()
        blank.clear_teaching_answers()
        for field in Member.TEACHING_ANSWER_FIELDS:
            if field not in ("teaching_application_note", "teaching_contact_method", "teaching_contact_detail"):
                assert getattr(member, field) == getattr(blank, field), field

    def it_reads_blank_availability_for_an_application_that_never_asked():
        assert MemberFactory().teaching_availability_display == ""


def describe_teaching_contact_summary():
    def it_puts_the_method_after_the_detail():
        member = MemberFactory(teaching_contact_method="text", teaching_contact_detail="503 555 0100")
        assert member.teaching_contact_summary == "503 555 0100 (text message)"

    def it_is_the_bare_detail_when_no_method_was_asked():
        member = MemberFactory(teaching_contact_method="", teaching_contact_detail="old@example.com")
        assert member.teaching_contact_summary == "old@example.com"


def _inquiry(name: str, applied: str, **fields: object) -> Member:
    """A member who applied at ``applied`` (an ISO datetime, Portland time), with any extra fields."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    stamp = datetime.fromisoformat(applied).replace(tzinfo=ZoneInfo("America/Los_Angeles"))
    return MemberFactory(full_legal_name=name, preferred_name="", teaching_applied_at=stamp, **fields)


def describe_teaching_inquiries():
    def it_lists_every_applicant_newest_first_and_skips_members_who_never_asked():
        older = _inquiry("Older", "2026-09-01T10:00")
        newer = _inquiry("Newer", "2026-10-01T10:00")
        MemberFactory()
        assert list(Member.objects.teaching_inquiries()) == [newer, older]

    def it_keeps_both_bounds_inclusive_by_local_day():
        _inquiry("Before", "2026-08-31T23:30")
        first = _inquiry("First day", "2026-09-01T00:30")
        last = _inquiry("Last day", "2026-09-30T23:30")
        _inquiry("After", "2026-10-01T00:30")
        from datetime import date

        found = Member.objects.teaching_inquiries(applied_from=date(2026, 9, 1), applied_to=date(2026, 9, 30))
        assert list(found) == [last, first]


def describe_in_teaching_state():
    @pytest.fixture
    def members() -> dict[str, Member]:
        now = timezone.now()
        return {
            "approved": _inquiry("Approved", "2026-09-01T10:00", instructor_oriented_at=now),
            "declined": _inquiry(
                "Declined", "2026-09-02T10:00", teaching_decided_at=now, teaching_decline_reason="Not yet."
            ),
            "pending": _inquiry("Pending", "2026-09-03T10:00"),
            "half_decline": _inquiry("Half", "2026-09-04T10:00", teaching_decided_at=now),
            "none": MemberFactory(),
        }

    @pytest.mark.parametrize("state", list(Member.TeachingApplicationState))
    def it_matches_the_property_for_every_state(members, state):
        found = set(Member.objects.filter(pk__in=[m.pk for m in members.values()]).in_teaching_state(state))
        assert found == {m for m in members.values() if m.teaching_application_state == state}
        assert found  # every state is represented in the fixture


def describe_free_to_ask_to_teach():
    def it_takes_members_who_never_asked_or_were_declined_and_nobody_else():
        now = timezone.now()
        never = MemberFactory()
        declined = _inquiry("Declined", "2026-09-02T10:00", teaching_decided_at=now, teaching_decline_reason="Not yet.")
        _inquiry("Pending", "2026-09-03T10:00")
        _inquiry("Half", "2026-09-04T10:00", teaching_decided_at=now)
        MemberFactory(instructor_oriented_at=now)
        MemberFactory(status=Member.Status.FORMER)
        assert set(Member.objects.free_to_ask_to_teach()) == {never, declined}


def describe_apply_to_teach_added_by_an_admin():
    """#794: an admin files an ask by hand for a member who reached out another way."""

    ANSWERS: dict[str, object] = {
        "contact_method": "text",
        "contact_detail": "503 555 0100",
        "class_title": "Lost Wax Casting",
    }

    def it_records_the_ask_on_the_day_given_as_pending():
        admin = _fog_admin("added-admin")
        member = MemberFactory()
        stamp = timezone.now() - timezone.timedelta(days=3)
        member.apply_to_teach("Casting rings.", applied_at=stamp, added_by=admin.user, **ANSWERS)
        member.refresh_from_db()
        assert member.teaching_applied_at == stamp
        assert member.teaching_class_title == "Lost Wax Casting"
        assert member.teaching_application_note == "Casting rings."
        assert member.teaching_application_state == Member.TeachingApplicationState.PENDING

    def it_logs_an_inquiry_added_row_naming_the_admin_and_no_applied_row():
        admin = _fog_admin("added-actor")
        admin.full_legal_name = "Alex Admin"
        admin.save(update_fields=["full_legal_name"])
        member = MemberFactory(full_legal_name="Robin Reachout", preferred_name="")
        member.apply_to_teach("Casting rings.", added_by=admin.user, **ANSWERS)
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.TEACHING_INQUIRY_ADDED)
        assert row.actor == admin.user
        assert row.who == "Alex Admin"
        assert row.target == member
        assert row.payload == {
            "member": "Robin Reachout",
            "reached_out_on": timezone.localdate(member.teaching_applied_at).isoformat(),
        }
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.TEACHING_APPLIED).exists()

    def it_sends_no_application_notice():
        admin = _fog_admin("added-silent")
        MemberFactory().apply_to_teach("Casting rings.", added_by=admin.user, **ANSWERS)
        assert Notification.objects.filter(trigger="instructor_application_received").count() == 0

    def it_takes_an_ask_with_no_contact_answers():
        admin = _fog_admin("added-nocontact")
        member = MemberFactory()
        member.apply_to_teach("Casting rings.", contact_method="", contact_detail="  ", added_by=admin.user)
        member.refresh_from_db()
        assert member.teaching_contact_method == ""
        assert member.teaching_contact_detail == ""
        assert member.teaching_application_state == Member.TeachingApplicationState.PENDING

    @pytest.mark.parametrize(
        ("method", "detail"), [("text", ""), ("", "503 555 0100")], ids=["method only", "detail only"]
    )
    def it_refuses_one_contact_answer_without_the_other(method, detail):
        admin = _fog_admin("added-half")
        member = MemberFactory()
        with pytest.raises(ValueError):
            member.apply_to_teach("Casting rings.", contact_method=method, contact_detail=detail, added_by=admin.user)
        member.refresh_from_db()
        assert member.teaching_applied_at is None

    def it_refuses_a_day_in_the_future():
        admin = _fog_admin("added-future")
        member = MemberFactory()
        with pytest.raises(ValueError):
            member.apply_to_teach(
                "Casting rings.",
                applied_at=timezone.now() + timezone.timedelta(minutes=5),
                added_by=admin.user,
                **ANSWERS,
            )
        member.refresh_from_db()
        assert member.teaching_applied_at is None

    def it_still_refuses_a_member_with_an_ask_waiting():
        admin = _fog_admin("added-pending")
        member = _inquiry("Pending", "2026-09-03T10:00")
        with pytest.raises(ValueError):
            member.apply_to_teach("Again.", added_by=admin.user, **ANSWERS)


def describe_apply_to_teach_website_scheme():
    """The Instructor Inquiries page renders the website as a link: only http and https get in."""

    @pytest.mark.parametrize("website", ["javascript:alert(1)", "ftp://files.example.com", "data:text/html,hi"])
    def it_refuses_any_other_scheme_and_saves_nothing(website):
        member = MemberFactory()
        with pytest.raises(ValueError):
            member.apply_to_teach("Rings.", contact_method="text", contact_detail="503 555 0100", website=website)
        member.refresh_from_db()
        assert member.teaching_applied_at is None
        assert member.teaching_website == ""

    @pytest.mark.parametrize("website", ["https://robin.example", "HTTP://robin.example/work"])
    def it_keeps_an_http_or_https_address(website):
        member = MemberFactory()
        member.apply_to_teach("Rings.", contact_method="text", contact_detail="503 555 0100", website=website)
        member.refresh_from_db()
        assert member.teaching_website == website

    def it_stamps_now_when_no_day_is_given():
        before = timezone.now()
        member = MemberFactory()
        member.apply_to_teach("Rings.", contact_method="text", contact_detail="503 555 0100")
        member.refresh_from_db()
        assert before <= member.teaching_applied_at <= timezone.now()
