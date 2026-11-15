"""Rewrite the three class review Guide pages for one review per class (#1183).

0279 and 0282 seeded the CMS Administrator, Instructor and Class Reviewer guides describing a
second review: a class the guild lead approved went on to an admin, who published it. Now the
guild lead's approval publishes the class, and an admin reviews only a class with no guild lead.
The Get Set Up cards link to these pages' sections by heading, so every heading stays.

Only a page nobody has edited is rewritten, as 0285 does: an admin's own wording is left alone.
The reverse puts the seeded body back when the page still holds this one, and removes the
revision this migration wrote.
"""

import re

from django.db import migrations
from django.utils import timezone
from django.utils.html import strip_tags

NOTE = "Rewritten for one review per class (#1183)"

DIGEST = (
    "The email comes in the 8 AM staff digest, and is left out if someone has already decided. "
    "Notification Settings lists every email this permission brings you, and how each reaches you."
)

#: ``(permission, title, seeded body, new body)``; each seeded body is what 0279 or 0282 wrote,
#: character for character.
PAGES = (
    (
        "class_approver",
        "CMS Administrator Guide",
        "<h2>How Review Goes</h2>"
        "<p>A class is published only once an admin side reviewer approves it. When the class's guild has "
        "a lead, the guild reviews it first: its lead and any staff marked Class Reviewer. Once the guild "
        "approves it, it comes to you. When the guild has no lead, it comes straight to you.</p>"
        f"<p>Each request rings your bell at once with a Review link. {DIGEST}</p>"
        "<h2>Reviewing a Class</h2>"
        "<ul>"
        "<li>Manage All Classes lists every class. Pick the Awaiting admin chip to see what is waiting on "
        "you.</li>"
        "<li>Open a class to approve it, or send it back to the instructor with a note.</li>"
        "</ul>",
        "<h2>How Review Goes</h2>"
        "<p>Each class has one review. When the class's guild has a lead, the guild reviews it: its lead "
        "and any staff marked Class Reviewer. Their approval publishes it, and it does not come to you. "
        "When the class has no guild lead, it comes to you, and your approval publishes it. You can still "
        "approve any class early.</p>"
        f"<p>Each request rings your bell at once with a Review link. {DIGEST}</p>"
        "<h2>Reviewing a Class</h2>"
        "<ul>"
        "<li>Manage All Classes lists every class. Pick the Awaiting admin chip to see what is waiting on "
        "you.</li>"
        "<li>Open a class to approve it, or send it back to the instructor with a note.</li>"
        "</ul>",
    ),
    (
        "instructor",
        "Instructor Guide",
        "<h2>Your Instructor Page</h2>"
        "<p>Your public instructor page shows your photo, bio and links. Edit them on the Instructor "
        "Profile tab.</p>"
        "<h2>Creating a Class</h2>"
        "<p>Press + New Class on the Overview tab and fill in your class. It saves as a draft until you "
        "send it for review. Show Me Around on the Overview walks you through it.</p>"
        "<h2>How Review Goes</h2>"
        "<ul>"
        "<li>Open your class on My Classes and press Submit for Review.</li>"
        "<li>It goes to its guild's lead first when the guild has one, then to an admin, and is published "
        "once the admin approves it.</li>"
        "<li>A class sent back with a note shows on the Overview, under what needs your attention.</li>"
        "</ul>",
        "<h2>Your Instructor Page</h2>"
        "<p>Your public instructor page shows your photo, bio and links. Edit them on the Instructor "
        "Profile tab.</p>"
        "<h2>Creating a Class</h2>"
        "<p>Press + New Class on the Overview tab and fill in your class. It saves as a draft until you "
        "send it for review. Show Me Around on the Overview walks you through it.</p>"
        "<h2>How Review Goes</h2>"
        "<ul>"
        "<li>Open your class on My Classes and press Submit for Review.</li>"
        "<li>It goes to its guild's lead when the guild has one, or to an admin when it has no guild "
        "lead, and is published once that reviewer approves it.</li>"
        "<li>A class sent back with a note shows on the Overview, under what needs your attention.</li>"
        "</ul>",
    ),
    (
        "class_reviewer",
        "Class Reviewer Guide",
        "<h2>How Guild Review Goes</h2>"
        "<p>Classes in your guild come to its lead and its Class Reviewers first. When one needs review "
        "you get a bell and an email with a Review link. The email comes in the 8 AM staff digest, and "
        "is left out if someone has already decided.</p>"
        "<p>A class you approve then goes to an admin, who publishes it.</p>"
        "<h2>Reviewing a Class</h2>"
        "<p>Your guild's page lists every class waiting on you under Needs Attention, Waiting on Your "
        "Review. Open one to approve it, or send it back with a note.</p>",
        "<h2>How Guild Review Goes</h2>"
        "<p>Classes in your guild come to its lead and its Class Reviewers. When one needs review you get "
        "a bell and an email with a Review link. The email comes in the 8 AM staff digest, and is left "
        "out if someone has already decided.</p>"
        "<p>A class you approve goes live right away. No admin reviews it after you.</p>"
        "<h2>Reviewing a Class</h2>"
        "<p>Your guild's page lists every class waiting on you under Needs Attention, Waiting on Your "
        "Review. Open one to approve it, or send it back with a note.</p>",
    ),
)


def _search_text(title: str, body: str) -> str:
    return re.sub(r"\s+", " ", f"{title} {strip_tags(body.replace('<', ' <'))}").strip()


def _swap(apps, permission: str, title: str, *, old: str, new: str) -> list[int]:
    """Put ``new`` on every guide page for ``permission`` that still holds ``old``; returns their pks."""
    WikiPage = apps.get_model("membership", "WikiPage")
    pks = list(WikiPage.objects.filter(guide_for=permission, title=title, body=old).values_list("pk", flat=True))
    WikiPage.objects.filter(pk__in=pks).update(
        body=new, body_edited_at=timezone.now(), search_text=_search_text(title, new)
    )
    return pks


def rewrite(apps, schema_editor) -> None:
    WikiRevision = apps.get_model("membership", "WikiRevision")
    for permission, title, seeded, body in PAGES:
        for pk in _swap(apps, permission, title, old=seeded, new=body):
            WikiRevision.objects.create(
                page_id=pk, title=title, body=body, facts=[], status="official", kind="save", note=NOTE
            )


def restore(apps, schema_editor) -> None:
    WikiRevision = apps.get_model("membership", "WikiRevision")
    for permission, title, seeded, body in PAGES:
        pks = _swap(apps, permission, title, old=body, new=seeded)
        WikiRevision.objects.filter(page_id__in=pks, note=NOTE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0285_rewrite_discount_admin_guide"),
    ]

    operations = [
        migrations.RunPython(rewrite, restore),
    ]
