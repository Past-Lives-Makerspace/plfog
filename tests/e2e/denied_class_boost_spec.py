"""End-to-end: a boosted click into a class this viewer may not open (criterion 15, issue #399).

``hub/base.html`` boosts the whole body, and htmx 2 does not swap a 4xx — it fires
``htmx:responseError`` and leaves the page exactly where it was. So before this ticket a
boosted click into a denied class was a *dead click*: no branded 404, no "Viewing as"
switcher, no feedback of any kind. An admin who had previewed a lower role could strand
themselves with no way back out, which is the safety ruling 9 rests on.

The Django test client is not htmx, so every spec asserting the 404 passes whether the
rule in ``static/js/hub_boot.js`` exists or not. Only a real browser sees this, which is
why the criterion names Playwright specifically.

The scenario is the real one rather than a contrived link. An admin opens the Classes
list, whose rows link straight at the per-class screen. The View As role then changes
underneath them — driven through the very endpoint the switcher's ``pick()`` posts to,
minus its ``window.location.reload()``, so the already-rendered links go stale exactly as
they do when a role is switched in another tab or a page is left open. The next click is
then a boosted request into a class the previewed role may not open.

Needs a browser. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re
from typing import cast

from django.urls import reverse
from playwright.sync_api import expect

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

EMAIL = "denied-boost-admin@example.com"
SWITCHER = ".pl-view-switcher__label"
NOT_FOUND = "We couldn't find that page."


def _seed_admin_and_someone_elses_class() -> ClassOffering:
    """An admin who teaches nothing, and a published class belonging to another instructor."""
    MembershipPlanFactory()
    user = UserFactory(username=EMAIL, email=EMAIL)
    member = cast(Member, user.member)
    member.full_legal_name = "Denied Boost Admin"
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["full_legal_name", "fog_role"])
    member.sync_user_permissions()

    instructor = cast(Member, InstructorFactory(full_legal_name="Someone Else", instructor_slug="someone-else"))
    return cast(
        ClassOffering,
        ClassOfferingFactory(
            slug="denied-boost-class",
            title="A Class The Preview Cannot Open",
            status=ClassOffering.Status.PUBLISHED,
            instructor=instructor,
        ),
    )


def _preview_as(page, live_server, role: str) -> None:
    """Switch the session's View As role without reloading the page.

    Posts to ``hub_view_as_set`` exactly as ``viewAsDropdown().pick()`` does, and stops
    where that function would call ``window.location.reload()``. That is what leaves the
    rendered links on screen while the session behind them has already changed.
    """
    endpoint = f"{live_server.url}{reverse('hub_view_as_set')}"
    status = page.evaluate(
        """async ({ endpoint, role }) => {
            const token = document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.split('=')[1];
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token || '' },
                body: JSON.stringify({ role }),
            });
            return response.status;
        }""",
        {"endpoint": endpoint, "role": role},
    )
    assert status == 200, f"view-as switch to {role!r} was refused with {status}"


def describe_a_boosted_click_into_a_denied_class():
    def it_renders_the_404_with_the_view_as_switcher_still_reachable(live_server, page, login_via_code):
        offering = _seed_admin_and_someone_elses_class()
        login_via_code(EMAIL)

        # The admin's Classes list links every row straight at the per-class screen.
        page.goto(f"{live_server.url}{reverse('classes:admin_classes')}")
        target = reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        link = page.locator(f'a[href="{target}"]').first
        expect(link).to_be_visible()

        # The role changes underneath the rendered page, so the link above is now stale.
        _preview_as(page, live_server, "member")

        link.click()

        # Not a dead click: the browser actually went there and the branded 404 rendered.
        page.wait_for_url(re.compile(re.escape(target)))
        expect(page.get_by_text(NOT_FOUND)).to_be_visible()

        # And the way back out is on the page, which is the whole point of ruling 9.
        expect(page.locator(SWITCHER)).to_be_visible()

    def it_still_opens_the_class_when_the_viewer_may(live_server, page, login_via_code):
        # The negative control. Without it the spec above would pass on a rule that broke
        # every boosted navigation, denied or not.
        offering = _seed_admin_and_someone_elses_class()
        login_via_code(EMAIL)

        page.goto(f"{live_server.url}{reverse('classes:admin_classes')}")
        target = reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        page.locator(f'a[href="{target}"]').first.click()

        page.wait_for_url(re.compile(re.escape(target)))
        expect(page.get_by_text(NOT_FOUND)).to_have_count(0)
        expect(page.get_by_text(offering.title).first).to_be_visible()
