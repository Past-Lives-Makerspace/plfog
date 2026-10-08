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

The page is a list of :class:`MatrixSection`: **Admin / Permissions** first (every row a
viewer gets because of a role or an admin permission, grouped by that permission into
:class:`MatrixBlock` s), then the member topics in :data:`CATEGORY_ORDER`. A forced email
never moves a row; it only locks that row's Email cell (the padlock).

This is the model/service layer for the page (CLAUDE.md: logic out of views). The
view calls :func:`build_matrix` for the GET context and :func:`save_matrix` for the
POST.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from django.utils.text import slugify

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

# Stable display order of the member topics; any category not listed falls to the end,
# alphabetically. ADMIN_SECTION is not a category and always renders first, so it is not
# listed here.
CATEGORY_ORDER: tuple[str, ...] = (
    "Orientations",
    "Guilds",
    "Events",
    "Classes",
    "Teaching",
    "Voting",
    # wiki.page_verified is member-facing and lands here. wiki.page_reported is staff-only
    # and sits in ADMIN_SECTION instead, so this category is never empty for the people
    # it exists for.
    "Wiki",
    "Billing",
    "Membership",
    "Spaces & Equipment",
    "Announcements",
    "Security",
    "Meetings",
    "Your requests",
)

# The one section that collects every event a viewer gets because of a role or an admin
# permission (approval requests + admin-only alerts) instead of scattering them through the
# member topics. Rendered FIRST, and shown only to a viewer with at least one row in it
# (_eligible_for); a plain member never sees it. When an admin/officer previews the page as
# a Member or Guest, the section is omitted entirely (include_staff_section=False).
ADMIN_SECTION = "Admin / Permissions"

# Recipients that target staff, leadership, or admins rather than an individual
# member. Every event routed to one of these lands in ADMIN_SECTION and is gated
# behind _eligible_for. Member-facing recipients (REGISTRANT,
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
        Recipients.GUILD_ORIENTERS_OR_EQUIPMENT_MANAGERS,
        Recipients.CLASS_APPROVERS,
        Recipients.GUILD_LEADERSHIP_OR_CLASS_APPROVERS,
        Recipients.SPACE_APPROVERS,
        Recipients.DISCOUNT_APPROVERS,
        Recipients.EVENTS_APPROVERS,
        Recipients.GUILD_LEADERSHIP_OR_EVENTS_APPROVERS,
        Recipients.BILLING_APPROVERS,
        Recipients.WEBMASTERS,
        Recipients.REFUND_AUTHORITY,
        Recipients.EQUIPMENT_MANAGERS,
        # A report routes to a guild's leadership or the admins — a staff row.
        # WIKI_PAGE_CONTRIBUTORS is deliberately absent: it is the member-facing half of
        # the pair, and hiding it in the staff section would hide it from the very people
        # it exists for.
        Recipients.WIKI_SCOPE_LEADERSHIP,
    }
)


class PermissionGroup(str, Enum):
    """The groups inside Admin / Permissions: the permission that brings a row, in page order.

    The eight capability groups share their value with :class:`membership.models.AdminCapability`
    ``Capability`` and read their heading from its label, so the settings page and the
    Permissions tab use the same words. Which of these a viewer holds is
    :meth:`_StaffProfile.holds`; which a row can reach them through is
    :data:`_GROUPS_BY_RECIPIENT`.
    """

    ADMIN = "admin"
    CLASS_APPROVER = "class_approver"
    BILLING_APPROVER = "billing_approver"
    REFUNDS = "refunds"
    EVENTS_APPROVER = "events_approver"
    SPACE_APPROVER = "space_approver"
    DISCOUNT_APPROVER = "discount_approver"
    EQUIPMENT = "equipment"
    WEBMASTER = "webmaster"
    GUILD_LEADERSHIP = "guild_leadership"
    EQUIPMENT_MANAGER = "equipment_manager"


# The groups each staff recipient can reach a viewer through, in order. A row lands under
# the FIRST of these the viewer holds, so a notice that reaches them through two roles
# shows once. Mirrors the resolvers the way _eligible_for does: every viewer _eligible_for
# admits holds at least one group here (a spec walks every recipient), and a miss raises.
_GROUPS_BY_RECIPIENT: dict[Recipients, tuple[PermissionGroup, ...]] = {
    Recipients.FOG_ADMINS: (PermissionGroup.ADMIN,),
    Recipients.REFUND_AUTHORITY: (PermissionGroup.ADMIN, PermissionGroup.REFUNDS),
    Recipients.GUILD_LEADERSHIP_OR_ADMINS: (PermissionGroup.ADMIN, PermissionGroup.GUILD_LEADERSHIP),
    Recipients.WIKI_SCOPE_LEADERSHIP: (PermissionGroup.ADMIN, PermissionGroup.GUILD_LEADERSHIP),
    Recipients.CLASS_APPROVERS: (PermissionGroup.CLASS_APPROVER,),
    Recipients.GUILD_LEADERSHIP_OR_CLASS_APPROVERS: (PermissionGroup.CLASS_APPROVER, PermissionGroup.GUILD_LEADERSHIP),
    Recipients.BILLING_APPROVERS: (PermissionGroup.BILLING_APPROVER,),
    Recipients.WEBMASTERS: (PermissionGroup.WEBMASTER,),
    Recipients.EVENTS_APPROVERS: (PermissionGroup.EVENTS_APPROVER,),
    Recipients.GUILD_LEADERSHIP_OR_EVENTS_APPROVERS: (
        PermissionGroup.EVENTS_APPROVER,
        PermissionGroup.GUILD_LEADERSHIP,
    ),
    Recipients.SPACE_APPROVERS: (PermissionGroup.SPACE_APPROVER,),
    Recipients.DISCOUNT_APPROVERS: (PermissionGroup.DISCOUNT_APPROVER,),
    Recipients.EQUIPMENT_MANAGERS: (
        PermissionGroup.EQUIPMENT,
        PermissionGroup.GUILD_LEADERSHIP,
        PermissionGroup.EQUIPMENT_MANAGER,
    ),
    Recipients.GUILD_ORIENTERS_OR_EQUIPMENT_MANAGERS: (
        PermissionGroup.EQUIPMENT,
        PermissionGroup.GUILD_LEADERSHIP,
        PermissionGroup.EQUIPMENT_MANAGER,
    ),
    Recipients.GUILD_LEADERSHIP: (PermissionGroup.GUILD_LEADERSHIP,),
    Recipients.GUILD_LEAD: (PermissionGroup.GUILD_LEADERSHIP,),
    Recipients.GUILD_ORIENTERS: (PermissionGroup.GUILD_LEADERSHIP,),
    Recipients.ALL_GUILD_LEADS: (PermissionGroup.GUILD_LEADERSHIP,),
}


class NoPermissionGroupError(Exception):
    """A staff row the viewer may see matched none of its recipient's permission groups.

    A programming error: :func:`_eligible_for` and :data:`_GROUPS_BY_RECIPIENT` have drifted
    apart. Raised rather than dropping the row, because a dropped row is a notice the viewer
    still receives but can no longer find or switch off.
    """


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

    @property
    def is_editable(self) -> bool:
        """Whether the viewer can flip this box: present, not locked, and usable now.

        A locked cell (a forced email, or the always-on bell) and an unavailable one (Discord
        before it is linked) render disabled, so no bulk control may count or touch them.
        """
        return self.present and not self.forced and self.available


@dataclass(frozen=True)
class Row:
    """One event row in the matrix: its label/description + a cell per user channel."""

    event_key: str
    label: str
    description: str
    cells: list[Cell]


def _in_display_order(channels: set[Channel]) -> list[Channel]:
    return [channel for channel in USER_CHANNELS if channel in channels]


@dataclass(frozen=True)
class MatrixBlock:
    """One run of rows under an optional heading, with the channels its rows let you flip.

    A member topic is one untitled block (``heading == ""``). Inside Admin / Permissions each
    :class:`PermissionGroup` the viewer has rows under is one block, headed with the group's
    name. ``editable_channels`` is every channel at least one of the block's cells lets the
    viewer change (:attr:`Cell.is_editable`), in column order: it drives the block's bulk
    controls, so a channel none of its rows offers gets no control.

    Not :class:`RowGroup`, which folds sibling events into one row; a block holds rows.
    """

    heading: str
    rows: list[Row]
    editable_channels: list[Channel]

    @classmethod
    def of(cls, heading: str, rows: list[Row]) -> MatrixBlock:
        """Build a block, deriving its editable channels from its rows' cells."""
        editable = {cell.channel for row in rows for cell in row.cells if cell.is_editable}
        return cls(heading=heading, rows=rows, editable_channels=_in_display_order(editable))


@dataclass(frozen=True)
class MatrixSection:
    """One section of the page: Admin / Permissions, or a member topic.

    ``slug`` is the jump-chip anchor. ``editable_channels`` is the union of its blocks', in
    column order, and drives the section's bulk controls. ``is_admin`` marks Admin /
    Permissions, the one section with a note on top and headed blocks inside.
    """

    title: str
    slug: str
    blocks: list[MatrixBlock]
    editable_channels: list[Channel]
    is_admin: bool

    @classmethod
    def of(cls, title: str, blocks: list[MatrixBlock], *, is_admin: bool) -> MatrixSection:
        """Build a section, deriving its slug and editable channels."""
        editable = {channel for block in blocks for channel in block.editable_channels}
        return cls(
            title=title,
            slug=slugify(title),
            blocks=blocks,
            editable_channels=_in_display_order(editable),
            is_admin=is_admin,
        )


def page_editable_channels(sections: list[MatrixSection]) -> list[Channel]:
    """The channels the page-wide bulk controls offer: the union of every section's."""
    return _in_display_order({channel for section in sections for channel in section.editable_channels})


@dataclass(frozen=True)
class RowGroup:
    """Sibling events a member thinks of as ONE setting, rendered as one matrix row.

    "What happened to my event proposal" is one question to a member and three events to
    the app (approved / changes requested / declined). The page asks it once: the group
    renders a single :class:`Row` at the position of its *first* member, in that member's
    section, its checkboxes are named after ``group_id``, and :func:`save_matrix` fans the
    one posted value back out to every key in ``event_keys``.

    This is presentation only, and deliberately so. The event registry keeps its separate
    events, every send path still reads its own key, the Emails tab and the admin copy
    catalogue still read ``EventType.label``/``description``, and
    :class:`core.models.NotificationPreference` keeps exactly one row per
    ``(user, event key, channel)`` — grouping changes no row count in that table and
    ``preferences.wants()`` never learns groups exist.

    What it costs: from *this page* the siblings now move in lockstep. A member can no
    longer ask for the approval mail but not the decline mail. That was accepted as the
    price of a page a member can actually read.

    ``event_keys`` is always an explicit tuple and never a key prefix. ``event.submitted``
    and ``guild_announcement.submitted`` share the prefix of the outcome notices below but
    are approval *requests* routed to staff through a different emit helper; a prefix
    match would swallow them.
    """

    group_id: str
    label: str
    description: str
    event_keys: tuple[str, ...]


# The sibling families this page collapses into one row each. ``group_id`` lives in its
# own literal ``group.`` namespace, which no registered event key uses (a spec pins that),
# so a group id can never collide with an event key.
#
# ``label`` and ``description`` are settings-page copy and reach no other surface: the
# Emails tab and the admin catalogue both read the registry's own ``description``.
ROW_GROUPS: tuple[RowGroup, ...] = (
    RowGroup(
        group_id="group.event_decision",
        label="Updates to your event",
        description="Your proposed event was approved, sent back for changes, or declined.",
        event_keys=("event.approved", "event.changes_requested", "event.declined"),
    ),
    RowGroup(
        group_id="group.class_decision",
        label="Updates to your class",
        description="A reviewer approved your class or asked you for changes.",
        event_keys=("instructor_class_approved", "instructor_changes_requested"),
    ),
    RowGroup(
        group_id="group.announcement_decision",
        label="Updates to your announcement",
        description="Your proposed announcement was approved, sent back for changes, or declined.",
        event_keys=(
            "guild_announcement.approved",
            "guild_announcement.changes_requested",
            "guild_announcement.declined",
        ),
    ),
    RowGroup(
        group_id="group.space_request_decision",
        label="Updates to your space request",
        description="Your studio or cubby request was approved or declined.",
        event_keys=("space.request_approved", "space.request_declined"),
    ),
    RowGroup(
        group_id="group.instructor_application_decision",
        label="Updates to your request to host a class",
        description="Your request to host a class was approved or declined.",
        event_keys=("instructor_application_approved", "instructor_application_declined"),
    ),
    RowGroup(
        group_id="group.waitlist_promotion",
        label="Added from the waitlist",
        description=(
            "Staff moved you off the waitlist into a class. If the class is paid, this carries your payment link."
        ),
        event_keys=("waitlist_promoted", "waitlist_promoted_pay"),
    ),
)

# Reverse index: the group a member key belongs to. Absent for the vast majority of
# events, which still render one row each.
_GROUP_BY_EVENT_KEY: dict[str, RowGroup] = {key: group for group in ROW_GROUPS for key in group.event_keys}


def field_name(event_key: str, channel: Channel) -> str:
    """The POST field name for an (event, channel) checkbox."""
    return f"pref__{event_key}__{channel.value}"


def _post_key_for(event: EventType) -> str:
    """The key ``event``'s checkboxes are named after — its group's id, or its own key.

    This one helper is the whole group fan-out. :func:`build_matrix` names the rendered
    cells with it and :func:`save_matrix` reads the posted value with it, so every member
    of a group reads the SAME posted field: one checkbox writes one
    :class:`core.models.NotificationPreference` row per member key, with no separate
    fan-out loop to keep in step.
    """
    group = _GROUP_BY_EVENT_KEY.get(event.key)
    return event.key if group is None else group.group_id


def _section_for(event: EventType) -> str:
    """The settings-page section an event renders under.

    One rule: an event routed to a staff recipient (:data:`STAFF_RECIPIENTS`) goes to
    :data:`ADMIN_SECTION`; every other event keeps its own member-facing ``category``. A
    forced email moves nothing: ``refund_failed`` forces its email and routes to the
    Billing Administrators, so it sits in Admin / Permissions with a padlocked Email cell,
    and ``class_cancelled`` forces its email and stays in Classes.

    This is display grouping only — an event's ``category`` (which also drives the email
    ``X-Category`` header) is left untouched.
    """
    if event.recipient in STAFF_RECIPIENTS:
        return ADMIN_SECTION
    return event.category


@dataclass(frozen=True)
class _StaffProfile:
    """A viewer's staff / leadership standing, computed once per settings page.

    Drives per-row visibility in ADMIN_SECTION (:func:`_eligible_for`): a capability row
    shows ONLY to a holder of that capability; a role-scoped row shows ONLY to that role. So
    an admin who does not hold (say) the Discount capability neither sees nor receives
    discount-code alerts, and what the page shows always equals what the send path
    delivers. It also says which :class:`PermissionGroup` a visible row files under
    (:meth:`holds`).
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

    def holds(self, group: PermissionGroup) -> bool:
        """Whether this viewer holds ``group``, the permission a row can reach them through.

        Guild leadership covers leading a guild, holding any guild staff role, or being a
        guild officer (the ALL_GUILD_LEADS audience); Equipment manager is a per-tool staff
        row. The capability groups are the matching :class:`AdminCapability` grant.
        """
        from membership.models import AdminCapability

        capability = AdminCapability.Capability
        caps = self.capabilities
        held: dict[PermissionGroup, bool] = {
            PermissionGroup.ADMIN: self.is_admin,
            PermissionGroup.CLASS_APPROVER: capability.CLASS_APPROVER in caps,
            PermissionGroup.BILLING_APPROVER: capability.BILLING_APPROVER in caps,
            PermissionGroup.REFUNDS: capability.REFUNDS in caps,
            PermissionGroup.EVENTS_APPROVER: capability.EVENTS_APPROVER in caps,
            PermissionGroup.SPACE_APPROVER: capability.SPACE_APPROVER in caps,
            PermissionGroup.DISCOUNT_APPROVER: capability.DISCOUNT_APPROVER in caps,
            PermissionGroup.EQUIPMENT: capability.EQUIPMENT in caps,
            PermissionGroup.WEBMASTER: capability.WEBMASTER in caps,
            PermissionGroup.GUILD_LEADERSHIP: self.is_leadership or self.is_officer,
            PermissionGroup.EQUIPMENT_MANAGER: self.manages_equipment,
        }
        return held[group]


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
        # Every audience of the composed orientation_requested resolver: a guild's leadership
        # (whole team for a shared slot, orienter plus lead for a personal one) or the
        # equipment's managers.
        Recipients.GUILD_ORIENTERS_OR_EQUIPMENT_MANAGERS: lead or profile.manages_equipment or cap.EQUIPMENT in caps,
        Recipients.CLASS_APPROVERS: cap.CLASS_APPROVER in caps,
        Recipients.GUILD_LEADERSHIP_OR_CLASS_APPROVERS: lead or cap.CLASS_APPROVER in caps,
        Recipients.SPACE_APPROVERS: cap.SPACE_APPROVER in caps,
        Recipients.DISCOUNT_APPROVERS: cap.DISCOUNT_APPROVER in caps,
        Recipients.EVENTS_APPROVERS: cap.EVENTS_APPROVER in caps,
        Recipients.GUILD_LEADERSHIP_OR_EVENTS_APPROVERS: lead or cap.EVENTS_APPROVER in caps,
        Recipients.BILLING_APPROVERS: cap.BILLING_APPROVER in caps,
        Recipients.WEBMASTERS: cap.WEBMASTER in caps,
        # Everyone who may refund: the Admin role OR the REFUNDS capability (a union, unlike
        # the other capability audiences), mirroring the refund_authority resolver.
        Recipients.REFUND_AUTHORITY: profile.is_admin or cap.REFUNDS in caps,
        # The three equipment-manage tiers, mirroring the equipment_managers resolver:
        # per-equipment staff row, owning-guild leadership, or the EQUIPMENT capability.
        Recipients.EQUIPMENT_MANAGERS: lead or profile.manages_equipment or cap.EQUIPMENT in caps,
        # Either audience of the composed wiki_scope_leadership resolver: a guild's
        # leadership for a scoped page, the admins for a space-wide one.
        Recipients.WIKI_SCOPE_LEADERSHIP: lead or profile.is_admin,
    }
    return checks[recipient]


def _group_for(recipient: Recipients, profile: _StaffProfile) -> PermissionGroup:
    """The Admin / Permissions group a visible staff row files under for this viewer.

    The first of the recipient's candidate groups (:data:`_GROUPS_BY_RECIPIENT`) the viewer
    holds, so a notice that reaches them through two roles shows once, under the earlier.

    Raises:
        NoPermissionGroupError: the viewer holds none of them, which means _eligible_for
            admitted a row the group table cannot place.
    """
    for group in _GROUPS_BY_RECIPIENT[recipient]:
        if profile.holds(group):
            return group
    raise NoPermissionGroupError(f"No permission group places a {recipient.value} row for this viewer")


def _group_headings() -> dict[PermissionGroup, str]:
    """Each group's heading; the capability groups use the Permissions tab's own labels."""
    from membership.models import AdminCapability

    capability = AdminCapability.Capability
    return {
        PermissionGroup.ADMIN: "Admin",
        PermissionGroup.CLASS_APPROVER: capability.CLASS_APPROVER.label,
        PermissionGroup.BILLING_APPROVER: capability.BILLING_APPROVER.label,
        PermissionGroup.REFUNDS: capability.REFUNDS.label,
        PermissionGroup.EVENTS_APPROVER: capability.EVENTS_APPROVER.label,
        PermissionGroup.SPACE_APPROVER: capability.SPACE_APPROVER.label,
        PermissionGroup.DISCOUNT_APPROVER: capability.DISCOUNT_APPROVER.label,
        PermissionGroup.EQUIPMENT: capability.EQUIPMENT.label,
        PermissionGroup.WEBMASTER: capability.WEBMASTER.label,
        PermissionGroup.GUILD_LEADERSHIP: "Guild leadership",
        PermissionGroup.EQUIPMENT_MANAGER: "Equipment manager",
    }


def _visible_events(profile: _StaffProfile, *, include_staff_section: bool = True) -> list[EventType]:
    """Events whose preference row should be shown to the viewer ``profile`` describes.

    Every registered *member-facing* event that declares a user channel is shown — the page
    is the full catalogue of how the app can reach you. (Audience scoping is the resolver's
    job at send time; the page lists every member event so a user always sees, e.g., the
    teaching events even before they teach.)

    An Admin / Permissions event (approval request or admin alert) is shown only to a viewer
    who is actually eligible to receive it — a holder of its capability or a member of its
    role (:func:`_eligible_for`). A plain member sees none of them; an admin sees only the
    duties they hold plus the admin/leadership alerts.

    When ``include_staff_section`` is ``False`` (an admin/officer previewing the page as a
    Member or Guest), every staff/leadership/admin event is dropped up front — before the
    eligibility check — so Admin / Permissions is omitted entirely.
    """
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
    """Order the member topics: CATEGORY_ORDER first, then unknown categories alphabetically.

    Admin / Permissions is not a category and is placed first by :func:`build_matrix`
    itself, so no category name can outrank it.
    """
    ranked = [c for c in CATEGORY_ORDER if c in categories]
    rest = sorted(c for c in categories if c not in CATEGORY_ORDER)
    return ranked + rest


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
    events = _visible_events(_staff_profile(user), include_staff_section=include_staff_section)
    return _channels_offered_by(events)


def _channels_offered_by(events: list[EventType]) -> list[Channel]:
    return [channel for channel in USER_CHANNELS if any(event.channel(channel) is not None for event in events)]


def build_matrix(user: User, *, include_staff_section: bool = True) -> list[MatrixSection]:
    """Assemble the settings page for ``user`` as a list of :class:`MatrixSection`.

    For each visible event, one :class:`Row` with a :class:`Cell` per user channel:
    forced cells locked-on, opt-out-able cells reflecting the saved preference (or the
    event's channel default). Sections come in page order: :data:`ADMIN_SECTION` first when
    the viewer has any row in it, its rows split into one :class:`MatrixBlock` per
    :class:`PermissionGroup` (group order, first held group wins, empty groups absent);
    then the member topics in :data:`CATEGORY_ORDER`, one untitled block each. Rows keep
    catalogue order inside a block.

    The members of a :class:`RowGroup` collapse into ONE row, emitted at the position of
    the group's first member and named after the group. Such a row's cell is on only when
    **every** member key is on: any opt-out wins, so the page never promises mail that one
    of the siblings would not send. A member holding a mix therefore sees the row off, and
    flattens the group on their next save — the approved rule, not a bug.

    When ``include_staff_section`` is ``False`` (an admin/officer previewing as Member or
    Guest), Admin / Permissions and any staff-only channel columns are omitted.

    Raises:
        NoPermissionGroupError: a visible staff row fits none of the viewer's groups.
    """
    profile = _staff_profile(user)
    events = _visible_events(profile, include_staff_section=include_staff_section)
    channels = _channels_offered_by(events)
    # Compute per-channel availability once (the Discord-linked lookup is a single
    # query) rather than re-deriving it for every cell.
    discord_linked = _member_discord_linked(user)
    availability = {channel: channel_availability(user, channel, discord_linked=discord_linked) for channel in channels}
    admin_rows: dict[PermissionGroup, list[Row]] = {}
    topic_rows: dict[str, list[Row]] = {}
    rendered: set[str] = set()
    for event in events:
        post_key = _post_key_for(event)
        if post_key in rendered:
            # A later sibling of a group whose row is already out. Plain event keys are
            # unique, so this only ever skips siblings — never a second real event.
            continue
        rendered.add(post_key)
        group = _GROUP_BY_EVENT_KEY.get(event.key)
        # The keys this row's state is read from: the whole family for a group, else the
        # event itself.
        pref_keys = (event.key,) if group is None else group.event_keys
        cells: list[Cell] = []
        for channel in channels:
            spec = event.channel(channel)
            if spec is None:
                cells.append(Cell(name="", channel=channel, enabled=False, forced=False, present=False))
                continue
            forced = spec.is_forced
            # IN_APP is always-on; show it locked-on like a forced channel.
            locked = forced or channel is Channel.IN_APP
            # Conservative read for a group: on only when every member key is on.
            enabled = all(preferences.wants(user, key, channel) for key in pref_keys)
            available, hint = availability[channel]
            cells.append(
                Cell(
                    name=field_name(post_key, channel),
                    channel=channel,
                    enabled=enabled,
                    forced=locked,
                    present=True,
                    available=available,
                    hint=hint,
                )
            )
        row = Row(
            event_key=post_key,
            label=event.label if group is None else group.label,
            description=event.description if group is None else group.description,
            cells=cells,
        )
        section = _section_for(event)
        if section == ADMIN_SECTION:
            admin_rows.setdefault(_group_for(event.recipient, profile), []).append(row)
        else:
            topic_rows.setdefault(section, []).append(row)
    sections: list[MatrixSection] = []
    if admin_rows:
        headings = _group_headings()
        blocks = [
            MatrixBlock.of(headings[group], admin_rows[group]) for group in PermissionGroup if group in admin_rows
        ]
        sections.append(MatrixSection.of(ADMIN_SECTION, blocks, is_admin=True))
    for category in _ordered_categories(set(topic_rows)):
        sections.append(MatrixSection.of(category, [MatrixBlock.of("", topic_rows[category])], is_admin=False))
    return sections


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

    A :class:`RowGroup` posts ONE checkbox per channel for the whole family, so every
    member key of the group reads that same field (:func:`_post_key_for`) and gets its own
    row written with the same ``enabled``. The iteration is still per event, so the group
    fan-out costs no extra bookkeeping and writes exactly the rows the ungrouped page
    wrote: nothing in :class:`core.models.NotificationPreference` ever carries a
    ``group.`` key.

    ``include_staff_section`` **must** match the flag the GET render used. When it is
    ``False`` (an admin/officer saving while previewing as Member/Guest), the staff-event
    rows were never rendered, so their checkboxes are absent from the POST — iterating them
    here would write ``enabled=False`` and silently wipe the admin's own staff prefs. The
    flag drops those events from the iteration entirely, the same protective pattern as the
    Discord-unlinked channel skip below.

    The same trap binds the page: every row this iterates must render its writable
    checkboxes, collapsed or not. A padlocked row (forced email) keeps live Push and Discord
    cells, so it renders in its topic like any other row rather than being hidden.
    """
    discord_linked = _member_discord_linked(user)
    availability = {
        channel: channel_availability(user, channel, discord_linked=discord_linked) for channel in USER_CHANNELS
    }
    for event in _visible_events(_staff_profile(user), include_staff_section=include_staff_section):
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
                defaults={"enabled": posted.get(field_name(_post_key_for(event), channel)) == "on"},
            )
