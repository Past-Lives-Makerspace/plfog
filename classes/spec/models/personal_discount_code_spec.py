"""Every instructor's personal discount code (#1065): how it is named, made, kept single, and honoured.

``DiscountCode.objects.ensure_personal_for`` makes or adopts the code; ``Member.save`` calls it
whenever teaching access is granted (``instructor_oriented_at`` set on an existing member), so
every grant path makes one. The code only ever uses the instructor scope from #764, so checkout
honours it through ``honoured_on`` with no new rule.
"""

from __future__ import annotations

import itertools
import re

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from classes.factories import ClassOfferingFactory, DiscountCodeFactory, InstructorFactory
from classes.forms import RegistrationForm
from classes.models import (
    PERSONAL_CODE_DESCRIPTION,
    ClassOffering,
    ClassSettings,
    CmsActivity,
    DiscountCode,
    personal_code_candidates,
    personal_code_stem,
    personal_code_surname,
)
from core.models import SiteConfiguration
from membership.models import Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _locked(name: str = "Jacqueline Sowell", **kwargs) -> Member:
    """A member who cannot teach yet, loaded from the database as every grant path finds one."""
    member = MemberFactory(full_legal_name=name, preferred_name="", instructor_oriented_at=None, **kwargs)
    return Member.objects.get(pk=member.pk)


def _set_pct(pct: int) -> None:
    config = SiteConfiguration.load()
    config.instructor_code_discount_pct = pct
    config.save(update_fields=["instructor_code_discount_pct"])


def _personal(member: Member) -> list[DiscountCode]:
    return list(DiscountCode.objects.filter(instructor=member, is_personal=True))


def describe_personal_code_stem():
    def it_is_the_first_word_in_capitals():
        assert personal_code_stem("Jacqueline Sowell") == "JACQUELINE"

    def it_folds_accents_and_keeps_letters_only():
        assert personal_code_stem("José Ruiz") == "JOSE"
        assert personal_code_stem("Anne-Marie O'Neil") == "ANNEMARIE"
        # Digits are dropped: the only digits in a personal code are the percent at its end.
        assert personal_code_stem("R2D2 Unit") == "RD"

    def it_falls_back_when_nothing_is_left():
        assert personal_code_stem("") == "TEACH"
        assert personal_code_stem("   ") == "TEACH"
        assert personal_code_stem("李 小龙") == "TEACH"
        assert personal_code_stem("42 Smith") == "TEACH"

    def it_cuts_a_very_long_name():
        assert personal_code_stem("A" * 60) == "A" * 20


def describe_personal_code_surname():
    def it_is_the_last_word_of_the_name_members_see():
        assert personal_code_surname("Jacqueline Sowell", "Jacqueline Sowell") == "SOWELL"
        assert personal_code_surname("Jackie Mae Fin", "Jacqueline Finger") == "FIN"

    def it_falls_back_to_the_legal_last_name_under_a_one_word_preferred_name():
        assert personal_code_surname("Jackie", "Jacqueline O'Finger") == "OFINGER"

    def it_is_empty_when_neither_name_has_a_second_word():
        assert personal_code_surname("Jacqueline", "Jacqueline") == ""
        assert personal_code_surname("Jacqueline", "") == ""

    def it_cuts_a_very_long_last_name():
        assert personal_code_surname("Jo " + "B" * 40, "") == "B" * 14


def describe_personal_code_candidates():
    def _first(display_name: str, legal: str, count: int, pct: int = 10) -> list[str]:
        return list(itertools.islice(personal_code_candidates(display_name, legal, pct), count))

    def it_tries_the_first_name_then_a_letter_of_the_last_name_at_a_time_then_a_b_c():
        assert _first("Jacqueline Finger", "Jacqueline Finger", 10) == [
            "JACQUELINE10",
            "JACQUELINEF10",
            "JACQUELINEFI10",
            "JACQUELINEFIN10",
            "JACQUELINEFING10",
            "JACQUELINEFINGE10",
            "JACQUELINEFINGER10",
            "JACQUELINEFINGERA10",
            "JACQUELINEFINGERB10",
            "JACQUELINEFINGERC10",
        ]

    def it_goes_straight_to_a_b_c_with_no_last_name():
        assert _first("Jacqueline", "Jacqueline", 3) == ["JACQUELINE10", "JACQUELINEA10", "JACQUELINEB10"]

    def it_runs_on_to_two_letters_after_z():
        candidates = _first("Kai", "Kai", 29)
        assert candidates[26:29] == ["KAIZ10", "KAIAA10", "KAIAB10"]

    def it_never_puts_a_digit_anywhere_but_the_trailing_percent():
        for code in _first("R2D2 Unit-9", "R2D2 Unit-9", 800, pct=100):
            assert re.fullmatch(r"[A-Z]+100", code), code
            assert len(code) <= DiscountCode._meta.get_field("code").max_length

    def it_fits_the_code_column_at_the_longest():
        long_name = f"{'A' * 60} {'B' * 60}"
        for code in _first(long_name, long_name, 800, pct=100):
            assert len(code) <= DiscountCode._meta.get_field("code").max_length


def describe_ensure_personal_for():
    def it_makes_an_approved_active_unlimited_instructor_code_at_the_site_percent():
        member = _locked()
        code = DiscountCode.objects.ensure_personal_for(member)
        assert code.code == "JACQUELINE10"
        assert code.discount_pct == 10
        assert code.discount_fixed_cents is None
        assert code.instructor == member
        assert code.class_offering is None
        assert code.is_personal is True
        assert code.is_approved is True
        assert code.is_active is True
        assert code.auto_apply is False
        assert code.max_uses is None
        assert code.valid_from is None
        assert code.valid_until is None
        assert code.description == PERSONAL_CODE_DESCRIPTION
        assert code.scope == DiscountCode.Scope.INSTRUCTOR

    def it_uses_the_name_members_see():
        member = _locked("Jacqueline Sowell")
        Member.objects.filter(pk=member.pk).update(preferred_name="Jackie")
        member.refresh_from_db()
        assert DiscountCode.objects.ensure_personal_for(member).code == "JACKIE10"

    def it_reads_the_percent_from_site_settings_when_it_is_made():
        _set_pct(30)
        first = DiscountCode.objects.ensure_personal_for(_locked("Jacqueline One"))
        assert (first.code, first.discount_pct) == ("JACQUELINE30", 30)

        _set_pct(15)
        # Only codes made afterwards change.
        first.refresh_from_db()
        assert first.discount_pct == 30
        second = DiscountCode.objects.ensure_personal_for(_locked("Robin Two"))
        assert (second.code, second.discount_pct) == ("ROBIN15", 15)

    def it_is_idempotent():
        member = _locked()
        first = DiscountCode.objects.ensure_personal_for(member)
        again = DiscountCode.objects.ensure_personal_for(member)
        assert again.pk == first.pk
        assert DiscountCode.objects.filter(instructor=member).count() == 1

    def it_adds_last_name_letters_until_the_code_is_free():
        DiscountCodeFactory(code="JACQUELINE10")
        DiscountCodeFactory(code="JACQUELINES10")
        code = DiscountCode.objects.ensure_personal_for(_locked("Jacqueline Sowell"))
        assert code.code == "JACQUELINESO10"

    def it_gives_two_jacquelines_with_different_last_names_distinct_codes():
        one = DiscountCode.objects.ensure_personal_for(_locked("Jacqueline Sowell"))
        two = DiscountCode.objects.ensure_personal_for(_locked("Jacqueline Ruiz"))
        assert (one.code, two.code) == ("JACQUELINE10", "JACQUELINER10")

    def it_gives_jacquelines_with_the_same_last_name_distinct_codes():
        codes = [DiscountCode.objects.ensure_personal_for(_locked("Jacqueline Finger")).code for _ in range(3)]
        assert codes == ["JACQUELINE10", "JACQUELINEF10", "JACQUELINEFI10"]

    def it_appends_letters_once_the_last_name_runs_out():
        for taken in ("JACQUELINE10", "JACQUELINEF10", "JACQUELINEFI10", "JACQUELINEFIN10"):
            DiscountCodeFactory(code=taken)
        for taken in ("JACQUELINEFING10", "JACQUELINEFINGE10", "JACQUELINEFINGER10"):
            DiscountCodeFactory(code=taken)
        assert DiscountCode.objects.ensure_personal_for(_locked("Jacqueline Finger")).code == "JACQUELINEFINGERA10"

    def it_appends_letters_for_a_jacqueline_with_no_last_name():
        one = DiscountCode.objects.ensure_personal_for(_locked("Jacqueline"))
        two = DiscountCode.objects.ensure_personal_for(_locked("Jacqueline"))
        assert (one.code, two.code) == ("JACQUELINE10", "JACQUELINEA10")

    def it_borrows_the_legal_last_name_under_a_one_word_preferred_name():
        DiscountCodeFactory(code="JACKIE10")
        member = _locked("Jacqueline Sowell")
        Member.objects.filter(pk=member.pk).update(preferred_name="Jackie")
        member.refresh_from_db()
        assert DiscountCode.objects.ensure_personal_for(member).code == "JACKIES10"

    def it_names_a_member_with_no_usable_name_by_the_fallback():
        assert DiscountCode.objects.ensure_personal_for(_locked("李 小龙")).code == "TEACH10"

    def it_adopts_the_oldest_code_an_admin_already_aimed_at_the_instructor():
        member = _locked()
        oldest = DiscountCodeFactory(code="JACQUELINE30", discount_pct=30, class_offering=None, instructor=member)
        DiscountCodeFactory(code="NEWERONE", class_offering=None, instructor=member)
        adopted = DiscountCode.objects.ensure_personal_for(member)
        assert adopted.pk == oldest.pk
        oldest.refresh_from_db()
        assert oldest.is_personal is True
        assert oldest.discount_pct == 30
        assert DiscountCode.objects.filter(instructor=member).count() == 2

    def it_logs_one_activity_row_and_asks_nobody_to_approve(monkeypatch):
        import sys

        sent: list[str] = []
        # The package re-exports the function under the module's name, so patch the module itself.
        monkeypatch.setattr(sys.modules["core.events.emit"], "emit", lambda key, **kwargs: sent.append(key))
        code = DiscountCode.objects.ensure_personal_for(_locked())
        assert (
            CmsActivity.objects.filter(kind=CmsActivity.Kind.DISCOUNT_CODE_CREATED, payload__code=code.code).count()
            == 1
        )
        assert "discount_code.requested" not in sent


def describe_one_personal_code_per_instructor():
    def it_refuses_a_second_personal_code_for_the_same_instructor():
        member = _locked()
        DiscountCode.objects.ensure_personal_for(member)
        with pytest.raises(IntegrityError), transaction.atomic():
            DiscountCodeFactory(code="SECOND", class_offering=None, instructor=member, is_personal=True)

    def it_refuses_a_personal_code_with_no_instructor():
        with pytest.raises(IntegrityError), transaction.atomic():
            DiscountCodeFactory(code="NOBODY", class_offering=None, instructor=None, is_personal=True)


def describe_reassign():
    def it_stops_being_personal_when_moved_off_its_instructor():
        member = _locked()
        code = DiscountCode.objects.ensure_personal_for(member)
        other = InstructorFactory()
        DiscountCode.objects.ensure_personal_for(other)

        code.reassign(instructor=other)

        code.refresh_from_db()
        assert code.is_personal is False
        assert code.instructor == other
        assert len(_personal(other)) == 1

    def it_stops_being_personal_when_moved_to_one_class_or_everywhere():
        member = _locked()
        code = DiscountCode.objects.ensure_personal_for(member)
        code.reassign(class_offering=ClassOfferingFactory())
        code.refresh_from_db()
        assert code.is_personal is False

        again = DiscountCode.objects.ensure_personal_for(member)
        again.reassign()
        again.refresh_from_db()
        assert again.is_personal is False

    def it_stays_personal_when_reassigned_to_the_same_instructor():
        member = _locked()
        code = DiscountCode.objects.ensure_personal_for(member)
        code.reassign(instructor=member)
        code.refresh_from_db()
        assert code.is_personal is True

    def it_leaves_an_ordinary_code_ordinary():
        code = DiscountCodeFactory(class_offering=None)
        code.reassign(instructor=InstructorFactory())
        code.refresh_from_db()
        assert code.is_personal is False


def describe_granting_teaching_makes_the_code():
    def it_makes_one_on_grant_teaching():
        member = _locked()
        member.grant_teaching(granted_by=None)
        assert [c.code for c in _personal(member)] == ["JACQUELINE10"]

    def it_makes_one_on_grant_instructor():
        member = _locked()
        member.grant_instructor(granted_by=None)
        assert len(_personal(member)) == 1

    def it_makes_one_when_an_admin_promotes_the_member_to_instructor():
        member = _locked()
        member.apply_admin_role(Member.ADMIN_ROLE_INSTRUCTOR)
        assert len(_personal(member)) == 1

    def it_makes_one_when_the_column_is_set_and_saved_directly():
        member = _locked()
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["instructor_oriented_at"])
        assert len(_personal(member)) == 1

    def it_makes_no_second_code_when_granted_twice():
        member = _locked()
        member.grant_teaching(granted_by=None)
        member.grant_teaching(granted_by=None)
        member.grant_instructor(granted_by=None)
        assert DiscountCode.objects.filter(instructor=member).count() == 1

    def it_makes_no_second_code_when_revoked_and_granted_again():
        member = _locked()
        member.grant_teaching(granted_by=None)
        member.revoke_teaching(revoked_by=None)
        assert len(_personal(member)) == 1  # revoking keeps the code; the classes it works on stay
        member.grant_teaching(granted_by=None)
        assert DiscountCode.objects.filter(instructor=member).count() == 1

    def it_keeps_an_instructor_code_an_admin_made_before_the_grant():
        member = _locked()
        admins = DiscountCodeFactory(code="JACQUELINE30", discount_pct=30, class_offering=None, instructor=member)
        member.grant_teaching(granted_by=None)
        assert [c.pk for c in _personal(member)] == [admins.pk]

    def it_makes_nothing_on_a_save_that_leaves_teaching_alone():
        member = _locked()
        member.about_me = "Changed"
        member.save()
        member.save(update_fields=["about_me"])
        assert not DiscountCode.objects.exists()

    def it_makes_nothing_when_update_fields_leave_out_the_grant():
        member = _locked()
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["about_me"])  # the column is not written, so nothing was granted
        assert not DiscountCode.objects.exists()

    def it_makes_nothing_when_an_instructor_is_saved_again():
        member = InstructorFactory()
        member = Member.objects.get(pk=member.pk)
        member.about_me = "Still teaching"
        member.save()
        assert not DiscountCode.objects.exists()

    def it_makes_nothing_for_a_new_row_or_a_deferred_load():
        MemberFactory(instructor_oriented_at=timezone.now())  # no production path creates a member able to teach
        locked = _locked()
        deferred = Member.objects.defer("instructor_oriented_at").get(pk=locked.pk)
        deferred.instructor_oriented_at = timezone.now()
        deferred.save()
        assert not DiscountCode.objects.exists()


def describe_live():
    def it_keeps_only_active_approved_codes():
        live = DiscountCodeFactory(code="LIVE")
        DiscountCodeFactory(code="OFF", is_active=False)
        DiscountCodeFactory(code="PENDING", is_approved=False)
        assert list(DiscountCode.objects.live()) == [live]


def describe_honoured_on_classes_of():
    def it_lists_every_code_that_works_on_one_of_their_classes():
        teacher = InstructorFactory()
        partner = InstructorFactory()
        stranger = InstructorFactory()
        shared = ClassOfferingFactory(instructor=teacher)
        shared.set_instructors([teacher, partner])
        elsewhere = ClassOfferingFactory(instructor=stranger)
        everywhere = DiscountCodeFactory(code="EVERY", class_offering=None)
        own_class = DiscountCodeFactory(code="ONECLASS", class_offering=shared)
        theirs = DiscountCodeFactory(code="THEIRS", class_offering=None, instructor=teacher)
        partners = DiscountCodeFactory(code="PARTNER", class_offering=None, instructor=partner)
        DiscountCodeFactory(code="OTHERCLASS", class_offering=elsewhere)
        DiscountCodeFactory(code="STRANGER", class_offering=None, instructor=stranger)

        codes = set(DiscountCode.objects.honoured_on_classes_of(teacher))

        assert codes == {everywhere, own_class, theirs, partners}

    def it_lists_their_own_code_before_they_teach_anything():
        teacher = InstructorFactory()
        theirs = DiscountCodeFactory(code="THEIRS", class_offering=None, instructor=teacher)
        assert list(DiscountCode.objects.honoured_on_classes_of(teacher)) == [theirs]


def describe_the_personal_code_at_checkout():
    """AC: it works on any class the instructor teaches, co-taught included, and nowhere else."""

    @pytest.fixture
    def settings_obj():
        return ClassSettings.load()

    def _class_by(teacher) -> ClassOffering:
        return ClassOfferingFactory(
            instructor=teacher, status=ClassOffering.Status.PUBLISHED, price_cents=10000, capacity=4
        )

    def _post(code: str) -> dict[str, str]:
        return {
            "first_name": "Sam",
            "last_name": "Smith",
            "pronouns": "",
            "email": "sam@example.com",
            "phone": "",
            "prior_experience": "",
            "looking_for": "",
            "discount_code": code,
            "liability_signature": "Sam Smith",
            "accepts_liability": "on",
        }

    def _form(offering, settings_obj, code: str) -> RegistrationForm:
        return RegistrationForm(data=_post(code), offering=offering, settings_obj=settings_obj)

    def it_is_accepted_on_a_class_they_teach_and_on_one_they_co_teach(settings_obj):
        teacher = InstructorFactory(full_legal_name="Jacqueline Sowell")
        code = DiscountCode.objects.ensure_personal_for(teacher)
        own = _class_by(teacher)
        co_taught = _class_by(InstructorFactory())
        co_taught.set_instructors([*co_taught.instructor_list, teacher])

        for offering in (own, co_taught):
            form = _form(offering, settings_obj, code.code.lower())
            assert form.is_valid(), form.errors
            assert form.validated_discount == code
            assert form.compute_final_price_cents() == 9000

    def it_is_refused_on_another_instructors_class(settings_obj):
        teacher = InstructorFactory(full_legal_name="Jacqueline Sowell")
        code = DiscountCode.objects.ensure_personal_for(teacher)
        form = _form(_class_by(InstructorFactory()), settings_obj, code.code)
        assert not form.is_valid()
        assert form.errors["discount_code"] == ["That discount code isn't recognized."]

    def it_is_refused_once_an_admin_switches_it_off(settings_obj):
        teacher = InstructorFactory(full_legal_name="Jacqueline Sowell")
        code = DiscountCode.objects.ensure_personal_for(teacher)
        code.is_active = False
        code.save()
        form = _form(_class_by(teacher), settings_obj, code.code)
        assert not form.is_valid()
        assert form.errors["discount_code"] == ["That discount code isn't valid right now."]
