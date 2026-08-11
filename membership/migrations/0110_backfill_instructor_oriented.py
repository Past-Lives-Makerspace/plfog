"""Backfill the instructor-orientation unlock for grandfathered members (Spec D §4).

Base predicate ``_GRANDFATHERED``: any :class:`classes.ClassOffering` row (the
``classes`` reverse join matches every status — draft, pending, published,
cancelled, archived — so "any status" needs no status filter) OR a non-empty
``instructor_slug`` (``apply_admin_role("instructor")`` is an explicit admin
grant of teaching).

Forward adds ``instructor_oriented_at__isnull=True`` so a re-run — or an admin
grant that landed between migrations — never stomps an existing timestamp.

Reverse clears ``instructor_oriented_at`` for the base predicate WITHOUT the
isnull guard (which would match zero rows after the forward). Honest caveat:
the reverse also clears anyone in that set who was unlocked by other means
before/after the forward run — acceptable for a rollback (they redo a
two-minute page), and per house rules a real reverse beats ``RunPython.noop``.
"""

from django.db import migrations
from django.db.models import Q
from django.utils import timezone

_GRANDFATHERED = Q(classes__isnull=False) | ~Q(instructor_slug="")


def _unlock_grandfathered(apps, schema_editor):
    """Stamp instructor_oriented_at=now() on still-locked grandfathered members."""
    member_model = apps.get_model("membership", "Member")
    grandfathered_pks = member_model.objects.filter(_GRANDFATHERED).values_list("pk", flat=True).distinct()
    member_model.objects.filter(pk__in=list(grandfathered_pks), instructor_oriented_at__isnull=True).update(
        instructor_oriented_at=timezone.now()
    )


def _relock_grandfathered(apps, schema_editor):
    """Clear instructor_oriented_at for every base-predicate member (documented over-clear)."""
    member_model = apps.get_model("membership", "Member")
    grandfathered_pks = member_model.objects.filter(_GRANDFATHERED).values_list("pk", flat=True).distinct()
    member_model.objects.filter(pk__in=list(grandfathered_pks)).update(instructor_oriented_at=None)


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0109_member_instructor_oriented_at"),
        # The backfill queries ClassOffering through the ``classes`` reverse join,
        # so the classes app's current head must be applied first.
        ("classes", "0051_alter_registration_wants_newsletter_help_text"),
    ]

    operations = [
        migrations.RunPython(_unlock_grandfathered, _relock_grandfathered),
    ]
