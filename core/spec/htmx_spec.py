"""Specs for reading htmx's request headers."""

from __future__ import annotations

from django.http import HttpRequest

from core.htmx import wants_fragment


def _request(**headers: str) -> HttpRequest:
    request = HttpRequest()
    for name, value in headers.items():
        request.META[name] = value
    return request


def describe_wants_fragment():
    def it_is_false_for_an_ordinary_navigation():
        assert wants_fragment(_request()) is False

    def it_is_true_for_a_plain_htmx_request():
        assert wants_fragment(_request(HTTP_HX_REQUEST="true")) is True

    def it_is_false_for_a_boosted_navigation():
        # hx-boost swaps the whole body, so a boosted arrival wants the page, not a
        # fragment. This is the half most call sites already got right.
        assert wants_fragment(_request(HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true")) is False

    def describe_a_history_restore():
        def it_is_false_even_though_the_request_is_not_boosted():
            """The half that is easy to get backwards, and did ship backwards once.

            With no htmx history cache a Back is a refetch carrying HX-Request and
            HX-History-Restore-Request but no HX-Boosted, so a check that only excludes
            boosted requests hands the member a bare fragment in place of their page.
            """
            request = _request(HTTP_HX_REQUEST="true", HTTP_HX_HISTORY_RESTORE_REQUEST="true")
            assert wants_fragment(request) is False

        def it_is_false_even_when_boosted_requests_are_counted():
            request = _request(HTTP_HX_REQUEST="true", HTTP_HX_HISTORY_RESTORE_REQUEST="true")
            assert wants_fragment(request, boosted_counts=True) is False

    def describe_when_boosted_requests_count():
        def it_is_true_for_a_boosted_request():
            # For a view no boosted navigation reaches, every htmx request is a fragment
            # request. The restore case above is still excluded.
            request = _request(HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true")
            assert wants_fragment(request, boosted_counts=True) is True

    def it_ignores_a_header_that_is_not_exactly_true():
        assert wants_fragment(_request(HTTP_HX_REQUEST="false")) is False
