"""Seed the Tour Guide Guide wiki page and link it to Tour Guide's Guide tab (#1167).

Content is the verified How It Works map from #1163 (branch permission-guides-content-map,
0486e8c0), brought up to date with Confirm First (#1166), and belongs to admins from here on: an
Official space wide page, which only admins edit. A page with the same title already there is
linked instead of made. Written against the historical models, so it fills the slug and the QR
code the way ``WikiPage.save`` does. The reverse unlinks the page and deletes it only if nobody
has edited it since.
"""

import re
import secrets

from django.db import migrations
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import slugify

TITLE = "Tour Guide Guide"
PERMISSION = "tour_guide"
QR_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
NOTE = "Seeded from the verified Tour Guide guide (#1167)"

BODY = (
    "<h2>Setting Up</h2>"
    "<p>On My Hours, set the times you can give tours: Every Week for hours that repeat, or Available On "
    "for dates you pick. Each window is carved into back to back tours of the length you choose, each "
    "with 1 to 50 seats.</p>"
    "<p>Add Days Off for when you are away. Your hours offer nothing on those days, and tours already "
    "booked stay booked.</p>"
    "<p>Connect your Google Calendar on My Hours if you use one. Your tours land on it, and times it shows "
    "you busy are not offered to visitors.</p>"
    "<h2>When Someone Books</h2>"
    "<ul>"
    "<li>Visitors book an open time inside your hours on tours.pastlives.space, or at the front desk kiosk "
    "in the lobby.</li>"
    "<li>A booking is confirmed at once, and you get an email, a bell and a push. You hear again when a "
    "visitor moves or cancels.</li>"
    "<li>To confirm or decline each booking yourself, pick Confirm First under Confirming Bookings on My "
    "Hours. Website visitors then send a request, and you answer it on the Upcoming tab by 24 hours before "
    "the tour. A request nobody answers by then is cancelled, and the visitor is told. Kiosk bookings are "
    "always confirmed at once.</li>"
    "<li>Reschedule or Cancel Tour from the Upcoming tab. The visitor is emailed, with your note if you "
    "add one.</li>"
    "</ul>"
    "<h2>A Tour Day</h2>"
    "<p>Meet your visitors in the lobby. When they check in at the kiosk you get a bell and a push: "
    "“Your 10:00 tour is here.” Afterwards, mark the tour Attended or No Show on the Past tab.</p>"
    "<h2>The Lobby Kiosk</h2>"
    "<p>The screen in the lobby has two buttons, I Booked a Tour and Schedule a Tour. Visitors check in "
    "with the email they booked with or their name, and walk ins can book an open time there.</p>"
    "<h2>Getting Paid</h2>"
    "<p>When a visitor you gave a tour to becomes a member, you are paid $50. Mark each tour Attended on "
    "the Past tab: that is the record of who you toured.</p>"
)


def _slug(WikiPage) -> str:
    base = slugify(TITLE)
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
    if WikiPage.objects.filter(guide_for=PERMISSION).exists():
        return
    existing = WikiPage.objects.filter(title__iexact=TITLE, guild__isnull=True, archived_at__isnull=True).first()
    if existing is not None:
        existing.guide_for = PERMISSION
        existing.save(update_fields=["guide_for"])
        return
    now = timezone.now()
    page = WikiPage.objects.create(
        title=TITLE,
        slug=_slug(WikiPage),
        qr_code=_qr_code(WikiPage),
        kind="howto",
        status="official",
        body=BODY,
        body_edited_at=now,
        guide_for=PERMISSION,
        search_text=re.sub(r"\s+", " ", f"{TITLE} {strip_tags(BODY.replace('<', ' <'))}").strip(),
    )
    WikiRevision.objects.create(page=page, title=TITLE, body=BODY, facts=[], status="official", kind="save", note=NOTE)


def _pointing_rows(WikiPage, pk: int) -> int:
    """How many rows of any model point at page ``pk`` (revisions, facts, locks, drafts, reports)."""
    total = 0
    for relation in WikiPage._meta.related_objects:
        total += relation.related_model._base_manager.filter(**{f"{relation.field.attname}": pk}).count()
    return total


def unseed(apps, schema_editor) -> None:
    # Raw deletes: the cascade collector compares historical model classes across migration
    # states and refuses, so the page goes only when its one seeded revision is all that
    # points at it, and is otherwise unlinked.
    WikiPage = apps.get_model("membership", "WikiPage")
    WikiRevision = apps.get_model("membership", "WikiRevision")
    for page in WikiPage.objects.filter(guide_for=PERMISSION):
        untouched = page.body == BODY and page.title == TITLE
        revisions = WikiRevision._base_manager.filter(page_id=page.pk)
        if untouched and revisions.count() == 1 and _pointing_rows(WikiPage, page.pk) == 1:
            revisions._raw_delete(schema_editor.connection.alias)
            WikiPage._base_manager.filter(pk=page.pk)._raw_delete(schema_editor.connection.alias)
        else:
            WikiPage.objects.filter(pk=page.pk).update(guide_for="")


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0277_permission_setup"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
