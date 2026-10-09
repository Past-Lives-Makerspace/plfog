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
        assert 'role="group" aria-label="Questions Past Lives sets for every class"' in locked
        assert "style=" not in locked
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
        assert response.content.decode().count('class="hub-card pl-faq-locked"') == len(LOCKED_CLASS_FAQS)

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


def _rows(*rows: tuple[int | None, str, str], initial: int) -> dict[str, str]:
    """FAQ rows as a posted page carries them: (id or None, question, answer), the first ``initial`` as saved."""
    data = {"faq-TOTAL_FORMS": str(len(rows)), "faq-INITIAL_FORMS": str(initial)}
    for i, (pk, question, answer) in enumerate(rows):
        data |= {f"faq-{i}-id": "" if pk is None else str(pk), f"faq-{i}-question": question, f"faq-{i}-answer": answer}
    return data


def describe_a_tab_opened_before_the_deploy():
    """The page was rendered by the previous release with the locked questions as saved rows;
    migration 0085 then deleted them, or the previous release saved one during the deploy."""

    @pytest.fixture
    def materialized(db) -> tuple[ClassOffering, list[ClassFaq]]:
        offering = ClassOfferingFactory()
        rows = [
            ClassFaqFactory(class_offering=offering, sort_order=i, question=faq["question"], answer=faq["answer"])
            for i, faq in enumerate([*LOCKED_CLASS_FAQS, *DEFAULT_CLASS_FAQS])
        ]
        return offering, rows

    def _stale_post(rows: list[ClassFaq], prior_answer: str) -> dict[str, str]:
        cancellation, accessible, prior = rows
        return _rows(
            (cancellation.pk, cancellation.question, cancellation.answer),
            (accessible.pk, accessible.question, accessible.answer),
            (prior.pk, prior.question, prior_answer),
            initial=3,
        )

    def it_saves_with_the_locked_rows_dropped_after_the_migration_deleted_them(materialized, admin_user, client):
        offering, rows = materialized
        stale = _stale_post(rows, "Edited in the old tab.")
        ClassFaq.objects.filter(pk__in=[rows[0].pk, rows[1].pk]).delete()  # what 0085 does
        client.force_login(admin_user)

        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}), _edit_payload(offering, **stale)
        )

        assert response.status_code == 302
        assert list(offering.faqs.values_list("question", "answer")) == [
            (DEFAULT_CLASS_FAQS[0]["question"], "Edited in the old tab.")
        ]

    def it_saves_with_the_locked_rows_dropped_and_deleted_when_they_still_exist(materialized, admin_user, client):
        # The previous release saved them during the deploy window, after the migration ran.
        offering, rows = materialized
        client.force_login(admin_user)

        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _edit_payload(offering, **_stale_post(rows, rows[2].answer)),
        )

        assert response.status_code == 302
        assert list(offering.faqs.values_list("pk", flat=True)) == [rows[2].pk]

    def it_keeps_a_stale_row_reworded_to_its_own_question_as_a_new_row(admin_user, client, db):
        offering = ClassOfferingFactory()
        gone = ClassFaqFactory(class_offering=offering, question=LOCKED_CLASS_FAQS[1]["question"])
        gone_pk = gone.pk
        gone.delete()
        client.force_login(admin_user)

        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _edit_payload(offering, **_rows((gone_pk, "Can I bring a friend?", "Yes, sign them up."), initial=1)),
        )

        assert response.status_code == 302
        assert list(offering.faqs.values_list("question", "answer")) == [
            ("Can I bring a friend?", "Yes, sign them up.")
        ]

    def it_saves_the_published_class_edit_from_a_stale_tab(instructor_user, client, db):
        offering = ClassOfferingFactory(instructor=instructor_user, status=ClassOffering.Status.PUBLISHED)
        gone = ClassFaqFactory(class_offering=offering, question=LOCKED_CLASS_FAQS[0]["question"], answer="Old.")
        stale = _rows((gone.pk, gone.question, gone.answer), initial=1)
        gone.delete()
        client.force_login(instructor_user.user)

        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            {"description": offering.description, "faq-MIN_NUM_FORMS": "0", "faq-MAX_NUM_FORMS": "1000", **stale},
        )

        assert response.status_code == 302
        assert offering.faqs.count() == 0

    def it_renders_a_stale_row_with_delete_not_remove_when_another_row_blocks_the_save(admin_user, client, db):
        offering = ClassOfferingFactory()
        gone = ClassFaqFactory(class_offering=offering, question="Do I need tools?", answer="")
        gone_pk = gone.pk
        gone.delete()
        client.force_login(admin_user)
        url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})

        # The stale row lost its answer, so the page comes back with the reason under it.
        response = client.post(url, _edit_payload(offering, **_rows((gone_pk, "Do I need tools?", ""), initial=1)))

        assert response.status_code == 200
        html = response.content.decode()
        rows = html[html.index('id="faq-rows"') : html.index('id="faq-empty-template"')]
        assert "Delete this question" in rows
        assert ">Remove</button>" not in rows
        assert "Select a valid choice" not in html
        # Delete posts the row with DELETE ticked, and the page saves.
        deleted = _rows((gone_pk, "Do I need tools?", ""), initial=1) | {"faq-0-DELETE": "on"}
        assert client.post(url, _edit_payload(offering, **deleted)).status_code == 302
        assert offering.faqs.count() == 0


def describe_a_locked_row_the_class_still_holds():
    def it_is_left_out_of_the_editable_rows(admin_user, client, db):
        offering = ClassOfferingFactory()
        ClassFaqFactory(class_offering=offering, question=LOCKED_CLASS_FAQS[0]["question"], answer="Old.")
        own = ClassFaqFactory(class_offering=offering, question="Gloves?", answer="Provided.")
        client.force_login(admin_user)

        formset = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).context["faq_formset"]

        assert [form.instance for form in formset.forms] == [own]

    def it_is_deleted_on_the_next_save(admin_user, client, db):
        offering = ClassOfferingFactory()
        ClassFaqFactory(class_offering=offering, question=LOCKED_CLASS_FAQS[0]["question"], answer="Old.")
        client.force_login(admin_user)

        response = client.post(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}), _edit_payload(offering))

        assert response.status_code == 302
        assert offering.faqs.count() == 0

    def it_refuses_rewording_an_own_row_into_a_locked_question(admin_user, client, db):
        offering = ClassOfferingFactory()
        own = ClassFaqFactory(class_offering=offering, question="Gloves?", answer="Provided.")
        client.force_login(admin_user)

        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _edit_payload(offering, **_rows((own.pk, "Is the space accessible", "Mine."), initial=1)),
        )

        assert response.status_code == 200
        assert LOCKED_FAQ_ERROR in response.content.decode()
        own.refresh_from_db()
        assert own.question == "Gloves?"


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
