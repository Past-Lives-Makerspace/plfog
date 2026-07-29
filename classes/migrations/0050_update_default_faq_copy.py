"""Update any ClassFaq rows that still carry the old site-default text.

When the FAQ editor is first saved for a class, the default text is materialised
as real ClassFaq rows.  Those rows are now stale.  We update them in-place so
every class that was never customised picks up the corrected copy.

Rows whose answer differs even slightly from the old default are left untouched —
those were intentionally customised by the class organiser.
"""

from __future__ import annotations

from django.db import migrations

OLD_Q1 = "What's your cancellation policy?"
OLD_A1 = (
    "Cancel up to 7 days before the first session for a full refund. Within 7 days, "
    "we can usually transfer your spot to a future class or to someone on the waitlist."
)
NEW_A1 = (
    "We know plans change, but late cancellations and no-shows leave empty seats that could've "
    "gone to someone on the waitlist, and instructors still prep and hold space for every "
    "registered student.\n\n"
    "Canceling with 48+ hours' notice: No fee. Please cancel by emailing studios@pastlives.space "
    "as early as possible so we can offer your spot to someone on the waitlist.\n\n"
    "Canceling with less than 48 hours' notice, or no-shows: A $50 fee applies. Of this, $35 goes "
    "to the instructor for their held time and prep, and $15 goes to Past Lives Makerspace for "
    "administrative processing.\n\n"
    "Emergencies: We understand things come up. Emergency exceptions are handled case-by-case. "
    "Please reach out to us directly, and we'll work with you.\n\n"
    "How to cancel: Email studios@pastlives.space"
)

OLD_Q2 = "Is the space accessible?"
OLD_A2 = (
    "Yes — our studio has step-free entry, accessible restrooms, and adjustable-height "
    "workstations available on request. Email info@pastlives.space with any specific "
    "needs and we'll make it work."
)
NEW_A2 = (
    "We have a ramp to reach our first floor, but please note that Past Lives Makerspace is not "
    "currently ADA accessible, and we do not have ADA accessible restrooms at this time. If you "
    "have questions about accessing a specific class or space, please reach out to us at "
    "studios@pastlives.space and we'll do our best to help."
)

OLD_Q3 = "What if I've never done this before?"
NEW_Q3 = "Do I need any prior experience or skill level?"
OLD_A3 = (
    "Most of our classes are designed for beginners and welcome curious newcomers. "
    "Check the Prerequisites section above for any required experience."
)
NEW_A3 = (
    "Generally, no! Our classes are designed for all skill levels, from complete beginners to "
    "those looking to refine existing techniques. Each class description will note if any prior "
    "experience is recommended."
)


def update_default_faqs(apps, schema_editor):
    ClassFaq = apps.get_model("classes", "ClassFaq")

    ClassFaq.objects.filter(question=OLD_Q1, answer=OLD_A1).update(answer=NEW_A1)
    ClassFaq.objects.filter(question=OLD_Q2, answer=OLD_A2).update(answer=NEW_A2)
    ClassFaq.objects.filter(question=OLD_Q3, answer=OLD_A3).update(question=NEW_Q3, answer=NEW_A3)


def reverse_default_faqs(apps, schema_editor):
    ClassFaq = apps.get_model("classes", "ClassFaq")

    ClassFaq.objects.filter(question=OLD_Q1, answer=NEW_A1).update(answer=OLD_A1)
    ClassFaq.objects.filter(question=OLD_Q2, answer=NEW_A2).update(answer=OLD_A2)
    ClassFaq.objects.filter(question=NEW_Q3, answer=NEW_A3).update(question=OLD_Q3, answer=OLD_A3)


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0049_classoffering_sale_fields"),
    ]

    operations = [
        migrations.RunPython(update_default_faqs, reverse_code=reverse_default_faqs),
    ]
