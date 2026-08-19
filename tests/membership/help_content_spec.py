"""Drift guard for the help-center content data (§9) — runs in the normal suite.

Keeps three things in lockstep for every article in ``help_content.ARTICLES``:
the ``/static/help/…`` images its body references, the ``ShotSpec`` entries in
its ``screenshots`` list, and the PNGs committed under ``static/help/``. A
screenshot can't silently 404 on a help page, and an orphaned PNG (or spec)
can't linger. Also sanity-checks the slug tables (``UNLISTED_SLUGS``,
``LEGACY_SLUG_MAP``) against the seeded article slugs.

``ARTICLES`` lands in phase 6 — until then every loop below is empty and the
guard is trivially green; the phase-6 data lights the assertions up without
test changes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from membership import help_content

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_HELP_DIR = REPO_ROOT / "static" / "help"

# Screenshot filenames are NN-step-slug.png (§9).
FILENAME_PATTERN = re.compile(r"^\d{2}-[a-z0-9-]+\.png$")

# Any /static/help/<dir>/<file> reference in a Markdown body.
BODY_IMAGE_PATTERN = re.compile(r"/static/help/([^/\s\"')]+)/([^/\s\"')]+)")


def _articles() -> list[dict[str, Any]]:
    """The seeded articles — tolerantly empty until phase 6 defines ARTICLES."""
    return list(getattr(help_content, "ARTICLES", []))


def _body_image_refs(article: dict[str, Any]) -> list[tuple[str, str]]:
    """Every (directory, filename) pair the article body references under /static/help/."""
    return BODY_IMAGE_PATTERN.findall(article["body"])


def _shot_specs(article: dict[str, Any]) -> list[dict[str, Any]]:
    """The article's ShotSpec list (articles without shots simply have none)."""
    return list(article.get("screenshots", []))


def describe_help_content():
    def describe_screenshot_drift_guard():
        def it_backs_every_body_image_with_a_shot_spec_and_a_file_on_disk():
            for article in _articles():
                slug = article["slug"]
                declared = {shot["file"] for shot in _shot_specs(article)}
                for directory, filename in _body_image_refs(article):
                    ref = f"/static/help/{directory}/{filename}"
                    assert directory == slug, f"{slug!r} body references another article's folder: {ref}"
                    assert filename in declared, f"{slug!r} body references {ref} with no matching ShotSpec"
                    assert (STATIC_HELP_DIR / directory / filename).is_file(), (
                        f"{slug!r} body references {ref} but the file is not on disk — "
                        f"run scripts/capture-help-screenshots.sh and commit the PNGs"
                    )

        def it_references_every_shot_spec_file_in_its_body():
            for article in _articles():
                referenced = {filename for _, filename in _body_image_refs(article)}
                for shot in _shot_specs(article):
                    assert shot["file"] in referenced, (
                        f"{article['slug']!r} declares ShotSpec {shot['file']!r} but its body never shows it"
                    )

        def it_names_every_screenshot_nn_step_slug_png():
            for article in _articles():
                names = {shot["file"] for shot in _shot_specs(article)}
                names.update(filename for _, filename in _body_image_refs(article))
                for name in names:
                    assert FILENAME_PATTERN.fullmatch(name), (
                        f"{article['slug']!r} screenshot {name!r} does not match NN-step-slug.png"
                    )

    def describe_seed_data_integrity():
        def it_seeds_the_eight_approved_categories_in_audience_order():
            assert [(c["slug"], c["audience"], c["sort_order"]) for c in help_content.CATEGORIES] == [
                ("getting-started", "member", 10),
                ("guilds", "member", 20),
                ("classes", "member", 30),
                ("events-community", "member", 40),
                ("contributing", "developer", 50),
                ("teaching", "instructor", 50),
                ("running-a-guild", "guild_lead", 60),
                ("admin", "admin", 70),
            ]

        def it_keeps_article_slugs_unique():
            slugs = [article["slug"] for article in _articles()]
            assert len(slugs) == len(set(slugs)), "duplicate article slug in ARTICLES"

        def it_files_every_listed_article_under_its_approved_category():
            # The §10.2 P1 table — listed articles only (UNLISTED_SLUGS are exempt).
            approved = {
                "welcome-to-fog": "getting-started",
                "notifications": "getting-started",
                "guilds-and-guild-pages": "guilds",
                "getting-oriented": "guilds",
                "guild-voting": "guilds",
                "taking-a-class": "classes",
                "community-calendar": "events-community",
                "propose-an-event": "events-community",
                "announcements": "events-community",
                "member-directory": "events-community",
                "become-an-instructor": "teaching",
                "run-your-class": "teaching",
                "your-guild-page": "running-a-guild",
                "guild-staff-roles": "running-a-guild",
                "running-orientations": "running-a-guild",
                "guild-announcements": "running-a-guild",
                "guild-events-hours-notes": "running-a-guild",
                "approving-classes": "running-a-guild",
                "members-and-invites": "admin",
                "reviewing-classes-admin": "admin",
                "voting-admin": "admin",
                # The contributor/technical section (post-P1, owner-approved 2026-08-11).
                "fog-is-open-source": "contributing",
                "the-fog-api": "contributing",
                "how-fog-runs": "contributing",
                "logins-and-usernames": "contributing",
                "discord-and-fog": "contributing",
                "calendar-sync": "contributing",
            }
            listed = {a["slug"]: a["category"] for a in _articles() if a["slug"] not in help_content.UNLISTED_SLUGS}
            assert listed == approved

        def it_points_every_related_slug_at_a_seeded_article():
            seeded = {article["slug"] for article in _articles()}
            for article in _articles():
                for related in article["related"]:
                    assert related != article["slug"], f"{article['slug']!r} lists itself as related"
                    assert related in seeded, f"{article['slug']!r} related slug {related!r} is not seeded"

        def it_publishes_no_gated_slugs():
            # GATED surfaces (§10.5 rule 4): Discord connect stays undocumented until
            # the prod bot is confirmed; billing waits on tab_payments_enabled.
            seeded = {article["slug"] for article in _articles()}
            assert not seeded & {"connecting-discord", "notifications-and-your-settings", "billing-admin"}

    def describe_slug_tables():
        def it_keeps_unlisted_slugs_within_the_seeded_set():
            known = {article["slug"] for article in _articles()} | {"instructor-orientation"}
            assert help_content.UNLISTED_SLUGS <= known, (
                f"UNLISTED_SLUGS names unknown articles: {sorted(help_content.UNLISTED_SLUGS - known)}"
            )

        def it_covers_all_eight_legacy_slugs():
            assert set(help_content.LEGACY_SLUG_MAP) == {
                "guilds-and-guild-pages",
                "guild-voting",
                "taking-a-class",
                "orientations",
                "teaching-a-class",
                "the-community-calendar",
                "connecting-discord",
                "notifications-and-your-settings",
            }

        def it_maps_legacy_slugs_to_seeded_or_pending_articles():
            # Pending targets (P2 — §10.3) are tolerated: the landing's legacy-anchor
            # filter drops them until they're seeded, so the anchor lands on /help/.
            seeded = {article["slug"] for article in _articles()}
            for old, new in help_content.LEGACY_SLUG_MAP.items():
                assert new in seeded or new in help_content.PENDING_LEGACY_TARGETS, (
                    f"LEGACY_SLUG_MAP[{old!r}] -> {new!r} is neither a seeded article slug nor a pending P2 target"
                )

        def it_keeps_pending_targets_out_of_the_seeded_set():
            seeded = {article["slug"] for article in _articles()}
            assert not seeded & help_content.PENDING_LEGACY_TARGETS
