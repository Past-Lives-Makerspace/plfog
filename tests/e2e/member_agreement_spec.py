"""Members can read the agreement in another tab before giving consent."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.urls import reverse
from playwright.sync_api import Page, expect

from core.models import SiteConfiguration
from membership.models import Member, MemberAgreementAcceptance
from tests.membership.factories import MembershipPlanFactory, UserFactory, WikiPageFactory

pytestmark = pytest.mark.django_db(transaction=True)


def describe_reading_the_member_agreement() -> None:
    @pytest.mark.parametrize("theme", ["dark", "light"])
    def it_opens_the_document_and_accepts_after_returning(
        page: Page,
        live_server: Any,
        login_via_code: Callable[[str], None],
        tmp_path: Path,
        theme: str,
    ) -> None:
        MembershipPlanFactory()
        email = f"agreement-{theme}@example.com"
        user = UserFactory(username=email, email=email)
        member = user.member
        member.status = Member.Status.ACTIVE
        member.save(update_fields=["status"])
        document = WikiPageFactory(
            title="Member Agreement",
            body="Keep shared work areas clear after every project.",
            created_by=member,
        )
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = f"{live_server.url}{document.get_absolute_url()}"
        config.save()
        login_via_code(email)

        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{live_server.url}{reverse('hub_member_agreement')}")
        page.evaluate("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
        link = page.get_by_role("link", name="Read the Member Agreement (opens in a new tab)")
        expect(link).to_be_visible()
        expect(page.get_by_role("button", name="Agree", exact=True)).to_be_disabled()
        page.screenshot(path=str(tmp_path / f"agreement-{theme}.png"))

        with page.expect_popup() as opened:
            link.click()
        reading_tab = opened.value
        expect(reading_tab.get_by_text(document.body, exact=True)).to_be_visible()
        assert reading_tab.url == config.member_agreement_url
        assert not MemberAgreementAcceptance.objects.filter(member=member).exists()
        reading_tab.close()

        page.locator(".pl-agreement-checkbox .pl-toggle").click()
        page.get_by_role("button", name="Agree", exact=True).click()
        page.wait_for_url(f"{live_server.url}{reverse('hub_home')}")
        assert MemberAgreementAcceptance.objects.get(member=member).agreement_url == config.member_agreement_url
