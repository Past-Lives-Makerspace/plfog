"""End-to-end: the Holiday Bizarre HQ, its volunteer sign-up and its wine wall (Track C, parts 1 to 3).

In a real browser: a signed out visitor opens the HQ and sees the public door but not the
admins one, opens Volunteer Sign-up, saves Your Info, signs up for a shift over htmx (the row
flips to Remove Me and the band's figures move in place), then removes themself. A member
signs in through the real login by code flow and finds their name, email and phone already in
Your Info; another opens the wine wall from the HQ, logs a bottle with a photo and sees the
band's count, value and supporters move. The service worker is blocked because it fetches
navigations past ``page.route``; every wait is on what the page shows. Run with
``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import io

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from PIL import Image
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from hq.models import HubCard, ShiftSignup, VolunteerShift, WineBottle
from membership.models import Member
from tests.hq.factories import EventHubFactory, HubCardFactory, VolunteerShiftFactory
from tests.membership.factories import CommunityEventFactory, MembershipPlanFactory

MEMBER_EMAIL = "hb-volunteer@example.com"


@pytest.fixture
def browser_context_args(browser_context_args):
    """Block the service worker: it fetches a form navigation itself, past ``page.route``."""
    return {**browser_context_args, "service_workers": "block"}


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the browser go quiet before teardown truncates (see class_composer_draft_spec)."""
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def _seed_hq() -> tuple[HubCard, HubCard, VolunteerShift]:
    """The open HQ with a public Volunteer Sign-up door, an admins only door and two shifts."""
    hub = EventHubFactory(is_open=True, slug="holiday-bizarre", title="Holiday Bizarre HQ")
    volunteers = HubCardFactory(
        hub=hub,
        kind=HubCard.Kind.VOLUNTEERS,
        audience=HubCard.Audience.PUBLIC,
        title="Volunteer Sign-up",
        button_text="Sign up to volunteer",
    )
    ledger = HubCardFactory(hub=hub, title="PL Ledger", audience=HubCard.Audience.ADMINS)
    shift = VolunteerShiftFactory(
        hub=hub, phase=VolunteerShift.Phase.BEFORE, role="Flyer runner", when_text="November", need=6
    )
    VolunteerShiftFactory(hub=hub, phase=VolunteerShift.Phase.DURING, role="Door and check-in", need=2)
    return volunteers, ledger, shift


def _seed_member() -> Member:
    """A member with a login, a preferred name and a phone, for the prefill."""
    MembershipPlanFactory()  # so the user signal provisions the member
    user = get_user_model().objects.create_user(username=MEMBER_EMAIL, email=MEMBER_EMAIL)
    member = user.member
    member.preferred_name = "Lee Organiser"
    member.phone = "503 555 0142"
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["preferred_name", "phone", "status"])
    return member


def describe_a_signed_out_visitor():
    def it_opens_the_hq_saves_their_info_signs_up_and_removes_themself(live_server, page):
        volunteers, ledger, shift = _seed_hq()
        page.goto(live_server.url + reverse("hq:page", args=["holiday-bizarre"]))

        expect(page.locator(f'[data-hq-card-pk="{volunteers.pk}"]')).to_be_visible()
        expect(page.locator(f'[data-hq-card-pk="{ledger.pk}"]')).to_have_count(0)
        page.locator(f'[data-hq-card-pk="{volunteers.pk}"] a[data-hq-card-link]').click()

        expect(page.locator("[data-hq-volunteers]")).to_be_visible()
        expect(page.locator("[data-hq-vol-empty]")).to_be_visible()
        expect(page.locator(f'[data-hq-shift="{shift.pk}"] button[data-hq-shift-signup]')).to_be_disabled()
        expect(page.locator("[data-hq-band-people]")).to_have_text("0")
        expect(page.locator("[data-hq-band-filled]")).to_have_text("0 / 8")

        page.fill('input[name="name"]', "Pat Guest")
        page.fill('input[name="email"]', "pat@example.com")
        page.fill('input[name="phone"]', "503 555 0100")
        page.locator("[data-hq-vol-save]").click()

        expect(page.locator("[data-hq-vol-saved]")).to_be_visible()
        row = page.locator(f'[data-hq-shift="{shift.pk}"]')
        expect(row.locator("button[data-hq-shift-signup]")).to_be_enabled()
        row.locator("button[data-hq-shift-signup]").click()

        expect(row.locator("button[data-hq-shift-remove]")).to_be_visible()
        expect(page.locator("[data-hq-band-people]")).to_have_text("1")
        expect(page.locator("[data-hq-band-filled]")).to_have_text("1 / 8")
        expect(row.locator("[data-hq-shift-filled]")).to_have_text("1 of 6 filled")
        expect(page.locator(".plt-msg").last).to_contain_text("You are on Flyer runner")
        signup = ShiftSignup.objects.get()
        assert (signup.email, signup.name, signup.member) == ("pat@example.com", "Pat Guest", None)

        row.locator("button[data-hq-shift-remove]").click()

        expect(row.locator("button[data-hq-shift-signup]")).to_be_visible()
        expect(page.locator("[data-hq-band-people]")).to_have_text("0")
        expect(page.locator("[data-hq-band-filled]")).to_have_text("0 / 8")
        assert not ShiftSignup.objects.exists()


def describe_a_member():
    def it_finds_their_info_already_filled_in(live_server, page, login_via_code):
        _seed_hq()
        member = _seed_member()
        login_via_code(MEMBER_EMAIL)
        page.goto(live_server.url + reverse("hq:volunteers", args=["holiday-bizarre"]))

        expect(page.locator("[data-hq-vol-prefilled]")).to_be_visible()
        expect(page.locator('input[name="name"]')).to_have_value("Lee Organiser")
        expect(page.locator('input[name="email"]')).to_have_value(member.primary_email)
        expect(page.locator('input[name="phone"]')).to_have_value("503 555 0142")
        expect(page.locator("button[data-hq-shift-signup]").first).to_be_enabled()


def describe_the_rsvp_door():
    def it_goes_from_the_rsvp_card_to_the_event_page_and_back_to_the_hq(live_server, page):
        hub = EventHubFactory(is_open=True, slug="holiday-bizarre", title="Holiday Bizarre HQ")
        hub.event = CommunityEventFactory(community=True, title="Holiday Bizarre")
        hub.save()
        rsvp = HubCardFactory(hub=hub, kind=HubCard.Kind.RSVP, audience=HubCard.Audience.PUBLIC)
        page.goto(live_server.url + reverse("hq:page", args=["holiday-bizarre"]))

        page.locator(f'[data-hq-card-pk="{rsvp.pk}"] a[data-hq-card-link]').click()

        expect(page.locator("[data-event-page]")).to_be_visible()
        expect(page).to_have_url(live_server.url + reverse("hub_event_detail", args=[hub.event.pk]) + "?from=hq")
        back = page.locator("[data-event-hq-back]")
        expect(back).to_have_text("\u2190 Holiday Bizarre HQ")
        back.click()

        expect(page.locator("[data-event-page]")).to_have_count(0)
        expect(page).to_have_url(live_server.url + reverse("hq:page", args=["holiday-bizarre"]))


def _png() -> dict:
    """A small bottle photo for the file input, as Playwright takes it."""
    buffer = io.BytesIO()
    Image.new("RGB", (64, 96), (120, 20, 40)).save(buffer, format="PNG")
    return {"name": "bottle.png", "mimeType": "image/png", "buffer": buffer.getvalue()}


@pytest.fixture
def media_in_tmp(settings, tmp_path):
    """Uploads land in a throwaway folder. Requested before ``serve_media``, which reads the root once."""
    settings.MEDIA_ROOT = str(tmp_path / "media")


def describe_the_wine_wall():
    def it_opens_from_the_hq_logs_a_bottle_with_a_photo_and_sees_the_band_move(
        live_server, page, login_via_code, media_in_tmp, serve_media
    ):
        hub = EventHubFactory(is_open=True, slug="holiday-bizarre", title="Holiday Bizarre HQ")
        wine = HubCardFactory(
            hub=hub,
            kind=HubCard.Kind.WINE_WALL,
            audience=HubCard.Audience.MEMBERS,
            title="Log a wine bottle",
            button_text="Enter a bottle",
        )
        member = _seed_member()
        login_via_code(MEMBER_EMAIL)
        page.goto(live_server.url + reverse("hq:page", args=["holiday-bizarre"]))
        page.locator(f'[data-hq-card-pk="{wine.pk}"] a[data-hq-card-link]').click()

        expect(page.locator("[data-hq-wine]")).to_be_visible()
        expect(page.locator("[data-hq-wine-bottles]")).to_have_text("0")
        expect(page.locator("[data-hq-wine-no-bottles]")).to_be_visible()
        expect(page.locator("[data-hq-wine-form]")).to_be_hidden()
        page.locator("[data-hq-wine-open]").click()

        expect(page.locator("[data-hq-wine-form]")).to_be_visible()
        expect(page.locator('input[name="credited_name"]')).to_have_value("Lee Organiser")
        page.select_option('select[name="kind"]', "white")
        page.fill('input[name="name"]', "2022 Pinot Gris")
        page.fill('input[name="winery"]', "Abacela")
        page.fill('input[name="value"]', "18")
        page.fill('input[name="qty"]', "2")
        page.fill('input[name="giver_name"]', "Pat Giver")
        page.select_option('select[name="giver_type"]', "wine_shop")
        page.locator("#id_photo").set_input_files(_png())  # Choose a photo; Take a photo is #id_photo_camera
        page.locator("[data-hq-wine-add]").click()

        expect(page.locator("[data-hq-wine-bottles]")).to_have_text("2")
        expect(page.locator("[data-hq-wine-value]")).to_have_text("$36")
        expect(page.locator("[data-hq-wine-supporters]")).to_have_text("1")
        expect(page.locator('[data-hq-wine-kind="white"]')).to_be_visible()
        expect(page.locator("[data-hq-bottle-photo]")).to_have_count(1)
        expect(page.locator("[data-hq-bottle-photo]")).to_have_js_property("naturalWidth", 64)
        expect(page.locator('[data-hq-winery="Abacela"] [data-hq-winery-bottles]')).to_have_text("2 bottles")
        expect(page.locator("[data-hq-wine-form]")).to_be_hidden()
        bottle = WineBottle.objects.get()
        assert (bottle.name, bottle.qty, bottle.value_cents, bottle.giver_type) == (
            "2022 Pinot Gris",
            2,
            1800,
            "wine_shop",
        )
        assert (bottle.logged_by_id, bottle.credited_name) == (member.pk, "Lee Organiser")
        assert bottle.logged_by_email == MEMBER_EMAIL
        assert bottle.photo.name.startswith("hq/bottles/")

    def it_takes_or_chooses_a_photo_with_one_preview_and_posts_the_last_one_picked(
        live_server, page, media_in_tmp, serve_media
    ):
        hub = EventHubFactory(is_open=True, slug="holiday-bizarre", title="Holiday Bizarre HQ")
        HubCardFactory(hub=hub, kind=HubCard.Kind.WINE_WALL, audience=HubCard.Audience.PUBLIC)
        page.goto(live_server.url + reverse("hq:wine", args=["holiday-bizarre"]))
        page.locator("[data-hq-wine-open]").click()
        preview = page.locator("#image-preview-id_photo img")
        camera = page.locator("#id_photo_camera")
        chooser = page.locator("#id_photo")

        expect(page.locator("[data-image-field-take]")).to_have_text("Take a photo")
        expect(camera).to_have_attribute("capture", "environment")
        with page.expect_file_chooser() as taken:
            page.locator("[data-image-field-take]").click()
        assert taken.value.element.get_attribute("id") == "id_photo_camera"
        taken.value.set_files(_png())

        expect(preview).to_be_visible()
        assert camera.evaluate("el => el.files.length") == 1

        with page.expect_file_chooser() as chosen:
            page.locator("#image-upload-zone-id_photo").click()
        assert chosen.value.element.get_attribute("id") == "id_photo"
        chosen.value.set_files({**_png(), "name": "chosen.png"})

        expect(preview).to_be_visible()
        assert chooser.evaluate("el => el.files.length") == 1
        assert camera.evaluate("el => el.files.length") == 0  # picking in one clears the other

        with page.expect_file_chooser() as retaken:
            page.locator("[data-image-field-take]").click()
        retaken.value.set_files(_png())

        assert camera.evaluate("el => el.files.length") == 1
        assert chooser.evaluate("el => el.files.length") == 0
        page.select_option('select[name="kind"]', "red")
        page.fill('input[name="name"]', "2021 Pinot Noir")
        page.fill('input[name="winery"]', "Abacela")
        page.fill('input[name="value"]', "25")
        page.fill('input[name="giver_name"]', "Pat Giver")
        page.fill('input[name="credited_name"]', "Lee")
        page.locator("[data-hq-wine-add]").click()

        expect(page.locator("[data-hq-bottle-photo]")).to_have_count(1)
        expect(page.locator("[data-hq-bottle-photo]")).to_have_js_property("naturalWidth", 64)
        assert WineBottle.objects.get().photo.name.startswith("hq/bottles/")

    def it_reaches_both_choices_by_keyboard_with_a_ring_and_drops_a_preview_that_will_not_post(live_server, page):
        hub = EventHubFactory(is_open=True, slug="holiday-bizarre", title="Holiday Bizarre HQ")
        HubCardFactory(hub=hub, kind=HubCard.Kind.WINE_WALL, audience=HubCard.Audience.PUBLIC)
        page.goto(live_server.url + reverse("hq:wine", args=["holiday-bizarre"]))
        page.locator("[data-hq-wine-open]").click()
        take = page.locator("[data-image-field-take]")
        zone = page.locator("#image-upload-zone-id_photo")
        preview = page.locator("#image-preview-id_photo")

        page.locator("#id_credited_name").click()  # then Tab: the photo's ? help, Take a photo, Choose a photo
        for _ in range(4):
            page.keyboard.press("Tab")
            if page.evaluate("document.activeElement.id") == "id_photo_camera":
                break
        assert page.evaluate("document.activeElement.id") == "id_photo_camera"
        assert take.evaluate("el => getComputedStyle(el).outlineStyle") == "solid"
        assert zone.evaluate("el => getComputedStyle(el).outlineStyle") == "none"

        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.id") == "id_photo"
        assert zone.evaluate("el => getComputedStyle(el).outlineStyle") == "solid"
        assert take.evaluate("el => getComputedStyle(el).outlineStyle") == "none"

        page.locator("#id_photo_camera").set_input_files(_png())
        expect(preview.locator("img")).to_be_visible()
        page.locator("#id_photo_camera").set_input_files([])
        expect(preview).to_be_hidden()
        expect(preview.locator("img")).to_have_count(0)

        page.locator("#id_photo").set_input_files(_png())
        expect(preview.locator("img")).to_be_visible()
        page.locator("#id_photo_camera").set_input_files([])  # the chooser still holds the photo that posts
        expect(preview.locator("img")).to_be_visible()
