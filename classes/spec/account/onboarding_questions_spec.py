"""BDD specs for the onboarding "a few more questions" step and step-3 routing into it."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.models import RegistrationQuestion
from core.models import UserProfile

pytestmark = pytest.mark.django_db


def describe_onboarding_questions_step():
    def it_renders_the_active_questions(book_client, member_user):
        question = RegistrationQuestion.objects.create(prompt="Experience?")
        book_client.force_login(member_user)

        resp = book_client.get(reverse("account:onboarding_questions"))

        assert resp.status_code == 200
        assert f"custom_q_{question.pk}" in resp.content.decode()

    def it_saves_answers_to_the_profile_and_returns_to_overview(book_client, member_user):
        question = RegistrationQuestion.objects.create(prompt="Experience?")
        book_client.force_login(member_user)

        resp = book_client.post(
            reverse("account:onboarding_questions"),
            {f"custom_q_{question.pk}": "Beginner"},
        )

        assert resp.status_code == 302
        assert resp.url == reverse("account:overview")
        profile = UserProfile.objects.get(user=member_user)
        assert profile.custom_question_answers == {str(question.pk): "Beginner"}

    def it_prefills_the_form_from_stored_profile_answers(book_client, member_user):
        question = RegistrationQuestion.objects.create(prompt="Experience?")
        UserProfile.objects.create(user=member_user, custom_question_answers={str(question.pk): "Pro"})
        book_client.force_login(member_user)

        resp = book_client.get(reverse("account:onboarding_questions"))

        assert resp.context["form"][f"custom_q_{question.pk}"].value() == "Pro"

    def it_does_not_500_when_no_profile_exists(book_client, member_user):
        RegistrationQuestion.objects.create(prompt="Experience?")
        book_client.force_login(member_user)

        resp = book_client.get(reverse("account:onboarding_questions"))

        assert resp.status_code == 200

    def it_requires_login(book_client):
        resp = book_client.get(reverse("account:onboarding_questions"))

        assert resp.status_code in (301, 302)


def describe_onboarding_step3_routing():
    def it_routes_to_the_questions_step_when_active_questions_exist(book_client, member_user):
        RegistrationQuestion.objects.create(prompt="Experience?")
        book_client.force_login(member_user)

        resp = book_client.post(
            reverse("account:onboarding_step3"),
            {"interest_category_slugs": [], "accessibility_note": ""},
        )

        assert resp.status_code == 302
        assert resp.url == reverse("account:onboarding_questions")

    def it_routes_straight_to_overview_when_no_questions_exist(book_client, member_user):
        book_client.force_login(member_user)

        resp = book_client.post(
            reverse("account:onboarding_step3"),
            {"interest_category_slugs": [], "accessibility_note": ""},
        )

        assert resp.status_code == 302
        assert resp.url == reverse("account:overview")
