"""Delete the ClassFaq rows that ask a locked question (cancellation, accessibility).

Those two questions became site policy (``LOCKED_CLASS_FAQS``): every class page shows the
code's copy first, so a class's own copy of either is dropped, whatever its answer (prod
held the current copy on 37 classes and an older cancellation answer on 553 and 667). The
match is ``classes.models._faq_key``'s, frozen here with the questions and their copy so a
later change to either cannot move this migration.

Reverse puts the locked questions back as rows, as the previous release expects them: on
every class that has FAQ rows, each locked question it does not already ask goes in at the
top with the frozen copy, and the class's own rows shift down to make room. A class with no
rows is left alone, since the previous release shows the defaults there anyway. The older
cancellation answer on two classes is not restored; they get the current copy.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from typing import Any

from django.db import migrations

LOCKED_FAQS: list[dict[str, str]] = [
    {
        "question": "What's your cancellation policy?",
        "answer": (
            "We know plans change, but late cancellations and no-shows leave empty seats that could've "
            "gone to someone on the waitlist, and instructors still prep materials and hold space for "
            "every registered student. Here's how we handle it:\n\n"
            "Canceling with 48+ hours' notice: No fee. Please cancel by emailing classes@pastlives.space "
            "as early as possible so we can offer your spot to someone on the waitlist.\n\n"
            "Canceling with less than 48 hours' notice, or no-shows: We do not offer refunds for late "
            "cancellations and no-shows.\n\n"
            "Emergencies: We understand things come up. Emergency exceptions are handled case-by-case. "
            "Please reach out to us directly, and we'll work with you.\n\n"
            "How to cancel: Email classes@pastlives.space"
        ),
    },
    {
        "question": "Is the space accessible?",
        "answer": (
            "We have a ramp to reach our first floor, but please note that Past Lives Makerspace is not "
            "currently ADA accessible, and we do not have ADA accessible restrooms at this time. If you "
            "have questions about accessing a specific class or space, please reach out to us at "
            "studios@pastlives.space and we'll do our best to help."
        ),
    },
]

_APOSTROPHES = str.maketrans({"‘": "'", "’": "'"})


def faq_key(question: str) -> str:
    """Frozen copy of ``classes.models._faq_key``."""
    text = unicodedata.normalize("NFKC", question).translate(_APOSTROPHES)
    return " ".join(text.split()).casefold().rstrip("?").rstrip()


LOCKED_KEYS = frozenset(faq_key(faq["question"]) for faq in LOCKED_FAQS)


def delete_locked_faq_rows(apps: Any, schema_editor: Any) -> None:
    ClassFaq = apps.get_model("classes", "ClassFaq")
    locked = [pk for pk, question in ClassFaq.objects.values_list("pk", "question") if faq_key(question) in LOCKED_KEYS]
    ClassFaq.objects.filter(pk__in=locked).delete()


def restore_locked_faq_rows(apps: Any, schema_editor: Any) -> None:
    ClassFaq = apps.get_model("classes", "ClassFaq")
    by_class: dict[int, list[Any]] = defaultdict(list)
    for faq in ClassFaq.objects.order_by("class_offering_id", "sort_order", "pk"):
        by_class[faq.class_offering_id].append(faq)
    for class_id, rows in by_class.items():
        asked = {faq_key(row.question) for row in rows}
        missing = [faq for faq in LOCKED_FAQS if faq_key(faq["question"]) not in asked]
        if not missing:
            continue
        for row in rows:
            row.sort_order += len(missing)
        ClassFaq.objects.bulk_update(rows, ["sort_order"])
        ClassFaq.objects.bulk_create(
            ClassFaq(class_offering_id=class_id, question=faq["question"], answer=faq["answer"], sort_order=i)
            for i, faq in enumerate(missing)
        )


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0085_classoffering_eventbrite_published"),
    ]

    operations = [
        migrations.RunPython(delete_locked_faq_rows, reverse_code=restore_locked_faq_rows),
    ]
