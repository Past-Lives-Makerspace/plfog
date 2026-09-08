"""End-to-end: the whole sticker loop, in a real browser (spec A §9, PR A4).

Seed a machine page from the equipment register, scan its sticker while signed out, come
through the real login-by-code flow, and land on the machine you are standing in front of.
Then contribute the way a member on a phone actually does: a tip, not an essay.

Only a browser proves this. The redirect chain crosses the allauth screens, the login has
to preserve ``?next`` across two form submits (which is why the first spec drives the login
forms itself rather than through ``login_via_code``, whose own ``goto`` would throw the
``?next=`` away), and the tip lands through a modal whose form posts with HTMX. Run with
``pytest -m e2e``.

Browser-writing e2e flake on local SQLite (one writer lock, so the browser's POST races
the test's read). Run these on Postgres, the way CI does::

    DATABASE_URL="postgres://plfog:plfog@localhost:5433/plfog" \
        pytest tests/e2e/wiki_sticker_scan_spec.py -m e2e --no-cov -o addopts="" -q
"""

from __future__ import annotations

import re
from io import StringIO

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from playwright.sync_api import expect

from core.models import SiteConfiguration
from membership.models import Member, WikiPage
from tests.membership.factories import EquipmentFactory, GuildFactory, MembershipPlanFactory

MEMBER_EMAIL = "sticker-scanner@example.com"


def _seed_account(email: str) -> None:
    """A sign-in-ready account, so the code flow can be driven from wherever we already are.

    ``login_via_code`` does its own ``goto`` of the login screen, which discards the
    ``?next=`` the scan set — exactly the thing this spec has to prove survives.
    """
    from allauth.account.models import EmailAddress

    user, _ = get_user_model().objects.get_or_create(username=email, defaults={"email": email})
    EmailAddress.objects.get_or_create(user=user, email=email, defaults={"verified": True, "primary": True})


def _wiki_on() -> None:
    config = SiteConfiguration.load()
    config.wiki_enabled = True
    config.save()


def describe_scanning_a_machine_sticker():
    def it_carries_a_signed_out_scan_through_login_onto_the_machines_page(live_server, page):
        MembershipPlanFactory()
        _wiki_on()
        EquipmentFactory(name="Table Saw", guild=GuildFactory(name="Woodworking"))
        call_command("seed_wiki_machine_pages", stdout=StringIO())
        wiki_page = WikiPage.objects.get(title="Table Saw")

        # Scan it signed out. The sticker route hands the browser to the login screen with
        # the machine's page as ?next=, which is the detail the whole feature turns on.
        page.goto(f"{live_server.url}{reverse('hub_wiki_qr', args=[wiki_page.qr_code])}")
        expect(page).to_have_url(re.compile(rf"login.*next={re.escape(wiki_page.get_absolute_url())}"))

        # Finish the login from exactly where the scan left us, WITHOUT navigating to the
        # login screen ourselves. Only this proves ?next= survives both form submits, which
        # is the whole difference between a sticker that works and one that teaches people
        # it does not.
        _seed_account(MEMBER_EMAIL)
        mail.outbox = []
        page.fill('input[name="email"]', MEMBER_EMAIL)
        page.locator('input[name="email"]').press("Enter")
        page.wait_for_selector('input[name="code"]')
        assert mail.outbox, "expected a login-code email"
        code = re.search(r"is:\s+(\S+)", mail.outbox[-1].body)
        assert code, f"no login code in {mail.outbox[-1].body!r}"
        page.fill('input[name="code"]', code.group(1))
        page.locator('input[name="code"]').press("Enter")

        # It has to land on the machine the member is standing in front of, not the hub home.
        expect(page).to_have_url(f"{live_server.url}{wiki_page.get_absolute_url()}")
        expect(page.locator("h1")).to_have_text("Table Saw")

    def it_shows_the_seeded_stub_asking_to_be_filled_in(live_server, page, login_via_code):
        MembershipPlanFactory()
        _wiki_on()
        EquipmentFactory(name="Table Saw")
        call_command("seed_wiki_machine_pages", stdout=StringIO())
        wiki_page = WikiPage.objects.get(title="Table Saw")

        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{wiki_page.get_absolute_url()}")
        # Every contribution is an edit, not a creation: the prompts are already there.
        expect(page.locator(".pl-wp-factsblock")).to_contain_text("Blade or bit")
        expect(page.locator(".pl-wp-factsblock")).to_contain_text("Not filled in yet")

    def it_takes_a_tip_from_the_phone_action_bar(live_server, page, login_via_code):
        MembershipPlanFactory()
        _wiki_on()
        EquipmentFactory(name="Table Saw")
        call_command("seed_wiki_machine_pages", stdout=StringIO())
        wiki_page = WikiPage.objects.get(title="Table Saw")

        login_via_code(MEMBER_EMAIL)
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{live_server.url}{wiki_page.get_absolute_url()}")

        page.locator(".pl-wp-actionbar").get_by_role("button", name="Add Tip", exact=True).click()
        # The modal component ids its body, not its root: #<modal_id>-body.
        page.fill("#wiki-tip-body textarea", "The riving knife lives in the drawer under the outfeed table.")
        page.locator("#wiki-tip-body").get_by_role("button", name="Add Tip", exact=True).click()

        # The tip goes through apply_edit, so it lands in the body and the page says so.
        expect(page.locator(".pl-wp-body")).to_contain_text("riving knife lives in the drawer")
        wiki_page.refresh_from_db()
        assert "riving knife" in wiki_page.body
        # A person has written here now, so the seeder must never rewrite this body again.
        assert wiki_page.body_edited_at is not None

    def it_gives_an_editor_a_sticker_to_print(live_server, page, login_via_code):
        MembershipPlanFactory()
        _wiki_on()
        EquipmentFactory(name="Table Saw")
        call_command("seed_wiki_machine_pages", stdout=StringIO())
        wiki_page = WikiPage.objects.get(title="Table Saw")

        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{wiki_page.get_absolute_url()}")

        share = page.locator(".pl-wp-qrshare")
        expect(share).to_contain_text("Share This Page")
        # The QR renders as real, scalable SVG rather than an empty box.
        expect(share.locator(".pl-qr-preview svg")).to_be_visible()
        expect(share.locator("#qr-share-url")).to_have_value(wiki_page.sticker_url)

    def it_lets_an_admin_print_a_sheet_of_stickers(live_server, page, login_via_code):
        MembershipPlanFactory()
        _wiki_on()
        EquipmentFactory(name="Table Saw")
        EquipmentFactory(name="Kiln")
        call_command("seed_wiki_machine_pages", stdout=StringIO())

        login_via_code(MEMBER_EMAIL)
        member = Member.objects.get(user__username=MEMBER_EMAIL)
        member.fog_role = Member.FogRole.ADMIN
        member.save(update_fields=["fog_role"])
        member.sync_user_permissions()

        page.goto(f"{live_server.url}{reverse('hub_wiki_stickers')}")
        expect(page.locator(".pl-wp-sticker")).to_have_count(2)
        expect(page.get_by_role("button", name="Print / Save as PDF")).to_be_visible()
        # A standalone print document: no member chrome around it.
        expect(page.locator(".hub-sidebar")).to_have_count(0)


def describe_scanning_a_sticker_whose_page_has_gone():
    def it_explains_itself_instead_of_dead_ending(live_server, page, login_via_code):
        MembershipPlanFactory()
        _wiki_on()
        login_via_code(MEMBER_EMAIL)

        page.goto(f"{live_server.url}/m/ZZZZZZ/")
        expect(page.locator("h1")).to_have_text("No Page For That Sticker")
        expect(page.locator("body")).to_contain_text("points at a page that has moved")
        # A printed sticker outlives its page, so the dead end offers a way onward.
        expect(page.locator(f'form[action="{reverse("hub_wiki_search")}"]')).to_be_visible()
