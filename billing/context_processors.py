"""Template context processors for billing app."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest


def tab_context(request: HttpRequest) -> dict[str, Any]:
    """Add tab balance and status to template context for the balance pill.

    ``request.user`` is read defensively. ``SurfaceMiddleware`` short-circuits member-only
    paths on the guest surfaces with an ``Http404`` *before* ``AuthenticationMiddleware``
    has run, and the themed ``templates/404.html`` renders every context processor — so on
    those responses the attribute does not exist yet. Same guard as ``core.persona``.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}

    from core.models import SiteConfiguration

    if not SiteConfiguration.load().tab_payments_enabled:
        return {}

    from membership.models import Member

    member: Member | None = getattr(user, "member", None)
    if member is None:
        return {}

    from billing.models import Tab

    tab, _created = Tab.objects.get_or_create(member=member)
    return {
        "tab_balance": tab.current_balance,
        "tab_is_locked": tab.is_locked,
        "tab_has_payment_method": tab.has_payment_method,
    }
