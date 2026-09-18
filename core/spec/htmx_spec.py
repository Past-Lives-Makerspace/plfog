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

    def describe_a_boosted_post_that_redirects():
        def it_is_false_so_the_redirect_target_renders_a_whole_page():
            """The counterexample that removed the "boosted requests count" option.

            "Resume payment" is a plain form inside the boosted body, so the redirect it
            follows reaches the orientation return page carrying both headers. Treating
            that as a fragment request swapped a bare polling card into the document.
            """
            request = _request(HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true")
            assert wants_fragment(request) is False

    def it_ignores_a_header_that_is_not_exactly_true():
        assert wants_fragment(_request(HTTP_HX_REQUEST="false")) is False
