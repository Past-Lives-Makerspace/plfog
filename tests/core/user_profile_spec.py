import pytest

from classes.factories import UserFactory


def describe_UserProfile_model():
    def it_persists_pronouns_and_phone(db):
        from core.models import UserProfile

        user = UserFactory()
        profile = UserProfile.objects.create(user=user, pronouns="they/them", phone="(503) 555-0146")
        profile.refresh_from_db()
        assert profile.pronouns == "they/them"
        assert profile.phone == "(503) 555-0146"

    def it_has_one_per_user(db):
        from django.db import IntegrityError

        from core.models import UserProfile

        user = UserFactory()
        UserProfile.objects.create(user=user)
        with pytest.raises(IntegrityError):
            UserProfile.objects.create(user=user)

    def it_str_includes_user_email(db):
        from core.models import UserProfile

        user = UserFactory(email="x@y.com", username="x@y.com")
        profile = UserProfile.objects.create(user=user)
        assert "x@y.com" in str(profile)
