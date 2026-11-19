"""The head tag rules search engines read (#1259): core.seo and the ``seo`` template tags."""

from __future__ import annotations

import json

import pytest
from django.template import Context, Template
from django.test import RequestFactory

from core import seo
from core.templatetags import seo as seo_tags


def describe_page_title():
    def it_adds_the_brand_once_when_it_fits():
        assert seo.page_title("Book a Tool", "Past Lives") == "Book a Tool - Past Lives"

    def it_leaves_out_the_brand_a_title_already_names():
        assert seo.page_title("Classes — Past Lives Makerspace", "Past Lives") == "Classes — Past Lives Makerspace"

    def it_leaves_out_the_brand_when_it_would_run_past_sixty_characters():
        title = "Make a Cup w/ Anjali — Jan 20, 2027 and a Long Glaze Day"
        assert seo.page_title(title, "Past Lives") == title

    def it_cuts_a_long_title_at_a_word():
        result = seo.page_title("Register — " + "Very Long Class Name " * 5, "Past Lives")
        assert len(result) <= 60
        assert result.endswith("…")
        assert "Past Lives" not in result

    def it_collapses_whitespace_from_the_template():
        assert seo.page_title("\n  Help\n  ", "Past Lives") == "Help - Past Lives"


def describe_truncate_at_word():
    def it_keeps_text_that_fits():
        assert seo.truncate_at_word("short", 10) == "short"

    def it_hard_cuts_one_long_word():
        assert seo.truncate_at_word("a" * 20, 10) == "a" * 9 + "…"


def describe_the_page_title_filter():
    def it_counts_real_characters_and_escapes_again():
        rendered = Template("{% load seo %}{% filter page_title:brand %}Tom &amp; Jerry{% endfilter %}").render(
            Context({"brand": "Past Lives"})
        )
        assert rendered == "Tom &amp; Jerry - Past Lives"


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()


def _canonical(request, template: str = "{% load seo %}{% canonical_url %}") -> str:
    return Template(template).render(Context({"request": request}))


def describe_canonical_url():
    def it_is_the_absolute_path_on_the_requests_host(rf, settings):
        settings.ALLOWED_HOSTS = ["classes.example"]
        request = rf.get("/classes/", secure=True, HTTP_HOST="classes.example")
        assert _canonical(request) == "https://classes.example/classes/"

    def it_drops_the_query_string(rf):
        assert _canonical(rf.get("/classes/", {"guild": "wood", "page": "1"})) == "http://testserver/classes/"

    def it_keeps_a_page_past_the_first(rf):
        assert _canonical(rf.get("/classes/", {"page": "3", "within": "7"})) == "http://testserver/classes/?page=3"

    def it_ignores_a_page_that_is_not_a_number(rf):
        assert _canonical(rf.get("/classes/", {"page": "last"})) == "http://testserver/classes/"

    def it_names_another_path_without_any_query(rf):
        request = rf.get("/tour/", {"date": "2026-12-01", "page": "2"})
        assert _canonical(request, '{% load seo %}{% canonical_url "/" %}') == "http://testserver/"


def describe_json_ld():
    def it_escapes_what_could_close_the_script_element():
        out = seo_tags.json_ld({"name": "</script><b>&"})
        assert "</script>" not in out
        assert json.loads(out) == {"name": "</script><b>&"}


def describe_absolute_url():
    def it_keeps_a_storage_url(rf):
        assert seo_tags.absolute_url("https://cdn.example/a.png", rf.get("/")) == "https://cdn.example/a.png"

    def it_puts_a_path_on_the_requests_host(rf):
        assert seo_tags.absolute_url("/media/a.png", rf.get("/")) == "http://testserver/media/a.png"
