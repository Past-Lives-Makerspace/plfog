"""Backfill ``MemberContact.kind`` from each row's freeform ``label``.

Best-effort classification of freeform historical labels: a case-insensitive substring
match against the label sorts each contact into the Website or Social profile section;
anything unrecognized keeps the default "other". Members can recategorize a row by
deleting and re-adding it under the section they want.

Reverse: sets every row back to "other" — the pre-migration default state left by 0131.
"""

from django.db import migrations

_WEBSITE_KEYWORDS = ("website", "site", "portfolio", "shop", "etsy", "blog")
_SOCIAL_KEYWORDS = ("instagram", "youtube", "facebook", "tiktok", "linkedin", "twitter")


def classify_kinds_forward(apps, schema_editor):
    """Sort each contact into website/social by label keywords; leave the rest 'other'."""
    MemberContact = apps.get_model("membership", "MemberContact")
    for contact in MemberContact.objects.all():
        label_lower = contact.label.lower()
        if any(keyword in label_lower for keyword in _WEBSITE_KEYWORDS):
            contact.kind = "website"
        elif any(keyword in label_lower for keyword in _SOCIAL_KEYWORDS):
            contact.kind = "social"
        else:
            continue
        contact.save(update_fields=["kind"])


def classify_kinds_reverse(apps, schema_editor):
    """Reset every contact to the 'other' default 0131 left behind."""
    MemberContact = apps.get_model("membership", "MemberContact")
    MemberContact.objects.update(kind="other")


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0131_membercontact_kind"),
    ]

    operations = [
        migrations.RunPython(classify_kinds_forward, classify_kinds_reverse),
    ]
