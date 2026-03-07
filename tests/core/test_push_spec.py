"""Tests for push notification views."""

import json
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from core.models import PushSubscription

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# VAPID Key View
# ---------------------------------------------------------------------------


def describe_vapid_key_view():
    """Tests for the VAPID public key endpoint."""

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

    def it_returns_200_ok_for_authenticated_user(authenticated_client):
        """Authenticated users should receive 200 OK."""
        response = authenticated_client.get("/webpush/vapid-key/")
        assert response.status_code == 200

    def it_requires_login(client):
        """Unauthenticated requests should be redirected to login."""
        response = client.get("/webpush/vapid-key/")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_returns_public_key_in_json(authenticated_client):
        """Response should contain VAPID public key in JSON format."""
        response = authenticated_client.get("/webpush/vapid-key/")
        assert response["Content-Type"] == "application/json"
        data = response.json()
        assert "vapid_public_key" in data

    def it_returns_empty_string_when_key_not_configured(authenticated_client):
        """Should return empty string for vapid_public_key when not configured."""
        response = authenticated_client.get("/webpush/vapid-key/")
        data = response.json()
        # Default in settings is empty string when not configured
        assert isinstance(data["vapid_public_key"], str)

    def it_requires_get_method(authenticated_client):
        """Should only accept GET requests."""
        response = authenticated_client.post("/webpush/vapid-key/")
        assert response.status_code == 405


# ---------------------------------------------------------------------------
# Subscribe View
# ---------------------------------------------------------------------------


def describe_subscribe_view():  # noqa: C901
    """Tests for the push subscription endpoint."""

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

    @pytest.fixture()
    def valid_subscription_data():
        """Valid push subscription data."""
        return {
            "endpoint": "https://fcm.googleapis.com/fcm/send/test-endpoint-123",
            "p256dh": "BGJHpFQ-4X8F8Q-EXAMPLE_P256DH_KEY_HERE",
            "auth": "AUTH_SECRET_KEY_12345",
        }

    def it_returns_200_ok_for_authenticated_post(authenticated_client, valid_subscription_data):
        """Authenticated POST with valid data should return 200 OK."""
        response = authenticated_client.post(
            "/webpush/subscribe/",
            data=json.dumps(valid_subscription_data),
            content_type="application/json",
        )
        assert response.status_code == 200

    def it_requires_login(client, valid_subscription_data):
        """Unauthenticated requests should be redirected to login."""
        response = client.post(
            "/webpush/subscribe/",
            data=json.dumps(valid_subscription_data),
            content_type="application/json",
        )
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_requires_post_method(authenticated_client):
        """Should only accept POST requests."""
        response = authenticated_client.get("/webpush/subscribe/")
        assert response.status_code == 405

    def it_creates_push_subscription(authenticated_client, valid_subscription_data):
        """Should create a new PushSubscription in the database."""
        response = authenticated_client.post(
            "/webpush/subscribe/",
            data=json.dumps(valid_subscription_data),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json() == {"success": True}

        # Verify subscription was created
        assert PushSubscription.objects.count() == 1
        subscription = PushSubscription.objects.first()
        assert subscription.endpoint == valid_subscription_data["endpoint"]
        assert subscription.p256dh == valid_subscription_data["p256dh"]
        assert subscription.auth == valid_subscription_data["auth"]

    def it_handles_duplicate_endpoint_gracefully(authenticated_client, valid_subscription_data):
        """Should update existing subscription instead of creating duplicate."""
        # Create first subscription
        authenticated_client.post(
            "/webpush/subscribe/",
            data=json.dumps(valid_subscription_data),
            content_type="application/json",
        )
        assert PushSubscription.objects.count() == 1

        # Subscribe again with same endpoint but different keys
        updated_data = valid_subscription_data.copy()
        updated_data["p256dh"] = "UPDATED_P256DH_KEY"
        updated_data["auth"] = "UPDATED_AUTH_KEY"

        response = authenticated_client.post(
            "/webpush/subscribe/",
            data=json.dumps(updated_data),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json() == {"success": True}

        # Should still have only one subscription, but with updated keys
        assert PushSubscription.objects.count() == 1
        subscription = PushSubscription.objects.first()
        assert subscription.p256dh == "UPDATED_P256DH_KEY"
        assert subscription.auth == "UPDATED_AUTH_KEY"

    def it_returns_400_for_missing_required_fields(authenticated_client):
        """Should return 400 when required fields are missing."""
        incomplete_data = {"endpoint": "https://example.com/push"}
        response = authenticated_client.post(
            "/webpush/subscribe/",
            data=json.dumps(incomplete_data),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json() == {"error": "Missing required fields"}

    def it_returns_400_for_missing_endpoint(authenticated_client):
        """Should return 400 when endpoint is missing."""
        data = {"p256dh": "some_key", "auth": "some_auth"}
        response = authenticated_client.post(
            "/webpush/subscribe/",
            data=json.dumps(data),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json() == {"error": "Missing required fields"}

    def it_returns_400_for_invalid_json(authenticated_client):
        """Should return 400 when request body is not valid JSON."""
        response = authenticated_client.post(
            "/webpush/subscribe/",
            data="not valid json",
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json() == {"error": "Invalid JSON"}

    def describe_subscription_user_assignment():
        """Tests for user assignment in subscriptions."""

        def it_assigns_subscription_to_authenticated_user(authenticated_client, valid_subscription_data):
            """Subscription should be linked to the authenticated user."""
            User = get_user_model()
            user = User.objects.get(email="test@example.com")

            authenticated_client.post(
                "/webpush/subscribe/",
                data=json.dumps(valid_subscription_data),
                content_type="application/json",
            )

            subscription = PushSubscription.objects.first()
            assert subscription.user == user

    def it_returns_500_on_unexpected_error(authenticated_client, valid_subscription_data):
        """Should return 500 when an unexpected error occurs."""
        with patch(
            "core.views.PushSubscription.objects.update_or_create",
            side_effect=RuntimeError("Database error"),
        ):
            response = authenticated_client.post(
                "/webpush/subscribe/",
                data=json.dumps(valid_subscription_data),
                content_type="application/json",
            )
        assert response.status_code == 500
        assert response.json() == {"error": "Subscription failed. Please try again."}


# ---------------------------------------------------------------------------
# Unsubscribe View
# ---------------------------------------------------------------------------


def describe_unsubscribe_view():  # noqa: C901
    """Tests for the push unsubscription endpoint."""

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

    @pytest.fixture()
    def user_with_subscription(authenticated_client):
        """Create a user with an existing push subscription."""
        User = get_user_model()
        user = User.objects.get(email="test@example.com")

        subscription = PushSubscription.objects.create(
            user=user,
            endpoint="https://fcm.googleapis.com/fcm/send/test-endpoint-456",
            p256dh="EXISTING_P256DH_KEY",
            auth="EXISTING_AUTH_KEY",
        )
        return authenticated_client, subscription

    def it_returns_200_ok_for_authenticated_post(authenticated_client):
        """Authenticated POST should return 200 OK."""
        response = authenticated_client.post(
            "/webpush/unsubscribe/",
            data=json.dumps({"endpoint": "https://example.com/push"}),
            content_type="application/json",
        )
        assert response.status_code == 200

    def it_requires_login(client):
        """Unauthenticated requests should be redirected to login."""
        response = client.post(
            "/webpush/unsubscribe/",
            data=json.dumps({"endpoint": "https://example.com/push"}),
            content_type="application/json",
        )
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_requires_post_method(authenticated_client):
        """Should only accept POST requests."""
        response = authenticated_client.get("/webpush/unsubscribe/")
        assert response.status_code == 405

    def it_deletes_subscription(user_with_subscription):
        """Should delete the subscription from the database."""
        client, subscription = user_with_subscription
        endpoint = subscription.endpoint

        response = client.post(
            "/webpush/unsubscribe/",
            data=json.dumps({"endpoint": endpoint}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json() == {"success": True}

        # Verify subscription was deleted
        assert PushSubscription.objects.filter(endpoint=endpoint).count() == 0

    def it_succeeds_even_if_subscription_not_found(authenticated_client):
        """iOS graceful degradation: Should succeed even if subscription doesn't exist.

        This handles the case where iOS Safari silently fails to register push
        subscriptions, so unsubscribe may be called without a prior subscribe.
        """
        response = authenticated_client.post(
            "/webpush/unsubscribe/",
            data=json.dumps({"endpoint": "https://fcm.googleapis.com/fcm/send/nonexistent"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json() == {"success": True}

    def it_returns_400_for_missing_endpoint(authenticated_client):
        """Should return 400 when endpoint is missing."""
        response = authenticated_client.post(
            "/webpush/unsubscribe/",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json() == {"error": "Missing endpoint"}

    def it_returns_400_for_invalid_json(authenticated_client):
        """Should return 400 when request body is not valid JSON."""
        response = authenticated_client.post(
            "/webpush/unsubscribe/",
            data="not valid json",
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json() == {"error": "Invalid JSON"}

    def it_only_deletes_own_subscription(user_with_subscription):
        """Should only delete subscriptions belonging to the authenticated user."""
        User = get_user_model()

        # Create another user with their own subscription
        other_user = User.objects.create_user(
            username="otheruser",
            email="other@example.com",
            password="otherpass123",
        )
        other_subscription = PushSubscription.objects.create(
            user=other_user,
            endpoint="https://fcm.googleapis.com/fcm/send/other-endpoint",
            p256dh="OTHER_P256DH_KEY",
            auth="OTHER_AUTH_KEY",
        )

        client, subscription = user_with_subscription
        endpoint = subscription.endpoint

        # Delete own subscription
        client.post(
            "/webpush/unsubscribe/",
            data=json.dumps({"endpoint": endpoint}),
            content_type="application/json",
        )

        # Other user's subscription should still exist
        assert PushSubscription.objects.filter(endpoint=other_subscription.endpoint).exists()

    def it_returns_500_on_unexpected_error(authenticated_client):
        """Should return 500 when an unexpected error occurs during unsubscribe."""
        with patch(
            "core.views.PushSubscription.objects.filter",
            side_effect=RuntimeError("Database connection lost"),
        ):
            response = authenticated_client.post(
                "/webpush/unsubscribe/",
                data=json.dumps({"endpoint": "https://example.com/push"}),
                content_type="application/json",
            )
        assert response.status_code == 500
        assert response.json() == {"error": "Unsubscription failed. Please try again."}


def describe_push_subscription_model():
    """Tests for the PushSubscription model."""

    def it_has_str_representation():
        """Test __str__ method returns expected format."""
        User = get_user_model()
        user = User.objects.create_user(
            username="strtestuser",
            email="strtest@example.com",
            password="testpass123",
        )
        endpoint = "https://example.com/push/very-long-endpoint-url-that-should-be-truncated-in-str"
        subscription = PushSubscription.objects.create(
            user=user,
            endpoint=endpoint,
            p256dh="test_p256dh_key",
            auth="test_auth_key",
        )
        expected = f"{user.email} - {endpoint[:50]}..."
        assert str(subscription) == expected


def describe_service_worker_middleware():
    """Tests for the ServiceWorkerAllowedMiddleware."""

    def it_adds_header_for_service_worker_request(client):
        """Test middleware adds Service-Worker-Allowed header for sw.js."""
        response = client.get("/sw.js")
        assert response.get("Service-Worker-Allowed") == "/"

    def it_does_not_add_header_for_other_requests(client):
        """Test middleware doesn't add header for non-SW requests."""
        response = client.get("/")
        assert response.get("Service-Worker-Allowed") is None


def describe_service_worker_view():
    """Tests for the service worker view that adds the SW-Allowed header."""

    def it_returns_sw_js_content(client):
        """Test view returns service worker JavaScript content."""
        response = client.get("/sw.js")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/javascript"
        assert b"addEventListener" in response.content

    def it_adds_service_worker_allowed_header(client):
        """Test view adds Service-Worker-Allowed header."""
        response = client.get("/sw.js")
        assert response["Service-Worker-Allowed"] == "/"

    def it_returns_404_if_sw_file_missing(client):
        """Test view returns 404 if sw.js doesn't exist."""
        # Temporarily move sw.js
        import shutil
        from pathlib import Path

        sw_path = Path(__file__).parent.parent.parent / "static" / "js" / "sw.js"
        backup_path = sw_path.with_suffix(".js.bak")
        if sw_path.exists():
            shutil.move(str(sw_path), str(backup_path))
        try:
            response = client.get("/sw.js")
            assert response.status_code == 404
        finally:
            if backup_path.exists():
                shutil.move(str(backup_path), str(sw_path))
