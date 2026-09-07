"""Acceptance spec for PLAT-1: the brand() context processor's configured values render
across the four de-branded templates (hub/base.html, classes/base_public.html,
guilds/base_public.html, core/privacy_policy.html), with the logo fallback proven too.

Assertions that check for the ABSENCE of "Past Lives" strings are scoped to the HTML each
template actually controls, not the full response body: ``hub/home.html``'s own dashboard
copy ("Welcome to Past Lives Member Portal") and the changelog modal (39 historical
CHANGELOG entries mentioning Past Lives) are both outside the four templates this story
touches, so the hub check is scoped to everything before ``<main class="hub-content">``.
Likewise the privacy policy extends the plain (non-hub) ``base.html``, whose own nav/meta
are out of PLAT-1's scope, so that check is scoped to the ``<article class="pl-legal">``
block privacy_policy.html itself renders.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from core.models import SiteConfiguration
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

GUILDS_HOST = "guilds.pastlives.app"
GUILDS_SETTINGS = dict(
    ALLOWED_HOSTS=["guilds.pastlives.app", "testserver"],
    GUILDS_HOSTS=["guilds.pastlives.app"],
    GUILDS_BASE_URL="https://guilds.pastlives.app",
    BOOK_BASE_URL="https://book.pastlives.space",
    MEMBER_BASE_URL="https://members.pastlives.app",
)


def _member_client() -> Client:
    MembershipPlanFactory()
    user = User.objects.create_user(username="brandmember", password="pass")
    client = Client()
    client.login(username=user.username, password="pass")
    return client


def _set_brand(**overrides: object) -> SiteConfiguration:
    config = SiteConfiguration.load()
    for field, value in overrides.items():
        setattr(config, field, value)
    config.save()
    return config


def _hub_chrome(body: str) -> str:
    """Everything hub/base.html renders before the dashboard's own <main> content —
    i.e. the region this story's template edits actually touch."""
    return body.split('<main class="hub-content">')[0]


def describe_hub_base():
    @pytest.fixture(autouse=True)
    def _branded():
        _set_brand(
            org_name="Fletcher Test Space",
            org_short_name="Fletcher",
            org_support_email="help@fletcher.test",
            org_website_url="https://fletcher.test",
        )

    def it_renders_the_configured_short_name_in_the_title_and_sidebar():
        body = _member_client().get(reverse("hub_home")).content.decode()
        chrome = _hub_chrome(body)
        assert "Home - Fletcher</title>" in chrome
        assert 'alt="Fletcher"' in chrome

    def it_renders_the_configured_website_on_the_sidebar_globe():
        body = _member_client().get(reverse("hub_home")).content.decode()
        chrome = _hub_chrome(body)
        assert 'href="https://fletcher.test"' in chrome
        assert 'title="fletcher.test"' in chrome
        assert 'aria-label="Fletcher main site"' in chrome

    def it_renders_the_configured_primary_color_as_the_theme_color():
        _set_brand(org_primary_color="#FF00AA")
        body = _member_client().get(reverse("hub_home")).content.decode()
        assert '<meta name="theme-color" content="#FF00AA">' in body

    def it_leaves_no_past_lives_string_in_the_chrome():
        # Not asserting the absence of "pastlives.space" here: the sidebar Wiki link is
        # built from MAKERSPACE_WIKI_URL, a separate setting untouched by PLAT-1's seven
        # brand fields (wiki.pastlives.space legitimately still appears).
        body = _member_client().get(reverse("hub_home")).content.decode()
        chrome = _hub_chrome(body)
        assert "Past Lives" not in chrome


def describe_classes_public_base():
    @pytest.fixture(autouse=True)
    def _branded():
        _set_brand(org_name="Fletcher Test Space", org_short_name="Fletcher", org_website_url="https://fletcher.test")

    def it_renders_the_configured_wordmark_and_nav_links():
        body = Client().get(reverse("classes:public_list")).content.decode()
        assert 'cp-topbar__wordmark">Fletcher<' in body
        assert 'href="https://fletcher.test"' in body
        assert 'href="https://fletcher.test/guilds"' in body
        assert 'href="https://fletcher.test/membership"' in body
        assert 'href="https://fletcher.test/contact"' in body

    def it_renders_the_configured_name_in_the_meta_description():
        body = Client().get(reverse("classes:public_list")).content.decode()
        assert 'content="Fletcher Test Space — Portland' in body


def describe_guilds_public_base():
    @pytest.fixture(autouse=True)
    def _branded():
        _set_brand(org_name="Fletcher Test Space", org_short_name="Fletcher", org_website_url="https://fletcher.test")

    @pytest.fixture(autouse=True)
    def _guilds_settings():
        with override_settings(**GUILDS_SETTINGS):
            yield

    def it_renders_the_configured_wordmark_and_nav_links():
        body = Client().get(reverse("hub_guild_directory"), HTTP_HOST=GUILDS_HOST).content.decode()
        assert 'cp-topbar__wordmark">Fletcher<' in body
        assert 'href="https://fletcher.test"' in body
        assert 'href="https://fletcher.test/membership"' in body

    def it_renders_the_configured_name_in_the_meta_description():
        body = Client().get(reverse("hub_guild_directory"), HTTP_HOST=GUILDS_HOST).content.decode()
        assert "Browse every guild at Fletcher Test Space in Portland, OR" in body


def describe_privacy_policy():
    @pytest.fixture(autouse=True)
    def _branded():
        _set_brand(org_name="Fletcher Test Space", org_support_email="help@fletcher.test")

    def _article(body: str) -> str:
        start = body.index('<article class="pl-legal">')
        return body[start : body.index("</article>", start)]

    def it_renders_the_configured_name_and_support_email():
        body = Client().get(reverse("privacy_policy")).content.decode()
        article = _article(body)
        assert "Fletcher Test Space" in article
        assert "help@fletcher.test" in article

    def it_leaves_no_past_lives_string_on_the_page():
        body = Client().get(reverse("privacy_policy")).content.decode()
        article = _article(body)
        assert "Past Lives" not in article
        assert "pastlives.space" not in article


def describe_logo_fallback():
    def it_uses_the_static_mark_when_no_logo_is_uploaded():
        body = _member_client().get(reverse("hub_home")).content.decode()
        assert "favicon.png" in body

    def it_uses_the_uploaded_logo_when_one_is_set():
        config = _set_brand(org_logo=SimpleUploadedFile("logo.png", _PNG, content_type="image/png"))
        body = _member_client().get(reverse("hub_home")).content.decode()
        assert config.org_logo.url in body
