"""#1249: the treasurer marks Owed now shares paid by hand, so Owed now shows only what is left to pay."""

from __future__ import annotations

import csv
from collections.abc import Iterator
import io
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse

from billing.models import BillingSettings, Payout, PayoutAccount, ReconciliationSnapshot
from billing.payments_panel import PanelWindow
from billing.payouts import LineState, ShareNotOwedError, mark_paid_by_hand, run_payouts, undo_paid_by_hand
from billing.reconciliation import (
    RecipientKind,
    ReconciliationResult,
    build_reconciliation,
    mark_row_paid_by_hand,
    stream_reconciliation_csv,
)
from classes.factories import ClassOfferingFactory, ClassSessionFactory, RegistrationFactory
from classes.models import ClassSession, Registration
from membership.models import Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
OCTOBER = PanelWindow(start=date(2026, 10, 1), end=date(2026, 10, 31))
SEPTEMBER = PanelWindow(start=date(2026, 9, 1), end=date(2026, 9, 30))
OCTOBER_POST = {"start": "2026-10-01", "end": "2026-10-31"}


@pytest.fixture(autouse=True)
def _frozen_now() -> Iterator[None]:
    with patch("django.utils.timezone.now", return_value=NOW):
        yield


@pytest.fixture(autouse=True)
def _split() -> None:
    """The 70/10/20 class split; payouts stay off unless a spec turns them on, so every share is by hand."""
    bs = BillingSettings.load()
    bs.class_instructor_percent = Decimal("70")
    bs.class_guild_percent = Decimal("10")
    bs.class_pl_percent = Decimal("20")
    bs.save()


@pytest.fixture
def admin(client: Client) -> User:
    user = User.objects.create_superuser(username="treasurer", password="pass", email="treasurer@example.com")
    client.login(username="treasurer", password="pass")
    return user


def _instructor(name: str = "Paidby Handtest") -> Member:
    return MemberFactory(full_legal_name=name, preferred_name="")


def _registration(
    instructor: Member, *, paid_at: datetime = NOW - timedelta(days=10), class_at: datetime = NOW - timedelta(days=5)
) -> Registration:
    """A paid registration; by default its class happened Oct 15, so its share fell due Oct 17 and is Owed now."""
    offering = ClassOfferingFactory(instructor=instructor)
    ClassSessionFactory(class_offering=offering, starts_at=class_at)
    reg = RegistrationFactory(class_offering=offering, amount_paid_cents=10000, stripe_payment_id="pi_1249")
    Registration.objects.filter(pk=reg.pk).update(confirmed_at=paid_at)
    reg.refresh_from_db()
    return reg


def _payout(reg: Registration, status: str, **fields: Any) -> Payout:
    return Payout.objects.create(
        registration=reg,
        payee=reg.class_offering.instructor,
        amount_cents=7000,
        due_at=NOW - timedelta(days=3),
        status=status,
        **fields,
    )


def _row(result: ReconciliationResult, member: Member) -> Any:
    return next(alloc for alloc in result.groups[RecipientKind.INSTRUCTOR] if alloc.recipient_id == member.pk)


def _line(result: ReconciliationResult, reg: Registration) -> Any:
    return next(
        line
        for line in [*result.lines, *result.due_earlier_lines]
        if (line.source_kind, line.source_pk) == ("class", reg.pk)
    )


def _triggers(response: Any) -> dict[str, Any]:
    return json.loads(response["HX-Trigger"])


def _table(client: Client, query: dict[str, str] = OCTOBER_POST) -> str:
    return client.get(reverse("billing_admin_reconciliation_table"), query).content.decode()


def describe_marking_one_share() -> None:
    """AC1: Mark paid on an Owed now line in Transaction Detail, after a confirm."""

    def it_records_a_share_owed_with_no_payout_row_yet_and_tells_nobody() -> None:
        reg = _registration(_instructor())
        user = User.objects.create_user(username="t2", password="x")
        assert mark_paid_by_hand([("class", reg.pk)], by=user) == 1
        payout = Payout.objects.get(registration=reg)
        assert payout.status == Payout.Status.OWED_MANUALLY
        assert payout.owed_reason == Payout.OwedReason.NOT_CONNECTED
        assert (payout.paid_by_hand_at, payout.paid_by_hand_by, payout.amount_cents) == (NOW, user, 7000)
        assert mail.outbox == []

    def it_marks_an_existing_owed_row_and_records_its_due_date() -> None:
        reg = _registration(_instructor())
        payout = _payout(reg, Payout.Status.OWED_MANUALLY, owed_reason=Payout.OwedReason.NOT_THROUGH_STRIPE)
        Payout.objects.filter(pk=payout.pk).update(due_at=NOW - timedelta(days=30))
        mark_paid_by_hand([("class", reg.pk)], by=None)
        payout.refresh_from_db()
        assert payout.paid_by_hand_at == NOW
        assert payout.due_at == datetime(2026, 10, 17, 18, tzinfo=UTC)  # the share's own Earning.due_at

    def it_shows_mark_paid_with_a_confirm_on_an_owed_line(client: Client, admin: User) -> None:
        reg = _registration(_instructor())
        content = _table(client)
        assert "data-recon-mark-paid" in content
        assert f"open-confirm', 'mark-paid-class-{reg.pk}'" in content
        assert reverse("billing_reconciliation_mark_paid", args=["class", reg.pk]) in content

    def it_marks_it_from_the_view_with_a_toast_and_a_refresh(client: Client, admin: User) -> None:
        reg = _registration(_instructor())
        response = client.post(reverse("billing_reconciliation_mark_paid", args=["class", reg.pk]))
        assert response.status_code == 204
        assert _triggers(response)["showToast"] == {"message": "Marked paid by hand.", "type": "success"}
        assert "reconciliation-changed" in _triggers(response)
        assert Payout.objects.get(registration=reg).paid_by_hand_by == admin

    def it_refuses_a_share_no_longer_owed_with_an_error_toast(client: Client, admin: User) -> None:
        reg = _registration(_instructor(), class_at=NOW + timedelta(days=5))
        response = client.post(reverse("billing_reconciliation_mark_paid", args=["class", reg.pk]))
        assert _triggers(response)["showToast"]["type"] == "error"
        assert not Payout.objects.filter(registration=reg).exists()

    def it_404s_a_line_kind_with_no_producer_share(client: Client, admin: User) -> None:
        assert client.post(reverse("billing_reconciliation_mark_paid", args=["tab", 1])).status_code == 404

    def it_is_admin_only(client: Client) -> None:
        reg = _registration(_instructor())
        User.objects.create_user(username="member", password="pass")
        client.login(username="member", password="pass")
        response = client.post(reverse("billing_reconciliation_mark_paid", args=["class", reg.pk]))
        assert response.status_code != 204
        assert not Payout.objects.filter(registration=reg).exists()


def describe_marking_a_producer_row() -> None:
    """AC2: Mark all paid marks every share the row counts in Owed now for these dates, after a confirm."""

    @pytest.fixture
    def shares() -> dict[str, Any]:
        instructor = _instructor()
        return {
            "instructor": instructor,
            "owed": _registration(instructor),
            # Paid in September, its class Oct 5: it fell due in October, so October's Owed now counts it.
            "earlier": _registration(
                instructor,
                paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC),
                class_at=datetime(2026, 10, 5, 18, tzinfo=UTC),
            ),
            "upcoming": _registration(instructor, class_at=NOW + timedelta(days=5)),
        }

    def it_marks_the_owed_shares_paid_in_or_before_the_dates_and_nothing_else(shares: dict[str, Any]) -> None:
        count = mark_row_paid_by_hand(OCTOBER, ("instructor", shares["instructor"].pk), by=None)
        assert count == 2
        assert Payout.objects.filter(paid_by_hand_at=NOW).count() == 2
        assert not Payout.objects.filter(registration=shares["upcoming"]).exists()

    def it_names_the_person_and_the_total_in_the_confirm(shares: dict[str, Any], client: Client, admin: User) -> None:
        content = _table(client)
        assert "data-recon-mark-all" in content
        assert f"mark-row-paid-instructor-{shares['instructor'].pk}" in content
        summary = content[content.index("data-recon-mark-all-summary") :]
        assert "Paidby Handtest" in summary[:400]
        assert "$140.00" in summary[:400]

    def it_marks_them_from_the_view_for_the_posted_dates(shares: dict[str, Any], client: Client, admin: User) -> None:
        url = reverse("billing_reconciliation_mark_row_paid", args=["instructor", shares["instructor"].pk])
        response = client.post(url, OCTOBER_POST)
        assert response.status_code == 204
        assert _triggers(response)["showToast"] == {"message": "Marked 2 shares paid by hand.", "type": "success"}
        assert _row(build_reconciliation(window=OCTOBER), shares["instructor"]).owed_now_cents == 0

    def it_says_share_once_for_one(client: Client, admin: User) -> None:
        instructor = _instructor()
        _registration(instructor)
        url = reverse("billing_reconciliation_mark_row_paid", args=["instructor", instructor.pk])
        assert _triggers(client.post(url, OCTOBER_POST))["showToast"]["message"] == "Marked 1 share paid by hand."

    def it_refuses_a_row_with_nothing_owed(client: Client, admin: User) -> None:
        instructor = _instructor()
        _registration(instructor, class_at=NOW + timedelta(days=5))
        url = reverse("billing_reconciliation_mark_row_paid", args=["instructor", instructor.pk])
        response = client.post(url, OCTOBER_POST)
        assert _triggers(response)["showToast"]["type"] == "error"
        assert not Payout.objects.exists()

    def it_hides_mark_all_on_a_row_with_nothing_owed(client: Client, admin: User) -> None:
        _registration(_instructor(), class_at=NOW + timedelta(days=5))
        assert "data-recon-mark-all" not in _table(client)

    def it_404s_a_recipient_that_is_not_a_producer(client: Client, admin: User) -> None:
        for kind in ("guild", "nope"):
            url = reverse("billing_reconciliation_mark_row_paid", args=[kind, 1])
            assert client.post(url, OCTOBER_POST).status_code == 404


def describe_the_paid_by_hand_column() -> None:
    """AC3: a marked share leaves Owed now and counts in Paid by hand: tab, print view, CSV."""

    def it_moves_the_share_from_owed_now_to_paid_by_hand() -> None:
        instructor = _instructor()
        reg = _registration(instructor)
        _registration(instructor)
        mark_paid_by_hand([("class", reg.pk)], by=None)
        row = _row(build_reconciliation(window=OCTOBER), instructor)
        assert (row.owed_now_cents, row.paid_by_hand_cents) == (7000, 7000)

    def it_counts_a_share_paid_earlier_in_the_dates_it_fell_due_in() -> None:
        instructor = _instructor()
        reg = _registration(
            instructor, paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC), class_at=datetime(2026, 10, 5, 18, tzinfo=UTC)
        )
        mark_paid_by_hand([("class", reg.pk)], by=None)
        october = build_reconciliation(window=OCTOBER)
        row = _row(october, instructor)
        assert (row.total_cents, row.owed_now_cents, row.paid_by_hand_cents) == (0, 0, 7000)
        assert _line(october, reg).payout.state == LineState.PAID_BY_HAND
        september = _row(build_reconciliation(window=SEPTEMBER), instructor)
        assert (september.owed_now_cents, september.not_due_cents, september.paid_by_hand_cents) == (0, 0, 0)

    def it_shows_the_column_on_the_tab_and_the_print_page(client: Client, admin: User) -> None:
        reg = _registration(_instructor())
        mark_paid_by_hand([("class", reg.pk)], by=None)
        content = _table(client)
        assert ">Paid by hand</th>" in content
        assert "data-recon-paid-by-hand>$70.00</td>" in content
        printed = client.get(reverse("billing_admin_reconciliation_print"), OCTOBER_POST).content.decode()
        assert '<th class="r">Paid by hand</th>' in printed
        assert '<td class="r">$70.00</td>' in printed

    def it_adds_the_column_to_the_csv() -> None:
        instructor = _instructor()
        reg = _registration(instructor)
        mark_paid_by_hand([("class", reg.pk)], by=None)
        body = b"".join(stream_reconciliation_csv(build_reconciliation(window=OCTOBER)).streaming_content).decode()
        row = next(r for r in csv.DictReader(io.StringIO(body)) if r["Recipient"] == instructor.display_name)
        assert (row["Owed now"], row["Paid by hand"]) == ("0.00", "70.00")

    def it_leaves_the_snapshot_page_alone(client: Client, admin: User) -> None:
        _registration(
            _instructor(), paid_at=datetime(2026, 9, 2, 18, tzinfo=UTC), class_at=datetime(2026, 9, 5, 18, tzinfo=UTC)
        )
        snapshot = ReconciliationSnapshot.take(period_start=SEPTEMBER.start, period_end=SEPTEMBER.end, title="Sep")
        content = client.get(reverse("billing_reconciliation_snapshot_detail", args=[snapshot.pk])).content.decode()
        assert "data-recon-paid-by-hand" not in content
        assert "data-recon-mark-all" not in content


def describe_the_badge_and_the_ledger() -> None:
    """AC4: the line reads "Paid by hand <date>"; the ledger records who and when."""

    def it_badges_the_line_with_the_day_it_was_marked() -> None:
        reg = _registration(_instructor())
        user = User.objects.create_user(username="marker", password="x")
        mark_paid_by_hand([("class", reg.pk)], by=user)
        payout = _line(build_reconciliation(window=OCTOBER), reg).payout
        assert (payout.badge.label, payout.badge.tone) == ("Paid by hand Oct 20", "muted")
        assert payout.export_label == "Paid by hand 2026-10-20"
        ledger = Payout.objects.get(registration=reg)
        assert (ledger.paid_by_hand_by, ledger.paid_by_hand_at) == (user, NOW)

    def it_keeps_the_recorded_due_date_when_the_class_moves_after() -> None:
        instructor = _instructor()
        reg = _registration(instructor)
        mark_paid_by_hand([("class", reg.pk)], by=None)
        ClassSession.objects.filter(class_offering=reg.class_offering).update(
            starts_at=NOW + timedelta(days=30), ends_at=NOW + timedelta(days=30, hours=2)
        )
        assert Payout.objects.get(registration=reg).is_settled
        row = _row(build_reconciliation(window=OCTOBER), instructor)
        assert (row.not_due_cents, row.paid_by_hand_cents) == (0, 7000)


def describe_undo() -> None:
    """AC5: Undo on a marked line puts it back in Owed now."""

    def it_shows_undo_paid_on_a_marked_line(client: Client, admin: User) -> None:
        reg = _registration(_instructor())
        mark_paid_by_hand([("class", reg.pk)], by=None)
        content = _table(client)
        assert "data-recon-undo-paid" in content
        assert reverse("billing_reconciliation_undo_paid", args=["class", reg.pk]) in content
        assert "data-recon-mark-paid" not in content

    def it_puts_the_share_back_in_owed_now(client: Client, admin: User) -> None:
        instructor = _instructor()
        reg = _registration(instructor)
        mark_paid_by_hand([("class", reg.pk)], by=admin)
        response = client.post(reverse("billing_reconciliation_undo_paid", args=["class", reg.pk]))
        assert response.status_code == 204
        assert _triggers(response)["showToast"] == {"message": "Back in Owed now.", "type": "success"}
        payout = Payout.objects.get(registration=reg)
        assert (payout.paid_by_hand_at, payout.paid_by_hand_by) == (None, None)
        row = _row(build_reconciliation(window=OCTOBER), instructor)
        assert (row.owed_now_cents, row.paid_by_hand_cents) == (7000, 0)

    def it_refuses_a_share_not_marked_paid(client: Client, admin: User) -> None:
        reg = _registration(_instructor())
        _payout(reg, Payout.Status.OWED_MANUALLY)
        response = client.post(reverse("billing_reconciliation_undo_paid", args=["class", reg.pk]))
        assert _triggers(response)["showToast"]["type"] == "error"

    def it_404s_a_line_with_no_recorded_share_or_no_producer(client: Client, admin: User) -> None:
        reg = _registration(_instructor())
        assert client.post(reverse("billing_reconciliation_undo_paid", args=["class", reg.pk])).status_code == 404
        assert client.post(reverse("billing_reconciliation_undo_paid", args=["tab", 1])).status_code == 404

    def it_raises_from_the_service_when_nothing_is_marked() -> None:
        reg = _registration(_instructor())
        _payout(reg, Payout.Status.OWED_MANUALLY)
        with pytest.raises(ValueError, match="not marked paid by hand"):
            undo_paid_by_hand("class", reg.pk)


def describe_only_a_share_in_owed_now() -> None:
    """AC6: not Not yet due, not Stripe retrying, not sent through Stripe; the send job never sends it."""

    @pytest.mark.parametrize(
        "status", [Payout.Status.FAILED, Payout.Status.PENDING, Payout.Status.SENT, Payout.Status.TAKEN_BACK]
    )
    def it_refuses_a_share_stripe_handles(status: str) -> None:
        reg = _registration(_instructor())
        _payout(reg, status)
        with pytest.raises(ShareNotOwedError):
            mark_paid_by_hand([("class", reg.pk)], by=None)
        assert Payout.objects.get(registration=reg).paid_by_hand_at is None

    def it_refuses_a_share_not_yet_due() -> None:
        reg = _registration(_instructor(), class_at=NOW + timedelta(days=5))
        with pytest.raises(ShareNotOwedError):
            mark_paid_by_hand([("class", reg.pk)], by=None)

    def it_refuses_a_share_already_paid() -> None:
        reg = _registration(_instructor())
        mark_paid_by_hand([("class", reg.pk)], by=None)
        with pytest.raises(ShareNotOwedError):
            mark_paid_by_hand([("class", reg.pk)], by=None)

    def it_marks_nothing_when_one_share_of_a_batch_is_refused() -> None:
        instructor = _instructor()
        owed = _registration(instructor)
        upcoming = _registration(instructor, class_at=NOW + timedelta(days=5))
        with pytest.raises(ShareNotOwedError):
            mark_paid_by_hand([("class", owed.pk), ("class", upcoming.pk)], by=None)
        assert not Payout.objects.exists()

    def it_hides_mark_paid_on_lines_not_owed(client: Client, admin: User) -> None:
        _registration(_instructor(), class_at=NOW + timedelta(days=5))
        assert "data-recon-mark-paid" not in _table(client)

    def it_refuses_on_the_model_a_row_not_owed_by_hand() -> None:
        reg = _registration(_instructor())
        payout = _payout(reg, Payout.Status.PENDING)
        with pytest.raises(ValueError, match="not an unpaid share owed by hand"):
            payout.mark_paid_by_hand(by=None, at=NOW, due_at=NOW)

    def it_never_sends_or_re_owes_a_share_marked_paid() -> None:
        instructor = _instructor()
        reg = _registration(instructor)
        mark_paid_by_hand([("class", reg.pk)], by=None)
        # Payouts go on and the payee connects before the payment: unrecorded, this share would now go through Stripe.
        bs = BillingSettings.load()
        bs.connect_enabled = True
        bs.save()
        BillingSettings.objects.filter(pk=1).update(payouts_on_since=NOW - timedelta(days=60))
        PayoutAccount.objects.create(
            member=instructor,
            stripe_account_id="acct_1249",
            livemode=False,
            status=PayoutAccount.Status.ACTIVE,
            active_since=NOW - timedelta(days=60),
        )
        later = NOW + timedelta(days=30)
        assert not Payout.objects.to_send(later).exists()
        assert not Payout.objects.stale(later).exists()
        with (
            patch("billing.stripe_utils.create_transfer") as transfer,
            patch("billing.stripe_utils.find_payout_transfer") as lookup,
        ):
            run_payouts(NOW)
            run_payouts(later)
        transfer.assert_not_called()
        lookup.assert_not_called()
        payout = Payout.objects.get(registration=reg)
        assert (payout.status, payout.paid_by_hand_at) == (Payout.Status.OWED_MANUALLY, NOW)
        assert _line(build_reconciliation(window=OCTOBER), reg).payout.state == LineState.PAID_BY_HAND

    def it_stops_flagging_a_given_up_share_once_paid() -> None:
        reg = _registration(_instructor())
        _payout(reg, Payout.Status.OWED_MANUALLY, owed_reason=Payout.OwedReason.TRANSFER_FAILED, owed_since=NOW)
        assert Payout.objects.needs_attention().count() == 1
        mark_paid_by_hand([("class", reg.pk)], by=None)
        assert not Payout.objects.needs_attention().exists()


def describe_a_share_a_past_snapshot_counted() -> None:
    """AC7: a share in a September snapshot's Owed now is marked from the live Reconciliation for those dates."""

    def it_is_marked_from_the_live_dates_and_the_snapshot_stays_as_taken(client: Client, admin: User) -> None:
        instructor = _instructor()
        reg = _registration(
            instructor, paid_at=datetime(2026, 9, 2, 18, tzinfo=UTC), class_at=datetime(2026, 9, 5, 18, tzinfo=UTC)
        )
        snapshot = ReconciliationSnapshot.take(period_start=SEPTEMBER.start, period_end=SEPTEMBER.end, title="Sep")
        frozen = snapshot.results
        assert Payout.objects.get(registration=reg).counted_owed_in == snapshot
        url = reverse("billing_reconciliation_mark_row_paid", args=["instructor", instructor.pk])
        assert client.post(url, {"start": "2026-09-01", "end": "2026-09-30"}).status_code == 204
        row = _row(build_reconciliation(window=SEPTEMBER), instructor)
        assert (row.owed_now_cents, row.paid_by_hand_cents) == (0, 7000)
        snapshot.refresh_from_db()
        assert snapshot.results == frozen

    def it_is_not_counted_owed_by_a_later_snapshot_once_paid() -> None:
        instructor = _instructor()
        reg = _registration(
            instructor, paid_at=datetime(2026, 10, 2, 18, tzinfo=UTC), class_at=datetime(2026, 10, 5, 18, tzinfo=UTC)
        )
        mark_paid_by_hand([("class", reg.pk)], by=None)
        snapshot = ReconciliationSnapshot.take(
            period_start=date(2026, 10, 1), period_end=date(2026, 10, 19), title="Oct"
        )
        assert Payout.objects.get(registration=reg).counted_owed_in is None
        rows = next(group for group in snapshot.results["groups"] if group["kind"] == "instructor")["rows"]
        assert rows[0]["owed_now_cents"] == 0
