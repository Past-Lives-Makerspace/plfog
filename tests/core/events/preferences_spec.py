"""Channel-generic preference resolution — backward-compatible with legacy columns."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from core.events import preferences
from core.events.registry import Channel
from core.models import NotificationPreference

pytestmark = pytest.mark.django_db


def _user():
    return User.objects.create_user(username="pref", email="pref@example.com")


def describe_wants():
    def describe_in_app():
        def it_is_always_on_for_declared_events():
            assert preferences.wants(_user(), "class_published", Channel.IN_APP) is True

    def describe_forced_channels():
        def it_ignores_preferences_for_forced_email():
            user = _user()
            # new_login forces email; an explicit opt-out row must not matter.
            NotificationPreference.objects.create(user=user, trigger="new_login", email_enabled=False)
            assert preferences.wants(user, "new_login", Channel.EMAIL) is True

    def describe_email_with_legacy_columns():
        def it_reads_email_enabled_from_an_explicit_row():
            user = _user()
            NotificationPreference.objects.create(user=user, trigger="class_published", email_enabled=True)
            assert preferences.wants(user, "class_published", Channel.EMAIL) is True

        def it_respects_an_explicit_opt_out():
            user = _user()
            NotificationPreference.objects.create(user=user, trigger="class_published", email_enabled=False)
            assert preferences.wants(user, "class_published", Channel.EMAIL) is False

        def it_falls_back_to_the_event_default_with_no_row():
            # class_published defaults email OFF.
            assert preferences.wants(_user(), "class_published", Channel.EMAIL) is False

    def describe_push_with_legacy_columns():
        def it_reads_push_enabled_from_an_explicit_row():
            user = _user()
            NotificationPreference.objects.create(user=user, trigger="class_published", push_enabled=True)
            assert preferences.wants(user, "class_published", Channel.PUSH) is True

        def it_defaults_push_off_with_no_row():
            assert preferences.wants(_user(), "class_published", Channel.PUSH) is False

    def describe_undeclared_channel():
        def it_returns_false_when_event_has_no_such_channel():
            # No legacy trigger declares discord.
            assert preferences.wants(_user(), "class_published", Channel.DISCORD) is False


def describe_enabled_channels():
    def it_lists_in_app_only_by_default():
        # class_published: in_app on, email off, push off → only in_app.
        assert preferences.enabled_channels(_user(), "class_published") == [Channel.IN_APP]

    def it_includes_email_when_opted_in():
        user = _user()
        NotificationPreference.objects.create(user=user, trigger="class_published", email_enabled=True)
        assert preferences.enabled_channels(user, "class_published") == [Channel.IN_APP, Channel.EMAIL]

    def it_includes_forced_email_without_a_row():
        # new_login forces email; in_app on too.
        assert preferences.enabled_channels(_user(), "new_login") == [Channel.IN_APP, Channel.EMAIL]
