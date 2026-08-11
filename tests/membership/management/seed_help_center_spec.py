"""BDD specs for the seed_help_center management command (§10.4)."""

from __future__ import annotations

import io
from io import StringIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from PIL import Image

from membership.help_content import ARTICLES, CATEGORIES, PAGE_INTRO, PAGE_PARKING, PAGE_WHO_TO_CONTACT
from membership.models import HelpCategory, OrgInfoPage, WikiArticle
from tests.membership.factories import WikiArticleFactory

pytestmark = pytest.mark.django_db


def _run(**options: object) -> str:
    out = StringIO()
    call_command("seed_help_center", stdout=out, **options)
    return out.getvalue()


def describe_seed_help_center():
    def describe_a_first_run():
        def it_creates_every_category(db):
            _run()
            assert HelpCategory.objects.count() == len(CATEGORIES)
            for seed in CATEGORIES:
                category = HelpCategory.objects.get(slug=seed["slug"])
                assert (category.name, category.audience, category.sort_order) == (
                    seed["name"],
                    seed["audience"],
                    seed["sort_order"],
                )

        def it_creates_every_guide_published_in_its_category(db):
            _run()
            assert WikiArticle.objects.count() == len(ARTICLES)
            for seed in ARTICLES:
                article = WikiArticle.objects.get(slug=seed["slug"])
                assert article.is_published
                if seed["category"] is None:
                    # Unlisted flow pages (Spec D's instructor orientation) seed uncategorized.
                    assert article.category is None
                else:
                    assert article.category is not None and article.category.slug == seed["category"]
                assert (article.title, article.body, article.sort_order) == (
                    seed["title"],
                    seed["body"],
                    seed["sort_order"],
                )

        def it_sets_related_guides_across_the_two_passes(db):
            # welcome-to-fog's related targets are seeded *after* it in ARTICLES order,
            # so a working link proves the second pass ran once every row existed.
            _run()
            article = WikiArticle.objects.get(slug="welcome-to-fog")
            related = list(article.related_articles.values_list("slug", flat=True))
            assert sorted(related) == sorted(ARTICLES[0]["related"])

        def it_reports_what_it_added(db):
            report = _run()
            assert f"Seeded {len(CATEGORIES)} categories, {len(ARTICLES)} guides" in report
            assert f"{len(ARTICLES)} added, 0 refreshed, 0 retired." in report

    def describe_re_running():
        def it_adds_no_duplicate_rows(db):
            _run()
            _run()
            assert WikiArticle.objects.count() == len(ARTICLES)
            assert HelpCategory.objects.count() == len(CATEGORIES)

        def it_refreshes_an_edited_guide_back_to_the_seeded_copy(db):
            _run()
            article = WikiArticle.objects.get(slug="guild-voting")
            article.title = "Hand-edited title"
            article.body = "Hand-edited body"
            article.category = None
            article.save()
            _run()
            article.refresh_from_db()
            seed = next(a for a in ARTICLES if a["slug"] == "guild-voting")
            assert (article.title, article.body) == (seed["title"], seed["body"])
            assert article.category is not None and article.category.slug == seed["category"]

        def it_resets_hand_edited_related_picks_to_the_seed(db):
            _run()
            article = WikiArticle.objects.get(slug="guild-voting")
            article.related_articles.set([WikiArticle.objects.get(slug="taking-a-class")])
            _run()
            seed = next(a for a in ARTICLES if a["slug"] == "guild-voting")
            assert sorted(article.related_articles.values_list("slug", flat=True)) == sorted(seed["related"])

        def it_reports_the_second_pass_as_refreshed_not_added(db):
            _run()
            assert f"0 added, {len(ARTICLES)} refreshed, 0 retired." in _run()

    def describe_dry_run():
        def it_writes_no_rows(db):
            _run(dry_run=True)
            assert WikiArticle.objects.count() == 0
            assert HelpCategory.objects.count() == 0

        def it_previews_the_report_in_the_conditional(db):
            assert f"Would seed {len(CATEGORIES)} categories" in _run(dry_run=True)

        def it_leaves_existing_rows_untouched_and_reports_them_as_refreshed(db):
            _run()
            article = WikiArticle.objects.get(slug="guild-voting")
            article.title = "Hand-edited title"
            article.save(update_fields=["title"])
            report = _run(dry_run=True)
            assert f"Would seed {len(CATEGORIES)} categories, {len(ARTICLES)} guides: 0 added" in report
            article.refresh_from_db()
            assert article.title == "Hand-edited title"

        def it_does_not_retire_a_legacy_guide(db):
            WikiArticleFactory(page=OrgInfoPage.load(), slug="orientations", is_published=True)
            report = _run(dry_run=True)
            assert "1 retired." in report
            assert WikiArticle.objects.get(slug="orientations").is_published

    def describe_legacy_retirement():
        @pytest.fixture
        def legacy_rows(db):
            page = OrgInfoPage.load()
            return {
                slug: WikiArticleFactory(page=page, slug=slug, body=f"Old {slug} body", is_published=True)
                for slug in (
                    "orientations",
                    "teaching-a-class",
                    "the-community-calendar",
                    "connecting-discord",
                    "notifications-and-your-settings",
                )
            }

        def it_unpublishes_every_retired_legacy_guide(legacy_rows):
            _run()
            for row in legacy_rows.values():
                row.refresh_from_db()
                assert not row.is_published

        def it_preserves_the_retired_body_instead_of_deleting(legacy_rows):
            _run()
            row = legacy_rows["connecting-discord"]
            row.refresh_from_db()
            assert row.body == "Old connecting-discord body"

        def it_keeps_rewritten_in_place_guides_published(legacy_rows):
            # guild-voting is a LEGACY_SLUG_MAP key AND a seeded slug — its row is
            # refreshed, never retired.
            WikiArticleFactory(page=OrgInfoPage.load(), slug="guild-voting", is_published=True)
            _run()
            assert WikiArticle.objects.get(slug="guild-voting").is_published

        def it_counts_the_retired_guides_in_the_report(legacy_rows):
            assert "5 retired." in _run()

        def it_retires_nothing_on_a_second_pass(legacy_rows):
            _run()
            assert "0 retired." in _run()

    def describe_the_org_info_page_defaults():
        def it_fills_every_blank_block_on_a_first_run(db):
            _run()
            page = OrgInfoPage.load()
            assert page.intro == PAGE_INTRO
            assert page.parking == PAGE_PARKING
            assert page.who_to_contact == PAGE_WHO_TO_CONTACT
            assert page.banner_image
            assert "help-banner" in page.banner_image.name

        def it_reports_which_blocks_it_filled(db):
            assert "Filled blank page blocks: intro, parking, who_to_contact, banner_image." in _run()

        def it_never_clobbers_an_admin_edited_block(db):
            page = OrgInfoPage.load()
            page.intro = "Josh's intro"
            page.parking = "Josh's parking notes"
            page.save()
            _run()
            page.refresh_from_db()
            assert page.intro == "Josh's intro"
            assert page.parking == "Josh's parking notes"
            # The still-blank blocks were filled around the edits.
            assert page.who_to_contact == PAGE_WHO_TO_CONTACT

        def it_never_replaces_an_uploaded_banner(db):
            buf = io.BytesIO()
            Image.new("RGB", (400, 200), (120, 120, 120)).save(buf, format="PNG")
            page = OrgInfoPage.load()
            page.banner_image = SimpleUploadedFile("josh-banner.png", buf.getvalue(), content_type="image/png")
            page.save()
            _run()
            page.refresh_from_db()
            assert "josh-banner" in page.banner_image.name

        def it_is_idempotent_on_a_second_run(db):
            _run()
            first_banner = OrgInfoPage.load().banner_image.name
            report = _run()
            page = OrgInfoPage.load()
            assert page.banner_image.name == first_banner
            assert page.intro == PAGE_INTRO
            assert "Filled blank page blocks: none (all set already)." in report

        def it_fails_loudly_when_the_banner_asset_is_missing(db, monkeypatch):
            monkeypatch.setattr("django.contrib.staticfiles.finders.find", lambda path: None)
            with pytest.raises(CommandError, match="Default help banner missing"):
                _run()

        def describe_dry_run():
            def it_fills_nothing(db):
                report = _run(dry_run=True)
                page = OrgInfoPage.load()
                assert page.intro == ""
                assert page.parking == ""
                assert page.who_to_contact == ""
                assert not page.banner_image
                assert "Would fill blank page blocks: intro, parking, who_to_contact, banner_image." in report
