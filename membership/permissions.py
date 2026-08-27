"""Edit-permission helpers — the single source of truth for "who may edit what".

Guild-lead authority comes **solely** from the ``Guild.guild_lead`` foreign key.
No FOG role, Django group, or ``is_staff`` flag is required: the moment a Member
is set as a guild's lead, they can edit that guild and its classes. ``fog_role``
(admin / guild_officer) is a *separate*, cross-guild staff tier and keeps working
as before.

These request-level helpers honor ``view_as`` preview mode — an admin previewing
as a lower role sees exactly what that viewer would. For role-independent checks
(management commands, model logic, tests) use ``Member.can_edit_guild`` /
``Member.can_edit_class`` instead, which read the member's actual roles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils import timezone

if TYPE_CHECKING:
    from django.http import HttpRequest

    from classes.models import Category, ClassOffering
    from membership.models import CommunityEvent, Guild, Meeting, Member


def is_effective_staff(request: HttpRequest) -> bool:
    """True when the request's *effective* role is admin or guild officer.

    "Effective" means it respects ``view_as`` preview — an admin previewing as a
    member is not effective staff. These two roles can edit any guild or class.
    """
    view_as = getattr(request, "view_as", None)
    return view_as is not None and (view_as.is_admin or view_as.is_guild_officer)


def _editing_member(request: HttpRequest) -> Member | None:
    """The linked Member behind this request when it may act as a member, else None.

    Returns None unless the user is authenticated, the effective ``view_as`` role
    is at least Member, and a Member is linked. This mirrors the gate the hub edit
    helpers have always used, so an admin previewing as Guest gets no edit rights.
    """
    if not request.user.is_authenticated:
        return None
    view_as = getattr(request, "view_as", None)
    if view_as is None or not view_as.is_member:
        return None
    return getattr(request.user, "member", None)


def can_edit_guild(request: HttpRequest, guild: Guild) -> bool:
    """True when this request may edit the guild: admin/officer, the lead, or any staff member."""
    if is_effective_staff(request):
        return True
    member = _editing_member(request)
    return member is not None and (guild.guild_lead_id == member.pk or guild.is_staffed_by(member))


def can_manage_orientations(request: HttpRequest, guild: Guild) -> bool:
    """True when this request may run the guild's orientations.

    Anyone who can edit the guild can run its orientations — that now includes every
    staff member (orienters are a staff role). Honors ``view_as`` preview mode like the
    other helpers, so an admin previewing as a member sees only what that viewer would.
    """
    return can_edit_guild(request, guild)


def can_edit_orienter_hours(request: HttpRequest, guild: Guild, orienter: Member | None) -> bool:
    """True when this request may edit ``orienter``'s recurring orientation hours in ``guild``.

    Own hours: anyone who may manage the guild's orientations (lead, staff, admin,
    officer). Someone else's hours, or the guild-level rows (``orienter is None``):
    admin/officer or the ``guild_lead`` holder only. This is deliberately the first
    intra-staff authority distinction (a locked spec decision) — widening it later is
    a one-line change here.
    """
    member = _editing_member(request)
    if orienter is not None and member is not None and orienter.pk == member.pk:
        return can_manage_orientations(request, guild)
    if is_effective_staff(request):
        return True
    return member is not None and guild.guild_lead_id == member.pk


def can_edit_class(request: HttpRequest, offering: ClassOffering) -> bool:
    """True when this request may edit the class offering.

    Editors are admins/officers, the lead or any staff member of the class's category's
    guild, or the class's own instructor.
    """
    if is_effective_staff(request):
        return True
    member = _editing_member(request)
    if member is None:
        return False
    guild = offering.category.guild
    if guild is not None and (guild.guild_lead_id == member.pk or guild.is_staffed_by(member)):
        return True
    return offering.instructor_id == member.pk


def can_edit_event(request: HttpRequest, event: CommunityEvent) -> bool:
    """True when this request may edit the community event.

    A site-wide event (no guild) requires admin — the same gate the admin authoring
    view (``_require_admin``) uses, so the "Edit event" affordance and the QR download
    never drift from who may actually save it. A guild event defers to
    :func:`can_edit_guild` (lead / staff / admin / officer). Honors ``view_as`` preview
    like the other helpers.
    """
    guild = event.guild
    if guild is None:
        view_as = getattr(request, "view_as", None)
        return view_as is not None and view_as.is_admin
    return can_edit_guild(request, guild)


def can_edit_meeting(request: HttpRequest, meeting: Meeting) -> bool:
    """True when this request may edit the meeting's workspace.

    A guild meeting defers to :func:`can_edit_guild` (lead / staff / admin / officer).
    A council meeting (no guild) is editable by effective staff or anyone holding
    lead/staff authority in ANY guild — the same membership test the
    ``all_guild_leads`` resolver applies. Honors ``view_as`` preview like the other
    helpers.
    """
    guild = meeting.guild
    if guild is not None:
        return can_edit_guild(request, guild)
    if is_effective_staff(request):
        return True
    member = _editing_member(request)
    return member is not None and member.staffed_guilds.exists()


def editable_meeting_scopes(request: HttpRequest) -> tuple[list[Guild], bool]:
    """The guilds this request may edit (name-ordered) plus council editability, cheaply.

    The bulk companion to :func:`can_edit_guild` / :func:`can_edit_meeting` for surfaces
    that need every editable scope at once (the Meetings home §6.2, the create modal) —
    two queries instead of one per guild, same answers. Effective staff edit everything;
    otherwise the member's led/staffed guilds are editable, and holding lead/staff
    authority in ANY guild grants the council scope (§5.1).
    """
    from membership.models import Guild

    if is_effective_staff(request):
        return list(Guild.objects.order_by("name")), True
    member = _editing_member(request)
    if member is None:
        return [], False
    guilds = list(member.staffed_guilds.order_by("name"))
    return guilds, bool(guilds)


def can_propose_to_meeting(
    request: HttpRequest,
    meeting: Meeting,
    *,
    member_guild_ids: set[int] | None = None,
    is_editable: bool | None = None,
) -> bool:
    """True when this request may propose an agenda item for the meeting.

    Guild meeting: any active member of that guild (its leadership and admins
    trivially). Council: any active member. Always ``False`` once the meeting is
    locked or its date has passed.

    ``member_guild_ids`` is the bulk optimization for a caller checking many meetings at
    once (the Meetings home §6.2): pass the viewer's joined-guild pks (see
    :func:`viewer_guild_membership_ids`). An active member of the meeting's guild is then
    answered straight from the set, skipping the per-meeting roster ``.exists()`` query.

    ``is_editable`` lets that same bulk caller pass the meeting's already-computed
    editability (from the cheap :func:`editable_meeting_scopes` sets) so this function
    reuses it instead of firing ``can_edit_meeting``'s per-card staff-roster query for a
    meeting in a guild the viewer is not on. The two are equivalent (both derive from the
    member's led/staffed guilds), so a caller that already knows ``viewer_can_edit`` must
    pass it to avoid an N+1 down the Meetings list. Omit both and every check runs per call.
    """
    from membership.models import Member

    if meeting.is_locked:
        return False
    if meeting.scheduled_date is not None and meeting.scheduled_date < timezone.localdate():
        return False
    member = _editing_member(request)
    active_member_of_scope = (
        member_guild_ids is not None
        and meeting.guild_id is not None
        and member is not None
        and member.status == Member.Status.ACTIVE
        and meeting.guild_id in member_guild_ids
    )
    if active_member_of_scope:
        return True
    editable = is_editable if is_editable is not None else can_edit_meeting(request, meeting)
    if editable:
        return True
    if member is None or member.status != Member.Status.ACTIVE:
        return False
    if meeting.guild is None:
        return True  # council: any active member may propose
    if member_guild_ids is not None:
        return meeting.guild_id in member_guild_ids
    return meeting.guild.memberships.filter(member=member).exists()


def viewer_guild_membership_ids(request: HttpRequest) -> set[int]:
    """The signed-in member's joined-guild pks, for a bulk caller checking
    :func:`can_propose_to_meeting` for many meetings without a per-meeting ``.exists()``.
    Empty for a request with no linked, active-preview member."""
    member = _editing_member(request)
    if member is None:
        return set()
    return set(member.guild_memberships.values_list("guild_id", flat=True))


def can_edit_category(request: HttpRequest, category: Category) -> bool:
    """True when this request may edit the category — its guild's lead, or staff.

    A category with no guild can only be edited by admins/officers.
    """
    guild = category.guild
    if guild is not None:
        return can_edit_guild(request, guild)
    return is_effective_staff(request)
