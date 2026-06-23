"""BDD specs for the register page: remembered answers + the account choice band."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import RegistrationFactory
from classes.models import RegistrationAnswer, RegistrationQuestion
from classes.spec.views.register_spec import _post_data
from core.models import UserProfile

pytestmark = pytest.mark.django_db


def describe_register_remembers_answers():
    def it_saves_answers_to_the_profile_for_a_logged_in_registrant(free_offering, client, member_user):
        question = RegistrationQuestion.objects.create(prompt="Experience?")
        client.force_login(member_user)
        data = _post_data(**{f"custom_q_{question.pk}": "Beginner"})

        resp = client.post(reverse("classes:register", kwargs={"slug": free_offering.slug}), data=data)

        assert resp.status_code == 302
        profile = UserProfile.objects.get(user=member_user)
        assert profile.custom_question_answers == {str(question.pk): "Beginner"}

    def it_prefills_a_logged_in_get_from_the_profile(free_offering, client, member_user):
        question = RegistrationQuestion.objects.create(prompt="Experience?")
        UserProfile.objects.create(user=member_user, custom_question_answers={str(question.pk): "Pro"})
        client.force_login(member_user)

        resp = client.get(reverse("classes:register", kwargs={"slug": free_offering.slug}))

        assert resp.context["answers_prefilled"] is True
        assert resp.context["form"][f"custom_q_{question.pk}"].value() == "Pro"

    def it_prefills_a_guest_from_a_prior_booking_by_email(free_offering, client):
        question = RegistrationQuestion.objects.create(prompt="Experience?")
        prior = RegistrationFactory(email="repeat@example.com")
        RegistrationAnswer.objects.create(registration=prior, question=question, answer_text="Some")

        resp = client.get(
            reverse("classes:register", kwargs={"slug": free_offering.slug}),
            {"email": "repeat@example.com"},
        )

        assert resp.context["answers_prefilled"] is True
        assert resp.context["form"][f"custom_q_{question.pk}"].value() == "Some"
        # The transparency note is shown rather than filling silently.
        assert "previous booking" in resp.content.decode()

    def it_does_not_flag_prefill_for_a_fresh_guest(free_offering, client):
        RegistrationQuestion.objects.create(prompt="Experience?")

        resp = client.get(reverse("classes:register", kwargs={"slug": free_offering.slug}))

        assert resp.context["answers_prefilled"] is False

    def it_wires_the_email_field_for_htmx_recall_when_questions_exist(free_offering, client):
        RegistrationQuestion.objects.create(prompt="Experience?")

        resp = client.get(reverse("classes:register", kwargs={"slug": free_offering.slug}))

        assert 'id="custom-questions-block"' in resp.content.decode()


def describe_register_account_band():
    def it_shows_login_and_signup_links_for_anonymous_visitors(free_offering, client):
        resp = client.get(reverse("classes:register", kwargs={"slug": free_offering.slug}))

        body = resp.content.decode()
        assert "reg-account-band" in body
        assert reverse("account_login") in body
        assert reverse("account_signup") in body

    def it_hides_the_band_for_a_logged_in_visitor(free_offering, client, member_user):
        client.force_login(member_user)

        resp = client.get(reverse("classes:register", kwargs={"slug": free_offering.slug}))

        assert "reg-account-band" not in resp.content.decode()
