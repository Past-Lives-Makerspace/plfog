"""Seed the help-center categories and guides from ``membership/help_content.py``.

Writes one :class:`~membership.models.HelpCategory` per ``CATEGORIES`` entry
(``update_or_create`` on slug) and one :class:`~membership.models.WikiArticle`
per ``ARTICLES`` entry (``update_or_create`` on ``(page, slug)``), refreshing
title / body / category / sort_order in place — correcting copy in
``help_content.py`` and re-running is the intended workflow. ``related_articles``
is set in a second pass once every article row exists.

Retired legacy guides (a ``LEGACY_SLUG_MAP`` key that is no longer a seeded
slug) are **unpublished, never deleted** — an admin-edited body is preserved,
just hidden. The :class:`~membership.models.OrgInfoPage` singleton's blocks
(intro, parking, who-to-contact, banner image) are **filled only when still
blank** from the ``PAGE_*`` defaults in ``help_content.py`` — the same
fill-if-blank contract the old ``seed_wiki_articles._sync_intro`` had, so an
admin edit is never clobbered. One exception: an intro exactly equal to the
retired seed's default (``RETIRED_INTRO``) counts as stale seed output and is
replaced — production still carries that text. The banner default ships with the repo at
``static/help/_defaults/`` and is saved into each environment's media storage.

Replaces ``seed_wiki_articles`` (its bodies were absorbed as raw material for
the P1 articles); the deploy step is now ``manage.py seed_help_center``.

Usage::

    manage.py seed_help_center [--dry-run]
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from membership.help_content import (
    ARTICLES,
    CATEGORIES,
    LEGACY_SLUG_MAP,
    PAGE_BANNER_STATIC_PATH,
    PAGE_INTRO,
    PAGE_PARKING,
    PAGE_WHO_TO_CONTACT,
    RETIRED_INTRO,
)


class Command(BaseCommand):
    help = (
        "Seed the help-center categories and guides from membership/help_content.py. "
        "Idempotent (categories keyed on slug, guides on slug); use --dry-run to preview."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing any rows.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from membership.models import HelpCategory, OrgInfoPage, WikiArticle

        dry_run = bool(options["dry_run"])
        page = OrgInfoPage.load()

        categories = self._sync_categories(HelpCategory, dry_run=dry_run)
        totals = {"created": 0, "updated": 0}
        for article in ARTICLES:
            created = self._sync_article(page, article, categories, WikiArticle, dry_run=dry_run)
            totals["created" if created else "updated"] += 1
        self._sync_related(page, WikiArticle, dry_run=dry_run)
        retired = self._retire_legacy(page, WikiArticle, dry_run=dry_run)
        filled = self._sync_page_defaults(page, dry_run=dry_run)
        self._report(totals, retired, filled, dry_run=dry_run)

    @staticmethod
    def _sync_categories(category_model: Any, *, dry_run: bool) -> dict[str, Any]:
        """Create or refresh every seeded category, returning them keyed by slug."""
        categories: dict[str, Any] = {}
        for seed in CATEGORIES:
            values = {"name": seed["name"], "audience": seed["audience"], "sort_order": seed["sort_order"]}
            if dry_run:
                existing = category_model.objects.filter(slug=seed["slug"]).first()
                categories[seed["slug"]] = existing if existing is not None else category_model(slug=seed["slug"])
                continue
            category, _created = category_model.objects.update_or_create(slug=seed["slug"], defaults=values)
            categories[seed["slug"]] = category
        return categories

    @staticmethod
    def _sync_article(
        page: Any, seed: dict[str, Any], categories: dict[str, Any], article_model: Any, *, dry_run: bool
    ) -> bool:
        """Create or refresh one guide, keyed on ``(page, slug)``. Returns True when it is new.

        ``category: None`` seeds an uncategorized guide — the unlisted flow pages
        (``UNLISTED_SLUGS``, e.g. the instructor orientation) live off the browsing
        surfaces entirely, so they belong to no category.
        """
        category = categories[seed["category"]] if seed["category"] is not None else None
        values = {
            "title": seed["title"],
            "body": seed["body"],
            "category": category if category is not None and category.pk is not None else None,
            "sort_order": seed["sort_order"],
        }
        existing = article_model.objects.filter(page=page, slug=seed["slug"]).first()
        if existing is None:
            if not dry_run:
                article_model.objects.create(page=page, slug=seed["slug"], **values)
            return True
        for name, value in values.items():
            setattr(existing, name, value)
        if not dry_run:
            existing.save(update_fields=[*values])
        return False

    @staticmethod
    def _sync_related(page: Any, article_model: Any, *, dry_run: bool) -> None:
        """Second pass: point each guide's ``related_articles`` at its seeded targets.

        Runs after every article row exists so forward references resolve. ``.set()``
        replaces the seed-owned picks wholesale — the M2M rows are seed data, and the
        seed order is the display order (explicit picks lead ``related_for_display``).
        """
        if dry_run:
            return
        rows = {a.slug: a for a in article_model.objects.filter(page=page)}
        for seed in ARTICLES:
            rows[seed["slug"]].related_articles.set([rows[slug] for slug in seed["related"]])

    @staticmethod
    def _retire_legacy(page: Any, article_model: Any, *, dry_run: bool) -> int:
        """Unpublish (never delete) legacy guides whose slug left the seeded set.

        A ``LEGACY_SLUG_MAP`` key that is also a seeded slug was rewritten in place
        and keeps its row; the rest (split, renamed, or retired guides) are hidden so
        an admin-edited body survives. Returns how many rows were (or would be) hidden.
        """
        seeded = {article["slug"] for article in ARTICLES}
        retired_slugs = [slug for slug in LEGACY_SLUG_MAP if slug not in seeded]
        rows = article_model.objects.filter(page=page, slug__in=retired_slugs, is_published=True)
        count = rows.count()
        if not dry_run and count:
            rows.update(is_published=False)
        return count

    @staticmethod
    def _sync_page_defaults(page: Any, *, dry_run: bool) -> list[str]:
        """Fill each still-blank OrgInfoPage block with its launch default.

        Only blank blocks are written — an admin-edited intro, parking note,
        who-to-contact list, or uploaded banner is never clobbered, and a
        re-run is a no-op. The banner default is read from the committed
        ``static/help/_defaults/`` asset and saved through the ``ImageField``
        into this environment's media storage (so ``OrgInfoPage.save()``'s
        normalization runs as for any upload). Returns the filled field names.
        """
        from django.contrib.staticfiles import finders
        from django.core.files import File

        text_defaults = (
            ("intro", PAGE_INTRO),
            ("parking", PAGE_PARKING),
            ("who_to_contact", PAGE_WHO_TO_CONTACT),
        )

        def _is_stale(name: str) -> bool:
            current = getattr(page, name).strip()
            # The retired seed's intro counts as blank: production still carries it,
            # and replacing known seed output is the point. Hand edits never match.
            return not current or (name == "intro" and current == RETIRED_INTRO.strip())

        filled = [name for name, _default in text_defaults if _is_stale(name)]
        banner_blank = not page.banner_image
        if banner_blank:
            filled.append("banner_image")
        if dry_run or not filled:
            return filled
        for name, default in text_defaults:
            if name in filled:
                setattr(page, name, default)
        if banner_blank:
            asset = finders.find(PAGE_BANNER_STATIC_PATH)
            if asset is None:
                raise CommandError(f"Default help banner missing from the repo: static/{PAGE_BANNER_STATIC_PATH}")
            with open(asset, "rb") as fh:
                page.banner_image.save(PAGE_BANNER_STATIC_PATH.rsplit("/", 1)[-1], File(fh), save=False)
        page.save(update_fields=[*filled, "updated_at"])
        return filled

    def _report(self, totals: dict[str, int], retired: int, filled: list[str], *, dry_run: bool) -> None:
        prefix = "Would seed" if dry_run else "Seeded"
        self.stdout.write(
            f"{prefix} {len(CATEGORIES)} categories, {len(ARTICLES)} guides: "
            f"{totals['created']} added, {totals['updated']} refreshed, {retired} retired."
        )
        blocks = ", ".join(filled) if filled else "none (all set already)"
        self.stdout.write(f"{'Would fill' if dry_run else 'Filled'} blank page blocks: {blocks}.")
