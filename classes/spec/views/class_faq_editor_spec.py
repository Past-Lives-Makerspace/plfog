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
from django.utils.html import escape

from classes.forms import LOCKED_FAQ_ERROR, build_class_faq_formset
from classes.models import DEFAULT_CLASS_FAQS, LOCKED_CLASS_FAQS, ClassFaq, ClassOffering
from classes.spec.views.class_composer_spec import _full_payload


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
        "capacity": offering.capacity,
        "scheduling_model": offering.scheduling_model,
        "sale_kind": "percent",
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
        response = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        formset = response.context["faq_formset"]
        assert [f.initial.get("question") for f in formset.forms] == [faq["question"] for faq in DEFAULT_CLASS_FAQS]

    def it_does_not_seed_when_the_class_already_has_rows(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        ClassFaqFactory(class_offering=offering, question="Custom?")
        response = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        formset = response.context["faq_formset"]
        assert len(formset.forms) == 1
        assert formset.forms[0].instance.question == "Custom?"

    def it_saves_submitted_rows_including_untouched_defaults(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _edit_payload(
                offering,
                **{
                    "faq-TOTAL_FORMS": "2",
                    "faq-0-question": "Do I need any experience?",
                    "faq-0-answer": "Rewritten: none at all.",
                    "faq-1-question": DEFAULT_CLASS_FAQS[0]["question"],
                    "faq-1-answer": DEFAULT_CLASS_FAQS[0]["answer"],
                },
            ),
        )
        assert response.status_code == 302
        saved = list(offering.faqs.values_list("question", "answer"))
        assert ("Do I need any experience?", "Rewritten: none at all.") in saved
        assert len(saved) == 2

    def it_deletes_a_row_flagged_for_deletion(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        faq = ClassFaqFactory(class_offering=offering, question="Old?", answer="Old.")
        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
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
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
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


@pytest.fixture
def instructor_user(db):
    user = UserFactory(username="lockedfaq@example.com")
    return InstructorFactory(user=user, full_legal_name="Locked Faq", instructor_slug="locked-faq")


def _locked_row(question: str) -> dict[str, str]:
    """One added FAQ row asking ``question`` with a crafted answer, as a hand-built POST would carry it."""
    return {"faq-TOTAL_FORMS": "1", "faq-0-question": question, "faq-0-answer": "Crafted: no rules at all."}


def describe_the_locked_questions_in_the_editor():
    def it_shows_them_read_only_above_the_editable_rows(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        locked = html[html.index('id="faq-locked"') : html.index('id="faq-rows"')]
        for faq in LOCKED_CLASS_FAQS:
            assert f"<strong>{escape(faq['question'])}</strong>" in locked
        assert locked.count("Set by Past Lives for every class") == len(LOCKED_CLASS_FAQS)
        assert "<input" not in locked
        assert "<textarea" not in locked
        assert "<button" not in locked
        # The editable rows follow, seeded with the remaining default only.
        assert escape(DEFAULT_CLASS_FAQS[0]["question"]) in html[html.index('id="faq-rows"') :]

    def it_shows_them_on_the_published_class_edit_page(instructor_user, client, db):
        offering = ClassOfferingFactory(instructor=instructor_user, status=ClassOffering.Status.PUBLISHED)
        client.force_login(instructor_user.user)
        response = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        assert response.templates[0].name == "classes/teach/class_form_published.html"
        assert response.content.decode().count("pl-faq-locked") == len(LOCKED_CLASS_FAQS)

    def it_offers_them_to_the_template_from_the_formset(db):
        formset = build_class_faq_formset(None, ClassOfferingFactory())
        assert formset.locked_faqs == LOCKED_CLASS_FAQS


def describe_a_crafted_post_asking_a_locked_question():
    @pytest.mark.parametrize("question", [LOCKED_CLASS_FAQS[0]["question"], "  is THE space accessible?  "])
    def it_is_refused_on_the_admin_composer(question, admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        response = client.post(url, _edit_payload(offering, **_locked_row(question)))
        assert response.status_code == 200
        assert LOCKED_FAQ_ERROR in response.content.decode()
        assert offering.faqs.count() == 0
        # The same payload with another question saves, so the locked question is the only refusal.
        assert client.post(url, _edit_payload(offering, **_locked_row("Can I bring a friend?"))).status_code == 302

    def it_is_refused_on_the_instructors_draft_composer(instructor_user, client, db):
        offering = ClassOfferingFactory(instructor=instructor_user, status=ClassOffering.Status.DRAFT)
        client.force_login(instructor_user.user)
        url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        payload = _full_payload(offering.category)
        response = client.post(url, {**payload, **_locked_row(LOCKED_CLASS_FAQS[1]["question"])})
        assert response.status_code == 200
        assert LOCKED_FAQ_ERROR in response.content.decode()
        assert offering.faqs.count() == 0
        assert client.post(url, {**payload, **_locked_row("Can I bring a friend?")}).status_code == 302

    def it_is_refused_on_the_published_class_edit(instructor_user, client, db):
        offering = ClassOfferingFactory(instructor=instructor_user, status=ClassOffering.Status.PUBLISHED)
        client.force_login(instructor_user.user)
        url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        payload = {
            "description": offering.description,
            "faq-INITIAL_FORMS": "0",
            "faq-MIN_NUM_FORMS": "0",
            "faq-MAX_NUM_FORMS": "1000",
        }
        response = client.post(url, {**payload, **_locked_row(LOCKED_CLASS_FAQS[0]["question"])})
        assert response.status_code == 200
        assert LOCKED_FAQ_ERROR in response.content.decode()
        assert offering.faqs.count() == 0
        assert client.post(url, {**payload, **_locked_row("Can I bring a friend?")}).status_code == 302

    def it_still_lets_an_existing_locked_row_be_deleted(admin_user, client, db):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        faq = ClassFaqFactory(class_offering=offering, question=LOCKED_CLASS_FAQS[0]["question"], answer="Old.")
        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
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
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, gallery=0)
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=7),
            ends_at=timezone.now() + timedelta(days=7, hours=2),
        )
        return offering

    def it_shows_the_locked_then_the_default_questions_when_the_class_has_none(published, client):
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published.slug}))
        body = response.content.decode()
        positions = [body.index(escape(faq["question"])) for faq in [*LOCKED_CLASS_FAQS, *DEFAULT_CLASS_FAQS]]
        assert positions == sorted(positions)

    def it_shows_the_classes_own_questions_instead_of_the_defaults_after_the_locked_ones(published, client):
        ClassFaqFactory(class_offering=published, question="Can I bring my dog?", answer="Sadly no.")
        ClassFaqFactory(class_offering=published, question=LOCKED_CLASS_FAQS[1]["question"], answer="Stale copy.")
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published.slug}))
        body = response.content.decode()
        assert body.index(escape(LOCKED_CLASS_FAQS[1]["question"])) < body.index("Can I bring my dog?")
        assert body.count(escape(LOCKED_CLASS_FAQS[1]["question"])) == 1
        assert "Stale copy." not in body
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
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, gallery=0)
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=7),
            ends_at=timezone.now() + timedelta(days=7, hours=2),
        )
        return offering

    def it_renders_the_gallery_above_the_booking_card_when_shots_exist(published, client):
        ClassImageFactory(class_offering=published, image=_image_file("g1.png"))
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published.slug}))
        body = response.content.decode()
        assert "cp-detail__rail-gallery" in body
        assert body.index("cp-detail__rail-gallery") < body.index("cp-detail__rail-card")

    def it_omits_the_gallery_section_without_gallery_shots(published, client):
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published.slug}))
        assert b"cp-detail__rail-gallery" not in response.content
