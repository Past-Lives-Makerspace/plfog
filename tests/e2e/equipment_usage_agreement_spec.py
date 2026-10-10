"""End-to-end: a member agrees to the CNC Machine's usage agreement, then Book a Time shows (#734).

The member opens the equipment page, sees "Agree to the usage agreement" where the reserve
form would be, opens the modal, scrolls the agreement text, checks the box and clicks
I agree, and lands back on the page with the reserve form showing. ``CAPTURE_734_DIR=<dir>``
also saves dark and light pictures of the prompt, the open modal and the manager's Details
tab fields there. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import time, timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from membership.models import EquipmentAgreementAcceptance, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentStaffMembershipFactory,
    MembershipPlanFactory,
)

MEMBER_EMAIL = "equipment-agreement-member@example.com"
MANAGER_EMAIL = "equipment-agreement-manager@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_734_DIR")
LINK = "https://docs.google.com/document/d/1JCfSw5Cesuzlgu5XitCaBK6BuskPs_73uuktsEIU4fE/edit?usp=sharing"
RULES = [
    "Complete the CNC orientation before your first reservation.",
    "Wear eye and ear protection whenever the spindle is running.",
    "Never leave the machine running unattended, even for a minute.",
    "Clamp every workpiece. Double check the clamps clear the toolpath.",
    "Run a dry pass above the material before every new job.",
    "Use only the bits in the CNC drawer. Report a broken bit on Discord.",
    "Vacuum the bed and the enclosure when you finish.",
    "Log every job in the binder by the machine.",
    "Stop the job with the red button if anything sounds wrong.",
    "Reservations are for one person. Ask a manager before bringing a guest.",
    "Keep food and drinks away from the machine and the control computer.",
    "Save your files to the shop drive, never to the control computer's desktop.",
    "Return the machine to its home position before you leave.",
    "Cancel your reservation if your plans change, so someone else can use the time.",
]


def _member(email: str, name: str) -> Member:
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.preferred_name = name
    member.full_legal_name = f"{name} Tester"
    member.save(update_fields=["status", "preferred_name", "full_legal_name"])
    return member


def _capture(page, name: str, target=None) -> None:
    """Save a dark and a light picture when CAPTURE_734_DIR is set: the whole page, or ``target``."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for theme in ("dark", "light"):
        page.evaluate(
            "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light') : document.documentElement.removeAttribute('data-theme')",
            theme,
        )
        page.wait_for_timeout(600)
        path = str(Path(CAPTURE_DIR) / f"734-{name}-{theme}.png")
        if target is None:
            page.screenshot(path=path, full_page=True)
        else:
            target.screenshot(path=path)
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")


def describe_equipment_usage_agreement():
    def it_lets_a_member_agree_and_then_reserve(live_server, page, login_via_code):
        MembershipPlanFactory()  # so the user signal provisions the member
        member = _member(MEMBER_EMAIL, "Robin")
        manager = _member(MANAGER_EMAIL, "Dana")
        cnc = EquipmentFactory(name="CNC Machine", usage_agreement_url=LINK, usage_agreement_text="\n\n".join(RULES))
        EquipmentStaffMembershipFactory(equipment=cnc, member=manager)
        # Open every day but today, so the first day the strip selects always has whole days of times.
        today = timezone.localdate()
        for offset in range(1, 7):
            EquipmentHoursFactory(
                equipment=cnc,
                weekday=(today + timedelta(days=offset)).weekday(),
                start_time=time(9, 0),
                end_time=time(17, 0),
            )
        detail_url = f"{live_server.url}{reverse('hub_equipment_detail', args=[cnc.slug])}"
        page.set_viewport_size({"width": 1100, "height": 900})

        login_via_code(MEMBER_EMAIL)
        page.goto(detail_url)
        prompt = page.locator("[data-equip-agree-prompt]")
        prompt.wait_for(state="visible")
        assert page.locator("#equip-reserve-form").count() == 0
        _capture(page, "page")
        _capture(page, "prompt", target=prompt)

        prompt.get_by_role("button", name="Agree to the usage agreement").click()
        modal = page.locator("[data-equip-agreement-form]")
        modal.wait_for(state="visible")
        link = modal.locator("[data-equip-agreement-link]")
        assert link.get_attribute("href") == LINK
        assert link.get_attribute("target") == "_blank"
        text_box = modal.locator("[data-equip-agreement-text]")
        assert text_box.evaluate("(el) => el.scrollHeight > el.clientHeight"), "the agreement text should scroll"
        submit = modal.locator("[data-equip-agree-submit]")
        assert submit.is_disabled()
        _capture(page, "modal", target=page.locator(".pl-modal:has([data-equip-agreement-form])"))
        text_box.evaluate("(el) => { el.scrollTop = el.scrollHeight; }")

        modal.locator(".pl-toggle").click()
        assert submit.is_enabled()
        with page.expect_navigation():
            submit.click()

        page.locator("#equip-reserve-form").wait_for(state="visible")
        assert page.locator("[data-equip-agree-prompt]").count() == 0
        acceptance = EquipmentAgreementAcceptance.objects.get(member=member, equipment=cnc)
        assert acceptance.fingerprint == cnc.usage_agreement_fingerprint
        assert acceptance.user_agent

        # The manager's Details tab: the two fields and the note that one member agreed.
        page.context.clear_cookies()
        login_via_code(MANAGER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_equipment_manage', args=[cnc.slug])}?tab=details")
        note = page.locator("[data-agreement-member-count]")
        note.wait_for(state="visible")
        assert "1 member agreed to the current version." in note.inner_text()
        assert page.locator("#id_usage_agreement_url").input_value() == LINK
        fields = page.locator("[data-equip-agreement-fields]")
        fields.scroll_into_view_if_needed()
        _capture(page, "details-fields", target=fields)
