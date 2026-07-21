"""BDD specs for the per-class FAQ editor and its public rendering,
plus the gallery block under the public page's booking rail."""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    ClassFaqFactory,
    ClassImageFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    UserFactory,
)
from classes.models import DEFAULT_CLASS_FAQS, ClassFaq, ClassOffering


def _image_file(name: str = "shot.png") -> SimpleUploadedFile:
    buf = BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _edit_payload(offering: ClassOffering, **faq_fields: str) -> dict:
    """A minimal valid admin edit POST; faq_fields override/extend the faq-* keys."""
    payload = {
        "title": offering.title,
        "slug": offering.slug,
        "category": offering.category.pk,
        "instructor": offering.instructor.pk,
        "price_cents": f"{offering.price_cents / 100:.2f}",
        "member_discount_pct": offering.member_discount_pct,
        "capacity": offering.capacity,
        "scheduling_model": offering.scheduling_model,
        "scheduling_type": offering.scheduling_type,
        "description": offering.description,
        "prerequisites": "",
        "materials_included": "",
        "materials_to_bring": "",
        "safety_requirements": "",
        "age_guardian_note": "",
        "flexible_note": "",
        "private_for_name": "",
        "sessions-TOTAL_FORMS": "0",
        "sessions-INITIAL_FORMS": "0",
        "sessions-MIN_NUM_FORMS": "0",
        "sessions-MAX_NUM_FORMS": "1000",
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
    }
    payload.update(faq_fields)
    return payload


def describe_admin_faq_editor():
    def it_seeds_the_default_questions_as_editable_rows(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        response = client.get(reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}))
        formset = response.context["faq_formset"]
        assert [f.initial.get("question") for f in formset.forms] == [faq["question"] for faq in DEFAULT_CLASS_FAQS]

    def it_does_not_seed_when_the_class_already_has_rows(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        ClassFaqFactory(class_offering=offering, question="Custom?")
        response = client.get(reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}))
        formset = response.context["faq_formset"]
        assert len(formset.forms) == 1
        assert formset.forms[0].instance.question == "Custom?"

    def it_saves_submitted_rows_including_untouched_defaults(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        response = client.post(
            reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}),
            _edit_payload(
                offering,
                **{
                    "faq-TOTAL_FORMS": "2",
                    "faq-0-question": "What's your cancellation policy?",
                    "faq-0-answer": "Rewritten: 48 hours, no questions asked.",
                    "faq-1-question": DEFAULT_CLASS_FAQS[2]["question"],
                    "faq-1-answer": DEFAULT_CLASS_FAQS[2]["answer"],
                },
            ),
        )
        assert response.status_code == 302
        saved = list(offering.faqs.values_list("question", "answer"))
        assert ("What's your cancellation policy?", "Rewritten: 48 hours, no questions asked.") in saved
        assert len(saved) == 2

    def it_deletes_a_row_flagged_for_deletion(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        faq = ClassFaqFactory(class_offering=offering, question="Old?", answer="Old.")
        response = client.post(
            reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}),
            _edit_payload(
                offering,
                **{
                    "faq-TOTAL_FORMS": "1",
                    "faq-INITIAL_FORMS": "1",
                    "faq-0-id": str(faq.pk),
                    "faq-0-question": faq.question,
                    "faq-0-answer": faq.answer,
                    "faq-0-DELETE": "on",
                },
            ),
        )
        assert response.status_code == 302
        assert not ClassFaq.objects.filter(pk=faq.pk).exists()

    def it_rerenders_with_errors_when_a_row_is_incomplete(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        response = client.post(
            reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}),
            _edit_payload(
                offering,
                **{
                    "faq-TOTAL_FORMS": "1",
                    "faq-0-question": "A question with no answer?",
                    "faq-0-answer": "",
                },
            ),
        )
        assert response.status_code == 200
        assert offering.faqs.count() == 0


def describe_teach_faq_editor():
    @pytest.fixture
    def instructor_fixture(db):
        user = UserFactory(username="faqteacher@example.com")
        return InstructorFactory(user=user, full_legal_name="Faq Teacher", instructor_slug="faq-teacher")

    def it_seeds_the_default_questions_on_the_teach_form(instructor_fixture, client, db):
        client.force_login(instructor_fixture.user)
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=ClassOffering.Status.DRAFT)
        response = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        formset = response.context["faq_formset"]
        assert [f.initial.get("question") for f in formset.forms] == [faq["question"] for faq in DEFAULT_CLASS_FAQS]


def describe_public_faq_rendering():
    @pytest.fixture
    def published(db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=7),
            ends_at=timezone.now() + timedelta(days=7, hours=2),
        )
        return offering

    def it_shows_the_default_questions_when_the_class_has_none(published, client):
        from django.utils.html import escape

        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published.slug}))
        body = response.content.decode()
        for faq in DEFAULT_CLASS_FAQS:
            assert escape(faq["question"]) in body

    def it_shows_the_classes_own_questions_instead(published, client):
        from django.utils.html import escape

        ClassFaqFactory(class_offering=published, question="Can I bring my dog?", answer="Sadly no.")
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published.slug}))
        body = response.content.decode()
        assert "Can I bring my dog?" in body
        assert escape(DEFAULT_CLASS_FAQS[0]["question"]) not in body

    def it_links_urls_and_emails_in_answers(published, client):
        ClassFaqFactory(
            class_offering=published,
            question="Who do I email?",
            answer="Reach us at info@pastlives.space anytime.",
        )
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published.slug}))
        assert b'href="mailto:info@pastlives.space"' in response.content


def describe_rail_gallery():
    @pytest.fixture
    def published(db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=7),
            ends_at=timezone.now() + timedelta(days=7, hours=2),
        )
        return offering

    def it_renders_the_gallery_under_the_booking_rail_when_shots_exist(published, client):
        ClassImageFactory(class_offering=published, image=_image_file("g1.png"))
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published.slug}))
        assert b"cp-detail__rail-gallery" in response.content

    def it_omits_the_gallery_section_without_gallery_shots(published, client):
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published.slug}))
        assert b"cp-detail__rail-gallery" not in response.content
