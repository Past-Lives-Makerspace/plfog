"""Add Category.icon_svg + seed Lucide line icons for the 14 known categories.

Lucide is MIT-licensed and matches the existing FOG sidebar SVG style
(24x24 viewBox, stroke=currentColor). Each existing category whose
name matches one of the seeds gets its ``icon_svg`` populated; admins
can override per-category later in the admin.
"""

from __future__ import annotations

from django.db import migrations, models


# Maps (case-insensitive category name) → Lucide SVG inner markup.
# Wrapped in the standard <svg> shell by _seed below.
_SEEDS: dict[str, str] = {
    "art framing": '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M22 11h-4M2 11h4M11 22v-4M11 6V2"/>',
    "ceramics": '<path d="M17 8h1a4 4 0 0 1 0 8h-1"/><path d="M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4z"/><line x1="6" y1="2" x2="6" y2="4"/><line x1="10" y1="2" x2="10" y2="4"/><line x1="14" y1="2" x2="14" y2="4"/>',
    "creative business": '<rect x="2" y="7" width="20" height="14" rx="2" ry="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
    "education": '<path d="M22 10v6M2 10l10-5 10 5-10 5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/>',
    "food independence": '<path d="M7 20h10"/><path d="M10 20c5.5-2.5.8-6.4 3-10"/><path d="M9.5 9.4c1.1.8 1.8 2.2 2.3 3.7-2 .4-3.5.4-4.8-.3-1.2-.6-2.3-1.9-3-4.2 2.8-.5 4.4 0 5.5.8z"/><path d="M14.1 6a7 7 0 0 0-1.1 4c1.9-.1 3.3-.6 4.3-1.4 1-1 1.6-2.3 1.7-4.6-2.7.1-4 1-4.9 2z"/>',
    "glass": '<path d="M8 22h8M7 10h10M12 15v7M12 15a5 5 0 0 0 5-5c0-2-.5-3.5-2-8H9c-1.5 4.5-2 6-2 8a5 5 0 0 0 5 5Z"/>',
    "jewelry": '<path d="M6 3h12l4 6-10 13L2 9Z"/><path d="M11 3 8 9l4 13 4-13-3-6"/><path d="M2 9h20"/>',
    "leather": '<circle cx="6" cy="6" r="3"/><path d="M8.12 8.12 12 12"/><path d="M20 4 8.12 15.88"/><circle cx="6" cy="18" r="3"/><path d="M14.8 14.8 20 20"/>',
    "metalworking": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    "tech": '<rect x="4" y="4" width="16" height="16" rx="2" ry="2"/><rect x="9" y="9" width="6" height="6"/><path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/><path d="M20 15h2"/><path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/>',
    "textiles": '<path d="M20.38 3.46 16 2a4 4 0 0 1-8 0L3.62 3.46a2 2 0 0 0-1.34 2.23l.58 3.47a1 1 0 0 0 .99.84H6v10c0 1.1.9 2 2 2h8a2 2 0 0 0 2-2V10h2.15a1 1 0 0 0 .99-.84l.58-3.47a2 2 0 0 0-1.34-2.23z"/>',
    "visual arts": '<circle cx="13.5" cy="6.5" r=".5"/><circle cx="17.5" cy="10.5" r=".5"/><circle cx="8.5" cy="7.5" r=".5"/><circle cx="6.5" cy="12.5" r=".5"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z"/>',
    "woodworking": '<path d="m15 12-8.5 8.5c-.83.83-2.17.83-3 0a2.12 2.12 0 0 1 0-3L12 9"/><path d="M17.64 15 22 10.64"/><path d="m20.91 11.7-1.25-1.25c-.6-.6-.93-1.4-.93-2.25v-.86L16.01 4.6a5.56 5.56 0 0 0-3.94-1.64H9l.92.82A6.18 6.18 0 0 1 12 8.4v1.56l2 2h2.47l2.26 1.91"/>',
    "writing": '<path d="M12 19l7-7 3 3-7 7-3-3z"/><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/><path d="M2 2l7.586 7.586"/><circle cx="11" cy="11" r="2"/>',
}


def _wrap(inner: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round" aria-hidden="true">' + inner + "</svg>"
    )


def seed(apps, schema_editor):
    Category = apps.get_model("classes", "Category")
    for cat in Category.objects.all():
        if cat.icon_svg:
            continue
        key = (cat.name or "").strip().lower()
        if key in _SEEDS:
            cat.icon_svg = _wrap(_SEEDS[key])
            cat.save(update_fields=["icon_svg"])


def unseed(apps, schema_editor):
    Category = apps.get_model("classes", "Category")
    seeded_markers = [f'viewBox="0 0 24 24"']  # only clear icons we plausibly seeded
    for cat in Category.objects.exclude(icon_svg=""):
        if any(marker in cat.icon_svg for marker in seeded_markers):
            cat.icon_svg = ""
            cat.save(update_fields=["icon_svg"])


class Migration(migrations.Migration):

    dependencies = [
        ("classes", "0023_class_offering_video_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="icon_svg",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Inline SVG markup shown next to the category name on public pages. "
                    "Tint via currentColor. Defaults to a Lucide icon seeded for known categories."
                ),
            ),
        ),
        migrations.RunPython(seed, unseed),
    ]
