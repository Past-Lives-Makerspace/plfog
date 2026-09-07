"""BDD specs for the instructor's workspace actions: withdraw, cancel, request a change, the
published light-edit page, Run it again on both workspaces, and the Profile tab."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.messages import get_messages
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassApproval, ClassOffering, ClassSession, CmsActivity, Registration
from core.models import Notification
from membership.models import Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory

Status = ClassOffering.Status


def _messages(response) -> list[str]:
    return [m.message for m in get_messages(response.wsgi_request)]


@pytest.fixture(autouse=True)
def _outbox():
    mail.outbox = []


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="actions-teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher A", instructor_slug="teacher-a")


@pytest.fixture
def other_instructor(db):
    user = UserFactory(username="actions-other@example.com")
    return InstructorFactory(user=user, full_legal_name="Other B", instructor_slug="other-b")


def _live(instructor, **kwargs) -> ClassOffering:
    offering = ClassOfferingFactory(
        instructor=instructor, status=Status.PUBLISHED, published_at=timezone.now(), **kwargs
    )
    start = timezone.now() + timedelta(days=3)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def _light_payload(**extra) -> dict:
    payload = {
        "description": "A long enough description of what we will make together in the studio today.",
        "prerequisites": "",
        "materials_included": "Clay",
        "materials_to_bring": "An apron",
        "safety_requirements": "",
        "age_guardian_note": "",
        "flexible_note": "",
        "video_url": "",
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
    }
    payload.update(extra)
    return payload


def describe_workspace_action_row():
    def it_offers_withdraw_on_a_pending_class(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.PENDING)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "Take back this submission?" in html
        assert reverse("classes:teach_class_withdraw", kwargs={"pk": offering.pk}) in html
        assert "Cancel this class?" not in html
        assert "Run this class again?" not in html

    def it_offers_the_live_actions_on_an_upcoming_class(instructor_fixture, client):
        offering = _live(instructor_fixture)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "Edit details" in html
        assert "Request a change" in html
        assert "Cancel this class?" in html
        assert "Run this class again?" in html
        assert reverse("classes:teach_class_duplicate_run", kwargs={"pk": offering.pk}) in html
        assert reverse("classes:teach_class_cancel", kwargs={"pk": offering.pk}) in html
        assert reverse("classes:teach_class_request_change", kwargs={"pk": offering.pk}) in html
        assert "Take back this submission?" not in html
        assert reverse("classes:admin_class_delete", kwargs={"pk": offering.pk}) not in html

    def it_offers_only_run_it_again_on_completed_and_cancelled_classes(instructor_fixture, client):
        done = ClassOfferingFactory(instructor=instructor_fixture, status=Status.PUBLISHED)
        start = timezone.now() - timedelta(days=3)
        ClassSessionFactory(class_offering=done, starts_at=start, ends_at=start + timedelta(hours=2))
        gone = ClassOfferingFactory(instructor=instructor_fixture, status=Status.CANCELLED)
        client.force_login(instructor_fixture.user)
        for offering in (done, gone):
            html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()
            assert "Run this class again?" in html
            assert "Cancel this class?" not in html
            assert "Request a change" not in html
            assert reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}) not in html


def describe_withdraw():
    def it_returns_the_class_to_draft_with_a_message(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.PENDING)
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_withdraw", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302
        assert resp.url == reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        assert "Submission withdrawn." in _messages(resp)
        offering.refresh_from_db()
        assert offering.status == Status.DRAFT
        assert offering.approvals.count() == 0

    def it_reports_a_class_that_is_not_in_review(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_withdraw", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302
        assert any("Only classes in review can be withdrawn" in m for m in _messages(resp))

    def it_404s_for_another_instructors_class_and_for_guild_staff(instructor_fixture, other_instructor, client):
        theirs = ClassOfferingFactory(instructor=other_instructor, status=Status.PENDING)
        client.force_login(instructor_fixture.user)
        assert client.post(reverse("classes:teach_class_withdraw", kwargs={"pk": theirs.pk})).status_code == 404
        # Guild staff who can edit the draft (editable_by) still cannot withdraw it.
        guild = GuildFactory(name="Staff Guild")
        GuildStaffMembershipFactory(guild=guild, member=instructor_fixture)
        staffed = ClassOfferingFactory(
            instructor=other_instructor, status=Status.PENDING, category=CategoryFactory(guild=guild)
        )
        assert staffed in ClassOffering.objects.editable_by(instructor_fixture)
        assert client.post(reverse("classes:teach_class_withdraw", kwargs={"pk": staffed.pk})).status_code == 404
        theirs.refresh_from_db()
        assert theirs.status == Status.PENDING

    def it_rejects_get(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.PENDING)
        client.force_login(instructor_fixture.user)
        assert client.get(reverse("classes:teach_class_withdraw", kwargs={"pk": offering.pk})).status_code == 405


def describe_instructor_cancel():
    def it_cancels_with_a_reason_and_names_the_refund_follow_up_when_paid(instructor_fixture, client):
        admin_user = UserFactory(username="cancel-admin@example.com", email="cancel-admin@example.com")
        admin = Member.objects.get(user=admin_user)
        admin.fog_role = Member.FogRole.ADMIN
        admin.save(update_fields=["fog_role"])
        offering = _live(instructor_fixture, title="Paid Pots")
        RegistrationFactory(
            class_offering=offering,
            email="booked@example.com",
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=5000,
        )
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_cancel", kwargs={"pk": offering.pk}), {"reason": "Kiln broke"})
        assert resp.status_code == 302
        assert resp.url == reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        assert "Class cancelled. Everyone registered has been told. An admin will handle refunds." in _messages(resp)
        offering.refresh_from_db()
        assert offering.status == Status.CANCELLED
        assert offering.cancelled_by == instructor_fixture
        assert [m for m in mail.outbox if m.to == ["booked@example.com"]]
        assert Notification.objects.filter(trigger="class_cancelled_admin_notice", user=admin_user).exists()

    def it_omits_the_refund_line_for_a_free_class(instructor_fixture, client):
        offering = _live(instructor_fixture)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_cancel", kwargs={"pk": offering.pk}), {"reason": "Sick"})
        assert "Class cancelled. Everyone registered has been told." in _messages(resp)

    def it_re_renders_the_modal_open_with_the_error_on_a_blank_reason(instructor_fixture, client):
        offering = _live(instructor_fixture)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_cancel", kwargs={"pk": offering.pk}), {"reason": " "})
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "Please tell people why." in html
        assert "x-init=\"$nextTick(() => $dispatch('open-modal', 'cancel-class'))\"" in html
        assert "Your reason is emailed to everyone registered." in html
        offering.refresh_from_db()
        assert offering.status == Status.PUBLISHED

    def it_refuses_a_class_that_is_not_live_and_404s_for_another_instructors(
        instructor_fixture, other_instructor, client
    ):
        draft = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_cancel", kwargs={"pk": draft.pk}), {"reason": "x"})
        assert resp.status_code == 302
        assert any("Only published classes can be cancelled" in m for m in _messages(resp))
        theirs = _live(other_instructor)
        assert (
            client.post(reverse("classes:teach_class_cancel", kwargs={"pk": theirs.pk}), {"reason": "x"}).status_code
            == 404
        )


def describe_request_change():
    def it_posts_full_page_and_redirects_with_a_message(instructor_fixture, client):
        offering = _live(instructor_fixture)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_request_change", kwargs={"pk": offering.pk}), {"note": "Move it to Friday."}
        )
        assert resp.status_code == 302
        assert resp.url == reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        assert "Sent to the admins." in _messages(resp)
        assert CmsActivity.objects.filter(
            kind=CmsActivity.Kind.CLASS_CHANGE_REQUESTED, class_offering=offering
        ).exists()

    def it_re_renders_the_modal_open_with_the_error_on_a_blank_note(instructor_fixture, client):
        offering = _live(instructor_fixture)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_request_change", kwargs={"pk": offering.pk}), {"note": ""})
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "Say what needs to change." in html
        assert "x-init=\"$nextTick(() => $dispatch('open-modal', 'request-change'))\"" in html
        assert not CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_CHANGE_REQUESTED).exists()

    def it_refuses_a_class_that_is_not_live(instructor_fixture, client):
        draft = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_request_change", kwargs={"pk": draft.pk}), {"note": "x"})
        assert resp.status_code == 302
        assert any("Only published classes can request a change" in m for m in _messages(resp))


def describe_published_light_edit():
    def it_renders_the_locked_summary_and_the_light_form(instructor_fixture, client):
        offering = _live(instructor_fixture, title="Locked Lathe", price_cents=8000, capacity=4)
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "This class is live." in html
        assert "Locked Details" in html
        assert "Locked Lathe" in html and "$80" in html and ">4<" in html
        assert 'name="materials_to_bring"' in html
        assert 'name="title"' not in html and 'name="price_cents"' not in html and 'name="capacity"' not in html
        assert "sessions-TOTAL_FORMS" not in html
        assert "Save &amp; Submit for Review" not in html
        assert reverse("classes:teach_class_duplicate_run", kwargs={"pk": offering.pk}) not in html
        assert "Request a Change" in html
        assert 'name="faq-TOTAL_FORMS"' in html
        assert ">Save<" in html

    def it_saves_only_the_light_fields_and_ignores_a_crafted_structural_post(instructor_fixture, client):
        offering = _live(instructor_fixture, title="Keep Title", price_cents=8000, capacity=4)
        session_count = offering.sessions.count()
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _light_payload(
                title="Hacked Title",
                price_cents="1.00",
                capacity="99",
                **{
                    "sessions-TOTAL_FORMS": "1",
                    "sessions-INITIAL_FORMS": "0",
                    "sessions-0-starts_at": "2030-01-01T10:00",
                    "sessions-0-ends_at": "2030-01-01T12:00",
                },
            ),
        )
        assert resp.status_code == 302
        assert resp.url == reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        assert "Class updated." in _messages(resp)
        offering.refresh_from_db()
        assert offering.materials_to_bring == "An apron"
        assert offering.title == "Keep Title"
        assert offering.price_cents == 8000
        assert offering.capacity == 4
        assert offering.status == Status.PUBLISHED
        assert ClassSession.objects.filter(class_offering=offering).count() == session_count

    def it_keeps_the_full_form_for_draft_and_pending_classes(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        for status in (Status.DRAFT, Status.PENDING):
            offering = ClassOfferingFactory(instructor=instructor_fixture, status=status)
            html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
            assert 'name="title"' in html
            assert "Locked Details" not in html
            assert reverse("classes:teach_class_duplicate_run", kwargs={"pk": offering.pk}) not in html

    def it_lets_guild_staff_who_can_edit_the_draft_make_light_edits(instructor_fixture, other_instructor, client):
        guild = GuildFactory(name="Edit Guild")
        GuildStaffMembershipFactory(guild=guild, member=instructor_fixture)
        offering = _live(other_instructor, category=CategoryFactory(guild=guild))
        client.force_login(instructor_fixture.user)
        assert client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).status_code == 200


def describe_run_it_again():
    def it_creates_the_undated_draft_copy_from_the_workspace(instructor_fixture, client):
        offering = _live(instructor_fixture, title="Again Anvil")
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_duplicate_run", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302
        run = ClassOffering.objects.get(slug=f"{offering.slug}-run")
        assert resp.url == reverse("classes:teach_class_edit", kwargs={"pk": run.pk})
        assert run.status == Status.DRAFT
        assert run.sessions.count() == 0
        assert run.title == "Again Anvil"

    def it_sits_on_the_admin_workspace_for_upcoming_completed_and_cancelled(admin_user, client):
        live = _live(InstructorFactory())
        gone = ClassOfferingFactory(status=Status.CANCELLED)
        pending = ClassOfferingFactory(status=Status.PENDING)
        client.force_login(admin_user)
        for offering in (live, gone):
            html = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})).content.decode()
            assert "Run this class again?" in html
            assert reverse("classes:admin_class_duplicate_run", kwargs={"pk": offering.pk}) in html
        html = client.get(reverse("classes:admin_class_detail", kwargs={"pk": pending.pk})).content.decode()
        assert "Run this class again?" not in html

    def it_is_gone_from_both_edit_pages_and_nothing_sits_under_save(admin_user, instructor_fixture, client):
        draft = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": draft.pk})).content.decode()
        assert reverse("classes:teach_class_duplicate_run", kwargs={"pk": draft.pk}) not in html
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_edit", kwargs={"pk": draft.pk})).content.decode()
        assert reverse("classes:admin_class_duplicate_run", kwargs={"pk": draft.pk}) not in html
        # The Save row is the last thing in the form: no form follows the closing </form>.
        tail = html[html.rindex("</form>") :]
        assert "<form" not in tail


def describe_classes_list_withdraw():
    def it_offers_withdraw_on_pending_rows(instructor_fixture, client):
        pending = ClassOfferingFactory(instructor=instructor_fixture, title="Pending Row", status=Status.PENDING)
        ClassOfferingFactory(instructor=instructor_fixture, title="Draft Row", status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_dashboard")).content.decode()
        assert reverse("classes:teach_class_withdraw", kwargs={"pk": pending.pk}) in html
        assert html.count("Take back this submission?") == 1


def describe_profile_tab():
    def it_states_when_the_page_goes_live_and_links_to_settings(db, client):
        user = UserFactory(username="no-slug-teacher@example.com")
        member = Member.objects.get(user=user)
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["instructor_oriented_at"])
        client.force_login(user)
        html = client.get(reverse("classes:teach_profile")).content.decode()
        card = html[
            html.index('data-card="instructor-page"') : html.index(
                "</section>", html.index('data-card="instructor-page"')
            )
        ]
        assert "Your public instructor page goes live with your first published class." in card
        assert "?tab=profile" in html
        assert "Save Profile" not in html


def describe_light_edit_scope():
    def it_hides_request_a_change_from_guild_staff_but_shows_it_to_the_instructor(
        instructor_fixture, other_instructor, client
    ):
        guild = GuildFactory(name="Scope Guild")
        GuildStaffMembershipFactory(guild=guild, member=instructor_fixture)
        offering = _live(other_instructor, category=CategoryFactory(guild=guild))
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert "This class is live." in html
        assert "Request a change" not in html
        assert reverse("classes:teach_class_request_change", kwargs={"pk": offering.pk}) not in html
        client.force_login(other_instructor.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert "Request a change" in html
        assert reverse("classes:teach_class_request_change", kwargs={"pk": offering.pk}) in html


def describe_completed_class_refusals():
    def it_refuses_to_cancel_a_class_that_already_happened(instructor_fixture, client):
        offering = _completed_class(instructor_fixture)
        RegistrationFactory(class_offering=offering, email="past@example.com", status=Registration.Status.CONFIRMED)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_cancel", kwargs={"pk": offering.pk}), {"reason": "Oops"})
        assert resp.status_code == 302
        assert "This class has already happened." in _messages(resp)
        offering.refresh_from_db()
        assert offering.status == Status.PUBLISHED
        assert [m for m in mail.outbox if m.to == ["past@example.com"]] == []

    def it_refuses_a_change_request_on_a_class_that_already_happened(instructor_fixture, client):
        offering = _completed_class(instructor_fixture)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_request_change", kwargs={"pk": offering.pk}), {"note": "x"})
        assert resp.status_code == 302
        assert "This class has already happened." in _messages(resp)
        assert not CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_CHANGE_REQUESTED).exists()


def _completed_class(instructor) -> ClassOffering:
    offering = ClassOfferingFactory(instructor=instructor, status=Status.PUBLISHED, published_at=timezone.now())
    start = timezone.now() - timedelta(days=3)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def describe_the_completed_guard_lives_on_the_model():
    """The precondition sits on ``cancel``/``request_change`` so every caller inherits it.

    It used to be a check in the two teach views, which left the admin cancel button free to
    email everyone who took a class that finished last month.
    """

    def it_refuses_an_admin_cancel_of_a_class_that_already_happened(instructor_fixture, admin_user, client):
        offering = _completed_class(instructor_fixture)
        RegistrationFactory(class_offering=offering, email="past@example.com", status=Registration.Status.CONFIRMED)
        mail.outbox.clear()
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_cancel", kwargs={"pk": offering.pk}), {"reason": "Oops"})
        assert resp.status_code == 302
        assert "This class has already happened." in _messages(resp)
        offering.refresh_from_db()
        assert offering.status == Status.PUBLISHED
        assert [m for m in mail.outbox if m.to == ["past@example.com"]] == []
        assert Notification.objects.filter(trigger="class_cancelled").count() == 0

    def it_raises_from_cancel_itself(instructor_fixture, admin_user):
        offering = _completed_class(instructor_fixture)
        with pytest.raises(ValueError, match="already happened"):
            offering.cancel(admin_user, "Oops")

    def it_raises_from_request_change_itself(instructor_fixture):
        offering = _completed_class(instructor_fixture)
        with pytest.raises(ValueError, match="already happened"):
            offering.request_change(instructor_fixture, "Please move it")

    def it_still_allows_cancelling_a_run_with_one_session_left(instructor_fixture, admin_user, client):
        # Multi-session parity: some occurrences are past, so the class is not COMPLETED.
        offering = _completed_class(instructor_fixture)
        upcoming = timezone.now() + timedelta(days=2)
        ClassSessionFactory(class_offering=offering, starts_at=upcoming, ends_at=upcoming + timedelta(hours=2))
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_cancel", kwargs={"pk": offering.pk}), {"reason": "Snow"})
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.status == Status.CANCELLED


def describe_withdraw_activity_actor():
    def it_records_the_instructor_as_the_actor(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.PENDING)
        client.force_login(instructor_fixture.user)
        client.post(reverse("classes:teach_class_withdraw", kwargs={"pk": offering.pk}))
        row = CmsActivity.objects.get(kind=CmsActivity.Kind.CLASS_WITHDRAWN, class_offering=offering)
        assert row.actor == instructor_fixture.user


def describe_cancel_modal_counts():
    def it_counts_seats_as_registered_and_leaves_the_waitlist_out(instructor_fixture, client):
        # "registered" is the Capacity row's number (confirmed plus pending payment); a
        # waitlisted person holds no seat and is not counted.
        offering = _live(instructor_fixture)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED, amount_paid_cents=5000)
        RegistrationFactory(class_offering=offering, status=Registration.Status.WAITLISTED)
        RegistrationFactory(class_offering=offering, status=Registration.Status.PENDING)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "2 registered, 1 paid." in html
        assert "3 registered" not in html
