import pytest
from django.test import Client

from classes.factories import InstructorFactory, UserFactory


def _airtable_member(user):
    from membership.models import Member

    Member.objects.filter(user=user).update(airtable_record_id="recTEST123")
    user.__dict__.pop("member", None)
    return Member.objects.get(user=user)


@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    settings.ALLOWED_HOSTS = ["book.pastlives.space", "testserver"]
    return Client(HTTP_HOST="book.pastlives.space")


def describe_public_topbar():
    def it_shows_log_in_for_anonymous_user(book_client, db):
        resp = book_client.get("/classes/")
        assert resp.status_code == 200
        assert b">Log in<" in resp.content
        assert b"My account" not in resp.content

    def it_shows_my_account_and_log_out_for_nonmember(book_client, db):
        user = UserFactory()
        book_client.force_login(user)
        resp = book_client.get("/classes/")
        assert resp.status_code == 200
        assert b"My account" in resp.content
        assert b"Log out" in resp.content
        # Member pill is gold and uppercase "Member"; check for the class.
        assert b"pl-public-topbar__pill" not in resp.content

    def it_shows_fog_link_and_member_pill_for_airtable_member(book_client, db):
        user = UserFactory()
        _airtable_member(user)
        book_client.force_login(user)
        resp = book_client.get("/classes/")
        assert resp.status_code == 200
        assert b"My account" in resp.content
        assert b">FOG" in resp.content
        assert b"pl-public-topbar__pill" in resp.content

    def it_shows_my_account_no_pill_for_instructor_only(book_client, db):
        inst = InstructorFactory()
        book_client.force_login(inst.user)
        resp = book_client.get("/classes/")
        assert resp.status_code == 200
        assert b"My account" in resp.content
        assert b">FOG" not in resp.content
        assert b"pl-public-topbar__pill" not in resp.content
