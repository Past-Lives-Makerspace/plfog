"""Data-migration spec for classes 0085: rows asking a locked FAQ question are deleted.

Uses Django's ``MigrationExecutor`` (the pattern of remove_member_discount_spec). The rows are
built with the factories at head, the app is stepped back to 0084 (0085's reverse is a no-op,
so they survive) and forward again, which runs the deletion against them. Each test restores
the schema to head in a ``finally`` so the rest of the suite sees the current DB.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

from classes.factories import ClassFaqFactory, ClassOfferingFactory
from classes.models import LOCKED_CLASS_FAQS, ClassFaq, is_locked_class_faq

_APP = "classes"
_BEFORE = "0084_classoffering_eventbrite_category"
_AFTER = "0085_delete_locked_class_faq_rows"
_HEAD = MigrationLoader(None).graph.leaf_nodes(_APP)[0][1]

_migration = import_module(f"classes.migrations.{_AFTER}")

# Prod 2026-10-09: classes 553 and 667 held this older cancellation answer (710 chars).
_OLDER_CANCELLATION = "An older cancellation answer, as two prod classes still carried it."


def _migrate(target: str) -> None:
    MigrationExecutor(connection).migrate([(_APP, target)])


def describe_the_frozen_questions():
    def it_matches_the_locked_questions_in_code():
        assert {faq["question"].strip().casefold() for faq in LOCKED_CLASS_FAQS} == _migration.LOCKED_QUESTIONS
        assert all(is_locked_class_faq(question) for question in _migration.LOCKED_QUESTIONS)


@pytest.mark.django_db(transaction=True)
def describe_migration_0085_delete_locked_class_faq_rows():
    def it_deletes_every_copy_of_a_locked_question_and_keeps_the_rest():
        try:
            offering = ClassOfferingFactory()
            other = ClassOfferingFactory()
            for faq in LOCKED_CLASS_FAQS:
                ClassFaqFactory(class_offering=offering, question=faq["question"], answer=faq["answer"])
            ClassFaqFactory(
                class_offering=other, question="What's your cancellation policy?", answer=_OLDER_CANCELLATION
            )
            ClassFaqFactory(class_offering=other, question="  IS THE SPACE ACCESSIBLE?\n", answer="Spaced and shouted.")
            kept = ClassFaqFactory(class_offering=offering, question="Can I bring my dog?", answer="Sadly no.")
            near = ClassFaqFactory(class_offering=other, question="Is the space accessible by bike?", answer="Yes.")

            _migrate(_BEFORE)
            assert ClassFaq.objects.count() == 6
            _migrate(_AFTER)

            assert sorted(ClassFaq.objects.values_list("pk", flat=True)) == sorted([kept.pk, near.pk])
        finally:
            _migrate(_HEAD)

    def it_reverses_without_touching_any_row():
        try:
            kept = ClassFaqFactory(question="Can I bring my dog?", answer="Sadly no.")
            _migrate(_BEFORE)
            assert list(ClassFaq.objects.values_list("pk", flat=True)) == [kept.pk]
        finally:
            _migrate(_HEAD)
