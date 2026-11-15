"""Rewrite the Discount Code Administrator Guide for codes instructors make themselves (#1185).

0282 seeded the page about approving and declining code requests, which #1184 and #1185 removed:
an instructor's code works at once, and the administrator oversees codes after the fact. The
Get Set Up card links each step to a section of this page, so the sections follow the card.

Only a page nobody has edited is rewritten, as 0282's reverse only deletes an untouched page:
an admin's own wording is left alone. The reverse puts the seeded body back when the page still
holds this one, and removes the revision this migration wrote.
"""

import re

from django.db import migrations
from django.utils import timezone
from django.utils.html import strip_tags

PERMISSION = "discount_approver"
TITLE = "Discount Code Administrator Guide"
NOTE = "Rewritten for codes instructors make themselves (#1185)"

#: What 0282 seeded, character for character.
SEEDED_BODY = (
    "<h2>How Requests Reach You</h2>"
    "<p>When an instructor makes a discount code that needs approval, or asks for one, it comes to "
    "you.</p>"
    "<p>Each one rings your bell at once with a link to approve or decline it. The email comes in the "
    "8 AM staff digest, and is left out if someone has already decided. Notification Settings lists "
    "every email this permission brings you, and how each reaches you.</p>"
    "<h2>Deciding a Request</h2>"
    "<ul>"
    "<li>There is no list page for these. Each one opens from its own link.</li>"
    "<li>Approve a code, or decline an instructor's request with a note they read.</li>"
    "</ul>"
)

BODY = (
    "<h2>How New Codes Reach You</h2>"
    "<p>Instructors make discount codes for their own classes, and each one works at once. Nobody "
    "approves it.</p>"
    "<p>When an instructor makes a code, or changes what one takes off, where it works or how many "
    "uses it has, you hear who did it, what it takes off and where it works. It rings your bell at "
    "once, and the email comes as one line in the 8 AM staff digest. Notification Settings lists "
    "every email this permission brings you, and how each reaches you.</p>"
    "<h2>Overseeing Codes</h2>"
    "<ul>"
    "<li>Discount Codes, on the Settings tab of the Classes admin, lists every code. Opening it takes "
    "the Admin role.</li>"
    "<li>Made by shows only the codes one person made.</li>"
    "<li>To stop a code, open its ... menu, press Edit and untick Is active.</li>"
    "</ul>"
)


def _search_text(body: str) -> str:
    return re.sub(r"\s+", " ", f"{TITLE} {strip_tags(body.replace('<', ' <'))}").strip()


def _swap(apps, *, old: str, new: str) -> list[int]:
    """Put ``new`` on every guide page for the permission that still holds ``old``; returns their pks."""
    WikiPage = apps.get_model("membership", "WikiPage")
    pks = list(WikiPage.objects.filter(guide_for=PERMISSION, title=TITLE, body=old).values_list("pk", flat=True))
    WikiPage.objects.filter(pk__in=pks).update(body=new, body_edited_at=timezone.now(), search_text=_search_text(new))
    return pks


def rewrite(apps, schema_editor) -> None:
    WikiRevision = apps.get_model("membership", "WikiRevision")
    for pk in _swap(apps, old=SEEDED_BODY, new=BODY):
        WikiRevision.objects.create(
            page_id=pk, title=TITLE, body=BODY, facts=[], status="official", kind="save", note=NOTE
        )


def restore(apps, schema_editor) -> None:
    WikiRevision = apps.get_model("membership", "WikiRevision")
    pks = _swap(apps, old=BODY, new=SEEDED_BODY)
    WikiRevision.objects.filter(page_id__in=pks, note=NOTE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0284_remove_member_can_self_approve_discounts_state"),
    ]

    operations = [
        migrations.RunPython(rewrite, restore),
    ]
