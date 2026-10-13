"""BDD specs for InstructorInquiryAddForm, the Add Inquiry modal on Instructor Inquiries (#794).

An admin files an ask to teach by hand: the member (a datalist picker of the members who
may ask), the day they reached out, and the application's answers, of which only the
working title and what they would teach are required. The application's rules between
answers still hold, websites take only http and https, and ``save`` files the ask
through ``Member.apply_to_teach`` as the admin.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from classes.forms import InstructorInquiryAddForm, TeachingApplicationForm
from core.models import SiteActivity
from membership.models import Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def robin() -> Member:
    return MemberFactory(full_legal_name="Robin Reachout", preferred_name="")


def _form(**overrides: object) -> InstructorInquiryAddForm:
    data: dict[str, object] = {
        "member": "Robin Reachout",
        "reached_out_on": timezone.localdate().isoformat(),
        "class_title": "Lost Wax Casting",
        "note": "Casting silver rings.",
    }
    data.update(overrides)
    return InstructorInquiryAddForm(data)


def describe_InstructorInquiryAddForm():
    def describe_required_answers():
        def it_takes_only_the_member_the_day_the_title_and_the_note(robin):
            form = _form()
            assert form.is_valid(), form.errors
            assert form.cleaned_data["member"] == robin

        def it_requires_exactly_those_four():
            required = {name for name, field in InstructorInquiryAddForm().fields.items() if field.required}
            assert required == {"member", "reached_out_on", "class_title", "note"}

        def it_says_what_is_missing_in_the_admins_words(robin):
            form = _form(class_title="", note="  ")
            assert not form.is_valid()
            assert form.errors["class_title"] == ["Add a working title for the class."]
            assert form.errors["note"] == ["Say what they want to teach."]

        def it_leaves_the_member_application_form_as_it_was():
            form = TeachingApplicationForm()
            assert form.fields["experience"].required
            assert form.fields["contact_method"].required
            assert form.fields["note"].error_messages["required"] == "Tell us a little about what you want to host."

    def describe_the_member_picker():
        def it_offers_the_members_who_may_ask_and_nobody_else(robin):
            now = timezone.now()
            declined = MemberFactory(
                full_legal_name="Dana Declined",
                preferred_name="",
                teaching_applied_at=now,
                teaching_decided_at=now,
                teaching_decline_reason="Not yet.",
            )
            MemberFactory(full_legal_name="Ivy Instructor", preferred_name="", instructor_oriented_at=now)
            MemberFactory(full_legal_name="Pat Pending", preferred_name="", teaching_applied_at=now)
            MemberFactory(full_legal_name="Fran Former", preferred_name="", status=Member.Status.FORMER)
            MemberFactory(full_legal_name="Hidden Helper", preferred_name="", hide_from_directory=True)
            assert InstructorInquiryAddForm().member_options == [declined.display_name, robin.display_name]

        def it_tells_two_members_with_one_name_apart_by_number():
            first = MemberFactory(full_legal_name="Sam Lee", preferred_name="")
            second = MemberFactory(full_legal_name="Sam Lee", preferred_name="")
            assert InstructorInquiryAddForm().member_options == [f"Sam Lee (#{first.pk})", f"Sam Lee (#{second.pk})"]
            form = _form(member=f"  sam lee (#{second.pk}) ")
            assert form.is_valid(), form.errors
            assert form.cleaned_data["member"] == second

        def it_refuses_a_name_not_on_the_list(robin):
            form = _form(member="Nobody Known")
            assert not form.is_valid()
            assert form.errors["member"] == ["Pick a member from the list."]

        def it_refuses_a_member_who_can_already_teach():
            MemberFactory(full_legal_name="Ivy Instructor", preferred_name="", instructor_oriented_at=timezone.now())
            form = _form(member="Ivy Instructor")
            assert not form.is_valid()
            assert form.errors["member"] == ["Ivy Instructor can already teach."]

        def it_refuses_a_member_with_an_ask_waiting():
            MemberFactory(full_legal_name="Pat Pending", preferred_name="", teaching_applied_at=timezone.now())
            form = _form(member="Pat Pending")
            assert not form.is_valid()
            assert form.errors["member"] == ["Pat Pending already has an inquiry waiting on an admin."]

    def describe_the_day_they_reached_out():
        def it_starts_on_today():
            assert InstructorInquiryAddForm().fields["reached_out_on"].initial == timezone.localdate()

        def it_refuses_a_day_in_the_future(robin):
            form = _form(reached_out_on=(timezone.localdate() + timedelta(days=1)).isoformat())
            assert not form.is_valid()
            assert form.errors["reached_out_on"] == ["Pick today or a day in the past."]

        def it_stamps_today_as_now(robin):
            form = _form()
            assert form.is_valid()
            before = timezone.now()
            assert before <= form.applied_at() <= timezone.now()

        def it_stamps_an_earlier_day_as_its_start_in_portland(robin):
            form = _form(reached_out_on="2026-09-14")
            assert form.is_valid()
            stamp = form.applied_at()
            assert stamp == timezone.make_aware(datetime.combine(date(2026, 9, 14), time.min))
            assert timezone.localdate(stamp) == date(2026, 9, 14)

    def describe_the_rules_between_answers():
        def it_needs_the_address_on_a_website_yes(robin):
            form = _form(has_website="yes", website="")
            assert not form.is_valid()
            assert form.errors["website"] == ["Add your website's address, or pick No."]

        @pytest.mark.parametrize("website", ["javascript:alert(1)", "ftp://files.example.com"])
        def it_refuses_a_website_that_is_not_http_or_https(robin, website):
            form = _form(has_website="yes", website=website)
            assert not form.is_valid()
            assert form.errors["website"] == ["That does not look like a web address. Check it and try again."]

        def it_drops_where_they_taught_for_a_first_timer_and_never_requires_it(robin):
            assert _form(experience="experienced", taught_where="").is_valid()
            form = _form(experience="first_time", taught_where="The art center.")
            assert form.is_valid()
            assert form.cleaned_data["taught_where"] == ""

        def it_takes_no_contact_answers_at_all(robin):
            form = _form(contact_method="", contact_detail="")
            assert form.is_valid(), form.errors

        def it_needs_where_to_reach_them_once_a_method_is_picked(robin):
            form = _form(contact_method="text", contact_detail="")
            assert not form.is_valid()
            assert form.errors["contact_detail"] == [
                "Add where to reach them, or set the way to reach them back to Pick one."
            ]

        def it_needs_a_method_for_a_detail(robin):
            form = _form(contact_method="", contact_detail="503 555 0100")
            assert not form.is_valid()
            assert form.errors["contact_method"] == ["Pick how they want to be reached, or clear where to reach them."]

        def it_still_checks_the_detail_against_the_method(robin):
            form = _form(contact_method="email", contact_detail="not an address")
            assert not form.is_valid()
            assert form.errors["contact_detail"] == [
                "That does not look like an email address. Check it and try again."
            ]

    def describe_save():
        def it_files_the_ask_as_the_admin_on_the_day_picked(robin):
            admin = get_user_model().objects.create_user(username="saver", email="saver@example.com", password="pw")
            form = _form(reached_out_on="2026-09-14", has_website="yes", website="https://robin.example")
            assert form.is_valid(), form.errors
            assert form.save(added_by=admin) == robin
            robin.refresh_from_db()
            assert timezone.localdate(robin.teaching_applied_at) == date(2026, 9, 14)
            assert robin.teaching_website == "https://robin.example"
            assert robin.teaching_application_state == Member.TeachingApplicationState.PENDING
            assert SiteActivity.objects.get(kind=SiteActivity.Kind.TEACHING_INQUIRY_ADDED).actor == admin
