"""Give every current instructor a personal discount code (#1065).

An instructor is a member with ``instructor_oriented_at`` set (``Member.can_create_classes``).
One who already has a code aimed at their classes (an admin made one, #764) keeps it: the oldest
becomes their personal code and nothing new is made. Everyone else gets a new code, approved and
active with no limits, named the way ``DiscountCodeQuerySet.ensure_personal_for`` names one:
the first word of their name in capital letters plus the percent from Site Settings
(JACQUELINE10); when that is taken, one more letter of their last name at a time
(JACQUELINEF10, JACQUELINEFI10); once the last name runs out, A, B, C before the percent
(JACQUELINEFINGERA10). Digits only ever end a code, as the percent.

The naming is copied here rather than imported, so a later change to the app code cannot change
what this migration did. Nothing is emailed. Running it twice makes nothing new.

The reverse deletes only the codes this migration made (they carry its own description) that
nobody has used yet; a used one keeps its registrations' link and stays. Adopted codes need
nothing undone: reversing 0098 drops the column that marks them.
"""

from __future__ import annotations

import itertools
import re
import string
import unicodedata
from collections.abc import Iterator
from typing import Any

from django.db import migrations

BACKFILL_DESCRIPTION = "Personal instructor code: made for every instructor when personal codes shipped."
FALLBACK_STEM = "TEACH"
STEM_MAX = 20
SURNAME_MAX = 14
DEFAULT_PCT = 10


def _letters(word: str) -> str:
    folded = unicodedata.normalize("NFKD", word).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z]", "", folded).upper()


def _stem(name: str) -> str:
    words = name.split()
    return _letters(words[0] if words else "")[:STEM_MAX] or FALLBACK_STEM


def _surname(display_name: str, full_legal_name: str) -> str:
    for name in (display_name, full_legal_name):
        words = name.split()
        if len(words) > 1:
            return _letters(words[-1])[:SURNAME_MAX]
    return ""


def _candidates(display_name: str, full_legal_name: str, pct: int) -> Iterator[str]:
    stem = _stem(display_name)
    surname = _surname(display_name, full_legal_name)
    yield f"{stem}{pct}"
    for end in range(1, len(surname) + 1):
        yield f"{stem}{surname[:end]}{pct}"
    for length in itertools.count(1):
        for letters in itertools.product(string.ascii_uppercase, repeat=length):
            yield f"{stem}{surname}{''.join(letters)}{pct}"


def backfill(apps: Any, schema_editor: Any) -> None:
    member_model = apps.get_model("membership", "Member")
    code_model = apps.get_model("classes", "DiscountCode")
    config_model = apps.get_model("core", "SiteConfiguration")
    config = config_model.objects.filter(pk=1).first()
    pct = config.instructor_code_discount_pct if config is not None else DEFAULT_PCT
    taken = set(code_model.objects.values_list("code", flat=True))
    for member in member_model.objects.filter(instructor_oriented_at__isnull=False).order_by("pk"):
        if code_model.objects.filter(instructor=member, is_personal=True).exists():
            continue
        adopted = code_model.objects.filter(instructor=member).order_by("created_at", "pk").first()
        if adopted is not None:
            adopted.is_personal = True
            adopted.save(update_fields=["is_personal"])
            continue
        display_name = member.preferred_name or member.full_legal_name
        code = next(c for c in _candidates(display_name, member.full_legal_name, pct) if c not in taken)
        taken.add(code)
        code_model.objects.create(
            code=code,
            description=BACKFILL_DESCRIPTION,
            discount_pct=pct,
            instructor=member,
            is_personal=True,
            is_approved=True,
            is_active=True,
        )


def remove_backfilled(apps: Any, schema_editor: Any) -> None:
    code_model = apps.get_model("classes", "DiscountCode")
    code_model.objects.filter(description=BACKFILL_DESCRIPTION, is_personal=True, use_count=0).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0098_discountcode_is_personal"),
        ("core", "0131_instructor_code_discount_pct"),
        ("membership", "0260_past_students_label"),
    ]

    operations = [migrations.RunPython(backfill, remove_backfilled)]
