"""End-to-end: the Site Settings → Automations tab in a real browser.

Three things only a browser can prove about this panel:

1. the money job's "Run now" opens its teleported confirm modal (the modal's form
   lives OUTSIDE the big settings form — nesting would break both) and Cancel closes it,
2. a safe job's "Run now" submits the surrounding settings form and records a real run, and
3. an unsaved pause-toggle edit is persisted by that same Run-now POST
   (``_persist_automation_toggles`` runs before the job), so no toggle work is lost.

Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from tests.membership.factories import MembershipPlanFactory

ADMIN_EMAIL = "automations-admin@example.com"


def describe_site_settings_automations():
    def it_guards_the_money_job_and_runs_a_safe_job_persisting_toggles(live_server, page, login_via_code):
        # A plan must exist so the login signal auto-creates the member.
        MembershipPlanFactory()

        # Sign in through the real code flow, then elevate to admin.
        login_via_code(ADMIN_EMAIL)
        user = get_user_model().objects.get(username=ADMIN_EMAIL)
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])

        # Open Site Settings on the Automations tab: every registry job has a card.
        page.goto(f"{live_server.url}{reverse('hub_admin_site_settings')}?tab=automations")
        from core.scheduled_jobs import SCHEDULED_JOBS

        for job in SCHEDULED_JOBS:
            expect(page.locator(".pl-automation-card", has_text=job.name).first).to_be_visible()

        # 1. The money job routes through its confirm modal; Cancel closes it un-run.
        billing_card = page.locator(".pl-automation-card", has_text="Charge member tabs")
        expect(billing_card.locator(".pl-automation-badge", has_text="charges cards")).to_be_visible()
        billing_card.get_by_role("button", name="Run now").click()
        modal = page.locator(".pl-modal", has_text="Run tab billing now?")
        expect(modal).to_be_visible()
        modal.get_by_role("button", name="Cancel", exact=True).click()
        expect(modal).to_be_hidden()

        # 2+3. Pause one job (unsaved edit), then Run-now a safe DB-only job: the POST
        # must persist the toggle AND run the job.
        voting_card = page.locator(".pl-automation-card", has_text="Guild voting reminders")
        voting_card.locator(".pl-toggle__slider").click()
        publish_card = page.locator(".pl-automation-card", has_text="Publish scheduled events")
        publish_card.get_by_role("button", name="Run now").click()

        # The run redirects back to the Automations tab with a recorded, successful run.
        expect(page).to_have_url(re.compile(r"tab=automations"))
        publish_card = page.locator(".pl-automation-card", has_text="Publish scheduled events")
        expect(publish_card.locator(".hub-pill--ok")).to_be_visible()

        # The pause edit survived the Run-now POST — on screen and in the database.
        voting_card = page.locator(".pl-automation-card", has_text="Guild voting reminders")
        expect(voting_card).to_contain_text("Paused — won't run on schedule.")
        from core.models import ScheduledJobState, ScheduledTaskRun

        assert ScheduledJobState.objects.is_enabled("send_voting_reminders") is False
        run = ScheduledTaskRun.objects.filter(task_key="publish_due_events").latest("started_at")
        assert run.succeeded
        assert not ScheduledTaskRun.objects.filter(task_key="bill_tabs").exists()
