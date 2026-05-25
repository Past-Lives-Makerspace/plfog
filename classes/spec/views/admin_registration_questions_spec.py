"""BDD specs for the admin Registration Questions CRUD views."""

from __future__ import annotations

from django.urls import reverse

from classes.models import RegistrationQuestion


def describe_admin_registration_questions():
    def it_lists_questions(admin_user, client, db):
        RegistrationQuestion.objects.create(
            prompt="How did you hear about us?",
            question_type=RegistrationQuestion.QuestionType.SHORT_TEXT,
            sort_order=1,
        )
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registration_questions"))
        assert response.status_code == 200
        assert b"How did you hear about us?" in response.content

    def it_creates_a_short_text_question(admin_user, client, db):
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_registration_question_create"),
            {
                "prompt": "Any dietary restrictions?",
                "question_type": RegistrationQuestion.QuestionType.SHORT_TEXT,
                "sort_order": 1,
            },
        )
        assert response.status_code == 302
        assert RegistrationQuestion.objects.filter(prompt="Any dietary restrictions?").exists()

    def it_creates_a_single_choice_question_with_choices(admin_user, client, db):
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_registration_question_create"),
            {
                "prompt": "Experience level?",
                "question_type": RegistrationQuestion.QuestionType.SINGLE_CHOICE,
                "choices_text": "Beginner\nIntermediate\nAdvanced",
                "sort_order": 1,
            },
        )
        assert response.status_code == 302
        q = RegistrationQuestion.objects.get(prompt="Experience level?")
        assert q.choices_json == ["Beginner", "Intermediate", "Advanced"]

    def it_rejects_single_choice_without_choices(admin_user, client, db):
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_registration_question_create"),
            {
                "prompt": "Pick one",
                "question_type": RegistrationQuestion.QuestionType.SINGLE_CHOICE,
                "choices_text": "",
                "sort_order": 1,
            },
        )
        assert response.status_code == 200
        assert b"at least one option" in response.content

    def it_edits_an_existing_question(admin_user, client, db):
        q = RegistrationQuestion.objects.create(
            prompt="Original prompt",
            question_type=RegistrationQuestion.QuestionType.SHORT_TEXT,
            sort_order=1,
        )
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_registration_question_edit", kwargs={"pk": q.pk}),
            {
                "prompt": "Updated prompt",
                "question_type": RegistrationQuestion.QuestionType.SHORT_TEXT,
                "sort_order": 2,
            },
        )
        assert response.status_code == 302
        q.refresh_from_db()
        assert q.prompt == "Updated prompt"
        assert q.sort_order == 2

    def it_deletes_a_question_on_post(admin_user, client, db):
        q = RegistrationQuestion.objects.create(
            prompt="To delete",
            question_type=RegistrationQuestion.QuestionType.SHORT_TEXT,
            sort_order=1,
        )
        client.force_login(admin_user)
        response = client.post(reverse("classes:admin_registration_question_delete", kwargs={"pk": q.pk}))
        assert response.status_code == 302
        assert not RegistrationQuestion.objects.filter(pk=q.pk).exists()

    def it_ignores_get_on_delete(admin_user, client, db):
        q = RegistrationQuestion.objects.create(
            prompt="Should survive",
            question_type=RegistrationQuestion.QuestionType.SHORT_TEXT,
            sort_order=1,
        )
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registration_question_delete", kwargs={"pk": q.pk}))
        assert response.status_code == 302
        assert RegistrationQuestion.objects.filter(pk=q.pk).exists()

    def it_gates_behind_admin_role(member_user, client, db):
        client.force_login(member_user)
        response = client.get(reverse("classes:admin_registration_questions"))
        assert response.status_code == 403
