"""Whose membership status locks them out, and what they are told (#409).

One rule, three gates. :func:`lockout_reason` answers only "is this user's membership locked
out"; each gate decides where that matters:

- ``AdminRedirectAccountAdapter.pre_login`` refuses a sign-in on the members surface only, so a
  former member can still sign in on the book site to see their class receipts.
- ``core.views.biometric_unlock`` always refuses: it is the app, which is the members site.
- ``core.middleware.MemberLockoutMiddleware`` never logs anyone out (the session cookie is
  shared with the book site). On the members surface it sends every request to the lockout
  page; on the book surface it allows only ``settings.LOCKED_OUT_BOOK_PATH_PREFIXES``.

The rule reads ``Member.status``, never ``User.is_active``:

- FORMER is always locked out, staff and superusers included.
- GUEST (an account made from a class booking, #654) is always locked out the same way.
- SUSPENDED is locked out while ``SiteConfiguration.suspended_members_locked_out`` is on.
- INVITED and ACTIVE are not, and neither is a user with no Member row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from django.http import Http404

if TYPE_CHECKING:
    from django.db.models import Field


def lockout_reason(user: object) -> str | None:
    """The Member status that locks ``user`` out, or None if their membership does not.

    Args:
        user: The user signing in or already signed in (anonymous users have no member).

    Returns:
        ``Member.Status.FORMER``, ``GUEST`` or ``SUSPENDED`` when locked out, else None.
    """
    # The reverse one-to-one accessor caches on the user, so the request's other readers of
    # ``request.user.member`` (MemberAgreementMiddleware, the hub views) reuse this lookup.
    member = getattr(user, "member", None)
    if member is None:
        return None

    from membership.models import Member

    if member.status in (Member.Status.FORMER, Member.Status.GUEST):
        return str(member.status)
    if member.status == Member.Status.SUSPENDED:
        from core.models import SiteConfiguration

        if SiteConfiguration.load().suspended_members_locked_out:
            return str(Member.Status.SUSPENDED)
    return None


def lockout_message(reason: str) -> str:
    """The admin-editable sentence shown to a member locked out for ``reason``.

    A blank setting falls back to the field's own default, so clearing the box never leaves a
    locked-out member looking at an empty page.

    Raises:
        Http404: ``reason`` is not a lockout reason (it arrives in a query string).
    """
    from core.models import SiteConfiguration
    from membership.models import Member

    field_by_reason: dict[str, str] = {
        str(Member.Status.FORMER): "former_member_signin_message",
        str(Member.Status.GUEST): "guest_member_signin_message",
        str(Member.Status.SUSPENDED): "suspended_member_signin_message",
    }
    if reason not in field_by_reason:
        raise Http404("No such lockout.")
    field_name = field_by_reason[reason]
    message: str = getattr(SiteConfiguration.load(), field_name)
    if message.strip():
        return message
    field = cast("Field[Any, Any]", SiteConfiguration._meta.get_field(field_name))
    default: str = field.default
    return default
