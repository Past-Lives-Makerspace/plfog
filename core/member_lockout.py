"""Who is turned away from the members site because of their membership status (#409).

One rule, three gates. The allauth login (``AdminRedirectAccountAdapter.pre_login``), the
biometric unlock (``core.views.biometric_unlock``) and every request of a live session
(``core.middleware.MemberLockoutMiddleware``) all ask :func:`lockout_reason` and nothing else.

The gate reads ``Member.status``, never ``User.is_active``: one account signs in to both the
members site and the book site through a shared session cookie, and a former member keeps
their class receipts on the book site. So only the members surface is gated.

- FORMER is always locked out, staff and superusers included.
- SUSPENDED is locked out while ``SiteConfiguration.suspended_members_locked_out`` is on.
- INVITED and ACTIVE sign in, and so does a user with no Member row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from django.http import Http404

if TYPE_CHECKING:
    from django.db.models import Field
    from django.http import HttpRequest


def lockout_reason(request: HttpRequest, user: object) -> str | None:
    """The Member status that keeps ``user`` off the members site, or None if they may sign in.

    Args:
        request: The current request; only its ``surface`` is read.
        user: The user signing in or already signed in.

    Returns:
        ``Member.Status.FORMER`` or ``Member.Status.SUSPENDED`` when locked out, else None.
        Always None off the members surface.
    """
    if getattr(request, "surface", None) != "members":
        return None
    # The reverse one-to-one accessor caches on the user, so the request's other readers of
    # ``request.user.member`` (MemberAgreementMiddleware, the hub views) reuse this lookup.
    member = getattr(user, "member", None)
    if member is None:
        return None

    from membership.models import Member

    if member.status == Member.Status.FORMER:
        return str(Member.Status.FORMER)
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
