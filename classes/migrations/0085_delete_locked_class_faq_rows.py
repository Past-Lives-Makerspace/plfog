"""Delete the ClassFaq rows that ask a locked question (cancellation, accessibility).

Those two questions became site policy (``LOCKED_CLASS_FAQS``): every class page shows the
code's copy first, so a class's own copy of either is dropped, whatever its answer (prod
held the current copy on 37 classes and an older cancellation answer on 553 and 667). The
match ignores case and surrounding space, as ``classes.models.is_locked_class_faq`` does;
the questions are frozen here so later copy changes cannot move this migration.

Reverse is a no-op, by the spec: the deleted copies were the defaults or older versions of
them, and the locked copy replaces them on every page either way.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

LOCKED_QUESTIONS = frozenset({"what's your cancellation policy?", "is the space accessible?"})


def delete_locked_faq_rows(apps: Any, schema_editor: Any) -> None:
    ClassFaq = apps.get_model("classes", "ClassFaq")
    locked = [
        pk
        for pk, question in ClassFaq.objects.values_list("pk", "question")
        if question.strip().casefold() in LOCKED_QUESTIONS
    ]
    ClassFaq.objects.filter(pk__in=locked).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0084_classoffering_eventbrite_category"),
    ]

    operations = [
        migrations.RunPython(delete_locked_faq_rows, reverse_code=migrations.RunPython.noop),
    ]
