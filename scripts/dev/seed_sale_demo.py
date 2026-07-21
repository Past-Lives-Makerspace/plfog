#!/usr/bin/env python3
"""Put one published paid class on a 25%-off sale for visual review.

Idempotent: if any class already has an active sale, it reports it and exits.
Otherwise it picks the first published paid class (creating a demo one, with a
future session, only if none exists) and enables a 25%-off percent sale with
the default banner text.

Usage (dev DB, from the repo root):
    docker compose exec web python scripts/dev/seed_sale_demo.py
or, outside compose:
    DATABASE_URL=... python scripts/dev/seed_sale_demo.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plfog.settings")

import django

django.setup()

from datetime import timedelta  # noqa: E402

from django.urls import reverse  # noqa: E402
from django.utils import timezone  # noqa: E402

from classes.models import Category, ClassOffering, ClassSession  # noqa: E402


def main() -> None:
    already = next((o for o in ClassOffering.objects.filter(sale_enabled=True) if o.sale_is_active), None)
    if already is not None:
        print(
            f"Already on sale: '{already.title}' ({already.sale_savings_display}) — "
            f"{reverse('classes:public_class_detail', kwargs={'slug': already.slug})}"
        )
        return

    offering = (
        ClassOffering.objects.filter(status=ClassOffering.Status.PUBLISHED, price_cents__gt=0).order_by("pk").first()
    )
    if offering is None:
        category, _ = Category.objects.get_or_create(slug="sale-demo", defaults={"name": "Sale Demo"})
        offering = ClassOffering.objects.create(
            title="Sale Demo Class",
            slug="sale-demo-class",
            category=category,
            description="Demo class seeded by scripts/dev/seed_sale_demo.py for sale review.",
            price_cents=8000,
            status=ClassOffering.Status.PUBLISHED,
        )
        starts = timezone.now() + timedelta(days=14)
        ClassSession.objects.create(class_offering=offering, starts_at=starts, ends_at=starts + timedelta(hours=2))
        print(f"Created demo class '{offering.title}'.")

    offering.sale_enabled = True
    offering.sale_kind = ClassOffering.SaleKind.PERCENT
    offering.sale_percent = 25
    offering.sale_banner_text = ""  # render path falls back to the default banner
    offering.save(update_fields=["sale_enabled", "sale_kind", "sale_percent", "sale_banner_text"])
    print(
        f"'{offering.title}' is now {offering.sale_savings_display}: "
        f"{offering.price_cents}¢ → {offering.sale_price_cents}¢ — "
        f"{reverse('classes:public_class_detail', kwargs={'slug': offering.slug})}"
    )


if __name__ == "__main__":
    main()
