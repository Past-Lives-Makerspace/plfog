"""BDD specs for the reconciliation allocation engine (billing/reconciliation.py)."""

from __future__ import annotations

import csv
from decimal import Decimal

import pytest
from django.utils import timezone

from billing.models import (
    BillingSettings,
    PaymentRefund,
    ReconciliationSnapshot,
    TabCharge,
    TabEntrySplit,
    TransactionAdjustment,
)
from billing.payments_panel import build_payments_ledger, parse_window
from billing.reconciliation import (
    CSV_HEADERS,
    RecipientKind,
    build_reconciliation,
    result_from_snapshot,
    split_cents,
    stream_reconciliation_csv,
)
from tests.billing.factories import (
    PaymentRefundFactory,
    TabChargeFactory,
    TabEntryFactory,
    TabEntrySplitFactory,
    TabFactory,
)
from tests.membership.factories import GuildFactory, MemberFactory, OrientationBookingFactory, OrientationSlotFactory

pytestmark = pytest.mark.django_db

_D = Decimal


def _window():
    return parse_window("", "")


def _paid_registration(*, cents, instructor=None, guild=None, when=None):
    from classes.factories import CategoryFactory, ClassOfferingFactory, RegistrationFactory

    category = CategoryFactory(guild=guild)
    offering = ClassOfferingFactory(category=category, instructor=instructor)
    reg = RegistrationFactory(class_offering=offering, amount_paid_cents=cents)
    reg.confirmed_at = when or timezone.now()
    reg.save(update_fields=["confirmed_at"])
    return reg


def _paid_orientation(*, cents, guild=None, orientator=None, status=None, when=None):
    from membership.models import OrientationBooking

    guild = guild or GuildFactory()
    slot = OrientationSlotFactory(guild=guild)
    booking = OrientationBookingFactory(
        slot=slot,
        oriented_by=orientator,
        amount_paid_cents=cents,
        status=status or OrientationBooking.Status.CONFIRMED,
    )
    if when is not None:
        OrientationBooking.objects.filter(pk=booking.pk).update(requested_at=when)
        booking.refresh_from_db()
    return booking


def _succeeded_tab_charge(*, guild, admin_cents, guild_cents, when=None):
    tab = TabFactory()
    total = _D(admin_cents + guild_cents) / 100
    charge = TabChargeFactory(tab=tab, status=TabCharge.Status.SUCCEEDED, amount=total)
    when = when or timezone.now()
    TabCharge.objects.filter(pk=charge.pk).update(charged_at=when)
    entry = TabEntryFactory(tab=tab, tab_charge=charge, amount=total)
    TabEntrySplitFactory(
        entry=entry,
        recipient_type=TabEntrySplit.RecipientType.ADMIN,
        guild=None,
        percent=_D("20"),
        amount=_D(admin_cents) / 100,
    )
    TabEntrySplitFactory(
        entry=entry,
        recipient_type=TabEntrySplit.RecipientType.GUILD,
        guild=guild,
        percent=_D("80"),
        amount=_D(guild_cents) / 100,
    )
    return charge, entry


def _alloc(result, kind, label):
    for a in result.groups[kind]:
        if a.label == label:
            return a
    return None


def describe_split_cents():
    def it_returns_all_zero_for_zero_cents():
        assert split_cents(0, {"a": _D(70), "b": _D(15), "c": _D(15)}) == {"a": 0, "b": 0, "c": 0}

    def it_gives_the_largest_share_the_single_cent():
        assert split_cents(1, {"instructor": _D(70), "guild": _D(15), "pl": _D(15)}) == {
            "instructor": 1,
            "guild": 0,
            "pl": 0,
        }

    def it_balances_odd_amounts_on_a_701515_split():
        shares = split_cents(3, {"instructor": _D(70), "guild": _D(15), "pl": _D(15)})
        assert shares == {"instructor": 3, "guild": 0, "pl": 0}
        assert sum(shares.values()) == 3

    def it_balances_odd_amounts_on_a_701020_split():
        shares = split_cents(101, {"instructor": _D(70), "guild": _D(10), "pl": _D(20)})
        assert sum(shares.values()) == 101

    def it_absorbs_drift_into_the_largest_percent_row():
        shares = split_cents(10, {"a": _D(33), "b": _D(33), "c": _D(34)})
        assert shares == {"a": 3, "b": 3, "c": 4}

    def it_gives_a_tie_to_the_first_producer_key_not_pl():
        # Producer key comes first; on a 50/50 tie it is the drift absorber, not Past Lives.
        shares = split_cents(3, {"instructor": _D(50), "pl": _D(50)})
        assert shares == {"instructor": 1, "pl": 2}

    def it_rejects_percentages_that_do_not_sum_to_100():
        with pytest.raises(ValueError, match="sum to 100"):
            split_cents(100, {"a": _D(70), "b": _D(20), "pl": _D(20)})


def describe_class_stream():
    def it_splits_a_paid_registration_by_the_class_percentages():
        guild = GuildFactory(name="Woodworking")
        instructor = MemberFactory(full_legal_name="Ada Instructor")
        _paid_registration(cents=10000, instructor=instructor, guild=guild)
        result = build_reconciliation(window=_window(), adjustments={})
        assert _alloc(result, RecipientKind.INSTRUCTOR, instructor.display_name).total_cents == 7000
        assert _alloc(result, RecipientKind.GUILD, "Woodworking").total_cents == 1000
        assert _alloc(result, RecipientKind.PL, "Past Lives").total_cents == 2000
        assert result.grand_total_cents == 10000

    def it_rolls_an_unset_instructor_share_to_past_lives_with_a_note():
        guild = GuildFactory(name="Metal")
        _paid_registration(cents=10000, instructor=None, guild=guild)
        result = build_reconciliation(window=_window(), adjustments={})
        assert _alloc(result, RecipientKind.PL, "Past Lives").total_cents == 9000
        assert _alloc(result, RecipientKind.GUILD, "Metal").total_cents == 1000
        assert result.unassigned_note_count == 1
        assert "instructor unset" in result.lines[0].note

    def it_rolls_a_guildless_category_share_to_past_lives():
        instructor = MemberFactory()
        _paid_registration(cents=10000, instructor=instructor, guild=None)
        result = build_reconciliation(window=_window(), adjustments={})
        assert _alloc(result, RecipientKind.PL, "Past Lives").total_cents == 3000
        assert result.groups[RecipientKind.GUILD] == []
        assert result.unassigned_note_count == 1

    def it_splits_the_net_of_a_partial_refund():
        guild = GuildFactory()
        instructor = MemberFactory()
        reg = _paid_registration(cents=10000, instructor=instructor, guild=guild)
        PaymentRefundFactory(registration=reg, amount_cents=5000, status=PaymentRefund.Status.SUCCEEDED)
        result = build_reconciliation(window=_window(), adjustments={})
        assert result.grand_total_cents == 5000
        assert _alloc(result, RecipientKind.INSTRUCTOR, instructor.display_name).total_cents == 3500

    def it_nets_a_fully_refunded_registration_to_zero():
        guild = GuildFactory()
        instructor = MemberFactory()
        reg = _paid_registration(cents=10000, instructor=instructor, guild=guild)
        PaymentRefundFactory(registration=reg, amount_cents=10000, status=PaymentRefund.Status.SUCCEEDED)
        result = build_reconciliation(window=_window(), adjustments={})
        assert result.grand_total_cents == 0
        assert result.groups[RecipientKind.INSTRUCTOR] == []
        assert result.groups[RecipientKind.PL] == []

    def it_keeps_conservation_when_a_refund_exceeds_the_payment():
        # An over refund past the payment (the forms cap this, so it needs a data anomaly):
        # the negative net still shows, so the per recipient rows keep summing to the grand
        # total. Dropping negative rows would silently break conservation of money.
        guild = GuildFactory()
        instructor = MemberFactory()
        reg = _paid_registration(cents=10000, instructor=instructor, guild=guild)
        PaymentRefundFactory(registration=reg, amount_cents=13000, status=PaymentRefund.Status.SUCCEEDED)
        result = build_reconciliation(window=_window(), adjustments={})
        assert result.grand_total_cents == -3000
        rows_total = sum(alloc.total_cents for kind in result.groups for alloc in result.groups[kind])
        assert rows_total == result.grand_total_cents

    def it_falls_back_when_a_stored_override_does_not_sum_to_100():
        # The adjustment form validates overrides, but the model is editable in Django admin.
        # A malformed stored override must not 500 the tab: it falls back to the standard split.
        guild = GuildFactory()
        instructor = MemberFactory()
        reg = _paid_registration(cents=10000, instructor=instructor, guild=guild)
        adj = TransactionAdjustment.objects.create(
            source_kind=TransactionAdjustment.SourceKind.CLASS,
            source_pk=reg.pk,
            override_percents={"instructor": 50, "guild": 20, "pl": 20},  # sums to 90
        )
        result = build_reconciliation(window=_window(), adjustments={("class", reg.pk): adj})
        assert result.grand_total_cents == 10000
        assert _alloc(result, RecipientKind.INSTRUCTOR, instructor.display_name).total_cents == 7000
        # A malformed override is not an unassigned recipient, so it must not inflate the
        # "share went to Past Lives" banner count.
        assert result.unassigned_note_count == 0

    def it_falls_back_when_a_stored_override_is_missing_a_key():
        guild = GuildFactory()
        instructor = MemberFactory()
        reg = _paid_registration(cents=10000, instructor=instructor, guild=guild)
        adj = TransactionAdjustment.objects.create(
            source_kind=TransactionAdjustment.SourceKind.CLASS,
            source_pk=reg.pk,
            override_percents={"instructor": 80, "guild": 20},  # missing "pl"
        )
        result = build_reconciliation(window=_window(), adjustments={("class", reg.pk): adj})
        assert result.grand_total_cents == 10000
        assert _alloc(result, RecipientKind.INSTRUCTOR, instructor.display_name).total_cents == 7000


def describe_orientation_stream():
    def it_splits_a_paid_orientation_by_the_orientation_percentages():
        guild = GuildFactory(name="Ceramics")
        orientator = MemberFactory(full_legal_name="Bo Orientator")
        _paid_orientation(cents=10000, guild=guild, orientator=orientator)
        result = build_reconciliation(window=_window(), adjustments={})
        assert _alloc(result, RecipientKind.ORIENTATOR, orientator.display_name).total_cents == 7000
        assert _alloc(result, RecipientKind.GUILD, "Ceramics").total_cents == 1500
        assert _alloc(result, RecipientKind.PL, "Past Lives").total_cents == 1500

    def it_rolls_a_null_orientator_share_to_past_lives_with_a_note():
        guild = GuildFactory(name="Fiber")
        _paid_orientation(cents=10000, guild=guild, orientator=None)
        result = build_reconciliation(window=_window(), adjustments={})
        assert _alloc(result, RecipientKind.PL, "Past Lives").total_cents == 8500
        assert result.unassigned_note_count == 1

    def it_excludes_pending_payment_bookings():
        from membership.models import OrientationBooking

        guild = GuildFactory()
        _paid_orientation(cents=10000, guild=guild, status=OrientationBooking.Status.PENDING_PAYMENT)
        result = build_reconciliation(window=_window(), adjustments={})
        assert result.grand_total_cents == 0

    def it_nets_orientation_refunds():
        guild = GuildFactory()
        orientator = MemberFactory()
        booking = _paid_orientation(cents=10000, guild=guild, orientator=orientator)
        PaymentRefundFactory(
            registration=None,
            orientation_booking=booking,
            amount_cents=4000,
            status=PaymentRefund.Status.SUCCEEDED,
        )
        result = build_reconciliation(window=_window(), adjustments={})
        assert result.grand_total_cents == 6000


def describe_tab_stream():
    def it_attributes_admin_splits_to_pl_and_guild_splits_to_the_guild():
        guild = GuildFactory(name="Print")
        _succeeded_tab_charge(guild=guild, admin_cents=2000, guild_cents=8000)
        result = build_reconciliation(window=_window(), adjustments={})
        assert _alloc(result, RecipientKind.PL, "Past Lives").total_cents == 2000
        assert _alloc(result, RecipientKind.GUILD, "Print").total_cents == 8000
        assert result.grand_total_cents == 10000

    def it_excludes_failed_charges():
        guild = GuildFactory()
        tab = TabFactory()
        charge = TabChargeFactory(tab=tab, status=TabCharge.Status.FAILED, amount=_D("100.00"))
        TabCharge.objects.filter(pk=charge.pk).update(charged_at=timezone.now())
        entry = TabEntryFactory(tab=tab, tab_charge=charge, amount=_D("100.00"))
        TabEntrySplitFactory(
            entry=entry,
            recipient_type=TabEntrySplit.RecipientType.GUILD,
            guild=guild,
            percent=_D("100"),
            amount=_D("100.00"),
        )
        result = build_reconciliation(window=_window(), adjustments={})
        assert result.grand_total_cents == 0

    def it_excludes_voided_entries():
        guild = GuildFactory()
        tab = TabFactory()
        charge = TabChargeFactory(tab=tab, status=TabCharge.Status.SUCCEEDED, amount=_D("100.00"))
        TabCharge.objects.filter(pk=charge.pk).update(charged_at=timezone.now())
        entry = TabEntryFactory(tab=tab, tab_charge=charge, amount=_D("100.00"), voided_at=timezone.now())
        TabEntrySplitFactory(
            entry=entry,
            recipient_type=TabEntrySplit.RecipientType.GUILD,
            guild=guild,
            percent=_D("100"),
            amount=_D("100.00"),
        )
        result = build_reconciliation(window=_window(), adjustments={})
        assert result.grand_total_cents == 0


def describe_aggregation():
    def it_matches_the_payments_ledger_collected_minus_refunded():
        guild = GuildFactory()
        instructor = MemberFactory()
        orientator = MemberFactory()
        reg = _paid_registration(cents=10000, instructor=instructor, guild=guild)
        PaymentRefundFactory(registration=reg, amount_cents=2500, status=PaymentRefund.Status.SUCCEEDED)
        _paid_orientation(cents=5000, guild=guild, orientator=orientator)
        _succeeded_tab_charge(guild=guild, admin_cents=2000, guild_cents=8000)
        window = _window()
        result = build_reconciliation(window=window, adjustments={})
        ledger = build_payments_ledger(window=window)
        assert result.grand_total_cents == ledger.collected_cents - ledger.refunded_cents

    def it_places_a_late_month_edge_payment_in_the_right_window():
        from datetime import date, datetime
        from datetime import timezone as dt_timezone

        from billing.payments_panel import PanelWindow

        guild = GuildFactory()
        instructor = MemberFactory()
        # Confirmed just after UTC midnight on the 1st — still the prior day in Portland.
        edge = datetime(2026, 3, 1, 6, 30, tzinfo=dt_timezone.utc)
        _paid_registration(cents=10000, instructor=instructor, guild=guild, when=edge)
        feb = PanelWindow(start=date(2026, 2, 1), end=date(2026, 2, 28))
        result = build_reconciliation(window=feb, adjustments={})
        assert result.grand_total_cents == 10000


def describe_editable_splits():
    def it_reads_billing_settings_over_the_constants():
        settings_obj = BillingSettings.load()
        settings_obj.class_instructor_percent = _D("50.00")
        settings_obj.class_guild_percent = _D("30.00")
        settings_obj.class_pl_percent = _D("20.00")
        settings_obj.save()
        guild = GuildFactory()
        instructor = MemberFactory()
        _paid_registration(cents=10000, instructor=instructor, guild=guild)
        result = build_reconciliation(window=_window(), adjustments={})
        assert _alloc(result, RecipientKind.INSTRUCTOR, instructor.display_name).total_cents == 5000
        assert _alloc(result, RecipientKind.GUILD, guild.name).total_cents == 3000


def describe_adjustments():
    def it_omits_a_transaction_flagged_omitted():
        guild = GuildFactory()
        instructor = MemberFactory()
        reg = _paid_registration(cents=10000, instructor=instructor, guild=guild)
        TransactionAdjustment.objects.create(
            source_kind=TransactionAdjustment.SourceKind.CLASS,
            source_pk=reg.pk,
            is_omitted=True,
        )
        result = build_reconciliation(window=_window())
        assert result.grand_total_cents == 0
        assert result.omitted_count == 1

    def it_re_splits_a_transaction_with_override_percents():
        guild = GuildFactory()
        instructor = MemberFactory()
        reg = _paid_registration(cents=10000, instructor=instructor, guild=guild)
        TransactionAdjustment.objects.create(
            source_kind=TransactionAdjustment.SourceKind.CLASS,
            source_pk=reg.pk,
            override_percents={"instructor": 60, "guild": 20, "pl": 20},
        )
        result = build_reconciliation(window=_window())
        assert _alloc(result, RecipientKind.INSTRUCTOR, instructor.display_name).total_cents == 6000
        assert _alloc(result, RecipientKind.GUILD, guild.name).total_cents == 2000
        assert result.lines[0].overridden is True


def describe_csv():
    def it_streams_headers_and_one_row_per_recipient():
        guild = GuildFactory(name="Glass")
        instructor = MemberFactory()
        _paid_registration(cents=10000, instructor=instructor, guild=guild)
        result = build_reconciliation(window=_window(), adjustments={})
        response = stream_reconciliation_csv(result)
        body = b"".join(response.streaming_content).decode()
        rows = list(csv.reader(body.splitlines()))
        assert rows[0] == CSV_HEADERS
        labels = {r[1] for r in rows[1:]}
        assert "Glass" in labels
        assert "Past Lives" in labels
        assert response["Content-Disposition"].startswith("attachment")


def describe_voting_allocation():
    def it_projects_guild_voting_then_freezes_it_on_snapshot():
        from tests.membership.factories import VotePreferenceFactory

        g1 = GuildFactory(name="Wood")
        g2 = GuildFactory(name="Metal")
        g3 = GuildFactory(name="Clay")
        member = MemberFactory()
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        window = _window()

        projected = build_reconciliation(window=window)
        wood = _alloc(projected, RecipientKind.GUILD, "Wood")
        assert wood.voting_cents == 50000  # 5/10 points of the $1000 floor pool
        assert wood.voting_projected is True

        ReconciliationSnapshot.take(period_start=window.start, period_end=window.end)
        frozen = build_reconciliation(window=window)
        wood_frozen = _alloc(frozen, RecipientKind.GUILD, "Wood")
        assert wood_frozen.voting_cents == 50000
        assert wood_frozen.voting_projected is False


def describe_voting_toggle_and_frozen_reads():
    def it_skips_voting_entirely_when_disabled():
        guild = GuildFactory()
        instructor = MemberFactory()
        _paid_registration(cents=10000, instructor=instructor, guild=guild)
        result = build_reconciliation(window=_window(), adjustments={}, include_voting=False)
        assert _alloc(result, RecipientKind.GUILD, guild.name).voting_cents == 0

    def it_skips_frozen_guild_rows_without_a_recipient_id():
        from datetime import date

        from billing.payments_panel import PanelWindow
        from billing.reconciliation import resolve_voting_allocation

        window = PanelWindow(start=date(2026, 5, 1), end=date(2026, 5, 31))
        ReconciliationSnapshot.objects.create(
            period_start=window.start,
            period_end=window.end,
            results={
                "groups": [
                    {"kind": "guild", "rows": [{"recipient_id": None, "voting_cents": 100}]},
                    {"kind": "instructor", "rows": []},
                ]
            },
            grand_total_cents=0,
        )
        frozen, projected, _on = resolve_voting_allocation(window)
        assert frozen == {}
        assert projected is False


def describe_snapshot_roundtrip():
    def it_rebuilds_a_result_from_a_frozen_snapshot():
        guild = GuildFactory(name="Leather")
        instructor = MemberFactory()
        _paid_registration(cents=10000, instructor=instructor, guild=guild)
        window = _window()
        snapshot = ReconciliationSnapshot.take(period_start=window.start, period_end=window.end)
        result = result_from_snapshot(snapshot)
        assert result.is_snapshot is True
        assert result.grand_total_cents == 10000
        assert _alloc(result, RecipientKind.GUILD, "Leather").total_cents == 1000
