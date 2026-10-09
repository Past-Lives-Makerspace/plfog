"""BDD specs for ClassFaq, ClassOffering.display_faqs, and gallery_display_images."""

from __future__ import annotations

from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile

from classes.factories import CategoryFactory, ClassFaqFactory, ClassImageFactory, ClassOfferingFactory
from classes.models import ARRIVAL_CLASS_FAQ, DEFAULT_CLASS_FAQS, LOCKED_CLASS_FAQS, is_locked_class_faq


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


def describe_is_locked_class_faq():
    def it_matches_each_locked_question_ignoring_case_and_surrounding_space(db):
        for faq in LOCKED_CLASS_FAQS:
            assert is_locked_class_faq(faq["question"])
            assert is_locked_class_faq(f"  {faq['question'].upper()}\n")

    def it_does_not_match_any_other_question(db):
        assert not is_locked_class_faq(DEFAULT_CLASS_FAQS[0]["question"])
        assert not is_locked_class_faq("Is the space accessible by bike?")


def describe_display_faqs():
    def it_leads_with_the_locked_questions_then_the_defaults_then_arrival_when_the_class_has_no_rows(db):
        offering = ClassOfferingFactory()
        assert offering.display_faqs == [*LOCKED_CLASS_FAQS, *DEFAULT_CLASS_FAQS, ARRIVAL_CLASS_FAQ]

    def it_puts_the_classes_own_rows_between_the_locked_questions_and_arrival(db):
        offering = ClassOfferingFactory()
        ClassFaqFactory(class_offering=offering, question="Can I bring my dog?", answer="Sadly no.")
        faqs = offering.display_faqs
        assert faqs == [
            *LOCKED_CLASS_FAQS,
            {"question": "Can I bring my dog?", "answer": "Sadly no."},
            ARRIVAL_CLASS_FAQ,
        ]

    def it_does_not_leak_defaults_alongside_custom_rows(db):
        offering = ClassOfferingFactory()
        ClassFaqFactory(class_offering=offering)
        questions = [faq["question"] for faq in offering.display_faqs]
        assert DEFAULT_CLASS_FAQS[0]["question"] not in questions

    def it_shows_a_locked_question_once_with_the_codes_copy_when_a_row_asks_it(db):
        offering = ClassOfferingFactory()
        ClassFaqFactory(
            class_offering=offering, sort_order=0, question=" what's your CANCELLATION policy? ", answer="Old copy."
        )
        ClassFaqFactory(class_offering=offering, sort_order=1, question="Is the space accessible?", answer="Old.")
        ClassFaqFactory(class_offering=offering, sort_order=2, question="Can I bring my dog?", answer="Sadly no.")
        faqs = offering.display_faqs
        assert faqs == [
            *LOCKED_CLASS_FAQS,
            {"question": "Can I bring my dog?", "answer": "Sadly no."},
            ARRIVAL_CLASS_FAQ,
        ]

    def it_falls_back_to_the_defaults_when_every_row_asks_a_locked_question(db):
        offering = ClassOfferingFactory()
        ClassFaqFactory(class_offering=offering, question=LOCKED_CLASS_FAQS[0]["question"], answer="Old copy.")
        assert offering.display_faqs == [*LOCKED_CLASS_FAQS, *DEFAULT_CLASS_FAQS, ARRIVAL_CLASS_FAQ]

    def it_does_not_duplicate_the_arrival_faq_when_a_custom_row_asks_it(db):
        offering = ClassOfferingFactory()
        ClassFaqFactory(
            class_offering=offering,
            question=ARRIVAL_CLASS_FAQ["question"],
            answer="Meet me at the loading dock instead.",
        )
        faqs = offering.display_faqs
        matching = [faq for faq in faqs if faq["question"] == ARRIVAL_CLASS_FAQ["question"]]
        assert matching == [
            {"question": ARRIVAL_CLASS_FAQ["question"], "answer": "Meet me at the loading dock instead."}
        ]

    def it_hands_out_copies_so_a_caller_cannot_change_the_site_copy(db):
        offering = ClassOfferingFactory()
        offering.display_faqs[0]["answer"] = "Changed."
        assert LOCKED_CLASS_FAQS[0]["answer"] != "Changed."


def describe_own_faqs():
    def it_lists_the_classes_rows_in_order_without_the_locked_ones(db):
        offering = ClassOfferingFactory()
        ClassFaqFactory(class_offering=offering, sort_order=1, question="Gloves?", answer="Provided.")
        ClassFaqFactory(class_offering=offering, sort_order=0, question="Is the space accessible?", answer="Old.")
        ClassFaqFactory(class_offering=offering, sort_order=2, question="Age?", answer="16 and up.")
        assert offering.own_faqs() == [
            {"question": "Gloves?", "answer": "Provided."},
            {"question": "Age?", "answer": "16 and up."},
        ]


def describe_locked_cancellation_policy():
    def it_points_cancellations_to_the_classes_inbox_with_no_late_fee(db):
        offering = ClassOfferingFactory()
        answer = next(
            faq["answer"] for faq in offering.display_faqs if faq["question"] == "What's your cancellation policy?"
        )
        assert "classes@pastlives.space" in answer
        assert "studios@pastlives.space" not in answer
        assert "$50" not in answer
        assert "We do not offer refunds for late cancellations and no-shows." in answer


def describe_gallery_display_images():
    def it_lists_only_gallery_rows(db):
        offering = ClassOfferingFactory(gallery=0)  # factory supplies a hero image
        ClassImageFactory(class_offering=offering, image=_image_file("g1.png"), alt_text="A finished bowl")
        images = offering.gallery_display_images
        assert len(images) == 1
        assert images[0]["alt"] == "A finished bowl"
        assert offering.image.url not in [img["url"] for img in images]

    def it_is_empty_without_gallery_rows_even_with_a_category_hero(db):
        category = CategoryFactory(hero_image=_image_file("cat.png"))
        offering = ClassOfferingFactory(category=category, image="", gallery=0)
        assert offering.gallery_display_images == []
        # display_images still falls back for the page hero
        assert offering.display_images


def describe_display_images():
    def it_keeps_the_hero_first_then_gallery_rows(db):
        offering = ClassOfferingFactory(gallery=0)
        ClassImageFactory(class_offering=offering, image=_image_file("g1.png"))
        images = offering.display_images
        assert len(images) == 2
        assert images[0]["url"] == offering.image.url
