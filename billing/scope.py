"""Whose money a viewer of Payments and Reports may see (#1294).

Billing Administrators and admins see every payment, as before. Guild leads and staff see their
guilds' money: class registrations in the guild's classes, orientation bookings, equipment
reservations, the late fees on those bookings and reservations, cubby rent and My Tab purchases of
the guild's products. Instructors see the registrations of every class they teach, whether or not
they are its payee. Someone who is both sees both.

:class:`PaymentsScope` is the one place that rule lives. The Payments ledger
(:func:`billing.payments_panel.build_payments_ledger`) narrows each source's queryset through
:meth:`PaymentsScope.restrict`, and the Reports tab narrows a built reconciliation through
:meth:`PaymentsScope.scope_reconciliation`. Nothing is hidden in a template: a row outside the
scope is never read into the page, the table refresh or the CSV.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, TypeVar

from django.db.models import Model, Q, QuerySet

if TYPE_CHECKING:
    from django.http import HttpRequest

    from billing.payments_panel import PanelWindow
    from billing.reconciliation import ReconciliationResult, RecipientKey, TransactionLine

_M = TypeVar("_M", bound=Model)

#: The sources :meth:`PaymentsScope.restrict` knows, as ``TransactionLine.source_kind`` and
#: ``PaymentRow.source_kind`` spell them. ``tab`` is a :class:`~billing.models.TabEntry`.
SOURCE_KINDS = frozenset({"tab", "class", "orientation", "reservation", "late_fee", "cubby"})


@dataclass(frozen=True)
class PaymentsScope:
    """What one viewer may see on Payments and Reports.

    Attributes:
        everything: True for an admin or a Billing Administrator; the other fields are then unused.
        guild_ids: the guilds the viewer leads or staffs.
        instructor_id: the viewer's member pk when they teach any class, else None.
    """

    everything: bool
    guild_ids: frozenset[int] = frozenset()
    instructor_id: int | None = None

    @classmethod
    def for_request(cls, request: HttpRequest, *, reports: bool = False) -> PaymentsScope:
        """The scope of the signed in viewer.

        Payments shows everything to admins and Billing Administrators (``has_billing_admin_access``).
        Reports (the Reconciliation tab, ``reports=True``) shows everything to admins only, as it
        always has; a Billing Administrator who leads, staffs or teaches sees their own share there
        like anyone else. Admin standing is the actual role, like every billing gate, so a View as
        preview neither grants nor narrows it. The guilds are
        :func:`~membership.permissions.manageable_guilds`; teaching is an instructors row on any
        class, any status (``ClassOffering.objects.for_instructor``).
        """
        from classes.models import ClassOffering
        from hub.view_as import ROLE_ADMIN, has_billing_admin_access
        from membership.permissions import manageable_guilds

        view_as = getattr(request, "view_as", None)
        sees_everything = (
            view_as is not None and view_as.has_actual(ROLE_ADMIN) if reports else has_billing_admin_access(request)
        )
        if sees_everything:
            return cls(everything=True)
        guild_ids = frozenset(manageable_guilds(request).values_list("pk", flat=True))
        member = getattr(request.user, "member", None)
        instructor_id = (
            member.pk if member is not None and ClassOffering.objects.for_instructor(member).exists() else None
        )
        return cls(everything=False, guild_ids=guild_ids, instructor_id=instructor_id)

    @property
    def is_scoped(self) -> bool:
        """True when the viewer sees only part of the money."""
        return not self.everything

    @property
    def is_empty(self) -> bool:
        """True when the viewer may see no money at all: no guild, no class, no billing role."""
        return self.is_scoped and not self.guild_ids and self.instructor_id is None

    @property
    def recipient_keys(self) -> frozenset[RecipientKey]:
        """The reconciliation recipients whose share is the viewer's: their guilds and themselves as instructor."""
        keys: set[RecipientKey] = {("guild", guild_id) for guild_id in self.guild_ids}
        if self.instructor_id is not None:
            keys.add(("instructor", self.instructor_id))
        return frozenset(keys)

    def _filter(self, kind: str) -> Q | None:
        """The filter for one source's model, or None when this scope matches none of its rows."""
        if kind == "class":
            return self._class_filter()
        if not self.guild_ids:
            return None
        return _guild_filter(kind, self.guild_ids)

    def _class_filter(self) -> Q | None:
        """Registrations in the viewer's guilds' classes, or in a class they teach, or both."""
        from classes.models import ClassOffering

        legs: list[Q] = []
        if self.guild_ids:
            legs.append(Q(class_offering__category__guild_id__in=self.guild_ids))
        if self.instructor_id is not None:
            # A subquery, not a join on the instructors rows: a class taught by two people
            # would list each registration twice through the guild leg's OR.
            taught = ClassOffering.objects.filter(instructors__pk=self.instructor_id).values("pk")
            legs.append(Q(class_offering_id__in=taught))
        if not legs:
            return None
        return legs[0] if len(legs) == 1 else legs[0] | legs[1]

    def restrict(self, kind: str, queryset: QuerySet[_M]) -> QuerySet[_M]:
        """``queryset`` of one source's rows narrowed to this scope.

        Args:
            kind: one of :data:`SOURCE_KINDS`; the queryset is of that source's model
                (``Registration``, ``OrientationBooking``, ``EquipmentReservation``,
                ``LateCancellationFee``, ``CubbyPayment`` or ``TabEntry``).
            queryset: the rows to narrow.

        Returns:
            ``queryset`` unchanged for an unscoped viewer, else only the rows in scope.

        Raises:
            ValueError: ``kind`` is not a known source.
        """
        if kind not in SOURCE_KINDS:
            raise ValueError(f"Unknown payment source {kind!r}.")
        if self.everything:
            return queryset
        q = self._filter(kind)
        return queryset.none() if q is None else queryset.filter(q)

    def scope_reconciliation(self, result: ReconciliationResult) -> ReconciliationResult:
        """``result`` as this viewer may see it: their payments, their recipient rows, their share.

        Lines are kept by :meth:`restrict`, so an instructor sees every registration of a class they
        teach even when a co-instructor is its payee (their share of it is then 0). A tab charge
        bundles every product a member bought, so its line is replaced by one line per purchase of
        the viewer's guilds' products. Each kept line carries ``viewer_share_cents``, and the
        recipient groups keep only the viewer's own rows, so the totals are theirs.
        """
        from billing.reconciliation import RecipientKind

        if self.everything:
            return result
        keys = self.recipient_keys
        lines = self._scoped_lines(result.lines, keys)
        if self.guild_ids:
            lines.extend(self._tab_entry_lines(result.window, result.lines, keys))
            lines.sort(key=lambda line: line.date, reverse=True)
        due_earlier = self._scoped_lines(result.due_earlier_lines, keys)
        groups = {
            kind: [alloc for alloc in rows if (kind.value, alloc.recipient_id) in keys]
            for kind, rows in result.groups.items()
        }
        groups[RecipientKind.PL] = []
        counted = [line for line in lines if not line.omitted]
        return replace(
            result,
            groups=groups,
            lines=lines,
            due_earlier_lines=due_earlier,
            grand_total_cents=sum(line.net_cents for line in counted),
            unassigned_note_count=sum(1 for line in counted if line.unassigned),
            omitted_count=len(lines) - len(counted),
        )

    def _scoped_lines(self, lines: Iterable[TransactionLine], keys: frozenset[RecipientKey]) -> list[TransactionLine]:
        """The non tab ``lines`` in scope, each with the viewer's share; one query per source kind."""
        from classes.models import Registration
        from membership.models import CubbyPayment, EquipmentReservation, OrientationBooking

        models: dict[str, Any] = {
            "class": Registration,
            "orientation": OrientationBooking,
            "reservation": EquipmentReservation,
            "cubby": CubbyPayment,
        }
        listed = [line for line in lines if line.source_kind in models]
        wanted: dict[str, set[int]] = {}
        for kind, model in models.items():
            pks = [line.source_pk for line in listed if line.source_kind == kind]
            if pks:
                kept = self.restrict(kind, model.objects.filter(pk__in=pks))
                wanted[kind] = set(kept.values_list("pk", flat=True))
        return [
            replace(line, viewer_share_cents=_share_of(line.shares, _line_keys(line, keys), omitted=line.omitted))
            for line in listed
            if line.source_pk in wanted.get(line.source_kind, set())
        ]

    def _tab_entry_lines(
        self, window: PanelWindow, charge_lines: Iterable[TransactionLine], keys: frozenset[RecipientKey]
    ) -> list[TransactionLine]:
        """One line per My Tab purchase of the viewer's guilds' products on the window's succeeded charges.

        The same charges the reconciliation's tab lines cover (an omitted charge's purchases are
        omitted too), read as purchases so a member's other products never show. Gross is what
        the purchase cost; the share is its frozen ``TabEntrySplit`` for the viewer's guilds.
        """
        from billing.models import TabEntry, TabEntrySplit
        from billing.reconciliation import TransactionLine

        charges = {line.source_pk: line for line in charge_lines if line.source_kind == "tab"}
        if not charges:
            return []
        entries = self.restrict(
            "tab",
            TabEntry.objects.filter(tab_charge_id__in=list(charges), voided_at__isnull=True),
        ).prefetch_related("splits")
        lines: list[TransactionLine] = []
        for entry in entries:
            charge_line = charges[entry.tab_charge_id]  # type: ignore[index]  # filtered on tab_charge_id above
            shares: dict[RecipientKey, int] = {}
            for split in entry.splits.all():
                if split.recipient_type == TabEntrySplit.RecipientType.GUILD:
                    key: RecipientKey = ("guild", split.guild_id)
                    shares[key] = shares.get(key, 0) + _to_cents(split.amount)
            gross = _to_cents(entry.amount)
            lines.append(
                TransactionLine(
                    source_kind="tab",
                    source_pk=entry.pk,
                    date=charge_line.date,
                    payer_name=charge_line.payer_name,
                    item=entry.description,
                    gross_cents=gross,
                    refunded_cents=0,
                    net_cents=gross,
                    shares=shares,
                    labels={},
                    omitted=charge_line.omitted,
                    viewer_share_cents=_share_of(shares, keys, omitted=charge_line.omitted),
                )
            )
        return lines


def _guild_filter(kind: str, guilds: frozenset[int]) -> Q:
    """The filter for one non class source's rows that belong to ``guilds``.

    An orientation booking is its ``guild``'s, the one the reconciliation credits; a reservation
    and its late fee are the item's guild's; a cubby payment is its unit's guild's; a My Tab
    purchase is a guild's when one of its frozen splits names that guild.
    """
    from billing.models import TabEntrySplit

    if kind == "tab":
        entries = TabEntrySplit.objects.filter(
            recipient_type=TabEntrySplit.RecipientType.GUILD, guild_id__in=guilds
        ).values("entry_id")
        return Q(pk__in=entries)
    filters = {
        "orientation": Q(guild_id__in=guilds),
        "reservation": Q(equipment__guild_id__in=guilds),
        "late_fee": Q(orientation_booking__guild_id__in=guilds) | Q(reservation__equipment__guild_id__in=guilds),
        "cubby": Q(rental__cubby__unit__guild_id__in=guilds),
    }
    return filters[kind]


def _line_keys(line: TransactionLine, keys: frozenset[RecipientKey]) -> frozenset[RecipientKey]:
    """The recipients whose share of ``line`` is the viewer's.

    A cubby's guild share is paid to the payee the guild picked (#979), so on a cubby line, which
    is in scope only for its guild's leads and staff, that payee's share is the guild's.
    """
    if line.source_kind != "cubby":
        return keys
    return keys | {key for key in line.shares if key[0] == "cubby_payee"}


def _share_of(shares: dict[RecipientKey, int], keys: frozenset[RecipientKey], *, omitted: bool) -> int:
    """The viewer's cents of one line: the shares of every recipient that is theirs; nothing when omitted."""
    if omitted:
        return 0
    return sum(cents for key, cents in shares.items() if key in keys)


def _to_cents(amount: Decimal) -> int:
    """A dollar ``Decimal`` as whole cents, half up, as the reconciliation's tab lines round."""
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
