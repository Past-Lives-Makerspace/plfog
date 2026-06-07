import pytest
from django.test import Client

from classes.factories import UserFactory


@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    settings.ALLOWED_HOSTS = ["book.pastlives.space", "members.pastlives.space"]
    return Client(HTTP_HOST="book.pastlives.space")


def describe_account_routes():
    def it_redirects_anonymous_to_login_for_protected_pages(book_client, db):
        for url in ["/account/", "/account/history/", "/account/receipts/", "/account/profile/"]:
            resp = book_client.get(url)
            assert resp.status_code == 302, f"{url} returned {resp.status_code}"
            assert "/auth/relay/" in resp["Location"]

    def it_serves_each_protected_route_to_a_logged_in_user(book_client, db):
        user = UserFactory()
        book_client.force_login(user)
        for url in ["/account/", "/account/history/", "/account/receipts/", "/account/profile/"]:
            resp = book_client.get(url)
            assert resp.status_code == 200, f"{url} returned {resp.status_code}"

    def it_serves_lookup_to_anonymous(book_client, db):
        resp = book_client.get("/account/lookup/")
        assert resp.status_code == 200

    def it_renders_the_pill_tabs(book_client, db):
        user = UserFactory()
        book_client.force_login(user)
        resp = book_client.get("/account/")
        assert b'class="bk-tabs"' in resp.content
        assert b">Upcoming<" in resp.content
        assert b">Past classes<" in resp.content
        assert b">Receipts<" in resp.content
        assert b">Profile<" in resp.content

    def it_falls_back_to_local_login_when_member_host_is_not_configured(db, settings):
        settings.PUBLIC_HOSTS = ["book.pastlives.space"]
        settings.MEMBER_HOST = ""
        settings.ALLOWED_HOSTS = ["book.pastlives.space", "testserver"]
        c = Client(HTTP_HOST="book.pastlives.space")
        resp = c.get("/account/")
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]

    def it_falls_back_to_local_login_on_members_surface(db, settings):
        settings.PUBLIC_HOSTS = ["book.pastlives.space"]
        settings.MEMBER_HOST = "members.pastlives.space"
        settings.PUBLIC_ONLY_PATH_PREFIXES = ()
        settings.ALLOWED_HOSTS = ["members.pastlives.space", "testserver"]
        c = Client(HTTP_HOST="members.pastlives.space")
        resp = c.get("/account/")
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]
        assert "/auth/relay/" not in resp["Location"]
