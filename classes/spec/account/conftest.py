"""Shared fixtures for the booking-surface account specs.

Onboarding lives only on the public/book surface — a request to its URLs on any
other host is redirected to ``book.pastlives.space``. These specs therefore drive
a client pinned to a public host (mirrors ``classes/spec/templates/auth_theme_spec.py``).
"""

from __future__ import annotations

import pytest
from django.test import Client


@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    settings.ALLOWED_HOSTS = ["book.pastlives.space", "members.pastlives.space"]
    return Client(HTTP_HOST="book.pastlives.space")
