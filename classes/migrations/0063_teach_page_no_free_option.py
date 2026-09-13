"""Take the free option off the Host a Workshop page's default copy (#368 item 5).

v1.60.0 removed the free class option from both composers: every class costs at least
$1.00. Three ``ClassSettings`` defaults still promised one: the What You Get card
"Free, Paid, Or On Sale", the "Can I Charge for It?" answer ("You can also run it free."),
and the money split note ("Run it free and there is nothing to split."). The defaults
change here, and a row still carrying the old default text moves with them.

Forward, per field: a value equal to the old default becomes the new default; anything
else is an admin's own copy and is left alone. Reverse: a value equal to the new default
goes back to the old default; anything else is left alone.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations, models

OLD_TEACH_PAGE_FEATURES = """\
A Page Worth Sharing: Your workshop gets its own page with a wide banner photo, a gallery, the schedule, your bio, and a sign up panel that follows the reader down the page.
Your Words, Your Photos: Write it the way you would say it. Add a banner and as many gallery shots as you like, and choose which part of each photo shows.
Sign Ups That Run Themselves: When it fills up, people join a waitlist. The moment a seat opens, the next person is offered it and held for three days.
Everyone On One Screen: See who is coming, mark someone as paid, move a person to another date, and email the whole group without leaving the page.
Free, Paid, Or On Sale: Run it free, set a price with a member discount, or put it on sale and the new price shows up everywhere on its own.
Run It Again In One Click: Went well? Make a copy with new dates and keep everything else exactly as it was."""

NEW_TEACH_PAGE_FEATURES = """\
A Page Worth Sharing: Your workshop gets its own page with a wide banner photo, a gallery, the schedule, your bio, and a sign up panel that follows the reader down the page.
Your Words, Your Photos: Write it the way you would say it. Add a banner and as many gallery shots as you like, and choose which part of each photo shows.
Sign Ups That Run Themselves: When it fills up, people join a waitlist. The moment a seat opens, the next person is offered it and held for three days.
Everyone On One Screen: See who is coming, mark someone as paid, move a person to another date, and email the whole group without leaving the page.
Paid Or On Sale: Set a price with a member discount, or put it on sale and the new price shows up everywhere on its own.
Run It Again In One Click: Went well? Make a copy with new dates and keep everything else exactly as it was."""

OLD_TEACH_PAGE_FAQ = (
    "<h3>Do I Need to Be an Expert?</h3>"
    "<p>No. You need to be safe and clear. Plenty of great workshops are run by people two steps ahead of "
    "everyone else in the room.</p>"
    "<h3>How Long Until I Hear Back?</h3>"
    "<p>An admin usually gets to it within a week. You can check this page any time to see where things stand.</p>"
    "<h3>Can I Charge for It?</h3>"
    "<p>Yes. You set the price and an optional member discount when you build the page. You can also run it free.</p>"
    "<h3>What If Nobody Signs Up?</h3>"
    "<p>You can cancel from your dashboard and everyone who signed up is told automatically. Nothing is stuck.</p>"
)

NEW_TEACH_PAGE_FAQ = (
    "<h3>Do I Need to Be an Expert?</h3>"
    "<p>No. You need to be safe and clear. Plenty of great workshops are run by people two steps ahead of "
    "everyone else in the room.</p>"
    "<h3>How Long Until I Hear Back?</h3>"
    "<p>An admin usually gets to it within a week. You can check this page any time to see where things stand.</p>"
    "<h3>Can I Charge for It?</h3>"
    "<p>Yes. You set the price and an optional member discount when you build the page. Every class costs at least "
    "$1.00.</p>"
    "<h3>What If Nobody Signs Up?</h3>"
    "<p>You can cancel from your dashboard and everyone who signed up is told automatically. Nothing is stuck.</p>"
)

OLD_TEACH_PAGE_SPLIT_NOTE = "Every paid class splits the same way. Run it free and there is nothing to split."
NEW_TEACH_PAGE_SPLIT_NOTE = "Every class splits the same way."

# field name -> (the default before this migration, the default after it)
COPY_FIELDS: dict[str, tuple[str, str]] = {
    "teach_page_features": (OLD_TEACH_PAGE_FEATURES, NEW_TEACH_PAGE_FEATURES),
    "teach_page_faq": (OLD_TEACH_PAGE_FAQ, NEW_TEACH_PAGE_FAQ),
    "teach_page_split_note": (OLD_TEACH_PAGE_SPLIT_NOTE, NEW_TEACH_PAGE_SPLIT_NOTE),
}


def drop_free_option_forward(apps: Any, schema_editor: Any) -> None:
    """Old default text becomes the new default; an admin's own copy is left alone."""
    ClassSettings = apps.get_model("classes", "ClassSettings")
    for row in ClassSettings.objects.all():
        changed: list[str] = []
        for name, (old, new) in COPY_FIELDS.items():
            if getattr(row, name) == old:
                setattr(row, name, new)
                changed.append(name)
        if changed:
            row.save(update_fields=changed)


def drop_free_option_reverse(apps: Any, schema_editor: Any) -> None:
    """New default text goes back to the old default; anything else is left alone."""
    ClassSettings = apps.get_model("classes", "ClassSettings")
    for row in ClassSettings.objects.all():
        changed: list[str] = []
        for name, (old, new) in COPY_FIELDS.items():
            if getattr(row, name) == new:
                setattr(row, name, old)
                changed.append(name)
        if changed:
            row.save(update_fields=changed)


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0062_convert_teach_page_markdown"),
    ]

    operations = [
        migrations.AlterField(
            model_name="classsettings",
            name="teach_page_faq",
            field=models.TextField(
                blank=True,
                default=NEW_TEACH_PAGE_FAQ,
                help_text="The Common Questions section. Make each question a heading and write the answer under it: every heading becomes one question that opens and closes. Use the toolbar for bold, lists and links. Leave blank to hide the section.",
            ),
        ),
        migrations.AlterField(
            model_name="classsettings",
            name="teach_page_features",
            field=models.TextField(
                blank=True,
                default=NEW_TEACH_PAGE_FEATURES,
                help_text="The What You Get cards, one per line, written as Title: description. Six lines make two even rows. Icons are decoration and follow the position of the line, so reordering the lines moves the icons with them. Leave blank to hide the section.",
            ),
        ),
        migrations.AlterField(
            model_name="classsettings",
            name="teach_page_split_note",
            field=models.TextField(
                blank=True,
                default=NEW_TEACH_PAGE_SPLIT_NOTE,
                help_text="The line under the split. Plain text. Leave blank to show no line.",
            ),
        ),
        migrations.RunPython(drop_free_option_forward, drop_free_option_reverse),
    ]
