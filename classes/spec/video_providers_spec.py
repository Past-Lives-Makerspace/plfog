"""BDD specs for the video provider registry (#368 item 8).

Recognition is the whole feature: what a member may paste, what is refused, and which
of the two renderings the link gets. The lookalike-host cases are the point of the
strict host set, so they are spelled out per provider.
"""

from __future__ import annotations

import re

import pytest
from django.core.exceptions import ValidationError

from classes import video_providers
from classes.video_providers import (
    FACEBOOK,
    INSTAGRAM,
    PROVIDERS,
    YOUTUBE,
    UrlRule,
    VideoProvider,
    _joined,
    recognize,
    unsupported_video_message,
    validate_video_url,
    validate_youtube_url,
)

YT_ID = "dQw4w9WgXcQ"


def describe_recognize():
    def describe_youtube():
        @pytest.mark.parametrize(
            "url",
            [
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://youtube.com/watch?v=dQw4w9WgXcQ",
                "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ&t=30",
                "https://www.youtube.com/embed/dQw4w9WgXcQ",
                "https://www.youtube.com/shorts/dQw4w9WgXcQ",
                "https://youtu.be/dQw4w9WgXcQ",
                "https://youtu.be/dQw4w9WgXcQ?t=12",
                "https://www.youtu.be/dQw4w9WgXcQ",
                "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
                "https://youtube-nocookie.com/embed/dQw4w9WgXcQ",
                "youtube.com/watch?v=dQw4w9WgXcQ",
            ],
            ids=lambda url: url,
        )
        def it_recognizes_every_youtube_shape(url):
            link = recognize(url)
            assert link is not None and link.provider is YOUTUBE
            assert link.video_id == YT_ID

        def it_embeds_through_the_nocookie_host():
            link = recognize("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            assert link.is_embed
            assert link.embed_src == f"https://www.youtube-nocookie.com/embed/{YT_ID}"

        @pytest.mark.parametrize(
            "url",
            [
                "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
                "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
                "https://evil.test/youtube.com/watch?v=dQw4w9WgXcQ",
                "https://youtube.com@evil.test/watch?v=dQw4w9WgXcQ",
                r"https://evil.test\@www.youtube.com/watch?v=dQw4w9WgXcQ",
            ],
            ids=lambda url: url,
        )
        def it_refuses_a_youtube_lookalike(url):
            assert recognize(url) is None

        def it_refuses_a_channel_path_that_is_not_a_video():
            assert recognize("https://www.youtube.com/@pastlivespdx") is None

    def describe_instagram():
        @pytest.mark.parametrize(
            "url",
            [
                "https://www.instagram.com/p/CxYzAbCdEfG/",
                "https://instagram.com/p/CxYzAbCdEfG",
                "https://www.instagram.com/reel/CxYzAbCdEfG/",
                "https://www.instagram.com/reels/CxYzAbCdEfG/",
                "https://www.instagram.com/tv/CxYzAbCdEfG/",
                "https://m.instagram.com/reel/CxYzAbCdEfG/",
                "https://www.instagram.com/reel/CxYzAbCdEfG/?igsh=abc123&utm_source=ig_web",
                "https://www.instagram.com/pastlivespdx/reel/CxYzAbCdEfG/",
                "instagram.com/reel/CxYzAbCdEfG/",
            ],
            ids=lambda url: url,
        )
        def it_recognizes_every_instagram_shape(url):
            link = recognize(url)
            assert link is not None and link.provider is INSTAGRAM
            assert link.video_id == "CxYzAbCdEfG"

        def it_renders_as_a_card_not_an_embed():
            link = recognize("https://www.instagram.com/reel/CxYzAbCdEfG/")
            assert not link.is_embed
            assert link.embed_src == ""
            assert link.url == "https://www.instagram.com/reel/CxYzAbCdEfG/"

        @pytest.mark.parametrize(
            "url",
            [
                "https://instagram.com.evil.test/reel/CxYzAbCdEfG/",
                "https://notinstagram.com/reel/CxYzAbCdEfG/",
                "https://evil.test/instagram.com/reel/CxYzAbCdEfG/",
                "https://instagram.com@evil.test/reel/CxYzAbCdEfG/",
                r"https://evil.test\@www.instagram.com/reel/CxYzAbCdEfG/",
            ],
            ids=lambda url: url,
        )
        def it_refuses_an_instagram_lookalike(url):
            assert recognize(url) is None

        def it_refuses_a_profile_page_that_is_not_a_video():
            assert recognize("https://www.instagram.com/pastlivespdx/") is None

    def describe_facebook():
        @pytest.mark.parametrize(
            ("url", "video_id"),
            [
                ("https://www.facebook.com/watch/?v=1234567890", "1234567890"),
                ("https://www.facebook.com/watch?v=1234567890", "1234567890"),
                ("https://m.facebook.com/watch/?v=1234567890", "1234567890"),
                ("https://web.facebook.com/watch/?v=1234567890", "1234567890"),
                ("https://www.facebook.com/watch/?ref=share&v=1234567890", "1234567890"),
                ("https://www.facebook.com/video.php?v=1234567890", "1234567890"),
                ("https://www.facebook.com/PastLivesPDX/videos/1234567890/", "1234567890"),
                ("https://www.facebook.com/PastLivesPDX/videos/1234567890", "1234567890"),
                ("https://www.facebook.com/PastLivesPDX/videos/forge-night/1234567890/", "1234567890"),
                ("https://www.facebook.com/reel/1234567890/", "1234567890"),
                ("https://www.facebook.com/share/v/aBcD1234/", "aBcD1234"),
                ("https://fb.com/PastLivesPDX/videos/1234567890/", "1234567890"),
                ("https://fb.watch/aBcD1234ef/", "aBcD1234ef"),
                ("https://www.fb.watch/aBcD1234ef/", "aBcD1234ef"),
                ("facebook.com/watch/?v=1234567890", "1234567890"),
            ],
            ids=lambda value: str(value),
        )
        def it_recognizes_every_facebook_shape(url, video_id):
            link = recognize(url)
            assert link is not None and link.provider is FACEBOOK
            assert link.video_id == video_id

        def it_renders_as_a_card_not_an_embed():
            link = recognize("https://www.facebook.com/watch/?v=1234567890")
            assert not link.is_embed
            assert link.embed_src == ""

        @pytest.mark.parametrize(
            "url",
            [
                "https://facebook.com.evil.test/watch/?v=1234567890",
                "https://notfacebook.com/watch/?v=1234567890",
                "https://evil.test/facebook.com/watch/?v=1234567890",
                "https://facebook.com@evil.test/watch/?v=1234567890",
                "https://fb.watch.evil.test/aBcD1234ef/",
                r"https://evil.test\@www.facebook.com/watch/?v=1234567890",
                r"https://evil.test\@fb.watch/aBcD1234ef/",
            ],
            ids=lambda url: url,
        )
        def it_refuses_a_facebook_lookalike(url):
            assert recognize(url) is None

        def it_refuses_a_page_path_that_is_not_a_video():
            assert recognize("https://www.facebook.com/PastLivesPDX/") is None

        def it_refuses_ascii_lookalike_digits_in_an_id():
            # A bare \\d in a rule would match every Unicode digit; these ids are ASCII, like _ID.
            assert recognize("https://www.facebook.com/watch/?v=\u0661\u0662\u0663\u0664") is None

        @pytest.mark.parametrize("share", ["p", "r", "g"], ids=lambda letter: f"/share/{letter}/")
        def it_refuses_a_share_link_that_is_not_the_video_form(share):
            # Only /share/v/ is documented as the video share. A post or reel share would
            # render a card promising a video, so it stays out until somebody confirms it.
            assert recognize(f"https://www.facebook.com/share/{share}/aBcD1234/") is None

    def describe_when_there_is_nothing_to_recognize():
        @pytest.mark.parametrize("url", ["", None, "   ", "https://vimeo.com/12345", "not a url"], ids=repr)
        def it_returns_none(url):
            assert recognize(url) is None

        def it_returns_none_for_a_non_web_scheme():
            assert recognize("ftp://www.youtube.com/watch?v=dQw4w9WgXcQ") is None

        def it_returns_none_for_a_url_the_parser_refuses():
            assert recognize("https://[oops/watch?v=dQw4w9WgXcQ") is None

        def it_returns_none_when_there_is_no_host():
            assert recognize("https:///watch?v=dQw4w9WgXcQ") is None

    def it_keeps_the_url_the_member_typed():
        link = recognize("  https://youtu.be/dQw4w9WgXcQ  ")
        assert link.url == "https://youtu.be/dQw4w9WgXcQ"


def describe_validate_video_url():
    def it_returns_a_recognized_url_stripped():
        assert validate_video_url("  https://youtu.be/dQw4w9WgXcQ ") == "https://youtu.be/dQw4w9WgXcQ"

    def it_lets_a_blank_value_through():
        assert validate_video_url("") == ""
        assert validate_video_url(None) == ""

    def it_refuses_an_unsupported_provider():
        with pytest.raises(ValidationError) as refusal:
            validate_video_url("https://vimeo.com/12345")
        assert refusal.value.messages == [unsupported_video_message()]

    def it_accepts_instagram_and_facebook():
        assert validate_video_url("https://www.instagram.com/reel/CxYzAbCdEfG/")
        assert validate_video_url("https://www.facebook.com/watch/?v=1234567890")


def _fourth_provider() -> VideoProvider:
    """A provider that exists nowhere in the code, to prove the registry is the source."""
    return VideoProvider(
        key="vimeo",
        name="Vimeo",
        example="https://vimeo.com/\u2026",
        rules=(UrlRule(frozenset({"vimeo.com", "www.vimeo.com"}), re.compile(r"/(?P<id>[0-9]+)/?")),),
    )


def describe_unsupported_video_message():
    def it_names_every_provider_in_the_registry():
        message = unsupported_video_message()
        for provider in PROVIDERS:
            assert provider.name in message, provider.name
            assert provider.example in message, provider.example

    def it_is_composed_from_the_registry_rather_than_written_out(monkeypatch):
        # The assertion above passes for a hardcoded three provider sentence too. A
        # provider added as data has to reach the message with no string edited.
        fourth = _fourth_provider()
        monkeypatch.setattr(video_providers, "PROVIDERS", (*PROVIDERS, fourth))
        assert unsupported_video_message() == (
            "Enter a YouTube, Instagram, Facebook, or Vimeo video link. For example "
            "https://www.youtube.com/watch?v=\u2026, https://www.instagram.com/reel/\u2026, "
            "https://www.facebook.com/watch/?v=\u2026, or https://vimeo.com/\u2026"
        )

    def it_reaches_recognition_and_validation_too(monkeypatch):
        # The other half of "a fourth provider is a data change": no call site edited.
        fourth = _fourth_provider()
        monkeypatch.setattr(video_providers, "PROVIDERS", (*PROVIDERS, fourth))
        link = recognize("https://vimeo.com/12345")
        assert link is not None and link.provider is fourth
        assert not link.is_embed
        assert validate_video_url("https://vimeo.com/12345") == "https://vimeo.com/12345"

    def it_carries_no_dash():
        assert "—" not in unsupported_video_message()
        assert " - " not in unsupported_video_message()


def describe_joined():
    # The message reads from the registry, so it has to stay a sentence at any length.
    def it_reads_a_single_name_plain():
        assert _joined(["YouTube"]) == "YouTube"

    def it_reads_two_names_with_or():
        assert _joined(["YouTube", "Instagram"]) == "YouTube or Instagram"

    def it_reads_three_names_as_a_list():
        assert _joined(["YouTube", "Instagram", "Facebook"]) == "YouTube, Instagram, or Facebook"


def describe_the_python_versus_browser_split():
    # Python ends a URL's authority at / ? #; WHATWG also ends it at a backslash. Every
    # URL below parses here (before the guard) as a provider and navigates, in a browser,
    # to evil.test. The card would then carry a provider's name over somebody else's page.
    HOSTILE = [
        r"https://evil.test\@www.youtube.com/watch?v=dQw4w9WgXcQ",
        r"https://evil.test\@www.instagram.com/reel/CxYzAbCdEfG/",
        r"https://evil.test\@www.facebook.com/watch/?v=1234567890",
        r"https://evil.test\@fb.watch/aBcD1234ef/",
    ]

    @pytest.mark.parametrize("url", HOSTILE, ids=lambda url: url)
    def it_refuses_a_backslash_before_the_at_sign(url):
        assert recognize(url) is None

    @pytest.mark.parametrize("url", HOSTILE, ids=lambda url: url)
    def it_is_not_a_vacuous_guard(url):
        # Without the guard these would be recognised: this is what urlsplit reads.
        from urllib.parse import urlsplit

        assert urlsplit(url).hostname in {
            "www.youtube.com",
            "www.instagram.com",
            "www.facebook.com",
            "fb.watch",
        }

    def it_refuses_a_backslash_anywhere_in_the_url():
        assert recognize(r"https://www.instagram.com/reel/CxYzAbC\EfG/") is None


def describe_destination():
    def it_shows_the_host_and_path_a_reader_can_check():
        link = recognize("https://www.instagram.com/reel/CxYzAbCdEfG/")
        assert link.destination == "www.instagram.com/reel/CxYzAbCdEfG"

    def it_drops_the_query_string():
        link = recognize("https://www.facebook.com/watch/?ref=share&v=1234567890")
        assert link.destination == "www.facebook.com/watch"

    def it_truncates_a_long_path():
        link = recognize("https://www.instagram.com/" + "a" * 80 + "/reel/CxYzAbCdEfG/")
        assert len(link.destination) == 48
        assert link.destination.endswith("\u2026")
        assert link.destination.startswith("www.instagram.com/aaa")


def describe_validate_youtube_url():
    def it_returns_a_youtube_url_stripped():
        assert validate_youtube_url("  https://youtu.be/dQw4w9WgXcQ ") == "https://youtu.be/dQw4w9WgXcQ"

    def it_lets_a_blank_value_through():
        assert validate_youtube_url("") == ""
        assert validate_youtube_url(None) == ""

    def it_takes_a_music_youtube_link():
        # The pre-registry regex accepted this shape; host strictness must not lose it.
        assert validate_youtube_url("https://music.youtube.com/watch?v=dQw4w9WgXcQ")

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.instagram.com/reel/CxYzAbCdEfG/",
            "https://www.facebook.com/watch/?v=1234567890",
            "https://vimeo.com/12345",
            "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
        ],
        ids=lambda url: url,
    )
    def it_refuses_anything_else_including_another_provider(url):
        with pytest.raises(ValidationError) as refusal:
            validate_youtube_url(url)
        assert refusal.value.messages == [
            f"Only YouTube videos play here. Enter a YouTube link, for example {YOUTUBE.example}"
        ]
