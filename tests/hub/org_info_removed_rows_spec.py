"""A new row removed or left half filled on the Help editor's four row lists (#775).

"+ Add" clones the empty form and bumps TOTAL_FORMS. Remove takes the clone off the page, but
TOTAL_FORMS still counts it, so the save posts no keys at all for that index.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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


@dataclass(frozen=True)
class RowList:
    """One row list on the editor: its formset prefix, where it saves and where its rows render."""

    prefix: str
    save_url_name: str
    context_name: str
    rows_id: str
    template_id: str
    tab: str
    saved_message: str

    def post(self, *rows: dict[str, Any], initial: int, total: int | None = None) -> dict[str, Any]:
        """The management form and each row under ``<prefix>-<index>-``; ``total`` also counts removed clones."""
        payload: dict[str, Any] = {
            f"{self.prefix}-TOTAL_FORMS": str(len(rows) if total is None else total),
            f"{self.prefix}-INITIAL_FORMS": str(initial),
            f"{self.prefix}-MIN_NUM_FORMS": "0",
            f"{self.prefix}-MAX_NUM_FORMS": "1000",
        }
        for index, row in enumerate(rows):
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
    "links", "hub_org_info_links_save", "link_formset", "link-rows", "link-empty-template", "faq", "Links saved."
)
FAQ = RowList("faq", "hub_org_info_faq_save", "faq_formset", "faq-rows", "faq-empty-template", "faq", "FAQ saved.")
CATEGORIES = RowList(
    "categories",
    "hub_help_categories_save",
    "category_formset",
    "category-rows",
    "category-empty-template",
    "categories",
    "Categories saved.",
)
ARTICLES = RowList(
    "articles",
    "hub_help_articles_save",
    "article_formset",
    "article-rows",
    "article-empty-template",
    "articles",
    "Help guides saved.",
)


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


def _new(**values: Any) -> dict[str, Any]:
    """A row "+ Add" cloned: a blank ``id`` and the empty form's ``sort_order``, plus what was typed."""
    return {"id": "", "sort_order": "0", **values}


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


def describe_resource_links():
    def describe_a_new_row_removed_before_save():
        def it_saves_the_rows_left_on_the_page(admin: Client):
            link = OrgLinkFactory(label="Handbook")
            before = OrgLink.objects.count()
            response = LINKS.save(admin, LINKS.post(_link(link, label="Member Handbook"), initial=1, total=2))
            assert _saved_to(response, LINKS)
            assert OrgLink.objects.count() == before
            assert OrgLink.objects.get(pk=link.pk).label == "Member Handbook"

        def it_saves_when_it_was_the_only_row(admin: Client):
            before = OrgLink.objects.count()
            response = LINKS.save(admin, LINKS.post(initial=0, total=1))
            assert _saved_to(response, LINKS)
            assert OrgLink.objects.count() == before

    def describe_a_new_row_half_filled():
        def it_errors_under_the_url_when_only_a_label_was_posted(admin: Client):
            before = OrgLink.objects.count()
            response = LINKS.save(admin, LINKS.post(_new(label="Handbook", url=""), initial=0))
            assert response.status_code == 200
            assert LINKS.field_errors(response) == [{"url"}]
            assert LINKS.rendered_errors(response) == {"0-url"}
            assert OrgLink.objects.count() == before

        def it_errors_under_the_label_when_only_a_url_was_posted(admin: Client):
            response = LINKS.save(admin, LINKS.post(_new(label="", url="https://example.com/h"), initial=0))
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
            response = LINKS.save(admin, LINKS.post(_link(link), _new(label="Handbook", url=""), initial=1))
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
            response = FAQ.save(admin, FAQ.post(_faq(item, question="Where can I park?"), initial=1, total=2))
            assert _saved_to(response, FAQ)
            assert OrgFAQItem.objects.count() == before
            assert OrgFAQItem.objects.get(pk=item.pk).question == "Where can I park?"

        def it_saves_when_it_was_the_only_row(admin: Client):
            before = OrgFAQItem.objects.count()
            response = FAQ.save(admin, FAQ.post(initial=0, total=1))
            assert _saved_to(response, FAQ)
            assert OrgFAQItem.objects.count() == before

    def describe_a_new_row_half_filled():
        def it_errors_under_the_answer_when_only_a_question_was_posted(admin: Client):
            before = OrgFAQItem.objects.count()
            response = FAQ.save(admin, FAQ.post(_new(question="Where do I park?"), initial=0))
            assert response.status_code == 200
            assert FAQ.field_errors(response) == [{"answer"}]
            assert FAQ.rendered_errors(response) == {"0-answer"}
            assert OrgFAQItem.objects.count() == before

        def it_errors_under_the_question_when_only_an_answer_was_posted(admin: Client):
            response = FAQ.save(admin, FAQ.post(_new(question="", answer="Out back."), initial=0))
            assert response.status_code == 200
            assert FAQ.field_errors(response) == [{"question"}]
            assert FAQ.rendered_errors(response) == {"0-question"}

        def it_errors_when_only_a_video_link_was_posted(admin: Client):
            response = FAQ.save(
                admin, FAQ.post(_new(video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"), initial=0)
            )
            assert response.status_code == 200
            assert FAQ.field_errors(response) == [{"question", "answer"}]

        def it_errors_when_only_a_document_was_uploaded(admin: Client):
            payload = FAQ.post(_new(document=SimpleUploadedFile("guide.pdf", b"%PDF-1.4")), initial=0)
            response = FAQ.save(admin, payload)
            assert response.status_code == 200
            assert FAQ.field_errors(response) == [{"question", "answer"}]

    def describe_a_saved_row_blanked_out():
        def it_errors_under_the_question_and_the_answer(admin: Client):
            item = OrgFAQItemFactory(question="Where do I park?")
            response = FAQ.save(admin, FAQ.post(_faq(item, question="", answer=""), initial=1))
            assert response.status_code == 200
            assert FAQ.field_errors(response) == [{"question", "answer"}]
            assert OrgFAQItem.objects.get(pk=item.pk).question == "Where do I park?"

    def describe_a_new_row_back_with_an_error():
        def it_offers_remove_on_the_new_row_and_keeps_delete_on_the_saved_one(admin: Client):
            item = OrgFAQItemFactory()
            response = FAQ.save(admin, FAQ.post(_faq(item), _new(question="Where do I park?"), initial=1))
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
            payload = CATEGORIES.post(_category(category, name="Getting Started"), initial=1, total=2)
            response = CATEGORIES.save(admin, payload)
            assert _saved_to(response, CATEGORIES)
            assert HelpCategory.objects.count() == before
            assert HelpCategory.objects.get(pk=category.pk).name == "Getting Started"

        def it_saves_when_it_was_the_only_row(admin: Client):
            before = HelpCategory.objects.count()
            response = CATEGORIES.save(admin, CATEGORIES.post(initial=0, total=1))
            assert _saved_to(response, CATEGORIES)
            assert HelpCategory.objects.count() == before

    def describe_a_new_row_half_filled():
        def it_errors_under_the_name_when_only_a_slug_was_posted(admin: Client):
            before = HelpCategory.objects.count()
            payload = CATEGORIES.post(_new(name="", slug="kiln-room", audience="member"), initial=0)
            response = CATEGORIES.save(admin, payload)
            assert response.status_code == 200
            assert CATEGORIES.field_errors(response) == [{"name"}]
            assert CATEGORIES.rendered_errors(response) == {"0-name"}
            assert HelpCategory.objects.count() == before

        def it_errors_under_the_name_when_only_a_description_was_posted(admin: Client):
            payload = CATEGORIES.post(_new(name="", description="Firing schedules.", audience="member"), initial=0)
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
            payload = CATEGORIES.post(
                _category(category), _new(name="", slug="kiln-room", audience="member"), initial=1
            )
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
            payload = ARTICLES.post(_article(article, title="Guild Voting"), initial=1, total=2)
            response = ARTICLES.save(admin, payload)
            assert _saved_to(response, ARTICLES)
            assert WikiArticle.objects.count() == before
            assert WikiArticle.objects.get(pk=article.pk).title == "Guild Voting"

        def it_saves_when_it_was_the_only_row(admin: Client):
            before = WikiArticle.objects.count()
            response = ARTICLES.save(admin, ARTICLES.post(initial=0, total=1))
            assert _saved_to(response, ARTICLES)
            assert WikiArticle.objects.count() == before

    def describe_a_new_row_half_filled():
        def it_errors_under_the_body_when_only_a_title_was_posted(admin: Client):
            before = WikiArticle.objects.count()
            response = ARTICLES.save(admin, ARTICLES.post(_new(title="Kiln loading", is_published="on"), initial=0))
            assert response.status_code == 200
            assert ARTICLES.field_errors(response) == [{"body"}]
            assert ARTICLES.rendered_errors(response) == {"0-body"}
            assert WikiArticle.objects.count() == before

        def it_errors_under_the_title_when_only_a_body_was_posted(admin: Client):
            payload = ARTICLES.post(_new(title="", body="Load from the back.", is_published="on"), initial=0)
            response = ARTICLES.save(admin, payload)
            assert response.status_code == 200
            assert ARTICLES.field_errors(response) == [{"title"}]
            assert ARTICLES.rendered_errors(response) == {"0-title"}

    def describe_a_saved_row_blanked_out():
        def it_errors_under_the_title_and_the_body(admin: Client):
            article = WikiArticleFactory(title="Guild voting")
            response = ARTICLES.save(admin, ARTICLES.post(_article(article, title="", body=""), initial=1))
            assert response.status_code == 200
            assert ARTICLES.field_errors(response) == [{"title", "body"}]
            assert WikiArticle.objects.get(pk=article.pk).title == "Guild voting"

    def describe_a_new_row_back_with_an_error():
        def it_offers_remove_on_the_new_row_and_keeps_delete_on_the_saved_one(admin: Client):
            article = WikiArticleFactory()
            payload = ARTICLES.post(_article(article), _new(title="Kiln loading", is_published="on"), initial=1)
            response = ARTICLES.save(admin, payload)
            saved_row, new_row = ARTICLES.rows_html(response).split('name="articles-1-title"')
            assert REMOVE in new_row
            assert REMOVE not in saved_row
            assert "getElementById('id_articles-0-DELETE')" in saved_row
            assert 'id="id_articles-1-DELETE"' not in new_row
