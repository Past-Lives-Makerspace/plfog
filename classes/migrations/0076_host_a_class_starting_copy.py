"""Move the Host a Class page's starting words from "workshop" to "class".

A field an admin already edited keeps their words: only a field still holding the old
starting copy, word for word, is rewritten. Reversing puts the old copy back the same way.
The strings are frozen here so later edits to the model defaults cannot change history.
"""

from django.db import migrations

#: field name -> (old starting copy, new starting copy)
COPY_FIELDS = {
    "teach_page_lead": (
        "Run a workshop or a class for the people already in the shop. Show a technique, teach "
        "a skill, or just get folks making things together. You get a page for it, a sign up "
        "list, and the tools to run the day.",
        "Run a class for the people already in the shop. Show a technique, teach a skill, or "
        "just get folks making things together. You get a page for it, a sign up list, and the "
        "tools to run the day.",
    ),
    "teach_page_features": (
        "A Page Worth Sharing: Your workshop gets its own page with a wide banner photo, a "
        "gallery, the schedule, your bio, and a sign up panel that follows the reader down "
        "the page.\n"
        "Your Words, Your Photos: Write it the way you would say it. Add a banner and as "
        "many gallery shots as you like, and choose which part of each photo shows.\n"
        "Sign Ups That Run Themselves: When it fills up, people join a waitlist. The moment "
        "a seat opens, the next person is offered it and held for three days.\n"
        "Everyone On One Screen: See who is coming, mark someone as paid, move a person to "
        "another date, and email the whole group without leaving the page.\n"
        "Paid Or On Sale: Set a price, or put it on sale and the new price shows up "
        "everywhere on its own.\n"
        "Run It Again In One Click: Went well? Make a copy with new dates and keep "
        "everything else exactly as it was.",
        "A Page Worth Sharing: Your class gets its own page with a wide banner photo, a "
        "gallery, the schedule, your bio, and a sign up panel that follows the reader down "
        "the page.\n"
        "Your Words, Your Photos: Write it the way you would say it. Add a banner and as "
        "many gallery shots as you like, and choose which part of each photo shows.\n"
        "Sign Ups That Run Themselves: When it fills up, people join a waitlist. The moment "
        "a seat opens, the next person is offered it and held for three days.\n"
        "Everyone On One Screen: See who is coming, mark someone as paid, move a person to "
        "another date, and email the whole group without leaving the page.\n"
        "Paid Or On Sale: Set a price, or put it on sale and the new price shows up "
        "everywhere on its own.\n"
        "Run It Again In One Click: Went well? Make a copy with new dates and keep "
        "everything else exactly as it was.",
    ),
    "teach_page_faq": (
        "<h3>Do I Need to Be an Expert?</h3><p>No. You need to be safe and clear. Plenty of "
        "great workshops are run by people two steps ahead of everyone else in the "
        "room.</p><h3>How Long Until I Hear Back?</h3><p>An admin usually gets to it within a "
        "week. You can check this page any time to see where things stand.</p><h3>Can I Charge "
        "for It?</h3><p>Yes. You set the price when you build the page. Every class costs at "
        "least $1.00.</p><h3>What If Nobody Signs Up?</h3><p>You can cancel from your dashboard "
        "and everyone who signed up is told automatically. Nothing is stuck.</p>",
        "<h3>Do I Need to Be an Expert?</h3><p>No. You need to be safe and clear. Plenty of "
        "great classes are run by people two steps ahead of everyone else in the "
        "room.</p><h3>How Long Until I Hear Back?</h3><p>An admin usually gets to it within a "
        "week. You can check this page any time to see where things stand.</p><h3>Can I Charge "
        "for It?</h3><p>Yes. You set the price when you build the page. Every class costs at "
        "least $1.00.</p><h3>What If Nobody Signs Up?</h3><p>You can cancel from your dashboard "
        "and everyone who signed up is told automatically. Nothing is stuck.</p>",
    ),
}


def _swap(apps, frm: int, to: int) -> None:
    ClassSettings = apps.get_model("classes", "ClassSettings")
    for field, texts in COPY_FIELDS.items():
        ClassSettings.objects.filter(**{field: texts[frm]}).update(**{field: texts[to]})


def forwards(apps, schema_editor):
    _swap(apps, 0, 1)


def backwards(apps, schema_editor):
    _swap(apps, 1, 0)


class Migration(migrations.Migration):
    dependencies = [("classes", "0075_class_type_and_host_a_class_copy")]

    operations = [migrations.RunPython(forwards, backwards)]
