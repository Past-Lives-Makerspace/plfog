"""Data-migration spec for classes 0085: rows asking a locked FAQ question are deleted, and back.

Uses Django's ``MigrationExecutor`` (the pattern of remove_member_discount_spec). Rows are built
with the factories at head; stepping back to 0084 runs the reverse (the locked rows return at
the top of every class with rows) and stepping forward runs the deletion. Each test restores
the schema to head in a ``finally`` so the rest of the suite sees the current DB.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

from classes.factories import ClassFaqFactory, ClassOfferingFactory
from classes.models import LOCKED_CLASS_FAQS, ClassFaq, ClassOffering, _faq_key

_APP = "classes"
_BEFORE = "0084_classoffering_eventbrite_category"
_AFTER = "0085_delete_locked_class_faq_rows"
_HEAD = MigrationLoader(None).graph.leaf_nodes(_APP)[0][1]

_migration = import_module(f"classes.migrations.{_AFTER}")

CANCELLATION, ACCESSIBLE = (faq["question"] for faq in LOCKED_CLASS_FAQS)

# Each one asks a locked question the way a person or a pasted document might type it.
LOOKALIKES = [
    CANCELLATION,
    "What’s your cancellation policy?",  # curly apostrophe
    "What‘s your cancellation policy?",  # the other curly one
    "WHAT'S YOUR CANCELLATION POLICY",  # no question mark
    "  what's   your\tcancellation\npolicy ?  ",  # runs of whitespace
    "Is the space accessible?",  # no-break space
    "Is the space accessible??",
    "Is the space accessible？",  # full-width question mark
    "Ｉs the space accessible?",  # full-width letter
]
NEAR_MISSES = ["Is the space accessible by bike?", "What's your refund policy?", "Accessible?"]


def _migrate(target: str) -> None:
    MigrationExecutor(connection).migrate([(_APP, target)])


def _rows(offering: ClassOffering) -> list[tuple[str, int]]:
    return list(ClassFaq.objects.filter(class_offering=offering).values_list("question", "sort_order"))


def describe_the_frozen_copy():
    def it_matches_the_locked_questions_and_copy_in_code():
        assert _migration.LOCKED_FAQS == LOCKED_CLASS_FAQS

    @pytest.mark.parametrize("question", [*LOOKALIKES, *NEAR_MISSES])
    def it_keys_every_question_as_the_model_does(question):
        assert _migration.faq_key(question) == _faq_key(question)

    @pytest.mark.parametrize("question", LOOKALIKES)
    def it_reads_each_lookalike_as_locked(question):
        assert _migration.faq_key(question) in _migration.LOCKED_KEYS

    @pytest.mark.parametrize("question", NEAR_MISSES)
    def it_reads_a_near_miss_as_the_classes_own(question):
        assert _migration.faq_key(question) not in _migration.LOCKED_KEYS


@pytest.mark.django_db(transaction=True)
def describe_migration_0085_delete_locked_class_faq_rows():
    def it_deletes_every_lookalike_of_a_locked_question_and_keeps_the_rest():
        try:
            offering = ClassOfferingFactory()
            locked = [ClassFaqFactory(class_offering=offering, question=q, answer="Old.") for q in LOOKALIKES]
            kept = [ClassFaqFactory(class_offering=offering, question=q, answer="Mine.") for q in NEAR_MISSES]

            _migrate(_BEFORE)  # the class already asks both, so the reverse adds nothing
            assert ClassFaq.objects.count() == len(locked) + len(kept)
            _migrate(_AFTER)

            assert sorted(ClassFaq.objects.values_list("pk", flat=True)) == sorted(row.pk for row in kept)
        finally:
            _migrate(_HEAD)

    def it_reverses_by_putting_the_locked_questions_back_at_the_top_of_each_class_with_rows():
        try:
            both_missing = ClassOfferingFactory()
            ClassFaqFactory(class_offering=both_missing, question="Gloves?", sort_order=0)
            ClassFaqFactory(class_offering=both_missing, question="Age?", sort_order=1)
            one_missing = ClassOfferingFactory()
            ClassFaqFactory(class_offering=one_missing, question="Dog?", sort_order=0)
            ClassFaqFactory(class_offering=one_missing, question="is the space ACCESSIBLE", sort_order=1)
            no_rows = ClassOfferingFactory()

            _migrate(_BEFORE)

            assert _rows(both_missing) == [(CANCELLATION, 0), (ACCESSIBLE, 1), ("Gloves?", 2), ("Age?", 3)]
            assert _rows(one_missing) == [(CANCELLATION, 0), ("Dog?", 1), ("is the space ACCESSIBLE", 2)]
            assert _rows(no_rows) == []
            restored = ClassFaq.objects.get(class_offering=both_missing, question=CANCELLATION)
            assert restored.answer == LOCKED_CLASS_FAQS[0]["answer"]

            _migrate(_AFTER)

            assert [q for q, _ in _rows(both_missing)] == ["Gloves?", "Age?"]
            assert [q for q, _ in _rows(one_missing)] == ["Dog?"]
        finally:
            _migrate(_HEAD)
