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

        with patch("urllib.request.urlopen", return_value=mock_resp):
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

        with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
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
            with patch("urllib.request.urlopen") as mock_fetch:
                response = legacy_image(request)
                mock_fetch.assert_not_called()

        assert response.status_code == 200
        assert response.content == b"cached-bytes"
