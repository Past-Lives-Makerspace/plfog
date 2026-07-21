"""BDD specs for ClassFaq, ClassOffering.display_faqs, and gallery_display_images."""

from __future__ import annotations

from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile

from classes.factories import CategoryFactory, ClassFaqFactory, ClassImageFactory, ClassOfferingFactory
from classes.models import DEFAULT_CLASS_FAQS


def _image_file(name: str = "shot.png") -> SimpleUploadedFile:
    buf = BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def describe_ClassFaq():
    def it_uses_the_question_as_str(db):
        faq = ClassFaqFactory(question="Do I need tools?")
        assert str(faq) == "Do I need tools?"

    def it_orders_by_sort_order_then_id(db):
        offering = ClassOfferingFactory()
        later = ClassFaqFactory(class_offering=offering, sort_order=2)
        first = ClassFaqFactory(class_offering=offering, sort_order=1)
        assert list(offering.faqs.all()) == [first, later]

    def it_cascades_when_offering_is_deleted(db):
        offering = ClassOfferingFactory()
        faq = ClassFaqFactory(class_offering=offering)
        offering.delete()
        from classes.models import ClassFaq

        assert not ClassFaq.objects.filter(pk=faq.pk).exists()


def describe_display_faqs():
    def it_falls_back_to_the_site_defaults_when_the_class_has_no_rows(db):
        offering = ClassOfferingFactory()
        assert offering.display_faqs == DEFAULT_CLASS_FAQS

    def it_returns_only_the_classes_own_rows_when_customized(db):
        offering = ClassOfferingFactory()
        ClassFaqFactory(class_offering=offering, question="Can I bring my dog?", answer="Sadly no.")
        faqs = offering.display_faqs
        assert faqs == [{"question": "Can I bring my dog?", "answer": "Sadly no."}]

    def it_does_not_leak_defaults_alongside_custom_rows(db):
        offering = ClassOfferingFactory()
        ClassFaqFactory(class_offering=offering)
        questions = [faq["question"] for faq in offering.display_faqs]
        assert DEFAULT_CLASS_FAQS[0]["question"] not in questions


def describe_gallery_display_images():
    def it_lists_only_gallery_rows(db):
        offering = ClassOfferingFactory()  # factory supplies a hero image
        ClassImageFactory(class_offering=offering, image=_image_file("g1.png"), alt_text="A finished bowl")
        images = offering.gallery_display_images
        assert len(images) == 1
        assert images[0]["alt"] == "A finished bowl"
        assert offering.image.url not in [img["url"] for img in images]

    def it_is_empty_without_gallery_rows_even_with_a_category_hero(db):
        category = CategoryFactory(hero_image=_image_file("cat.png"))
        offering = ClassOfferingFactory(category=category, image="")
        assert offering.gallery_display_images == []
        # display_images still falls back for the page hero
        assert offering.display_images


def describe_display_images():
    def it_keeps_the_hero_first_then_gallery_rows(db):
        offering = ClassOfferingFactory()
        ClassImageFactory(class_offering=offering, image=_image_file("g1.png"))
        images = offering.display_images
        assert len(images) == 2
        assert images[0]["url"] == offering.image.url
