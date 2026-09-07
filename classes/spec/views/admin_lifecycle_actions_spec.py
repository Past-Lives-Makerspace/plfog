"""BDD specs for the admin side of the class lifecycle: the two-stage queue, Remind lead, the
publish confirm, Cancel / Archive / Restore, the readiness guard on approve, the review page
readiness block, the lifecycle facets, and the permission edges."""

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
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassApproval, ClassOffering, CmsActivity, Registration
from core.models import Notification
from tests.membership.factories import GuildFactory

Status = ClassOffering.Status


def _messages(response) -> list[str]:
    return [m.message for m in get_messages(response.wsgi_request)]


def _lead_user(username: str = "lead@example.com"):
    """A linked, email-bearing user whose auto-created Member can lead a guild."""
    return UserFactory(username=username, email=username, first_name="Lena", last_name="Lead")


def _guild_with_lead(lead_user, name: str = "Woodshop"):
    from membership.models import Member

    return GuildFactory(name=name, guild_lead=Member.objects.get(user=lead_user))


@pytest.fixture(autouse=True)
def _outbox():
    mail.outbox = []


@pytest.fixture
def cms_admin_user(db):
    """A plain member holding the CLASS_APPROVER capability (a CMS Administrator)."""
    from membership.models import AdminCapability, Member
    from tests.membership.factories import MembershipPlanFactory

    MembershipPlanFactory()
    user = UserFactory(username="cms@example.com")
    Member.objects.get(user=user).admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
    return user


def _upcoming_published(**kwargs) -> ClassOffering:
    offering = ClassOfferingFactory(status=Status.PUBLISHED, published_at=timezone.now(), **kwargs)
    start = timezone.now() + timedelta(days=3)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def _payload(cat, inst, **extra) -> dict:
    payload = {
        "title": "Direct Publish",
        "category": cat.pk,
        "instructor": inst.pk,
        "price_cents": "50.00",
        "member_discount_pct": 10,
        "capacity": 6,
        "scheduling_model": "flexible",
        "sale_kind": "percent",
        "scheduling_type": "single_session",
        "description": "d",
        "prerequisites": "",
        "materials_included": "",
        "materials_to_bring": "",
        "safety_requirements": "",
        "age_guardian_note": "",
        "flexible_note": "",
        "private_for_name": "",
        "recurring_pattern": "",
        "sessions-TOTAL_FORMS": "0",
        "sessions-INITIAL_FORMS": "0",
        "sessions-MIN_NUM_FORMS": "0",
        "sessions-MAX_NUM_FORMS": "1000",
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
        "images-TOTAL_FORMS": "0",
        "images-INITIAL_FORMS": "0",
        "images-MIN_NUM_FORMS": "0",
        "images-MAX_NUM_FORMS": "1000",
    }
    payload.update(extra)
    return payload


def _stored_class_images() -> set[str]:
    from django.core.files.storage import default_storage

    from classes.models import CLASS_IMAGE_PREFIX

    try:
        return set(default_storage.listdir(CLASS_IMAGE_PREFIX)[1])
    except FileNotFoundError:
        return set()


def _real_png(name: str = "hero.png"):
    from io import BytesIO

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _fake_png(name: str):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, content_type="image/png")


def _bounced_admin_rows(n: int, start: int = 0) -> None:
    for i in range(start, start + n):
        offering = ClassOfferingFactory(title=f"Bounced {i}", status=Status.DRAFT)
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.ADMIN,
            decision=ClassApproval.Decision.CHANGES_REQUESTED,
            notes=f"Note {i}",
            decided_at=timezone.now(),
        )


def describe_admin_overview_queue():
    def it_splits_waiting_on_you_from_with_guild_leads(admin_user, client, db):
        lead_user = _lead_user()
        guild = _guild_with_lead(lead_user)
        with_lead = ClassOfferingFactory(
            title="Lead Holds It", status=Status.PENDING, category=CategoryFactory(guild=guild)
        )
        ClassApproval.objects.create(class_offering=with_lead, role=ClassApproval.Role.GUILD_LEAD)
        rowless = ClassOfferingFactory(title="Rowless Pending", status=Status.PENDING)
        admin_row = ClassOfferingFactory(title="Admin Gate Open", status=Status.PENDING)
        ClassApproval.objects.create(class_offering=admin_row, role=ClassApproval.Role.ADMIN)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_overview"))
        assert set(resp.context["waiting_on_you"]) == {rowless, admin_row}
        assert [item["offering"] for item in resp.context["with_guild_leads"]] == [with_lead]
        assert resp.context["stats"]["awaiting_you"] == 2
        assert resp.context["stats"]["with_leads"] == 1
        html = resp.content.decode()
        assert "Waiting on You" in html and "With Guild Leads" in html
        assert "Lena Lead" in html
        assert reverse("classes:admin_class_remind_lead", kwargs={"pk": with_lead.pk}) in html
        # Approve opens the publish confirm on the overview row.
        assert "Publish this class?" in html
        assert "posts to Discord" in html

    def it_shows_the_empty_states(admin_user, client, db):
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_overview")).content.decode()
        assert "Nothing waiting on you." in html
        assert "Nothing with guild leads." in html

    def it_offers_review_it_yourself_for_a_leadless_guild(admin_user, client, db):
        from tests.membership.factories import MemberFactory

        # The lead exists (so the gate was required) but has no email, and there is no staff.
        silent_lead = MemberFactory(_pre_signup_email="")
        guild = GuildFactory(name="Silent Guild", guild_lead=silent_lead)
        offering = ClassOfferingFactory(status=Status.PENDING, category=CategoryFactory(guild=guild))
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_overview"))
        assert resp.context["with_guild_leads"][0]["leadless"] is True
        html = resp.content.decode()
        assert "This guild has no lead. Review it yourself." in html
        assert reverse("classes:admin_class_remind_lead", kwargs={"pk": offering.pk}) not in html


def describe_remind_lead():
    @pytest.fixture
    def held_by_lead(db):
        lead_user = _lead_user("remind-lead@example.com")
        guild = _guild_with_lead(lead_user, "Remind Guild")
        offering = ClassOfferingFactory(ready=True, status=Status.PENDING, category=CategoryFactory(guild=guild))
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        return offering

    def it_sends_once_per_day_and_never_the_instructor_explainer(admin_user, client, held_by_lead):
        from classes.factories import InstructorFactory

        instructor = InstructorFactory(user=UserFactory(email="teacher@example.com"), instructor_slug="rem-t")
        held_by_lead.instructor = instructor
        held_by_lead.save(update_fields=["instructor"])
        client.force_login(admin_user)
        url = reverse("classes:admin_class_remind_lead", kwargs={"pk": held_by_lead.pk})
        first = client.post(url)
        assert first.status_code == 204
        assert "Reminder sent to Lena Lead." in first["HX-Trigger"]
        lead_mail = [m for m in mail.outbox if m.to == ["remind-lead@example.com"]]
        assert len(lead_mail) == 1
        assert "[missing:" not in lead_mail[0].body
        assert [m for m in mail.outbox if m.to == ["teacher@example.com"]] == []
        second = client.post(url)
        assert second.status_code == 204
        assert "Already reminded today." in second["HX-Trigger"]
        assert len([m for m in mail.outbox if m.to == ["remind-lead@example.com"]]) == 1

    def it_names_the_guild_leads_when_the_guild_has_staff_but_no_lead(admin_user, client, db):
        from membership.models import GuildStaffMembership, Member
        from tests.membership.factories import GuildStaffMembershipFactory, MemberFactory

        lead = MemberFactory(_pre_signup_email="")
        guild = GuildFactory(name="Staffed", guild_lead=lead)
        staff_user = UserFactory(username="staff@example.com", email="staff@example.com")
        GuildStaffMembershipFactory(
            guild=guild, member=Member.objects.get(user=staff_user), role=GuildStaffMembership.Role.CO_LEAD
        )
        guild.guild_lead = None
        guild.save(update_fields=["guild_lead"])
        offering = ClassOfferingFactory(status=Status.PENDING, category=CategoryFactory(guild=guild))
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_remind_lead", kwargs={"pk": offering.pk}))
        assert "Reminder sent to the guild leads." in resp["HX-Trigger"]

    def it_toasts_an_error_for_a_leadless_guild(admin_user, client, db):
        from tests.membership.factories import MemberFactory

        guild = GuildFactory(name="Nobody", guild_lead=MemberFactory(_pre_signup_email=""))
        offering = ClassOfferingFactory(status=Status.PENDING, category=CategoryFactory(guild=guild))
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_remind_lead", kwargs={"pk": offering.pk}))
        assert resp.status_code == 204
        assert "This guild has no lead. Review it yourself." in resp["HX-Trigger"]
        assert mail.outbox == []

    def it_toasts_an_error_when_the_class_is_not_waiting_on_a_lead(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.PENDING)
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_remind_lead", kwargs={"pk": offering.pk}))
        assert resp.status_code == 204
        assert "not waiting on a guild lead" in resp["HX-Trigger"]

    def it_rejects_get(admin_user, client, held_by_lead):
        client.force_login(admin_user)
        assert client.get(reverse("classes:admin_class_remind_lead", kwargs={"pk": held_by_lead.pk})).status_code == 405


def describe_admin_class_detail_actions():
    def it_shows_the_pipeline_strip_and_publish_confirm_on_a_pending_class(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.PENDING)
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert 'aria-label="Waiting on an admin"' in html
        assert "Publish this class?" in html
        assert "Review with notes" in html
        assert "Archive this class?" in html
        assert "Nobody is notified." in html
        assert "Cancel this class?" not in html

    def it_shows_the_reviewer_note_on_a_bounced_class(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.ADMIN,
            decision=ClassApproval.Decision.CHANGES_REQUESTED,
            notes="Fix the price.",
            decided_at=timezone.now(),
        )
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "Changes requested" in html
        assert "Fix the price." in html

    def it_offers_cancel_and_hides_archive_for_an_upcoming_class_with_registrations(admin_user, client, db):
        offering = _upcoming_published()
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "Cancel this class?" in html
        assert "1 registered, 0 paid." in html
        assert "Cancel this class instead. It has upcoming dates and 1 active registrations." in html
        assert "Archive this class?" not in html
        assert reverse("classes:admin_class_cancel", kwargs={"pk": offering.pk}) in html

    def it_offers_archive_for_an_upcoming_class_without_registrations(admin_user, client, db):
        offering = _upcoming_published()
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "Archive this class?" in html
        assert "Cancel this class?" in html

    def it_offers_restore_on_an_archived_class(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.ARCHIVED)
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "Restore to draft?" in html
        assert reverse("classes:admin_class_restore", kwargs={"pk": offering.pk}) in html
        assert reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}) not in html
        assert "Archive this class?" not in html

    def it_shows_the_cancel_record_on_a_cancelled_class(admin_user, client, db):
        offering = ClassOfferingFactory(
            status=Status.CANCELLED, cancelled_at=timezone.now(), cancellation_reason="Kiln broke"
        )
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "Reason: Kiln broke" in html
        assert "Cancel this class?" not in html
        assert "Archive this class?" in html

    def it_shows_the_publish_confirm_to_a_cms_administrator(cms_admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.PENDING)
        client.force_login(cms_admin_user)
        html = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "Publish this class?" in html
        assert reverse("classes:admin_class_cancel", kwargs={"pk": offering.pk}) not in html
        assert reverse("classes:admin_class_restore", kwargs={"pk": offering.pk}) not in html


def describe_admin_class_cancel():
    def it_cancels_with_a_reason_and_tells_everyone(admin_user, client, db):
        member = UserFactory(last_login=timezone.now(), email="member@example.com")
        offering = _upcoming_published(title="Forge Night")
        RegistrationFactory(class_offering=offering, email="booked@example.com", status=Registration.Status.CONFIRMED)
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:admin_class_cancel", kwargs={"pk": offering.pk}), {"reason": "The forge is down."}
        )
        assert resp.status_code == 302
        assert resp.url == reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})
        assert "Class cancelled. Everyone registered has been told." in _messages(resp)
        offering.refresh_from_db()
        assert offering.status == Status.CANCELLED
        assert offering.cancellation_reason == "The forge is down."
        assert offering.cancelled_by == admin_user.member
        booked = [m for m in mail.outbox if m.to == ["booked@example.com"]]
        assert len(booked) == 1 and "Reason: The forge is down." in booked[0].body
        assert Notification.objects.filter(trigger="class_cancelled", user=member).exists()

    def it_re_renders_the_modal_open_with_the_error_on_a_blank_reason(admin_user, client, db):
        offering = _upcoming_published()
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_cancel", kwargs={"pk": offering.pk}), {"reason": "  "})
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "Please tell people why." in html
        # The reopen waits a tick so the child modal's listener is bound before the dispatch.
        assert "x-init=\"$nextTick(() => $dispatch('open-modal', 'cancel-class'))\"" in html
        assert "@open-modal.window=\"if ($event.detail === 'cancel-class') open = true\"" in html
        # The error renders inside the modal body, not stranded on the page.
        body_at = html.index('id="cancel-class-body"')
        assert html.index("Please tell people why.") > body_at
        assert html.index("</form>", body_at) > html.index("Please tell people why.")
        offering.refresh_from_db()
        assert offering.status == Status.PUBLISHED

    def it_does_not_dispatch_the_reopen_on_a_plain_get(admin_user, client, db):
        offering = _upcoming_published()
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})).content.decode()
        # The Cancel button dispatches the same event on click; only the auto-reopen is absent.
        assert "x-init=\"$nextTick(() => $dispatch('open-modal', 'cancel-class'))\"" not in html

    def it_refuses_a_class_that_is_not_published(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_cancel", kwargs={"pk": offering.pk}), {"reason": "Why"})
        assert resp.status_code == 302
        assert any("Only published classes can be cancelled" in m for m in _messages(resp))
        offering.refresh_from_db()
        assert offering.status == Status.DRAFT


def describe_admin_class_archive():
    def it_archives_quietly_and_says_so(admin_user, client, db):
        bystander = UserFactory(last_login=timezone.now(), email="bystander@example.com")
        offering = ClassOfferingFactory(status=Status.DRAFT)
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_archive", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302
        assert any("Nobody was notified." in m for m in _messages(resp))
        offering.refresh_from_db()
        assert offering.status == Status.ARCHIVED
        assert mail.outbox == []
        assert not Notification.objects.filter(trigger="class_cancelled", user=bystander).exists()

    def it_refuses_an_upcoming_class_with_registrations(admin_user, client, db):
        offering = _upcoming_published()
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_archive", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302
        assert resp.url == reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})
        assert any("Cancel this class instead." in m for m in _messages(resp))
        offering.refresh_from_db()
        assert offering.status == Status.PUBLISHED


def describe_admin_class_restore():
    def it_restores_to_draft(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.ARCHIVED)
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_restore", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302
        assert any("restored to draft" in m for m in _messages(resp))
        offering.refresh_from_db()
        assert offering.status == Status.DRAFT
        assert offering.approvals.count() == 0
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_RESTORED, class_offering=offering).exists()

    def it_refuses_a_class_that_is_not_archived(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED)
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_restore", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302
        assert any("Only archived classes can be restored" in m for m in _messages(resp))


def describe_readiness_guard_on_approve():
    def it_quick_approve_shows_an_error_and_redirects_on_an_unready_class(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.PENDING, description="Short")
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_approve", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302
        assert any(m.startswith("Not ready to publish:") for m in _messages(resp))
        offering.refresh_from_db()
        assert offering.status == Status.PENDING
        # Refused before the admin row is minted, so nothing is stranded open.
        assert not offering.approvals.exists()

    def it_refused_quick_approve_does_not_block_the_guild_leads_later_escalation(admin_user, client, db):
        from membership.models import AdminCapability, Member

        lead_user = _lead_user("escalate-lead@example.com")
        guild = _guild_with_lead(lead_user, "Escalate Guild")
        offering = ClassOfferingFactory(
            status=Status.PENDING, description="Short", category=CategoryFactory(guild=guild)
        )
        gate = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        holder_user = UserFactory(username="cms-esc@example.com", email="cms-esc@example.com")
        Member.objects.get(user=holder_user).admin_capabilities.create(
            capability=AdminCapability.Capability.CLASS_APPROVER
        )
        client.force_login(admin_user)
        client.post(reverse("classes:admin_class_approve", kwargs={"pk": offering.pk}))
        assert not offering.approvals.filter(role=ClassApproval.Role.ADMIN).exists()
        mail.outbox = []
        gate.decide(ClassApproval.Decision.APPROVED, user=lead_user)
        assert offering.approvals.filter(role=ClassApproval.Role.ADMIN, decision="").count() == 1
        assert [m for m in mail.outbox if m.to == ["cms-esc@example.com"]]

    def it_admin_review_page_shows_the_readiness_message_as_a_form_error(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.PENDING, description="Short")
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:admin_class_review", kwargs={"pk": offering.pk}),
            {"decision": ClassApproval.Decision.APPROVED, "notes": ""},
        )
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "Not ready to publish: Write a short description. Add at least one date." in html
        assert "This class is not ready to publish yet." in html
        offering.refresh_from_db()
        assert offering.status == Status.PENDING

    def it_tokenized_review_page_shows_the_readiness_message_as_a_form_error(client, db):
        offering = ClassOfferingFactory(status=Status.PENDING, description="Short")
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        resp = client.post(
            reverse("classes:class_review", kwargs={"token": row.token}),
            {"decision": ClassApproval.Decision.APPROVED, "notes": ""},
        )
        assert resp.status_code == 200
        assert "Not ready to publish:" in resp.content.decode()
        row.refresh_from_db()
        assert row.decision == ""

    def it_review_page_renders_the_readiness_block_and_the_pipeline_strip(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.PENDING, description="Short")
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk})).content.decode()
        assert "Readiness" in html
        assert "Add at least one date." in html
        assert "Write a short description." in html
        assert "Review Pipeline" in html
        assert "Approval Progress" not in html
        assert "This class is not ready to publish yet." in html

    def it_review_page_carries_no_hint_on_a_ready_class(admin_user, client, db):
        offering = ClassOfferingFactory(ready=True, status=Status.PENDING)
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk})).content.decode()
        assert "This class is not ready to publish yet." not in html


def describe_admin_class_create_publish_path():
    def it_refuses_an_unready_class_before_writing_anything(admin_user, client, db):
        from classes.factories import InstructorFactory
        from core.models import SiteActivity

        files_before = _stored_class_images()
        activity_before = CmsActivity.objects.count()
        site_before = SiteActivity.objects.count()
        client.force_login(admin_user)
        # Photos present, but the description is short and a flexible class has no note.
        resp = client.post(
            reverse("classes:admin_class_create"),
            _payload(
                CategoryFactory(),
                InstructorFactory(),
                image=_real_png(),
                gallery_images=[_fake_png("g.png")],
            ),
        )
        assert resp.status_code == 200
        assert "Not ready to publish: Write a short description. Say how students pick a time." in (
            resp.content.decode()
        )
        assert not ClassOffering.objects.filter(title="Direct Publish").exists()
        assert _stored_class_images() == files_before
        assert CmsActivity.objects.count() == activity_before
        assert SiteActivity.objects.count() == site_before

    def it_reads_a_past_session_as_no_date(admin_user, client, db):
        from classes.factories import READY_DESCRIPTION, InstructorFactory

        past = timezone.now() - timedelta(days=2)
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:admin_class_create"),
            _payload(
                CategoryFactory(),
                InstructorFactory(),
                description=READY_DESCRIPTION,
                scheduling_model="fixed",
                image=_real_png(),
                gallery_images=[_fake_png("g.png")],
                **{
                    "sessions-TOTAL_FORMS": "1",
                    "sessions-0-starts_at": past.strftime("%Y-%m-%dT%H:%M"),
                    "sessions-0-ends_at": (past + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                },
            ),
        )
        assert resp.status_code == 200
        assert "Not ready to publish: Add at least one date." in resp.content.decode()
        assert not ClassOffering.objects.filter(title="Direct Publish").exists()

    def it_rolls_back_files_and_activity_when_the_gallery_cap_refuses_after_the_save(admin_user, client, db):
        from classes.factories import READY_DESCRIPTION, InstructorFactory
        from classes.models import MAX_GALLERY_IMAGES, ClassImage
        from core.models import SiteActivity

        files_before = _stored_class_images()
        activity_before = CmsActivity.objects.count()
        site_before = SiteActivity.objects.count()
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:admin_class_create"),
            _payload(
                CategoryFactory(),
                InstructorFactory(),
                description=READY_DESCRIPTION,
                flexible_note="We will pick a time together.",
                image=_real_png(),
                gallery_images=[_fake_png(f"g{i}.png") for i in range(MAX_GALLERY_IMAGES + 1)],
            ),
        )
        assert resp.status_code == 200
        assert f"at most {MAX_GALLERY_IMAGES} images" in resp.content.decode()
        assert not ClassOffering.objects.filter(title="Direct Publish").exists()
        assert not ClassImage.objects.exists()
        assert _stored_class_images() == files_before
        assert CmsActivity.objects.count() == activity_before
        assert SiteActivity.objects.count() == site_before

    def it_publishes_a_ready_class_through_publish_exactly_once(admin_user, client, db):
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        from classes.factories import READY_DESCRIPTION, InstructorFactory

        member = UserFactory(last_login=timezone.now(), email="member2@example.com")
        buf = BytesIO()
        Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, "PNG")
        hero = SimpleUploadedFile("hero.png", buf.getvalue(), content_type="image/png")
        gallery = SimpleUploadedFile("g.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, content_type="image/png")
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:admin_class_create"),
            _payload(
                CategoryFactory(),
                InstructorFactory(),
                description=READY_DESCRIPTION,
                flexible_note="We will pick a time together.",
                image=hero,
                gallery_images=[gallery],
            ),
        )
        assert resp.status_code == 302
        created = ClassOffering.objects.get(title="Direct Publish")
        assert created.status == Status.PUBLISHED
        assert created.approved_by == admin_user
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_PUBLISHED, class_offering=created).count() == 1
        assert Notification.objects.filter(trigger="class_published", user=member).count() == 1


def describe_admin_classes_facets():
    def it_renders_lifecycle_chips_with_counts_and_badges(admin_user, client, db):
        ClassOfferingFactory(title="Bounced One", status=Status.DRAFT)
        bounced = ClassOffering.objects.get(title="Bounced One")
        ClassApproval.objects.create(
            class_offering=bounced,
            role=ClassApproval.Role.ADMIN,
            decision=ClassApproval.Decision.DENIED,
            decided_at=timezone.now(),
        )
        _upcoming_published(title="Live One")
        ClassOfferingFactory(title="Gone One", status=Status.CANCELLED)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_classes"))
        chips = {label: (count, selected) for _url, label, count, selected in resp.context["status_filters"]}
        assert chips["All"] == (3, True)
        assert chips["Changes requested"] == (1, False)
        assert chips["Upcoming"] == (1, False)
        assert chips["Cancelled"] == (1, False)
        assert chips["Archived"] == (0, False)
        html = resp.content.decode()
        assert "pl-lifecycle-badge--changes_requested" in html
        assert "pl-lifecycle-badge--cancelled" in html

    def it_filters_by_a_lifecycle_facet(admin_user, client, db):
        _upcoming_published(title="Live One")
        finished = ClassOfferingFactory(title="Finished One", status=Status.PUBLISHED)
        start = timezone.now() - timedelta(days=3)
        ClassSessionFactory(class_offering=finished, starts_at=start, ends_at=start + timedelta(hours=1))
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_classes") + "?status=completed")
        assert b"Finished One" in resp.content
        assert b"Live One" not in resp.content
        assert resp.context["selected_status"] == "completed"

    def it_falls_back_to_all_for_an_unknown_facet(admin_user, client, db):
        ClassOfferingFactory(title="Any Class")
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_classes") + "?status=published")
        assert resp.context["selected_status"] == ""
        assert b"Any Class" in resp.content

    def it_shows_the_empty_facet_state(admin_user, client, db):
        ClassOfferingFactory(title="Only Draft")
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_classes") + "?status=cancelled")
        assert b"No classes here yet." in resp.content

    def it_sorts_by_lifecycle_order(admin_user, client, db):
        ClassOfferingFactory(title="Zed Draft", status=Status.DRAFT)
        ClassOfferingFactory(title="Alpha Archived", status=Status.ARCHIVED)
        client.force_login(admin_user)
        resp = client.get(reverse("classes:admin_classes") + "?sort=lifecycle_order&dir=asc")
        titles = [c.title for c in resp.context["page"]]
        assert titles == ["Zed Draft", "Alpha Archived"]


def describe_admin_classes_query_count():
    def it_holds_the_query_count_constant_from_two_to_ten_bounced_rows(admin_user, client, db):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        client.force_login(admin_user)
        url = reverse("classes:admin_classes")
        _bounced_admin_rows(2)
        client.get(url)  # warm-up: first-request work (session, tour state) must not skew the count
        with CaptureQueriesContext(connection) as two:
            resp = client.get(url)
        assert resp.status_code == 200 and b"Note 1" in resp.content
        _bounced_admin_rows(8, start=2)
        with CaptureQueriesContext(connection) as ten:
            resp = client.get(url)
        assert b"Note 9" in resp.content
        assert len(ten) == len(two)


def describe_permission_edges():
    def it_forbids_a_plain_member_from_cancel_restore_and_remind(member_user, client, db):
        offering = _upcoming_published()
        client.force_login(member_user)
        for name in ("classes:admin_class_cancel", "classes:admin_class_restore", "classes:admin_class_remind_lead"):
            resp = client.post(reverse(name, kwargs={"pk": offering.pk}), {"reason": "x"})
            assert resp.status_code == 403, name
        offering.refresh_from_db()
        assert offering.status == Status.PUBLISHED

    def it_forbids_a_cms_administrator_from_restore_and_cancel(cms_admin_user, client, db):
        archived = ClassOfferingFactory(status=Status.ARCHIVED)
        live = _upcoming_published()
        client.force_login(cms_admin_user)
        assert client.post(reverse("classes:admin_class_restore", kwargs={"pk": archived.pk})).status_code == 403
        assert (
            client.post(reverse("classes:admin_class_cancel", kwargs={"pk": live.pk}), {"reason": "x"}).status_code
            == 403
        )
        archived.refresh_from_db()
        assert archived.status == Status.ARCHIVED
