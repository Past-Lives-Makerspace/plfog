"""Signal receivers for the classes app, connected in ``ClassesConfig.ready``."""

from __future__ import annotations

from typing import Any

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from classes.models import ClassApproval


@receiver(pre_delete, sender=ClassApproval, dispatch_uid="classes.clear_review_bells_on_gate_delete")
def clear_review_bells_on_gate_delete(sender: type[ClassApproval], instance: ClassApproval, **kwargs: Any) -> None:
    """Mark read the bell entries asking about a review gate as it is deleted (#894).

    A signal rather than a ``delete`` override, because a gate goes away three ways: one row,
    a queryset (resubmit, guild move, withdraw, restore, unpublish) and a cascade from
    deleting the class, and only ``pre_delete`` fires for all three.
    """
    ClassApproval.mark_review_bells_read([instance.token])
