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
    from membership.models import CommunityEvent, Equipment, Guild, Meeting, Member, WikiPage, WikiPageQuerySet


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
    officer) — but ONLY while actually on this guild's leadership. An admin/officer who
    is not on the guild's staff must not publish hours for themselves: ``generate_slots``
    skips non-leadership personal rules, so such a save would be a silent no-op — fail
    loudly here instead (they can still edit-on-behalf, the ``orienter != self`` path).
    Someone else's hours, or the guild-level rows (``orienter is None``): admin/officer
    or the ``guild_lead`` holder only. This is deliberately the first intra-staff
    authority distinction (a locked spec decision) — widening it later happens here.
    """
    member = _editing_member(request)
    if orienter is not None and member is not None and orienter.pk == member.pk:
        if all(leader.pk != member.pk for leader in guild.leadership_members()):
            return False  # self scope off this guild's leadership would only make dead rules
        return can_manage_orientations(request, guild)
    if is_effective_staff(request):
        return True
    return member is not None and guild.guild_lead_id == member.pk


def can_edit_equipment_orienter_hours(request: HttpRequest, equipment: Equipment, orienter: Member | None) -> bool:
    """True when this request may edit ``orienter``'s recurring orientation hours on ``equipment``.

    The equipment twin of :func:`can_edit_orienter_hours`. Own hours: anyone who may
    manage the equipment (all three tiers) — but ONLY while actually one of the tool's
    orienters. An admin or capability holder who has not been added on the Staff tab must
    not publish hours for themselves: ``_rule_generates`` skips personal rules whose owner
    is off ``orienter_members()``, so such a save would be a silent no-op — fail loudly
    here instead (they can still edit on behalf, the ``orienter != self`` path).
    Someone else's hours, or the shared rows (``orienter is None``): a full admin
    (``view_as``-aware), an EQUIPMENT capability holder (preview-independent, like every
    capability gate), or the owning guild's lead. A plain per-equipment manager therefore
    edits only their own hours.
    """
    from membership.models import AdminCapability

    member = _editing_member(request)
    if orienter is not None and member is not None and orienter.pk == member.pk:
        if all(runner.pk != member.pk for runner in equipment.orienter_members()):
            return False  # self scope off this tool's orienters would only make dead rules
        return can_manage_equipment(request, equipment)
    view_as = getattr(request, "view_as", None)
    if view_as is not None and view_as.is_admin:
        return True
    actual_member: Member | None = getattr(request.user, "member", None)
    if actual_member is not None and actual_member.has_admin_capability(AdminCapability.Capability.EQUIPMENT):
        return True
    guild = equipment.guild
    return member is not None and guild is not None and guild.guild_lead_id == member.pk


def can_manage_equipment(request: HttpRequest, equipment: Equipment) -> bool:
    """True when this request may manage the equipment (its manage panel, details, staff).

    Three tiers (the locked equipment-permissions decision):
    site tier — full admin (``view_as``-aware) or the EQUIPMENT capability;
    guild tier — the owning guild's lead or any staff member;
    resource tier — an ``EquipmentStaffMembership`` row.
    Guild officers get no blanket grant here — the site tier is deliberately
    narrower than ``is_effective_staff``.

    The admin leg honors ``view_as`` preview, but the capability leg is deliberately
    **preview-independent** — it reads the request's actual linked member, like every
    house capability gate (``hub.view_as._capability_or_admin_required``): a granted
    duty follows the person, not the preview. Migration 0161 backfills EQUIPMENT onto
    every existing admin, so in practice a previewing admin keeps manage access.
    """
    from membership.models import AdminCapability

    view_as = getattr(request, "view_as", None)
    if view_as is not None and view_as.is_admin:
        return True
    actual_member: Member | None = getattr(request.user, "member", None)
    if actual_member is not None and actual_member.has_admin_capability(AdminCapability.Capability.EQUIPMENT):
        return True
    member = _editing_member(request)
    if member is None:
        return False
    guild = equipment.guild
    if guild is not None and (guild.guild_lead_id == member.pk or guild.is_staffed_by(member)):
        return True
    return equipment.staff_memberships.filter(member=member).exists()


def can_create_equipment(request: HttpRequest) -> bool:
    """True when this request may create equipment — full admin or EQUIPMENT capability only.

    Guild leads and per-equipment managers edit and run equipment they manage but do not
    create it (locked decision #3). The admin leg honors ``view_as`` preview; the
    capability leg is preview-independent, matching :func:`can_manage_equipment` and the
    house capability gates.
    """
    from membership.models import AdminCapability

    view_as = getattr(request, "view_as", None)
    if view_as is not None and view_as.is_admin:
        return True
    actual_member: Member | None = getattr(request.user, "member", None)
    return actual_member is not None and actual_member.has_admin_capability(AdminCapability.Capability.EQUIPMENT)


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


# --- Wiki (member wiki, specs A and D) -------------------------------------------------
#
# ``can_moderate_wiki_page`` is spec D's (brief §9.1). A shipped a private, D-shaped
# stand-in (``_can_moderate_wiki_page``) because its own archived-page leg needed one
# before D landed; D deletes that and lands the public pair here, so there is exactly one
# definition and every template affordance and every view gate in both specs reads it.


def can_moderate_wiki_scope(request: HttpRequest, guild: Guild | None) -> bool:
    """True when this request may moderate wiki pages in ``guild`` (None = space wide).

    The page-less twin of :func:`can_moderate_wiki_page`, for the one moment there is no
    page yet: spec D's safety gate has to decide whether a member may publish an Official
    page *before* it creates one. Both read the same two legs, so the gate and the
    afterwards-affordance can never disagree.
    """
    if is_effective_staff(request):
        return True
    return guild is not None and can_edit_guild(request, guild)


def can_moderate_wiki_page(request: HttpRequest, page: WikiPage) -> bool:
    """True for effective staff, or for the page's own guild lead or any staff role.

    Page-scoped rather than request-scoped, and that is the whole point: a guild lead must
    be able to archive a bad page in their own guild without pulling in an officer, and
    :func:`is_effective_staff` alone cannot express that. ``can_edit_guild`` already covers
    co-lead, secretary, treasurer and orienter. A space-wide page (``guild`` is None) is
    moderatable by effective staff only.
    """
    return can_moderate_wiki_scope(request, page.guild)


def moderatable_wiki_scopes(request: HttpRequest) -> tuple[list[Guild], bool]:
    """The guilds whose wiki pages this request may moderate, plus space-wide access.

    Reuses :func:`editable_meeting_scopes`'s cheap two-query guild list but deliberately
    REPLACES its council boolean: that helper grants the council scope to anyone holding
    lead or staff authority in ANY guild, which is right for meetings and would hand every
    guild lead the whole site-wide wiki. Space-wide pages are effective-staff only.
    """
    guilds, _council = editable_meeting_scopes(request)
    return guilds, is_effective_staff(request)


def _is_own_unpublished_proposal(request: HttpRequest, page: WikiPage) -> bool:
    """True when this request belongs to the author of a page still held for a read.

    The carve-out spec D's decline loop needs (D21). Narrow on purpose: the page must be
    unpublished, un-archived, and this request's own active member must be its
    ``created_by``. An archived proposal is not included — restoring one is a moderator's
    act, not the author's.
    """
    from membership.models import Member

    if page.is_published or page.archived_at is not None or page.created_by_id is None:
        return False
    member = _editing_member(request)
    return member is not None and member.pk == page.created_by_id and member.status == Member.Status.ACTIVE


def can_edit_wiki_page(request: HttpRequest, page: WikiPage) -> bool:
    """True when this request may edit the page's title, body, facts, and attachments.

    ``OFFICIAL`` pages: effective staff only (admins/officers) — a member sees no edit
    affordance at all, not a disabled one. An archived page: ``can_moderate_wiki_page``
    only — nothing about archiving requires a page to be locked to every plain member
    forever, but members cannot delete and cannot resurrect one either. Everything else:
    any ACTIVE member, live, with no approval queue (the locked "who may edit" rule).

    This is also the gate on the three micro-contribution routes — quick tip, quick
    photo, and the editor's image upload (brief §9.3). The tip route writes the body
    through ``apply_edit``, so gating it on membership alone would put two write
    affordances at the bottom of an Official page.

    **An unpublished proposal is always editable by its own author** (spec D's D21),
    whatever its status. Spec D's safety gate saves a member's Safety page
    ``is_published=False`` with ``status=OFFICIAL`` for a lead to read, and declining it
    asks the author to change something — so without this carve-out the author is locked
    out of the very draft they were just asked to fix, which is a dead end wearing a
    friendly note. Publishing is still gated on ``can_moderate_wiki_page``; only editing
    is opened, and the moment it is published the ordinary Official rule applies again.

    **A held-back page is invisible here too.** If the gate only filtered the listings,
    every write route would still answer a crafted URL and it would be decoration. Note
    where that leg actually bites: OFFICIAL returns above it, so a held Official page is
    settled by the earlier rule. What it gates is a held COMMUNITY or guild-verified
    page, whose author and whose guild lead/staff keep their edit right.
    The check lives HERE and not in each view for brief §3's named reason: v1.39.0 gated
    ``/register/<key>/`` while every register kept its own URL, so a member loading
    ``/finance/`` got a 200 and the full financials. Every alternate path to a page needs
    the same gate, so it belongs on the one function every path already calls — including
    the ones spec D adds on top of A's merged main.
    """
    from membership.models import Member, WikiPage

    # OFFICIAL is checked FIRST. Testing archived first let an archived Official page in a
    # guild fall through to the moderator leg, handing that guild's lead an edit right the
    # locked rule never grants on Official content — and the author carve-out below is
    # deliberately inside this branch and after the archived guard, so an archived
    # proposal stays moderators-only.
    if page.status == WikiPage.Status.OFFICIAL:
        if _is_own_unpublished_proposal(request, page):
            return True
        return is_effective_staff(request)
    if page.archived_at is not None:
        return can_moderate_wiki_page(request, page)
    member = _editing_member(request)
    if member is None or member.status != Member.Status.ACTIVE:
        return False
    if not page.is_published:
        # visible_for() is the same predicate in bulk; asking it for one row keeps the
        # listing filter and the write gate provably in step.
        return WikiPage.objects.visible_for(request).filter(pk=page.pk).exists()
    return True


def can_verify_wiki_page(request: HttpRequest, page: WikiPage) -> bool:
    """True when this request may stand behind the page with a green check.

    An OFFICIAL page is never verifiable, by anyone — this guard runs first, before any
    authority test. Otherwise: a guild-scoped page defers to :func:`can_edit_guild`
    (lead, every staff role, orienters included via the staff-role fold-in). A
    space-wide page takes :func:`is_effective_staff`. A page linked to Equipment
    additionally admits that tool's own orienters via ``Equipment.is_run_by`` — the
    people who teach the machine — even when the tool belongs to no guild.
    """
    from membership.models import WikiPage

    if page.status == WikiPage.Status.OFFICIAL:
        return False
    guild = page.guild
    if guild is not None:
        if can_edit_guild(request, guild):
            return True
    elif is_effective_staff(request):
        return True
    equipment = page.equipment
    if equipment is not None:
        member = _editing_member(request)
        if member is not None and equipment.is_run_by(member):
            return True
    return False


def visible_wiki_pages(request: HttpRequest) -> WikiPageQuerySet:
    """The pages this request may see in listings and search — a FILTER, not a check.

    Views ask for this and render what comes back, so a view that forgets to gate shows
    too little, never too much.
    """
    from membership.models import WikiPage

    return WikiPage.objects.visible_for(request)


def editable_wiki_scopes(request: HttpRequest) -> tuple[list[Guild], bool]:
    """(guilds this request may scope a page to, may-create-space-wide) in two queries.

    The bulk companion for the New-page scope picker, mirroring
    :func:`editable_meeting_scopes`. Every active member may create a space-wide page
    and a page in any guild they have joined; staff get every guild.
    """
    from membership.models import Guild, Member

    if is_effective_staff(request):
        return list(Guild.objects.order_by("name")), True
    member = _editing_member(request)
    if member is None or member.status != Member.Status.ACTIVE:
        return [], False
    # Member.joined_guilds is this query, name-ordered. No .distinct() needed:
    # GuildMembership carries uq_guildmembership_guild_member.
    return list(member.joined_guilds), True
