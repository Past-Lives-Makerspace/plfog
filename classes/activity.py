"""Helpers for writing CmsActivity rows from the rest of the classes app.

Every meaningful workflow point calls ``log(kind, ...)`` to append a row
to the admin Activity feed. Keep this module dependency-light so other
modules can import it without circular-import risk.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

    from classes.models import ClassOffering, CmsActivity, Registration


def log(
    kind: str,
    *,
    class_offering: "ClassOffering | None" = None,
    registration: "Registration | None" = None,
    actor: "AbstractBaseUser | None" = None,
    payload: dict[str, Any] | None = None,
) -> "CmsActivity":
    """Create a single CmsActivity row. Safe to call from save()/signals."""
    from classes.models import CmsActivity

    return CmsActivity.objects.create(
        kind=kind,
        class_offering=class_offering,
        registration=registration,
        actor=actor if (actor is not None and getattr(actor, "pk", None)) else None,
        payload=payload or {},
    )
