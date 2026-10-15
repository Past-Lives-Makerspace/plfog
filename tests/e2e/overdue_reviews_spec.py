"""End-to-end: an overdue class review shows red on the admin Classes queue and on Home (#817).

Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, InstructorFactory, UserFactory
from classes.models import ClassApproval, ClassOffering
from membership.models import Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

SHOTS = Path(__file__).resolve().parents[2] / "mockups" / "screenshots"


def describe_overdue_reviews():
    def it_marks_an_overdue_review_on_the_admin_queue_and_home(live_server, page, login_via_code):
        MembershipPlanFactory()
        jay = Member.objects.get(user=UserFactory(username="jay@example.com", email="jay@example.com"))
        jay.full_legal_name = "Jay Holtman"
        jay.save(update_fields=["full_legal_name"])
        guild = GuildFactory(name="Ceramics Guild", guild_lead=jay)
        offering = ClassOfferingFactory(
            title="Make Two Mugs with Erin",
            status=ClassOffering.Status.PENDING,
            instructor=InstructorFactory(full_legal_name="Erin Horgan Corrales"),
            category=CategoryFactory(guild=guild),
        )
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=23, hours=1))
        gate = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        ClassApproval.objects.filter(pk=gate.pk).update(created_at=timezone.now() - timedelta(days=15, hours=1))

        login_via_code("overdue-admin@example.com")
        user = get_user_model().objects.get(username="overdue-admin@example.com")
        member = Member.objects.get(user=user)
        member.fog_role = Member.FogRole.ADMIN
        member.status = Member.Status.ACTIVE
        member.save(update_fields=["fog_role", "status"])
        member.sync_user_permissions()

        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{live_server.url}{reverse('classes:admin_overview')}")
        marker = page.locator(".pl-overdue-marker", has_text="Waiting 15 days · starts in 23 days")
        expect(marker).to_be_visible()
        page.screenshot(path=str(SHOTS / "817-admin-queue-overdue.png"), full_page=False)

        page.goto(f"{live_server.url}{reverse('hub_home')}")
        no_thanks = page.get_by_role("button", name="No thanks")
        if no_thanks.is_visible():
            no_thanks.click()
        card = page.locator('[data-block="overdue-reviews"]')
        expect(card).to_contain_text("Make Two Mugs with Erin")
        expect(card).to_contain_text("Waiting on Jay Holtman")
        card.scroll_into_view_if_needed()
        card.screenshot(path=str(SHOTS / "817-home-overdue-card.png"))
