"""Convert the Host a Workshop prose fields from Markdown to the rich editor's HTML.

v1.46.0 stored How It Works, What We Ask Of You and Common Questions as Markdown and
rendered them through the member profile. v1.47.0 edits them in the rich editor, so the
stored value is the HTML Quill would save. The renderer is dual mode either way (a value
that does not start with ``<`` still renders as Markdown), so this migration is about
what an admin sees in the editor and what the FAQ accordion splits on, not about the
page breaking.

Forward, per field: the v1.46.0 Markdown default becomes the matching HTML default; any
other Markdown is rendered through the member profile once; HTML is left alone.
Reverse: the HTML default goes back to the Markdown default; anything else is left as
is, which the old renderer displayed unchanged (it passed sanitized HTML through).
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

LEGACY_TEACH_PAGE_HOW_IT_WORKS_MD = """\
1. **Say you're interested.** Tell us what you'd like to host. A sentence is plenty. An admin reads every note.
2. **Build your page.** Once you're in, the editor walks you through it in five short steps. Save a draft any time and come back.
3. **Open sign ups.** Send it for a quick look. Your guild lead and an admin check it over, then it goes into the catalog and out to members."""

LEGACY_TEACH_PAGE_EXPECTATIONS_MD = """\
- Know your material and know the tools you're using.
- Show up on time and leave the space the way you found it.
- Answer people when they message you through the app.
- Tell an admin as early as you can if you need to move or cancel a date."""

LEGACY_TEACH_PAGE_FAQ_MD = """\
### Do I Need to Be an Expert?
No. You need to be safe and clear. Plenty of great workshops are run by people two steps ahead of everyone else in the room.

### How Long Until I Hear Back?
An admin usually gets to it within a week. You can check this page any time to see where things stand.

### Can I Charge for It?
Yes. You set the price and an optional member discount when you build the page. You can also run it free.

### What If Nobody Signs Up?
You can cancel from your dashboard and everyone who signed up is told automatically. Nothing is stuck."""

TEACH_PAGE_HOW_IT_WORKS_HTML = (
    "<ol>"
    "<li><strong>Say you're interested.</strong> Tell us what you'd like to host. A sentence is plenty. "
    "An admin reads every note.</li>"
    "<li><strong>Build your page.</strong> Once you're in, the editor walks you through it in five short steps. "
    "Save a draft any time and come back.</li>"
    "<li><strong>Open sign ups.</strong> Send it for a quick look. Your guild lead and an admin check it over, "
    "then it goes into the catalog and out to members.</li>"
    "</ol>"
)

TEACH_PAGE_EXPECTATIONS_HTML = (
    "<ul>"
    "<li>Know your material and know the tools you're using.</li>"
    "<li>Show up on time and leave the space the way you found it.</li>"
    "<li>Answer people when they message you through the app.</li>"
    "<li>Tell an admin as early as you can if you need to move or cancel a date.</li>"
    "</ul>"
)

TEACH_PAGE_FAQ_HTML = (
    "<h3>Do I Need to Be an Expert?</h3>"
    "<p>No. You need to be safe and clear. Plenty of great workshops are run by people two steps ahead of "
    "everyone else in the room.</p>"
    "<h3>How Long Until I Hear Back?</h3>"
    "<p>An admin usually gets to it within a week. You can check this page any time to see where things stand.</p>"
    "<h3>Can I Charge for It?</h3>"
    "<p>Yes. You set the price and an optional member discount when you build the page. You can also run it free.</p>"
    "<h3>What If Nobody Signs Up?</h3>"
    "<p>You can cancel from your dashboard and everyone who signed up is told automatically. Nothing is stuck.</p>"
)

# field name -> (the v1.46.0 Markdown default, the v1.47.0 HTML default)
PROSE_FIELDS: dict[str, tuple[str, str]] = {
    "teach_page_how_it_works": (LEGACY_TEACH_PAGE_HOW_IT_WORKS_MD, TEACH_PAGE_HOW_IT_WORKS_HTML),
    "teach_page_expectations": (LEGACY_TEACH_PAGE_EXPECTATIONS_MD, TEACH_PAGE_EXPECTATIONS_HTML),
    "teach_page_faq": (LEGACY_TEACH_PAGE_FAQ_MD, TEACH_PAGE_FAQ_HTML),
}


def _looks_like_html(value: str) -> bool:
    return value.lstrip().startswith("<")


def convert_markdown_forward(apps: Any, schema_editor: Any) -> None:
    """Markdown defaults to HTML defaults; other Markdown rendered once; HTML untouched."""
    # A pure function over strings, no model imports: safe inside a migration.
    from membership.markdown import render_markdown

    ClassSettings = apps.get_model("classes", "ClassSettings")
    for row in ClassSettings.objects.all():
        changed: list[str] = []
        for name, (legacy_md, html) in PROSE_FIELDS.items():
            value = getattr(row, name)
            if value == legacy_md:
                setattr(row, name, html)
            elif value.strip() and not _looks_like_html(value):
                setattr(row, name, render_markdown(value, profile="member"))
            else:
                continue
            changed.append(name)
        if changed:
            row.save(update_fields=changed)


def convert_markdown_reverse(apps: Any, schema_editor: Any) -> None:
    """HTML defaults back to the Markdown defaults; everything else is left as is."""
    ClassSettings = apps.get_model("classes", "ClassSettings")
    for row in ClassSettings.objects.all():
        changed: list[str] = []
        for name, (legacy_md, html) in PROSE_FIELDS.items():
            if getattr(row, name) == html:
                setattr(row, name, legacy_md)
                changed.append(name)
        if changed:
            row.save(update_fields=changed)


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0061_teach_page_money_split_and_rich_text"),
    ]

    operations = [
        migrations.RunPython(convert_markdown_forward, convert_markdown_reverse),
    ]
