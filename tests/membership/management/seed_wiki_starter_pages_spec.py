"""BDD specs for the seed_wiki_starter_pages management command.

It runs by hand against production, so the guards that matter are the ones that keep it off
member writing: it never touches a page that exists, it writes stubs a person has not
written in, and its stubs state no facts.
"""

from __future__ import annotations

import re
from io import StringIO

import pytest
from django.core.management import call_command

from membership.management.commands.seed_wiki_starter_pages import CREATION_NOTE, PAGES
from membership.markdown import render_wiki_content, sanitize_wiki_html
from membership.models import WikiPage, WikiRevision
from tests.membership.factories import GuildFactory, WikiPageFactory

pytestmark = pytest.mark.django_db


def _run(**options: object) -> str:
    out = StringIO()
    call_command("seed_wiki_starter_pages", stdout=out, **options)
    return out.getvalue()


def describe_seed_wiki_starter_pages():
    def it_adds_every_starter_page_as_a_space_wide_stub():
        output = _run()
        pages = WikiPage.objects.order_by("title")
        assert sorted(page.title for page in pages) == sorted(entry.title for entry in PAGES)
        for page in pages:
            assert page.guild_id is None
            assert page.created_by is None
            assert page.body_edited_at is None
            assert not page.has_written_body
        assert f"Added {len(PAGES)} starter pages; 0 already existed" in output

    def it_files_each_page_under_its_kind_with_a_first_revision():
        _run()
        for entry in PAGES:
            page = WikiPage.objects.get(title=entry.title)
            assert page.kind == entry.kind
            assert page.body == entry.body
            assert list(WikiRevision.objects.filter(page=page).values_list("note", flat=True)) == [CREATION_NOTE]

    def it_adds_nothing_on_a_second_run():
        _run()
        output = _run()
        assert WikiPage.objects.count() == len(PAGES)
        assert f"Added 0 starter pages; {len(PAGES)} already existed" in output

    def it_leaves_an_existing_page_with_the_same_title_exactly_as_it_was():
        mine = WikiPageFactory(title=PAGES[0].title.upper(), body="<p>What I wrote.</p>")
        _run()
        mine.refresh_from_db()
        assert mine.body == "<p>What I wrote.</p>"
        assert WikiPage.objects.filter(title__iexact=PAGES[0].title).count() == 1

    def it_is_not_blocked_by_a_guild_page_or_an_archived_page_of_the_same_title():
        WikiPageFactory(title=PAGES[0].title, guild=GuildFactory())
        WikiPageFactory(title=PAGES[1].title, archived=True)
        _run()
        assert WikiPage.objects.filter(title=PAGES[0].title, guild__isnull=True).exists()
        assert WikiPage.objects.filter(title=PAGES[1].title, archived_at__isnull=True).exists()

    def it_writes_nothing_on_a_dry_run():
        output = _run(dry_run=True)
        assert WikiPage.objects.count() == 0
        assert f"Would add {len(PAGES)} starter pages" in output
        assert f"  + {PAGES[0].title}" in output

    def describe_the_page_bodies():
        @pytest.mark.parametrize("entry", PAGES, ids=lambda entry: entry.title)
        def it_survives_the_sanitizer_unchanged(entry):
            # Stored as-is, so a body the sanitizer would rewrite on the first edit would
            # show the member a different page from the one they opened.
            assert sanitize_wiki_html(entry.body) == entry.body

        @pytest.mark.parametrize("entry", PAGES, ids=lambda entry: entry.title)
        def it_states_no_facts_only_headings(entry):
            text = re.sub(r"<h2[^>]*>.*?</h2>", "", render_wiki_content(entry.body))
            assert re.sub(r"<[^>]+>", "", text).strip() == ""

        @pytest.mark.parametrize("entry", PAGES, ids=lambda entry: entry.title)
        def it_uses_a_real_kind(entry):
            assert entry.kind in WikiPage.Kind.values
