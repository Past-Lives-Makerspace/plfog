"""BDD specs for the skip link hub/base.html renders on every chrome branch.

The link is the first focusable element inside ``<body>`` and targets the
``<main id="main-content">`` the same template renders, so a keyboard or screen-reader
user can jump past the sidebar and topbar (authenticated) or the public topbar
(anonymous and guest surfaces). The framed preview (``?framed=1``) renders no chrome
at all, so there is nothing to skip: it renders no link, while the landmark keeps its id.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from classes.factories import ClassOfferingFactory
from classes.models import ClassApproval, ClassOffering
from tests.membership.factories import MembershipPlanFactory, UserFactory

pytestmark = pytest.mark.django_db

SKIP_LINK = '<a href="#main-content" class="skip-link">Skip to main content</a>'
MAIN_OPEN = '<main id="main-content" class="hub-content" tabindex="-1">'
# Anything Tab can land on: the focusable elements, plus an explicit tabindex="0" or
# contenteditable on any tag, so a future extra_body override cannot slip in ahead.
_FOCUSABLE = re.compile(r'<(a|button|input|select|textarea|summary|iframe)\b|\btabindex="0"|\bcontenteditable\b')


def _body(html: str) -> str:
    return html[html.index("<body") :]


def _assert_skip_link_is_first_and_targets_main(html: str) -> None:
    body = _body(html)
    first = _FOCUSABLE.search(body)
    assert first is not None
    assert body.index(SKIP_LINK) == first.start()
    assert html.count('id="main-content"') == 1
    assert MAIN_OPEN in html


def _framed_preview(client: Client, *, framed: bool) -> str:
    offering = ClassOfferingFactory(ready=True, slug="skip-link-preview", status=ClassOffering.Status.PENDING)
    row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
    url = reverse("classes:class_review_preview", kwargs={"token": row.token})
    return client.get(f"{url}?framed=1" if framed else url).content.decode()


def describe_the_skip_link():
    def describe_on_an_authenticated_hub_page():
        def it_is_the_first_focusable_element_and_targets_main(client: Client):
            MembershipPlanFactory()
            client.force_login(UserFactory(username="skip-link@example.com"))
            html = client.get(reverse("hub_home")).content.decode()
            assert "hub-sidebar" in html
            assert "pl-topbar" in html
            _assert_skip_link_is_first_and_targets_main(html)

    def describe_on_an_anonymous_public_topbar_page():
        def it_is_the_first_focusable_element_on_the_community_calendar(client: Client):
            html = client.get(reverse("hub_community_calendar")).content.decode()
            assert "pl-public-topbar" in html
            assert "hub-sidebar" not in html
            _assert_skip_link_is_first_and_targets_main(html)
            # The calendar's own extra_head used to hide a skip link that did not exist yet.
            assert ".skip-link{display:none}" not in html

        def it_is_the_first_focusable_element_on_the_404_page(client: Client):
            response = client.get("/this-page-does-not-exist/")
            assert response.status_code == 404
            html = response.content.decode()
            assert "pl-public-topbar" in html
            _assert_skip_link_is_first_and_targets_main(html)

    def describe_on_the_framed_preview():
        def it_renders_no_link_because_there_is_no_chrome_to_skip(client: Client):
            html = _framed_preview(client, framed=True)
            assert "pl-public-topbar" not in html
            assert "hub-sidebar" not in html
            assert "skip-link" not in html
            assert 'href="#main-content"' not in html
            # The landmark keeps its id: only the link is conditional.
            assert MAIN_OPEN in html

        def it_keeps_the_link_without_the_flag(client: Client):
            html = _framed_preview(client, framed=False)
            # The class page's own base (classes/base_public.html) overrides the topbar block
            # with .cp-topbar; the skip link comes from hub/base.html's <body> either way.
            assert "cp-topbar" in html
            _assert_skip_link_is_first_and_targets_main(html)
