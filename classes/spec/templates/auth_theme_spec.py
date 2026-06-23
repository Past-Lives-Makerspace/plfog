"""The booking auth/onboarding pages must stay inside the themed token scope.

CSS appearance is not unit-testable, but the structural contract is: every
booking auth/onboarding page renders the shared ``.bk-auth-surface`` wrapper
(so the ``--bk-*`` tokens resolve), and the dark-glass card background is only
declared under a ``[data-theme="dark"]`` selector (so light mode stays light).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse


@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    settings.ALLOWED_HOSTS = ["book.pastlives.space", "members.pastlives.space"]
    return Client(HTTP_HOST="book.pastlives.space")


def describe_booking_auth_theming():
    def it_renders_login_inside_the_themed_surface(book_client, db):
        resp = book_client.get(reverse("account_login"))
        assert resp.status_code == 200
        assert "bk-auth-surface" in resp.content.decode()

    def it_renders_signup_inside_the_themed_surface(book_client, db):
        resp = book_client.get(reverse("account_signup"))
        assert resp.status_code == 200
        assert "bk-auth-surface" in resp.content.decode()


def describe_book_account_css():
    def it_drives_the_base_auth_card_from_theme_tokens():
        css = Path("static/css/book-account.css").read_text()
        base = css.split('[data-theme="dark"] .bk-auth-card', 1)[0]
        base_rule = base.rsplit(".bk-auth-card {", 1)[1]
        assert "var(--bk-card-bg)" in base_rule
        assert "rgba(7,30,50,.78)" not in base_rule

    def it_only_paints_the_dark_glass_card_under_a_dark_selector():
        css = Path("static/css/book-account.css").read_text()
        for idx in _all_indexes(css, "rgba(7,30,50,.78)"):
            selector = css.rfind("{", 0, idx)
            line_start = css.rfind("\n", 0, selector)
            assert '[data-theme="dark"]' in css[line_start:selector]


def _all_indexes(text: str, needle: str) -> list[int]:
    out: list[int] = []
    start = text.find(needle)
    while start != -1:
        out.append(start)
        start = text.find(needle, start + 1)
    return out
