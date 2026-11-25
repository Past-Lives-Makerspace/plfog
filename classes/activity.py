"""Helpers for writing CmsActivity rows from the rest of the classes app.

Every meaningful workflow point calls ``log(kind, ...)`` to append a row
to the admin Activity feed. Keep this module dependency-light so other
modules can import it without circular-import risk.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from classes.models import ClassOffering, CmsActivity, Registration

# Map CmsActivity kind strings → SiteActivity kind strings.
# Only the cross-site subset is listed; unmapped kinds are intentionally excluded.
_SITE_KIND_MAP: dict[str, str] = {
    "class_published": "class_published",
    "class_submitted": "class_submitted",
    "class_approved": "class_approved",
    # A cancel is the member-facing event; an archive is quiet housekeeping and is
    # deliberately NOT mirrored into the site feed.
    "class_cancelled": "class_cancelled",
    # A seat is "registered" once it is confirmed (#1330): a free one as it is made, a paid one
    # when its payment lands. A checkout still waiting on payment is not mirrored (see log()).
    "registration_created": "class_registered",
    "registration_confirmed": "class_registered",
    # A staff promote from the waitlist confirms the seat at once, logged in place of
    # registration_confirmed, so it is the seat's one "registered" (#1330 follow-up).
    "waitlist_promoted": "class_registered",
    "registration_cancelled": "class_registration_cancelled",
    "registration_refunded": "refund_issued",
    "waitlist_joined": "class_waitlist_joined",
}


def _mirror_to_site_activity(
    kind: str,
    class_offering: "ClassOffering | None",
    registration: "Registration | None",
    actor: "User | None",
    payload: dict[str, Any] | None,
) -> None:
    """Mirror a CmsActivity event into SiteActivity if it belongs in the site feed.

    A registration made awaiting payment is not a registration yet (#1330): it is mirrored when
    the payment confirms it, and an abandoned checkout mirrors nothing.
    """
    site_kind = _SITE_KIND_MAP.get(kind)
    if site_kind is None:
        return
    from classes.models import RegistrationStatus
    from core.models import SiteActivity

    if (
        kind == "registration_created"
        and registration is not None
        and registration.status == RegistrationStatus.PENDING
    ):
        return

    target = registration or class_offering
    SiteActivity.log(site_kind, actor=actor, target=target, payload=payload or {})


def log(
    kind: str,
    *,
    class_offering: "ClassOffering | None" = None,
    registration: "Registration | None" = None,
    actor: "User | None" = None,
    payload: dict[str, Any] | None = None,
    mirror: bool = True,
) -> "CmsActivity":
    """Create a single CmsActivity row. Safe to call from save()/signals.

    ``mirror=False`` keeps the row out of the site feed even when its kind is mirrored: a cancel
    of a seat that was never confirmed (#1330).
    """
    from classes.models import CmsActivity

    if mirror:
        _mirror_to_site_activity(kind, class_offering, registration, actor, payload)
    return CmsActivity.objects.create(
        kind=kind,
        class_offering=class_offering,
        registration=registration,
        actor=actor if (actor is not None and getattr(actor, "pk", None)) else None,
        payload=payload or {},
    )
