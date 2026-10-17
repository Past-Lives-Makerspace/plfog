"""A published single class with several dates becomes one published run per date (#823).

From this release, ``ClassOffering.publish`` and the admin composer's save split such a class
themselves (``ClassOffering.split_dates_into_runs``). This applies the same split to the classes
that went live before it: production held two (686 Make a Sushi Set, 687 Make a Cup).

Each later session moves onto a run cloned the way ``duplicate_as_new_run`` clones (the row's
columns, a ``<slug>-run`` slug, Eventbrite and legacy fields cleared, the instructors, the gallery
and the FAQ rows copied) but kept published, with the source's ``published_at`` and
``approved_by``, and stamped announced so #classes is not posted once per run. Model methods do
not exist on historical models, so the clone is written out here, and the seat holding statuses
are frozen so a later change to the live constant cannot move this migration. A class anyone
holds a seat on is never touched.

Each run gets a ``class_published`` activity row carrying :data:`MARKER` and its source's pk.
Reverse reads those rows: it moves each run's session back to its source and deletes the run,
leaving any run somebody has since signed up for where it is.

Add only, so the previous release keeps serving while this applies (STANDARDS.md section 10):
the runs are ordinary published classes it already knows how to show.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations
from django.db.models import Count, Exists, OuterRef
from django.utils import timezone

MARKER = "split_multi_date_single_classes_0093"

SEAT_HOLDING_STATUSES = ("confirmed", "pending", "waitlisted")


def classes_to_split(apps: Any) -> list[int]:
    """The pks of every published, fixed, single class with more than one date and no seat held."""
    ClassOffering = apps.get_model("classes", "ClassOffering")
    Registration = apps.get_model("classes", "Registration")
    seat_held = Registration.objects.filter(class_offering_id=OuterRef("pk"), status__in=SEAT_HOLDING_STATUSES)
    return list(
        ClassOffering.objects.filter(
            status="published",
            scheduling_type="single_session",
            scheduling_model="fixed",
        )
        .annotate(session_total=Count("sessions"), seat_held=Exists(seat_held))
        .filter(session_total__gt=1, seat_held=False)
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def _free_slug(ClassOffering: Any, base: str) -> str:
    candidate = base
    n = 1
    while ClassOffering.objects.filter(slug=candidate).exists():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def _clone_run(apps: Any, source_pk: int, announced_at: Any) -> Any:
    """A published copy of ``source_pk`` with no sessions, carrying what Run it again carries."""
    ClassOffering = apps.get_model("classes", "ClassOffering")
    ClassImage = apps.get_model("classes", "ClassImage")
    ClassFaq = apps.get_model("classes", "ClassFaq")
    ClassInstructor = apps.get_model("classes", "ClassInstructor")

    run = ClassOffering.objects.get(pk=source_pk)
    run.pk = None
    run.id = None
    run._state.adding = True
    run.slug = _free_slug(ClassOffering, f"{run.slug}-run")
    run.legacy_cms_id = ""
    run.cancelled_at = None
    run.cancelled_by = None
    run.cancellation_reason = ""
    run.eventbrite_event_id = ""
    run.eventbrite_ticket_class_id = ""
    run.eventbrite_sync_state = "idle"
    run.eventbrite_sync_error = ""
    run.eventbrite_synced_at = None
    run.eventbrite_published = False
    run.eventbrite_enabled = False
    run.eventbrite_rules_agreed_by = None
    run.eventbrite_rules_agreed_at = None
    run.channel_announced_at = announced_at
    run.save()

    ClassImage.objects.bulk_create(
        ClassImage(
            class_offering=run,
            image=image.image.name,
            alt_text=image.alt_text,
            sort_order=image.sort_order,
            focus_x=image.focus_x,
            focus_y=image.focus_y,
        )
        for image in ClassImage.objects.filter(class_offering_id=source_pk).order_by("sort_order", "created_at")
    )
    # Migration 0086 deleted every row asking a locked question, and the class page hides any
    # such row anyway, so every row the source has is copied.
    ClassFaq.objects.bulk_create(
        ClassFaq(class_offering=run, question=faq.question, answer=faq.answer, sort_order=faq.sort_order)
        for faq in ClassFaq.objects.filter(class_offering_id=source_pk).order_by("sort_order", "pk")
    )
    ClassInstructor.objects.bulk_create(
        ClassInstructor(class_offering=run, member_id=row.member_id, added_at=row.added_at)
        for row in ClassInstructor.objects.filter(class_offering_id=source_pk).order_by("added_at", "pk")
    )
    return run


def split_all(apps: Any) -> list[tuple[int, int]]:
    """Split every class :func:`classes_to_split` names; return ``(source pk, run pk)`` per run made."""
    ClassOffering = apps.get_model("classes", "ClassOffering")
    ClassSession = apps.get_model("classes", "ClassSession")
    CmsActivity = apps.get_model("classes", "CmsActivity")
    made: list[tuple[int, int]] = []
    for source_pk in classes_to_split(apps):
        source = ClassOffering.objects.get(pk=source_pk)
        announced_at = source.channel_announced_at or timezone.now()
        sessions = list(ClassSession.objects.filter(class_offering_id=source_pk).order_by("starts_at", "pk"))
        for session in sessions[1:]:
            run = _clone_run(apps, source_pk, announced_at)
            ClassSession.objects.filter(pk=session.pk).update(class_offering_id=run.pk)
            CmsActivity.objects.create(
                kind="class_published",
                class_offering_id=run.pk,
                payload={"marker": MARKER, "split_from": source_pk},
            )
            made.append((source_pk, run.pk))
    return made


def merge_all(apps: Any) -> list[int]:
    """Undo :func:`split_all`: sessions go back to their source and the runs go; return the pks removed."""
    ClassOffering = apps.get_model("classes", "ClassOffering")
    ClassSession = apps.get_model("classes", "ClassSession")
    CmsActivity = apps.get_model("classes", "CmsActivity")
    Registration = apps.get_model("classes", "Registration")
    removed: list[int] = []
    for row in CmsActivity.objects.filter(payload__marker=MARKER).order_by("pk"):
        run_pk = row.class_offering_id
        if run_pk is None or Registration.objects.filter(class_offering_id=run_pk).exists():
            continue
        ClassSession.objects.filter(class_offering_id=run_pk).update(class_offering_id=row.payload["split_from"])
        ClassOffering.objects.filter(pk=run_pk).delete()
        row.delete()
        removed.append(run_pk)
    return removed


def forwards(apps: Any, schema_editor: Any) -> None:
    split_all(apps)


def backwards(apps: Any, schema_editor: Any) -> None:
    merge_all(apps)


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0092_classinstructor"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
