"""Signal receivers for the classes app, connected in ``ClassesConfig.ready``."""

from __future__ import annotations

from typing import Any

from django.db.models.signals import post_delete, pre_delete
from django.dispatch import receiver

from classes.models import ClassApproval, ClassOffering, ClassRecord, ClassSession


@receiver(pre_delete, sender=ClassApproval, dispatch_uid="classes.clear_review_bells_on_gate_delete")
def clear_review_bells_on_gate_delete(sender: type[ClassApproval], instance: ClassApproval, **kwargs: Any) -> None:
    """Mark read the bell entries asking about a review gate as it is deleted (#894).

    A signal rather than a ``delete`` override, because a gate goes away three ways: one row,
    a queryset (resubmit, guild move, withdraw, restore, unpublish) and a cascade from
    deleting the class, and only ``pre_delete`` fires for all three.
    """
    ClassApproval.mark_review_bells_read([instance.token])


@receiver(post_delete, sender=ClassOffering, dispatch_uid="classes.class_record_follows_deleted_run")
def class_record_follows_deleted_run(sender: type[ClassOffering], instance: ClassOffering, **kwargs: Any) -> None:
    """Delete a run's class with its last run, or refile it, as the run goes (#1260).

    A signal rather than a ``delete`` override, because a run is deleted one at a time and as a
    queryset (the demo teardowns and the demo scrub), and ``post_delete`` fires for both, after
    the rows are gone, so the class sees only the runs that remain.
    """
    ClassRecord.follow_runs(instance.class_record_id)


@receiver(post_delete, sender=ClassSession, dispatch_uid="classes.class_state_follows_deleted_session")
def class_state_follows_deleted_session(sender: type[ClassSession], instance: ClassSession, **kwargs: Any) -> None:
    """Recompute the class's state when one of its runs loses a date (#1260).

    A signal rather than a ``delete`` override, because dates are deleted one at a time and as a
    queryset (the composer replacing a run's dates), and only ``post_delete`` fires for both. A
    saved date recomputes in ``ClassSession.save``. When the delete began at the run (a cascade),
    the run row is still there and about to go, so ``ClassRecord.follow_runs`` recomputes once it
    has gone instead.
    """
    origin = kwargs.get("origin")
    if isinstance(origin, ClassOffering) or getattr(origin, "model", None) is ClassOffering:
        return
    instance.refresh_class_state()
