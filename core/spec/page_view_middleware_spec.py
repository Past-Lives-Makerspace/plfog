"""BDD specs for the usage log's middleware (#1078): which requests write a PageView, and what a row holds."""

from __future__ import annotations

import logging

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.db import IntegrityError
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.urls import resolve, reverse

from core.middleware import page_view_name
from core.models import PageView
from membership.models import FundingSnapshot
from tests.membership.factories import FundingSnapshotFactory

pytestmark = pytest.mark.django_db

_PAGE = reverse("hub_admin_growth")


@pytest.fixture()
def admin(client: Client) -> User:
    user = User.objects.create_superuser("usage_admin", "usage_admin@example.com", "pass")
    client.force_login(user)
    return user


def _html_request(path: str, *, surface: str = "members", user: object | None = None):
    request = RequestFactory().get(path)
    request.user = user or User(pk=1, username="u")  # authenticated: a saved-looking user
    request.surface = surface
    request.resolver_match = resolve(path.split("?", 1)[0])
    return request


def describe_a_signed_in_page_view():
    def it_writes_one_row_for_a_normal_page_load(client: Client, admin: User):
        assert client.get(_PAGE).status_code == 200

        row = PageView.objects.get()
        assert (row.user, row.view_name, row.role, row.surface, row.from_app) == (
            admin,
            "hub_admin_growth",
            PageView.Role.ADMIN,
            PageView.Surface.MEMBERS,
            False,
        )

    def it_writes_one_row_for_a_boosted_navigation(client: Client, admin: User):
        client.get(_PAGE, HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true")
        assert PageView.objects.get().view_name == "hub_admin_growth"

    def it_flags_the_app_by_its_user_agent_token(client: Client, admin: User):
        client.get(_PAGE, HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) AppleWebKit PastLivesApp")
        assert PageView.objects.get().from_app is True

    def it_flags_the_android_app_by_the_webview_marker_until_the_token_ships(client: Client, admin: User):
        client.get(_PAGE, HTTP_USER_AGENT="Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/AP2A; wv) Chrome/130.0 Mobile")
        assert PageView.objects.get().from_app is True

    def it_records_the_classes_host_as_the_classes_surface():
        request = _html_request(reverse("classes:public_list"), surface="public")
        assert page_view_name(request, HttpResponse("<p>ok</p>")) == "classes:public_list"

    def it_keeps_no_path_id_or_query_string(client: Client, admin: User):
        snapshot = FundingSnapshotFactory()
        client.get(reverse("hub_admin_voting_history_detail", args=[snapshot.pk]) + "?member_type=standard&x=9137")

        row = PageView.objects.get()
        assert row.view_name == "hub_admin_voting_history_detail"
        stored = [str(getattr(row, f.attname)) for f in PageView._meta.concrete_fields if f.name not in {"id", "user"}]
        assert not any("/" in value or "?" in value or "9137" in value for value in stored)
        assert str(snapshot.pk) not in row.view_name
        assert {f.name for f in PageView._meta.concrete_fields} == {
            "id",
            "user",
            "view_name",
            "role",
            "surface",
            "from_app",
            "viewed_at",
        }
        assert FundingSnapshot.objects.count() == 1


def describe_requests_that_write_nothing():
    def it_skips_an_htmx_partial(client: Client, admin: User):
        client.get(_PAGE, HTTP_HX_REQUEST="true")
        assert not PageView.objects.exists()

    def it_skips_an_anonymous_visitor(client: Client):
        client.get(reverse("classes:public_list"))
        assert not PageView.objects.exists()

    def it_skips_the_health_check(client: Client, admin: User):
        client.get(reverse("health_check"))
        assert not PageView.objects.exists()

    def it_skips_the_api_even_as_a_browsable_html_page(client: Client, admin: User):
        client.get("/api/v1/", HTTP_ACCEPT="text/html")
        assert not PageView.objects.exists()

    def it_skips_a_static_file_path():
        request = _html_request(_PAGE)
        request.path = "/static/css/hub.css"
        assert page_view_name(request, HttpResponse("<p>ok</p>")) == ""

    def it_skips_a_redirect_and_a_non_html_response():
        request = _html_request(_PAGE)
        assert page_view_name(request, HttpResponse(status=302)) == ""
        assert page_view_name(request, HttpResponse("{}", content_type="application/json")) == ""

    def it_skips_an_anonymous_request_built_by_hand():
        request = _html_request(_PAGE)
        request.user = AnonymousUser()
        assert page_view_name(request, HttpResponse("<p>ok</p>")) == ""


def describe_a_failure_to_write():
    def it_serves_the_page_and_logs_the_error(client: Client, admin: User, monkeypatch, caplog):
        def broken(cls, **kwargs):
            raise RuntimeError("usage log down")

        monkeypatch.setattr(PageView, "record", classmethod(broken))
        with caplog.at_level(logging.ERROR, logger="core.middleware"):
            response = client.get(_PAGE)

        assert response.status_code == 200
        assert "Could not record a page view" in caplog.text

    def it_keeps_the_database_usable_after_a_failed_insert(client: Client, admin: User, monkeypatch):
        def broken(cls, **kwargs):
            PageView.objects.create(user_id=admin.pk, view_name="x", role="admin", surface="members")
            raise IntegrityError("duplicate")

        monkeypatch.setattr(PageView, "record", classmethod(broken))
        assert client.get(_PAGE).status_code == 200
        assert not PageView.objects.exists()  # the savepoint rolled the half write back
        assert client.get(_PAGE).status_code == 200
