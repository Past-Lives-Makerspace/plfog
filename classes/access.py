"""Who may open a class-management screen, and what they may do once they are on it.

One per-class screen serves four populations — full admins, ``CLASS_APPROVER``
reviewers, the class's own instructor, and the guild lead or staff behind the class's
category — and each of them sees a different strip of tabs and a different set of
actions. :func:`class_access` answers "which of those is this request, on this class"
exactly once, and :class:`ClassAccess` carries the answer to the view and the template.

**Those four populations are not disjoint, and the sets compose rather than compete.** One
member can hold the ``CLASS_APPROVER`` grant *and* teach the class, or lead or staff the
guild behind it. Picking a single set by first match took their own workspace away: one
Overview tab and an approve button, no roster, no waitlist, no welcome email, no submit, no
edit. Ruling 25 settles it — whoever they already are on this class, they keep that set and
the reviewer row is UNIONED into it (:func:`_with_reviewer_grant`). A ``CLASS_APPROVER`` who
matches nothing else still gets :func:`_reviewer_access` exactly as before.

The union is the whole rule, and it is what keeps this safe in both directions: a composed
viewer gains nothing they did not already hold under one of their two hats, and loses
nothing either. That second half is not decoration — the grant alone already opens the
Overview on every class in the catalog, and the Approve button lives on the Overview and
nowhere else (``templates/classes/teach/class_overview.html``), so a composed set without it
would carry ``can_approve`` that no page could ever render.

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
from dataclasses import dataclass, replace
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

    Built only by :func:`class_access`, which picks one of the four capability sets below
    and may union the reviewer row into it (:func:`_with_reviewer_grant`). Every field is
    spelled out on every set rather than defaulted, so a row of this table can never drift
    by omission.
    """

    role: str
    shell: str
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
    """A ``CLASS_APPROVER`` holder with no other claim on the class: read it, approve or bounce it.

    One tab and two buttons. The grant's contract is approve/validate, so it opens no
    roster, no waitlist, no discount codes, no mailbox and no composer.

    This is the grant *standing alone*, for a holder who matches no other leg. One who does
    gets their own set with this row unioned into it instead (ruling 25,
    :func:`_with_reviewer_grant`) rather than falling back to this one.
    """
    return ClassAccess(
        role=ROLE_REVIEWER,
        shell=ADMIN_SHELL,
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

    **And Emails now works.** ``can_send_email`` was False here until #371 item 1, which made
    the one tab this population lands on the one thing they could not use: the Send Email link
    was withheld, and a guild lead who reached the composer another way got 200 from the
    preview and 403 from Send. Jo's call is to let them send, with the composer's per-person
    recipient picker.

    That is a deliberate relaxation of ruling 12 in practice, and it is recorded here rather
    than arrived at quietly. ``can_view_registrations`` stays False, so the roster tab is still
    closed to them — but the composer's picker lists registrants by name, so a guild lead who
    may address a class can see who is in it. Jo was shown that consequence and chose it. Do
    not read the surviving False on ``can_view_registrations`` as a claim that names are hidden
    from this population; it only means the Registrations TAB is not theirs.

    **Read "this population" wider than the docstring's first line.** This row is not reached
    only by the lead or staffer of the class's own guild. ``class_access``'s guild leg asks
    ``can_edit_class``, which short-circuits on ``is_effective_staff``, so a **site-wide guild
    officer holding the teaching grant lands here on every class in the catalog** — the long
    comment above ``class_access`` spells that out, and it has been true since #399. Flipping
    ``can_send_email`` therefore hands that member the composer, and every registrant's name and
    email, on classes they neither teach nor have any guild claim on. That is accepted for the
    same reason as the lead's case and one more: the same short-circuit in ``_can_edit_guild``
    already lets them address every guild's full membership through the same composer, so
    withholding one class roster would be inconsistency, not protection. Anyone narrowing this
    row later should narrow the leg, not the flag.
    """
    return ClassAccess(
        role=ROLE_GUILD,
        shell=TEACH_SHELL,
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
        can_send_email=True,
    )


def _with_reviewer_grant(access: ClassAccess) -> ClassAccess:
    """``access`` unioned with the reviewer row. Ruling 25.

    Jo: "A Guild Lead can approve their own class since they don't have the full ability to
    get the class published; only admins/CMS admins can do that. A CMS Admin can approve
    their own class too."

    :func:`_reviewer_access` carries exactly two capabilities — ``can_view_overview`` and
    ``can_approve`` — so the union of any set with it is those two ORed in. Both are needed:
    ``can_approve`` is the ruling, and the Overview is the only page that renders it, as
    well as where the CMS Administrator's own queue
    (``templates/classes/admin/classes_list.html``) sends every row. Granting it back widens
    nothing, because the grant alone already opens the Overview on every class in the
    catalog. ``describe_the_reviewer_row_is_unioned_in`` pins that this helper stays equal to
    the reviewer row's True capabilities, so it cannot go stale if that row ever gains one.

    The base set keeps its own ``role`` and ``shell``: on this class they are still primarily
    its instructor or its guild, and the shell is chrome rather than a capability.

    ``dataclasses.replace`` on the frozen dataclass, deliberately, rather than a mutable dict
    of defaults: the composed set is still every field spelled out, and a typo'd capability
    still raises rather than quietly rendering an empty tab strip.

    Args:
        access: The set this viewer already holds on this class.

    Returns:
        That set with the reviewer row's capabilities ORed into it.
    """
    return replace(access, can_view_overview=True, can_approve=True)


def leads_or_staffs(member: Member, offering: ClassOffering) -> bool:
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
# ticket — still governs everyone ruling 23 does not name.
#
# What the precondition actually denies is the second of those two: a named-but-ungranted
# instructor. It does NOT deny the first, because a site-wide guild officer who holds the
# teaching grant satisfies ``member.can_create_classes`` outright and so reaches this leg on
# every class. That is not new and not a widening — before #399 ``editable_by`` returned the
# whole catalog for an officer — but do not read this paragraph as a claim that they are
# refused here. They are not.
#
# The instructor leg keeps its ``can_create_classes`` precondition unconditionally, which
# is the real predicate behind ``teaching_member_required``.
def class_access(request: HttpRequest, offering: ClassOffering) -> ClassAccess | None:
    """The capability set this request holds on this class, or ``None`` for no access.

    First match wins among the legs, and the order is load-bearing:

    0. Guest (effective) — denied outright. ``can_edit_class`` and ``instructor_id``
       read the *model*, not the preview, so without this leg an admin previewing as
       Guest would still be shown the classes they teach.
    1. Effective admin — the full set.
    2. Teaching access and the class's instructor — the instructor set.
    3. :func:`membership.permissions.can_edit_class`, plus either teaching access or
       ruling 23's waiver (:func:`leads_or_staffs`) — the guild set. Legs 1 and 2 have
       already caught admins and the instructor, so what this leg adds is guild
       lead-or-staff, plus the site-wide guild officers ``can_edit_class`` lets through on
       its ``is_effective_staff`` short-circuit, who have reached this leg since #399.

    **The ``CLASS_APPROVER`` grant is not a leg** (ruling 25). It is read once, before the
    legs are walked, and then unioned into whichever set matched
    (:func:`_with_reviewer_grant`). It stands alone — as the one-tab
    :func:`_reviewer_access` — only for a holder who matched no leg at all. Because the
    composition is a union, a composed viewer holds exactly what their two hats already gave
    them separately: nothing is widened (``can_administer`` stays admin-only, and the grant
    already opened the Overview on every class) and nothing is taken away.

    The grant is suppressed while previewing, because an admin who genuinely holds it and
    picks "Member" should see what a member sees. It is read once, into
    ``holds_reviewer_grant``, so that suppression governs the composed sets too.

    Reading the grant after the legs rather than before them costs a holder the
    ``can_edit_class`` evaluation the old order short-circuited past: at most two
    ``is_staffed_by`` queries (``can_edit_class`` and :func:`leads_or_staffs` each ask
    independently), on a population of a few dozen, on a screen that already fetched the
    class. The same cost every non-holder on this leg has always paid.

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
    # Legs 2 and 3, and the grant, all act as a member, so an unauthenticated request or one
    # with no linked Member matches nothing below.
    member: Member | None = getattr(request.user, "member", None)
    if member is None:
        return None

    from membership.models import AdminCapability

    holds_reviewer_grant = not view_as.is_previewing and member.has_admin_capability(
        AdminCapability.Capability.CLASS_APPROVER
    )
    if member.can_create_classes and offering.instructor_id == member.pk:
        access = _instructor_access()
    elif can_edit_class(request, offering) and (member.can_create_classes or leads_or_staffs(member, offering)):
        access = _guild_access()
    else:
        # No leg matched. The grant alone, or nothing.
        return _reviewer_access() if holds_reviewer_grant else None
    return _with_reviewer_grant(access) if holds_reviewer_grant else access


def class_screen_required(view_func: _ViewFunc) -> _ViewFunc:
    """Decorator: resolve the class and the viewer's capability set, or 404.

    Attaches ``request.class_access`` and ``request.class_offering`` for the view, which
    then reads the capability set rather than re-deriving who the viewer is.

    The refusal is a 404, never a 403. ``templates/404.html`` extends ``hub/base.html``
    and so carries the "Viewing as" switcher an admin previewing a lower role needs to
    get back out; a bare ``HttpResponseForbidden`` strands them on unstyled plain text.
    A 404 also declines to confirm that the class exists.

    The fetch follows the two forward keys the *shared* chrome reads, and nothing else.
    Every tab now extends ``class_screen_base.html``, whose header names the instructor and
    the category, so fetching plainly cost Registrations, Waitlist, Discount Codes and
    Emails two extra queries each — the pre-merge headers on those tabs named neither.
    ``select_related`` is a join on a fetch that happens anyway, so it costs nothing on the
    action routes that never render a header.

    ``sessions`` is deliberately NOT prefetched. For a single object a prefetch is one query
    either way, so it would save the header nothing and would add a query to every POST-only
    route under this decorator — ``teach_submit_spec``'s "one sessions query" budget catches
    exactly that. Anything a single tab needs beyond the header stays that tab's business:
    the Overview re-fetches through ``_class_screen_offering`` for its sessions and its
    registration count.
    """

    @wraps(view_func)
    @login_required
    def wrapper(request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> HttpResponse:
        offering = get_object_or_404(
            ClassOffering.objects.select_related("instructor", "category__guild"),
            pk=pk,
        )
        access = class_access(request, offering)
        if access is None:
            raise Http404("No class management screen for this viewer.")
        request.class_access = access  # type: ignore[attr-defined]
        request.class_offering = offering  # type: ignore[attr-defined]
        return view_func(request, pk, *args, **kwargs)

    return wrapper  # type: ignore[return-value]
