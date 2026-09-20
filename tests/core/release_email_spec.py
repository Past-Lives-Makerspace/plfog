"""BDD specs for the release-update email renderer and its resolvers."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from django.template.loader import render_to_string

from core import release_email
from core.models import EventDelivery, SiteConfiguration
from core.release_email import (
    Card,
    FeaturePage,
    _humanize_slug,
    _index_pages,
    build_release_cards,
    captured_feature_slugs,
    feature_page_url,
    feature_shot_choices,
    feature_shot_key,
    current_release_entries,
    line_entries,
    parse_lines,
    render_release_email,
    resolve_feature_shot_url,
    save_feature_shot,
    send_release_test,
)

# A changelog in both of its halves. The first two entries are the CURRENT BATCH — unswept
# changelog.d/ fragments, which carry a date and no version number, because a fragment cannot
# know its own release number without merge order (see plfog.changelog). Below them is swept
# history, carrying the numbers those releases shipped under. The release email covers the
# batch by default and reaches back into the numbered lines only when given ``--lines``.
# One batch entry has a screenshot slug so the renderer paths are exercised.
FIXTURE_CHANGELOG: list[dict[str, object]] = [
    {
        "date": "2026-07-10",
        "title": "A home base when you sign in",
        "changes": ["See what's coming up.", "Jump to everywhere you go."],
        "screenshot": "home",
    },
    {
        "date": "2026-07-09",
        "title": "One place for how our space works",
        "changes": ["Map, parking, and the code of conduct."],
    },
    {
        "version": "0.20.5",
        "date": "2026-07-08",
        "title": "A release already swept into history",
        "changes": ["Folded into changelog/history.json, so it keeps its number."],
    },
    {
        "version": "0.19.9",
        "date": "2026-06-01",
        "title": "An older release line",
        "changes": ["Not in the current line."],
    },
]


class _FakeStorage:
    """A minimal default_storage stand-in: tracks which keys exist / were saved / deleted."""

    def __init__(self, existing: set[str] | None = None, *, dir_missing: bool = False) -> None:
        self.existing = set(existing or ())
        self.saved: dict[str, bytes] = {}
        self.deleted: list[str] = []
        # FileSystemStorage raises FileNotFoundError when the dir was never created; S3/R2
        # returns empty. `dir_missing` reproduces the FileSystem case for the guard test.
        self.dir_missing = dir_missing

    def exists(self, key: str) -> bool:
        return key in self.existing

    def url(self, key: str) -> str:
        return f"https://cdn.example/{key}"

    def listdir(self, prefix: str) -> tuple[list[str], list[str]]:
        """Mirror the storage backends: `(dirs, files)` where `files` are basenames."""
        if self.dir_missing:
            raise FileNotFoundError(prefix)
        marker = prefix.rstrip("/") + "/"
        files = sorted(key.rsplit("/", 1)[-1] for key in self.existing if key.startswith(marker))
        return [], files

    def save(self, key: str, content: object) -> str:
        self.saved[key] = content.read()  # type: ignore[attr-defined]
        self.existing.add(key)
        return key

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.existing.discard(key)


@pytest.fixture
def fake_storage(monkeypatch):
    """Replace the module's default_storage with a FakeStorage; return it for assertions."""
    storage = _FakeStorage()
    monkeypatch.setattr(release_email, "default_storage", storage)
    return storage


@pytest.fixture
def fixture_changelog(monkeypatch):
    """Point the renderer at FIXTURE_CHANGELOG (read lazily inside current_release_entries)."""
    monkeypatch.setattr("plfog.version.CHANGELOG", FIXTURE_CHANGELOG)


def describe_feature_page():
    def describe_path():
        def it_resolves_the_url_name(db):
            page = FeaturePage(slug="home", label="Home", url_name="hub_home")
            assert page.path == "/home/"

        def it_appends_the_query_string(db):
            page = FeaturePage(slug="g", label="Guilds", url_name="hub_user_settings", query="?tab=guilds")
            assert page.path.endswith("?tab=guilds")


def describe_index_pages():
    def it_indexes_by_slug():
        pages = [FeaturePage("a", "A", "hub_home"), FeaturePage("b", "B", "hub_help")]
        assert set(_index_pages(pages)) == {"a", "b"}

    def describe_with_a_duplicate_slug():
        def it_raises_at_build_time():
            pages = [FeaturePage("dupe", "One", "hub_home"), FeaturePage("dupe", "Two", "hub_help")]
            with pytest.raises(ValueError, match="Duplicate FeaturePage slug"):
                _index_pages(pages)


def describe_feature_shot_key():
    def it_builds_the_stable_storage_key():
        assert feature_shot_key("home") == "email/features/home.png"


def describe_resolve_feature_shot_url():
    def it_returns_the_url_when_the_asset_exists(fake_storage):
        fake_storage.existing.add("email/features/home.png")
        assert resolve_feature_shot_url("home") == "https://cdn.example/email/features/home.png"

    def it_returns_empty_for_a_known_but_uncaptured_slug(fake_storage):
        assert resolve_feature_shot_url("home") == ""

    def it_returns_empty_for_an_empty_slug(fake_storage):
        assert resolve_feature_shot_url("") == ""


def describe_feature_page_url():
    def it_builds_the_absolute_url_for_a_known_slug(db, settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        assert feature_page_url("home") == "https://members.example/home/"

    def it_returns_empty_for_an_unknown_slug(db):
        assert feature_page_url("not-a-page") == ""


def describe_humanize_slug():
    def it_titles_a_hyphenated_slug():
        assert _humanize_slug("guild-pages") == "Guild pages"

    def it_titles_an_underscored_slug():
        assert _humanize_slug("qr_codes") == "Qr codes"


def describe_captured_feature_slugs():
    def it_returns_sorted_slugs_of_captured_pngs(fake_storage):
        fake_storage.existing.update({"email/features/home.png", "email/features/guild-pages.png"})
        assert captured_feature_slugs() == ["guild-pages", "home"]

    def it_ignores_non_png_objects(fake_storage):
        fake_storage.existing.update({"email/features/home.png", "email/features/notes.txt"})
        assert captured_feature_slugs() == ["home"]

    def it_returns_empty_when_nothing_is_captured(fake_storage):
        assert captured_feature_slugs() == []

    def describe_when_the_prefix_dir_is_missing():
        def it_returns_empty_without_raising(monkeypatch):
            # FileSystemStorage raises FileNotFoundError on a never-created dir; the guard
            # must swallow it and report "nothing captured", not 500.
            storage = _FakeStorage(dir_missing=True)
            monkeypatch.setattr(release_email, "default_storage", storage)
            assert captured_feature_slugs() == []


def describe_feature_shot_choices():
    # These take `db` since #405: the choice list reads each page's feature state, so a
    # screenshot of a feature an admin has just hidden is no longer offered.
    def it_leads_with_no_screenshot(fake_storage, db):
        assert feature_shot_choices()[0] == ("", "No screenshot")

    def it_offers_a_captured_registry_slug_with_its_friendly_label(fake_storage, db):
        fake_storage.existing.add("email/features/home.png")
        choices = feature_shot_choices()
        assert ("home", "Member home dashboard") in choices

    def it_does_not_offer_a_registry_slug_with_no_asset(fake_storage, db):
        fake_storage.existing.add("email/features/home.png")
        # org-info is in the registry but was never captured → not offered.
        assert all(value != "org-info" for value, _label in feature_shot_choices())

    def it_offers_a_bespoke_captured_slug_after_the_curated_ones(fake_storage, db):
        fake_storage.existing.update({"email/features/home.png", "email/features/guild-pages.png"})
        choices = feature_shot_choices()
        # The curated 'home' comes before the bespoke, humanized 'guild-pages'.
        assert choices.index(("home", "Member home dashboard")) < choices.index(("guild-pages", "Guild pages"))

    def it_orders_bespoke_slugs_alphabetically_after_curated(fake_storage, db):
        fake_storage.existing.update({"email/features/qr-codes.png", "email/features/guild-pages.png"})
        # Neither is in the registry, so both are appended alphabetically.
        assert feature_shot_choices() == [
            ("", "No screenshot"),
            ("guild-pages", "Guild pages"),
            ("qr-codes", "Qr codes"),
        ]

    def it_does_not_duplicate_a_slug_that_is_both_registry_and_captured(fake_storage, db):
        fake_storage.existing.add("email/features/home.png")
        values = [value for value, _label in feature_shot_choices()]
        assert values.count("home") == 1


def describe_line_entries():
    def it_filters_to_a_single_line(fixture_changelog):
        assert [str(e["title"]) for e in line_entries(["0.20"])] == ["A release already swept into history"]

    def it_spans_several_lines_preserving_changelog_order(fixture_changelog):
        titles = [str(e["title"]) for e in line_entries(["0.20", "0.19"])]
        # CHANGELOG order (newest-first) is preserved across the union.
        assert titles == ["A release already swept into history", "An older release line"]

    def it_returns_empty_for_a_line_with_no_entries(fixture_changelog):
        assert line_entries(["3.0"]) == []

    def it_skips_the_current_batch_rather_than_crashing_on_it(fixture_changelog):
        # Batch entries have no version key at all. Reading one as ``e["version"]`` would
        # raise, which would take down every release email the moment anything was unswept.
        assert all("home base" not in str(e["title"]) for e in line_entries(["0.20", "0.19"]))


def describe_current_release_entries():
    def it_returns_everything_not_yet_swept_newest_first(fixture_changelog):
        assert [str(e["title"]) for e in current_release_entries()] == [
            "A home base when you sign in",
            "One place for how our space works",
        ]

    def it_excludes_swept_history(fixture_changelog):
        titles = [str(e["title"]) for e in current_release_entries()]
        assert "A release already swept into history" not in titles

    def it_is_empty_right_after_a_sweep(monkeypatch):
        monkeypatch.setattr(
            "plfog.version.CHANGELOG",
            [{"version": "1.62.1", "date": "2026-09-13", "title": "All swept", "changes": []}],
        )
        assert current_release_entries() == []


def describe_parse_lines():
    def it_parses_and_trims_a_comma_list():
        assert parse_lines(" 0.20 , 0.21 ") == ["0.20", "0.21"]

    def it_raises_on_an_empty_value():
        with pytest.raises(ValueError, match="at least one"):
            parse_lines("  ")

    def it_raises_on_a_token_that_is_not_major_minor():
        with pytest.raises(ValueError, match="MAJOR.MINOR"):
            parse_lines("0.20,banana")

    def it_raises_on_a_full_version_token():
        with pytest.raises(ValueError, match="MAJOR.MINOR"):
            parse_lines("0.20.5")


def describe_build_release_cards():
    def it_yields_one_card_per_current_batch_entry_newest_first(db, fixture_changelog, fake_storage):
        cards = build_release_cards()
        assert [c.title for c in cards] == [
            "A home base when you sign in",
            "One place for how our space works",
        ]

    def it_excludes_anything_already_swept(db, fixture_changelog, fake_storage):
        cards = build_release_cards()
        assert all("older release line" not in c.title for c in cards)
        assert all("already swept" not in c.title for c in cards)

    def describe_when_given_explicit_lines():
        # These run against the REAL CHANGELOG (no fixture) so the 0.20 + 0.21 batches
        # are both present — that is exactly the span the release email needs to cover.
        def it_spans_both_named_lines(db, fake_storage):
            titles = [c.title for c in build_release_cards(lines=["0.20", "0.21"])]
            assert "A home base when you sign in" in titles  # a 0.20 feature
            assert "Your notifications, cleaned up" in titles  # a 0.21 feature

        def it_scopes_to_only_the_named_line(db, fake_storage):
            titles = [c.title for c in build_release_cards(lines=["0.21"])]
            assert "Your notifications, cleaned up" in titles
            assert "A home base when you sign in" not in titles  # 0.20 is out of scope

    def it_links_the_title_when_the_slug_maps_to_a_feature_page(db, fixture_changelog, fake_storage, settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        cards = build_release_cards()
        assert cards[0].feature_url == "https://members.example/home/"
        assert cards[1].feature_url == ""  # no screenshot slug → plain title


def describe_render_release_email():
    def it_renders_the_preheader_hero_cards_and_cta(db, fixture_changelog, fake_storage):
        fake_storage.existing.add("email/features/home.png")
        cards = build_release_cards()
        html, _text = render_release_email(
            "0.20.5",
            subject="What's new",
            preheader="Two fresh things this week",
            intro="<p>Here's what shipped.</p>",
            cards=cards,
        )
        assert "Two fresh things this week" in html  # preheader div
        assert "display: none" in html  # preheader is hidden
        assert "v0.20" in html  # version badge
        assert "2026-07-10" in html  # release date
        assert "Heads-Up: New Member Portal Features" in html
        assert "A home base when you sign in" in html
        assert "See what&#x27;s coming up." in html or "See what's coming up." in html  # bullet
        assert "Visit the Member Portal" in html  # CTA button
        assert "play.google.com/store/apps/details?id=app.pastlives.hub" in html  # the shared footer's Play badge
        assert "Here&#x27;s what shipped." in html or "Here's what shipped." in html  # intro

    def it_renders_an_image_for_a_captured_card_with_alt(db, fixture_changelog, fake_storage):
        fake_storage.existing.add("email/features/home.png")
        cards = build_release_cards()
        html, _text = render_release_email("0.20.5", subject="s", preheader="p", intro="", cards=cards)
        assert 'src="https://cdn.example/email/features/home.png"' in html
        assert 'alt="A home base when you sign in"' in html

    def it_renders_a_text_only_card_when_there_is_no_screenshot(db, fixture_changelog, fake_storage):
        cards = build_release_cards()  # nothing captured → no image on either card
        html, _text = render_release_email("0.20.5", subject="s", preheader="p", intro="", cards=cards)
        assert "One place for how our space works" in html  # card still appears
        assert "email/features/" not in html  # but no feature-card screenshot (the Play badge img is always present)

    def it_keeps_the_text_part_in_sync_with_the_html(db, fixture_changelog, fake_storage):
        cards = build_release_cards()
        _html, text = render_release_email(
            "0.20.5",
            subject="What's new at Past Lives",
            preheader="p",
            intro="<p>Here's what shipped.</p>",
            cards=cards,
        )
        assert "What's new at Past Lives" in text
        assert "Here's what shipped." in text  # intro flattened
        assert "## A home base when you sign in" in text
        assert "• See what's coming up." in text
        assert "## One place for how our space works" in text
        assert "Visit the Member Portal: " in text
        assert "play.google.com/store/apps/details?id=app.pastlives.hub" in text
        assert "unsubscribe" in text

    def describe_store_badges():
        # #467: the release email relies on the shared footer, so the badges show exactly once
        # in each part and no store URL is hardcoded in the renderer.
        _PLAY = "https://play.google.com/store/apps/details?id=app.pastlives.hub"
        _IOS = "https://apps.apple.com/app/id1234567890"

        def it_shows_each_badge_exactly_once_in_html_and_text(db, fixture_changelog, fake_storage):
            config = SiteConfiguration.load()
            config.app_store_url = _IOS
            config.save()
            html, text = render_release_email("0.20.5", subject="s", preheader="p", intro="", cards=[])
            assert html.count(_PLAY) == 1
            assert html.count(_IOS) == 1
            assert html.count('alt="Get it on Google Play"') == 1
            assert html.count('alt="Download on the App Store"') == 1
            assert text.count(_PLAY) == 1
            assert text.count(_IOS) == 1

        def it_no_longer_carries_its_own_play_store_sentence(db, fixture_changelog, fake_storage):
            html, text = render_release_email("0.20.5", subject="s", preheader="p", intro="", cards=[])
            assert "officially on the Play Store" not in html
            assert "officially on the Play Store" not in text

        def it_ends_the_text_part_with_the_shared_footer(db, fixture_changelog, fake_storage):
            _html, text = render_release_email("0.20.5", subject="s", preheader="p", intro="", cards=[])
            footer = render_to_string("membership/emails/_footer.txt")
            assert text.endswith(footer)
            # The text part gains the tokenised preferences link the HTML already carried.
            assert "t=__PL_PREFS_TOKEN__" in text

    def it_omits_a_card_that_is_not_included(db, fixture_changelog, fake_storage):
        cards = build_release_cards()
        cards[1].included = False
        html, text = render_release_email("0.20.5", subject="s", preheader="p", intro="", cards=cards)
        assert "A home base when you sign in" in html
        assert "One place for how our space works" not in html
        assert "One place for how our space works" not in text

    def describe_when_spanning_lines():
        # Real CHANGELOG so 0.20 + 0.21 both exist; the badge tracks the newest line.
        def it_badges_the_newest_selected_line(db, fake_storage):
            cards = build_release_cards(lines=["0.20", "0.21"])
            html, _text = render_release_email(
                "0.21.4", subject="s", preheader="p", intro="", cards=cards, lines=["0.20", "0.21"]
            )
            assert "v0.21" in html  # newest of the spanned lines drives the badge

    def describe_with_no_entries_for_the_line():
        def it_renders_without_a_date(db, fixture_changelog, fake_storage):
            # A line with no changelog entries (near-unreachable) → empty hero date, no cards.
            html, _text = render_release_email("3.0.0", subject="s", preheader="p", intro="", cards=[])
            assert "v3.0" in html
            assert "Heads-Up: New Member Portal Features" in html


def describe_save_feature_shot():
    def it_saves_new_bytes_and_returns_the_url(fake_storage):
        url = save_feature_shot("home", b"pngdata")
        assert fake_storage.saved["email/features/home.png"] == b"pngdata"
        assert url == "https://cdn.example/email/features/home.png"

    def describe_when_the_asset_already_exists():
        def it_overwrites_in_place(fake_storage):
            fake_storage.existing.add("email/features/home.png")
            save_feature_shot("home", b"new")
            assert fake_storage.deleted == ["email/features/home.png"]
            assert fake_storage.saved["email/features/home.png"] == b"new"


def describe_send_release_test():
    def it_sends_only_to_the_admin_and_never_touches_the_spine(db, mailoutbox):
        admin = User.objects.create_user(username="lead", email="lead@example.com", password="p")
        send_release_test(admin, "<html>hi</html>", "hi", "Test — What's new")
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["lead@example.com"]
        assert mailoutbox[0].subject == "Test — What's new"
        # A test is a direct send — no EventDelivery ledger row is written, so it can
        # never consume the real site_announcement period.
        assert EventDelivery.objects.count() == 0


def describe_card():
    def it_defaults_to_included_with_no_screenshot():
        card = Card(title="X")
        assert card.included is True
        assert card.screenshot_url == ""
        assert card.bullets == []


def describe_available_feature_pages():
    """#405 AC 5b: a switched-off feature drops out of the screenshot registry.

    Same bug class as the kiosk slide. The capture job walks this registry and publishes what
    it finds, so a Spaces page left in the list while Spaces is hidden would have the release
    email carrying a photograph of its own 404.
    """

    def it_lists_every_page_while_every_feature_is_on(db):
        from core.release_email import FEATURE_PAGES, available_feature_pages
        from tests.features import turn_on

        for key in ("spaces", "directory"):
            turn_on(key)
        assert available_feature_pages() == FEATURE_PAGES

    def it_drops_a_hidden_features_page(db):
        from core.release_email import available_feature_pages
        from tests.features import hide

        hide("spaces")
        assert "spaces" not in {page.slug for page in available_feature_pages()}

    def it_drops_a_coming_soon_features_page_too(db):
        from core.release_email import available_feature_pages
        from tests.features import coming_soon

        coming_soon("directory", "Launching Sept 30th!")
        assert "member-directory" not in {page.slug for page in available_feature_pages()}

    def it_keeps_pages_that_belong_to_no_feature(db):
        from core.features import FEATURES
        from core.release_email import available_feature_pages
        from tests.features import hide

        for feature in FEATURES:
            hide(feature.key)
        slugs = {page.slug for page in available_feature_pages()}
        # Home, Help, the guild directory, notifications and the calendar are not switchable.
        assert {"home", "help", "guild-directory", "notifications", "community-calendar"} <= slugs
