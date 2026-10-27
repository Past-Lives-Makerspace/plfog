"""The Bookings tab on the Reservations page (#627): one list of equipment reservations, scoped by who looks.

The twin of the Orientations page's tab (``hub/orientation_bookings.py``, #626), built from the
same pieces (``hub/bookings_tab.py``, the shared pane CSS, ``components/row_actions.html``).
A member who manages no equipment sees only their own reservations, past, upcoming and
cancelled, with the self service actions the schedule already offers. A viewer who manages
some equipment (:func:`membership.permissions.manages_equipment`) sees the staff view: every
reservation on equipment they manage plus their own, with filters and a "..." menu per row.

The list follows the effective role: an admin previewing as a member sees what that member
would, even though migration 0161 gave every admin the EQUIPMENT capability. Every action
gate is untouched and still reads the capability; the row menu's managed set is read the way
the list is, so a menu never offers what the list says the viewer does not run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from django.db.models import Q
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from classes.table import prepare_table, table_search
from hub.bookings_tab import date_param, late_fee_of, pane_query
from membership.models import Equipment, EquipmentReservation, Member, credit_hours_short, duration_label
from membership.permissions import manageable_reservations, manages_equipment

if TYPE_CHECKING:
    from datetime import datetime

    from django.db.models import QuerySet

    from django.contrib.auth.models import User

    from billing.models import LateCancellationFee
    from core.models import SiteConfiguration
    from membership.models import EquipmentReservationQuerySet

#: The chips above the table (``?show=``). Upcoming is the default and never written to the URL.
#: Needs Approval (#748) is the staff view's alone: the requests on equipment they manage.
SHOW_CHOICES = ("upcoming", "needs_approval", "past", "all")

#: Every query parameter the pane owns. The List pane's own ``guild``, ``kind`` and ``q`` stay out.
PANE_KEYS = ("show", "search", "equipment", "status", "fee", "start", "end", "sort", "dir")

#: The Late Fee filter's values: the fee statuses a manager asks about.
FEE_FILTERS = ("unpaid", "paid", "waived")

#: The columns a ``?sort=`` may name; anything else falls back to the date.
STAFF_SORTABLE = frozenset({"starts_at", "member__full_legal_name", "equipment__name", "status"})
MEMBER_SORTABLE = frozenset({"starts_at", "equipment__name", "status"})

#: What the staff search box reads. Members get no search: their list is short.
SEARCH_FIELDS = ["member__full_legal_name", "member__preferred_name", "equipment__name"]

#: The pane's search parameter. Not ``q``: that one is the List pane's search on the same page.
SEARCH_PARAM = "search"


@dataclass
class ReservationRow:
    """One table row: the reservation, whose it is, and which "..." menu items it shows."""

    reservation: EquipmentReservation
    is_own: bool
    can_manage: bool
    late_fee: LateCancellationFee | None
    can_email: bool = False
    can_view_member: bool = False
    can_waive_fee: bool = False
    can_refund_fee: bool = False
    can_cancel: bool = False
    can_approve: bool = False
    can_decline: bool = False
    can_cancel_mine: bool = False
    can_pay_fee: bool = False
    late_cancel_warning: str = ""
    #: A request nobody decided before its time ended (#748): shown as "Not decided", never warn.
    is_undecided: bool = False
    #: The Waive modal's form, built only for a row that offers Waive Late Fee.
    waive_form: Any = None
    #: Credit Unused Time (#787): a started, confirmed, hourly booking the viewer manages with time left to credit.
    can_credit_unused: bool = False
    #: The Credit Unused Time modal's form, built only for a row that offers it.
    credit_form: Any = None
    #: End Early (#787) on the member's own in progress booking: the hours it would credit, "" when not offered.
    end_early_hours: str = ""

    @property
    def has_money_item(self) -> bool:
        """Whether the staff menu's Money group has an item (so it gets a divider)."""
        return self.can_waive_fee or self.can_refund_fee or self.can_credit_unused

    @property
    def credited_minutes(self) -> int:
        """Minutes of this booking's unused time credited back (#787), from the row query's annotation.

        Read straight off the annotation :func:`_with_related` adds, so a row built without it fails loudly.
        """
        return cast(int, self.reservation.credited_minutes_total)  # type: ignore[attr-defined]

    @property
    def credited_label(self) -> str:
        """The row's "Credited 2h" pill (#787): the unused time a manager credited back, "" when none."""
        minutes = self.credited_minutes
        return f"Credited {credit_hours_short(minutes)}" if minutes else ""

    @property
    def has_unread_messages(self) -> bool:
        """Whether the thread holds a message the viewer has not read: the row's New message pill.

        Read straight off the annotation :func:`_with_related` adds, so a row built without it fails loudly.
        """
        return cast(bool, self.reservation.has_unread_messages)  # type: ignore[attr-defined]

    @property
    def fee_unpaid(self) -> bool:
        """Whether the reservation's late cancellation fee is still owed: the Status column's fee pill."""
        return self.late_fee is not None and self.late_fee.is_unpaid


def _show(request: HttpRequest, *, is_staff_view: bool) -> str:
    """The chip the request names, else Upcoming. Needs Approval is a staff chip; a member gets Upcoming."""
    show = request.GET.get("show", "")
    if show == "needs_approval" and not is_staff_view:
        return "upcoming"
    return show if show in SHOW_CHOICES else "upcoming"


def _base_rows(request: HttpRequest, member: Member | None, *, is_staff_view: bool) -> QuerySet[EquipmentReservation]:
    """Every reservation the viewer may see: their own, plus, for staff, every one on equipment they manage.

    Managers' blocks (#657) are held time, not bookings, so they never list here; the manage tab lists them.
    """
    bookings = EquipmentReservation.objects.reservations()
    own = Q(member=member) if member is not None else Q(pk__in=[])
    if not is_staff_view:
        return bookings.filter(own)
    managed = manageable_reservations(request, EquipmentReservation.objects.all(), honour_preview=True)
    return bookings.filter(Q(pk__in=managed.values("pk")) | own)


def _apply_show(
    rows: QuerySet[EquipmentReservation], show: str, waiting: QuerySet[EquipmentReservation]
) -> QuerySet[EquipmentReservation]:
    """Narrow ``rows`` to the chip: Upcoming is the held ones not yet ended, Past the rest.

    Held means confirmed or awaiting approval (#748); Needs Approval is ``waiting``, the
    requests on equipment the viewer manages.
    """
    now = timezone.now()
    upcoming = Q(status__in=EquipmentReservation.HOLDING_STATUSES, ends_at__gt=now)
    if show == "upcoming":
        return rows.filter(upcoming)
    if show == "needs_approval":
        return rows.filter(pk__in=waiting.values("pk"))
    if show == "past":
        return rows.exclude(upcoming)
    return rows


def _apply_filters(request: HttpRequest, rows: QuerySet[EquipmentReservation]) -> QuerySet[EquipmentReservation]:
    """The staff filters: equipment, status, late fee and the From / To dates. Unknown values are ignored."""
    equipment = request.GET.get("equipment", "")
    if equipment.isdigit():
        rows = rows.filter(equipment_id=int(equipment))
    status = request.GET.get("status", "")
    if status in EquipmentReservation.Status.values:
        rows = rows.filter(status=status)
    fee = request.GET.get("fee", "")
    if fee in FEE_FILTERS:
        rows = rows.filter(late_fee__status=fee)
    start = date_param(request, "start")
    if start is not None:
        rows = rows.filter(starts_at__date__gte=start)
    end = date_param(request, "end")
    if end is not None:
        rows = rows.filter(starts_at__date__lte=end)
    return rows


def _with_related(request: HttpRequest, rows: QuerySet[EquipmentReservation]) -> QuerySet[EquipmentReservation]:
    """Everything a row and its menu read, so the pane costs the same with 1 row or 25.

    The viewer's unread cue rides along as one subquery (``with_unread_messages_for``).
    """
    from hub.views import _primary_email_prefetch

    # The reservation's own refunds feed its Paid and Refunded pills (#749); the unused time credited back
    # rides the row as one scalar subquery (#787), so the "Credited 2h" pill and the menu cost no query.
    related = rows.select_related("equipment__guild", "member__user", "late_fee__member").prefetch_related(
        "late_fee__refunds", "refunds", _primary_email_prefetch("member__user__emailaddress_set")
    )
    # Every caller passes EquipmentReservation.objects rows, so the model's own queryset is what arrives here.
    return (
        cast("EquipmentReservationQuerySet", related)
        .with_credited_unused()
        .with_unread_messages_for(_viewer_user(request))
    )


def _viewer_user(request: HttpRequest) -> User | None:
    """The signed in account whose unread cues a list shows, or ``None``."""
    user = request.user
    return cast("User", user) if user.is_authenticated else None


def build_row(
    reservation: EquipmentReservation,
    *,
    viewer: Member | None,
    can_manage: bool,
    refund_authority: bool,
    is_admin: bool,
    actual_admin: bool,
    site: SiteConfiguration | None = None,
) -> ReservationRow:
    """One row and its menu flags (#627 spec, section 5.4).

    ``can_manage`` is whether the reservation is in the viewer's managed scope, read as the
    list is. It is also the waive rule: ``billing.late_fees.can_waive`` for a reservation fee
    is :func:`can_manage_equipment` on its equipment. A manager on their own row of equipment
    they manage gets the staff menu, since the cancel endpoint treats a posted reason as the
    manager route for one's own row. ``site`` is the loaded settings row, so a page of rows
    reads the late fee window once.
    """
    from billing.forms import LateFeeWaiveForm
    from billing.models import LateCancellationFee
    from membership.late_cancel import cancel_sentence, policy_for_equipment

    is_own = viewer is not None and reservation.member_id == viewer.pk
    fee = late_fee_of(reservation)
    row = ReservationRow(reservation=reservation, is_own=is_own, can_manage=can_manage, late_fee=fee)
    now = timezone.now()
    confirmed = reservation.status == EquipmentReservation.Status.CONFIRMED
    waiting = reservation.is_awaiting_approval and reservation.ends_at > now
    row.is_undecided = reservation.is_awaiting_approval and not waiting
    if can_manage:
        row.can_email = not is_own and (is_admin or reservation.member.is_public("email"))
        row.can_view_member = actual_admin
        if fee is not None and fee.is_unpaid:
            row.can_waive_fee = True
            row.waive_form = LateFeeWaiveForm(fee=fee)
        row.can_refund_fee = (
            refund_authority
            and fee is not None
            and fee.status == LateCancellationFee.Status.PAID
            and fee.refundable_cents > 0
        )
        # A booking whose unused time was credited is never cancelled or declined (#787): no refund on top.
        credited = row.credited_minutes > 0
        row.can_cancel = confirmed and reservation.ends_at > now and not credited
        # A request awaiting approval offers Approve and Decline in place of Cancel (#748).
        row.can_approve = waiting
        row.can_decline = waiting and not credited
        # Credit Unused Time (#787): once a confirmed booking on an hourly item has started, while time is left to credit.
        credit_left = reservation.duration_minutes - row.credited_minutes
        # A booking the member ended early already had its unused time credited (#787).
        ended_early = cast(bool, reservation.has_ended_early)  # type: ignore[attr-defined]
        if (
            confirmed
            and reservation.equipment.is_hourly
            and reservation.starts_at <= now
            and credit_left > 0
            and not ended_early
        ):
            from hub.forms import EquipmentCreditUnusedForm

            row.can_credit_unused = True
            row.credit_form = EquipmentCreditUnusedForm(
                limit_minutes=credit_left, auto_id=f"credit-unused-{reservation.pk}-%s"
            )
        # A manager's own booking takes this staff menu, and still offers End Early under the same gates (#787).
        _offer_end_early(row, viewer, now)
        return row
    # The member menu: only ever on the viewer's own row (the staff rows are managed or own).
    row.can_cancel_mine = is_own and (confirmed or waiting) and reservation.starts_at > now and not row.credited_minutes
    row.can_pay_fee = is_own and fee is not None and fee.is_unpaid
    _offer_end_early(row, viewer, now)
    # A request was never booked, so cancelling it never carries a fee line (#748).
    if row.can_cancel_mine and confirmed:
        policy = policy_for_equipment(reservation.equipment, site=site)
        row.late_cancel_warning = cancel_sentence(policy) if policy.is_late(reservation.starts_at, now=now) else ""
    return row


def _offer_end_early(row: ReservationRow, viewer: Member | None, now: datetime) -> None:
    """Set the row's End Early hours (#787) while the viewer's own booking would take it, in either menu."""
    reservation = row.reservation
    if reservation.can_end_early(viewer, now=now, credited_minutes=row.credited_minutes):
        row.end_early_hours = duration_label(reservation.end_early_minutes(now))


def _scope_equipment(request: HttpRequest) -> list[Equipment]:
    """The Equipment filter's options: the items in the viewer's managed scope that have reservations."""
    managed = manageable_reservations(request, EquipmentReservation.objects.all(), honour_preview=True)
    return list(Equipment.objects.filter(pk__in=managed.values("equipment_id")).order_by("name"))


def _chip_labels(waiting_count: int) -> list[tuple[str, str]]:
    """The chips in order: Needs Approval follows Upcoming only while something waits (#748)."""
    chips = [("upcoming", "Upcoming")]
    if waiting_count:
        chips.append(("needs_approval", "Needs Approval"))
    return [*chips, ("past", "Past"), ("all", "All")]


def reservation_bookings_context(request: HttpRequest) -> dict[str, Any]:
    """Everything ``hub/partials/reservation_bookings_pane.html`` renders, for the page or the partial."""
    from core.models import SiteConfiguration
    from hub.view_as import has_refund_authority
    from hub.views import _get_member

    member = _get_member(request)
    # The staff view follows the effective role: an admin previewing as a member sees their own rows.
    is_staff_view = manages_equipment(request, honour_preview=True)
    show = _show(request, is_staff_view=is_staff_view)
    # The requests on equipment the viewer manages (#748): the Needs Approval chip's rows and its count.
    waiting = (
        manageable_reservations(
            request, EquipmentReservation.objects.reservations().awaiting_approval(), honour_preview=True
        )
        if is_staff_view
        else EquipmentReservation.objects.none()
    )
    rows = _apply_show(_base_rows(request, member, is_staff_view=is_staff_view), show, waiting)
    waiting_count = 0
    if is_staff_view:
        rows = _apply_filters(request, rows)
        # Counted through the same filters and search the chip's own list goes through, so they agree.
        search = request.GET.get(SEARCH_PARAM, "").strip()
        waiting_count = table_search(_apply_filters(request, waiting), search, SEARCH_FIELDS).count()
    table = prepare_table(
        request,
        _with_related(request, rows),
        search_fields=SEARCH_FIELDS if is_staff_view else [],
        search_param=SEARCH_PARAM,
        default_sort="starts_at",
        default_dir="asc" if show in ("upcoming", "needs_approval") else "desc",
        sortable=STAFF_SORTABLE if is_staff_view else MEMBER_SORTABLE,
    )
    page = table["page"]
    reservations = list(page.object_list)
    # Read the way the list is (honour_preview), so a menu never offers more than the list shows.
    managed_ids: set[int] = (
        set(
            manageable_reservations(
                request,
                EquipmentReservation.objects.filter(pk__in=[r.pk for r in reservations]),
                honour_preview=True,
            ).values_list("pk", flat=True)
        )
        if is_staff_view and reservations
        else set()
    )
    view_as = getattr(request, "view_as", None)
    refund_authority = has_refund_authority(request)
    is_admin = view_as is not None and view_as.is_admin
    actual_admin = view_as is not None and view_as.has_actual("admin")
    site = SiteConfiguration.load() if member is not None and reservations else None
    reservation_rows = [
        build_row(
            r,
            viewer=member,
            can_manage=r.pk in managed_ids,
            refund_authority=refund_authority,
            is_admin=is_admin,
            actual_admin=actual_admin,
            site=site,
        )
        for r in reservations
    ]

    # The pane's own params, so every link and form keeps them and nothing from the List pane.
    params = {key: request.GET.get(key, "").strip() for key in PANE_KEYS}
    params["search"] = table["q"]
    if not is_staff_view:
        params.update(search="", equipment="", status="", fee="", start="", end="")
    params["show"] = "" if show == "upcoming" else show
    params["sort"] = "" if table["sort"] == "starts_at" else table["sort"]
    filters = {key: params[key] for key in ("search", "equipment", "status", "fee", "start", "end")}
    page_query = pane_query({**params, "page": str(page.number) if page.number > 1 else ""})
    return {
        "is_staff_view": is_staff_view,
        "reservation_rows": reservation_rows,
        "page": page,
        "sort": table["sort"],
        "sort_dir": table["sort_dir"],
        "base_params": pane_query(params),
        "show": show,
        "chips": [
            {
                "key": key,
                "label": label,
                "url": "?" + pane_query({**filters, "show": "" if key == "upcoming" else key}),
                "count": waiting_count if key == "needs_approval" else 0,
            }
            for key, label in _chip_labels(waiting_count)
        ],
        "filters": filters,
        "bookings_is_filtered": any(filters.values()),
        "clear_filters_url": "?" + pane_query({"show": params["show"]}),
        "equipment_options": _scope_equipment(request) if is_staff_view else [],
        "statuses": EquipmentReservation.Status.choices,
        "fee_filters": [(value, value.capitalize()) for value in FEE_FILTERS],
        "viewer_has_refund_authority": refund_authority,
        "bookings_next": f"{reverse('hub_equipment_index')}?{page_query}",
        "bookings_refresh_url": f"{reverse('hub_equipment_bookings')}?{page_query}",
        # A message notice or a Send lands here with ?messages=res-<pk>: the Messages modal opens on it.
        "open_reservation_thread": reservation_thread_to_open(request),
    }


def awaiting_approval_rows(request: HttpRequest, *, limit: int) -> tuple[list[ReservationRow], int]:
    """The soonest ``limit`` requests awaiting the viewer's approval, as rows with the tab's staff menu, and how many wait in all.

    The Manage Bookings home's Needs Attention group (#768): the Needs Approval scope
    (:func:`manageable_reservations` over ``awaiting_approval()``, a preview honoured), each row
    built as the tab builds it, so Approve and Decline Reservation are the tab's own endpoints
    and never a second path. A viewer who manages nothing gets no rows.
    """
    from hub.view_as import has_refund_authority
    from hub.views import _get_member

    waiting = manageable_reservations(
        request, EquipmentReservation.objects.reservations().awaiting_approval(), honour_preview=True
    )
    total = waiting.count()
    if not total:
        return [], 0
    view_as = getattr(request, "view_as", None)
    refund_authority = has_refund_authority(request)
    is_admin = view_as is not None and view_as.is_admin
    actual_admin = view_as is not None and view_as.has_actual("admin")
    member = _get_member(request)
    rows = [
        build_row(
            reservation,
            viewer=member,
            can_manage=True,
            refund_authority=refund_authority,
            is_admin=is_admin,
            actual_admin=actual_admin,
        )
        for reservation in _with_related(request, waiting)[:limit]
    ]
    return rows, total


#: A reservation's thread key prefix in ``?messages=`` ("res-12"), apart from an orientation booking's pk.
THREAD_KEY_PREFIX = "res-"


def reservation_thread_side(request: HttpRequest, reservation: EquipmentReservation) -> str | None:
    """Which side of ``reservation``'s thread the request is on: "member", "staff", or ``None`` for no access.

    The member who booked is the member side, even when they also manage the equipment;
    anyone ``can_manage_equipment`` admits (its staff, its guild's leadership, Equipment
    Administrators, admins) is the staff side. A block has no thread, so nobody may read one.
    """
    from hub.views import _get_member
    from membership.permissions import can_manage_equipment

    if reservation.is_block:
        return None
    member = _get_member(request)
    if member is not None and reservation.member_id == member.pk:
        return "member"
    if can_manage_equipment(request, reservation.equipment):
        return "staff"
    return None


def reservation_thread_to_open(request: HttpRequest) -> int | None:
    """The reservation whose messages a page opens on arrival (``?messages=res-<pk>``), when the viewer may read them.

    A Send lands back with that key (a boosted redirect drops a ``#fragment``), and every
    message notice links with it. ``None`` for any other key, an unknown reservation, or one
    the viewer has no side on, so a modal never opens empty.
    """
    key = request.GET.get("messages", "")
    raw_pk = key.removeprefix(THREAD_KEY_PREFIX)
    if raw_pk == key or not raw_pk.isdigit():
        return None
    reservation = EquipmentReservation.objects.select_related("equipment__guild").filter(pk=int(raw_pk)).first()
    if reservation is None or reservation_thread_side(request, reservation) is None:
        return None
    return reservation.pk
