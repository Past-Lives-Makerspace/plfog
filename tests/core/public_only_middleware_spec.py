import pytest
from django.test import Client


@pytest.fixture(autouse=True)
def _surface_hosts(settings):
    settings.ALLOWED_HOSTS = ["book.pastlives.space", "members.pastlives.space"]
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    settings.MEMBER_HOST = "members.pastlives.space"
    settings.PUBLIC_ONLY_PATH_PREFIXES = ("/account/",)


def describe_public_only_redirect():
    def it_redirects_account_to_book_when_on_members_host(db):
        c = Client(HTTP_HOST="members.pastlives.space")
        resp = c.get("/account/")
        assert resp.status_code == 302
        assert resp["Location"].startswith("http://book.pastlives.space/account/")

    def it_preserves_query_string_on_redirect(db):
        c = Client(HTTP_HOST="members.pastlives.space")
        resp = c.get("/account/", {"foo": "bar"})
        assert resp.status_code == 302
        assert resp["Location"].endswith("?foo=bar")

    def it_does_not_middleware_redirect_account_when_on_book_host(db):
        c = Client(HTTP_HOST="book.pastlives.space")
        resp = c.get("/account/")
        # The PUBLIC_ONLY_MIDDLEWARE redirects members→book for /account/ paths.
        # On the book host the middleware must NOT issue that redirect in reverse.
        # Anonymous users are relayed to the members relay endpoint by the view —
        # that is expected view behaviour, not a middleware redirect.
        location = resp.get("Location", "")
        assert not location or "/auth/relay/" in location

    def it_does_not_redirect_unrelated_paths_on_members(db):
        c = Client(HTTP_HOST="members.pastlives.space")
        resp = c.get("/classes/")
        # /classes/ is allowed on both surfaces, no cross-host redirect expected
        assert "book.pastlives.space" not in resp.get("Location", "")
