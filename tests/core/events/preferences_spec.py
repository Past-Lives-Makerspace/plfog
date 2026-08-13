"""Channel-generic preference resolution on the unified per-(event, channel) model."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from core.events import preferences
from core.events.registry import Channel
from core.models import NotificationPreference

pytestmark = pytest.mark.django_db


def _user():
    return User.objects.create_user(username="pref", email="pref@example.com")


def _pref(user, event_key, channel, enabled):
    return NotificationPreference.objects.create(user=user, event_key=event_key, channel=channel.value, enabled=enabled)


def describe_wants():
    def describe_in_app():
        def it_is_always_on_for_declared_events():
            assert preferences.wants(_user(), "class_published", Channel.IN_APP) is True

    def describe_forced_channels():
        def it_ignores_preferences_for_forced_email():
            user = _user()
            # member.invited forces email; an explicit opt-out row must not matter.
            _pref(user, "member.invited", Channel.EMAIL, False)
            assert preferences.wants(user, "member.invited", Channel.EMAIL) is True

    def describe_email_per_channel_row():
        def it_reads_enabled_from_an_explicit_row():
            user = _user()
            _pref(user, "class_published", Channel.EMAIL, True)
            assert preferences.wants(user, "class_published", Channel.EMAIL) is True

        def it_respects_an_explicit_opt_out():
            user = _user()
            _pref(user, "class_published", Channel.EMAIL, False)
            assert preferences.wants(user, "class_published", Channel.EMAIL) is False

        def it_falls_back_to_the_event_default_with_no_row():
            # class_published defaults email OFF.
            assert preferences.wants(_user(), "class_published", Channel.EMAIL) is False

    def describe_push_per_channel_row():
        def it_reads_enabled_from_an_explicit_row():
            user = _user()
            _pref(user, "class_published", Channel.PUSH, True)
            assert preferences.wants(user, "class_published", Channel.PUSH) is True

        def it_defaults_push_off_with_no_row():
            assert preferences.wants(_user(), "class_published", Channel.PUSH) is False

    def describe_non_legacy_channels():
        def it_now_honors_a_real_per_channel_preference():
            # Discord is a per-event broadcast channel that site.announcement declares; the
            # per-channel model lets a row govern it directly (no legacy column existed before).
            user = _user()
            _pref(user, "site_announcement", Channel.DISCORD, False)
            assert preferences.wants(user, "site_announcement", Channel.DISCORD) is False

    def describe_undeclared_channel():
        def it_returns_false_when_event_has_no_such_channel():
            # A seeded legacy event (class_reminder) declares no discord channel. (class_published
            # now DOES — it gained the Discord broadcast — so use a still-discord-free event here.)
            assert preferences.wants(_user(), "class_reminder", Channel.DISCORD) is False


def describe_optional_recipient():
    # class_validation_requested declares: in_app on, email on (email_default), push off.
    def it_receives_nothing_without_an_explicit_row():
        user = _user()
        assert preferences.wants(user, "class_validation_requested", Channel.IN_APP, is_optional=True) is False
        assert preferences.wants(user, "class_validation_requested", Channel.EMAIL, is_optional=True) is False
        assert preferences.enabled_channels(user, "class_validation_requested", is_optional=True) == []

    def it_ignores_the_in_app_always_on_shortcut_when_optional():
        user = _user()
        # Non-optional: in-app is always on. Optional: in-app needs an explicit opt-in row.
        assert preferences.wants(user, "class_validation_requested", Channel.IN_APP, is_optional=False) is True
        assert preferences.wants(user, "class_validation_requested", Channel.IN_APP, is_optional=True) is False

    def it_fires_only_an_explicitly_enabled_channel():
        user = _user()
        _pref(user, "class_validation_requested", Channel.EMAIL, True)
        assert preferences.wants(user, "class_validation_requested", Channel.EMAIL, is_optional=True) is True
        assert preferences.enabled_channels(user, "class_validation_requested", is_optional=True) == [Channel.EMAIL]

    def it_still_forces_a_forced_channel_even_when_optional():
        user = _user()
        # member.invited forces email — forced beats the optional gate.
        assert preferences.wants(user, "member.invited", Channel.EMAIL, is_optional=True) is True


def describe_enabled_channels():
    def it_lists_in_app_only_by_default():
        # class_reminder: in_app on, email off, push off, no discord → only in_app.
        assert preferences.enabled_channels(_user(), "class_reminder") == [Channel.IN_APP]

    def it_includes_email_when_opted_in():
        user = _user()
        _pref(user, "class_reminder", Channel.EMAIL, True)
        assert preferences.enabled_channels(user, "class_reminder") == [Channel.IN_APP, Channel.EMAIL]

    def it_includes_forced_email_without_a_row():
        # member.invited forces email (and is email-only), so it's included with no row.
        assert preferences.enabled_channels(_user(), "member.invited") == [Channel.EMAIL]
