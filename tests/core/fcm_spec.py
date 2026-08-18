"""Native push (FCM HTTP v1) sending with dead-device cleanup."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.test import override_settings

from core import fcm
from core.models import FcmDevice

pytestmark = pytest.mark.django_db

_FCM_URL = "https://fcm.googleapis.com/v1/projects/proj/messages:send"
_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


def _device(user, token="tok-abc"):
    return FcmDevice.objects.create(user=user, token=token, platform=FcmDevice.Platform.ANDROID)


def describe_send_fcm():
    @pytest.fixture()
    def user(db):
        return User.objects.create_user(username="d", email="d@example.com")

    def it_posts_to_fcm_with_token_and_bearer(user):
        device = _device(user)
        with patch("core.fcm._access_token_and_project", return_value=("access-tok", "proj")), respx.mock:
            route = respx.post(_FCM_URL).mock(return_value=httpx.Response(200, json={"name": "ok"}))
            result = fcm.send_fcm(device, title="Hi", body="There", url="/x/")
        assert result is True
        assert route.called
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer access-tok"
        assert b"tok-abc" in request.content
        assert b"/x/" in request.content

    def it_deletes_device_on_404_unregistered(user):
        device = _device(user, token="dead")
        with patch("core.fcm._access_token_and_project", return_value=("access-tok", "proj")), respx.mock:
            respx.post(_FCM_URL).mock(return_value=httpx.Response(404, json={"error": {"status": "UNREGISTERED"}}))
            result = fcm.send_fcm(device, title="Hi", body="There", url="/x/")
        assert result is False
        assert not FcmDevice.objects.filter(pk=device.pk).exists()

    def it_keeps_device_on_other_http_error(user):
        device = _device(user, token="keep")
        with patch("core.fcm._access_token_and_project", return_value=("access-tok", "proj")), respx.mock:
            respx.post(_FCM_URL).mock(return_value=httpx.Response(500, text="boom"))
            result = fcm.send_fcm(device, title="Hi", body="There", url="/x/")
        assert result is False
        assert FcmDevice.objects.filter(pk=device.pk).exists()

    def it_swallows_transport_errors_without_deleting(user):
        device = _device(user, token="net")
        with patch("core.fcm._access_token_and_project", return_value=("access-tok", "proj")), respx.mock:
            respx.post(_FCM_URL).mock(side_effect=httpx.ConnectError("down"))
            result = fcm.send_fcm(device, title="Hi", body="There", url="/x/")
        assert result is False
        assert FcmDevice.objects.filter(pk=device.pk).exists()

    def it_noops_when_credentials_unconfigured(user):
        device = _device(user)
        with patch("core.fcm._access_token_and_project", return_value=None), respx.mock:
            route = respx.post(_FCM_URL).mock(return_value=httpx.Response(200))
            result = fcm.send_fcm(device, title="Hi", body="There", url="/x/")
        assert result is False
        assert not route.called


def describe_access_token_and_project():
    @override_settings(FCM_SERVICE_ACCOUNT_JSON="")
    def it_returns_none_when_unconfigured():
        assert fcm._access_token_and_project() is None

    @override_settings(FCM_SERVICE_ACCOUNT_JSON="{not valid json")
    def it_returns_none_on_unparseable_json():
        assert fcm._access_token_and_project() is None

    @override_settings(FCM_SERVICE_ACCOUNT_JSON='{"project_id": "plfog-proj", "type": "service_account"}')
    def it_mints_token_and_resolves_project():
        creds = MagicMock()
        creds.token = "minted-token"
        with patch("core.fcm.service_account.Credentials.from_service_account_info", return_value=creds) as mk:
            result = fcm._access_token_and_project()
        creds.refresh.assert_called_once()
        assert result == ("minted-token", "plfog-proj")
        assert mk.call_args.kwargs["scopes"] == [_SCOPE]

    @override_settings(FCM_SERVICE_ACCOUNT_JSON='{"project_id": "plfog-proj"}')
    def it_returns_none_when_credential_build_fails():
        with patch("core.fcm.service_account.Credentials.from_service_account_info", side_effect=ValueError("bad key")):
            assert fcm._access_token_and_project() is None
