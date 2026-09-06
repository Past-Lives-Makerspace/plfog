"""BDD specs for the "Push On This Device" card above the notifications matrix.

The card itself is inert until ``static/js/native-push.js`` reveals it inside the Capacitor
app, so what is testable here is exactly the Django half: that the card is rendered for a
signed-in member, that the server-rendered device-count line reflects both push transports,
and that a token-scoped (logged-out) viewer never gets it.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.email_prefs import make_prefs_token
from core.events.channels import push_device_count
from core.models import FcmDevice, PushSubscription
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

_CARD_MARKER = 'id="push-this-device"'
_SETTINGS_URL_TAB = "?tab=notifications"


def _signed_in(client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pw12345!")
    client.login(username=username, password="pw12345!")
    return user


def _fcm_device(user: User, token: str, platform: str = FcmDevice.Platform.ANDROID) -> FcmDevice:
    return FcmDevice.objects.create(user=user, token=token, platform=platform)


def _web_subscription(user: User, endpoint: str) -> PushSubscription:
    return PushSubscription.objects.create(user=user, endpoint=endpoint, p256dh="p256", auth="auth")


def _signed_in_free(username: str) -> User:
    """A user with no client session — for exercising the counter directly."""
    MembershipPlanFactory()
    return User.objects.create_user(username=username, email=f"{username}@example.com")


def _settings_html(client) -> str:
    response = client.get(f"{reverse('hub_user_settings')}{_SETTINGS_URL_TAB}")
    assert response.status_code == 200
    return response.content.decode()


def describe_push_device_count():
    """The count the card reads is derived from the same rows PushAdapter delivers to."""

    def it_is_zero_with_no_devices():
        user = _signed_in_free("counter_zero")
        assert push_device_count(user) == 0

    def it_counts_native_and_web_push_together():
        user = _signed_in_free("counter_both")
        _fcm_device(user, "tok-a")
        _fcm_device(user, "tok-b", platform=FcmDevice.Platform.IOS)
        _web_subscription(user, "https://push.example.com/a")
        assert push_device_count(user) == 3

    def it_ignores_another_members_devices():
        user = _signed_in_free("counter_mine")
        other = _signed_in_free("counter_theirs")
        _fcm_device(user, "tok-mine")
        _fcm_device(other, "tok-theirs")
        _web_subscription(other, "https://push.example.com/theirs")
        assert push_device_count(user) == 1


def describe_the_card_on_the_settings_page():
    def it_renders_for_a_signed_in_member(client):
        _signed_in(client, "card_member")
        assert _CARD_MARKER in _settings_html(client)

    def it_passes_the_device_count_into_the_page(client):
        user = _signed_in(client, "card_ctx")
        _fcm_device(user, "tok-ctx")
        response = client.get(f"{reverse('hub_user_settings')}{_SETTINGS_URL_TAB}")
        assert response.context["push_device_count"] == 1

    def describe_the_device_count_line():
        def it_is_absent_when_no_device_is_registered(client):
            _signed_in(client, "card_zero")
            html = _settings_html(client)
            assert _CARD_MARKER in html  # the card still renders; only the count line is dropped
            assert "Your account has push set up on" not in html

        def it_reads_singular_for_one_device(client):
            user = _signed_in(client, "card_one")
            _fcm_device(user, "tok-one")
            assert "Your account has push set up on 1 device." in _settings_html(client)

        def it_reads_plural_for_several_devices(client):
            user = _signed_in(client, "card_many")
            _fcm_device(user, "tok-1")
            _fcm_device(user, "tok-2", platform=FcmDevice.Platform.IOS)
            _web_subscription(user, "https://push.example.com/many")
            assert "Your account has push set up on 3 devices." in _settings_html(client)

    def describe_for_a_token_scoped_viewer():
        def it_does_not_render_the_card(client):
            MembershipPlanFactory()
            user = User.objects.create_user(username="card_token", email="card_token@example.com")
            _fcm_device(user, "tok-token")
            token = make_prefs_token(user)

            response = client.get(f"{reverse('hub_user_settings')}{_SETTINGS_URL_TAB}&t={token}")

            assert response.status_code == 200
            assert response.templates[0].name == "hub/settings_notifications_token.html"
            html = response.content.decode()
            assert _CARD_MARKER not in html
            assert "Your account has push set up on" not in html
