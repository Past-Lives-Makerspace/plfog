"""End-to-end: Delete a wiki page from its More menu (Felix, 2026-11-17).

An admin opens More, picks Delete, and confirms in the plain modal with Delete Page.
That lands on the wiki home with the message, and the page is gone. The same More menu carries the member's quick actions. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from playwright.sync_api import Page, expect

from membership.models import Member, WikiPage
from tests.features import turn_on
from tests.membership.factories import MembershipPlanFactory, WikiPageFactory

ADMIN_EMAIL = "wiki-delete-admin@example.com"


def _admin() -> None:
    MembershipPlanFactory()
    member = User.objects.create_user(username=ADMIN_EMAIL, email=ADMIN_EMAIL).member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Rowan Ellis"
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["status", "full_legal_name", "fog_role"])
    member.sync_user_permissions()


def describe_deleting_a_wiki_page():
    def it_confirms_in_the_plain_modal_then_deletes(live_server, page: Page, login_via_code):
        turn_on("wiki")
        _admin()
        wiki_page = WikiPageFactory(title="Old Jointer Notes")
        page.set_viewport_size({"width": 1280, "height": 900})
        login_via_code(ADMIN_EMAIL)
        page.goto(f"{live_server.url}{wiki_page.get_absolute_url()}")

        more = page.locator(".pl-wp-actions__more")
        more.get_by_role("button", name="More actions for this page").click()
        menu = more.get_by_role("menu")
        for label in ("Add a Photo", "Add a Tip", "Still accurate", "Report a Problem"):
            expect(menu.get_by_role("menuitem", name=label, exact=True)).to_be_visible()
        menu.get_by_role("menuitem", name="Delete", exact=True).click()

        dialog = page.locator(".pl-modal-backdrop").filter(has_text="Delete this page?")
        expect(dialog).to_be_visible()
        confirm = dialog.get_by_role("button", name="Delete Page")
        expect(dialog.get_by_placeholder("Type DELETE to confirm")).to_have_count(0)
        confirm.click()

        expect(page).to_have_url(f"{live_server.url}/wiki/")
        expect(page.get_by_text("Deleted Old Jointer Notes.")).to_be_visible()
        assert not WikiPage.objects.filter(pk=wiki_page.pk).exists()
