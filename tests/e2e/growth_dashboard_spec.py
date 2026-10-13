"""The admin Growth page in a real browser (#795): the metric picker and a chart's PNG download.

The picker submits its GET form as soon as a metric is chosen (through hx-boost), and a chart
is drawn at a desktop and a phone size, so Download PNG must save the copy that is showing.
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import Member
from tests.membership.factories import MemberFactory


def _sign_in_admin(login_via_code, email: str = "growth-admin@example.com") -> None:
    login_via_code(email)
    user = get_user_model().objects.get(username=email)
    member = Member.objects.filter(user=user).first() or MemberFactory(user=user)
    member.fog_role = Member.FogRole.ADMIN
    member.welcome_dismissed_at = timezone.now()
    member.save(update_fields=["fog_role", "welcome_dismissed_at"])
    member.sync_user_permissions()


def it_switches_the_trend_when_a_metric_is_picked(page, live_server, login_via_code):
    _sign_in_admin(login_via_code)
    page.goto(f"{live_server.url}{reverse('hub_admin_growth')}?mode=month")
    page.select_option("#growth-metric", "new_members")
    page.wait_for_url(re.compile(r"metric=new_members"))
    expect(page.locator("[data-main-chart] .pl-growth__svg--wide svg")).to_have_attribute(
        "aria-label", re.compile(r"^New Members per Month, Last 90 Days")
    )
    expect(page.locator('[data-mode="month"]')).to_have_attribute("aria-current", "true")


def it_downloads_the_chart_that_is_showing_as_a_png(page, live_server, login_via_code):
    _sign_in_admin(login_via_code, "growth-download@example.com")
    page.set_viewport_size({"width": 390, "height": 900})
    page.goto(f"{live_server.url}{reverse('hub_admin_growth')}")
    expect(page.locator("[data-cms-chart] .pl-growth__svg--narrow svg")).to_be_visible()
    with page.expect_download() as info:
        page.locator('[data-chart-download="growth-cms-activity.png"]').click()
    download = info.value
    assert download.suggested_filename == "growth-cms-activity.png"
    with open(download.path(), "rb") as image:
        header = image.read(24)
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(header[16:20], "big")
    assert width == 316 * 3  # the phone copy, drawn at three times its size
