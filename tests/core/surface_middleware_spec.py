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
            "/classes/teach/",
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

    def describe_canonical_domain_redirect():
        # The .app hosts must stay in ALLOWED_HOSTS so the request reaches the
        # middleware at all (get_host validates); we then 301 it to the member host.
        _APP_HOSTS = override_settings(
            ALLOWED_HOSTS=["pastlives.app", "www.pastlives.app", "members.pastlives.space"],
            MEMBER_HOST="members.pastlives.space",
        )

        @_APP_HOSTS
        def it_301s_pastlives_app_to_the_member_host():
            request, middleware = _build("pastlives.app", "/guilds/")
            response = middleware(request)
            assert response.status_code == 301
            assert response["Location"] == "https://members.pastlives.space/guilds/"

        @_APP_HOSTS
        def it_301s_the_www_variant_too():
            request, middleware = _build("www.pastlives.app", "/")
            response = middleware(request)
            assert response.status_code == 301
            assert response["Location"] == "https://members.pastlives.space/"

        @_APP_HOSTS
        def it_preserves_the_query_string():
            request, middleware = _build("pastlives.app", "/members/", query="tab=profile")
            response = middleware(request)
            assert response.status_code == 301
            assert response["Location"] == "https://members.pastlives.space/members/?tab=profile"

        @_APP_HOSTS
        def it_does_not_redirect_the_member_host():
            request, middleware = _build("members.pastlives.space", "/guilds/")
            response = middleware(request)
            assert response.status_code == 200

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
                "/classes/teach/",
                "/classes/teach/profile/",
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
            # /classes/instructors/ (plural) is public bios; /classes/teach/ is the teaching dashboard.
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
            # Edge: /classes/instructors/ (plural public bios) is not member-only — only /classes/teach/ is.
            request, middleware = _build("book.pastlives.space", "/classes/instructors/")
            response = middleware(request)
            assert response.status_code == 200

    def describe_legacy_instructor_redirect():
        def it_301_redirects_old_instructor_dashboard_to_teach_on_members_surface():
            request, middleware = _build("members.pastlives.space", "/classes/instructor/")
            response = middleware(request)
            # The legacy redirect is handled by Django's URL routing, not the middleware itself.
            # Middleware passes the request through (200 from the _ok handler) since
            # /classes/instructor/ is no longer a MEMBER_ONLY prefix.
            # The actual 301 is issued by RedirectView in classes/urls.py.
            assert response.status_code == 200


def describe_guilds_surface():
    @pytest.fixture(autouse=True)
    def _guilds_settings():
        with override_settings(
            ALLOWED_HOSTS=[
                "guilds.pastlives.app",
                "book.pastlives.space",
                "members.pastlives.space",
                "testserver",
            ],
            GUILDS_HOSTS=["guilds.pastlives.app"],
            GUILDS_BASE_URL="https://guilds.pastlives.app",
            PUBLIC_HOSTS=["book.pastlives.space"],
            MEMBER_HOST="members.pastlives.space",
        ):
            yield

    def it_sets_surface_to_guilds_for_the_guilds_host():
        request, middleware = _build("guilds.pastlives.app", "/guilds/")
        middleware(request)
        assert request.surface == "guilds"

    def it_redirects_root_to_the_directory():
        request, middleware = _build("guilds.pastlives.app", "/")
        response = middleware(request)
        assert response.status_code == 302
        assert response["Location"] == "/guilds/"

    @pytest.mark.parametrize(
        "path",
        [
            "/guilds/",
            "/guilds/woodworking/",
            "/guilds/1/",
            "/orientation/slots/1/book/",
            "/guilds/1/orientation/request-custom/",
            "/orientation/bookings/1/cancel/",
            "/spaces/",
            "/help/",
            "/accounts/login/",
            "/accounts/signup/",
            "/accounts/login/code/",
            "/accounts/login/code/confirm/",
        ],
    )
    def it_allows_guest_appropriate_views(path):
        request, middleware = _build("guilds.pastlives.app", path)
        response = middleware(request)
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "path",
        [
            "/settings/",  # hub_user_settings
            "/members/",  # hub_member_directory
            "/guilds/1/edit/",  # guild editor
            "/guilds/1/products/add/",  # product admin
            "/guilds/1/cart/confirm/",  # cart
            "/account/lookup/",  # book-only namespaced account view
        ],
    )
    def it_404s_views_that_are_not_guest_appropriate(path):
        request, middleware = _build("guilds.pastlives.app", path)
        with pytest.raises(Http404):
            middleware(request)

    def it_404s_a_path_that_does_not_resolve():
        request, middleware = _build("guilds.pastlives.app", "/totally/unknown/xyz/")
        with pytest.raises(Http404):
            middleware(request)


def describe_signage_surface():
    @pytest.fixture(autouse=True)
    def _signage_settings():
        with override_settings(
            ALLOWED_HOSTS=[
                "slideshow.pastlives.space",
                "members.pastlives.space",
                "testserver",
            ],
            SIGNAGE_HOSTS=["slideshow.pastlives.space"],
            SIGNAGE_BASE_URL="https://slideshow.pastlives.space",
            MEMBER_HOST="members.pastlives.space",
        ):
            yield

    def it_sets_surface_to_signage_for_the_signage_host(db):
        request, middleware = _build("slideshow.pastlives.space", "/woodshop/")
        middleware(request)
        assert request.surface == "signage"

    def it_redirects_root_to_the_first_enabled_zone(db):
        from membership.models import SlideshowZone

        SlideshowZone.objects.create(name="Lobby", slug="lobby", sort_order=1)
        SlideshowZone.objects.create(name="Woodshop", slug="woodshop", sort_order=0)
        request, middleware = _build("slideshow.pastlives.space", "/")
        response = middleware(request)
        assert response.status_code == 302
        assert response["Location"] == "/woodshop/"

    def it_skips_disabled_zones_when_picking_the_root_target(db):
        from membership.models import SlideshowZone

        SlideshowZone.objects.create(name="Woodshop", slug="woodshop", sort_order=0, is_enabled=False)
        SlideshowZone.objects.create(name="Lobby", slug="lobby", sort_order=1)
        request, middleware = _build("slideshow.pastlives.space", "/")
        response = middleware(request)
        assert response.status_code == 302
        assert response["Location"] == "/lobby/"

    def it_renders_a_holding_page_at_root_when_no_zones_exist(db):
        request, middleware = _build("slideshow.pastlives.space", "/")
        response = middleware(request)
        assert response.status_code == 200
        assert b"No screens configured yet" in response.content

    @pytest.mark.parametrize("path", ["/woodshop/", "/woodshop/deck/"])
    def it_allows_the_player_and_deck_views(db, path):
        request, middleware = _build("slideshow.pastlives.space", path)
        response = middleware(request)
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "path",
        [
            "/settings/",  # hub_user_settings
            "/members/",  # hub_member_directory
            "/guilds/1/edit/",  # guild editor
            "/guilds/1/products/add/",  # product admin
        ],
    )
    def it_404s_views_that_are_not_kiosk_views(db, path):
        request, middleware = _build("slideshow.pastlives.space", path)
        with pytest.raises(Http404):
            middleware(request)

    def it_404s_a_path_that_does_not_resolve(db):
        request, middleware = _build("slideshow.pastlives.space", "/totally/unknown/xyz/")
        with pytest.raises(Http404):
            middleware(request)


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
