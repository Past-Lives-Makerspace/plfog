"""BDD specs for the seed_wiki_articles management command."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from membership.management.commands.seed_wiki_articles import ARTICLES
from membership.models import OrgInfoPage, WikiArticle

pytestmark = pytest.mark.django_db


def _run(**options: object) -> str:
    out = StringIO()
    call_command("seed_wiki_articles", stdout=out, **options)
    return out.getvalue()


def describe_seed_wiki_articles():
    def describe_a_first_run():
        def it_creates_every_guide(db):
            _run()
            assert WikiArticle.objects.count() == len(ARTICLES)

        def it_publishes_every_guide(db):
            _run()
            assert WikiArticle.objects.filter(is_published=False).count() == 0

        def it_orders_the_guides_by_their_position_in_the_list(db):
            _run()
            slugs = list(WikiArticle.objects.order_by("sort_order").values_list("slug", flat=True))
            assert slugs == [guide["slug"] for guide in ARTICLES]

        def it_fills_the_blank_intro(db):
            _run()
            assert OrgInfoPage.load().intro.startswith("Welcome to the Past Lives member hub")

        def it_reports_what_it_added(db):
            assert f"{len(ARTICLES)} added" in _run()

    def describe_the_connecting_discord_guide():
        def it_is_seeded_without_the_slash_command_list(db):
            _run()
            discord = WikiArticle.objects.get(slug="connecting-discord")
            for command in ("/link", "/join-guild", "/vote", "/whats-on", "/info", "/schedule-orientation"):
                assert command not in discord.body

        def it_notes_the_held_commands_in_the_report(db):
            assert "slash-command list held" in _run()

    def describe_re_running():
        def it_adds_no_duplicate_guides(db):
            _run()
            _run()
            assert WikiArticle.objects.count() == len(ARTICLES)

        def it_reports_the_second_pass_as_refreshed_not_added(db):
            _run()
            output = _run()
            assert "0 added" in output
            assert f"{len(ARTICLES)} refreshed" in output

        def it_restores_an_edited_body_to_the_seed_copy(db):
            _run()
            article = WikiArticle.objects.get(slug="guild-voting")
            article.body = "stale copy"
            article.save()
            _run()
            article.refresh_from_db()
            assert article.body != "stale copy"

        def it_leaves_an_admin_edited_intro_alone(db):
            _run()
            page = OrgInfoPage.load()
            page.intro = "Our own welcome."
            page.save()
            _run()
            assert OrgInfoPage.load().intro == "Our own welcome."

    def describe_dry_run():
        def it_writes_no_articles(db):
            _run(dry_run=True)
            assert WikiArticle.objects.count() == 0

        def it_leaves_the_intro_blank(db):
            _run(dry_run=True)
            assert OrgInfoPage.load().intro == ""

        def it_still_reports_what_it_would_add(db):
            output = _run(dry_run=True)
            assert output.startswith("Would seed")
            assert f"{len(ARTICLES)} added" in output

        def it_reports_no_new_guides_against_an_already_seeded_page(db):
            _run()
            output = _run(dry_run=True)
            assert "0 added" in output
            assert f"{len(ARTICLES)} refreshed" in output
