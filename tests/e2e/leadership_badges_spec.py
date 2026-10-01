"""End-to-end: admins make colored badges and give them to people on the Leadership Directory (#571).

Pytest's Django client never runs the editor's script, so this walks it in a real browser: add a
badge through its modal, rename and recolor it with the pill following as it is typed, have a
partial hex code refused and put back, give the badge on one tab and watch the same member's
toggle on another tab follow, see the pills on both public cards, then take it back and delete
the badge through its confirm, which names how many people hold it.

Waits are on what the page shows, never a fixed sleep: the save pill reads Saved with its
``data-saves`` count past the last one. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.urls import reverse

from membership.models import LeadershipBadge, LeadershipTab, Member
from tests.membership.factories import (
    LeadershipListingFactory,
    LeadershipRoleFactory,
    LeadershipTabFactory,
    MemberFactory,
    MembershipPlanFactory,
)

ADMIN_EMAIL = "leadership-badges-admin@example.com"
ALPINE_READY = "() => !!(document.querySelector('.pl-leadership-admin') || {})._x_dataStack"
SAVED_PAST = (
    "(n) => { const pill = document.querySelector('[data-save-pill]');"
    " return pill && Number(pill.dataset.saves) >= n && pill.textContent.trim() === 'Saved'; }"
)


def _seed() -> tuple[Member, LeadershipTab, LeadershipTab]:
    """An admin, and Morlock on two People tabs."""
    MembershipPlanFactory()  # so the user signal provisions the member this then promotes
    user = User.objects.create_user(username=ADMIN_EMAIL, email=ADMIN_EMAIL)
    admin = user.member
    admin.fog_role = Member.FogRole.ADMIN
    admin.status = Member.Status.ACTIVE
    admin.save(update_fields=["fog_role", "status"])
    admin.sync_user_permissions()
    LeadershipTab.objects.all().delete()
    leadership = LeadershipTabFactory(title="Leadership", sort_order=0)
    board = LeadershipTabFactory(title="Board", sort_order=1)
    morlock = MemberFactory(full_legal_name="Morlock Mender")
    for tab, title in ((leadership, "Guild Executor"), (board, "Board Advisor")):
        LeadershipRoleFactory(listing=LeadershipListingFactory(tab=tab, member=morlock), title=title)
    return morlock, leadership, board


def _saves(page) -> int:
    return int(page.locator("[data-save-pill]").get_attribute("data-saves") or 0)


def _wait_saved(page, at_least: int) -> None:
    page.wait_for_function(SAVED_PAST, arg=at_least)


def _open_editor(page, live_server) -> None:
    page.goto(f"{live_server.url}{reverse('hub_admin_leadership')}")
    page.wait_for_function(ALPINE_READY)


def _background(locator) -> str:
    return locator.evaluate("el => getComputedStyle(el).backgroundColor")


def _text_color(locator) -> str:
    return locator.evaluate("el => getComputedStyle(el).color")


def describe_leadership_badges():
    def it_makes_edits_gives_and_deletes_a_badge(live_server, page, login_via_code):
        morlock, leadership, board = _seed()
        login_via_code(ADMIN_EMAIL)
        _open_editor(page, live_server)

        # Add a badge through its modal.
        page.get_by_role("button", name="+ Add a badge").click()
        add_form = page.locator(f"form[action='{reverse('hub_admin_leadership_badge_add')}']")
        add_form.locator("#id_newbadge-label").fill("Elevator Trained")
        add_form.locator(".pl-color-picker__hex").fill("#FFE066")
        add_form.get_by_role("button", name="Add").click()
        page.locator("[data-badge-fields]").wait_for()
        page.wait_for_function(ALPINE_READY)
        badge = LeadershipBadge.objects.get()
        assert (badge.label, badge.color) == ("Elevator Trained", "#FFE066")
        fields = page.locator(f"[data-badge-fields='{badge.pk}']")
        preview = fields.locator("[data-badge-pill]")
        assert preview.inner_text() == "Elevator Trained"
        assert (_background(preview), _text_color(preview)) == ("rgb(255, 224, 102)", "rgb(0, 0, 0)")

        # Rename it: every pill of the badge, its toggles on each row too, follows the typing.
        before = _saves(page)
        label = fields.locator("[data-autosave='label']")
        label.fill("Elevator Certified")
        assert page.locator(f"[data-badge-pill='{badge.pk}']").all_inner_texts() == ["Elevator Certified"] * 3
        label.press("Tab")
        _wait_saved(page, before + 1)

        # Recolor it: the pill turns navy with white text, and the change saves.
        before = _saves(page)
        hex_input = fields.locator("[data-autosave='color']")
        hex_input.fill("#092E4C")
        assert (_background(preview), _text_color(preview)) == ("rgb(9, 46, 76)", "rgb(255, 255, 255)")
        hex_input.press("Tab")
        _wait_saved(page, before + 1)
        badge.refresh_from_db()
        assert (badge.label, badge.color) == ("Elevator Certified", "#092E4C")

        # A partial hex code is refused on blur: an error toast, and the saved color comes back.
        hex_input.fill("#09")
        hex_input.press("Tab")
        fields.locator(".pl-field-error").wait_for()
        page.locator(".plt-toast--error").first.wait_for()
        assert hex_input.input_value() == "#092E4C"
        assert _background(preview) == "rgb(9, 46, 76)"
        badge.refresh_from_db()
        assert badge.color == "#092E4C"

        # Give it on the Leadership tab: Morlock's toggle on the Board tab turns on with it.
        toggles = page.locator(f"[data-badge-toggle][data-badge-id='{badge.pk}'][data-member-id='{morlock.pk}']")
        assert toggles.count() == 2
        before = _saves(page)
        page.locator(f"#leadership-pane-{leadership.pk} .pl-badge-toggle").click()
        _wait_saved(page, before + 1)
        assert [toggles.nth(i).is_checked() for i in range(2)] == [True, True]
        assert list(badge.members.all()) == [morlock]
        confirm = page.locator(f"[data-badge-delete-message='{badge.pk}']")
        assert confirm.inner_text().startswith("1 person holds it.")

        # The public page shows the pill under the name plate on both of Morlock's cards.
        page.goto(f"{live_server.url}{reverse('hub_leadership_directory')}?tab={board.pk}")
        board_pill = page.locator(f"#leadership-pane-{board.pk} .pl-leader-card__badges .pl-leader-badge")
        board_pill.wait_for(state="visible")
        assert board_pill.inner_text() == "Elevator Certified"
        assert (_background(board_pill), _text_color(board_pill)) == ("rgb(9, 46, 76)", "rgb(255, 255, 255)")
        assert page.locator(f"#leadership-pane-{leadership.pk} .pl-leader-badge").count() == 1

        # Take it back from the Board tab's row: both toggles turn off.
        _open_editor(page, live_server)
        page.locator(f"[data-tab-strip] [data-tab-id='{board.pk}']").click()
        before = _saves(page)
        page.locator(f"#leadership-pane-{board.pk} .pl-badge-toggle").click()
        _wait_saved(page, before + 1)
        assert [toggles.nth(i).is_checked() for i in range(2)] == [False, False]
        assert badge.members.count() == 0
        assert confirm.inner_text() == "Nobody holds this badge, so only the badge goes."

        # Delete it through the confirm.
        fields.get_by_role("button", name="Delete badge").click()
        page.locator(f"form[action='{reverse('hub_admin_leadership_badge_delete', args=[badge.pk])}']").get_by_role(
            "button", name="Delete badge"
        ).click()
        page.locator("[data-badges] .pl-leadership-admin__empty").wait_for()
        assert not LeadershipBadge.objects.exists()
