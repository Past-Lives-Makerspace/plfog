"""Data-migration spec for classes 0062: the Host a Workshop prose fields, Markdown to HTML.

Uses Django's ``MigrationExecutor`` (the pattern of the membership migration specs) so
rows are built against the 0061 state, after the money split fields and the new HTML
defaults exist but before the stored Markdown is converted. Each test restores the schema
to head in a ``finally`` so the rest of the suite sees the current DB.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

_APP = "classes"
_BEFORE = "0061_teach_page_money_split_and_rich_text"
_AFTER = "0062_convert_teach_page_markdown"
_HEAD = "0062_convert_teach_page_markdown"

_migration = import_module(f"classes.migrations.{_AFTER}")
PROSE_FIELDS = _migration.PROSE_FIELDS
LEGACY_FAQ_MD = _migration.LEGACY_TEACH_PAGE_FAQ_MD
FAQ_HTML = _migration.TEACH_PAGE_FAQ_HTML


def _migrate(target: str):
    """Migrate the classes app to ``target`` and return that state's historical apps."""
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _make_settings(apps, **kwargs):
    """Put ``kwargs`` on the settings row. Migration 0058 seeds pk=1, so update it when it is there."""
    ClassSettings = apps.get_model(_APP, "ClassSettings")
    row, _created = ClassSettings.objects.update_or_create(
        pk=1,
        defaults={"liability_waiver_text": "LIABILITY", "model_release_waiver_text": "MODEL RELEASE", **kwargs},
    )
    return row


def _settings(apps):
    return apps.get_model(_APP, "ClassSettings").objects.get(pk=1)


@pytest.mark.django_db(transaction=True)
def describe_migration_0062_convert_teach_page_markdown():
    def it_converts_every_v1_46_markdown_default_to_the_html_default():
        try:
            apps = _migrate(_BEFORE)
            _make_settings(apps, **{name: legacy_md for name, (legacy_md, _html) in PROSE_FIELDS.items()})

            apps = _migrate(_AFTER)
            row = _settings(apps)
            for name, (_legacy_md, html) in PROSE_FIELDS.items():
                assert getattr(row, name) == html
            assert row.teach_page_faq.startswith("<h3>")
        finally:
            _migrate(_HEAD)

    def it_renders_a_custom_markdown_value_to_html_once():
        try:
            apps = _migrate(_BEFORE)
            _make_settings(
                apps,
                teach_page_how_it_works="1. **Ask.** Say hi.",
                teach_page_expectations="- Be kind.\n- Be safe.",
                teach_page_faq="### Is It Free?\n\nYes.",
            )

            apps = _migrate(_AFTER)
            row = _settings(apps)
            assert row.teach_page_how_it_works == "<ol>\n<li><strong>Ask.</strong> Say hi.</li>\n</ol>"
            assert row.teach_page_expectations == "<ul>\n<li>Be kind.</li>\n<li>Be safe.</li>\n</ul>"
            assert row.teach_page_faq == "<h3>Is It Free?</h3>\n<p>Yes.</p>"
        finally:
            _migrate(_HEAD)

    def it_leaves_html_and_blank_values_alone():
        try:
            apps = _migrate(_BEFORE)
            _make_settings(
                apps,
                teach_page_how_it_works="<ol><li>Already rich.</li></ol>",
                teach_page_expectations="",
                teach_page_faq="  <h3>Padded</h3><p>Kept as is.</p>",
            )

            apps = _migrate(_AFTER)
            row = _settings(apps)
            assert row.teach_page_how_it_works == "<ol><li>Already rich.</li></ol>"
            assert row.teach_page_expectations == ""
            assert row.teach_page_faq == "  <h3>Padded</h3><p>Kept as is.</p>"
        finally:
            _migrate(_HEAD)

    def it_reverse_restores_the_markdown_default_and_leaves_custom_html_alone():
        try:
            apps = _migrate(_AFTER)
            _make_settings(
                apps,
                teach_page_how_it_works="<p>An admin's own words.</p>",
                teach_page_expectations=PROSE_FIELDS["teach_page_expectations"][1],
                teach_page_faq=FAQ_HTML,
            )

            apps = _migrate(_BEFORE)
            row = _settings(apps)
            assert row.teach_page_how_it_works == "<p>An admin's own words.</p>"
            assert row.teach_page_expectations == PROSE_FIELDS["teach_page_expectations"][0]
            assert row.teach_page_faq == LEGACY_FAQ_MD
        finally:
            _migrate(_HEAD)

    def it_does_nothing_with_no_settings_row():
        try:
            apps = _migrate(_BEFORE)
            apps.get_model(_APP, "ClassSettings").objects.all().delete()
            apps = _migrate(_AFTER)
            assert apps.get_model(_APP, "ClassSettings").objects.count() == 0
        finally:
            _migrate(_HEAD)
