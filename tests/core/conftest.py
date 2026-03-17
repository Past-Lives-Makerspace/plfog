"""Shared fixtures for core app tests."""

import pytest
from django.contrib.auth import get_user_model


@pytest.fixture()
def authenticated_client(client):
    """Create an authenticated client for testing."""
    User = get_user_model()
    user = User.objects.create_user(
        username="testuser",
        email="test@example.com",
        password="testpass123",
    )
    client.force_login(user)
    return client
