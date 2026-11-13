"""Migration spec for classes 0092: every class's instructor becomes its first instructors row, and back.

Uses Django's ``MigrationExecutor`` (the pattern of delete_locked_class_faq_rows_spec). Rows are
built with the factories at head; stepping back to 0091 runs the reverse (the first listed
instructor is written back as the payee, then the table goes) and stepping forward runs the
backfill. Each test restores the schema to head in a ``finally`` so the rest of the suite sees
the current DB.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from classes.factories import ClassOfferingFactory, InstructorFactory
from classes.models import ClassInstructor, ClassOffering
from classes.spec.migrations._head import migrate_all_to_head

_APP = "classes"
_BEFORE = "0091_discountcode_instructor"
_AFTER = "0092_classinstructor"


def _migrate(target: str) -> None:
    MigrationExecutor(connection).migrate([(_APP, target)])


def _payee_id(pk: int) -> int | None:
    """The class's payee, reading that one column: a class column added after 0092 is absent mid-step."""
    return ClassOffering.objects.filter(pk=pk).values_list("instructor_id", flat=True).get()


@pytest.mark.django_db(transaction=True)
def describe_migration_0092_classinstructor():
    def it_copies_each_classs_instructor_into_a_row_stamped_with_the_classs_creation():
        try:
            teacher = InstructorFactory()
            taught = ClassOfferingFactory(instructor=teacher)
            nobody = ClassOfferingFactory(instructor=None)
            _migrate(_BEFORE)
            _migrate(_AFTER)
            rows = list(ClassInstructor.objects.values_list("class_offering_id", "member_id", "added_at"))
            assert rows == [(taught.pk, teacher.pk, taught.created_at)]
            assert not ClassInstructor.objects.filter(class_offering=nobody).exists()
            assert _payee_id(taught.pk) == teacher.pk
        finally:
            migrate_all_to_head()

    def it_reverses_by_writing_the_first_listed_instructor_back_as_the_payee():
        try:
            a, b = InstructorFactory(), InstructorFactory()
            offering = ClassOfferingFactory(instructor=a, instructors=[a, b])
            # b was added later; move b's row ahead of a's so the first listed is not the payee.
            ClassInstructor.objects.filter(class_offering=offering, member=b).update(
                added_at=offering.created_at - timedelta(days=1)
            )
            assert _payee_id(offering.pk) == a.pk
            _migrate(_BEFORE)
            assert _payee_id(offering.pk) == b.pk
        finally:
            migrate_all_to_head()
