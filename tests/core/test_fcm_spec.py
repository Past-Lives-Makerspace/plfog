"""Tests for native (FCM) device registration views + the FcmDevice model."""

import json
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from core.models import FcmDevice

pytestmark = pytest.mark.django_db


def describe_fcm_register_view():
    """Tests for the FCM device-registration endpoint."""

    @pytest.fixture()
    def valid_data():
        return {"token": "fcm-token-abc123", "platform": "android"}

    def it_returns_200_for_authenticated_post(authenticated_client, valid_data):
        response = authenticated_client.post(
            "/push/fcm/register/", data=json.dumps(valid_data), content_type="application/json"
        )
        assert response.status_code == 200
        assert response.json() == {"success": True}

    def it_requires_login(client, valid_data):
        response = client.post("/push/fcm/register/", data=json.dumps(valid_data), content_type="application/json")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_requires_post_method(authenticated_client):
        response = authenticated_client.get("/push/fcm/register/")
        assert response.status_code == 405

    def it_creates_device_for_authenticated_user(authenticated_client, valid_data):
        User = get_user_model()
        user = User.objects.get(email="test@example.com")
        authenticated_client.post("/push/fcm/register/", data=json.dumps(valid_data), content_type="application/json")
        device = FcmDevice.objects.get(token=valid_data["token"])
        assert device.user == user
        assert device.platform == FcmDevice.Platform.ANDROID

    def it_defaults_platform_to_android_when_omitted(authenticated_client):
        authenticated_client.post(
            "/push/fcm/register/", data=json.dumps({"token": "no-platform"}), content_type="application/json"
        )
        assert FcmDevice.objects.get(token="no-platform").platform == FcmDevice.Platform.ANDROID

    def it_updates_existing_token_in_place(authenticated_client, valid_data):
        authenticated_client.post("/push/fcm/register/", data=json.dumps(valid_data), content_type="application/json")
        updated = valid_data | {"platform": "ios"}
        authenticated_client.post("/push/fcm/register/", data=json.dumps(updated), content_type="application/json")
        assert FcmDevice.objects.filter(token=valid_data["token"]).count() == 1
        assert FcmDevice.objects.get(token=valid_data["token"]).platform == FcmDevice.Platform.IOS

    def it_returns_400_for_missing_token(authenticated_client):
        response = authenticated_client.post(
            "/push/fcm/register/", data=json.dumps({"platform": "android"}), content_type="application/json"
        )
        assert response.status_code == 400
        assert response.json() == {"error": "Missing token"}

    def it_returns_400_for_invalid_platform(authenticated_client):
        response = authenticated_client.post(
            "/push/fcm/register/",
            data=json.dumps({"token": "t", "platform": "windows"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json() == {"error": "Invalid platform"}

    def it_refuses_a_token_owned_by_another_user(authenticated_client):
        User = get_user_model()
        other = User.objects.create_user(username="prev", email="prev@example.com", password="x")
        FcmDevice.objects.create(user=other, token="shared-token", platform=FcmDevice.Platform.ANDROID)
        response = authenticated_client.post(
            "/push/fcm/register/",
            data=json.dumps({"token": "shared-token", "platform": "android"}),
            content_type="application/json",
        )
        assert response.status_code == 409
        assert response.json() == {"error": "Token already registered to another account"}
        # the device stays with its original owner
        assert FcmDevice.objects.get(token="shared-token").user == other

    def it_returns_400_for_invalid_json(authenticated_client):
        response = authenticated_client.post("/push/fcm/register/", data="not json", content_type="application/json")
        assert response.status_code == 400
        assert response.json() == {"error": "Invalid JSON"}

    def it_returns_500_on_unexpected_error(authenticated_client, valid_data):
        with patch("core.views.FcmDevice.objects.update_or_create", side_effect=RuntimeError("db down")):
            response = authenticated_client.post(
                "/push/fcm/register/", data=json.dumps(valid_data), content_type="application/json"
            )
        assert response.status_code == 500
        assert response.json() == {"error": "Registration failed. Please try again."}


def describe_fcm_unregister_view():
    """Tests for the FCM device-unregistration endpoint."""

    @pytest.fixture()
    def user_with_device(authenticated_client):
        User = get_user_model()
        user = User.objects.get(email="test@example.com")
        device = FcmDevice.objects.create(user=user, token="existing-token", platform=FcmDevice.Platform.ANDROID)
        return authenticated_client, device

    def it_requires_login(client):
        response = client.post(
            "/push/fcm/unregister/", data=json.dumps({"token": "x"}), content_type="application/json"
        )
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_requires_post_method(authenticated_client):
        response = authenticated_client.get("/push/fcm/unregister/")
        assert response.status_code == 405

    def it_deletes_the_device(user_with_device):
        client, device = user_with_device
        response = client.post(
            "/push/fcm/unregister/", data=json.dumps({"token": device.token}), content_type="application/json"
        )
        assert response.status_code == 200
        assert response.json() == {"success": True}
        assert not FcmDevice.objects.filter(pk=device.pk).exists()

    def it_succeeds_when_token_unknown(authenticated_client):
        response = authenticated_client.post(
            "/push/fcm/unregister/", data=json.dumps({"token": "nope"}), content_type="application/json"
        )
        assert response.status_code == 200
        assert response.json() == {"success": True}

    def it_returns_400_for_missing_token(authenticated_client):
        response = authenticated_client.post(
            "/push/fcm/unregister/", data=json.dumps({}), content_type="application/json"
        )
        assert response.status_code == 400
        assert response.json() == {"error": "Missing token"}

    def it_returns_400_for_invalid_json(authenticated_client):
        response = authenticated_client.post("/push/fcm/unregister/", data="nope", content_type="application/json")
        assert response.status_code == 400
        assert response.json() == {"error": "Invalid JSON"}

    def it_only_deletes_own_device(user_with_device):
        User = get_user_model()
        other = User.objects.create_user(username="other", email="other@example.com", password="x")
        other_device = FcmDevice.objects.create(user=other, token="other-token", platform=FcmDevice.Platform.ANDROID)
        client, device = user_with_device
        client.post("/push/fcm/unregister/", data=json.dumps({"token": device.token}), content_type="application/json")
        assert FcmDevice.objects.filter(pk=other_device.pk).exists()

    def it_returns_500_on_unexpected_error(authenticated_client):
        with patch("core.views.FcmDevice.objects.filter", side_effect=RuntimeError("db lost")):
            response = authenticated_client.post(
                "/push/fcm/unregister/", data=json.dumps({"token": "x"}), content_type="application/json"
            )
        assert response.status_code == 500
        assert response.json() == {"error": "Unregistration failed. Please try again."}


def describe_fcm_device_model():
    """Tests for the FcmDevice model."""

    def it_has_str_representation():
        User = get_user_model()
        user = User.objects.create_user(username="strd", email="strd@example.com")
        device = FcmDevice.objects.create(
            user=user, token="abcdefghijklmnopqrstuvwxyz-0123456789", platform=FcmDevice.Platform.IOS
        )
        expected = f"{user.email} - iOS - {device.token[:16]}..."
        assert str(device) == expected
