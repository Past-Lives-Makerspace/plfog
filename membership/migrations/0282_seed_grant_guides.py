"""Seed one Guide wiki page for each of the six permissions that got a Get Set Up card in #1167, part 3.

Discount Code Administrator, Refunds, Webmaster, Instructor, Admin and Class Reviewer. Each page is
an Official, space wide page linked to its permission through ``guide_for``, so the permission's
Guide shows it; admins edit it from here on. Content is the verified How It Works map from #1163
(branch permission-guides-content-map, 0486e8c0), checked against the code again.

Idempotent like 0278 and 0279: a permission that already has a page is left alone, and a live
space wide page with the same title is linked instead of made. The reverse unlinks each page and
deletes it only when nobody has touched it since.
"""

import re
import secrets

from django.db import migrations
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import slugify

QR_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
NOTE = "Seeded from the verified permission guides (#1167)"
DIGEST = (
    "The email comes in the 8 AM staff digest, and is left out if someone has already decided. "
    "Notification Settings lists every email this permission brings you, and how each reaches you."
)

#: ``(permission, title, body)`` for each page.
PAGES = (
    (
        "discount_approver",
        "Discount Code Administrator Guide",
        "<h2>How Requests Reach You</h2>"
        "<p>When an instructor makes a discount code that needs approval, or asks for one, it comes to "
        "you.</p>"
        f"<p>Each one rings your bell at once with a link to approve or decline it. {DIGEST}</p>"
        "<h2>Deciding a Request</h2>"
        "<ul>"
        "<li>There is no list page for these. Each one opens from its own link.</li>"
        "<li>Approve a code, or decline an instructor's request with a note they read.</li>"
        "</ul>",
    ),
    (
        "refunds",
        "Refunds Guide",
        "<h2>Where Refunds Happen</h2>"
        "<p>Refunds opens no pages of its own. It adds a refund action to pages you can already open:</p>"
        "<ul>"
        "<li>A class's Registrations rows.</li>"
        "<li>The orientation and reservation booking menus.</li>"
        "<li>The Payments table, which needs Billing Administrator too.</li>"
        "</ul>"
        "<p>When an instructor cancels a class with paid registrations, you get a notice pointing to that "
        "class's Registrations tab.</p>",
    ),
    (
        "webmaster",
        "Webmaster Guide",
        "<h2>What Comes to You</h2>"
        "<ul>"
        "<li>When a scheduled automation fails, you get an email saying what broke and when, at most once "
        "a day for each automation.</li>"
        "<li>You get an email for each new request in the Feedback Inbox, and for each member reply to "
        "one.</li>"
        "</ul>"
        "<p>There is nothing to open: the work is in the emails. Notification Settings lists them, and how "
        "each reaches you.</p>",
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
    ),
    (
        "admin_role",
        "Admin Guide",
        "<h2>Admin Tools</h2>"
        "<p>Admin Tools is your starting point for every admin page in the Member Portal, including "
        "members, guilds, classes and Site Settings.</p>"
        "<h2>Your Duties</h2>"
        "<p>Admin duties such as reviewing classes or billing alerts are separate permissions. You hear "
        "about a duty only once you turn it on for yourself, from the View As menu at the top of any "
        "page.</p>",
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
    ),
)


def _slug(WikiPage, title: str) -> str:
    base = slugify(title)
    slug, n = base, 2
    while WikiPage.objects.filter(slug=slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def _qr_code(WikiPage) -> str:
    while True:
        code = "".join(secrets.choice(QR_ALPHABET) for _ in range(6))
        if not WikiPage.objects.filter(qr_code=code).exists():
            return code


def seed(apps, schema_editor) -> None:
    WikiPage = apps.get_model("membership", "WikiPage")
    WikiRevision = apps.get_model("membership", "WikiRevision")
    for permission, title, body in PAGES:
        if WikiPage.objects.filter(guide_for=permission).exists():
            continue
        existing = WikiPage.objects.filter(title__iexact=title, guild__isnull=True, archived_at__isnull=True).first()
        if existing is not None:
            existing.guide_for = permission
            existing.save(update_fields=["guide_for"])
            continue
        page = WikiPage.objects.create(
            title=title,
            slug=_slug(WikiPage, title),
            qr_code=_qr_code(WikiPage),
            kind="howto",
            status="official",
            body=body,
            body_edited_at=timezone.now(),
            guide_for=permission,
            search_text=re.sub(r"\s+", " ", f"{title} {strip_tags(body.replace('<', ' <'))}").strip(),
        )
        WikiRevision.objects.create(
            page=page, title=title, body=body, facts=[], status="official", kind="save", note=NOTE
        )


def _pointing_rows(WikiPage, pk: int) -> int:
    """How many rows of any model point at page ``pk`` (revisions, facts, locks, drafts, reports)."""
    total = 0
    for relation in WikiPage._meta.related_objects:
        total += relation.related_model._base_manager.filter(**{f"{relation.field.attname}": pk}).count()
    return total


def unseed(apps, schema_editor) -> None:
    # Raw deletes, as in 0278 and 0279: the cascade collector compares historical model classes across
    # migration states and refuses, so a page goes only when its one seeded revision is all that
    # points at it, and is otherwise unlinked.
    WikiPage = apps.get_model("membership", "WikiPage")
    WikiRevision = apps.get_model("membership", "WikiRevision")
    for permission, title, body in PAGES:
        for page in WikiPage.objects.filter(guide_for=permission):
            untouched = page.body == body and page.title == title
            revisions = WikiRevision._base_manager.filter(page_id=page.pk)
            if untouched and revisions.count() == 1 and _pointing_rows(WikiPage, page.pk) == 1:
                revisions._raw_delete(schema_editor.connection.alias)
                WikiPage._base_manager.filter(pk=page.pk)._raw_delete(schema_editor.connection.alias)
            else:
                WikiPage.objects.filter(pk=page.pk).update(guide_for="")


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0281_setup_permission_choices"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
