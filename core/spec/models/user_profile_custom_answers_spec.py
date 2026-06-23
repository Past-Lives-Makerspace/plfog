"""BDD specs for UserProfile.set_custom_answers — remembered registration answers."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from core.models import UserProfile

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(db):
    User = get_user_model()
    return User.objects.create_user(username="ann@example.com", email="ann@example.com", password="x")


def describe_set_custom_answers():
    def it_stores_answers_keyed_by_string_id(user):
        profile = UserProfile.objects.create(user=user)

        profile.set_custom_answers({5: "alpha", 6: "beta"})

        profile.refresh_from_db()
        assert profile.custom_question_answers == {"5": "alpha", "6": "beta"}

    def it_merges_without_dropping_unrelated_answers(user):
        profile = UserProfile.objects.create(user=user, custom_question_answers={"5": "alpha"})

        profile.set_custom_answers({6: "beta"})

        profile.refresh_from_db()
        assert profile.custom_question_answers == {"5": "alpha", "6": "beta"}

    def it_updates_a_changed_answer(user):
        profile = UserProfile.objects.create(user=user, custom_question_answers={"5": "old"})

        profile.set_custom_answers({5: "new"})

        profile.refresh_from_db()
        assert profile.custom_question_answers == {"5": "new"}

    def it_skips_blank_and_none_values(user):
        profile = UserProfile.objects.create(user=user)

        profile.set_custom_answers({5: "", 6: None})

        profile.refresh_from_db()
        assert profile.custom_question_answers == {}

    def it_does_not_save_when_nothing_changes(user):
        profile = UserProfile.objects.create(user=user, custom_question_answers={"5": "same"})
        before = profile.updated_at

        profile.set_custom_answers({5: "same"})

        profile.refresh_from_db()
        assert profile.custom_question_answers == {"5": "same"}
        assert profile.updated_at == before  # no write happened
