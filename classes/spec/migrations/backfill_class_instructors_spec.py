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
from django.db.migrations.loader import MigrationLoader

from classes.factories import ClassOfferingFactory, InstructorFactory
from classes.models import ClassInstructor, ClassOffering

_APP = "classes"
_BEFORE = "0091_discountcode_instructor"
_AFTER = "0092_classinstructor"
_HEAD = MigrationLoader(None).graph.leaf_nodes(_APP)[0][1]


def _migrate(target: str) -> None:
    MigrationExecutor(connection).migrate([(_APP, target)])


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
            assert ClassOffering.objects.get(pk=taught.pk).instructor_id == teacher.pk
        finally:
            _migrate(_HEAD)

    def it_reverses_by_writing_the_first_listed_instructor_back_as_the_payee():
        try:
            a, b = InstructorFactory(), InstructorFactory()
            offering = ClassOfferingFactory(instructor=a, instructors=[a, b])
            # b was added later; move b's row ahead of a's so the first listed is not the payee.
            ClassInstructor.objects.filter(class_offering=offering, member=b).update(
                added_at=offering.created_at - timedelta(days=1)
            )
            assert ClassOffering.objects.get(pk=offering.pk).instructor_id == a.pk
            _migrate(_BEFORE)
            assert ClassOffering.objects.get(pk=offering.pk).instructor_id == b.pk
        finally:
            _migrate(_HEAD)
