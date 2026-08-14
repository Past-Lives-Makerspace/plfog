"""Refresh the cancellation-policy FAQ on classes that still carry the old default.

When a class first saves its FAQ editor, the site-default text is materialised as
real ClassFaq rows. The cancellation policy changed: no more late-cancel fee, and
the contact address moved from studios@ to classes@. We update those rows in-place
so every class that never customised the answer picks up the corrected copy.

Rows whose answer differs even slightly from the old default are left untouched —
those were intentionally reworded by the class organiser.
"""

from __future__ import annotations

from django.db import migrations

Q1 = "What's your cancellation policy?"

OLD_A1 = (
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

NEW_A1 = (
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
)


def update_cancellation_faq(apps, schema_editor):
    ClassFaq = apps.get_model("classes", "ClassFaq")
    ClassFaq.objects.filter(question=Q1, answer=OLD_A1).update(answer=NEW_A1)


def reverse_cancellation_faq(apps, schema_editor):
    ClassFaq = apps.get_model("classes", "ClassFaq")
    ClassFaq.objects.filter(question=Q1, answer=NEW_A1).update(answer=OLD_A1)


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0051_alter_registration_wants_newsletter_help_text"),
    ]

    operations = [
        migrations.RunPython(update_cancellation_faq, reverse_code=reverse_cancellation_faq),
    ]
