"""Seed one Guide wiki page for each of the seven capabilities that got a Get Set Up card (#1167, part 2).

Each page is an Official, space wide page linked to its capability through ``guide_for``, so the
capability's Guide shows it; admins edit it from here on. Content is the verified How It Works map
from #1163 (branch permission-guides-content-map, 0486e8c0), checked against the code again.

Idempotent like 0278: a capability that already has a page is left alone, and a live space wide
page with the same title is linked instead of made. Written against the historical models, so it
fills the slug and the QR code the way ``WikiPage.save`` does. The reverse unlinks each page and
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
    ),
    (
        "space_approver",
        "Space & Cubby Administrator Guide",
        "<h2>How Requests Reach You</h2>"
        "<p>You review every studio space and shelf request, site wide.</p>"
        f"<p>Each request rings your bell at once with a link to it. {DIGEST}</p>"
        "<h2>Deciding a Request</h2>"
        "<ul>"
        "<li>Requests wait on Space Requests Awaiting Review until someone approves or declines them.</li>"
        "<li>Approving tells the member yes and notes that you will finalize the agreement yourself. It "
        "does not create one automatically.</li>"
        "<li>A decline needs a note, which the member reads.</li>"
        "</ul>",
    ),
    (
        "events_approver",
        "Calendar Administrator Guide",
        "<h2>How Proposals Reach You</h2>"
        "<p>You review Calendar proposals that are not for a guild. A guild's proposals go to its lead and "
        "staff.</p>"
        f"<p>Each proposal rings your bell at once. {DIGEST}</p>"
        "<h2>Reviewing a Proposal</h2>"
        "<p>Manage Events lists every event. Pick the Needs review chip and open a proposal to approve it, "
        "ask for changes or decline it. Approving puts it on the Calendar, or schedules it when it has a "
        "later publish time.</p>"
        "<h2>Adding Your Own Events</h2>"
        "<p>Press + Add event on Manage Events or the Calendar. Events you add are published straight "
        "away, with no review. You can edit, delete or retry the sync of any event that is not a "
        "guild's.</p>",
    ),
    (
        "event_creator",
        "Event Creator Guide",
        "<h2>How Your Events Go Out</h2>"
        "<p>You can add events to the Calendar whatever the member event setting says.</p>"
        "<p>Every event you save goes to an admin or a Calendar Administrator for a quick review first, "
        "and is announced once it is approved.</p>"
        "<h2>Adding an Event</h2>"
        "<p>Press + Add event on the Calendar, fill in your event and save it.</p>",
    ),
    (
        "billing_approver",
        "Billing Administrator Guide",
        "<h2>The Dashboard</h2>"
        "<ul>"
        "<li>Payments lists every payment, with a date range and filters, and exports a CSV.</li>"
        "<li>While My Tab is on, Overview and Open Tabs show members' tabs too.</li>"
        "<li>Settings, Stripe and Reconciliation stay with admins.</li>"
        "</ul>"
        "<h2>Alerts</h2>"
        "<p>You get an alert when money needs a person to look at it:</p>"
        "<ul>"
        "<li>A member's tab charge fails, or a refund fails.</li>"
        "<li>A payout to an instructor or orientor fails (it retries daily), or a refund's share cannot be "
        "taken back from the person paid.</li>"
        "<li>A class is paid for after its registration lost the seat. Refund it or re-seat them.</li>"
        "<li>A late fee or an orientation is paid online with nothing left to match it to. Refund it in "
        "Stripe.</li>"
        "</ul>"
        "<p>Notification Settings lists every alert this permission brings you, and how each reaches "
        "you.</p>",
    ),
    (
        "equipment",
        "Equipment Administrator Guide",
        "<h2>What You Can Change</h2>"
        "<ul>"
        "<li>Add a tool, room or space with + Add on Reservations.</li>"
        "<li>Open any item in the building to manage it: its Details, Staff, Hours &amp; Limits, "
        "Reservations and Orientation.</li>"
        "</ul>"
        "<h2>Who Hears About Bookings</h2>"
        "<p>An item's bookings and orientation requests go to its managers and its guild's leadership. You "
        "hear about them only when the item has neither.</p>"
        "<p>Give an item managers on its Staff tab, and they hear instead.</p>",
    ),
    (
        "space_manager",
        "Space Manager Guide",
        "<h2>Adding a Space</h2>"
        "<p>Press + Add on Reservations to add a room or space members can reserve, such as the loading "
        "dock. Tools are not yours to add.</p>"
        "<h2>Managing Your Spaces</h2>"
        "<p>Saving a space makes you its manager and opens its page. Manage it from there: its details, "
        "hours and staff. Its bookings come to you as its manager.</p>",
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
    # Raw deletes, as in 0278: the cascade collector compares historical model classes across
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
        ("membership", "0278_seed_tour_guide_guide"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
