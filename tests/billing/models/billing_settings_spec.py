"""BDD-style tests for BillingSettings model."""

from decimal import Decimal

import pytest
from django.db import IntegrityError

from billing.models import BillingSettings
from tests.billing.factories import BillingSettingsFactory

pytestmark = pytest.mark.django_db


def describe_BillingSettings():
    def it_is_a_singleton():
        settings = BillingSettingsFactory()
        assert settings.pk == 1

    def it_forces_pk_1_on_save():
        settings = BillingSettings(pk=99, default_tab_limit=Decimal("100.00"))
        settings.save()
        settings.refresh_from_db()
        assert settings.pk == 1

    def it_loads_with_defaults():
        settings = BillingSettings.load()
        assert settings.pk == 1
        assert settings.charge_frequency == BillingSettings.ChargeFrequency.MONTHLY
        assert settings.default_tab_limit == Decimal("200.00")
        assert settings.max_retry_attempts == 3
        assert settings.retry_interval_hours == 24

    def it_has_str_representation():
        settings = BillingSettingsFactory()
        assert str(settings) == "Billing Settings"

    def describe_next_charge_at():
        def it_returns_none_when_off():
            settings = BillingSettingsFactory(charge_frequency=BillingSettings.ChargeFrequency.OFF)
            assert settings.next_charge_at() is None

        def it_returns_a_future_datetime_for_daily():
            from django.utils import timezone as _tz

            settings = BillingSettingsFactory(
                charge_frequency=BillingSettings.ChargeFrequency.DAILY,
                charge_time="23:00",
            )
            result = settings.next_charge_at()
            assert result is not None
            assert result > _tz.localtime()

        def it_returns_a_future_datetime_for_weekly():
            from django.utils import timezone as _tz

            settings = BillingSettingsFactory(
                charge_frequency=BillingSettings.ChargeFrequency.WEEKLY,
                charge_day_of_week=0,
                charge_day_of_month=None,
                charge_time="12:00",
            )
            result = settings.next_charge_at()
            assert result is not None
            assert result > _tz.localtime()
            assert result.weekday() == 0

        def it_returns_a_future_datetime_for_monthly():
            from django.utils import timezone as _tz

            settings = BillingSettingsFactory(
                charge_frequency=BillingSettings.ChargeFrequency.MONTHLY,
                charge_day_of_month=15,
                charge_day_of_week=None,
                charge_time="09:00",
            )
            result = settings.next_charge_at()
            assert result is not None
            assert result > _tz.localtime()
            assert result.day == 15

        def it_handles_an_already_parsed_time_object():
            from datetime import time as _time

            settings = BillingSettingsFactory(
                charge_frequency=BillingSettings.ChargeFrequency.DAILY,
                charge_time="23:00",
            )
            # Force the in-memory field to a time object (what comes back after a DB round-trip)
            settings.charge_time = _time(23, 0)
            result = settings.next_charge_at()
            assert result is not None

        def it_returns_today_when_weekly_target_is_today_and_time_is_future():
            from datetime import time as _time
            from unittest.mock import patch

            from django.utils import timezone as _tz

            now = _tz.localtime()
            settings = BillingSettingsFactory(
                charge_frequency=BillingSettings.ChargeFrequency.WEEKLY,
                charge_day_of_week=now.weekday(),
                charge_day_of_month=None,
                charge_time="23:00",
            )
            settings.charge_time = _time(23, 0)

            # Freeze now at 00:00 so today's 23:00 is still in the future
            fake_now = now.replace(hour=0, minute=0, second=0, microsecond=0)
            with patch("billing.models.timezone.localtime", return_value=fake_now):
                result = settings.next_charge_at()
            assert result is not None
            assert result.date() == fake_now.date()

        def it_skips_a_week_when_weekly_target_already_passed_today():
            from datetime import time as _time
            from unittest.mock import patch

            from django.utils import timezone as _tz

            now = _tz.localtime()
            # Pick weekly target = today, charge_time earlier than now so days_until=0 path hits 7
            settings = BillingSettingsFactory(
                charge_frequency=BillingSettings.ChargeFrequency.WEEKLY,
                charge_day_of_week=now.weekday(),
                charge_day_of_month=None,
                charge_time="00:00",
            )
            settings.charge_time = _time(0, 0)

            fake_now = now.replace(hour=23, minute=59, second=0, microsecond=0)
            with patch("billing.models.timezone.localtime", return_value=fake_now):
                result = settings.next_charge_at()
            assert result is not None
            assert (result.date() - fake_now.date()).days == 7

        def it_returns_candidate_when_candidate_is_future_in_current_month():
            from datetime import time as _time
            from unittest.mock import patch

            from django.utils import timezone as _tz

            # Freeze "now" to day 5 at 09:00; charge_day_of_month=28 makes candidate=day 28 > now
            now = _tz.localtime()
            fake_now = now.replace(day=5, hour=9, minute=0, second=0, microsecond=0)

            settings = BillingSettingsFactory(
                charge_frequency=BillingSettings.ChargeFrequency.MONTHLY,
                charge_day_of_month=28,
                charge_day_of_week=None,
                charge_time="09:00",
            )
            settings.charge_time = _time(9, 0)

            with patch("billing.models.timezone.localtime", return_value=fake_now):
                result = settings.next_charge_at()
            assert result is not None
            assert result.day == 28
            assert result.month == fake_now.month  # still in the same month

        def it_wraps_to_january_when_monthly_candidate_is_december():
            from datetime import time as _time
            from unittest.mock import patch

            from django.utils import timezone as _tz

            # Force "now" into December so the monthly candidate lands there
            current_year = _tz.localtime().year
            dec_31 = _tz.localtime().replace(
                year=current_year, month=12, day=31, hour=23, minute=59, second=0, microsecond=0
            )

            settings = BillingSettingsFactory(
                charge_frequency=BillingSettings.ChargeFrequency.MONTHLY,
                charge_day_of_month=15,
                charge_day_of_week=None,
                charge_time="09:00",
            )
            settings.charge_time = _time(9, 0)

            with patch("billing.models.timezone.localtime", return_value=dec_31):
                result = settings.next_charge_at()
            assert result is not None
            assert result.year == current_year + 1
            assert result.month == 1
            assert result.day == 15

    def describe_charge_frequency():
        def it_defaults_to_monthly():
            settings = BillingSettings.load()
            assert settings.charge_frequency == BillingSettings.ChargeFrequency.MONTHLY

        def it_accepts_all_valid_frequencies():
            for freq in BillingSettings.ChargeFrequency:
                settings = BillingSettings.load()
                settings.charge_frequency = freq
                if freq == BillingSettings.ChargeFrequency.WEEKLY:
                    settings.charge_day_of_week = 0
                    settings.charge_day_of_month = None
                elif freq == BillingSettings.ChargeFrequency.MONTHLY:
                    settings.charge_day_of_week = None
                    settings.charge_day_of_month = 1
                else:
                    settings.charge_day_of_week = None
                    settings.charge_day_of_month = None
                settings.save()
                settings.refresh_from_db()
                assert settings.charge_frequency == freq

    def describe_cross_field_constraints():
        def it_rejects_day_of_week_when_not_weekly():
            settings = BillingSettings.load()
            settings.charge_frequency = BillingSettings.ChargeFrequency.MONTHLY
            settings.charge_day_of_week = 3
            with pytest.raises(IntegrityError):
                settings.save()

        def it_rejects_day_of_month_when_not_monthly():
            settings = BillingSettings.load()
            settings.charge_frequency = BillingSettings.ChargeFrequency.WEEKLY
            settings.charge_day_of_month = 15
            settings.charge_day_of_week = 0
            with pytest.raises(IntegrityError):
                settings.save()

        def it_rejects_day_of_week_above_6():
            settings = BillingSettings.load()
            settings.charge_frequency = BillingSettings.ChargeFrequency.WEEKLY
            settings.charge_day_of_week = 7
            settings.charge_day_of_month = None
            with pytest.raises(IntegrityError):
                settings.save()

        def it_rejects_day_of_month_above_28():
            settings = BillingSettings.load()
            settings.charge_frequency = BillingSettings.ChargeFrequency.MONTHLY
            settings.charge_day_of_month = 29
            settings.charge_day_of_week = None
            with pytest.raises(IntegrityError):
                settings.save()

        def it_rejects_day_of_month_below_1():
            settings = BillingSettings.load()
            settings.charge_frequency = BillingSettings.ChargeFrequency.MONTHLY
            settings.charge_day_of_month = 0
            settings.charge_day_of_week = None
            with pytest.raises(IntegrityError):
                settings.save()

        def it_allows_day_of_week_when_weekly():
            settings = BillingSettings.load()
            settings.charge_frequency = BillingSettings.ChargeFrequency.WEEKLY
            settings.charge_day_of_week = 4
            settings.charge_day_of_month = None
            settings.save()
            settings.refresh_from_db()
            assert settings.charge_day_of_week == 4

        def it_allows_day_of_month_when_monthly():
            settings = BillingSettings.load()
            settings.charge_frequency = BillingSettings.ChargeFrequency.MONTHLY
            settings.charge_day_of_month = 28
            settings.charge_day_of_week = None
            settings.save()
            settings.refresh_from_db()
            assert settings.charge_day_of_month == 28

    def describe_test_mode_accessors():
        def it_defaults_test_mode_to_true():
            settings = BillingSettings.load()
            assert settings.test_mode is True

        def it_returns_test_slot_values_when_test_mode_on():
            settings = BillingSettings.load()
            settings.test_mode = True
            settings.test_connect_client_id = "ca_test"
            settings.test_connect_platform_publishable_key = "pk_test"
            settings.test_connect_platform_secret_key = "sk_test"
            settings.test_connect_platform_webhook_secret = "whsec_test"
            settings.connect_client_id = "ca_live"
            settings.connect_platform_publishable_key = "pk_live"
            settings.connect_platform_secret_key = "sk_live"
            settings.connect_platform_webhook_secret = "whsec_live"
            assert settings.active_client_id == "ca_test"
            assert settings.active_publishable_key == "pk_test"
            assert settings.active_secret_key == "sk_test"
            assert settings.active_webhook_secret == "whsec_test"

        def it_returns_live_slot_values_when_test_mode_off():
            settings = BillingSettings.load()
            settings.test_mode = False
            settings.test_connect_client_id = "ca_test"
            settings.test_connect_platform_publishable_key = "pk_test"
            settings.test_connect_platform_secret_key = "sk_test"
            settings.test_connect_platform_webhook_secret = "whsec_test"
            settings.connect_client_id = "ca_live"
            settings.connect_platform_publishable_key = "pk_live"
            settings.connect_platform_secret_key = "sk_live"
            settings.connect_platform_webhook_secret = "whsec_live"
            assert settings.active_client_id == "ca_live"
            assert settings.active_publishable_key == "pk_live"
            assert settings.active_secret_key == "sk_live"
            assert settings.active_webhook_secret == "whsec_live"

        def it_labels_mode_test_when_on():
            settings = BillingSettings.load()
            settings.test_mode = True
            assert settings.mode_label == "Test"

        def it_labels_mode_live_when_off():
            settings = BillingSettings.load()
            settings.test_mode = False
            assert settings.mode_label == "Live"

    def describe_clean_connect_validation():
        def it_passes_when_connect_disabled_and_fields_empty():
            settings = BillingSettings.load()
            settings.connect_enabled = False
            settings.clean()  # should not raise

        def it_passes_when_connect_enabled_and_all_live_fields_set():
            settings = BillingSettings.load()
            settings.connect_enabled = True
            settings.test_mode = False
            settings.connect_client_id = "ca_test_1"
            settings.connect_platform_publishable_key = "pk_test_1"
            settings.connect_platform_secret_key = "sk_test_1"
            settings.connect_platform_webhook_secret = "whsec_1"
            settings.clean()  # should not raise

        def it_raises_when_live_mode_enabled_with_missing_client_id():
            from django.core.exceptions import ValidationError

            settings = BillingSettings.load()
            settings.connect_enabled = True
            settings.test_mode = False
            settings.connect_platform_publishable_key = "pk_x"
            settings.connect_platform_secret_key = "sk_x"
            settings.connect_platform_webhook_secret = "whsec_x"
            settings.connect_client_id = ""
            with pytest.raises(ValidationError) as excinfo:
                settings.clean()
            assert "connect_client_id" in excinfo.value.message_dict

        def it_raises_when_live_mode_enabled_with_all_fields_missing():
            from django.core.exceptions import ValidationError

            settings = BillingSettings.load()
            settings.connect_enabled = True
            settings.test_mode = False
            settings.connect_client_id = ""
            settings.connect_platform_publishable_key = ""
            settings.connect_platform_secret_key = ""
            settings.connect_platform_webhook_secret = ""
            with pytest.raises(ValidationError) as excinfo:
                settings.clean()
            assert "connect_client_id" in excinfo.value.message_dict
            assert "connect_platform_publishable_key" in excinfo.value.message_dict
            assert "connect_platform_secret_key" in excinfo.value.message_dict
            assert "connect_platform_webhook_secret" in excinfo.value.message_dict

        def it_passes_when_test_mode_enabled_with_all_test_fields_set():
            settings = BillingSettings.load()
            settings.connect_enabled = True
            settings.test_mode = True
            settings.test_connect_client_id = "ca_test_1"
            settings.test_connect_platform_publishable_key = "pk_test_1"
            settings.test_connect_platform_secret_key = "sk_test_1"
            settings.test_connect_platform_webhook_secret = "whsec_test_1"
            settings.clean()  # should not raise

        def it_raises_when_test_mode_enabled_with_all_test_fields_missing():
            from django.core.exceptions import ValidationError

            settings = BillingSettings.load()
            settings.connect_enabled = True
            settings.test_mode = True
            settings.test_connect_client_id = ""
            settings.test_connect_platform_publishable_key = ""
            settings.test_connect_platform_secret_key = ""
            settings.test_connect_platform_webhook_secret = ""
            with pytest.raises(ValidationError) as excinfo:
                settings.clean()
            assert "test_connect_client_id" in excinfo.value.message_dict
            assert "test_connect_platform_publishable_key" in excinfo.value.message_dict
            assert "test_connect_platform_secret_key" in excinfo.value.message_dict
            assert "test_connect_platform_webhook_secret" in excinfo.value.message_dict

        def it_does_not_require_the_inactive_slot():
            # Test mode on, only the test slot filled, live slot blank → no error.
            settings = BillingSettings.load()
            settings.connect_enabled = True
            settings.test_mode = True
            settings.test_connect_client_id = "ca_test_1"
            settings.test_connect_platform_publishable_key = "pk_test_1"
            settings.test_connect_platform_secret_key = "sk_test_1"
            settings.test_connect_platform_webhook_secret = "whsec_test_1"
            settings.connect_client_id = ""
            settings.connect_platform_publishable_key = ""
            settings.connect_platform_secret_key = ""
            settings.connect_platform_webhook_secret = ""
            settings.clean()  # should not raise
