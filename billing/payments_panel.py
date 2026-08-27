"""Read-time aggregation for the Payments tab — no model, no fourth table.

The panel is a merge over the existing money tables (``TabCharge`` rows and paid
``Registration`` rows, orientation bookings via a documented seam once the
paid-orientations spec lands). Each source keeps its own lifecycle; this module
only derives one row shape (:class:`PaymentRow`) with a source-neutral identity
and a per-source status badge, then sorts and caps the merged list.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Iterator

from django.http import StreamingHttpResponse
from django.utils import timezone

if TYPE_CHECKING:
    from billing.models import PaymentRefund

MAX_ROWS = 500

SOURCE_LABELS = {"tab": "Tab", "class": "Class", "orientation": "Orientation"}

STATUS_LABELS = {
    "paid": "Paid",
    "partial": "Partially refunded",
    "refunded": "Refunded",
    "refund_failed": "Refund failed",
    "charge_failed": "Charge failed",
    "refund_pending": "Refund pending",
}

# The Failed filter chip matches BOTH red-row meanings; the badge text disambiguates.
_STATUS_FILTERS = {
    "paid": {"paid"},
    "partial": {"partial"},
    "refunded": {"refunded"},
    "failed": {"refund_failed", "charge_failed"},
}

CSV_HEADERS = [
    "Date",
    "Source",
    "Payer",
    "Item",
    "Amount",
    "Status",
    "Refund Status",
    "Refund Amount",
    "Refund Attempt",
    "Refund Source",
    "Refund Settled",
]


@dataclass(frozen=True)
class PaymentRow:
    """One ledger row with a source-neutral identity (per the cross-spec contract)."""

    source_kind: str  # "tab" | "class" | "orientation"
    source_pk: int
    payer_name: str
    payer_url: str | None  # set only for fog-admin viewers (§5.5 linking rule)
    item: str
    amount_cents: int
    status: str  # key into STATUS_LABELS
    date: datetime
    refund_rows: tuple[PaymentRefund, ...] = ()
    can_refund: bool = False
    tab_pk: int | None = None  # tab rows keep the existing tab-detail modal opener
    stripe_url: str = ""  # muted "Stripe" payment link on tab rows
    pending_age: str = ""  # e.g. "2 h" when status == "refund_pending"

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS[self.source_kind]

    @property
    def succeeded_refunds(self) -> list[PaymentRefund]:
        """Succeeded refunds, for the muted amount lines beneath the row's amount."""
        from billing.models import PaymentRefund as PaymentRefundModel

        return [r for r in self.refund_rows if r.status == PaymentRefundModel.Status.SUCCEEDED]


@dataclass(frozen=True)
class PaymentsLedger:
    """The built panel: capped rows plus the header stats for the active filters."""

    rows: tuple[PaymentRow, ...]
    total_count: int
    collected_cents: int
    refunded_cents: int

    @property
    def capped(self) -> bool:
        return self.total_count > len(self.rows)

    @property
    def net_cents(self) -> int:
        return self.collected_cents - self.refunded_cents


@dataclass(frozen=True)
class PanelWindow:
    """The resolved date window — always in the project timezone (Portland)."""

    start: date
    end: date
    start_dt: datetime = field(init=False)
    end_dt: datetime = field(init=False)

    def __post_init__(self) -> None:
        tz = timezone.get_current_timezone()
        object.__setattr__(self, "start_dt", datetime.combine(self.start, time.min, tzinfo=tz))
        object.__setattr__(self, "end_dt", datetime.combine(self.end + timedelta(days=1), time.min, tzinfo=tz))


def parse_window(start_raw: str, end_raw: str) -> PanelWindow:
    """Resolve the GET date filters — defaults to the current month (like the reports page)."""
    today = timezone.localdate()
    try:
        start = date.fromisoformat(start_raw)
    except ValueError:
        start = today.replace(day=1)
    try:
        end = date.fromisoformat(end_raw)
    except ValueError:
        end = today
    return PanelWindow(start=start, end=end)


def _age_label(since: datetime) -> str:
    """Short age like "35 m", "2 h", or "3 d" — makes a stuck Pending refund visible."""
    delta = timezone.now() - since
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 0)} m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h"
    return f"{hours // 24} d"


def _tab_rows(window: PanelWindow) -> list[PaymentRow]:
    """Tab charge rows. Refund action deferred by locked decision — ``can_refund`` is always False."""
    from django.db.models.functions import Coalesce

    from billing.models import TabCharge

    charges = (
        TabCharge.objects.exclude(status__in=[TabCharge.Status.PENDING, TabCharge.Status.PENDING_CHECKOUT])
        .annotate(effective_charged_at=Coalesce("charged_at", "created_at"))
        .filter(effective_charged_at__gte=window.start_dt, effective_charged_at__lt=window.end_dt)
        .select_related("tab__member")
        .order_by("-created_at")
    )
    rows: list[PaymentRow] = []
    for charge in charges:
        charged = charge.effective_charged_at
        status = "paid" if charge.status == TabCharge.Status.SUCCEEDED else "charge_failed"
        stripe_url = charge.stripe_receipt_url
        if not stripe_url and charge.stripe_payment_intent_id:
            stripe_url = f"https://dashboard.stripe.com/payments/{charge.stripe_payment_intent_id}"
        rows.append(
            PaymentRow(
                source_kind="tab",
                source_pk=charge.pk,
                payer_name=charge.tab.member.display_name,
                payer_url=None,
                item="Tab charge",
                amount_cents=int(round(charge.amount * 100)),
                status=status,
                date=charged,
                tab_pk=charge.tab_id,
                stripe_url=stripe_url,
            )
        )
    return rows


def _class_rows(window: PanelWindow, *, viewer_is_admin: bool) -> list[PaymentRow]:
    """Paid class registration rows, with refund state derived from the ledger.

    Includes rows with NO ``stripe_payment_id`` — a staff Mark as Paid (cash,
    comped) is money collected and belongs in the ledger. Those rows carry no
    refund action: there is nothing on Stripe to refund.
    """
    from django.urls import reverse

    from billing.models import PaymentRefund
    from classes.models import Registration

    registrations = (
        Registration.objects.filter(amount_paid_cents__gt=0)
        .filter(confirmed_at__gte=window.start_dt, confirmed_at__lt=window.end_dt)
        .select_related("class_offering", "member")
        .prefetch_related("refunds")
    )
    rows: list[PaymentRow] = []
    for registration in registrations:
        refunds = tuple(registration.refunds.all())
        state = registration.refund_state
        pending = next((r for r in refunds if r.status == PaymentRefund.Status.PENDING), None)
        pending_age = ""
        if state == "failed":
            status = "refund_failed"
        elif pending is not None:
            status = "refund_pending"
            pending_age = _age_label(pending.created_at)
        elif state == "full":
            status = "refunded"
        elif state == "partial":
            status = "partial"
        else:
            status = "paid"
        guest_name = f"{registration.first_name} {registration.last_name}".strip()
        payer_name = (
            registration.member.display_name if registration.member is not None else (guest_name or registration.email)
        )
        payer_url = reverse("classes:admin_registration_detail", args=[registration.pk]) if viewer_is_admin else None
        rows.append(
            PaymentRow(
                source_kind="class",
                source_pk=registration.pk,
                payer_name=payer_name,
                payer_url=payer_url,
                item=registration.class_offering.title,
                amount_cents=registration.amount_paid_cents,
                status=status,
                date=registration.confirmed_at,
                refund_rows=refunds,
                can_refund=bool(registration.stripe_payment_id) and registration.refundable_cents > 0,
                pending_age=pending_age,
            )
        )
    return rows


def _orientation_rows(window: PanelWindow) -> list[PaymentRow]:
    """The documented seam the paid-orientations companion spec fills — empty until then."""
    return []


def build_payments_ledger(
    *,
    window: PanelWindow,
    source: str = "all",
    status: str = "all",
    viewer_is_admin: bool = False,
) -> PaymentsLedger:
    """Merge, filter, sort (date desc), and cap the ledger; compute the header stats.

    Unknown ``source``/``status`` values behave like ``"all"`` — filter chips are
    URL parameters, and a stale link should show everything rather than nothing.
    """
    rows: list[PaymentRow] = []
    if source in ("all", "tab"):
        rows.extend(_tab_rows(window))
    if source in ("all", "class"):
        rows.extend(_class_rows(window, viewer_is_admin=viewer_is_admin))
    if source in ("all", "orientation"):
        rows.extend(_orientation_rows(window))

    wanted = _STATUS_FILTERS.get(status)
    if wanted is not None:
        rows = [row for row in rows if row.status in wanted]

    rows.sort(key=lambda row: row.date, reverse=True)
    collected = sum(row.amount_cents for row in rows if row.status != "charge_failed")
    refunded = sum(r.amount_cents for row in rows for r in row.succeeded_refunds)
    return PaymentsLedger(
        rows=tuple(rows[:MAX_ROWS]),
        total_count=len(rows),
        collected_cents=collected,
        refunded_cents=refunded,
    )


class _Echo:
    """File-like object whose write() returns the value — the streaming CSV trick."""

    def write(self, value: str) -> str:
        return value


def _csv_lines(ledger: PaymentsLedger, writer: Any) -> Iterator[str]:
    yield writer.writerow(CSV_HEADERS)
    for row in ledger.rows:
        base = [
            timezone.localtime(row.date).date().isoformat(),
            row.source_label,
            row.payer_name,
            row.item,
            f"{row.amount_cents / 100:.2f}",
            row.status_label,
        ]
        if not row.refund_rows:
            yield writer.writerow([*base, "", "", "", "", ""])
            continue
        for refund in row.refund_rows:
            yield writer.writerow(
                [
                    *base,
                    refund.get_status_display(),
                    f"{refund.amount_cents / 100:.2f}",
                    str(refund.attempt),
                    refund.get_source_display(),
                    timezone.localtime(refund.settled_at).date().isoformat() if refund.settled_at else "",
                ]
            )


def stream_payments_csv(ledger: PaymentsLedger) -> StreamingHttpResponse:
    """Streaming CSV download mirroring the reports pattern — table columns plus refund detail."""
    writer = csv.writer(_Echo())
    response = StreamingHttpResponse(_csv_lines(ledger, writer), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="plfog-payments-{stamp}.csv"'
    return response
