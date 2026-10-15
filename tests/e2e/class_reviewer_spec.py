"""End-to-end: Class Reviewer and the instructor's own class actions (#816).

A guild lead flips a staff member's Class Reviewer switch on the Staff tab and it saves
over HTMX with no page reload; an instructor whose class waits on the guild lead sees
Remind lead and Duplicate on their class page, and Remind lead names who it reached.
Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from pathlib import Path

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassApproval, ClassInstructor, ClassOffering
from membership.models import GuildStaffMembership, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory, MembershipPlanFactory

SHOTS = Path(__file__).resolve().parents[2] / "mockups" / "screenshots"


def _member_for(email: str) -> Member:
    member = Member.objects.get(user__username=email)
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    return member


def describe_class_reviewer():
    def it_lets_the_lead_switch_a_staff_member_to_class_reviewer(live_server, page, login_via_code):
        MembershipPlanFactory()
        login_via_code("jay-lead@example.com")
        lead = _member_for("jay-lead@example.com")
        lead.preferred_name = "Jay Holtman"
        lead.save(update_fields=["preferred_name"])
        guild = GuildFactory(name="Ceramics Guild", guild_lead=lead)
        amber = MemberFactory(full_legal_name="Amber Capwell")
        GuildStaffMembershipFactory(guild=guild, member=amber, role="", custom_title="Studio Technician")
        sam = MemberFactory(full_legal_name="Sam McKee")
        GuildStaffMembershipFactory(guild=guild, member=sam, role=GuildStaffMembership.Role.ORIENTER)

        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{live_server.url}{reverse('hub_guild_edit', args=[guild.pk])}?tab=staff")
        switch = page.get_by_label("Class Reviewer: Amber Capwell")
        expect(switch).not_to_be_checked()

        with page.expect_response(lambda r: "class-reviewer" in r.url and r.request.method == "POST") as posted:
            page.locator(f'[data-class-reviewer-row="{amber.pk}"] .pl-toggle__slider').click()
        assert posted.value.ok
        expect(switch).to_be_checked()
        assert GuildStaffMembership.objects.get(guild=guild, member=amber).can_review_classes is True
        assert GuildStaffMembership.objects.get(guild=guild, member=sam).can_review_classes is False

        page.locator(f'[data-class-reviewer-row="{amber.pk}"]').scroll_into_view_if_needed()
        page.screenshot(path=str(SHOTS / "816-staff-class-reviewer.png"), full_page=False)

    def it_gives_the_instructor_remind_lead_and_duplicate_on_a_pending_class(live_server, page, login_via_code):
        MembershipPlanFactory()
        login_via_code("erin-teach@example.com")
        erin = InstructorFactory(
            user=get_user_model().objects.get(username="erin-teach@example.com"),
            full_legal_name="Erin Horgan Corrales",
            instructor_slug="erin",
        )
        lead = Member.objects.get(user=UserFactory(username="jay@example.com", email="jay@example.com"))
        lead.full_legal_name = "Jay Holtman"
        lead.status = Member.Status.ACTIVE
        lead.save(update_fields=["full_legal_name", "status"])
        guild = GuildFactory(name="Ceramics Guild", guild_lead=lead)
        offering = ClassOfferingFactory(
            title="Make Two Mugs with Erin",
            instructor=erin,
            category=CategoryFactory(guild=guild),
            status=ClassOffering.Status.PENDING,
        )
        ClassInstructor.objects.get_or_create(class_offering=offering, member=erin)
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)

        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{live_server.url}{reverse('classes:teach_class_detail', kwargs={'pk': offering.pk})}")
        expect(page.get_by_role("button", name="Duplicate", exact=True)).to_be_visible()
        remind = page.get_by_role("button", name="Remind lead", exact=True)
        expect(remind).to_be_visible()
        page.set_viewport_size({"width": 1280, "height": 1400})
        page.screenshot(path=str(SHOTS / "816-instructor-class-actions.png"), full_page=False)

        remind.click()
        expect(page.locator("body")).to_contain_text("Reminder sent to Jay Holtman")
