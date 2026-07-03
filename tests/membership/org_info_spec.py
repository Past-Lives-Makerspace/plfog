"""BDD specs for the Space & Org Info models (singleton page + FAQ + links)."""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from membership.models import OrgFAQItem, OrgInfoPage, OrgLink
from tests.membership.factories import OrgFAQItemFactory, OrgInfoPageFactory, OrgLinkFactory

pytestmark = pytest.mark.django_db


def _image_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 120, 120)).save(buf, format="PNG")
    return buf.getvalue()


def describe_OrgInfoPage():
    def describe_load():
        def it_creates_and_returns_the_pk1_singleton():
            page = OrgInfoPage.load()
            assert page.pk == 1
            assert OrgInfoPage.objects.count() == 1

        def it_is_idempotent_and_preserves_edits():
            first = OrgInfoPage.load()
            first.intro = "Welcome to the space."
            first.save()
            second = OrgInfoPage.load()
            assert second.pk == 1
            assert second.intro == "Welcome to the space."
            assert OrgInfoPage.objects.count() == 1

    def it_forces_pk_1_even_when_created_directly():
        OrgInfoPage.objects.all().delete()
        page = OrgInfoPage.objects.create(intro="hi")
        assert page.pk == 1

    def it_stringifies_as_the_page_name():
        assert str(OrgInfoPage.load()) == "Space & Org Info"

    def it_names_the_banner_as_the_hero_field():
        assert OrgInfoPage().get_hero_image_field_name() == "banner_image"

    def describe_has_code_of_conduct():
        def it_is_false_when_both_blank():
            assert OrgInfoPage().has_code_of_conduct is False

        def it_is_true_with_a_written_body():
            assert OrgInfoPage(code_of_conduct="Be excellent to each other.").has_code_of_conduct is True

        def it_is_true_with_only_an_external_link():
            assert OrgInfoPage(code_of_conduct_url="https://example.com/coc").has_code_of_conduct is True

    def describe_floorplan_normalization():
        def it_downscales_a_large_floor_plan_to_the_hero_long_edge():
            page = OrgInfoPage.load()
            page.floorplan_image = SimpleUploadedFile("plan.png", _image_bytes(2600, 60), content_type="image/png")
            page.save()
            page.refresh_from_db()
            assert page.floorplan_image.width == 2400


def describe_OrgFAQItem():
    def it_stringifies_as_its_question():
        assert str(OrgFAQItemFactory(question="Where is the exit?")) == "Where is the exit?"

    def it_orders_by_sort_order():
        page = OrgInfoPageFactory()
        q2 = OrgFAQItem.objects.create(page=page, question="Second?", answer="A", sort_order=2)
        q1 = OrgFAQItem.objects.create(page=page, question="First?", answer="A", sort_order=1)
        assert list(page.faq_items.all()) == [q1, q2]

    def describe_document():
        def it_has_no_document_when_both_blank():
            faq = OrgFAQItemFactory()
            assert faq.has_document is False
            assert faq.document_display_name == ""

        def it_reports_an_uploaded_file():
            faq = OrgFAQItem.objects.create(
                page=OrgInfoPageFactory(),
                question="Q?",
                answer="A",
                document=SimpleUploadedFile("policy.pdf", b"%PDF-1.4"),
            )
            assert faq.has_document is True
            assert faq.document_display_name.endswith(".pdf")
            assert faq.document_href == faq.document.url

        def it_reports_an_external_link():
            faq = OrgFAQItem.objects.create(
                page=OrgInfoPageFactory(), question="Q?", answer="A", document_url="https://docs.example/x"
            )
            assert faq.has_document is True
            assert faq.document_display_name == "https://docs.example/x"
            assert faq.document_href == "https://docs.example/x"

        def it_rejects_both_a_file_and_a_link():
            from django.db import IntegrityError, transaction

            with pytest.raises(IntegrityError), transaction.atomic():
                OrgFAQItem.objects.create(
                    page=OrgInfoPageFactory(),
                    question="Q?",
                    answer="A",
                    document=SimpleUploadedFile("a.pdf", b"%PDF-1.4"),
                    document_url="https://docs.example/x",
                )


def describe_OrgLink():
    def it_stringifies_with_its_label():
        link = OrgLinkFactory(label="Member Guide")
        assert "Member Guide" in str(link)

    def it_orders_by_sort_order():
        page = OrgInfoPageFactory()
        page.links.all().delete()  # clear the migration-seeded Member Guide link
        b = OrgLink.objects.create(page=page, label="B", url="https://b.example", sort_order=2)
        a = OrgLink.objects.create(page=page, label="A", url="https://a.example", sort_order=1)
        assert list(page.links.all()) == [a, b]
