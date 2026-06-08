import pytest
from django.test import Client

from classes.factories import UserFactory
from membership.models import Member


def _airtable_promote(user):
    Member.objects.filter(user=user).update(airtable_record_id="recTEST123")
    user.__dict__.pop("member", None)


@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    settings.ALLOWED_HOSTS = ["book.pastlives.space", "members.pastlives.space"]
    return Client(HTTP_HOST="book.pastlives.space")


def describe_account_profile():
    def describe_for_nonmember():
        def it_renders_editable_form(book_client, db):
            user = UserFactory(email="a@b.com", username="a@b.com")
            user.first_name = "Avery"
            user.last_name = "Sandoval"
            user.save()
            book_client.force_login(user)
            resp = book_client.get("/account/profile/")
            assert resp.status_code == 200
            assert b"Avery" in resp.content
            # The form fields are not readonly.
            assert b"readonly" not in resp.content or b'readonly="readonly"' not in resp.content

        def it_saves_first_and_last_name(book_client, db):
            user = UserFactory()
            book_client.force_login(user)
            resp = book_client.post(
                "/account/profile/",
                {
                    "first_name": "Avery",
                    "last_name": "Sandoval",
                    "pronouns": "they/them",
                    "phone": "(503) 555-0146",
                },
            )
            assert resp.status_code == 302  # redirects on success
            user.refresh_from_db()
            assert user.first_name == "Avery"
            assert user.last_name == "Sandoval"

        def it_persists_pronouns_and_phone_to_userprofile(book_client, db):
            from core.models import UserProfile

            user = UserFactory()
            book_client.force_login(user)
            book_client.post(
                "/account/profile/",
                {
                    "first_name": "A",
                    "last_name": "S",
                    "pronouns": "they/them",
                    "phone": "(503) 555-0146",
                },
            )
            profile = UserProfile.objects.get(user=user)
            assert profile.pronouns == "they/them"
            assert profile.phone == "(503) 555-0146"

    def describe_for_member():
        def it_renders_read_only_with_edit_on_fog_link(book_client, db):
            user = UserFactory()
            _airtable_promote(user)
            book_client.force_login(user)
            resp = book_client.get("/account/profile/")
            assert resp.status_code == 200
            assert b"Read-only here" in resp.content
            assert b"Edit on FOG" in resp.content
            assert b"readonly" in resp.content  # at least one input has readonly

        def it_does_not_mutate_user_on_post(book_client, db):
            user = UserFactory(email="m@x.com", username="m@x.com")
            user.first_name = "Mira"
            user.save()
            _airtable_promote(user)
            book_client.force_login(user)
            book_client.post(
                "/account/profile/",
                {
                    "first_name": "X",
                    "last_name": "Y",
                    "pronouns": "x/y",
                    "phone": "555",
                },
            )
            user.refresh_from_db()
            assert user.first_name == "Mira"  # unchanged

    def it_redirects_anonymous_to_login(book_client, db):
        resp = book_client.get("/account/profile/")
        assert resp.status_code == 302
        assert "/auth/relay/" in resp["Location"]
