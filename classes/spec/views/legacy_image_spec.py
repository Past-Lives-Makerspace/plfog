"""BDD specs for the legacy_image proxy view."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import RequestFactory


def describe_legacy_image():
    def it_returns_403_when_url_is_not_from_allowed_domain(db):
        from classes.views_legacy_image import legacy_image

        factory = RequestFactory()
        request = factory.get("/_legacy-image/", {"url": "https://evil.example.com/img.jpg"})
        response = legacy_image(request)
        assert response.status_code == 403

    def it_returns_403_when_url_param_is_missing(db):
        from classes.views_legacy_image import legacy_image

        factory = RequestFactory()
        request = factory.get("/_legacy-image/")
        response = legacy_image(request)
        assert response.status_code == 403

    def it_fetches_and_returns_image_from_allowed_domain(db):
        from classes.views_legacy_image import legacy_image

        factory = RequestFactory()
        url = "https://classes.pastlives.space/sites/default/files/img.jpg"
        request = factory.get("/_legacy-image/", {"url": url})

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"fake-image-data"
        mock_resp.headers = {"Content-Type": "image/jpeg"}
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("classes.views_legacy_image._OPENER.open", return_value=mock_resp):
            with patch("django.core.cache.cache.get", return_value=None):
                with patch("django.core.cache.cache.set"):
                    response = legacy_image(request)

        assert response.status_code == 200
        assert response.content == b"fake-image-data"

    def it_returns_404_when_upstream_fetch_fails(db):
        from classes.views_legacy_image import legacy_image

        factory = RequestFactory()
        url = "https://classes.pastlives.space/sites/default/files/missing.jpg"
        request = factory.get("/_legacy-image/", {"url": url})

        with patch("classes.views_legacy_image._OPENER.open", side_effect=Exception("connection refused")):
            with patch("django.core.cache.cache.get", return_value=None):
                response = legacy_image(request)

        assert response.status_code == 404

    def it_returns_cached_response_without_fetching(db):
        from classes.views_legacy_image import legacy_image

        factory = RequestFactory()
        url = "https://classes.pastlives.space/sites/default/files/cached.jpg"
        request = factory.get("/_legacy-image/", {"url": url})

        cached_data = ("image/png", b"cached-bytes")
        with patch("django.core.cache.cache.get", return_value=cached_data):
            with patch("classes.views_legacy_image._OPENER.open") as mock_fetch:
                response = legacy_image(request)
                mock_fetch.assert_not_called()

        assert response.status_code == 200
        assert response.content == b"cached-bytes"

    def it_no_redirect_handler_refuses_all_redirects():
        from classes.views_legacy_image import _NoRedirect

        handler = _NoRedirect()
        result = handler.redirect_request(None, None, 301, "Moved", {}, "https://evil.example.com/")
        assert result is None


def describe_legacy_image_self_host_guard():
    def it_refuses_to_fetch_a_hostname_this_app_answers_on(db, settings):
        from classes.views_legacy_image import legacy_image

        # The DNS cutover: classes.pastlives.space now points at this app. Proxying it
        # would make the app request itself, once per card on the catalog page.
        settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "classes.pastlives.space"]
        request = RequestFactory().get(
            "/_legacy-image/",
            {"url": "https://classes.pastlives.space/sites/default/files/img.jpg"},
        )

        with patch("classes.views_legacy_image._OPENER.open") as mock_fetch:
            response = legacy_image(request)
            mock_fetch.assert_not_called()

        assert response.status_code == 404

    def it_refuses_to_fetch_the_host_serving_the_current_request(db, settings):
        from classes.views_legacy_image import legacy_image

        # A wildcard ALLOWED_HOSTS names nothing, so the live request's own host is the
        # only thing left that can tell the view it is about to call itself.
        settings.ALLOWED_HOSTS = ["*"]
        request = RequestFactory().get(
            "/_legacy-image/",
            {"url": "https://classes.pastlives.space/sites/default/files/img.jpg"},
            HTTP_HOST="classes.pastlives.space",
        )

        with patch("classes.views_legacy_image._OPENER.open") as mock_fetch:
            response = legacy_image(request)
            mock_fetch.assert_not_called()

        assert response.status_code == 404

    def it_still_fetches_while_the_legacy_host_is_somebody_else(db, settings):
        from classes.views_legacy_image import legacy_image

        settings.ALLOWED_HOSTS = ["testserver", "book.pastlives.space", "*"]
        request = RequestFactory().get(
            "/_legacy-image/",
            {"url": "https://classes.pastlives.space/sites/default/files/img.jpg"},
        )

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"still-drupal"
        mock_resp.headers = {"Content-Type": "image/jpeg"}
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with (
            patch("classes.views_legacy_image._OPENER.open", return_value=mock_resp),
            patch("django.core.cache.cache.get", return_value=None),
            patch("django.core.cache.cache.set"),
        ):
            response = legacy_image(request)

        assert response.status_code == 200
        assert response.content == b"still-drupal"

    def it_treats_an_empty_hostname_as_not_ours():
        from classes.views_legacy_image import _is_own_host

        request = RequestFactory().get("/_legacy-image/")

        assert _is_own_host("", request) is False

    def it_arms_the_guard_from_a_wildcard_subdomain_entry(db, settings):
        from classes.views_legacy_image import legacy_image

        # ``*.pastlives.space`` covers classes.pastlives.space. The original guard
        # collapsed the entry to the bare domain and compared for equality, so the
        # subdomain that actually needed protecting never matched.
        settings.ALLOWED_HOSTS = ["testserver", "*.pastlives.space"]
        request = RequestFactory().get(
            "/_legacy-image/",
            {"url": "https://classes.pastlives.space/sites/default/files/img.jpg"},
        )

        with patch("classes.views_legacy_image._OPENER.open") as mock_fetch:
            response = legacy_image(request)
            mock_fetch.assert_not_called()

        assert response.status_code == 404

    def it_arms_the_guard_from_a_leading_dot_subdomain_entry(db, settings):
        from classes.views_legacy_image import _is_own_host

        # Django also accepts the ``.example.com`` spelling for the same wildcard.
        settings.ALLOWED_HOSTS = ["testserver", ".pastlives.space"]
        request = RequestFactory().get("/_legacy-image/")

        assert _is_own_host("classes.pastlives.space", request) is True
        assert _is_own_host("pastlives.space", request) is True

    def it_does_not_treat_a_lookalike_domain_as_ours(db, settings):
        from classes.views_legacy_image import _is_own_host

        # Suffix matching must not be a bare ``endswith``: evilpastlives.space is not
        # a subdomain of pastlives.space.
        settings.ALLOWED_HOSTS = ["testserver", "*.pastlives.space"]
        request = RequestFactory().get("/_legacy-image/")

        assert _is_own_host("evilpastlives.space", request) is False
