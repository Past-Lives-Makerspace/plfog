"""BDD specs for RegistrationQuestion and RegistrationAnswer."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from classes.factories import RegistrationFactory
from classes.models import RegistrationAnswer, RegistrationQuestion

pytestmark = pytest.mark.django_db


def describe_RegistrationQuestion():
    def it_orders_by_sort_order():
        b = RegistrationQuestion.objects.create(prompt="B", sort_order=2)
        a = RegistrationQuestion.objects.create(prompt="A", sort_order=1)
        assert list(RegistrationQuestion.objects.all()) == [a, b]

    def it_truncates_long_prompts_in_str():
        q = RegistrationQuestion.objects.create(prompt="x" * 200)
        assert len(str(q)) == 80


def describe_RegistrationAnswer():
    def it_enforces_one_answer_per_question_per_registration():
        reg = RegistrationFactory()
        q = RegistrationQuestion.objects.create(prompt="Q1")
        RegistrationAnswer.objects.create(registration=reg, question=q, answer_text="first")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                RegistrationAnswer.objects.create(registration=reg, question=q, answer_text="second")

    def it_protects_question_with_existing_answers():
        from django.db.models.deletion import ProtectedError

        reg = RegistrationFactory()
        q = RegistrationQuestion.objects.create(prompt="Q1")
        RegistrationAnswer.objects.create(registration=reg, question=q, answer_text="x")
        with pytest.raises(ProtectedError):
            q.delete()
