"""The event-category × channel preference matrix for the settings page (§2.7).

The unified preferences page is an **event × channel** grid sourced from the event
registry: one row per event the user may control, one cell per *per-recipient*
channel that event declares. A ``FORCED`` cell renders locked-on (the user can't opt
out — essentials + operational mail, Decision 1); an opt-out-able cell reflects the
user's saved :class:`core.models.NotificationPreference` row (or the event's channel
default when none exists).

Broadcast channels (Discord) are **not** per-user — they post once per event to a
configured webhook (admin-configured, §2.4) — so they never appear as a user
preference column. Channels the user has no agency over (IN_APP is always on) still
appear, rendered locked-on, so the page honestly shows every way an event reaches
them.

This is the model/service layer for the page (CLAUDE.md: logic out of views). The
view calls :func:`build_matrix` for the GET context and :func:`save_matrix` for the
POST.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.events import preferences
from core.events.registry import Channel, EventType, Recipients, all_events
from core.models import NotificationPreference

if TYPE_CHECKING:
    from django.contrib.auth.models import User

# The per-recipient channels a user can hold a preference on, in display order.
# DISCORD is a per-event BROADCAST channel (event→webhook, not per-user) and is
# deliberately excluded — a member cannot opt into/out of a site-wide broadcast.
# DISCORD_DM, by contrast, IS per-user (the bot DMs the individual member), so it is
# included; its column appears once at least one event declares it (visible_channels).
USER_CHANNELS: tuple[Channel, ...] = (
    Channel.IN_APP,
    Channel.EMAIL,
    Channel.PUSH,
    Channel.DISCORD_DM,
    Channel.SCHEDULED_EMAIL,
    Channel.DIGEST,
)

# Human labels for the matrix column headers.
CHANNEL_LABELS: dict[Channel, str] = {
    Channel.IN_APP: "In-app (Bell)",
    Channel.EMAIL: "Email",
    Channel.PUSH: "Push",
    Channel.DISCORD_DM: "Discord",
    Channel.SCHEDULED_EMAIL: "Scheduled",
    Channel.DIGEST: "Digest",
}

# Shown on a disabled DISCORD_DM toggle when the member hasn't linked Discord yet.
_DISCORD_LINK_HINT = "Connect your Discord account first to receive DMs."

# Stable category display order; any category not listed falls to the end, alpha
# (before STAFF_SECTION). STAFF_SECTION is always forced dead-last by
# _ordered_categories, so it is not listed here.
CATEGORY_ORDER: tuple[str, ...] = (
    "Orientations",
    "Guilds",
    "Events",
    "Classes",
    "Teaching",
    "Voting",
    "Billing",
    "Membership",
    "Spaces & Equipment",
    "Announcements",
    "Security",
    "Meetings",
)

# The single display section that collects every staff / leadership / admin event
# (approval requests + admin-only alerts) instead of scattering them through the
# member-facing categories. Rendered dead-last and shown ONLY to eligible viewers
# (see _is_staff_or_leadership); a plain member never sees it. When an admin/officer
# previews the page as a Member or Guest, the section is omitted entirely
# (include_staff_section=False).
STAFF_SECTION = "Staff & leadership"

# Recipients that target staff, leadership, or admins rather than an individual
# member. Every event routed to one of these is grouped under STAFF_SECTION and
# gated behind _is_staff_or_leadership. Member-facing recipients (REGISTRANT,
# INSTRUCTOR, the SINGLE_USER approval-*outcome* notices the proposer receives,
# broadcast audiences, …) are deliberately absent — those stay in their own
# category and remain visible to everyone.
STAFF_RECIPIENTS: frozenset[Recipients] = frozenset(
    {
        Recipients.FOG_ADMINS,
        Recipients.GUILD_LEADERSHIP,
        Recipients.GUILD_LEADERSHIP_OR_ADMINS,
        Recipients.GUILD_LEAD,
        Recipients.ALL_GUILD_LEADS,
        Recipients.GUILD_ORIENTERS,
        Recipients.CLASS_APPROVERS,
        Recipients.GUILD_LEADERSHIP_OR_CLASS_APPROVERS,
        Recipients.SPACE_APPROVERS,
        Recipients.DISCOUNT_APPROVERS,
        Recipients.EVENTS_APPROVERS,
        Recipients.GUILD_LEADERSHIP_OR_EVENTS_APPROVERS,
        Recipients.BILLING_APPROVERS,
        Recipients.EQUIPMENT_MANAGERS,
    }
)


@dataclass(frozen=True)
class Cell:
    """One (event, channel) checkbox in the matrix.

    ``name`` is the form field name (``pref__<event_key>__<channel>``) the POST reads.
    ``enabled`` is the current checked state; ``forced`` renders it locked-on
    (always-on, the user can't change it); ``present`` is True when the event declares
    this channel at all (an absent channel renders as an empty cell). ``available`` is
    False when the user can't use this channel yet (the Discord DM channel before they
    link Discord) — the box renders disabled with ``hint`` as its tooltip, distinct
    from the locked-on ``forced`` state.
    """

    name: str
    channel: Channel
    enabled: bool
    forced: bool
    present: bool
    available: bool = True
    hint: str = ""
    badge: str = ""


@dataclass(frozen=True)
class Row:
    """One event row in the matrix: its label/description + a cell per user channel.

    ``badge`` is a short note explaining a non-obvious reason the user receives this
    event — set when they're a default recipient via an admin capability (e.g. "You get
    this as a CMS Administrator") rather than by a plain opt-in.
    """

    event_key: str
    label: str
    description: str
    cells: list[Cell]
    badge: str = ""


def field_name(event_key: str, channel: Channel) -> str:
    """The POST field name for an (event, channel) checkbox."""
    return f"pref__{event_key}__{channel.value}"


def _section_for(event: EventType) -> str:
    """The settings-page section an event renders under.

    Staff/leadership/admin events collapse into the single STAFF_SECTION; every
    other event keeps its own member-facing ``category``. This is display grouping
    only — an event's ``category`` (which also drives the email ``X-Category``
    header) is left untouched.
    """
    return STAFF_SECTION if event.recipient in STAFF_RECIPIENTS else event.category


@dataclass(frozen=True)
class _StaffProfile:
    """A viewer's staff / leadership standing, computed once per settings page.

    Drives per-row visibility in the STAFF_SECTION: a capability row shows ONLY to a holder
    of that capability; a role-scoped row shows ONLY to that role. So an admin who does not
    hold (say) the Discount capability neither sees nor receives discount-code alerts, and
    what the page shows always equals what the send path delivers.
    """

    is_admin: bool
    is_active: bool
    is_officer: bool
    leads_guild: bool
    staffs_guild: bool
    is_orienter: bool
    manages_equipment: bool
    capabilities: frozenset[str]

    @property
    def is_leadership(self) -> bool:
        return self.leads_guild or self.staffs_guild


def _staff_profile(user: User) -> _StaffProfile:
    """Compute ``user``'s staff/leadership standing in a handful of bounded queries.

    A user with no linked member is a plain member on every axis (an all-``False`` profile),
    so none of the staff rows are eligible.
    """
    from membership.models import GuildStaffMembership, Member

    member = Member.objects.filter(user=user).only("id", "fog_role", "status").first()
    if member is None:
        return _StaffProfile(False, False, False, False, False, False, False, frozenset())
    staff_roles = set(member.guild_staff_roles.values_list("role", flat=True))
    return _StaffProfile(
        is_admin=member.fog_role == Member.FogRole.ADMIN,
        is_active=member.status == Member.Status.ACTIVE,
        is_officer=member.fog_role == Member.FogRole.GUILD_OFFICER,
        leads_guild=member.led_guilds.exists(),
        staffs_guild=bool(staff_roles),
        is_orienter=GuildStaffMembership.Role.ORIENTER in staff_roles,
        manages_equipment=member.equipment_staff_memberships.exists(),
        capabilities=frozenset(member.admin_capabilities.values_list("capability", flat=True)),
    )


def _eligible_for(recipient: Recipients, profile: _StaffProfile) -> bool:
    """Whether ``profile`` should see (and receive) a staff row routed to ``recipient``.

    Capability rows show only to holders of that capability; role rows only to that role.
    A composite ``GUILD_LEADERSHIP_OR_*`` row shows to guild leadership OR the capability
    holder (its two send-time audiences). Mirrors the resolvers so page == delivery.
    """
    from membership.models import AdminCapability

    cap = AdminCapability.Capability
    caps = profile.capabilities
    lead = profile.is_leadership
    checks: dict[Recipients, bool] = {
        Recipients.FOG_ADMINS: profile.is_admin,
        Recipients.GUILD_LEADERSHIP: lead,
        Recipients.GUILD_LEADERSHIP_OR_ADMINS: lead or profile.is_admin,
        Recipients.GUILD_LEAD: profile.leads_guild,
        # all_guild_leads resolves against Member.objects.active(), so a FORMER/SUSPENDED
        # lead/officer would see this row but never receive the mail — gate on is_active too.
        Recipients.ALL_GUILD_LEADS: (lead or profile.is_officer) and profile.is_active,
        Recipients.GUILD_ORIENTERS: profile.leads_guild or profile.is_orienter,
        Recipients.CLASS_APPROVERS: cap.CLASS_APPROVER in caps,
        Recipients.GUILD_LEADERSHIP_OR_CLASS_APPROVERS: lead or cap.CLASS_APPROVER in caps,
        Recipients.SPACE_APPROVERS: cap.SPACE_APPROVER in caps,
        Recipients.DISCOUNT_APPROVERS: cap.DISCOUNT_APPROVER in caps,
        Recipients.EVENTS_APPROVERS: cap.EVENTS_APPROVER in caps,
        Recipients.GUILD_LEADERSHIP_OR_EVENTS_APPROVERS: lead or cap.EVENTS_APPROVER in caps,
        Recipients.BILLING_APPROVERS: cap.BILLING_APPROVER in caps,
        # The three equipment-manage tiers, mirroring the equipment_managers resolver:
        # per-equipment staff row, owning-guild leadership, or the EQUIPMENT capability.
        Recipients.EQUIPMENT_MANAGERS: lead or profile.manages_equipment or cap.EQUIPMENT in caps,
    }
    return checks[recipient]


def _visible_events(user: User, *, include_staff_section: bool = True) -> list[EventType]:
    """Events whose preference row should be shown to ``user``.

    Every registered *member-facing* event that declares a user channel is shown — the page
    is the full catalogue of how the app can reach you. (Audience scoping is the resolver's
    job at send time; the page lists every member event so a user always sees, e.g., the
    teaching events even before they teach.)

    A STAFF_SECTION event (approval request or admin alert) is shown only to a viewer who is
    actually eligible to receive it — a holder of its capability or a member of its role
    (:func:`_eligible_for`). A plain member sees none of them; an admin sees only the duties
    they hold plus the admin/leadership alerts.

    When ``include_staff_section`` is ``False`` (an admin/officer previewing the page as a
    Member or Guest), every staff/leadership/admin event is dropped up front — before the
    eligibility check — so the Staff & Leadership section is omitted entirely.
    """
    profile = _staff_profile(user)
    out: list[EventType] = []
    for event in all_events():
        if not any(event.has_channel(channel) for channel in USER_CHANNELS):
            continue
        if event.recipient in STAFF_RECIPIENTS:
            if not include_staff_section:
                continue
            if not _eligible_for(event.recipient, profile):
                continue
        out.append(event)
    return out


def _ordered_categories(categories: set[str]) -> list[str]:
    """Order the rendered sections: CATEGORY_ORDER first, unknown extras alpha, staff last.

    STAFF_SECTION is forced dead-last (its comment finally becomes true): a member-facing
    category always sorts ahead of the Staff & Leadership section, even a brand-new one not
    yet listed in CATEGORY_ORDER.
    """
    tail = [STAFF_SECTION] if STAFF_SECTION in categories else []
    ranked = [c for c in CATEGORY_ORDER if c in categories]
    rest = sorted(c for c in categories if c not in CATEGORY_ORDER and c != STAFF_SECTION)
    return ranked + rest + tail


def _member_discord_linked(user: User) -> bool:
    """Whether ``user``'s member has a verified Discord account linked for DMs.

    ``Member`` is imported lazily to avoid a model-layer import at module load. A user
    with no linked member is treated as not linked (the column renders disabled).
    """
    from membership.models import Member

    member = Member.objects.filter(user=user).only("discord_user_id").first()
    return bool(member and member.discord_user_id)


def channel_availability(user: User, channel: Channel, *, discord_linked: bool) -> tuple[bool, str]:
    """Whether ``user`` can use ``channel`` right now, and a hint to show when not.

    Every channel is available except the per-member Discord DM channel, which needs the
    member to have linked their Discord account first; until then its cells render
    disabled with a hint. ``discord_linked`` is passed in (computed once per page) so the
    matrix doesn't re-query the member for every cell.
    """
    if channel is Channel.DISCORD_DM and not discord_linked:
        return False, _DISCORD_LINK_HINT
    return True, ""


def visible_channels(user: User, *, include_staff_section: bool = True) -> list[Channel]:
    """The user channels at least one visible event actually offers, in display order.

    A channel no event declares (today: the unbuilt Scheduled-email and Digest shells)
    would render as an entire column of empty '—' cells — dead, confusing UI. We drop
    such columns until something uses them; the column reappears automatically the day
    an event starts declaring that channel.

    ``include_staff_section`` is forwarded to :func:`_visible_events` so a member-view
    preview doesn't render a dead column for a channel only staff events offer.
    """
    events = _visible_events(user, include_staff_section=include_staff_section)
    return [channel for channel in USER_CHANNELS if any(event.channel(channel) is not None for event in events)]


# Each capability-scoped recipient maps to the capability whose holders receive the
# event by default; a member holding it gets a "You get this as a …" badge on the row.
_CAPABILITY_BY_RECIPIENT: dict[Recipients, str] = {
    Recipients.CLASS_APPROVERS: "class_approver",
    Recipients.GUILD_LEADERSHIP_OR_CLASS_APPROVERS: "class_approver",
    Recipients.SPACE_APPROVERS: "space_approver",
    Recipients.DISCOUNT_APPROVERS: "discount_approver",
    Recipients.EVENTS_APPROVERS: "events_approver",
    Recipients.GUILD_LEADERSHIP_OR_EVENTS_APPROVERS: "events_approver",
    Recipients.BILLING_APPROVERS: "billing_approver",
    Recipients.EQUIPMENT_MANAGERS: "equipment",
}


def _capability_badges(user: User, events: list[EventType]) -> dict[str, str]:
    """Map each visible event the user receives via a held capability to its row badge.

    One bounded query for the member's capabilities (not a resolver call per row): a row
    is badged only when the event routes to a capability the member actually holds.
    """
    from membership.models import AdminCapability, Member

    member = Member.objects.filter(user=user).only("id").first()
    if member is None:
        return {}
    held = set(member.admin_capabilities.values_list("capability", flat=True))
    if not held:
        return {}
    labels = {choice.value: choice.label for choice in AdminCapability.Capability}
    badges: dict[str, str] = {}
    for event in events:
        capability = _CAPABILITY_BY_RECIPIENT.get(event.recipient)
        if capability is not None and capability in held:
            badges[event.key] = f"You get this as a {labels[capability]}"
    return badges


def build_matrix(user: User, *, include_staff_section: bool = True) -> list[tuple[str, list[Row]]]:
    """Assemble the matrix for ``user`` — a list of ``(category, [Row, ...])``.

    For each visible event, one :class:`Row` with a :class:`Cell` per user channel:
    forced cells locked-on, opt-out-able cells reflecting the saved preference (or the
    event's channel default). A row the user receives via an admin capability carries a
    badge. Sections are returned in :data:`CATEGORY_ORDER`, with every staff/leadership
    event collected into a single :data:`STAFF_SECTION` rendered **last** (and only for
    eligible viewers — see :func:`_visible_events`).

    When ``include_staff_section`` is ``False`` (an admin/officer previewing as Member or
    Guest), the Staff & Leadership section and any staff-only channel columns are omitted.
    """
    events = _visible_events(user, include_staff_section=include_staff_section)
    channels = visible_channels(user, include_staff_section=include_staff_section)
    # Compute per-channel availability once (the Discord-linked lookup is a single
    # query) rather than re-deriving it for every cell.
    discord_linked = _member_discord_linked(user)
    availability = {channel: channel_availability(user, channel, discord_linked=discord_linked) for channel in channels}
    capability_badges = _capability_badges(user, events)
    by_category: dict[str, list[Row]] = {}
    for event in events:
        cells: list[Cell] = []
        for channel in channels:
            spec = event.channel(channel)
            if spec is None:
                cells.append(Cell(name="", channel=channel, enabled=False, forced=False, present=False))
                continue
            forced = spec.is_forced
            # IN_APP is always-on; show it locked-on like a forced channel.
            locked = forced or channel is Channel.IN_APP
            enabled = preferences.wants(user, event.key, channel)
            available, hint = availability[channel]
            cells.append(
                Cell(
                    name=field_name(event.key, channel),
                    channel=channel,
                    enabled=enabled,
                    forced=locked,
                    present=True,
                    available=available,
                    hint=hint,
                )
            )
        row = Row(
            event_key=event.key,
            label=event.label,
            description=event.description,
            cells=cells,
            badge=capability_badges.get(event.key, ""),
        )
        by_category.setdefault(_section_for(event), []).append(row)
    return [(category, by_category[category]) for category in _ordered_categories(set(by_category))]


def save_matrix(user: User, posted: dict[str, str], *, include_staff_section: bool = True) -> None:
    """Persist the matrix POST for ``user``.

    For each visible (event, opt-out-able channel) cell, upsert a
    :class:`core.models.NotificationPreference` row with ``enabled`` reflecting the
    checkbox. FORCED and IN_APP cells are skipped — they are always-on and have no
    user-controlled state to store, so we never write a row that pretends otherwise.
    A checked box is ``enabled=True``; an absent box (HTML omits unchecked checkboxes)
    is ``enabled=False``. A channel the user can't use yet (the Discord DM channel
    before they link Discord) is skipped — its box renders disabled, so the browser
    omits it, and writing ``enabled=False`` would silently wipe a preference they set
    while linked. Skipping preserves their choice across an unlink/relink.

    ``include_staff_section`` **must** match the flag the GET render used. When it is
    ``False`` (an admin/officer saving while previewing as Member/Guest), the staff-event
    rows were never rendered, so their checkboxes are absent from the POST — iterating them
    here would write ``enabled=False`` and silently wipe the admin's own staff prefs. The
    flag drops those events from the iteration entirely, the same protective pattern as the
    Discord-unlinked channel skip below.
    """
    discord_linked = _member_discord_linked(user)
    availability = {
        channel: channel_availability(user, channel, discord_linked=discord_linked) for channel in USER_CHANNELS
    }
    for event in _visible_events(user, include_staff_section=include_staff_section):
        for channel in USER_CHANNELS:
            spec = event.channel(channel)
            if spec is None or spec.is_forced or channel is Channel.IN_APP:
                continue
            if not availability[channel][0]:
                continue
            NotificationPreference.objects.update_or_create(
                user=user,
                event_key=event.key,
                channel=channel.value,
                defaults={"enabled": posted.get(field_name(event.key, channel)) == "on"},
            )
