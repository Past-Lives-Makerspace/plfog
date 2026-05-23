import pytest
from django.test import Client

from classes.factories import UserFactory


@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    settings.ALLOWED_HOSTS = ["book.pastlives.space", "members.pastlives.space", "testserver"]
    return Client(HTTP_HOST="book.pastlives.space")


def describe_onboarding_wizard():
    def describe_step_1_first_attendance():
        def it_renders_form_with_4_options(book_client, db):
            user = UserFactory()
            book_client.force_login(user)
            resp = book_client.get("/account/onboarding/")
            assert resp.status_code == 200
            assert b"First time at" in resp.content
            assert b"first_attendance_status" in resp.content
            assert b"first_time" in resp.content
            assert b"returning" in resp.content
            assert b"event_only" in resp.content
            assert b"unknown" in resp.content

        def it_persists_first_attendance_choice(book_client, db):
            from core.models import UserProfile

            user = UserFactory()
            book_client.force_login(user)
            resp = book_client.post("/account/onboarding/", {"first_attendance_status": "first_time"})
            assert resp.status_code == 302
            assert "/account/onboarding/2/" in resp["Location"]
            profile = UserProfile.objects.get(user=user)
            assert profile.first_attendance_status == "first_time"

    def describe_step_2_about_you():
        def it_renders_form_with_name_pronouns_phone_referral(book_client, db):
            user = UserFactory()
            book_client.force_login(user)
            resp = book_client.get("/account/onboarding/2/")
            assert resp.status_code == 200
            assert b"preferred_name" in resp.content
            assert b"pronouns" in resp.content
            assert b"phone" in resp.content
            assert b"referral_source" in resp.content

        def it_persists_step_2_values(book_client, db):
            from core.models import UserProfile

            user = UserFactory()
            book_client.force_login(user)
            book_client.post(
                "/account/onboarding/2/",
                {
                    "preferred_name": "Avery",
                    "pronouns": "they/them",
                    "phone": "(503) 555-0146",
                    "referral_source": "instagram",
                },
            )
            profile = UserProfile.objects.get(user=user)
            assert profile.preferred_name == "Avery"
            assert profile.referral_source == "instagram"

    def describe_step_3_interests():
        def it_renders_chips_for_categories(book_client, db):
            from classes.models import Category

            Category.objects.create(name="Blacksmithing", slug="blacksmithing")
            Category.objects.create(name="Ceramics", slug="ceramics")
            user = UserFactory()
            book_client.force_login(user)
            resp = book_client.get("/account/onboarding/3/")
            assert resp.status_code == 200
            assert b"Blacksmithing" in resp.content
            assert b"Ceramics" in resp.content

        def it_marks_user_onboarded_and_redirects_to_overview(book_client, db):
            from core.models import UserProfile

            user = UserFactory()
            book_client.force_login(user)
            resp = book_client.post(
                "/account/onboarding/3/",
                {
                    "interest_category_slugs": ["blacksmithing", "ceramics"],
                    "accessibility_note": "wheelchair access",
                },
            )
            assert resp.status_code == 302
            assert "/account/" in resp["Location"]
            profile = UserProfile.objects.get(user=user)
            assert profile.onboarding_completed_at is not None
            assert profile.is_onboarded is True
            assert set(profile.interest_category_slugs) == {"blacksmithing", "ceramics"}
            assert profile.accessibility_note == "wheelchair access"

    def it_skip_link_lands_on_overview(book_client, db):
        user = UserFactory()
        book_client.force_login(user)
        resp = book_client.get("/account/onboarding/")
        assert b"Skip for now" in resp.content
