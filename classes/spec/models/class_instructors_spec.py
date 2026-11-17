"""BDD specs for a class's instructors (#686, part 1).

The ``ClassInstructor`` rows are the source of truth: ``teaches`` and the queryset helpers read
them, ``set_instructors`` writes them in order, and ``instructor`` (the billing payee) follows
the first row on every save, whatever was assigned to it in memory.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from classes.factories import ClassOfferingFactory, InstructorFactory
from classes.models import ClassInstructor, ClassOffering

pytestmark = pytest.mark.django_db


def _members(offering: ClassOffering) -> list[int]:
    return [member.pk for member in offering.instructor_list]


def describe_a_class_saved_with_an_instructor():
    def it_lists_that_member_as_its_one_instructor():
        teacher = InstructorFactory()
        offering = ClassOfferingFactory(instructor=teacher)
        assert _members(offering) == [teacher.pk]
        assert offering.instructor_id == teacher.pk

    def it_lists_nobody_when_no_instructor_was_set():
        offering = ClassOfferingFactory(instructor=None)
        assert _members(offering) == []
        assert offering.instructor_id is None

    def it_seeds_a_row_for_a_payee_a_class_has_without_rows():
        # The previous release saves instructor alone during the deploy window; the next save heals it.
        teacher = InstructorFactory()
        offering = ClassOfferingFactory(instructor=teacher)
        offering.instructor_rows.all().delete()
        offering.save()
        assert _members(offering) == [teacher.pk]


def describe_teaches():
    def it_is_true_for_every_listed_instructor_and_false_for_anyone_else():
        first, second, other = InstructorFactory(), InstructorFactory(), InstructorFactory()
        offering = ClassOfferingFactory(instructor=first, instructors=[first, second])
        assert offering.teaches(first) is True
        assert offering.teaches(second) is True
        assert offering.teaches(other) is False


def describe_set_instructors():
    def it_keeps_the_order_given_and_makes_the_first_the_payee():
        a, b, c = InstructorFactory(), InstructorFactory(), InstructorFactory()
        offering = ClassOfferingFactory(instructor=None)
        offering.set_instructors([b, c, a])
        assert _members(offering) == [b.pk, c.pk, a.pk]
        assert offering.instructor_id == b.pk
        assert ClassOffering.objects.get(pk=offering.pk).instructor_id == b.pk

    def it_keeps_a_listed_members_place_and_appends_the_new_ones_after():
        a, b, c = InstructorFactory(), InstructorFactory(), InstructorFactory()
        offering = ClassOfferingFactory(instructor=a)
        offering.set_instructors([c, a, b])
        assert _members(offering) == [a.pk, c.pk, b.pk]
        assert offering.instructor_id == a.pk

    def it_removes_whoever_is_left_off_and_moves_the_payee_to_the_new_first():
        a, b = InstructorFactory(), InstructorFactory()
        offering = ClassOfferingFactory(instructor=a, instructors=[a, b])
        offering.set_instructors([b])
        assert _members(offering) == [b.pk]
        assert offering.instructor_id == b.pk
        assert ClassOffering.objects.get(pk=offering.pk).instructor_id == b.pk

    def it_refuses_an_empty_list():
        offering = ClassOfferingFactory()
        with pytest.raises(ValueError, match="at least one instructor"):
            offering.set_instructors([])
        assert len(_members(offering)) == 1

    def it_lists_a_member_given_twice_once():
        a = InstructorFactory()
        offering = ClassOfferingFactory(instructor=None)
        offering.set_instructors([a, a])
        assert _members(offering) == [a.pk]


def describe_the_payee():
    def it_follows_the_first_row_whatever_was_assigned_to_instructor():
        a, b = InstructorFactory(), InstructorFactory()
        offering = ClassOfferingFactory(instructor=a)
        offering.instructor = b
        offering.save(update_fields=["instructor"])
        assert offering.instructor_id == a.pk
        assert ClassOffering.objects.get(pk=offering.pk).instructor_id == a.pk
        assert _members(offering) == [a.pk]

    def it_cannot_list_one_member_twice():
        a = InstructorFactory()
        offering = ClassOfferingFactory(instructor=a)
        with pytest.raises(IntegrityError), transaction.atomic():
            ClassInstructor.objects.create(class_offering=offering, member=a)

    def it_names_who_teaches_what():
        a = InstructorFactory(full_legal_name="Alex Eason")
        offering = ClassOfferingFactory(instructor=a, title="Picture Framing")
        assert str(offering.instructor_rows.get()) == "Alex Eason teaches Picture Framing"


def describe_run_again_and_duplicate():
    def it_copies_every_instructor_in_order_to_the_new_run():
        a, b = InstructorFactory(), InstructorFactory()
        source = ClassOfferingFactory(instructor=a, instructors=[a, b])
        source_pk = source.pk
        run = source.duplicate_as_new_run()
        assert run.pk != source_pk
        assert _members(run) == [a.pk, b.pk]
        assert run.instructor_id == a.pk
        assert _members(ClassOffering.objects.get(pk=source_pk)) == [a.pk, b.pk]

    def it_copies_them_to_a_duplicate_too():
        a, b = InstructorFactory(), InstructorFactory()
        source = ClassOfferingFactory(instructor=a, instructors=[a, b])
        copy = source.duplicate()
        assert _members(copy) == [a.pk, b.pk]

    def it_clones_a_class_with_nobody_listed_with_nobody():
        source = ClassOfferingFactory(instructor=None)
        run = source.duplicate_as_new_run()
        assert _members(run) == []
        assert run.instructor_id is None


def describe_the_queryset_helpers():
    def it_lists_a_class_for_each_of_its_instructors():
        a, b = InstructorFactory(), InstructorFactory()
        offering = ClassOfferingFactory(instructor=a, instructors=[a, b])
        ClassOfferingFactory(instructor=a)
        assert list(ClassOffering.objects.for_instructor(b)) == [offering]
        assert ClassOffering.objects.for_instructor(a).count() == 2

    def it_hosts_an_authored_class_two_people_teach_once():
        a, b = InstructorFactory(), InstructorFactory()
        offering = ClassOfferingFactory(instructor=a, instructors=[a, b], created_by=a)
        assert list(ClassOffering.objects.hosted_by(a)) == [offering]
        assert ClassOffering.objects.hosted_by(a).count() == 1
        assert list(ClassOffering.objects.hosted_by(b)) == [offering]


def describe_instructor_names():
    def it_is_empty_with_nobody_listed():
        assert ClassOfferingFactory(instructor=None).instructor_names == ""

    def it_is_the_one_name_for_one_instructor():
        a = InstructorFactory(full_legal_name="Alex Eason")
        assert ClassOfferingFactory(instructor=a).instructor_names == "Alex Eason"

    def it_joins_two_with_and_and_more_with_commas_in_the_order_added():
        a = InstructorFactory(full_legal_name="Alex Eason")
        b = InstructorFactory(full_legal_name="Jenelle Giordano")
        c = InstructorFactory(full_legal_name="Pat Plain", preferred_name="Pat")
        assert (
            ClassOfferingFactory(instructor=a, instructors=[a, b]).instructor_names == "Alex Eason and Jenelle Giordano"
        )
        assert (
            ClassOfferingFactory(instructor=a, instructors=[b, a, c]).instructor_names
            == "Jenelle Giordano, Alex Eason and Pat"
        )
