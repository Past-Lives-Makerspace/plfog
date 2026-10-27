"""The Manage Bookings home (#768): one page for everyone who runs orientations or equipment.

Teaching is the one place for an instructor's classes; this is the one place for a guild lead's,
an orientor's or an equipment manager's bookings, at ``/manage/bookings/``. Three cards: Needs
Attention (orientation requests waiting on the viewer and reservations awaiting their approval,
each the Bookings tab's own row, menu and modal, so there is no second Confirm or Approve path),
What You Run (a card per guild and per piece of equipment in scope, with its state, its
orientations and links into the pages that manage it) and Your Hours (the viewer's own recurring
hours across every owner, with the existing Edit Hours modal). ``/orientations/manage/``, the
old staff dashboard, 302s here.

The sidebar entry and the page share one gate, :func:`membership.permissions.runs_bookings`. The
lists honour a ``view_as`` preview the way the Bookings tabs do (an admin previewing as a member
sees what that member would), and every action keeps its own gate. Kept out of ``hub/views.py``
so that module stops growing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, cast

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Prefetch, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from hub.orientation_bookings import needs_reply_rows
from hub.reservation_bookings import awaiting_approval_rows, reservation_thread_to_open
from membership.models import (
    Equipment,
    EquipmentReservation,
    Guild,
    GuildOrientationSettings,
    Member,
    OrientationAvailability,
    OrientationType,
)
from membership.permissions import (
    is_effective_staff,
    manageable_equipment,
    manageable_guilds,
    manages_all_equipment,
    member_runs_anything,
    runs_bookings,
)

if TYPE_CHECKING:
    from django.db.models import QuerySet

#: How many rows each Needs Attention group shows before its "View all" link.
ATTENTION_CAP = 10

GuildState = Literal["on", "paused", "off"]
EquipmentState = Literal["open", "closed", "retired"]


@dataclass(frozen=True)
class OrientationLine:
    """One active orientation on a guild card: its name and the collapsed row's meta line (#732 wording)."""

    name: str
    meta: str


@dataclass(frozen=True)
class GuildCard:
    """One guild the viewer runs: its booking state, its active orientations and who publishes hours."""

    guild: Guild
    state: GuildState
    state_label: str
    #: The lead's "why" while Paused, blank otherwise.
    closed_message: str
    orientations: list[OrientationLine]
    #: How many people have published recurring hours on this guild.
    hours_people: int

    @property
    def orientations_url(self) -> str:
        """The guild's Orientations page (#672)."""
        return reverse("hub_guild_orientations", args=[self.guild.pk])

    @property
    def bookings_url(self) -> str:
        """The Orientations page's Bookings tab filtered to this guild."""
        return f"{reverse('hub_orientations')}?view=bookings&guild={self.guild.pk}"


@dataclass(frozen=True)
class EquipmentCard:
    """One piece of equipment the viewer manages: its state and how many requests await approval."""

    equipment: Equipment
    state: EquipmentState
    state_label: str
    awaiting: int

    @property
    def manage_url(self) -> str:
        """The equipment's manage panel."""
        return reverse("hub_equipment_manage", args=[self.equipment.slug])

    @property
    def bookings_url(self) -> str:
        """The Reservations page's Bookings tab filtered to this equipment."""
        return f"{reverse('hub_equipment_index')}?view=bookings&equipment={self.equipment.pk}"


@dataclass
class HoursGroup:
    """The viewer's recurring hours on one owner (a guild or a piece of equipment) and its Edit Hours URL."""

    key: str
    owner_name: str
    edit_url: str
    rules: list[OrientationAvailability] = field(default_factory=list)

    @property
    def has_open(self) -> bool:
        """Whether any rule is an open window, so a fixed rule names its style (the Orientation Schedule rule)."""
        return any(rule.is_open for rule in self.rules)


def _guild_state(guild: Guild) -> tuple[GuildState, str, str]:
    """The guild card's chip: Off with no settings row or booking switched off, Paused while closed, else On."""
    try:
        settings_row = guild.orientation_settings
    except GuildOrientationSettings.DoesNotExist:
        return "off", "Off", ""
    if not settings_row.is_enabled:
        return "off", "Off", ""
    if settings_row.is_closed:
        return "paused", "Paused", settings_row.closed_message
    return "on", "On", ""


def _equipment_state(equipment: Equipment) -> tuple[EquipmentState, str]:
    """The equipment card's chip: Retired when inactive, Closed for maintenance while closed, else Open."""
    if not equipment.is_active:
        return "retired", "Retired"
    if equipment.is_closed:
        return "closed", "Closed for maintenance"
    return "open", "Open"


def _orientation_meta(orientation_type: OrientationType) -> str:
    """The collapsed orientation row's line (``OrientationTypeForm.summary_meta``): "60 min · $15", "45 min · Free"."""
    if orientation_type.is_donation:
        price = "Donation based"
    elif orientation_type.price_cents:
        price = OrientationType.dollars(orientation_type.price_cents)
    else:
        price = "Free"
    return f"{orientation_type.duration_minutes} min · {price}"


def _guild_cards(request: HttpRequest) -> list[GuildCard]:
    """What You Run's guild cards: every guild in scope, its settings, active orientations and hours count, in three queries.

    Admins and officers run every active guild, so theirs are narrowed to the guilds with an
    orientation settings row (a guild that never opened orientations has nothing to run); a lead
    or staffer sees each guild they lead or staff, settings row or not, with an Off chip until
    one exists.
    """
    guilds = manageable_guilds(request)
    if is_effective_staff(request):
        guilds = guilds.filter(orientation_settings__isnull=False)
    active_types = OrientationType.objects.active().order_by("sort_order", "name")
    cards: list[GuildCard] = []
    for guild in (
        guilds.select_related("orientation_settings")
        .prefetch_related(Prefetch("orientation_types", queryset=active_types, to_attr="active_orientations"))
        .annotate(
            hours_people=Count(
                "orientation_rules__orienter", distinct=True, filter=Q(orientation_rules__is_active=True)
            )
        )
    ):
        state, label, closed_message = _guild_state(guild)
        active_orientations = cast(list[OrientationType], guild.active_orientations)  # type: ignore[attr-defined]
        cards.append(
            GuildCard(
                guild=guild,
                state=state,
                state_label=label,
                closed_message=closed_message,
                orientations=[OrientationLine(name=t.name, meta=_orientation_meta(t)) for t in active_orientations],
                hours_people=cast(int, guild.hours_people),  # type: ignore[attr-defined]
            )
        )
    return cards


def _equipment_cards(request: HttpRequest) -> list[EquipmentCard]:
    """What You Run's equipment cards: every item in scope with its chip and how many requests await approval, in one query.

    The site tier (an admin or an EQUIPMENT capability holder, :func:`manages_all_equipment`)
    sees every active item; everyone else, an officer included, sees exactly what
    :func:`manageable_equipment` says they lead, staff or manage, retired gear included, since
    a retired tool stays with the people assigned to it. The preview is honoured like the
    Bookings tabs.
    """
    items: QuerySet[Equipment]
    if manages_all_equipment(request, honour_preview=True):
        items = Equipment.objects.active()
    else:
        items = manageable_equipment(request, Equipment.objects.all(), honour_preview=True)
    awaiting = Q(
        reservations__kind=EquipmentReservation.Kind.RESERVATION,
        reservations__status=EquipmentReservation.Status.PENDING_APPROVAL,
        reservations__ends_at__gt=timezone.now(),
    )
    cards: list[EquipmentCard] = []
    for item in (
        items.select_related("guild").annotate(awaiting=Count("reservations", filter=awaiting)).order_by("name")
    ):
        state, label = _equipment_state(item)
        cards.append(
            EquipmentCard(
                equipment=item,
                state=state,
                state_label=label,
                awaiting=cast(int, item.awaiting),  # type: ignore[attr-defined]
            )
        )
    return cards


def _hours_groups(member: Member) -> list[HoursGroup]:
    """Your Hours: the member's own recurring rules on every owner they are still on, grouped by owner, in one query.

    Only rules whose owner the member still leads or staffs (a guild) or still runs (equipment:
    a staff row, or the owning guild's leadership) are listed, which is exactly what
    ``can_edit_orienter_hours`` and its equipment twin let them open. A rule left behind on a
    guild they have since left is that guild's Former Staff business, and its Edit Hours would
    only be refused. Each group's Edit Hours URL is the owner's existing modal form scoped to
    the member (``?orienter=<self>``); the modal's save lands on the owner's page as it always has.
    """
    led_or_staffed = member.staffed_guilds.values("pk")
    run = Equipment.objects.filter(
        Q(staff_memberships__member=member) | Q(guild__guild_lead=member) | Q(guild__staff_memberships__member=member)
    ).values("pk")
    rules = (
        OrientationAvailability.objects.for_orienter(member)
        .filter(Q(guild__in=led_or_staffed) | Q(orientation_type__equipment__in=run))
        .select_related("guild", "orientation_type__guild", "orientation_type__equipment")
        .order_by("weekday", "start_time", "pk")
    )
    groups: dict[str, HoursGroup] = {}
    for rule in rules:
        owner = rule.orientation_type.owner if rule.orientation_type is not None else cast(Guild, rule.guild)
        if isinstance(owner, Guild):
            key = f"guild-{owner.pk}"
            edit_url = f"{reverse('hub_guild_orientation_hours_form', args=[owner.pk])}?orienter={member.pk}"
        else:
            key = f"equipment-{owner.pk}"
            edit_url = f"{reverse('hub_equipment_orientation_hours_form', args=[owner.slug])}?orienter={member.pk}"
        if key not in groups:
            groups[key] = HoursGroup(key=key, owner_name=owner.name, edit_url=edit_url)
        groups[key].rules.append(rule)
    return sorted(groups.values(), key=lambda group: group.owner_name.lower())


def manage_bookings_context(request: HttpRequest) -> dict[str, Any]:
    """Everything ``hub/manage_bookings.html`` renders.

    The Needs Attention rows come from the two Bookings tabs' own builders, so the row partials,
    menus and modals those tabs render work here unchanged: ``is_staff_view`` is always on
    (every row here is in the viewer's scope) and ``bookings_next`` is this page, so every
    Confirm, Decline, Approve and Decline Reservation lands back here. ``hours_groups`` is
    ``None`` for a viewer on no leadership (an admin who runs nothing), which hides the card.
    """
    from hub.view_as import has_refund_authority
    from hub.views import _get_member

    member = _get_member(request)
    booking_rows, needs_reply_total = needs_reply_rows(request, limit=ATTENTION_CAP)
    reservation_rows, awaiting_total = awaiting_approval_rows(request, limit=ATTENTION_CAP)
    return {
        "booking_rows": booking_rows,
        "needs_reply_total": needs_reply_total,
        "needs_reply_all_url": f"{reverse('hub_orientations')}?view=bookings&show=reply",
        "reservation_rows": reservation_rows,
        "awaiting_total": awaiting_total,
        "awaiting_all_url": f"{reverse('hub_equipment_index')}?view=bookings&show=needs_approval",
        "attention_count": needs_reply_total + awaiting_total,
        "guild_cards": _guild_cards(request),
        "equipment_cards": _equipment_cards(request),
        "hours_groups": _hours_groups(member) if member is not None and member_runs_anything(member) else None,
        "show_add_orientation": manageable_guilds(request).exists(),
        "is_staff_view": True,
        "viewer_has_refund_authority": has_refund_authority(request),
        "bookings_next": reverse("hub_manage_bookings"),
        # A Send from a row's Messages lands back with ?messages=res-<pk>: the modal opens on it again.
        "open_reservation_thread": reservation_thread_to_open(request),
    }


@login_required
def hub_manage_bookings(request: HttpRequest) -> HttpResponse:
    """The Manage Bookings home (#768). A member who runs nothing is sent home, as Admin Tools does."""
    from hub.views import _get_hub_context

    if not runs_bookings(request):
        return redirect("hub_home")
    return render(
        request, "hub/manage_bookings.html", {**_get_hub_context(request), **manage_bookings_context(request)}
    )
