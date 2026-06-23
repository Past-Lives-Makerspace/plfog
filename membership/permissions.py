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

if TYPE_CHECKING:
    from django.http import HttpRequest

    from classes.models import Category, ClassOffering
    from membership.models import Guild, Member


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
    """True when this request may edit the guild: admin/officer, or the guild's lead."""
    if is_effective_staff(request):
        return True
    member = _editing_member(request)
    return member is not None and guild.guild_lead_id == member.pk


def can_manage_orientations(request: HttpRequest, guild: Guild) -> bool:
    """True when this request may run the guild's orientations.

    Editors (admin / officer / the guild's lead) always can; so can a member the
    guild has designated as an orienter. Honors ``view_as`` preview mode like the
    other helpers — an admin previewing as a member sees only what that viewer
    would. Adding or removing orienters stays gated on ``can_edit_guild``.
    """
    if can_edit_guild(request, guild):
        return True
    member = _editing_member(request)
    return member is not None and guild.orienters.filter(pk=member.pk).exists()


def can_edit_class(request: HttpRequest, offering: ClassOffering) -> bool:
    """True when this request may edit the class offering.

    Editors are admins/officers, the lead of the class's category's guild (FK
    only), or the class's own instructor.
    """
    if is_effective_staff(request):
        return True
    member = _editing_member(request)
    if member is None:
        return False
    guild = offering.category.guild
    if guild is not None and guild.guild_lead_id == member.pk:
        return True
    return offering.instructor_id == member.pk


def can_edit_category(request: HttpRequest, category: Category) -> bool:
    """True when this request may edit the category — its guild's lead, or staff.

    A category with no guild can only be edited by admins/officers.
    """
    guild = category.guild
    if guild is not None:
        return can_edit_guild(request, guild)
    return is_effective_staff(request)
