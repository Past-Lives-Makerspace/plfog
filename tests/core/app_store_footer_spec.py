"""#467: the shared email footer carries the "Get the app" line and the store badges.

Every HTML email includes ``membership/emails/_footer.html`` and every text email
``_footer.txt``, so those two files are the one place the badges live and every email that
carries the footer gets them: the notification shell, the ``_base.html`` transactional
emails, the login code email, receipts and class emails. The URLs come from Site Settings
through the ``site_urls`` tags, because emails render without a request. A blank App Store
URL is the "not launched" state: that badge is left out and the copy says iOS is coming soon.
"""

from __future__ import annotations

import re

import pytest
from django.conf import settings
from django.template.loader import render_to_string

from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db

PLAY = "https://play.google.com/store/apps/details?id=test.fletcher"
IOS = "https://apps.apple.com/app/id1234567890"

BOTH_LINE = "Get the Past Lives app for iPhone or Android."
PLAY_ONLY_LINE = "Get the Past Lives app on Google Play. iOS coming soon."


def _configure(*, play: str, ios: str) -> None:
    config = SiteConfiguration.load()
    config.google_play_url = play
    config.app_store_url = ios
    config.save()


def _store_urls_in(body: str) -> set[str]:
    """Every Play or App Store URL in a rendered body, HTML attribute or bare text alike."""
    found = re.findall(r"https?://[^\s\"'<>]+", body)
    return {url for url in found if "play.google.com" in url or "apps.apple.com" in url}


def describe_html_footer():
    def describe_when_both_stores_are_set():
        def it_links_both_official_badges():
            _configure(play=PLAY, ios=IOS)
            html = render_to_string("membership/emails/_footer.html")
            assert f'href="{PLAY}"' in html
            assert f'href="{IOS}"' in html
            assert 'alt="Get it on Google Play"' in html
            assert 'alt="Download on the App Store"' in html
            # Static storage hashes filenames in production, so match the stem, not the name.
            assert "img/google-play-badge" in html
            assert "img/app-store-badge" in html

        def it_says_iphone_or_android():
            _configure(play=PLAY, ios=IOS)
            html = render_to_string("membership/emails/_footer.html")
            assert BOTH_LINE in html
            assert "coming soon" not in html

    def describe_when_the_app_store_is_blank():
        def it_shows_only_the_play_badge_and_says_ios_is_coming_soon():
            _configure(play=PLAY, ios="")
            html = render_to_string("membership/emails/_footer.html")
            assert f'href="{PLAY}"' in html
            assert 'alt="Get it on Google Play"' in html
            assert "apps.apple.com" not in html
            assert "img/app-store-badge" not in html
            assert PLAY_ONLY_LINE in html

    def describe_when_only_the_app_store_is_set():
        def it_shows_only_the_apple_badge():
            _configure(play="", ios=IOS)
            html = render_to_string("membership/emails/_footer.html")
            assert f'href="{IOS}"' in html
            assert "img/google-play-badge" not in html
            assert "Get the Past Lives app on the App Store." in html
            assert "coming soon" not in html

    def describe_when_neither_store_is_set():
        def it_carries_no_app_line_at_all():
            _configure(play="", ios="")
            html = render_to_string("membership/emails/_footer.html")
            assert "Get the Past Lives app" not in html
            assert "img/google-play-badge" not in html
            assert "img/app-store-badge" not in html

    def it_keeps_the_manage_preferences_link():
        _configure(play=PLAY, ios=IOS)
        html = render_to_string("membership/emails/_footer.html")
        assert "__PL_PREFS_TOKEN__" in html
        assert "Manage your email preferences or unsubscribe" in html


def describe_text_footer():
    def it_lists_each_store_as_a_plain_absolute_url():
        _configure(play=PLAY, ios=IOS)
        text = render_to_string("membership/emails/_footer.txt")
        assert BOTH_LINE in text
        assert f"Get it on Google Play: {PLAY}" in text
        assert f"Download on the App Store: {IOS}" in text

    def it_omits_the_app_store_line_while_ios_is_unlaunched():
        _configure(play=PLAY, ios="")
        text = render_to_string("membership/emails/_footer.txt")
        assert PLAY_ONLY_LINE in text
        assert f"Get it on Google Play: {PLAY}" in text
        assert "App Store" not in text

    def it_lists_only_the_app_store_when_play_is_blank():
        _configure(play="", ios=IOS)
        text = render_to_string("membership/emails/_footer.txt")
        assert "Get the Past Lives app on the App Store." in text
        assert f"Download on the App Store: {IOS}" in text
        assert "Google Play" not in text

    def it_leaves_no_gap_when_neither_store_is_set():
        _configure(play="", ios="")
        text = render_to_string("membership/emails/_footer.txt")
        assert "Get the Past Lives app" not in text
        assert f"Past Lives Makerspace — {settings.MEMBER_BASE_URL}\nManage your email preferences" in text


def describe_txt_and_html_agree():
    def it_lists_the_same_store_urls_when_both_are_set():
        _configure(play=PLAY, ios=IOS)
        html = render_to_string("membership/emails/_footer.html")
        text = render_to_string("membership/emails/_footer.txt")
        assert _store_urls_in(text) == _store_urls_in(html) == {PLAY, IOS}

    def it_lists_the_same_store_urls_when_the_app_store_is_blank():
        _configure(play=PLAY, ios="")
        html = render_to_string("membership/emails/_footer.html")
        text = render_to_string("membership/emails/_footer.txt")
        assert _store_urls_in(text) == _store_urls_in(html) == {PLAY}


def describe_every_email_that_carries_the_footer():
    def it_reaches_the_transactional_shell():
        _configure(play=PLAY, ios="")
        html = render_to_string("membership/emails/_base.html")
        assert 'alt="Get it on Google Play"' in html
        assert f'href="{PLAY}"' in html

    def it_reaches_the_login_code_email_in_both_parts():
        _configure(play=PLAY, ios="")
        html = render_to_string("account/email/login_code_message.html", {"code": "123456"})
        text = render_to_string("account/email/login_code_message.txt", {"code": "123456"})
        assert 'alt="Get it on Google Play"' in html
        assert f"Get it on Google Play: {PLAY}" in text
