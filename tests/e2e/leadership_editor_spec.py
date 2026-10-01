"""End-to-end: the Leadership Directory editor saves every change by itself (#564).

Pytest's Django client never runs the editor's script, so this walks it in a real browser:
add a tab, add two people to it, rename it, reorder the people (arrow and drag), add a role
line typing its title and email back to back, remove a line, have a blank title refused,
reload, and find all of it saved; then the public page shows the tab and ?tab= opens it.

Waits are on what the page shows, never a fixed sleep: the save pill reads Saved with its
``data-saves`` count past the last one (the queue is drained), a new line carries its
``data-role-id``. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.urls import reverse

from membership.models import LeadershipRole, LeadershipTab, Member
from tests.membership.factories import (
    GuildFactory,
    LeadershipListingFactory,
    LeadershipRoleFactory,
    LeadershipTabFactory,
    MemberFactory,
    MembershipPlanFactory,
)

ADMIN_EMAIL = "leadership-editor-admin@example.com"
ALPINE_READY = "() => !!(document.querySelector('.pl-leadership-admin') || {})._x_dataStack"
PENDING = "() => document.querySelector('.pl-leadership-admin')._x_dataStack[0].pending"
SAVES_OR_PENDING = (
    "() => { const d = document.querySelector('.pl-leadership-admin')._x_dataStack[0]; return d.saves + d.pending; }"
)
SAVED_PAST = (
    "(n) => { const pill = document.querySelector('[data-save-pill]');"
    " return pill && Number(pill.dataset.saves) >= n && pill.textContent.trim() === 'Saved'; }"
)


def _seed() -> tuple[Member, Member, LeadershipTab]:
    """An admin, a Leadership tab with one card, the Guild Leads tab, and two members to add."""
    MembershipPlanFactory()  # so the user signal provisions the member this then promotes
    user = User.objects.create_user(username=ADMIN_EMAIL, email=ADMIN_EMAIL)
    admin = user.member
    admin.fog_role = Member.FogRole.ADMIN
    admin.status = Member.Status.ACTIVE
    admin.save(update_fields=["fog_role", "status"])
    admin.sync_user_permissions()
    LeadershipTab.objects.all().delete()
    leadership = LeadershipTabFactory(title="Leadership", sort_order=0)
    LeadershipRoleFactory(
        listing=LeadershipListingFactory(tab=leadership, member=MemberFactory(full_legal_name="Lena Lead")),
        title="Executive Director",
    )
    LeadershipTabFactory(kind=LeadershipTab.Kind.GUILD_LEADS, title="Guild Leads", sort_order=1)
    GuildFactory(name="Ceramics Guild")
    wilma = MemberFactory(full_legal_name="Wilma Weaver")
    otto = MemberFactory(full_legal_name="Otto Ostrander")
    return wilma, otto, leadership


def _saves(page) -> int:
    return int(page.locator("[data-save-pill]").get_attribute("data-saves") or 0)


def _wait_saved(page, at_least: int) -> None:
    page.wait_for_function(SAVED_PAST, arg=at_least)


def _open_editor(page, live_server, query: str = "") -> None:
    page.goto(f"{live_server.url}{reverse('hub_admin_leadership')}{query}")
    page.wait_for_function(ALPINE_READY)


def _add_person(page, tab_id: int, member: Member, title: str) -> None:
    pane = page.locator(f"#leadership-pane-{tab_id}")
    pane.get_by_role("button", name="+ Add a person").click()
    modal_form = page.locator(f"form[action='{reverse('hub_admin_leadership_person_add', args=[tab_id])}']")
    modal_form.locator(f"#id_add-{tab_id}-member").select_option(str(member.pk))
    modal_form.locator(f"#id_add-{tab_id}-title").fill(title)
    modal_form.get_by_role("button", name="Add").click()
    page.locator(
        f"#leadership-pane-{tab_id} [data-listing-id] .pl-slide-summary__title", has_text=member.display_name
    ).wait_for()
    page.wait_for_function(ALPINE_READY)


def _row(page, tab_id: int, member: Member):
    return page.locator(f"#leadership-pane-{tab_id} [data-listing-id]", has_text=member.display_name)


def _people_order(page, tab_id: int) -> list[str]:
    return page.locator(f"#leadership-pane-{tab_id} [data-listing-id] .pl-slide-summary__title").all_inner_texts()


def describe_leadership_editor():
    def it_saves_every_edit_by_itself_and_the_public_page_shows_the_tab(live_server, page, login_via_code):
        wilma, otto, leadership = _seed()
        login_via_code(ADMIN_EMAIL)
        _open_editor(page, live_server)

        # Add a tab: the modal posts, the editor comes back open on the new tab.
        page.get_by_role("button", name="+ Add a tab").click()
        page.locator("#id_newtab-title").fill("Board")
        page.locator("#id_newtab-intro").fill("The people who advise the council.")
        page.locator(f"form[action='{reverse('hub_admin_leadership_tab_add')}']").get_by_role(
            "button", name="Add"
        ).click()
        page.locator("[data-tab-strip] .vote-tab--active", has_text="Board").wait_for()
        page.wait_for_function(ALPINE_READY)
        board = LeadershipTab.objects.get(title="Board")
        assert page.locator("[data-tab-strip] .vote-tab--active").get_attribute("data-tab-id") == str(board.pk)

        # Add two people to it.
        _add_person(page, board.pk, wilma, "Board Advisor")
        _add_person(page, board.pk, otto, "Board Chair")
        assert _people_order(page, board.pk) == ["Wilma Weaver", "Otto Ostrander"]

        # Rename it: the strip label follows the typing, and the change saves.
        before = _saves(page)
        title = page.locator(f"#id_tab-{board.pk}-title")
        title.fill("Board of Advisors")
        assert page.locator(f"[data-tab-strip] [data-tab-id='{board.pk}']").inner_text() == "Board of Advisors"
        title.press("Tab")
        _wait_saved(page, before + 1)

        # A blank title is refused: its error shows and the field goes back to the saved title.
        before = _saves(page)
        title.fill("")
        title.press("Tab")
        page.locator(f"[data-tab-fields='{board.pk}'] .pl-field-error").wait_for()
        assert title.input_value() == "Board of Advisors"
        assert page.locator(f"[data-tab-strip] [data-tab-id='{board.pk}']").inner_text() == "Board of Advisors"
        assert _saves(page) == before

        # Reorder with the arrow: Otto moves above Wilma at once.
        before = _saves(page)
        _row(page, board.pk, otto).locator("[data-move='up']").click()
        _wait_saved(page, before + 1)
        assert _people_order(page, board.pk) == ["Otto Ostrander", "Wilma Weaver"]

        # Add a role line, typing its title and then its email without waiting in between:
        # the email's save queues behind the line's first save and uses the id it answers.
        before = _saves(page)
        wilma_row = _row(page, board.pk, wilma)
        wilma_row.get_by_role("button", name="+ Add a role").click()
        new_line = wilma_row.locator("[data-role]").last
        assert new_line.get_attribute("data-role-id") is None
        # Hold the line's first save in flight so the email's save is sure to queue behind it.
        held = []
        page.route("**/roles/add/", lambda route: held.append(route))
        new_line.locator("[data-autosave='title']").fill("Treasurer Liaison")
        with page.expect_request("**/roles/add/"):
            new_line.locator("[data-autosave='title']").press("Tab")
        new_line.locator("[data-autosave='email']").fill("treasurer@example.com")
        new_line.locator("[data-autosave='email']").press("Tab")
        assert page.evaluate(PENDING) == 2  # the create, and the email waiting on its id
        assert page.locator("[data-save-pill]").inner_text() == "Saving…"
        held[0].continue_()
        page.unroute("**/roles/add/")
        _wait_saved(page, before + 2)
        assert wilma_row.locator("[data-role]").count() == 2
        created = LeadershipRole.objects.filter(title="Treasurer Liaison")
        assert list(created.values_list("email", flat=True)) == ["treasurer@example.com"]
        assert new_line.get_attribute("data-role-id") == str(created.get().pk)

        # Remove a role line at once.
        before = _saves(page)
        _row(page, board.pk, otto).locator("[data-role]", has=page.locator("input[value='Board Chair']")).get_by_role(
            "button", name="Remove"
        ).click()
        _wait_saved(page, before + 1)
        assert not LeadershipRole.objects.filter(title="Board Chair").exists()

        # Reload: everything above was saved.
        page.reload()
        page.wait_for_function(ALPINE_READY)
        assert page.locator("[data-tab-strip] .vote-tab--active").get_attribute("data-tab-id") == str(board.pk)
        assert page.locator(f"[data-tab-strip] [data-tab-id='{board.pk}']").inner_text() == "Board of Advisors"
        assert page.locator(f"#id_tab-{board.pk}-title").input_value() == "Board of Advisors"
        assert _people_order(page, board.pk) == ["Otto Ostrander", "Wilma Weaver"]
        wilma_titles = _row(page, board.pk, wilma).locator("[data-autosave='title']")
        assert [wilma_titles.nth(i).input_value() for i in range(wilma_titles.count())] == [
            "Board Advisor",
            "Treasurer Liaison",
        ]
        assert _row(page, board.pk, wilma).locator("[data-autosave='email']").nth(1).input_value() == (
            "treasurer@example.com"
        )
        assert _row(page, board.pk, otto).locator("[data-role]").count() == 0

        # The public page shows the tab in the strip, and ?tab= opens it.
        page.goto(f"{live_server.url}{reverse('hub_leadership_directory')}")
        page.locator(f"#leadership-pane-{leadership.pk}").wait_for(state="visible")
        assert page.locator(f"#leadership-tab-{board.pk}").inner_text() == "Board of Advisors"
        assert page.locator(f"#leadership-pane-{board.pk}").is_hidden()
        page.goto(f"{live_server.url}{reverse('hub_leadership_directory')}?tab={board.pk}")
        board_pane = page.locator(f"#leadership-pane-{board.pk}")
        board_pane.wait_for(state="visible")
        assert "vote-tab--active" in (page.locator(f"#leadership-tab-{board.pk}").get_attribute("class") or "")
        assert page.locator(f"#leadership-pane-{leadership.pk}").is_hidden()
        assert board_pane.locator(".pl-leader-card__plate").all_inner_texts()[0].startswith("Otto Ostrander")
        assert "The people who advise the council." in board_pane.inner_text()

        # A click switches panes and rewrites ?tab= so a reload keeps the tab.
        page.locator(f"#leadership-tab-{leadership.pk}").click()
        page.locator(f"#leadership-pane-{leadership.pk}").wait_for(state="visible")
        assert page.url.endswith(f"?tab={leadership.pk}")

    def it_drags_a_person_by_the_grip_and_saves_the_order(live_server, page, login_via_code):
        wilma, otto, leadership = _seed()
        LeadershipListingFactory(tab=leadership, member=wilma, sort_order=1)
        LeadershipListingFactory(tab=leadership, member=otto, sort_order=2)
        login_via_code(ADMIN_EMAIL)
        _open_editor(page, live_server, f"?tab={leadership.pk}")
        assert _people_order(page, leadership.pk) == ["Lena Lead", "Wilma Weaver", "Otto Ostrander"]
        before = _saves(page)
        _row(page, leadership.pk, otto).locator(".pl-slide-grip").drag_to(_row(page, leadership.pk, wilma))
        _wait_saved(page, before + 1)
        assert _people_order(page, leadership.pk) == ["Lena Lead", "Otto Ostrander", "Wilma Weaver"]
        page.reload()
        page.wait_for_function(ALPINE_READY)
        assert _people_order(page, leadership.pk) == ["Lena Lead", "Otto Ostrander", "Wilma Weaver"]

    def it_moves_a_tab_and_removes_a_person_at_once(live_server, page, login_via_code):
        wilma, _otto, leadership = _seed()
        LeadershipListingFactory(tab=leadership, member=wilma, sort_order=1)
        guild_leads = LeadershipTab.objects.get(kind=LeadershipTab.Kind.GUILD_LEADS)
        login_via_code(ADMIN_EMAIL)
        _open_editor(page, live_server, f"?tab={guild_leads.pk}")
        before = _saves(page)
        page.locator(f"#leadership-pane-{guild_leads.pk}").get_by_role("button", name="← Move left").click()
        _wait_saved(page, before + 1)
        strip = page.locator("[data-tab-strip] [data-tab-id]")
        assert strip.nth(0).get_attribute("data-tab-id") == str(guild_leads.pk)

        page.locator(f"[data-tab-strip] [data-tab-id='{leadership.pk}']").click()
        before = _saves(page)
        _row(page, leadership.pk, wilma).get_by_role("button", name="Remove from tab").click()
        _wait_saved(page, before + 1)
        assert _row(page, leadership.pk, wilma).count() == 0

        page.reload()
        page.wait_for_function(ALPINE_READY)
        assert page.locator("[data-tab-strip] [data-tab-id]").nth(0).get_attribute("data-tab-id") == str(guild_leads.pk)
        assert _people_order(page, leadership.pk) == ["Lena Lead"]

    def it_reloads_with_a_toast_when_the_order_it_saves_is_stale(live_server, page, login_via_code):
        wilma, otto, leadership = _seed()
        LeadershipListingFactory(tab=leadership, member=wilma, sort_order=1)
        login_via_code(ADMIN_EMAIL)
        _open_editor(page, live_server, f"?tab={leadership.pk}")
        assert _people_order(page, leadership.pk) == ["Lena Lead", "Wilma Weaver"]
        LeadershipListingFactory(tab=leadership, member=otto, sort_order=2)  # another window adds Otto
        with page.expect_response(lambda response: "/people/order/" in response.url) as answer:
            _row(page, leadership.pk, wilma).locator("[data-move='up']").click()
        assert answer.value.status == 409
        page.locator(".plt-toast--error").first.wait_for()
        _row(page, leadership.pk, otto).wait_for()  # the reload brings in the card the order missed
        page.wait_for_function(ALPINE_READY)
        assert _people_order(page, leadership.pk) == ["Lena Lead", "Wilma Weaver", "Otto Ostrander"]

    def it_says_couldnt_save_and_keeps_the_card_when_the_session_has_expired(live_server, page, login_via_code):
        wilma, _otto, leadership = _seed()
        LeadershipListingFactory(tab=leadership, member=wilma, sort_order=1)
        login_via_code(ADMIN_EMAIL)
        _open_editor(page, live_server, f"?tab={leadership.pk}")
        # Signed out behind the page's back: fetch follows the login redirect and reads a 200.
        page.context.clear_cookies(name="sessionid")
        _row(page, leadership.pk, wilma).get_by_role("button", name="Remove from tab").click()
        page.locator(".plt-toast--error").first.wait_for()
        page.wait_for_function(
            "() => document.querySelector('[data-save-pill]').textContent.trim().startsWith(\"Couldn't save\")"
        )
        assert _row(page, leadership.pk, wilma).is_visible()
        assert leadership.listings.get(member=wilma).is_listed is True

    def it_holds_a_blank_title_until_blur_instead_of_saving_it_mid_typing(live_server, page, login_via_code):
        _wilma, _otto, leadership = _seed()
        login_via_code(ADMIN_EMAIL)
        page.clock.install()
        _open_editor(page, live_server, f"?tab={leadership.pk}")
        page.clock.pause_at(datetime.now() + timedelta(seconds=5))
        title = page.locator(f"#id_tab-{leadership.pk}-title")
        errors = page.locator(f"[data-tab-fields='{leadership.pk}'] .pl-field-error")

        # Cleared and left past the typing pause: nothing is queued, nothing snaps back.
        title.fill("")
        page.clock.run_for(2000)
        assert page.evaluate(PENDING) == 0
        assert title.input_value() == ""
        assert errors.count() == 0

        # The same pause with a value does save, so the guard held the blank, not a dead timer.
        before = _saves(page)
        title.fill("Leadership Team")
        page.clock.run_for(2000)
        assert page.evaluate(SAVES_OR_PENDING) > before
        page.clock.resume()
        _wait_saved(page, before + 1)

        # On blur a blank is checked: refused, its error shown, the saved title put back.
        title.fill("")
        title.press("Tab")
        errors.wait_for()
        assert title.input_value() == "Leadership Team"
        assert LeadershipTab.objects.get(pk=leadership.pk).title == "Leadership Team"
