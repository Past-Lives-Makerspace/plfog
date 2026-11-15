"""A new row removed or left half filled on the Help editor's four row lists (#775).

"+ Add" clones the empty form and bumps TOTAL_FORMS. Remove takes the clone off the page, but
TOTAL_FORMS still counts it, so the save posts no keys at all for that index. A clone still on
the page posts what its ``<template>`` holds (``RowList.blank``) with what was typed over it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse

from membership.models import HelpCategory, Member, OrgFAQItem, OrgLink, WikiArticle
from tests.membership.factories import (
    HelpCategoryFactory,
    MemberFactory,
    OrgFAQItemFactory,
    OrgLinkFactory,
    WikiArticleFactory,
)
from tests.tours.conftest import signed_in

pytestmark = pytest.mark.django_db

REMOVE = "onclick=\"this.closest('.hub-card').remove();\""
EMPTY_EDITOR = "<p><br></p>"
"""What a blank rich text field posts: static/js/rich-editor-init.js writes Quill's HTML into its textarea."""


@dataclass(frozen=True, eq=False)
class RowList:
    """One row list on the editor: its formset prefix, where it saves, where its rows render, and its blank row."""

    prefix: str
    save_url_name: str
    context_name: str
    rows_id: str
    template_id: str
    tab: str
    saved_message: str
    blank: dict[str, str]

    def new(self, **typed: Any) -> dict[str, Any]:
        """A row "+ Add" cloned, as the page posts it: the clone's blanks with what was typed over them."""
        return {**self.blank, **typed}

    def post(self, *rows: dict[str, Any] | None, initial: int) -> dict[str, Any]:
        """The management form, then each row under ``<prefix>-<index>-``.

        ``None`` is a row Remove took off the page: TOTAL_FORMS still counts it, but none of its keys are posted.
        """
        payload: dict[str, Any] = {
            f"{self.prefix}-TOTAL_FORMS": str(len(rows)),
            f"{self.prefix}-INITIAL_FORMS": str(initial),
            f"{self.prefix}-MIN_NUM_FORMS": "0",
            f"{self.prefix}-MAX_NUM_FORMS": "1000",
        }
        for index, row in enumerate(rows):
            if row is not None:
                payload.update({f"{self.prefix}-{index}-{name}": value for name, value in row.items()})
        return payload

    def save(self, client: Client, payload: dict[str, Any]) -> HttpResponse:
        return client.post(reverse(self.save_url_name), payload)

    def field_errors(self, response: HttpResponse) -> list[set[str]]:
        """The names of the fields each bound row errored on, row by row."""
        return [set(errors) for errors in response.context[self.context_name].errors]

    def rows_html(self, response: HttpResponse) -> str:
        """The rendered rows, without the hidden ``<template>`` the "+ Add" button clones."""
        content = response.content.decode()
        return content.split(f'id="{self.rows_id}"')[1].split(f'<template id="{self.template_id}">')[0]

    def rendered_errors(self, response: HttpResponse) -> set[str]:
        """Each ``<index>-<field>`` whose error list renders under its input."""
        pattern = rf'class="pl-field-errors" id="id_{re.escape(self.prefix)}-(\d+-\w+)_error"'
        return set(re.findall(pattern, self.rows_html(response)))


LINKS = RowList(
    "links",
    "hub_org_info_links_save",
    "link_formset",
    "link-rows",
    "link-empty-template",
    "faq",
    "Links saved.",
    {"id": "", "label": "", "url": "", "sort_order": "0"},
)
FAQ = RowList(
    "faq",
    "hub_org_info_faq_save",
    "faq_formset",
    "faq-rows",
    "faq-empty-template",
    "faq",
    "FAQ saved.",
    {"id": "", "question": "", "answer": EMPTY_EDITOR, "video_url": "", "document_url": "", "sort_order": "0"},
)
CATEGORIES = RowList(
    "categories",
    "hub_help_categories_save",
    "category_formset",
    "category-rows",
    "category-empty-template",
    "categories",
    "Categories saved.",
    {"id": "", "name": "", "slug": "", "audience": "member", "description": "", "sort_order": "0"},
)
ARTICLES = RowList(
    "articles",
    "hub_help_articles_save",
    "article_formset",
    "article-rows",
    "article-empty-template",
    "articles",
    "Help guides saved.",
    {
        "id": "",
        "title": "",
        "slug": "",
        "category": "",
        "body": EMPTY_EDITOR,
        "is_published": "on",
        "sort_order": "0",
    },
)


class _ClonePost(HTMLParser):
    """What an untouched clone posts, read from its ``<template>``: each named control's starting value.

    A textarea is a rich text field, so it posts ``EMPTY_EDITOR``. A file input, an unchecked box
    and a multi-select with nothing picked post nothing; a select posts its selected option, else its first.
    """

    def __init__(self, prefix: str) -> None:
        super().__init__()
        self.marker = f"{prefix}-__prefix__-"
        self.posted: dict[str, str] = {}
        self.select: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "option" and self.select is not None:
            if self.select not in self.posted or "selected" in attr:
                self.posted[self.select] = attr.get("value") or ""
            return
        name = attr.get("name") or ""
        if not name.startswith(self.marker):
            return
        field_name = name.removeprefix(self.marker)
        if tag == "textarea":
            self.posted[field_name] = EMPTY_EDITOR
        elif tag == "select":
            self.select = None if "multiple" in attr else field_name
        elif attr.get("type") == "checkbox":
            if "checked" in attr:
                self.posted[field_name] = "on"
        elif attr.get("type") != "file":
            self.posted[field_name] = attr.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self.select = None


@pytest.fixture
def admin() -> Client:
    return signed_in(MemberFactory(fog_role=Member.FogRole.ADMIN))


def _messages(response: HttpResponse) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def _saved_to(response: HttpResponse, rows: RowList) -> bool:
    """The save went through: back to the row list's tab, with its saved message."""
    return (
        response.status_code == 302
        and response["Location"] == f"{reverse('hub_help_edit')}?tab={rows.tab}"
        and _messages(response) == [rows.saved_message]
    )


def _total_forms(response: HttpResponse, rows: RowList) -> int:
    """The TOTAL_FORMS the error page sends with the next Save: every row the post counted, drawn or not."""
    match = re.search(rf'name="{re.escape(rows.prefix)}-TOTAL_FORMS" value="(\d+)"', response.content.decode())
    assert match is not None
    return int(match.group(1))


def _link(link: OrgLink, **edits: str) -> dict[str, Any]:
    return {"id": str(link.pk), "label": link.label, "url": link.url, "sort_order": "0", **edits}


def _faq(item: OrgFAQItem, **edits: str) -> dict[str, Any]:
    return {
        "id": str(item.pk),
        "question": item.question,
        "answer": item.answer,
        "video_url": "",
        "document_url": "",
        "sort_order": "0",
        **edits,
    }


def _category(category: HelpCategory, **edits: str) -> dict[str, Any]:
    return {
        "id": str(category.pk),
        "name": category.name,
        "slug": category.slug,
        "audience": category.audience,
        "description": category.description,
        "sort_order": "0",
        **edits,
    }


def _article(article: WikiArticle, **edits: str) -> dict[str, Any]:
    return {
        "id": str(article.pk),
        "title": article.title,
        "slug": article.slug,
        "body": article.body,
        "is_published": "on",
        "sort_order": "0",
        **edits,
    }


def describe_the_blank_rows():
    @pytest.mark.parametrize("rows", [LINKS, FAQ, CATEGORIES, ARTICLES], ids=lambda rows: rows.prefix)
    def it_posts_what_its_clone_template_holds(admin: Client, rows: RowList):
        content = admin.get(reverse("hub_help_edit")).content.decode()
        clone = _ClonePost(rows.prefix)
        clone.feed(content.split(f'<template id="{rows.template_id}">')[1].split("</template>")[0])
        assert clone.posted == rows.blank


def describe_resource_links():
    def describe_a_new_row_removed_before_save():
        def it_saves_the_rows_left_on_the_page(admin: Client):
            link = OrgLinkFactory(label="Handbook")
            before = OrgLink.objects.count()
            response = LINKS.save(admin, LINKS.post(_link(link, label="Member Handbook"), None, initial=1))
            assert _saved_to(response, LINKS)
            assert OrgLink.objects.count() == before
            assert OrgLink.objects.get(pk=link.pk).label == "Member Handbook"

        def it_saves_when_it_was_the_only_row(admin: Client):
            before = OrgLink.objects.count()
            response = LINKS.save(admin, LINKS.post(None, initial=0))
            assert _saved_to(response, LINKS)
            assert OrgLink.objects.count() == before

    def describe_a_removed_row_beside_one_with_an_error():
        def it_leaves_the_removed_row_off_the_page(admin: Client):
            response = LINKS.save(admin, LINKS.post(None, LINKS.new(label="Handbook"), initial=0))
            assert response.status_code == 200
            rows = LINKS.rows_html(response)
            assert 'name="links-1-label"' in rows
            assert 'name="links-0-' not in rows
            assert _total_forms(response, LINKS) == 2

        def it_saves_the_next_post_once_the_other_row_is_complete(admin: Client):
            payload = LINKS.post(None, LINKS.new(label="Handbook", url="https://example.com/h"), initial=0)
            assert _saved_to(LINKS.save(admin, payload), LINKS)
            assert OrgLink.objects.filter(label="Handbook").count() == 1

    def describe_a_new_row_left_blank():
        def it_saves_the_rest_and_creates_nothing(admin: Client):
            link = OrgLinkFactory(label="Handbook")
            before = OrgLink.objects.count()
            response = LINKS.save(admin, LINKS.post(_link(link, label="Member Handbook"), LINKS.new(), initial=1))
            assert _saved_to(response, LINKS)
            assert OrgLink.objects.count() == before
            assert OrgLink.objects.get(pk=link.pk).label == "Member Handbook"

    def describe_a_new_row_half_filled():
        def it_errors_under_the_url_when_only_a_label_was_typed(admin: Client):
            before = OrgLink.objects.count()
            response = LINKS.save(admin, LINKS.post(LINKS.new(label="Handbook"), initial=0))
            assert response.status_code == 200
            assert LINKS.field_errors(response) == [{"url"}]
            assert LINKS.rendered_errors(response) == {"0-url"}
            assert OrgLink.objects.count() == before

        def it_errors_under_the_label_when_only_a_url_was_typed(admin: Client):
            response = LINKS.save(admin, LINKS.post(LINKS.new(url="https://example.com/h"), initial=0))
            assert response.status_code == 200
            assert LINKS.field_errors(response) == [{"label"}]
            assert LINKS.rendered_errors(response) == {"0-label"}

    def describe_a_saved_row_blanked_out():
        def it_errors_under_both_fields_and_keeps_the_row(admin: Client):
            link = OrgLinkFactory(label="Handbook")
            response = LINKS.save(admin, LINKS.post(_link(link, label="", url=""), initial=1))
            assert response.status_code == 200
            assert LINKS.field_errors(response) == [{"label", "url"}]
            assert OrgLink.objects.get(pk=link.pk).label == "Handbook"

    def describe_a_new_row_back_with_an_error():
        def it_offers_remove_on_the_new_row_and_keeps_delete_on_the_saved_one(admin: Client):
            link = OrgLinkFactory()
            response = LINKS.save(admin, LINKS.post(_link(link), LINKS.new(label="Handbook"), initial=1))
            saved_row, new_row = LINKS.rows_html(response).split('name="links-1-label"')
            assert REMOVE in new_row
            assert REMOVE not in saved_row
            assert "getElementById('id_links-0-DELETE')" in saved_row
            assert 'id="id_links-1-DELETE"' not in new_row


def describe_faq():
    def describe_a_new_row_removed_before_save():
        def it_saves_the_rows_left_on_the_page(admin: Client):
            item = OrgFAQItemFactory(question="Where do I park?")
            before = OrgFAQItem.objects.count()
            response = FAQ.save(admin, FAQ.post(_faq(item, question="Where can I park?"), None, initial=1))
            assert _saved_to(response, FAQ)
            assert OrgFAQItem.objects.count() == before
            assert OrgFAQItem.objects.get(pk=item.pk).question == "Where can I park?"

        def it_saves_when_it_was_the_only_row(admin: Client):
            before = OrgFAQItem.objects.count()
            response = FAQ.save(admin, FAQ.post(None, initial=0))
            assert _saved_to(response, FAQ)
            assert OrgFAQItem.objects.count() == before

    def describe_a_removed_row_beside_one_with_an_error():
        def it_leaves_the_removed_row_off_the_page(admin: Client):
            response = FAQ.save(admin, FAQ.post(None, FAQ.new(question="Where do I park?"), initial=0))
            assert response.status_code == 200
            rows = FAQ.rows_html(response)
            assert 'name="faq-1-question"' in rows
            assert 'name="faq-0-' not in rows
            assert _total_forms(response, FAQ) == 2

        def it_saves_the_next_post_once_the_other_row_is_complete(admin: Client):
            payload = FAQ.post(None, FAQ.new(question="Where do I park?", answer="<p>Out back.</p>"), initial=0)
            assert _saved_to(FAQ.save(admin, payload), FAQ)
            assert OrgFAQItem.objects.filter(question="Where do I park?").count() == 1

    def describe_a_new_row_half_filled():
        def it_errors_under_the_answer_when_only_a_question_was_typed(admin: Client):
            before = OrgFAQItem.objects.count()
            response = FAQ.save(admin, FAQ.post(FAQ.new(question="Where do I park?"), initial=0))
            assert response.status_code == 200
            assert FAQ.field_errors(response) == [{"answer"}]
            assert FAQ.rendered_errors(response) == {"0-answer"}
            assert OrgFAQItem.objects.count() == before

        def it_errors_under_the_question_when_only_an_answer_was_typed(admin: Client):
            response = FAQ.save(admin, FAQ.post(FAQ.new(answer="<p>Out back.</p>"), initial=0))
            assert response.status_code == 200
            assert FAQ.field_errors(response) == [{"question"}]
            assert FAQ.rendered_errors(response) == {"0-question"}

        def it_errors_when_only_a_video_link_was_typed(admin: Client):
            payload = FAQ.post(FAQ.new(video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"), initial=0)
            response = FAQ.save(admin, payload)
            assert response.status_code == 200
            assert FAQ.field_errors(response) == [{"question", "answer"}]

        def it_errors_when_only_a_document_was_uploaded(admin: Client):
            payload = FAQ.post(FAQ.new(document=SimpleUploadedFile("guide.pdf", b"%PDF-1.4")), initial=0)
            response = FAQ.save(admin, payload)
            assert response.status_code == 200
            assert FAQ.field_errors(response) == [{"question", "answer"}]

    def describe_a_saved_row_blanked_out():
        def it_errors_under_the_question_and_the_answer(admin: Client):
            item = OrgFAQItemFactory(question="Where do I park?")
            response = FAQ.save(admin, FAQ.post(_faq(item, question="", answer=EMPTY_EDITOR), initial=1))
            assert response.status_code == 200
            assert FAQ.field_errors(response) == [{"question", "answer"}]
            assert OrgFAQItem.objects.get(pk=item.pk).question == "Where do I park?"

    def describe_a_new_row_back_with_an_error():
        def it_offers_remove_on_the_new_row_and_keeps_delete_on_the_saved_one(admin: Client):
            item = OrgFAQItemFactory()
            response = FAQ.save(admin, FAQ.post(_faq(item), FAQ.new(question="Where do I park?"), initial=1))
            saved_row, new_row = FAQ.rows_html(response).split('name="faq-1-question"')
            assert REMOVE in new_row
            assert REMOVE not in saved_row
            assert "getElementById('id_faq-0-DELETE')" in saved_row
            assert 'id="id_faq-1-DELETE"' not in new_row


def describe_categories():
    def describe_a_new_row_removed_before_save():
        def it_saves_the_rows_left_on_the_page(admin: Client):
            category = HelpCategoryFactory(name="Getting started")
            before = HelpCategory.objects.count()
            payload = CATEGORIES.post(_category(category, name="Getting Started"), None, initial=1)
            response = CATEGORIES.save(admin, payload)
            assert _saved_to(response, CATEGORIES)
            assert HelpCategory.objects.count() == before
            assert HelpCategory.objects.get(pk=category.pk).name == "Getting Started"

        def it_saves_when_it_was_the_only_row(admin: Client):
            before = HelpCategory.objects.count()
            response = CATEGORIES.save(admin, CATEGORIES.post(None, initial=0))
            assert _saved_to(response, CATEGORIES)
            assert HelpCategory.objects.count() == before

    def describe_a_removed_row_beside_one_with_an_error():
        def it_leaves_the_removed_row_off_the_page(admin: Client):
            response = CATEGORIES.save(admin, CATEGORIES.post(None, CATEGORIES.new(slug="kiln-room"), initial=0))
            assert response.status_code == 200
            rows = CATEGORIES.rows_html(response)
            assert 'name="categories-1-name"' in rows
            assert 'name="categories-0-' not in rows
            assert _total_forms(response, CATEGORIES) == 2

        def it_saves_the_next_post_once_the_other_row_is_complete(admin: Client):
            payload = CATEGORIES.post(None, CATEGORIES.new(name="Kiln room", slug="kiln-room"), initial=0)
            assert _saved_to(CATEGORIES.save(admin, payload), CATEGORIES)
            assert HelpCategory.objects.filter(slug="kiln-room").count() == 1

    def describe_a_new_row_left_blank():
        def it_saves_the_rest_and_creates_nothing(admin: Client):
            category = HelpCategoryFactory(name="Getting started")
            before = HelpCategory.objects.count()
            payload = CATEGORIES.post(_category(category, name="Getting Started"), CATEGORIES.new(), initial=1)
            response = CATEGORIES.save(admin, payload)
            assert _saved_to(response, CATEGORIES)
            assert HelpCategory.objects.count() == before
            assert HelpCategory.objects.get(pk=category.pk).name == "Getting Started"

    def describe_a_new_row_half_filled():
        def it_errors_under_the_name_when_only_a_slug_was_typed(admin: Client):
            before = HelpCategory.objects.count()
            response = CATEGORIES.save(admin, CATEGORIES.post(CATEGORIES.new(slug="kiln-room"), initial=0))
            assert response.status_code == 200
            assert CATEGORIES.field_errors(response) == [{"name"}]
            assert CATEGORIES.rendered_errors(response) == {"0-name"}
            assert HelpCategory.objects.count() == before

        def it_errors_under_the_name_when_only_a_description_was_typed(admin: Client):
            payload = CATEGORIES.post(CATEGORIES.new(description="Firing schedules."), initial=0)
            response = CATEGORIES.save(admin, payload)
            assert response.status_code == 200
            assert CATEGORIES.field_errors(response) == [{"name"}]

    def describe_a_saved_row_blanked_out():
        def it_errors_under_the_name(admin: Client):
            category = HelpCategoryFactory(name="Getting started")
            payload = CATEGORIES.post(_category(category, name="", slug=""), initial=1)
            response = CATEGORIES.save(admin, payload)
            assert response.status_code == 200
            assert CATEGORIES.field_errors(response) == [{"name"}]
            assert HelpCategory.objects.get(pk=category.pk).name == "Getting started"

    def describe_a_new_row_back_with_an_error():
        def it_offers_remove_on_the_new_row_and_keeps_delete_on_the_saved_one(admin: Client):
            category = HelpCategoryFactory()
            payload = CATEGORIES.post(_category(category), CATEGORIES.new(slug="kiln-room"), initial=1)
            response = CATEGORIES.save(admin, payload)
            saved_row, new_row = CATEGORIES.rows_html(response).split('name="categories-1-name"')
            assert REMOVE in new_row
            assert REMOVE not in saved_row
            assert "getElementById('id_categories-0-DELETE')" in saved_row
            assert 'id="id_categories-1-DELETE"' not in new_row


def describe_articles():
    def describe_a_new_row_removed_before_save():
        def it_saves_the_rows_left_on_the_page(admin: Client):
            article = WikiArticleFactory(title="Guild voting")
            before = WikiArticle.objects.count()
            payload = ARTICLES.post(_article(article, title="Guild Voting"), None, initial=1)
            response = ARTICLES.save(admin, payload)
            assert _saved_to(response, ARTICLES)
            assert WikiArticle.objects.count() == before
            assert WikiArticle.objects.get(pk=article.pk).title == "Guild Voting"

        def it_saves_when_it_was_the_only_row(admin: Client):
            before = WikiArticle.objects.count()
            response = ARTICLES.save(admin, ARTICLES.post(None, initial=0))
            assert _saved_to(response, ARTICLES)
            assert WikiArticle.objects.count() == before

    def describe_a_removed_row_beside_one_with_an_error():
        def it_leaves_the_removed_row_off_the_page(admin: Client):
            response = ARTICLES.save(admin, ARTICLES.post(None, ARTICLES.new(title="Kiln loading"), initial=0))
            assert response.status_code == 200
            rows = ARTICLES.rows_html(response)
            assert 'name="articles-1-title"' in rows
            assert 'name="articles-0-' not in rows
            assert _total_forms(response, ARTICLES) == 2

        def it_saves_the_next_post_once_the_other_row_is_complete(admin: Client):
            completed = ARTICLES.new(title="Kiln loading", body="<p>Load from the back.</p>")
            assert _saved_to(ARTICLES.save(admin, ARTICLES.post(None, completed, initial=0)), ARTICLES)
            assert WikiArticle.objects.filter(title="Kiln loading").count() == 1

    def describe_a_new_row_half_filled():
        def it_errors_under_the_body_when_only_a_title_was_typed(admin: Client):
            before = WikiArticle.objects.count()
            response = ARTICLES.save(admin, ARTICLES.post(ARTICLES.new(title="Kiln loading"), initial=0))
            assert response.status_code == 200
            assert ARTICLES.field_errors(response) == [{"body"}]
            assert ARTICLES.rendered_errors(response) == {"0-body"}
            assert WikiArticle.objects.count() == before

        def it_errors_under_the_title_when_only_a_body_was_typed(admin: Client):
            payload = ARTICLES.post(ARTICLES.new(body="<p>Load from the back.</p>"), initial=0)
            response = ARTICLES.save(admin, payload)
            assert response.status_code == 200
            assert ARTICLES.field_errors(response) == [{"title"}]
            assert ARTICLES.rendered_errors(response) == {"0-title"}

    def describe_a_saved_row_blanked_out():
        def it_errors_under_the_title_and_the_body(admin: Client):
            article = WikiArticleFactory(title="Guild voting")
            payload = ARTICLES.post(_article(article, title="", body=EMPTY_EDITOR), initial=1)
            response = ARTICLES.save(admin, payload)
            assert response.status_code == 200
            assert ARTICLES.field_errors(response) == [{"title", "body"}]
            assert WikiArticle.objects.get(pk=article.pk).title == "Guild voting"

    def describe_a_new_row_back_with_an_error():
        def it_offers_remove_on_the_new_row_and_keeps_delete_on_the_saved_one(admin: Client):
            article = WikiArticleFactory()
            payload = ARTICLES.post(_article(article), ARTICLES.new(title="Kiln loading"), initial=1)
            response = ARTICLES.save(admin, payload)
            saved_row, new_row = ARTICLES.rows_html(response).split('name="articles-1-title"')
            assert REMOVE in new_row
            assert REMOVE not in saved_row
            assert "getElementById('id_articles-0-DELETE')" in saved_row
            assert 'id="id_articles-1-DELETE"' not in new_row
