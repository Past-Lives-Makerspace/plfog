"""The Bookings tab on the Orientations page (#626): one list of orientation bookings, scoped by who looks.

A member who runs nothing sees only their own bookings and their own hand recorded
orientations, with the self service actions the cards already offer. A viewer who runs some
orientation (:func:`membership.permissions.manages_orientations`) sees the staff view: every
booking they may run (:func:`membership.permissions.manageable_orientation_bookings`, the
queryset form of the action gate) plus their own, with filters, the CSV export, Add Member
and a "..." menu per row. A row's staff menu shows only on a booking in the managed scope;
the viewer's own booking elsewhere gets the member menu.

The tab replaced the old staff dashboard at ``/orientations/manage/``, which listed every
booking to any lead and passed an unknown ``?sort=`` straight to ``order_by``.

Kept out of ``hub/views.py`` so that module stops growing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from django.db.models import Q
from django.http import HttpRequest, QueryDict
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from classes.table import prepare_table, table_search
from membership.models import Guild, Member, OrientationBooking, OrientationRecord
from membership.permissions import (
    is_effective_staff,
    manageable_orientation_bookings,
    manageable_orientation_records,
    manages_orientations,
)

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from billing.models import LateCancellationFee
    from core.models import SiteConfiguration

#: The chips above the table (``?show=``). Upcoming is the default and never written to the URL.
SHOW_CHOICES = ("upcoming", "reply", "past", "all")

#: Every query parameter the pane owns. Anything else on the page URL (the List pane's
#: ``owner`` and ``q``) stays out of the pane's links.
PANE_KEYS = ("show", "search", "guild", "status", "oriented", "start", "end", "sort", "dir")

#: The columns a ``?sort=`` may name; anything else falls back to the date.
STAFF_SORTABLE = frozenset({"slot__starts_at", "member__full_legal_name", "orientation_type__name", "status"})
MEMBER_SORTABLE = frozenset({"slot__starts_at", "orientation_type__name", "status"})

#: What the staff search box reads. Members get no search: their list is short.
SEARCH_FIELDS = ["member__full_legal_name", "member__preferred_name", "orientation_type__name", "guild__name"]

#: The pane's search parameter. Not ``q``: that one is the List pane's search on the same page.
SEARCH_PARAM = "search"


@dataclass
class BookingRow:
    """One table row: the booking, whose it is, and which "..." menu items it shows.

    Every flag is decided here, once, so the menu template only reads booleans and an item
    never shows when its endpoint would refuse the viewer.
    """

    booking: OrientationBooking
    is_own: bool
    can_manage: bool
    late_fee: LateCancellationFee | None
    can_email: bool = False
    can_view_member: bool = False
    can_reply: bool = False
    can_mark: bool = False
    can_undo: bool = False
    can_refund: bool = False
    can_retry_refund: bool = False
    can_waive_fee: bool = False
    can_refund_fee: bool = False
    can_cancel: bool = False
    can_finish_paying: bool = False
    can_cancel_mine: bool = False
    can_pay_fee: bool = False
    late_cancel_warning: str = ""
    #: The Waive modal's form, built only for a row that offers Waive Late Fee.
    waive_form: Any = None

    @property
    def has_oriented_item(self) -> bool:
        """Whether the staff menu's Oriented group has an item (so it gets a divider)."""
        return self.can_mark or self.can_undo

    @property
    def has_money_item(self) -> bool:
        """Whether the staff menu's Money group has an item (so it gets a divider)."""
        return self.can_refund or self.can_retry_refund or self.can_waive_fee or self.can_refund_fee

    @property
    def fee_unpaid(self) -> bool:
        """Whether the booking's late cancellation fee is still owed: the Status column's fee pill."""
        return self.late_fee is not None and self.late_fee.is_unpaid


def _viewer(request: HttpRequest) -> Member | None:
    """The request's linked member, if any: whose rows read "You"."""
    from hub.views import _get_member

    return _get_member(request)


def _show(request: HttpRequest, *, is_staff_view: bool) -> str:
    """The chip the request names, else Upcoming. Needs a Reply is a staff chip only."""
    show = request.GET.get("show", "")
    if show not in SHOW_CHOICES or (show == "reply" and not is_staff_view):
        return "upcoming"
    return show


def _managed(request: HttpRequest, *, honour_preview: bool) -> QuerySet[OrientationBooking]:
    """Every booking the viewer may run; ``honour_preview`` for what the tab lists (#626 review)."""
    return manageable_orientation_bookings(request, OrientationBooking.objects.all(), honour_preview=honour_preview)


def _base_rows(
    request: HttpRequest, member: Member | None, *, is_staff_view: bool, honour_preview: bool = True
) -> QuerySet[OrientationBooking]:
    """Every booking the viewer may see: their own, plus, for staff, every one they may run."""
    own = Q(member=member) if member is not None else Q(pk__in=[])
    if not is_staff_view:
        return OrientationBooking.objects.filter(own)
    managed = _managed(request, honour_preview=honour_preview)
    return OrientationBooking.objects.filter(Q(pk__in=managed.values("pk")) | own)


def _needs_reply(
    request: HttpRequest, rows: QuerySet[OrientationBooking], *, honour_preview: bool = True
) -> QuerySet[OrientationBooking]:
    """The requests in ``rows`` waiting on the viewer: requested, and in the scope they run.

    The viewer's own request on an orientation someone else runs is theirs to wait on, not to
    answer, so it is neither counted nor listed under Needs a Reply.
    """
    managed = _managed(request, honour_preview=honour_preview)
    return rows.filter(status=OrientationBooking.Status.REQUESTED, pk__in=managed.values("pk"))


def _apply_show(
    request: HttpRequest, rows: QuerySet[OrientationBooking], show: str, *, honour_preview: bool = True
) -> QuerySet[OrientationBooking]:
    """Narrow ``rows`` to the chip: Upcoming is the live ones still ahead, Past every one behind."""
    now = timezone.now()
    if show == "upcoming":
        return rows.filter(status__in=OrientationBooking.LIVE_STATUSES, slot__starts_at__gte=now)
    if show == "reply":
        return _needs_reply(request, rows, honour_preview=honour_preview)
    if show == "past":
        return rows.filter(slot__starts_at__lt=now)
    return rows


def _date_param(request: HttpRequest, key: str) -> date | None:
    """A From or To date from the query string, or None when blank or not a real date."""
    try:
        return parse_date(request.GET.get(key, ""))
    except ValueError:  # well formed but impossible, e.g. 2026-02-30
        return None


def _apply_filters(request: HttpRequest, rows: QuerySet[OrientationBooking]) -> QuerySet[OrientationBooking]:
    """The staff filters: guild, status, oriented and the From / To dates. Unknown values are ignored."""
    guild = request.GET.get("guild", "")
    if guild.isdigit():
        rows = rows.filter(guild_id=int(guild))
    status = request.GET.get("status", "")
    if status in OrientationBooking.Status.values:
        rows = rows.filter(status=status)
    oriented = request.GET.get("oriented", "")
    if oriented in ("yes", "no"):
        rows = rows.filter(is_completed=oriented == "yes")
    start = _date_param(request, "start")
    if start is not None:
        rows = rows.filter(slot__starts_at__date__gte=start)
    end = _date_param(request, "end")
    if end is not None:
        rows = rows.filter(slot__starts_at__date__lte=end)
    return rows


def staff_rows(request: HttpRequest) -> QuerySet[OrientationBooking]:
    """The staff view's rows under the request's chip, filters and search: what Export CSV downloads.

    An action, so it reads the action scope (no preview), like its gate.
    """
    member = _viewer(request)
    rows = _base_rows(request, member, is_staff_view=True, honour_preview=False)
    show = _show(request, is_staff_view=True)
    rows = _apply_filters(request, _apply_show(request, rows, show, honour_preview=False))
    return table_search(rows, request.GET.get(SEARCH_PARAM, "").strip(), SEARCH_FIELDS)


def _with_related(rows: QuerySet[OrientationBooking]) -> QuerySet[OrientationBooking]:
    """Everything a row and its menu read, so the pane costs the same with 1 row or 25."""
    from hub.views import _primary_email_prefetch

    return rows.select_related(
        "slot",
        "slot__orienter",
        "guild",
        "member__user",
        "orientation_type__guild__orientation_settings",
        "orientation_type__equipment",
        "late_fee__member",
    ).prefetch_related("refunds", "late_fee__refunds", _primary_email_prefetch("member__user__emailaddress_set"))


def _late_fee(booking: OrientationBooking) -> LateCancellationFee | None:
    """The booking's late cancellation fee, read off ``select_related`` (no query), or None.

    The reverse one to one from ``LateCancellationFee`` has no row for most bookings; Django's
    missing related object is an ``AttributeError`` too, so ``getattr`` with a default is the
    lookup that answers None for "no fee".
    """
    fee: LateCancellationFee | None = getattr(booking, "late_fee", None)
    return fee


def build_row(
    booking: OrientationBooking,
    *,
    viewer: Member | None,
    can_manage: bool,
    refund_authority: bool,
    is_admin: bool,
    actual_admin: bool,
    site: SiteConfiguration | None = None,
) -> BookingRow:
    """One row and its menu flags (#626 spec, section 4.4).

    ``can_manage`` is whether the booking is in the viewer's managed scope; it is also the
    waive rule, since ``billing.late_fees.can_waive`` for an orientation fee is the same
    owner rule. A checkout still in progress gets View Request only: confirm, decline and
    cancel all refuse a hold, and its paid amount is not settled yet. ``site`` is the loaded
    site settings row, so a page of rows reads the late fee window once.
    """
    from billing.forms import LateFeeWaiveForm
    from billing.models import LateCancellationFee
    from membership.late_cancel import booking_cancel_warning, policy_for_type

    is_own = viewer is not None and booking.member_id == viewer.pk
    row = BookingRow(booking=booking, is_own=is_own, can_manage=can_manage, late_fee=_late_fee(booking))
    status = booking.status
    upcoming = booking.slot.starts_at >= timezone.now()
    fee = row.late_fee
    if can_manage:
        if status == OrientationBooking.Status.PENDING_PAYMENT:
            return row
        row.can_email = is_admin or booking.member.is_public("email")
        row.can_view_member = actual_admin
        row.can_reply = status == OrientationBooking.Status.REQUESTED
        row.can_mark = status == OrientationBooking.Status.CONFIRMED and not booking.is_completed
        row.can_undo = booking.is_completed
        if refund_authority and booking.amount_paid_cents:
            refund_state = booking.refund_state
            row.can_retry_refund = refund_state == "failed"
            row.can_refund = refund_state != "failed" and booking.refundable_cents > 0
        if fee is not None and fee.is_unpaid:
            row.can_waive_fee = True
            row.waive_form = LateFeeWaiveForm(fee=fee)
        row.can_refund_fee = (
            refund_authority
            and fee is not None
            and fee.status == LateCancellationFee.Status.PAID
            and fee.refundable_cents > 0
        )
        row.can_cancel = status == OrientationBooking.Status.CONFIRMED and upcoming
        return row
    # The member menu: only ever on the viewer's own row (the staff rows are managed or own).
    row.can_finish_paying = is_own and status == OrientationBooking.Status.PENDING_PAYMENT
    row.can_cancel_mine = (
        is_own and status in (OrientationBooking.Status.REQUESTED, OrientationBooking.Status.CONFIRMED) and upcoming
    )
    row.can_pay_fee = is_own and fee is not None and fee.is_unpaid
    if row.can_cancel_mine:
        row.late_cancel_warning = booking_cancel_warning(booking, policy_for_type(booking.orientation_type, site=site))
    return row


def _query(params: dict[str, str]) -> str:
    """A pane URL's query string: ``view=bookings`` first, then the given non blank params."""
    query = QueryDict(mutable=True)
    query["view"] = "bookings"
    for key, value in params.items():
        if value:
            query[key] = value
    return query.urlencode()


def _recorded(
    request: HttpRequest, member: Member | None, *, is_staff_view: bool, show: str
) -> list[OrientationRecord] | None:
    """The hand recorded orientations (#465) the pane lists under its table, or None for no list.

    Staff see the ones on types they run, plus their own, when Oriented = Yes or the Past chip
    is on, under the guild filter and the From / To dates (the day it happened). A member sees
    their own whenever they have any.
    """
    own = Q(member=member) if member is not None else Q(pk__in=[])
    if not is_staff_view:
        mine = list(OrientationRecord.objects.with_related().filter(own))
        return mine or None
    if request.GET.get("oriented") != "yes" and show != "past":
        return None
    managed = manageable_orientation_records(request, OrientationRecord.objects.all())
    records = OrientationRecord.objects.with_related().filter(Q(pk__in=managed.values("pk")) | own)
    guild = request.GET.get("guild", "")
    if guild.isdigit():
        records = records.for_guild(int(guild))
    start = _date_param(request, "start")
    if start is not None:
        records = records.filter(completed_on__gte=start)
    end = _date_param(request, "end")
    if end is not None:
        records = records.filter(completed_on__lte=end)
    return list(records)


def _scope_guilds(request: HttpRequest, member: Member | None) -> list[Guild]:
    """The Guild filter's options: every active guild for an admin or officer, else the viewer's own."""
    if is_effective_staff(request):
        return list(Guild.objects.filter(is_active=True).order_by("name"))
    if member is None:
        return []
    return list(
        Guild.objects.filter(Q(guild_lead=member) | Q(staff_memberships__member=member)).distinct().order_by("name")
    )


def _staff_extras(request: HttpRequest, member: Member | None) -> dict[str, Any]:
    """What sits above the staff table: the hours nudge and Add Member (the old dashboard's extras)."""
    from classes.templatetags.classes_tags import cents_as_price
    from hub.forms import OrientationAddMemberForm
    from hub.views import _manageable_slots
    from membership.models import OrientationAvailability

    leadership_guilds = (
        list(
            Guild.objects.filter(Q(guild_lead=member) | Q(staff_memberships__member=member)).distinct().order_by("name")
        )
        if member is not None
        else []
    )
    # "Post your hours": a lead or staffer with no personal hours anywhere gets a link to each
    # guild's Orientations tab; it goes away with their first rule.
    hours_nudge_guilds = (
        leadership_guilds
        if leadership_guilds and not OrientationAvailability.objects.filter(orienter=member).exists()
        else []
    )
    slots = list(_manageable_slots(request))
    # The add form's paid guild note: slot pk to price, for "this guild charges $X".
    paid_slot_prices = {
        str(slot.pk): cents_as_price(slot.orientation_type.price_cents)
        for slot in slots
        if slot.orientation_type.is_paid
    }
    return {
        "hours_nudge_guilds": hours_nudge_guilds,
        "add_member_form": OrientationAddMemberForm(slot_queryset=_manageable_slots(request)) if slots else None,
        "paid_slot_prices_json": json.dumps(paid_slot_prices),
    }


def bookings_pane_context(request: HttpRequest, *, body_only: bool = False) -> dict[str, Any]:
    """Everything ``hub/partials/orientation_bookings_pane.html`` renders, for the page or the partial.

    ``body_only`` is the refund refresh, which swaps in only the table's body: it skips what
    sits above it (the hours nudge and the Add Member form with its slot list).
    """
    from core.models import SiteConfiguration
    from hub.view_as import has_refund_authority

    member = _viewer(request)
    # The staff view follows the effective role: an admin previewing as a member sees their own rows.
    is_staff_view = manages_orientations(request, honour_preview=True)
    show = _show(request, is_staff_view=is_staff_view)
    base = _base_rows(request, member, is_staff_view=is_staff_view)
    rows = _apply_show(request, base, show)
    if is_staff_view:
        rows = _apply_filters(request, rows)
    table = prepare_table(
        request,
        _with_related(rows),
        search_fields=SEARCH_FIELDS if is_staff_view else [],
        search_param=SEARCH_PARAM,
        default_sort="slot__starts_at",
        default_dir="asc" if show in ("upcoming", "reply") else "desc",
        sortable=STAFF_SORTABLE if is_staff_view else MEMBER_SORTABLE,
    )
    page = table["page"]
    bookings = list(page.object_list)
    managed_ids: set[int] = (
        set(
            manageable_orientation_bookings(
                request, OrientationBooking.objects.filter(pk__in=[b.pk for b in bookings])
            ).values_list("pk", flat=True)
        )
        if is_staff_view and bookings
        else set()
    )
    view_as = getattr(request, "view_as", None)
    refund_authority = has_refund_authority(request)
    is_admin = view_as is not None and view_as.is_admin
    actual_admin = view_as is not None and view_as.has_actual("admin")
    # One settings read for every own row's late fee line, not one per row.
    site = SiteConfiguration.load() if member is not None and bookings else None
    booking_rows = [
        build_row(
            b,
            viewer=member,
            can_manage=b.pk in managed_ids,
            refund_authority=refund_authority,
            is_admin=is_admin,
            actual_admin=actual_admin,
            site=site,
        )
        for b in bookings
    ]

    # The pane's own params, so every link and form keeps them and nothing from the List pane.
    params = {key: request.GET.get(key, "").strip() for key in PANE_KEYS}
    params["search"] = table["q"]
    if not is_staff_view:
        params.update(search="", guild="", status="", oriented="", start="", end="")
    params["show"] = "" if show == "upcoming" else show
    params["sort"] = "" if table["sort"] == "slot__starts_at" else table["sort"]
    filters = {key: params[key] for key in ("search", "guild", "status", "oriented", "start", "end")}
    page_query = _query({**params, "page": str(page.number) if page.number > 1 else ""})
    return {
        "is_staff_view": is_staff_view,
        "booking_rows": booking_rows,
        "page": page,
        "sort": table["sort"],
        "sort_dir": table["sort_dir"],
        "base_params": _query(params),
        "show": show,
        "chips": [
            {"key": key, "label": label, "url": "?" + _query({**filters, "show": "" if key == "upcoming" else key})}
            for key, label in (("upcoming", "Upcoming"), ("reply", "Needs a Reply"), ("past", "Past"), ("all", "All"))
            if key != "reply" or is_staff_view
        ],
        "needs_reply_count": _needs_reply(request, base).count() if is_staff_view else 0,
        "filters": filters,
        "bookings_is_filtered": any(filters.values()),
        "clear_filters_url": "?" + _query({"show": params["show"]}),
        "guild_options": _scope_guilds(request, member) if is_staff_view else [],
        "statuses": OrientationBooking.Status.choices,
        "export_query": _query(params),
        "recorded_orientations": _recorded(request, member, is_staff_view=is_staff_view, show=show),
        "viewer_has_refund_authority": refund_authority,
        "viewer_is_admin": actual_admin,
        "bookings_next": f"{reverse('hub_orientations')}?{page_query}",
        "bookings_refresh_url": f"{reverse('hub_orientations_bookings')}?{page_query}&part=body",
        **(_staff_extras(request, member) if is_staff_view and not body_only else {}),
    }
