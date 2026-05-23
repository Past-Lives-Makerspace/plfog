"""BDD-style tests for core.middleware.SurfaceMiddleware."""

from __future__ import annotations

import pytest
from django.http import Http404, HttpResponse
from django.test import RequestFactory, override_settings

from core.middleware import SurfaceMiddleware


@pytest.fixture(autouse=True)
def _surface_settings():
    with override_settings(
        ALLOWED_HOSTS=[
            "book.pastlives.space",
            "members.pastlives.space",
            "pastlives.plaza.codes",
            "localhost",
            "testserver",
        ],
        PUBLIC_HOSTS=["book.pastlives.space"],
        MEMBER_HOST="members.pastlives.space",
        MEMBER_ONLY_PATH_PREFIXES=(
            "/admin/",
            "/billing/",
            "/classes/admin/",
            "/classes/instructor/",
            "/settings/",
            "/tab/",
        ),
    ):
        yield


def _ok(request):
    return HttpResponse("ok")


def _build(host: str, path: str = "/", secure: bool = False, query: str = ""):
    rf = RequestFactory()
    request = rf.get(path, secure=secure, QUERY_STRING=query, HTTP_HOST=host)
    middleware = SurfaceMiddleware(_ok)
    return request, middleware


def describe_SurfaceMiddleware():
    def describe_surface_attribute():
        def it_sets_surface_to_public_for_book_host():
            request, middleware = _build("book.pastlives.space", "/classes/")
            middleware(request)
            assert request.surface == "public"

        def it_sets_surface_to_members_for_members_host():
            request, middleware = _build("members.pastlives.space", "/")
            middleware(request)
            assert request.surface == "members"

        def it_sets_surface_to_members_for_localhost():
            request, middleware = _build("localhost", "/")
            middleware(request)
            assert request.surface == "members"

        def it_sets_surface_to_members_for_hetzner_staging():
            request, middleware = _build("pastlives.plaza.codes", "/")
            middleware(request)
            assert request.surface == "members"

        def it_ignores_port_when_matching_host():
            request, middleware = _build("book.pastlives.space:8000", "/classes/")
            middleware(request)
            assert request.surface == "public"

        def it_matches_host_case_insensitively():
            request, middleware = _build("BOOK.PastLives.Space", "/classes/")
            middleware(request)
            assert request.surface == "public"

    def describe_root_path_on_public():
        def it_redirects_root_to_classes_catalog():
            request, middleware = _build("book.pastlives.space", "/")
            response = middleware(request)
            assert response.status_code == 302
            assert response["Location"] == "/classes/"

        def it_does_not_redirect_root_on_members_host():
            request, middleware = _build("members.pastlives.space", "/")
            response = middleware(request)
            assert response.status_code == 200

    def describe_accounts_on_public():
        def it_serves_accounts_in_place_on_book_host():
            # Allauth now runs on both surfaces — no cross-host redirect.
            request, middleware = _build("book.pastlives.space", "/accounts/login/")
            response = middleware(request)
            assert response.status_code == 200

        def it_serves_accounts_with_query_string_in_place():
            request, middleware = _build("book.pastlives.space", "/accounts/login/", query="next=/classes/")
            response = middleware(request)
            assert response.status_code == 200

        def it_serves_accounts_on_secure_request_in_place():
            request, middleware = _build("book.pastlives.space", "/accounts/login/", secure=True)
            response = middleware(request)
            assert response.status_code == 200

        def it_does_not_redirect_accounts_on_members_host():
            request, middleware = _build("members.pastlives.space", "/accounts/login/")
            response = middleware(request)
            assert response.status_code == 200

    def describe_member_only_prefixes_on_public():
        @pytest.mark.parametrize(
            "path",
            [
                "/admin/",
                "/admin/anything/",
                "/billing/",
                "/billing/payment-method/",
                "/classes/admin/",
                "/classes/admin/new/",
                "/classes/instructor/",
                "/classes/instructor/profile/",
                "/settings/",
                "/settings/profile/",
                "/tab/",
                "/tab/history/",
            ],
        )
        def it_raises_404_for_member_only_paths(path):
            request, middleware = _build("book.pastlives.space", path)
            with pytest.raises(Http404):
                middleware(request)

        def it_lets_public_classes_paths_through_on_book():
            request, middleware = _build("book.pastlives.space", "/classes/")
            response = middleware(request)
            assert response.status_code == 200

        def it_lets_public_class_detail_through_on_book():
            request, middleware = _build("book.pastlives.space", "/classes/some-class/")
            response = middleware(request)
            assert response.status_code == 200

        def it_lets_instructor_bio_through_on_book_not_dashboard():
            # /classes/instructors/ (plural) is public bios; /classes/instructor/ (singular) is the dashboard.
            request, middleware = _build("book.pastlives.space", "/classes/instructors/jane/")
            response = middleware(request)
            assert response.status_code == 200

        def it_lets_self_serve_registration_through_on_book():
            request, middleware = _build("book.pastlives.space", "/classes/my/some-token/")
            response = middleware(request)
            assert response.status_code == 200

        def it_does_not_404_member_only_paths_on_members_host():
            request, middleware = _build("members.pastlives.space", "/billing/")
            response = middleware(request)
            assert response.status_code == 200

    def describe_member_only_prefix_boundaries():
        def it_does_not_match_classes_instructors_plural_as_member_only():
            # Edge: ensure startswith("/classes/instructor/") does not match
            # the plural public bio path "/classes/instructors/...".
            request, middleware = _build("book.pastlives.space", "/classes/instructors/")
            response = middleware(request)
            assert response.status_code == 200


def describe_handle_members_surface_early_returns():
    def it_passes_through_when_public_only_prefixes_is_empty():
        with override_settings(
            PUBLIC_ONLY_PATH_PREFIXES=(),
            PUBLIC_HOSTS=["book.pastlives.space"],
            MEMBER_HOST="members.pastlives.space",
            MEMBER_ONLY_PATH_PREFIXES=(),
        ):
            request, middleware = _build("members.pastlives.space", "/some/path/")
            response = middleware(request)
            assert response.status_code == 200

    def it_passes_through_when_public_hosts_is_empty():
        with override_settings(
            PUBLIC_ONLY_PATH_PREFIXES=("/account/",),
            PUBLIC_HOSTS=[],
            MEMBER_HOST="members.pastlives.space",
            MEMBER_ONLY_PATH_PREFIXES=(),
        ):
            request, middleware = _build("members.pastlives.space", "/account/")
            response = middleware(request)
            assert response.status_code == 200

    def it_redirects_public_only_path_on_members_surface_to_book_host():
        with override_settings(
            PUBLIC_ONLY_PATH_PREFIXES=("/account/",),
            PUBLIC_HOSTS=["book.pastlives.space"],
            MEMBER_HOST="members.pastlives.space",
            MEMBER_ONLY_PATH_PREFIXES=(),
            ALLOWED_HOSTS=["book.pastlives.space", "members.pastlives.space", "testserver"],
        ):
            request, middleware = _build("members.pastlives.space", "/account/overview/")
            response = middleware(request)
            assert response.status_code == 302
            assert "book.pastlives.space" in response["Location"]
            assert "/account/overview/" in response["Location"]
