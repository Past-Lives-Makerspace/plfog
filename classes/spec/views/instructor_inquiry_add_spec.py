"""BDD specs for Add Inquiry on Instructor Inquiries (#794).

The + Add Inquiry button and its modal show to everyone who can open the page; the POST
is refused to anyone else. A save files the ask exactly as an application on the day
picked (Pending, in the table and the CSV), logs a site activity row naming the admin
and sends no application notice. A member who can already teach or has an ask waiting
is a field error, and any failed save renders the page with the modal open and every
answer kept.
"""

from __future__ import annotations

import csv
import io
from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import Notification, SiteActivity
from membership.models import AdminCapability, Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

PAGE = reverse("classes:admin_instructor_inquiries")
ADD = reverse("classes:admin_instructor_inquiry_add")
EXPORT = reverse("classes:admin_instructor_inquiries_export")
REOPEN = "$dispatch('open-modal', 'inquiry-add')\"></div>"


@pytest.fixture
def robin() -> Member:
    return MemberFactory(full_legal_name="Robin Reachout", preferred_name="")


def _post(**overrides: str) -> dict[str, str]:
    data = {
        "member": "Robin Reachout",
        "reached_out_on": "2026-09-14",
        "class_title": "Lost Wax Casting",
        "note": "Casting silver rings.",
        "experience": "experienced",
        "has_website": "yes",
        "website": "https://robin.example",
        "contact_method": "text",
        "contact_detail": "503 555 0100",
    }
    data.update(overrides)
    return {f"inquiry-{name}": value for name, value in data.items()}


def describe_access():
    def it_shows_the_button_and_the_modal_to_an_admin(admin_user, client: Client, robin):
        client.force_login(admin_user)
        content = client.get(PAGE).content.decode()
        assert "data-inquiry-add>+ Add Inquiry</button>" in content
        assert f'action="{ADD}"' in content
        assert 'id="inquiry-add-title">Add an Inquiry</h2>' in content
        assert '<option value="Robin Reachout"></option>' in content
        assert f'value="{timezone.localdate().isoformat()}"' in content
        assert REOPEN not in content
        assert 'max="' + timezone.localdate().isoformat() + '"' in content
        for title in ("Class Idea", "Experience", "Logistics", "Their Work", "How to Reach Them", "Anything Else"):
            assert f'<h2 class="pl-teach-section__title">{title}</h2>' in content, title
        assert '<h2 class="pl-teach-section__title">Show Your Work</h2>' not in content
        assert '<p class="pl-teach-application__intro">Rough is fine' not in content

    def it_refuses_a_crafted_post_from_a_member_who_is_not_an_admin(client: Client, robin):
        user = get_user_model().objects.create_user(username="plain", email="plain@example.com", password="pw")
        client.force_login(user)
        assert client.post(ADD, _post()).status_code == 403
        robin.refresh_from_db()
        assert robin.teaching_applied_at is None

    def it_sends_a_visitor_who_is_not_signed_in_to_log_in(client: Client, robin):
        response = client.post(ADD, _post())
        assert response.status_code == 302
        assert "login" in response["Location"]
        robin.refresh_from_db()
        assert robin.teaching_applied_at is None

    def it_takes_only_a_post(admin_user, client: Client):
        client.force_login(admin_user)
        assert client.get(ADD).status_code == 405


def describe_saving():
    def it_files_the_ask_and_lists_it_as_pending_on_the_day_picked(admin_user, client: Client, robin):
        client.force_login(admin_user)
        response = client.post(ADD, _post(), follow=True)
        assert response.redirect_chain == [(PAGE, 302)]
        content = response.content.decode()
        assert "Added an inquiry for Robin Reachout." in content
        assert f'data-inquiry-row="{robin.pk}"' in content
        row = content[content.index(f'data-inquiry-row="{robin.pk}"') :]
        assert '<td class="pl-inquiries__date">Sep 14, 2026</td>' in row
        assert 'pl-inquiries__status--pending">Pending</span>' in row
        robin.refresh_from_db()
        assert timezone.localdate(robin.teaching_applied_at) == date(2026, 9, 14)
        assert robin.teaching_application_state == Member.TeachingApplicationState.PENDING

    def it_puts_the_row_in_the_csv(admin_user, client: Client, robin):
        client.force_login(admin_user)
        client.post(ADD, _post())
        body = b"".join(client.get(EXPORT).streaming_content).decode()
        rows = list(csv.reader(io.StringIO(body)))
        assert rows[1][:8] == [
            "2026-09-14",
            "Robin Reachout",
            "Casting silver rings.",
            "https://robin.example",
            "",
            "Experienced instructor",
            "503 555 0100 (text message)",
            "Pending",
        ]

    def it_logs_an_activity_row_naming_the_admin(admin_user, client: Client, robin):
        Member.objects.filter(user=admin_user).update(full_legal_name="Alex Admin", preferred_name="")
        client.force_login(admin_user)
        client.post(ADD, _post())
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.TEACHING_INQUIRY_ADDED)
        assert row.actor == admin_user
        assert row.who == "Alex Admin"
        assert row.target == robin

    def it_sends_no_application_notice(admin_user, client: Client, robin):
        approver = Member.objects.get(user=admin_user)
        AdminCapability.objects.create(member=approver, capability=AdminCapability.Capability.CLASS_APPROVER)
        client.force_login(admin_user)
        client.post(ADD, _post())
        assert not Notification.objects.filter(trigger="instructor_application_received").exists()
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.TEACHING_APPLIED).exists()


def describe_a_failed_save():
    def it_refuses_a_member_who_can_already_teach(admin_user, client: Client):
        ivy = MemberFactory(full_legal_name="Ivy Instructor", preferred_name="", instructor_oriented_at=timezone.now())
        client.force_login(admin_user)
        response = client.post(ADD, _post(member="Ivy Instructor"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Ivy Instructor can already teach." in content
        assert REOPEN in content
        ivy.refresh_from_db()
        assert ivy.teaching_applied_at is None

    def it_refuses_a_member_with_an_ask_waiting(admin_user, client: Client):
        stamp = timezone.now()
        pat = MemberFactory(
            full_legal_name="Pat Pending", preferred_name="", teaching_applied_at=stamp, teaching_class_title="Kept"
        )
        client.force_login(admin_user)
        content = client.post(ADD, _post(member="Pat Pending")).content.decode()
        assert "Pat Pending already has an inquiry waiting on an admin." in content
        assert REOPEN in content
        pat.refresh_from_db()
        assert pat.teaching_applied_at == stamp
        assert pat.teaching_class_title == "Kept"

    def it_refuses_a_javascript_website_and_saves_nothing(admin_user, client: Client, robin):
        client.force_login(admin_user)
        content = client.post(ADD, _post(website="javascript:alert(1)")).content.decode()
        assert "That does not look like a web address. Check it and try again." in content
        assert 'href="javascript:' not in content
        robin.refresh_from_db()
        assert robin.teaching_applied_at is None
        assert robin.teaching_website == ""
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.TEACHING_INQUIRY_ADDED).exists()

    def it_keeps_every_answer_when_it_reopens(admin_user, client: Client, robin):
        client.force_login(admin_user)
        response = client.post(ADD, _post(class_title="", shop_area="Jewelry bench"))
        content = response.content.decode()
        assert "Add a working title for the class." in content
        assert "Some answers need another look." in content
        assert REOPEN in content
        for kept in (
            'value="Robin Reachout"',
            'value="2026-09-14"',
            "Casting silver rings.</textarea>",
            'value="Jewelry bench"',
            'value="https://robin.example"',
            'value="503 555 0100"',
        ):
            assert kept in content, kept

    def it_lands_on_the_member_field_when_the_member_changed_since_the_check(
        admin_user, client: Client, robin, monkeypatch
    ):
        def refuse(self: Member, *args: object, **kwargs: object) -> None:
            raise ValueError("already waiting")

        monkeypatch.setattr(Member, "apply_to_teach", refuse)
        client.force_login(admin_user)
        content = client.post(ADD, _post()).content.decode()
        assert "Their record changed while you were saving. Check where they stand and try again." in content
        assert REOPEN in content
