"""BDD specs for the instructor bio modal on the public class detail page.

The bio body is served as its own partial (``classes:public_instructor_bio``)
and pulled into the modal over HTMX, so the standalone instructor page and the
modal can never drift. These specs parse the rendered HTML rather than grepping
it, because the point of the feature is *where* the markup sits — a button in
the section header, triggers that target the modal body, and a modal shell
inside the portal page so the scoped ``.ip-*`` styles apply.
"""

from __future__ import annotations

from datetime import timedelta
from html.parser import HTMLParser

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, InstructorFactory
from classes.models import ClassOffering
from membership.models import Member

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _Elements(HTMLParser):
    """Collects every element with its attributes, text, and ancestor chain."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[dict] = []
        self.elements: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = {"tag": tag, "attrs": {k: v or "" for k, v in attrs}, "text": "", "ancestors": list(self.stack)}
        self.elements.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if self.stack:
            self.stack[-1]["text"] += data


def _parse(response) -> list[dict]:
    parser = _Elements()
    parser.feed(response.content.decode())
    return parser.elements


def _has_class(node: dict, name: str) -> bool:
    return name in node["attrs"].get("class", "").split()


def _one(elements: list[dict], tag: str, css_class: str) -> dict:
    matches = [n for n in elements if n["tag"] == tag and _has_class(n, css_class)]
    assert len(matches) == 1, f"expected exactly one <{tag} class={css_class}>, found {len(matches)}"
    return matches[0]


@pytest.fixture
def instructor(db):
    return InstructorFactory(
        full_legal_name="Jules Ashby",
        instructor_slug="jules-ashby",
        instructor_bio="I cut dovetails for a living.",
    )


@pytest.fixture
def published_class(db, instructor):
    offering = ClassOfferingFactory(
        title="Shaker Side Table",
        slug="shaker-side-table",
        category=CategoryFactory(name="Woodshop", slug="woodshop"),
        instructor=instructor,
        status=ClassOffering.Status.PUBLISHED,
    )
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=7),
        ends_at=timezone.now() + timedelta(days=7, hours=2),
    )
    return offering


def _bio_url(instructor) -> str:
    return reverse("classes:public_instructor_bio", kwargs={"slug": instructor.instructor_slug})


def describe_public_instructor_bio():
    def it_renders_the_profile_body_for_the_instructor(published_class, instructor, client):
        response = client.get(_bio_url(instructor))

        assert response.status_code == 200
        content = response.content.decode()
        assert "Jules Ashby" in content
        assert "I cut dovetails for a living." in content
        assert "Shaker Side Table" in content

    def it_returns_a_bare_partial_with_no_page_chrome(published_class, instructor, client):
        response = client.get(_bio_url(instructor))

        content = response.content.decode()
        assert "<html" not in content
        assert "cp-topbar" not in content

    def it_offers_a_link_to_the_standalone_profile_page(published_class, instructor, client):
        response = client.get(_bio_url(instructor))

        expected = reverse("classes:public_instructor", kwargs={"slug": instructor.instructor_slug})
        link = _one(_parse(response), "a", "ip-full-page-link")
        assert link["attrs"]["href"] == expected

    def it_404s_an_unknown_slug(db, client):
        response = client.get(reverse("classes:public_instructor_bio", kwargs={"slug": "nobody-here"}))

        assert response.status_code == 404

    def it_404s_an_inactive_instructor(db, client):
        retired = InstructorFactory(instructor_slug="retired", status=Member.Status.FORMER)

        response = client.get(_bio_url(retired))

        assert response.status_code == 404


def describe_standalone_instructor_page():
    def it_still_renders_the_full_page_from_the_shared_partial(published_class, instructor, client):
        url = reverse("classes:public_instructor", kwargs={"slug": instructor.instructor_slug})

        response = client.get(url)

        content = response.content.decode()
        assert response.status_code == 200
        assert "<html" in content
        assert "I cut dovetails for a living." in content
        assert "Shaker Side Table" in content
        # The "view full page" link belongs to the modal only — it would self-link here.
        assert "ip-full-page-link" not in content


def describe_class_detail_instructor_section():
    @pytest.fixture
    def elements(published_class, client):
        return _parse(client.get(reverse("classes:public_class_detail", kwargs={"slug": published_class.slug})))

    def it_puts_the_bio_button_beside_the_section_heading(elements, instructor):
        header = _one(elements, "div", "cp-detail__section-head")
        heading = next(n for n in elements if n["tag"] == "h2" and header in n["ancestors"])
        button = next(n for n in elements if n["tag"] == "button" and header in n["ancestors"])

        assert heading["text"].strip() == "Your Instructor"
        assert button["text"].strip() == "View Instructor Bio"
        assert button["attrs"]["hx-get"] == _bio_url(instructor)
        assert button["attrs"]["hx-target"] == "#instructor-bio-body"
        assert "open-modal" in button["attrs"]["@click"]

    def it_opens_the_modal_from_the_instructor_name_and_see_all_classes_links(elements, instructor):
        profile_url = reverse("classes:public_instructor", kwargs={"slug": instructor.instructor_slug})
        name_link = _one(elements, "a", "cp-detail__instructor-name")
        see_all_link = _one(elements, "a", "cp-detail__instructor-link")

        for link in (name_link, see_all_link):
            # href stays pointed at the real page, so the link still works without JS.
            assert link["attrs"]["href"] == profile_url
            assert link["attrs"]["hx-get"] == _bio_url(instructor)
            assert link["attrs"]["hx-target"] == "#instructor-bio-body"
            assert "open-modal" in link["attrs"]["@click"]

    def it_renders_the_modal_shell_inside_the_portal_page(elements):
        body = next(n for n in elements if n["attrs"].get("id") == "instructor-bio-body")
        backdrop = _one(elements, "div", "pl-modal-backdrop")

        assert backdrop in body["ancestors"]
        assert backdrop["attrs"]["role"] == "dialog"
        assert backdrop["attrs"]["aria-modal"] == "true"
        assert backdrop["attrs"]["aria-labelledby"] == "instructor-bio-title"
        # .ip-* styles are scoped to .cp-page, so the modal has to live inside it.
        assert any(_has_class(a, "cp-page") for a in backdrop["ancestors"])

    def it_names_the_modal_after_the_instructor(elements, instructor):
        title = next(n for n in elements if n["attrs"].get("id") == "instructor-bio-title")

        assert title["text"].strip() == instructor.display_name

    def describe_when_the_instructor_has_no_public_profile():
        def it_omits_the_button_and_the_modal(db, client):
            instructor = InstructorFactory(full_legal_name="Unlisted Teacher", instructor_slug="")
            offering = ClassOfferingFactory(
                title="Private Lesson",
                slug="private-lesson",
                category=CategoryFactory(),
                instructor=instructor,
                status=ClassOffering.Status.PUBLISHED,
            )
            ClassSessionFactory(
                class_offering=offering,
                starts_at=timezone.now() + timedelta(days=3),
                ends_at=timezone.now() + timedelta(days=3, hours=2),
            )

            response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))

            # Assert on markup, never on visible text: the hub's "what's new" widget
            # echoes the current CHANGELOG onto every page, release copy included.
            elements = _parse(response)
            assert [n for n in elements if _has_class(n, "cp-detail__instructor-name")] != []
            assert [n for n in elements if n["attrs"].get("hx-target") == "#instructor-bio-body"] == []
            assert [n for n in elements if n["attrs"].get("id") == "instructor-bio-body"] == []
            assert [n for n in elements if _has_class(n, "pl-modal-backdrop")] == []
