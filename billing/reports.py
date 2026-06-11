"""Reports queries and CSV export for billing.

Built on top of ``TabEntrySplit`` — the frozen per-recipient snapshot rows
created at entry time. Each row in the report corresponds to one
``TabEntrySplit`` (not one ``TabEntry``), so a single charge that splits
between Admin and a guild produces two rows.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Iterator

from django.db.models import Q, QuerySet
from django.http import StreamingHttpResponse
from django.utils import timezone

from billing.models import TabCharge, TabEntrySplit

if TYPE_CHECKING:
    from django.http.request import QueryDict  # noqa: F401

_ZERO = Decimal("0.00")


@dataclass(frozen=True)
class ReportRow:
    created_at: datetime
    member_name: str
    description: str
    recipient_type: str  # "admin" | "guild"
    recipient_label: str  # "Admin" or guild name
    guild_id: int | None
    amount: Decimal  # this recipient's share
    percent: Decimal
    entry_amount: Decimal  # the parent entry's full amount
    charge_status: str
    charge_type: str  # "product" | "custom"


@dataclass(frozen=True)
class PayoutRow:
    recipient_type: str  # "admin" | "guild"
    recipient_label: str
    guild_id: int | None
    entry_count: int  # distinct entries that paid this recipient
    amount: Decimal


def _apply_filters(
    qs: QuerySet[TabEntrySplit],
    *,
    start_date: date_cls | None,
    end_date: date_cls | None,
    guild_ids: list[int] | None,
    charge_types: list[str] | None,
    statuses: list[str] | None,
) -> QuerySet[TabEntrySplit]:
    if start_date:
        qs = qs.filter(entry__created_at__date__gte=start_date)
    if end_date:
        qs = qs.filter(entry__created_at__date__lte=end_date)

    if guild_ids:
        # Only show guild splits that match the requested guilds.
        # Admin splits for matching entries are still included.
        qs = qs.filter(
            Q(recipient_type=TabEntrySplit.RecipientType.GUILD, guild_id__in=guild_ids)
            | Q(recipient_type=TabEntrySplit.RecipientType.ADMIN, entry__splits__guild_id__in=guild_ids)
        ).distinct()

    if charge_types:
        if "product" in charge_types and "custom" not in charge_types:
            qs = qs.filter(entry__product__isnull=False)
        elif "custom" in charge_types and "product" not in charge_types:
            qs = qs.filter(entry__product__isnull=True)

    if statuses:
        status_q = Q()
        if "pending" in statuses:
            status_q |= Q(entry__tab_charge__isnull=True)
        other = [s for s in statuses if s != "pending"]
        if other:
            status_q |= Q(entry__tab_charge__status__in=other)
        qs = qs.filter(status_q)

    return qs


def build_report(
    *,
    start_date: date_cls | None = None,
    end_date: date_cls | None = None,
    guild_ids: list[int] | None = None,
    charge_types: list[str] | None = None,
    statuses: list[str] | None = None,
) -> tuple[list[ReportRow], list[PayoutRow], Decimal]:
    """Return (rows, payout_summary, admin_total) sourced from TabEntrySplit."""
    qs: QuerySet[TabEntrySplit] = (
        TabEntrySplit.objects.all()
        .select_related("entry__tab__member", "entry__tab_charge", "entry__product", "guild")
        .filter(entry__voided_at__isnull=True)
        .order_by("entry__created_at", "entry_id", "id")
    )
    qs = _apply_filters(
        qs,
        start_date=start_date,
        end_date=end_date,
        guild_ids=guild_ids,
        charge_types=charge_types,
        statuses=statuses,
    )

    rows: list[ReportRow] = []
    payouts: dict[tuple[str, int | None], PayoutRow] = {}
    admin_total = _ZERO

    for split in qs.iterator(chunk_size=500):
        entry = split.entry
        charge_status = (
            entry.tab_charge.status  # type: ignore[union-attr]  # tab_charge_id guard ensures non-None
            if entry.tab_charge_id
            else "pending"
        )
        charge_type = "product" if entry.product_id else "custom"
        recipient_label = (
            "Admin"
            if split.recipient_type == TabEntrySplit.RecipientType.ADMIN
            else (
                split.guild.name  # type: ignore[union-attr]  # guild_id guard ensures non-None
                if split.guild_id
                else "?"
            )
        )

        rows.append(
            ReportRow(
                created_at=entry.created_at,
                member_name=entry.tab.member.display_name,
                description=entry.description,
                recipient_type=split.recipient_type,
                recipient_label=recipient_label,
                guild_id=split.guild_id,
                amount=split.amount,
                percent=split.percent,
                entry_amount=entry.amount,
                charge_status=charge_status,
                charge_type=charge_type,
            )
        )

        if split.recipient_type == TabEntrySplit.RecipientType.ADMIN:
            admin_total += split.amount

        key = (split.recipient_type, split.guild_id)
        existing = payouts.get(key)
        if existing is None:
            payouts[key] = PayoutRow(
                recipient_type=split.recipient_type,
                recipient_label=recipient_label,
                guild_id=split.guild_id,
                entry_count=1,
                amount=split.amount,
            )
        else:
            payouts[key] = PayoutRow(
                recipient_type=split.recipient_type,
                recipient_label=recipient_label,
                guild_id=split.guild_id,
                entry_count=existing.entry_count + 1,
                amount=existing.amount + split.amount,
            )

    payout_list = sorted(
        payouts.values(),
        key=lambda p: (0 if p.recipient_type == "admin" else 1, p.recipient_label),
    )
    return rows, payout_list, admin_total


class _Echo:
    """File-like object whose ``write()`` returns the payload (for StreamingHttpResponse)."""

    def write(self, value: str) -> str:
        return value


CSV_HEADERS = [
    "date",
    "member",
    "description",
    "recipient_type",
    "recipient",
    "charge_type",
    "charge_status",
    "entry_amount",
    "percent",
    "amount",
]


def stream_report_csv(
    *,
    start_date: date_cls | None = None,
    end_date: date_cls | None = None,
    guild_ids: list[int] | None = None,
    charge_types: list[str] | None = None,
    statuses: list[str] | None = None,
) -> StreamingHttpResponse:
    """Build a streaming CSV response for a filtered report."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(CSV_HEADERS)
        rows, _payouts, _admin_total = build_report(
            start_date=start_date,
            end_date=end_date,
            guild_ids=guild_ids,
            charge_types=charge_types,
            statuses=statuses,
        )
        for r in rows:
            yield writer.writerow(
                [
                    r.created_at.date().isoformat(),
                    r.member_name,
                    r.description,
                    r.recipient_type,
                    r.recipient_label,
                    r.charge_type,
                    r.charge_status,
                    f"{r.entry_amount:.2f}",
                    f"{r.percent:.2f}",
                    f"{r.amount:.2f}",
                ]
            )

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="plfog-billing-report-{stamp}.csv"'
    return response


class ReportFilterForm:
    """A lightweight not-a-Django-form that parses ``request.GET`` into filter kwargs.

    We use this instead of a real forms.Form because the Reports page submits
    filters via GET query params — each field is optional, errors should fall
    back to "no filter", and the same field set needs to survive round-trips
    on the CSV download URL. A plain dataclass parse is cleaner than wiring up
    a real form for that.
    """

    def __init__(self, data: "QueryDict | dict[str, Any] | None") -> None:
        self.data: Any = data if data is not None else {}

    def filter_kwargs(self) -> dict[str, Any]:
        return {
            "start_date": self._parse_date("start_date"),
            "end_date": self._parse_date("end_date"),
            "guild_ids": self._parse_int_list("guilds") or None,
            "charge_types": self._parse_str_list("charge_type") or None,
            "statuses": self._parse_str_list("status") or None,
        }

    def _parse_date(self, key: str) -> date_cls | None:
        raw = (self.data.get(key) or "").strip()
        if not raw:
            return None
        try:
            return date_cls.fromisoformat(raw)
        except ValueError:
            return None

    def _parse_int_list(self, key: str) -> list[int]:
        raw = self.data.getlist(key) if hasattr(self.data, "getlist") else self.data.get(key, [])
        if isinstance(raw, str):
            raw = [raw]
        parsed: list[int] = []
        for value in raw:
            try:
                parsed.append(int(value))
            except (TypeError, ValueError):
                continue
        return parsed

    def _parse_str_list(self, key: str) -> list[str]:
        raw = self.data.getlist(key) if hasattr(self.data, "getlist") else self.data.get(key, [])
        if isinstance(raw, str):
            raw = [raw] if raw else []
        return [s for s in raw if s]


# Convenience: expose valid status / charge-type choices for the filter UI
STATUS_CHOICES = [
    ("pending", "Pending (not yet charged)"),
    (TabCharge.Status.SUCCEEDED, "Succeeded"),
    (TabCharge.Status.FAILED, "Failed"),
    (TabCharge.Status.PROCESSING, "Processing"),
]

CHARGE_TYPE_CHOICES = [
    ("product", "Product"),
    ("custom", "Custom (Enter Your Own Price)"),
]
