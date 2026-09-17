"""Who may open a class-management screen, and what they may do once they are on it.

One per-class screen serves four populations — full admins, ``CLASS_APPROVER``
reviewers, the class's own instructor, and the guild lead or staff behind the class's
category — and each of them sees a different strip of tabs and a different set of
actions. :func:`class_access` answers "which of those is this request, on this class"
exactly once, and :class:`ClassAccess` carries the answer to the view and the template.

A frozen dataclass rather than a dict, deliberately: a typo'd capability raises
``AttributeError`` where ``dict.get`` would quietly render an empty tab strip. Failing
loudly is the whole reason the answer has a shape.

The per-object rule on the guild leg is :func:`membership.permissions.can_edit_class`
itself, never a local re-derivation. That helper already reads the *effective*
(preview-aware) role, already guards the nullable ``Category.guild``, and already asks
``Guild.is_staffed_by`` rather than comparing ids. Production carries 86 classes under a
guild-less category, and a hand-written predicate raises ``AttributeError`` on every one
of them. Nothing in ``membership/permissions.py`` changes; this module composes it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import TYPE_CHECKING, Any

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404

from classes.models import ClassOffering
from core.models import SiteConfiguration
from membership.permissions import can_edit_class

if TYPE_CHECKING:
    from membership.models import Member

_ViewFunc = Callable[..., HttpResponse]

ROLE_ADMIN = "admin"
ROLE_REVIEWER = "reviewer"
ROLE_INSTRUCTOR = "instructor"
ROLE_GUILD = "guild"

ADMIN_SHELL = "classes/admin/base.html"
TEACH_SHELL = "classes/teach/base.html"

TAB_OVERVIEW = "overview"
TAB_REGISTRATIONS = "registrations"
TAB_WAITLIST = "waitlist"
TAB_DISCOUNT_CODES = "discount_codes"
TAB_EMAILS = "emails"

#: The per-class tab strip, in render order, each paired with the capability that
#: reveals it. A tab a viewer may not use is not rendered at all — never rendered and
#: then 404'd on the click.
_TAB_STRIP: tuple[tuple[str, str, str], ...] = (
    (TAB_OVERVIEW, "Overview", "can_view_overview"),
    (TAB_REGISTRATIONS, "Registrations", "can_view_registrations"),
    (TAB_WAITLIST, "Waitlist", "can_view_waitlist"),
    (TAB_DISCOUNT_CODES, "Discount Codes", "can_view_discount_codes"),
    (TAB_EMAILS, "Emails", "can_view_emails"),
)


@dataclass(frozen=True)
class ClassTab:
    """One visible tab in the per-class strip: its url segment and its label."""

    key: str
    label: str


@dataclass(frozen=True)
class ClassAccess:
    """What one request may see and do on one class.

    Built only by :func:`class_access`, which picks exactly one of the four capability
    sets below. Every field is spelled out on every set rather than defaulted, so a row
    of this table can never drift by omission.
    """

    role: str
    shell: str
    landing_tab: str
    can_view_overview: bool
    can_view_registrations: bool
    can_view_waitlist: bool
    can_view_discount_codes: bool
    can_view_emails: bool
    can_edit: bool
    can_approve: bool
    can_administer: bool
    can_submit: bool
    can_cancel: bool
    can_sale: bool
    can_send_email: bool

    @property
    def tabs(self) -> tuple[ClassTab, ...]:
        """The visible tabs in order, so a template loops once instead of branching five times."""
        return tuple(
            ClassTab(key=key, label=label) for key, label, capability in _TAB_STRIP if getattr(self, capability)
        )


def _admin_access() -> ClassAccess:
    """A full fog admin: every tab, every action. Submitting is the instructor's move, not theirs."""
    return ClassAccess(
        role=ROLE_ADMIN,
        shell=ADMIN_SHELL,
        landing_tab=TAB_OVERVIEW,
        can_view_overview=True,
        can_view_registrations=True,
        can_view_waitlist=True,
        can_view_discount_codes=True,
        can_view_emails=True,
        can_edit=True,
        can_approve=True,
        can_administer=True,
        can_submit=False,
        can_cancel=True,
        can_sale=True,
        can_send_email=True,
    )


def _reviewer_access() -> ClassAccess:
    """A ``CLASS_APPROVER`` holder who is not an admin: read the class, approve or bounce it.

    One tab and two buttons. The grant's contract is approve/validate, so it opens no
    roster, no waitlist, no discount codes, no mailbox and no composer.
    """
    return ClassAccess(
        role=ROLE_REVIEWER,
        shell=ADMIN_SHELL,
        landing_tab=TAB_OVERVIEW,
        can_view_overview=True,
        can_view_registrations=False,
        can_view_waitlist=False,
        can_view_discount_codes=False,
        can_view_emails=False,
        can_edit=False,
        can_approve=True,
        can_administer=False,
        can_submit=False,
        can_cancel=False,
        can_sale=False,
        can_send_email=False,
    )


def _instructor_access() -> ClassAccess:
    """The class's own instructor: their whole class, minus the admin lifecycle actions.

    Discount Codes is the one conditional tab on the strip — instructor self-service
    codes are a site flag, default off, and the tab follows it.
    """
    return ClassAccess(
        role=ROLE_INSTRUCTOR,
        shell=TEACH_SHELL,
        landing_tab=TAB_OVERVIEW,
        can_view_overview=True,
        can_view_registrations=True,
        can_view_waitlist=True,
        can_view_discount_codes=SiteConfiguration.load().instructor_discount_codes_enabled,
        can_view_emails=True,
        can_edit=True,
        can_approve=False,
        can_administer=False,
        can_submit=True,
        can_cancel=True,
        can_sale=True,
        can_send_email=True,
    )


def _guild_access() -> ClassAccess:
    """A guild lead or staffer on a class they do not teach: Edit and Emails, nothing else.

    Ruling 12: no Overview on someone else's class, so Emails is where they land.
    """
    return ClassAccess(
        role=ROLE_GUILD,
        shell=TEACH_SHELL,
        landing_tab=TAB_EMAILS,
        can_view_overview=False,
        can_view_registrations=False,
        can_view_waitlist=False,
        can_view_discount_codes=False,
        can_view_emails=True,
        can_edit=True,
        can_approve=False,
        can_administer=False,
        can_submit=False,
        can_cancel=False,
        can_sale=False,
        can_send_email=False,
    )


def _leads_or_staffs(member: Member, offering: ClassOffering) -> bool:
    """Ruling 23's population: the lead or a staffer of the class's category guild.

    This does **not** decide admission — :func:`membership.permissions.can_edit_class`
    still does, and this helper can never let in anyone that refuses. It decides only
    whether the teaching-grant precondition on the guild leg is *waived*. So it is not
    the parallel per-object predicate the plan forbids: the nullable ``Category.guild``,
    the effective-role reading and the staff join all remain ``can_edit_class``'s
    problem, which is exactly why the guild leg still asks it first.

    It does guard the nullable guild itself, because production carries 86 classes under
    a guild-less category and this runs on every one of them.

    Args:
        member: The linked Member behind the request.
        offering: The class being opened.

    Returns:
        True when this member leads or staffs the guild that owns the class's category.
    """
    guild = offering.category.guild
    return guild is not None and (guild.guild_lead_id == member.pk or guild.is_staffed_by(member))


# Ruling 23, and it is this ticket's ONE deliberate widening: a guild lead or staffer who
# was never granted teaching access reaches Edit and Emails on their own guild's classes,
# where the teaching portal bounced them before. Staff get the same reach as leads. It
# widens nothing else, because the GUILD capability set is Edit plus Emails and nothing
# more — no Overview, no roster, no waitlist, no discount codes, no lifecycle actions.
#
# The precondition is WAIVED for that population, not dropped from the leg. Dropping it
# outright would admit two populations ruling 23 does not name, because ``can_edit_class``
# is broader than "guild lead or staff of this class": it short-circuits on
# ``is_effective_staff``, so every site-wide guild officer would reach EVERY class, and its
# last clause is the instructor check, so anyone merely *named* instructor of a class would
# reach it without ever being granted teaching. PLAN.md §2 named the first of those as the
# reason the precondition existed, and ruling 6 — do not widen anyone's access in this
# ticket — still governs everyone ruling 23 does not name. Both stay denied.
#
# The instructor leg keeps its ``can_create_classes`` precondition unconditionally, which
# is the real predicate behind ``teaching_member_required``.
def class_access(request: HttpRequest, offering: ClassOffering) -> ClassAccess | None:
    """The capability set this request holds on this class, or ``None`` for no access.

    First match wins and the order is load-bearing:

    0. Guest (effective) — denied outright. ``can_edit_class`` and ``instructor_id``
       read the *model*, not the preview, so without this leg an admin previewing as
       Guest would still be shown the classes they teach.
    1. Effective admin — the full set.
    2. Not previewing, and holds ``CLASS_APPROVER`` — the reviewer set. Suppressed while
       previewing because an admin who genuinely holds the grant and picks "Member"
       should see what a member sees.
    3. Teaching access and the class's instructor — the instructor set.
    4. :func:`membership.permissions.can_edit_class`, plus either teaching access or
       ruling 23's waiver (:func:`_leads_or_staffs`) — the guild set. Legs 1 and 3 have
       already caught admins and the instructor, so what this leg adds is exactly guild
       lead-or-staff.

    Args:
        request: The incoming request, carrying ``view_as`` from the middleware.
        offering: The class being opened.

    Returns:
        The viewer's capability set, or ``None`` when no leg matches.
    """
    view_as = getattr(request, "view_as", None)
    if view_as is None or view_as.is_guest:
        return None
    if view_as.is_admin:
        return _admin_access()
    # Legs 2, 3 and 4 all act as a member, so an unauthenticated request or one with no
    # linked Member matches no leg.
    member: Member | None = getattr(request.user, "member", None)
    if member is None:
        return None

    from membership.models import AdminCapability

    if not view_as.is_previewing and member.has_admin_capability(AdminCapability.Capability.CLASS_APPROVER):
        return _reviewer_access()
    if member.can_create_classes and offering.instructor_id == member.pk:
        return _instructor_access()
    if can_edit_class(request, offering) and (member.can_create_classes or _leads_or_staffs(member, offering)):
        return _guild_access()
    return None


def class_screen_required(view_func: _ViewFunc) -> _ViewFunc:
    """Decorator: resolve the class and the viewer's capability set, or 404.

    Attaches ``request.class_access`` and ``request.class_offering`` for the view, which
    then reads the capability set rather than re-deriving who the viewer is.

    The refusal is a 404, never a 403. ``templates/404.html`` extends ``hub/base.html``
    and so carries the "Viewing as" switcher an admin previewing a lower role needs to
    get back out; a bare ``HttpResponseForbidden`` strands them on unstyled plain text.
    A 404 also declines to confirm that the class exists.

    The offering is fetched plainly — the per-view prefetches stay in the views, where
    each screen knows what it is about to render.
    """

    @wraps(view_func)
    @login_required
    def wrapper(request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> HttpResponse:
        offering = get_object_or_404(ClassOffering, pk=pk)
        access = class_access(request, offering)
        if access is None:
            raise Http404("No class management screen for this viewer.")
        request.class_access = access  # type: ignore[attr-defined]
        request.class_offering = offering  # type: ignore[attr-defined]
        return view_func(request, pk, *args, **kwargs)

    return wrapper  # type: ignore[return-value]
