"""BDD specs for the shared registration-questions helper (classes/questions.py).

Covers the pre-fill resolver: profile-backed answers for logged-in users, the
fall-back to a user's own past registration answers, and the guest-by-email path.
Field building and answer collection are exercised through the registration form
spec; here we focus on the new resolution logic.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser

from classes.factories import RegistrationFactory
from classes.models import RegistrationAnswer, RegistrationQuestion
from classes.questions import prefill_answers
from core.models import UserProfile

pytestmark = pytest.mark.django_db


def describe_prefill_answers():
    def it_returns_empty_when_no_active_questions():
        answers, found = prefill_answers(None, "someone@example.com")
        assert answers == {}
        assert found is False

    def describe_authenticated_user():
        def it_prefills_from_the_stored_profile_answers(member_user):
            question = RegistrationQuestion.objects.create(prompt="Experience?")
            UserProfile.objects.create(user=member_user, custom_question_answers={str(question.pk): "Beginner"})

            answers, found = prefill_answers(member_user)

            assert answers == {question.pk: "Beginner"}
            assert found is True

        def it_ignores_stored_answers_for_now_inactive_questions(member_user):
            retired = RegistrationQuestion.objects.create(prompt="Old?", is_active=False)
            UserProfile.objects.create(user=member_user, custom_question_answers={str(retired.pk): "x"})

            answers, found = prefill_answers(member_user)

            assert answers == {}
            assert found is False

        def it_keeps_active_stored_answers_while_skipping_inactive_ones(member_user):
            active = RegistrationQuestion.objects.create(prompt="Active?")
            retired = RegistrationQuestion.objects.create(prompt="Retired?", is_active=False)
            UserProfile.objects.create(
                user=member_user,
                custom_question_answers={str(active.pk): "yes", str(retired.pk): "old"},
            )

            answers, found = prefill_answers(member_user)

            assert answers == {active.pk: "yes"}
            assert found is True

        def it_falls_back_to_a_past_registration_answer_when_profile_is_silent(member_user):
            question = RegistrationQuestion.objects.create(prompt="Allergies?")
            registration = RegistrationFactory(email=member_user.email)
            RegistrationAnswer.objects.create(registration=registration, question=question, answer_text="None")

            answers, found = prefill_answers(member_user)

            assert answers == {question.pk: "None"}
            assert found is True

        def it_prefers_the_profile_answer_over_an_older_registration_answer(member_user):
            question = RegistrationQuestion.objects.create(prompt="Experience?")
            UserProfile.objects.create(user=member_user, custom_question_answers={str(question.pk): "Pro"})
            registration = RegistrationFactory(email=member_user.email)
            RegistrationAnswer.objects.create(registration=registration, question=question, answer_text="Newbie")

            answers, _ = prefill_answers(member_user)

            assert answers == {question.pk: "Pro"}

    def describe_guest_by_email():
        def it_prefills_from_the_latest_registration_for_that_email():
            question = RegistrationQuestion.objects.create(prompt="Experience?")
            registration = RegistrationFactory(email="repeat@example.com")
            RegistrationAnswer.objects.create(registration=registration, question=question, answer_text="Some")

            answers, found = prefill_answers(AnonymousUser(), "repeat@example.com")

            assert answers == {question.pk: "Some"}
            assert found is True

        def it_uses_the_most_recent_answer_when_several_exist():
            question = RegistrationQuestion.objects.create(prompt="Experience?")
            older = RegistrationFactory(email="g@example.com")
            RegistrationAnswer.objects.create(registration=older, question=question, answer_text="old")
            newer = RegistrationFactory(email="g@example.com")
            RegistrationAnswer.objects.create(registration=newer, question=question, answer_text="new")

            answers, _ = prefill_answers(AnonymousUser(), "g@example.com")

            assert answers == {question.pk: "new"}

        def it_drops_history_for_questions_that_are_now_inactive():
            active = RegistrationQuestion.objects.create(prompt="Active?")
            retired = RegistrationQuestion.objects.create(prompt="Retired?", is_active=False)
            registration = RegistrationFactory(email="g2@example.com")
            RegistrationAnswer.objects.create(registration=registration, question=active, answer_text="keep")
            RegistrationAnswer.objects.create(registration=registration, question=retired, answer_text="drop")

            answers, _ = prefill_answers(AnonymousUser(), "g2@example.com")

            assert answers == {active.pk: "keep"}

        def it_returns_nothing_for_an_email_with_no_history():
            RegistrationQuestion.objects.create(prompt="Experience?")

            answers, found = prefill_answers(AnonymousUser(), "stranger@example.com")

            assert answers == {}
            assert found is False

        def it_returns_nothing_for_an_anonymous_user_with_no_email():
            RegistrationQuestion.objects.create(prompt="Experience?")

            answers, found = prefill_answers(AnonymousUser(), "")

            assert answers == {}
            assert found is False
