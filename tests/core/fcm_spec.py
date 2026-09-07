"""Native push (FCM HTTP v1) sending with dead-device cleanup."""

import json
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
            result = fcm.send_fcm(device, title="Hi", body="There", url="/x/", channel_id="general")
        assert result is True
        assert route.called
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer access-tok"
        assert b"tok-abc" in request.content
        assert b"/x/" in request.content

    def it_tags_the_message_with_the_android_channel_id(user):
        device = _device(user)
        with patch("core.fcm._access_token_and_project", return_value=("access-tok", "proj")), respx.mock:
            route = respx.post(_FCM_URL).mock(return_value=httpx.Response(200, json={"name": "ok"}))
            fcm.send_fcm(device, title="Hi", body="There", url="/x/", channel_id="urgent")
        sent = json.loads(route.calls.last.request.content)
        assert sent["message"]["android"]["notification"]["channel_id"] == "urgent"

    def it_sends_the_urgent_channel_at_high_priority(user):
        device = _device(user)
        with patch("core.fcm._access_token_and_project", return_value=("access-tok", "proj")), respx.mock:
            route = respx.post(_FCM_URL).mock(return_value=httpx.Response(200, json={"name": "ok"}))
            fcm.send_fcm(device, title="Hi", body="There", url="/x/", channel_id="urgent")
        sent = json.loads(route.calls.last.request.content)
        assert sent["message"]["android"]["priority"] == "high"

    def it_sends_non_urgent_channels_at_normal_priority(user):
        device = _device(user)
        with patch("core.fcm._access_token_and_project", return_value=("access-tok", "proj")), respx.mock:
            route = respx.post(_FCM_URL).mock(return_value=httpx.Response(200, json={"name": "ok"}))
            fcm.send_fcm(device, title="Hi", body="There", url="/x/", channel_id="general")
        sent = json.loads(route.calls.last.request.content)
        assert sent["message"]["android"]["priority"] == "normal"

    def describe_the_apns_block():
        """iOS delivery options. Without these an iOS push arrives silent and ungrouped.

        The block is attached only for an iOS device: FCM rejects a whole message when any
        part of it is malformed, and ``send_fcm`` turns a rejection into a quiet ``False``, so
        keeping it off Android sends keeps any mistake here from taking Android down too.
        """

        def _sent_payload(user, channel_id, platform=FcmDevice.Platform.IOS):
            device = FcmDevice.objects.create(user=user, token=f"tok-{platform}-{channel_id}", platform=platform)
            with patch("core.fcm._access_token_and_project", return_value=("access-tok", "proj")), respx.mock:
                route = respx.post(_FCM_URL).mock(return_value=httpx.Response(200, json={"name": "ok"}))
                fcm.send_fcm(device, title="Hi", body="There", url="/x/", channel_id=channel_id)
            return json.loads(route.calls.last.request.content)["message"]

        def it_is_absent_for_an_android_device(user):
            assert "apns" not in _sent_payload(user, "general", platform=FcmDevice.Platform.ANDROID)

        def it_is_present_for_an_ios_device(user):
            assert "apns" in _sent_payload(user, "general")

        def it_ships_a_sound(user):
            assert _sent_payload(user, "general")["apns"]["payload"]["aps"]["sound"] == "default"

        def it_sets_no_push_type_header(user):
            # FCM sets apns-push-type itself, and it is the field it is documented to reject
            # on a bad value. Sending it buys nothing and only adds a rejection surface.
            assert "apns-push-type" not in _sent_payload(user, "general")["apns"]["headers"]

        def it_sends_the_urgent_channel_at_apns_priority_10(user):
            assert _sent_payload(user, "urgent")["apns"]["headers"]["apns-priority"] == "10"

        @pytest.mark.parametrize("channel_id", ["guilds", "classes", "general"])
        def it_sends_non_urgent_channels_at_apns_priority_5(user, channel_id):
            assert _sent_payload(user, channel_id)["apns"]["headers"]["apns-priority"] == "5"

        @pytest.mark.parametrize("channel_id", ["urgent", "guilds", "classes", "general"])
        def it_groups_the_tray_by_channel_id(user, channel_id):
            # iOS has no channels, so channel_id doubles as the APNs thread-id group key.
            assert _sent_payload(user, channel_id)["apns"]["payload"]["aps"]["thread-id"] == channel_id

        def it_carries_no_badge_count(user):
            # A badge needs an unread tally this path does not have; a wrong badge is worse than none.
            assert "badge" not in _sent_payload(user, "urgent")["apns"]["payload"]["aps"]

        def it_leaves_an_android_send_byte_for_byte_as_it_was(user):
            # The shipped Android payload must be exactly what it was before iOS existed.
            message = _sent_payload(user, "urgent", platform=FcmDevice.Platform.ANDROID)
            assert message == {
                "token": "tok-android-urgent",
                "notification": {"title": "Hi", "body": "There"},
                "android": {"priority": "high", "notification": {"channel_id": "urgent"}},
                "data": {"url": "/x/"},
            }

        def it_leaves_the_shared_fields_untouched_on_ios(user):
            message = _sent_payload(user, "urgent")
            assert message["android"] == {"priority": "high", "notification": {"channel_id": "urgent"}}
            assert message["data"] == {"url": "/x/"}
            assert message["notification"] == {"title": "Hi", "body": "There"}

    def describe_apns_priority():
        def it_maps_urgent_to_10_and_everything_else_to_5():
            assert fcm._apns_priority(fcm.PUSH_CHANNEL_URGENT) == "10"
            assert fcm._apns_priority(fcm.PUSH_CHANNEL_GUILDS) == "5"
            assert fcm._apns_priority(fcm.PUSH_CHANNEL_CLASSES) == "5"
            assert fcm._apns_priority(fcm.PUSH_CHANNEL_GENERAL) == "5"

    def it_deletes_device_on_404_unregistered(user):
        device = _device(user, token="dead")
        with patch("core.fcm._access_token_and_project", return_value=("access-tok", "proj")), respx.mock:
            respx.post(_FCM_URL).mock(return_value=httpx.Response(404, json={"error": {"status": "UNREGISTERED"}}))
            result = fcm.send_fcm(device, title="Hi", body="There", url="/x/", channel_id="general")
        assert result is False
        assert not FcmDevice.objects.filter(pk=device.pk).exists()

    def it_keeps_device_on_other_http_error(user):
        device = _device(user, token="keep")
        with patch("core.fcm._access_token_and_project", return_value=("access-tok", "proj")), respx.mock:
            respx.post(_FCM_URL).mock(return_value=httpx.Response(500, text="boom"))
            result = fcm.send_fcm(device, title="Hi", body="There", url="/x/", channel_id="general")
        assert result is False
        assert FcmDevice.objects.filter(pk=device.pk).exists()

    def it_swallows_transport_errors_without_deleting(user):
        device = _device(user, token="net")
        with patch("core.fcm._access_token_and_project", return_value=("access-tok", "proj")), respx.mock:
            respx.post(_FCM_URL).mock(side_effect=httpx.ConnectError("down"))
            result = fcm.send_fcm(device, title="Hi", body="There", url="/x/", channel_id="general")
        assert result is False
        assert FcmDevice.objects.filter(pk=device.pk).exists()

    def it_noops_when_credentials_unconfigured(user):
        device = _device(user)
        with patch("core.fcm._access_token_and_project", return_value=None), respx.mock:
            route = respx.post(_FCM_URL).mock(return_value=httpx.Response(200))
            result = fcm.send_fcm(device, title="Hi", body="There", url="/x/", channel_id="general")
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
