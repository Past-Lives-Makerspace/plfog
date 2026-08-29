"""Session-backed "Viewing as" role preview for the hub.

A user's *actual* roles are derived from ``member.fog_role``. Admins can pick
a *view-as* role via the topbar dropdown — the picked role name is stored in
``request.session["view_as_role"]``.

Hierarchy roles are a linear ladder: admin > guild_officer > member.
Effective roles are every role from the user's actual set that sits at or
below the picked role. When no role is picked (or the picked role is the
user's highest), effective == actual.

``guest`` sits outside the hierarchy — assigned to anonymous visitors so
view-as checks work uniformly on public-facing pages.

Every hub-side UI gate should read ``request.view_as`` (attached by
``ViewAsMiddleware``) so the session override is respected uniformly.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
    from django.http import HttpRequest, HttpResponseBase

_ViewFunc = Callable[..., "HttpResponseBase"]

ROLE_MEMBER = "member"
ROLE_GUILD_OFFICER = "guild_officer"
ROLE_ADMIN = "admin"
ROLE_GUEST = "guest"

ALL_ROLES: frozenset[str] = frozenset({ROLE_MEMBER, ROLE_GUILD_OFFICER, ROLE_ADMIN, ROLE_GUEST})

ROLE_HIERARCHY: tuple[str, ...] = (ROLE_MEMBER, ROLE_GUILD_OFFICER, ROLE_ADMIN)

ROLE_LABELS: tuple[tuple[str, str], ...] = (
    (ROLE_ADMIN, "Admin"),
    (ROLE_GUILD_OFFICER, "Guild Officer"),
    (ROLE_MEMBER, "Member"),
    (ROLE_GUEST, "Guest"),
)

SESSION_ROLE_KEY = "view_as_role"


def compute_actual_roles(user: "AbstractBaseUser | AnonymousUser | None") -> frozenset[str]:
    """Derive the set of roles a user actually holds.

    Anonymous visitors get ``{ROLE_GUEST}``. Superusers without a linked
    Member still get the full hierarchy set so emergency access is never
    dependent on Member data being correct.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return frozenset({ROLE_GUEST})

    from membership.models import Member as MemberModel

    member = MemberModel.objects.filter(user_id=user.pk).first()  # type: ignore[misc]

    roles: set[str] = set()
    if member is not None and member.status == MemberModel.Status.ACTIVE:
        if member.fog_role == MemberModel.FogRole.ADMIN:
            roles.update({ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER})
        elif member.fog_role == MemberModel.FogRole.GUILD_OFFICER:
            roles.update({ROLE_GUILD_OFFICER, ROLE_MEMBER})
        else:
            roles.add(ROLE_MEMBER)
    if getattr(user, "is_superuser", False):
        roles.update({ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER})
    if not roles:
        roles.add(ROLE_GUEST)
    return frozenset(roles)


def _highest_role(actual: frozenset[str]) -> str | None:
    for role in reversed(ROLE_HIERARCHY):
        if role in actual:
            return role
    return None


def _roles_up_to(picked: str, actual: frozenset[str]) -> frozenset[str]:
    """Return the subset of ``actual`` at or below ``picked`` in the hierarchy."""
    idx = ROLE_HIERARCHY.index(picked)
    allowed = frozenset(ROLE_HIERARCHY[: idx + 1])
    return actual & allowed


def _read_picked_role(session, actual: frozenset[str]) -> str | None:  # noqa: ANN001 — Django session is untyped
    """Return the session-picked role if the user is allowed to hold or preview it.

    Admins can preview any role (including Guest and Instructor) by picking
    it in the dropdown, even if it's not in their actual set. Non-admins are
    restricted to roles they already hold.
    """
    raw = session.get(SESSION_ROLE_KEY) if session is not None else None
    if raw is None:
        return None
    if raw in actual:
        return raw
    if ROLE_ADMIN in actual and raw in {ROLE_GUEST, ROLE_MEMBER, ROLE_GUILD_OFFICER, ROLE_ADMIN}:
        return raw
    return None


class ViewAs:
    """Effective-role wrapper attached to every request by the middleware."""

    def __init__(self, actual: frozenset[str], picked: str | None) -> None:
        self.actual = actual
        self.view_as_role = picked or _highest_role(actual)
        if picked is None:
            self.effective = actual
        elif picked in ROLE_HIERARCHY:
            self.effective = _roles_up_to(picked, actual)
        elif picked == ROLE_GUEST:
            self.effective = frozenset({ROLE_GUEST})
        else:
            self.effective = frozenset()

    def has(self, role: str) -> bool:
        return role in self.effective

    def has_actual(self, role: str) -> bool:
        return role in self.actual

    @property
    def is_admin(self) -> bool:
        return self.has(ROLE_ADMIN)

    @property
    def actual_is_admin(self) -> bool:
        return self.has_actual(ROLE_ADMIN)

    @property
    def actual_is_guild_officer(self) -> bool:
        return self.has_actual(ROLE_GUILD_OFFICER)

    @property
    def is_guild_officer(self) -> bool:
        return self.has(ROLE_GUILD_OFFICER)

    @property
    def is_member(self) -> bool:
        return self.has(ROLE_MEMBER)

    @property
    def is_guest(self) -> bool:
        return ROLE_GUEST in self.effective

    @property
    def has_member_role(self) -> bool:
        return ROLE_MEMBER in self.actual

    @property
    def show_dropdown(self) -> bool:
        """The "Viewing as" dropdown is for users who can preview other roles.

        Admins can preview every role. Guild officers can downgrade to Member.
        Plain members and guests don't see it.
        """
        if ROLE_ADMIN in self.actual:
            return True
        hierarchy_roles = self.actual & frozenset(ROLE_HIERARCHY)
        return len(hierarchy_roles - {ROLE_MEMBER}) > 0

    @property
    def current_label(self) -> str:
        """Human label for the currently-viewed role."""
        for name, label in ROLE_LABELS:
            if name == self.view_as_role:
                return label
        return ""

    @property
    def dropdown_options(self) -> list[dict[str, object]]:
        """Ordered ``{name, label, selected}`` dicts for the dropdown menu — highest role first.

        Admins see every role (so they can preview what each viewer type sees).
        Non-admins only see roles from their own ``actual`` set — for them the
        dropdown is a downgrade tool.
        """
        selectable: frozenset[str]
        if ROLE_ADMIN in self.actual:
            selectable = ALL_ROLES
        else:
            selectable = self.actual
        options = []
        for name, label in ROLE_LABELS:
            if name not in selectable:
                continue
            options.append({"name": name, "label": label, "selected": name == self.view_as_role})
        return options

    @classmethod
    def for_request(cls, request: "HttpRequest") -> "ViewAs":
        actual = compute_actual_roles(request.user)
        picked = _read_picked_role(getattr(request, "session", None), actual)
        return cls(actual=actual, picked=picked)


def fog_admin_required(view_func: _ViewFunc) -> _ViewFunc:
    """Decorator that allows users whose actual role includes admin.

    Wraps ``@login_required``: anonymous users get bounced to login; authenticated
    non-admins get a 403. The check honors ``has_actual`` so a session view-as
    preview can't grant or revoke access.
    """
    from functools import wraps
    from typing import Any

    from django.contrib.auth.decorators import login_required
    from django.http import HttpResponseForbidden

    @wraps(view_func)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        view_as = getattr(request, "view_as", None)
        if view_as is None or not view_as.has_actual(ROLE_ADMIN):
            return HttpResponseForbidden("Admin access required.")
        return view_func(request, *args, **kwargs)

    return login_required(wrapper)  # type: ignore[return-value]


def _capability_or_admin_required(capability: str, forbidden_message: str) -> Callable[[_ViewFunc], _ViewFunc]:
    """Decorator factory: pass fog-admins (actual role, preview-independent) or capability holders.

    Like :func:`fog_admin_required`, the admin check honors ``has_actual`` so a
    session view-as preview can't grant or revoke access. The capability check
    reads the user's linked member; a user with no member fails it.
    """
    from functools import wraps
    from typing import Any

    from django.contrib.auth.decorators import login_required
    from django.http import HttpResponseForbidden

    def decorator(view_func: _ViewFunc) -> _ViewFunc:
        @wraps(view_func)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
            view_as = getattr(request, "view_as", None)
            if view_as is not None and view_as.has_actual(ROLE_ADMIN):
                return view_func(request, *args, **kwargs)
            member = getattr(request.user, "member", None)
            if member is not None and member.has_admin_capability(capability):
                return view_func(request, *args, **kwargs)
            return HttpResponseForbidden(forbidden_message)

        return login_required(wrapper)  # type: ignore[return-value]

    return decorator


def billing_admin_access_required(view_func: _ViewFunc) -> _ViewFunc:
    """Decorator gating the admin Payments dashboard: fog-admins or Billing Administrators.

    The ``BILLING_APPROVER`` capability now gates the billing admin views (it was
    alert-only before). Views that touch Stripe credentials or billing config stay
    ``fog_admin_required`` — a Billing Administrator sees Overview / Open Tabs /
    Payments; the Settings and Stripe tabs stay admin-only.
    """
    from membership.models import AdminCapability

    return _capability_or_admin_required(
        AdminCapability.Capability.BILLING_APPROVER,
        "Billing dashboard access requires admin privileges or the Billing Administrator permission.",
    )(view_func)


def refund_authority_required(view_func: _ViewFunc) -> _ViewFunc:
    """Decorator gating refund actions: fog-admins or holders of the ``REFUNDS`` capability.

    A ``REFUNDS`` holder may refund ANY registration — that is what the grant
    means: a trusted duty handed to a person, not per-class scoping. The grant
    opens no pages on its own; it adds Refund/Retry actions on payment surfaces
    the holder can already reach.
    """
    from membership.models import AdminCapability

    return _capability_or_admin_required(
        AdminCapability.Capability.REFUNDS,
        "Refunds require admin privileges or the Refunds permission.",
    )(view_func)


def has_refund_authority(request: "HttpRequest") -> bool:
    """True when the request's user may issue refunds — fog-admin (actual) or ``REFUNDS`` holder.

    The template-side twin of :func:`refund_authority_required`: views compute it
    once so templates can gate Refund/Retry buttons without granting anything —
    the endpoints stay decorator-gated regardless.
    """
    from membership.models import AdminCapability

    view_as = getattr(request, "view_as", None)
    if view_as is not None and view_as.has_actual(ROLE_ADMIN):
        return True
    member = getattr(request.user, "member", None)
    return member is not None and member.has_admin_capability(AdminCapability.Capability.REFUNDS)


def has_billing_admin_access(request: "HttpRequest") -> bool:
    """True when the user may reach the billing admin pages — fog-admin (actual) or ``BILLING_APPROVER`` holder.

    The template-side twin of :func:`billing_admin_access_required`: it computes
    the visibility of the Admin Tools Payments and Reports cards without granting
    anything — the views stay decorator-gated regardless.
    """
    from membership.models import AdminCapability

    view_as = getattr(request, "view_as", None)
    if view_as is not None and view_as.has_actual(ROLE_ADMIN):
        return True
    member = getattr(request.user, "member", None)
    return member is not None and member.has_admin_capability(AdminCapability.Capability.BILLING_APPROVER)


class ViewAsMiddleware:
    """Attach ``request.view_as`` to every request.

    Must run after ``AuthenticationMiddleware`` (needs ``request.user``) and
    ``SessionMiddleware`` (needs ``request.session``).
    """

    def __init__(self, get_response) -> None:  # noqa: ANN001
        self.get_response = get_response

    def __call__(self, request: "HttpRequest"):
        request.view_as = ViewAs.for_request(request)  # type: ignore[attr-defined]
        return self.get_response(request)
