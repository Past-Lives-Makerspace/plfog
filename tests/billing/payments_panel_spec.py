"""BDD specs for the Payments tab aggregation (billing/payments_panel.py)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

import pytest
from django.utils import timezone

from billing.models import PaymentRefund, TabCharge
from billing.payments_panel import (
    MAX_ROWS,
    PanelWindow,
    _age_label,
    build_payments_ledger,
    parse_window,
    stream_payments_csv,
)
from classes.factories import RegistrationFactory
from classes.models import Registration
from tests.billing.factories import PaymentRefundFactory, TabChargeFactory, TabFactory

pytestmark = pytest.mark.django_db

_WINDOW = PanelWindow(start=date(2026, 8, 1), end=date(2026, 8, 31))


def _aware(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return timezone.make_aware(datetime(year, month, day, hour, 0))


def _paid_registration(**kwargs) -> Registration:
    defaults = {
        "status": Registration.Status.CONFIRMED,
        "amount_paid_cents": 5000,
        "stripe_payment_id": "pi_test_panel",
        "confirmed_at": _aware(2026, 8, 10),
    }
    defaults.update(kwargs)
    return RegistrationFactory(**defaults)


def describe_parse_window():
    def it_defaults_to_the_current_month():
        window = parse_window("", "")
        today = timezone.localdate()
        assert window.start == today.replace(day=1)
        assert window.end == today

    def it_uses_valid_iso_dates():
        window = parse_window("2026-08-01", "2026-08-15")
        assert window.start == date(2026, 8, 1)
        assert window.end == date(2026, 8, 15)

    def it_falls_back_per_field_on_garbage():
        window = parse_window("not-a-date", "2026-08-15")
        assert window.start == timezone.localdate().replace(day=1)
        assert window.end == date(2026, 8, 15)


def describe_age_label():
    def it_formats_minutes_hours_and_days():
        now = timezone.now()
        assert _age_label(now - timedelta(minutes=35)) == "35 m"
        assert _age_label(now - timedelta(hours=2)) == "2 h"
        assert _age_label(now - timedelta(days=3)) == "3 d"


def describe_build_payments_ledger():
    def it_merges_tab_and_class_rows_sorted_date_desc():
        charge = TabChargeFactory(status=TabCharge.Status.SUCCEEDED, charged_at=_aware(2026, 8, 5))
        registration = _paid_registration(confirmed_at=_aware(2026, 8, 20))
        ledger = build_payments_ledger(window=_WINDOW)
        assert [(r.source_kind, r.source_pk) for r in ledger.rows] == [
            ("class", registration.pk),
            ("tab", charge.pk),
        ]

    def it_excludes_pending_and_pending_checkout_tab_charges():
        TabChargeFactory(status=TabCharge.Status.PENDING)
        TabChargeFactory(status=TabCharge.Status.PENDING_CHECKOUT)
        ledger = build_payments_ledger(window=_WINDOW)
        assert ledger.rows == ()

    def it_excludes_unpaid_and_paymentless_registrations():
        RegistrationFactory(status=Registration.Status.CONFIRMED, amount_paid_cents=0, confirmed_at=_aware(2026, 8, 3))
        RegistrationFactory(
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=1000,
            stripe_payment_id="",
            confirmed_at=_aware(2026, 8, 3),
        )
        ledger = build_payments_ledger(window=_WINDOW)
        assert ledger.rows == ()

    def describe_class_status_derivation():
        def it_shows_paid_with_no_refunds():
            _paid_registration()
            (row,) = build_payments_ledger(window=_WINDOW).rows
            assert (row.status, row.status_label, row.can_refund) == ("paid", "Paid", True)

        def it_shows_partially_refunded():
            registration = _paid_registration()
            PaymentRefundFactory(registration=registration, amount_cents=2000, status=PaymentRefund.Status.SUCCEEDED)
            (row,) = build_payments_ledger(window=_WINDOW).rows
            assert row.status == "partial"
            assert row.can_refund is True

        def it_shows_refunded_when_fully_covered():
            registration = _paid_registration()
            PaymentRefundFactory(registration=registration, amount_cents=5000, status=PaymentRefund.Status.SUCCEEDED)
            (row,) = build_payments_ledger(window=_WINDOW).rows
            assert row.status == "refunded"
            assert row.can_refund is False

        def it_shows_refund_failed_with_the_retry_anchor():
            registration = _paid_registration()
            failed = PaymentRefundFactory(
                registration=registration, amount_cents=5000, status=PaymentRefund.Status.FAILED
            )
            (row,) = build_payments_ledger(window=_WINDOW).rows
            assert (row.status, row.status_label) == ("refund_failed", "Refund failed")
            assert row.failed_refund_pk == failed.pk

        def it_shows_refund_pending_with_an_age():
            registration = _paid_registration()
            refund = PaymentRefundFactory(
                registration=registration, amount_cents=5000, status=PaymentRefund.Status.PENDING
            )
            PaymentRefund.objects.filter(pk=refund.pk).update(created_at=timezone.now() - timedelta(hours=2))
            (row,) = build_payments_ledger(window=_WINDOW).rows
            assert (row.status, row.pending_age) == ("refund_pending", "2 h")

    def describe_tab_status_derivation():
        def it_labels_a_failed_charge_charge_failed():
            TabChargeFactory(status=TabCharge.Status.FAILED, stripe_payment_intent_id="pi_tab_fail")
            (row,) = build_payments_ledger(window=_WINDOW, source="tab").rows
            assert (row.status, row.status_label) == ("charge_failed", "Charge failed")
            assert row.can_refund is False
            assert row.stripe_url == "https://dashboard.stripe.com/payments/pi_tab_fail"

        def it_prefers_the_receipt_url_and_carries_the_tab_modal_opener():
            charge = TabChargeFactory(
                status=TabCharge.Status.SUCCEEDED,
                charged_at=_aware(2026, 8, 5),
                stripe_receipt_url="https://stripe.example/receipt",
            )
            (row,) = build_payments_ledger(window=_WINDOW).rows
            assert row.stripe_url == "https://stripe.example/receipt"
            assert row.tab_pk == charge.tab_id

        def it_uses_created_at_when_charged_at_is_missing():
            charge = TabChargeFactory(status=TabCharge.Status.FAILED)
            TabCharge.objects.filter(pk=charge.pk).update(created_at=_aware(2026, 8, 7))
            (row,) = build_payments_ledger(window=_WINDOW).rows
            assert row.date == _aware(2026, 8, 7)

    def describe_the_failed_filter_chip():
        def it_matches_both_failed_meanings():
            registration = _paid_registration()
            PaymentRefundFactory(registration=registration, amount_cents=5000, status=PaymentRefund.Status.FAILED)
            TabChargeFactory(status=TabCharge.Status.FAILED)
            TabChargeFactory(status=TabCharge.Status.SUCCEEDED, charged_at=_aware(2026, 8, 5))
            ledger = build_payments_ledger(window=_WINDOW, status="failed")
            assert sorted(row.status for row in ledger.rows) == ["charge_failed", "refund_failed"]

        def it_filters_paid_rows_only():
            _paid_registration()
            TabChargeFactory(status=TabCharge.Status.FAILED)
            ledger = build_payments_ledger(window=_WINDOW, status="paid")
            assert [row.status for row in ledger.rows] == ["paid"]

        def it_treats_an_unknown_status_like_all():
            _paid_registration()
            ledger = build_payments_ledger(window=_WINDOW, status="bogus")
            assert len(ledger.rows) == 1

    def describe_source_filter():
        def it_limits_to_one_source():
            _paid_registration()
            TabChargeFactory(status=TabCharge.Status.SUCCEEDED, charged_at=_aware(2026, 8, 5))
            assert [r.source_kind for r in build_payments_ledger(window=_WINDOW, source="class").rows] == ["class"]
            assert [r.source_kind for r in build_payments_ledger(window=_WINDOW, source="tab").rows] == ["tab"]

        def it_returns_no_orientation_rows_yet():
            ledger = build_payments_ledger(window=_WINDOW, source="orientation")
            assert ledger.rows == ()

    def describe_payer_identity():
        def it_uses_the_member_display_name_when_linked():
            from tests.membership.factories import MemberFactory

            member = MemberFactory(preferred_name="Moss")
            _paid_registration(member=member)
            (row,) = build_payments_ledger(window=_WINDOW).rows
            assert row.payer_name == "Moss"

        def it_falls_back_to_the_guest_name():
            _paid_registration(member=None, first_name="Guest", last_name="Payer")
            (row,) = build_payments_ledger(window=_WINDOW).rows
            assert row.payer_name == "Guest Payer"

        def it_falls_back_to_the_email_when_nameless():
            _paid_registration(member=None, first_name="", last_name="", email="ghost@example.com")
            (row,) = build_payments_ledger(window=_WINDOW).rows
            assert row.payer_name == "ghost@example.com"

        def it_links_the_payer_for_admin_viewers_even_guests():
            registration = _paid_registration(member=None, first_name="Guest", last_name="Payer")
            (row,) = build_payments_ledger(window=_WINDOW, viewer_is_admin=True).rows
            assert row.payer_url == f"/classes/admin/registrations/{registration.pk}/"

        def it_leaves_the_payer_unlinked_for_non_admin_viewers():
            _paid_registration()
            (row,) = build_payments_ledger(window=_WINDOW, viewer_is_admin=False).rows
            assert row.payer_url is None

    def describe_date_window():
        def it_buckets_by_the_project_timezone_not_utc():
            # 05:30 UTC on Sep 1 is still Aug 31 in Portland — the row belongs to August.
            registration = _paid_registration(confirmed_at=datetime(2026, 9, 1, 5, 30, tzinfo=dt_timezone.utc))
            ledger = build_payments_ledger(window=_WINDOW)
            assert [row.source_pk for row in ledger.rows] == [registration.pk]

        def it_excludes_rows_outside_the_window():
            _paid_registration(confirmed_at=_aware(2026, 7, 31))
            _paid_registration(confirmed_at=_aware(2026, 9, 1))
            assert build_payments_ledger(window=_WINDOW).rows == ()

    def describe_totals():
        def it_computes_collected_refunded_and_net():
            registration = _paid_registration()
            PaymentRefundFactory(registration=registration, amount_cents=2000, status=PaymentRefund.Status.SUCCEEDED)
            TabChargeFactory(status=TabCharge.Status.SUCCEEDED, charged_at=_aware(2026, 8, 5), amount=Decimal("10.00"))
            TabChargeFactory(status=TabCharge.Status.FAILED, charged_at=_aware(2026, 8, 6))
            ledger = build_payments_ledger(window=_WINDOW)
            assert ledger.collected_cents == 6000  # failed tab charge never collected
            assert ledger.refunded_cents == 2000
            assert ledger.net_cents == 4000

    def describe_the_row_cap():
        def it_caps_at_500_rows_and_reports_the_total(monkeypatch):
            tab = TabFactory()
            for day in (5, 6, 7):
                TabChargeFactory(tab=tab, status=TabCharge.Status.SUCCEEDED, charged_at=_aware(2026, 8, day))
            monkeypatch.setattr("billing.payments_panel.MAX_ROWS", 2)
            ledger = build_payments_ledger(window=_WINDOW)
            assert len(ledger.rows) == 2
            assert ledger.total_count == 3
            assert ledger.capped is True

        def it_is_uncapped_under_the_limit():
            TabChargeFactory(status=TabCharge.Status.SUCCEEDED, charged_at=_aware(2026, 8, 5))
            ledger = build_payments_ledger(window=_WINDOW)
            assert ledger.capped is False
            assert MAX_ROWS == 500


def _csv_lines(ledger) -> list[str]:
    response = stream_payments_csv(ledger)
    assert response["Content-Type"] == "text/csv"
    assert "plfog-payments-" in response["Content-Disposition"]
    body = "".join(part.decode() if isinstance(part, bytes) else part for part in response.streaming_content)
    return body.splitlines()


def describe_stream_payments_csv():
    def it_streams_the_table_columns_plus_refund_detail():
        registration = _paid_registration()
        PaymentRefundFactory(
            registration=registration,
            amount_cents=2000,
            status=PaymentRefund.Status.SUCCEEDED,
            settled_at=_aware(2026, 8, 12),
        )
        ledger = build_payments_ledger(window=_WINDOW)
        lines = _csv_lines(ledger)
        assert lines[0].startswith("Date,Source,Payer,Item,Amount,Status,Refund Status")
        assert "Succeeded,20.00,1,Issued in app,2026-08-12" in lines[1]

    def it_leaves_refund_columns_blank_without_refunds():
        TabChargeFactory(status=TabCharge.Status.SUCCEEDED, charged_at=_aware(2026, 8, 5))
        ledger = build_payments_ledger(window=_WINDOW)
        lines = _csv_lines(ledger)
        assert lines[1].endswith(",,,,")
