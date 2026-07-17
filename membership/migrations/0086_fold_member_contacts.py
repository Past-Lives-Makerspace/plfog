"""Fold three fixed contact columns into ``MemberContact`` and backfill ``instructor_bio``.

Forward: every instructor's ``about_me`` seeds ``instructor_bio`` (only while it is still
empty, so an already-written instructor bio is never clobbered), and each non-empty
``other_contact_info`` / ``instructor_website`` / ``instructor_social_handle`` becomes a
labeled :class:`~membership.models.MemberContact` row carrying the visibility it had before
(Other → directory only; Website / Social → instructor page only).

Reverse: repopulates the three columns from the matching seeded contact rows, deletes those
rows, and clears ``instructor_bio`` — restoring the exact state left by migration 0085.
"""

from django.db import migrations

_OTHER = "Other"
_WEBSITE = "Website"
_SOCIAL = "Social"

# (source field, contact label, show_in_directory, show_on_instructor_page)
_SEEDS = [
    ("other_contact_info", _OTHER, True, False),
    ("instructor_website", _WEBSITE, False, True),
    ("instructor_social_handle", _SOCIAL, False, True),
]

_FIELD_BY_LABEL = {label: field_name for field_name, label, _, _ in _SEEDS}


def fold_contacts_forward(apps, schema_editor):
    """Seed ``instructor_bio`` and create a ``MemberContact`` per non-empty legacy field."""
    Member = apps.get_model("membership", "Member")
    MemberContact = apps.get_model("membership", "MemberContact")
    for member in Member.objects.all():
        if member.instructor_slug and member.about_me and not member.instructor_bio:
            member.instructor_bio = member.about_me
            member.save(update_fields=["instructor_bio"])
        order = 0
        for field_name, label, in_directory, on_instructor in _SEEDS:
            value = getattr(member, field_name)
            if value:
                MemberContact.objects.create(
                    member=member,
                    label=label,
                    value=value,
                    show_in_directory=in_directory,
                    show_on_instructor_page=on_instructor,
                    sort_order=order,
                )
                order += 1


def fold_contacts_reverse(apps, schema_editor):
    """Restore the three legacy columns from the matching seeded rows; clear ``instructor_bio``."""
    Member = apps.get_model("membership", "Member")
    MemberContact = apps.get_model("membership", "MemberContact")
    for member in Member.objects.all():
        changed = ["instructor_bio"]
        member.instructor_bio = ""
        for label, field_name in _FIELD_BY_LABEL.items():
            row = MemberContact.objects.filter(member=member, label=label).order_by("sort_order", "id").first()
            if row is not None:
                setattr(member, field_name, row.value)
                changed.append(field_name)
                row.delete()
        member.save(update_fields=changed)


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0085_member_instructor_bio_membercontact"),
    ]

    operations = [
        migrations.RunPython(fold_contacts_forward, fold_contacts_reverse),
    ]
