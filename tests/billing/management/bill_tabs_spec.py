"""BDD-style tests for the bill_tabs management command."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from billing.models import BillingSettings, TabCharge
from core.models import SiteConfiguration
from tests.billing.factories import (
    BillingSettingsFactory,
    ProductFactory,
    TabChargeFactory,
    TabEntryFactory,
    TabFactory,
)
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db


def _call_bill_tabs(**kwargs: object) -> str:
    out = StringIO()
    call_command("bill_tabs", stdout=out, **kwargs)
    return out.getvalue()


def describe_bill_tabs():
    def describe_locking():
        @patch("billing.management.commands.bill_tabs.Command._acquire_lock", return_value=False)
        def it_exits_when_lock_not_acquired(mock_lock):
            output = _call_bill_tabs()
            assert "Another billing run" in output

    def describe_schedule():
        def it_exits_when_tab_payments_disabled():
            BillingSettingsFactory()
            config = SiteConfiguration.load()
            config.tab_payments_enabled = False
            config.save(update_fields=["tab_payments_enabled"])
            output = _call_bill_tabs(force=True)
            assert "disabled" in output

        def it_exits_when_billing_is_off():
            BillingSettingsFactory(charge_frequency=BillingSettings.ChargeFrequency.OFF)
            output = _call_bill_tabs()
            assert "turned off" in output

        @patch("billing.management.commands.bill_tabs.Command._is_billing_time", return_value=False)
        def it_exits_when_not_billing_time(mock_time):
            BillingSettingsFactory(charge_frequency=BillingSettings.ChargeFrequency.MONTHLY)
            output = _call_bill_tabs()
            assert "Not time" in output

        @patch("billing.management.commands.bill_tabs.Command._is_billing_time", return_value=True)
        def it_runs_when_billing_time(mock_time):
            BillingSettingsFactory()
            output = _call_bill_tabs()
            assert "Billing complete" in output

        def it_runs_with_force_flag():
            BillingSettingsFactory()
            output = _call_bill_tabs(force=True)
            assert "Billing complete" in output

    def describe_is_billing_time():
        def it_returns_true_for_daily():
            settings = BillingSettingsFactory(charge_frequency=BillingSettings.ChargeFrequency.DAILY)
            from billing.management.commands.bill_tabs import Command

            cmd = Command()
            assert cmd._is_billing_time(settings) is True

        @patch("billing.management.commands.bill_tabs.timezone")
        def it_returns_true_for_weekly_on_correct_day(mock_tz):
            mock_tz.localtime.return_value = type("FakeTime", (), {"weekday": lambda self: 2})()
            settings = BillingSettingsFactory(
                charge_frequency=BillingSettings.ChargeFrequency.WEEKLY,
                charge_day_of_week=2,
                charge_day_of_month=None,
            )
            from billing.management.commands.bill_tabs import Command

            cmd = Command()
            assert cmd._is_billing_time(settings) is True

        @patch("billing.management.commands.bill_tabs.timezone")
        def it_returns_false_for_weekly_on_wrong_day(mock_tz):
            mock_tz.localtime.return_value = type("FakeTime", (), {"weekday": lambda self: 3})()
            settings = BillingSettingsFactory(
                charge_frequency=BillingSettings.ChargeFrequency.WEEKLY,
                charge_day_of_week=2,
                charge_day_of_month=None,
            )
            from billing.management.commands.bill_tabs import Command

            cmd = Command()
            assert cmd._is_billing_time(settings) is False

        @patch("billing.management.commands.bill_tabs.timezone")
        def it_returns_true_for_monthly_on_correct_day(mock_tz):
            mock_tz.localtime.return_value = type("FakeTime", (), {"day": 15})()
            settings = BillingSettingsFactory(
                charge_frequency=BillingSettings.ChargeFrequency.MONTHLY,
                charge_day_of_month=15,
                charge_day_of_week=None,
            )
            from billing.management.commands.bill_tabs import Command

            cmd = Command()
            assert cmd._is_billing_time(settings) is True

        @patch("billing.management.commands.bill_tabs.timezone")
        def it_returns_false_for_monthly_on_wrong_day(mock_tz):
            mock_tz.localtime.return_value = type("FakeTime", (), {"day": 14})()
            settings = BillingSettingsFactory(
                charge_frequency=BillingSettings.ChargeFrequency.MONTHLY,
                charge_day_of_month=15,
                charge_day_of_week=None,
            )
            from billing.management.commands.bill_tabs import Command

            cmd = Command()
            assert cmd._is_billing_time(settings) is False

        def it_returns_false_for_off():
            settings = BillingSettingsFactory(
                charge_frequency=BillingSettings.ChargeFrequency.OFF,
                charge_day_of_week=None,
                charge_day_of_month=None,
            )
            from billing.management.commands.bill_tabs import Command

            cmd = Command()
            assert cmd._is_billing_time(settings) is False

    def describe_processing():
        @patch("billing.stripe_utils.create_payment_intent")
        @patch("billing.management.commands.bill_tabs.send_receipt")
        def it_charges_tabs_with_pending_entries(mock_receipt, mock_stripe):
            mock_stripe.return_value = {
                "id": "pi_test",
                "status": "succeeded",
                "charge_id": "ch_test",
                "receipt_url": "https://stripe.com/receipt",
            }
            BillingSettingsFactory()
            member = MemberFactory(status="active")
            tab = TabFactory(
                member=member,
                stripe_customer_id="cus_test",
                stripe_payment_method_id="pm_test",
            )
            TabEntryFactory(tab=tab, amount=Decimal("30.00"))
            TabEntryFactory(tab=tab, amount=Decimal("20.00"))

            output = _call_bill_tabs(force=True)

            assert "1 charged" in output
            charge = TabCharge.objects.get(tab=tab)
            assert charge.status == TabCharge.Status.SUCCEEDED
            assert charge.amount == Decimal("50.00")
            assert charge.stripe_payment_intent_id == "pi_test"
            mock_receipt.assert_called_once()

        @patch("billing.stripe_utils.create_payment_intent")
        @patch("billing.management.commands.bill_tabs.send_receipt")
        def it_creates_one_tabcharge_for_entries_across_multiple_guilds(mock_receipt, mock_stripe):
            """Since v1.5.0 we batch all pending entries into one charge per tab."""
            mock_stripe.return_value = {
                "id": "pi_batch",
                "status": "succeeded",
                "charge_id": "ch_batch",
                "receipt_url": "",
            }
            BillingSettingsFactory()
            member = MemberFactory(status="active")
            tab = TabFactory(
                member=member,
                stripe_customer_id="cus_test",
                stripe_payment_method_id="pm_test",
            )
            guild_a = GuildFactory()
            guild_b = GuildFactory()
            product_a = ProductFactory(guild=guild_a, price=Decimal("12.00"))
            product_b = ProductFactory(guild=guild_b, price=Decimal("8.00"))
            tab.add_entry(description=product_a.name, amount=product_a.price, product=product_a)
            tab.add_entry(description=product_b.name, amount=product_b.price, product=product_b)
            # Manual (non-product) entries require an explicit splits payload;
            # use a 100% admin split for this case.
            tab.add_entry(
                description="manual",
                amount=Decimal("5.00"),
                splits=[{"recipient_type": "admin", "guild": None, "percent": Decimal("100")}],
            )

            output = _call_bill_tabs(force=True)

            assert "1 charged" in output
            # Exactly one TabCharge, covering all three entries
            charges = list(TabCharge.objects.filter(tab=tab))
            assert len(charges) == 1
            assert charges[0].amount == Decimal("25.00")
            assert charges[0].entry_count == 3
            # Platform path, single PaymentIntent call
            assert mock_stripe.call_count == 1

        def it_skips_inactive_members():
            BillingSettingsFactory()
            member = MemberFactory(status="former")
            tab = TabFactory(member=member, stripe_customer_id="cus_test", stripe_payment_method_id="pm_test")
            TabEntryFactory(tab=tab, amount=Decimal("30.00"))

            output = _call_bill_tabs(force=True)

            assert "0 charged" in output

        @patch("billing.stripe_utils.create_payment_intent")
        @patch("billing.management.commands.bill_tabs.send_receipt")
        def it_skips_zero_balance(mock_receipt, mock_stripe):
            BillingSettingsFactory()
            member = MemberFactory(status="active")
            TabFactory(member=member, stripe_customer_id="cus_test", stripe_payment_method_id="pm_test")
            # No entries — zero balance

            output = _call_bill_tabs(force=True)

            assert "0 charged" in output
            mock_stripe.assert_not_called()

        @patch("billing.stripe_utils.create_payment_intent")
        @patch("billing.management.commands.bill_tabs.send_receipt")
        def it_skips_sub_minimum_balance(mock_receipt, mock_stripe):
            BillingSettingsFactory()
            member = MemberFactory(status="active")
            tab = TabFactory(member=member, stripe_customer_id="cus_test", stripe_payment_method_id="pm_test")
            TabEntryFactory(tab=tab, amount=Decimal("0.25"))

            output = _call_bill_tabs(force=True)

            assert "0 charged" in output

        def it_skips_tabs_without_payment_method():
            BillingSettingsFactory()
            member = MemberFactory(status="active")
            tab = TabFactory(member=member, stripe_customer_id="cus_test", stripe_payment_method_id="")
            # Tab has no payment method — can_add_entry now returns False, so we
            # can't call tab.add_entry. Instead write a row directly via the factory.
            TabEntryFactory(tab=tab, amount=Decimal("30.00"))

            output = _call_bill_tabs(force=True)

            assert "0 charged" in output

        def it_skips_tabs_without_customer_id():
            BillingSettingsFactory()
            member = MemberFactory(status="active")
            tab = TabFactory(member=member, stripe_customer_id="", stripe_payment_method_id="pm_test")
            TabEntryFactory(tab=tab, amount=Decimal("30.00"))

            output = _call_bill_tabs(force=True)

            assert "0 charged" in output

        @patch("billing.stripe_utils.create_payment_intent")
        @patch("billing.management.commands.bill_tabs.notify_admin_charge_failed")
        def it_handles_stripe_failure(mock_notify, mock_stripe):
            mock_stripe.side_effect = Exception("Card declined")
            BillingSettingsFactory(retry_interval_hours=24)
            member = MemberFactory(status="active")
            tab = TabFactory(member=member, stripe_customer_id="cus_test", stripe_payment_method_id="pm_test")
            TabEntryFactory(tab=tab, amount=Decimal("30.00"))

            output = _call_bill_tabs(force=True)

            assert "0 charged, 1 skipped" in output
            charge = TabCharge.objects.get(tab=tab)
            assert charge.status == TabCharge.Status.FAILED
            assert charge.retry_count == 1
            assert charge.next_retry_at is not None
            mock_notify.assert_called_once()

        @patch("billing.stripe_utils.create_payment_intent")
        @patch("billing.management.commands.bill_tabs.send_receipt")
        def it_excludes_voided_entries_from_charge(mock_receipt, mock_stripe):
            mock_stripe.return_value = {
                "id": "pi_test",
                "status": "succeeded",
                "charge_id": "ch_test",
                "receipt_url": "",
            }
            BillingSettingsFactory()
            member = MemberFactory(status="active")
            tab = TabFactory(member=member, stripe_customer_id="cus_test", stripe_payment_method_id="pm_test")
            TabEntryFactory(tab=tab, amount=Decimal("30.00"))
            voided = TabEntryFactory(tab=tab, amount=Decimal("20.00"))
            from tests.billing.factories import UserFactory

            voided.void(user=UserFactory(), reason="test")

            output = _call_bill_tabs(force=True)

            assert "1 charged" in output
            charge = TabCharge.objects.get(tab=tab)
            assert charge.amount == Decimal("30.00")

    def describe_advisory_lock():
        def it_acquire_lock_returns_true_for_sqlite():
            from billing.management.commands.bill_tabs import Command

            cmd = Command()
            assert cmd._acquire_lock() is True

        def it_acquire_lock_uses_pg_try_advisory_lock_for_postgres():
            from unittest.mock import MagicMock, patch

            from billing.management.commands.bill_tabs import Command

            cmd = Command()
            mock_cursor = MagicMock()
            mock_cursor.__enter__ = lambda s: s
            mock_cursor.__exit__ = MagicMock(return_value=False)
            mock_cursor.fetchone.return_value = (True,)
            mock_connection = MagicMock()
            mock_connection.vendor = "postgresql"
            mock_connection.cursor.return_value = mock_cursor
            with patch("billing.management.commands.bill_tabs.connection", mock_connection):
                result = cmd._acquire_lock()
            assert result is True
            mock_cursor.execute.assert_called_once()
            call_sql = mock_cursor.execute.call_args[0][0]
            assert "pg_try_advisory_lock" in call_sql

        def it_acquire_lock_returns_false_when_lock_held():
            from unittest.mock import MagicMock, patch

            from billing.management.commands.bill_tabs import Command

            cmd = Command()
            mock_cursor = MagicMock()
            mock_cursor.__enter__ = lambda s: s
            mock_cursor.__exit__ = MagicMock(return_value=False)
            mock_cursor.fetchone.return_value = (False,)
            mock_connection = MagicMock()
            mock_connection.vendor = "postgresql"
            mock_connection.cursor.return_value = mock_cursor
            with patch("billing.management.commands.bill_tabs.connection", mock_connection):
                result = cmd._acquire_lock()
            assert result is False

        def it_release_lock_does_nothing_for_sqlite():
            from billing.management.commands.bill_tabs import Command

            cmd = Command()
            cmd._release_lock()  # Should not raise

        def it_release_lock_calls_pg_advisory_unlock_for_postgres():
            from unittest.mock import MagicMock, patch

            from billing.management.commands.bill_tabs import Command

            cmd = Command()
            mock_cursor = MagicMock()
            mock_cursor.__enter__ = lambda s: s
            mock_cursor.__exit__ = MagicMock(return_value=False)
            mock_connection = MagicMock()
            mock_connection.vendor = "postgresql"
            mock_connection.cursor.return_value = mock_cursor
            with patch("billing.management.commands.bill_tabs.connection", mock_connection):
                cmd._release_lock()
            mock_cursor.execute.assert_called_once()
            call_sql = mock_cursor.execute.call_args[0][0]
            assert "pg_advisory_unlock" in call_sql

    def describe_retries():
        @patch("billing.stripe_utils.create_payment_intent")
        @patch("billing.management.commands.bill_tabs.send_receipt")
        def it_retries_failed_charges(mock_receipt, mock_stripe):
            mock_stripe.return_value = {
                "id": "pi_retry",
                "status": "succeeded",
                "charge_id": "ch_retry",
                "receipt_url": "https://stripe.com/receipt",
            }
            BillingSettingsFactory(max_retry_attempts=3)
            member = MemberFactory(status="active")
            tab = TabFactory(member=member, stripe_customer_id="cus_test", stripe_payment_method_id="pm_test")
            TabChargeFactory(
                tab=tab,
                status=TabCharge.Status.FAILED,
                amount=Decimal("50.00"),
                retry_count=1,
                next_retry_at=timezone.now() - timedelta(hours=1),
            )

            output = _call_bill_tabs(force=True)

            assert "1 retried" in output
            charge = TabCharge.objects.get(tab=tab)
            assert charge.status == TabCharge.Status.SUCCEEDED

        @patch("billing.stripe_utils.create_payment_intent")
        @patch("billing.management.commands.bill_tabs.notify_admin_charge_failed")
        def it_locks_tab_after_max_retries(mock_notify, mock_stripe):
            mock_stripe.side_effect = Exception("Still declined")
            BillingSettingsFactory(max_retry_attempts=3)
            member = MemberFactory(status="active")
            tab = TabFactory(member=member, stripe_customer_id="cus_test", stripe_payment_method_id="pm_test")
            TabChargeFactory(
                tab=tab,
                status=TabCharge.Status.FAILED,
                amount=Decimal("50.00"),
                retry_count=2,
                next_retry_at=timezone.now() - timedelta(hours=1),
            )

            _call_bill_tabs(force=True)

            tab.refresh_from_db()
            assert tab.is_locked is True
            assert "3 attempts" in tab.locked_reason
            mock_notify.assert_called_once()

        @patch("billing.stripe_utils.create_payment_intent")
        def it_schedules_next_retry_when_retries_remain(mock_stripe):
            mock_stripe.side_effect = Exception("Declined again")
            BillingSettingsFactory(max_retry_attempts=3, retry_interval_hours=12)
            member = MemberFactory(status="active")
            tab = TabFactory(member=member, stripe_customer_id="cus_test", stripe_payment_method_id="pm_test")
            TabChargeFactory(
                tab=tab,
                status=TabCharge.Status.FAILED,
                amount=Decimal("50.00"),
                retry_count=1,
                next_retry_at=timezone.now() - timedelta(hours=1),
            )

            _call_bill_tabs(force=True)

            charge = TabCharge.objects.get(tab=tab)
            assert charge.retry_count == 2
            assert charge.next_retry_at is not None
            tab.refresh_from_db()
            assert tab.is_locked is False

        def it_skips_retry_when_no_payment_method():
            BillingSettingsFactory(max_retry_attempts=3)
            member = MemberFactory(status="active")
            tab = TabFactory(member=member, stripe_customer_id="cus_test", stripe_payment_method_id="")
            TabChargeFactory(
                tab=tab,
                status=TabCharge.Status.FAILED,
                amount=Decimal("50.00"),
                retry_count=1,
                next_retry_at=timezone.now() - timedelta(hours=1),
            )

            output = _call_bill_tabs(force=True)

            assert "0 retried" in output
