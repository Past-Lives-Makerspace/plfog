"""Deactivate the three demo registration questions that leaked onto real environments.

These exact prompts are seeded by ``manage.py demo_data`` (a dev/demo command).
``demo_data`` was run on a real environment at some point, leaving three *global*
``RegistrationQuestion`` rows that then surfaced on every registrant's form even
though no admin intentionally configured them. Custom questions are CMS-managed;
this clears the leftover demo rows.

Forward only flips ``is_active`` to False for rows whose prompt exactly matches a
demo prompt *and* is currently active — admin-edited prompts (now legitimately
"real") no longer match and are left alone. Historical answers are untouched
(``RegistrationAnswer.question`` is ``PROTECT``). Reverse reactivates the same rows.
"""

from __future__ import annotations

from django.db import migrations

DEMO_PROMPTS = [
    "How did you hear about this class?",
    "Do you have any allergies or medical conditions we should know about?",
    "What's your experience level?",
]


def deactivate_demo_questions(apps, schema_editor) -> None:
    RegistrationQuestion = apps.get_model("classes", "RegistrationQuestion")
    RegistrationQuestion.objects.filter(prompt__in=DEMO_PROMPTS, is_active=True).update(is_active=False)


def reactivate_demo_questions(apps, schema_editor) -> None:
    RegistrationQuestion = apps.get_model("classes", "RegistrationQuestion")
    RegistrationQuestion.objects.filter(prompt__in=DEMO_PROMPTS, is_active=False).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0036_backfill_series_scheduling_type"),
    ]

    operations = [
        migrations.RunPython(deactivate_demo_questions, reactivate_demo_questions),
    ]
