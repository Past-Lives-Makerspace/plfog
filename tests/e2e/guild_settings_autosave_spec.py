"""End-to-end: Guild Settings save themselves as they are edited (#575).

Pytest's Django client never runs the autosave script, so this walks it in a real browser:
typing in About saves after the pause and survives a reload; a bad link URL is refused
inline with the typed value kept and nothing saved; a FAQ question typed in two steps is
created once and edited in place, never duplicated; a saved link's Delete takes it off the
page and the database; the member suggestions and Reservations toggles save at once (the
second puts a Reservations tab on the guild page); typing then leaving at once, by a
boosted in page link and by a hard sidebar link, still lands the save; and a photo dropped
on a new orientation type while the row's first save is in flight still saves, because that
save's answer clears only a file it sent.

Waits are on what the page shows, never a fixed sleep: the save pill reads Saved with its
``data-saves`` count past the last one, a new row carries its hidden id. Run with
``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import base64
import re
import time

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import Guild, GuildFAQItem, GuildLink, GuildOrientationSettings, OrientationType
from tests.membership.factories import EquipmentFactory, GuildFactory, GuildLinkFactory, MembershipPlanFactory

ADMIN_EMAIL = "guild-autosave-admin@example.com"
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
ALPINE_READY = "() => !!(document.querySelector('[data-guild-autosave]') || {})._x_dataStack"
QUEUE_IDLE = "() => document.querySelector('[data-guild-autosave]')._x_dataStack[0].pending === 0"
SAVED_PAST = (
    "(n) => { const pill = document.querySelector('[data-save-pill]');"
    " return !!pill && Number(pill.dataset.saves) >= n && pill.textContent.trim() === 'Saved'; }"
)


def _admin_guild() -> Guild:
    """A guild and the plan the login signal needs to provision the admin's member."""
    MembershipPlanFactory()
    return GuildFactory(name="Ceramics Guild", about="", allow_member_announcement_suggestions=False)


def _hide(guild: Guild) -> None:
    guild.is_active = False
    guild.save(update_fields=["is_active"])


def _sign_in_as_admin(login_via_code) -> None:
    login_via_code(ADMIN_EMAIL)
    user = get_user_model().objects.get(username=ADMIN_EMAIL)
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])


def _open(page, live_server, guild: Guild, tab: str) -> None:
    page.goto(f"{live_server.url}{reverse('hub_guild_edit', args=[guild.pk])}?tab={tab}")
    page.wait_for_function(ALPINE_READY)


def _open_orientations(page, live_server, guild: Guild) -> None:
    """The guild's Orientations page (#672), which carries the same autosave root."""
    page.goto(f"{live_server.url}{reverse('hub_guild_orientations', args=[guild.pk])}")
    page.wait_for_function(ALPINE_READY)


def _saves(page) -> int:
    return int(page.locator("[data-save-pill]").get_attribute("data-saves") or 0)


def _flag_a_delete_behind_a_bad_row(page, live_server, login_via_code):
    """Two saved links, a new row with a bad URL, and a Delete on a saved row refused behind it.

    Returns the guild, the flagged link and the bad row's URL input; the link is still in the
    database and its row is hidden, flagged, waiting for the next good post.
    """
    guild = _admin_guild()
    GuildLinkFactory(guild=guild, label="Discord", url="https://discord.gg/ceramics")
    wiki = GuildLinkFactory(guild=guild, label="Wiki", url="https://example.com/wiki")
    _sign_in_as_admin(login_via_code)
    _open(page, live_server, guild, "links")
    page.get_by_role("button", name="+ Add a link").click()
    page.locator('input[name="links-2-label"]').fill("Docs")
    url = page.locator('input[name="links-2-url"]')
    url.fill("not a url")
    url.press("Tab")
    page.locator("#link-rows .pl-field-error").wait_for()
    _link_row(page, wiki).get_by_role("button", name="Delete this link").click()
    page.locator("#link-rows .pl-field-error").wait_for()
    assert GuildLink.objects.filter(pk=wiki.pk).exists()
    expect(_link_row(page, wiki)).to_be_hidden()
    return guild, wiki, url


# Drops a one pixel PNG on an image field's zone the way a browser does: a DragEvent whose
# DataTransfer carries the file. Only the field's own inline script listens for it.
DROP_PNG = """(zoneId) => {
    const bytes = Uint8Array.from(atob('%s'), (c) => c.charCodeAt(0));
    const transfer = new DataTransfer();
    transfer.items.add(new File([bytes], 'card.png', { type: 'image/png' }));
    const zone = document.getElementById(zoneId);
    zone.dispatchEvent(new DragEvent('drop', { dataTransfer: transfer, bubbles: true, cancelable: true }));
}""" % base64.b64encode(_PNG).decode()


# Holds every autosave answer (a request carrying X-Autosave) until plReleaseSaves() runs;
# plHeldSaves counts them. Any other fetch the page makes goes through untouched.
HOLD_SAVES = """() => {
    const realFetch = window.fetch;
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    window.plHeldSaves = 0;
    window.plReleaseSaves = release;
    window.fetch = (url, options) => {
        const headers = (options && options.headers) || {};
        if (!headers["X-Autosave"]) return realFetch(url, options);
        window.plHeldSaves += 1;
        return realFetch(url, options).then((response) => gate.then(() => response));
    };
}"""


def _wait_for_photo(guild: Guild, name: str) -> None:
    """The photo's save follows the held one through the queue."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        row = OrientationType.objects.filter(guild=guild, name=name).first()
        if row is not None and row.photo.name:
            return
        time.sleep(0.1)
    raise AssertionError(f"{name!r} never saved its photo")


def _link_row(page, link: GuildLink):
    """A saved link's row, found by its hidden id (a row's label is an input value, not text)."""
    return page.locator(f'#link-rows [data-formset-row]:has(input[name$="-id"][value="{link.pk}"])')


def _wait_saved(page, at_least: int) -> None:
    page.wait_for_function(SAVED_PAST, arg=at_least)


def _wait_for_about(guild: Guild, text: str) -> None:
    """The save fired on the way out may still be landing while the next page loads."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        guild.refresh_from_db()
        if guild.about == text:
            return
        time.sleep(0.1)
    raise AssertionError(f"About never became {text!r}; it is {guild.about!r}")


def _wait_for_thankyou_subject(guild: Guild, text: str) -> None:
    """The Orientations page twin of ``_wait_for_about``: the thank-you subject lands on the way out."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if GuildOrientationSettings.objects.get(guild=guild).thankyou_email_subject == text:
            return
        time.sleep(0.1)
    raise AssertionError(f"The thank-you subject never became {text!r}")


def describe_guild_settings_autosave():
    def it_saves_typing_after_the_pause_and_the_value_survives_a_reload(live_server, page, login_via_code):
        guild = _admin_guild()
        _sign_in_as_admin(login_via_code)
        _open(page, live_server, guild, "basic")

        pill = page.locator("[data-save-pill]")
        expect(pill).to_be_hidden()
        page.locator("#id_about").fill("A guild for people who like mud.")
        _wait_saved(page, 1)
        expect(pill).to_be_visible()
        expect(pill).to_have_class(re.compile(r"pl-meeting-savestate--saved"))
        guild.refresh_from_db()
        assert guild.about == "A guild for people who like mud."

        page.reload()
        page.wait_for_function(ALPINE_READY)
        expect(page.locator("#id_about")).to_have_value("A guild for people who like mud.")

    def it_posts_a_banner_once_and_never_again_with_the_next_edit(live_server, page, login_via_code):
        guild = _admin_guild()
        _sign_in_as_admin(login_via_code)
        _open(page, live_server, guild, "images")

        page.locator('input[name="banner_image"]').set_input_files(
            {"name": "banner.png", "mimeType": "image/png", "buffer": _PNG}
        )
        _wait_saved(page, 1)
        guild.refresh_from_db()
        stored = guild.banner_image.name
        assert stored
        expect(page.locator('input[name="banner_image"]')).to_have_value("")

        # The next edit of the same form posts no file: the stored banner stays as it was.
        page.get_by_role("button", name="Basic Information").click()
        page.locator("#id_about").fill("Still the same banner.")
        _wait_saved(page, 2)
        guild.refresh_from_db()
        assert guild.banner_image.name == stored
        assert guild.about == "Still the same banner."

    def it_completes_a_delete_clicked_while_another_row_is_invalid(live_server, page, login_via_code):
        guild, wiki, url = _flag_a_delete_behind_a_bad_row(page, live_server, login_via_code)

        # Fixing the URL lands the whole form, the queued delete with it.
        before = _saves(page)
        url.fill("https://example.com/docs")
        url.press("Tab")
        _wait_saved(page, before + 1)
        expect(page.locator("#link-rows [data-formset-row]")).to_have_count(2)
        assert not GuildLink.objects.filter(pk=wiki.pk).exists()
        assert GuildLink.objects.filter(guild=guild, label="Docs").exists()

    def it_completes_a_flagged_delete_when_the_bad_row_is_removed_instead(live_server, page, login_via_code):
        guild, wiki, _url = _flag_a_delete_behind_a_bad_row(page, live_server, login_via_code)

        # Removing the bad row takes the refusal away, so the waiting delete posts at once.
        before = _saves(page)
        page.locator("#link-rows [data-formset-row]").last.get_by_role("button", name="Remove").click()
        _wait_saved(page, before + 1)
        expect(page.locator("#link-rows [data-formset-row]")).to_have_count(1)
        assert not GuildLink.objects.filter(pk=wiki.pk).exists()
        assert GuildLink.objects.filter(guild=guild).count() == 1

        page.reload()
        page.wait_for_function(ALPINE_READY)
        expect(page.locator("#link-rows [data-formset-row]")).to_have_count(1)
        expect(page.locator('input[name="links-0-label"]')).to_have_value("Discord")

    def it_holds_a_boosted_leave_behind_a_kept_delete_until_it_lands(live_server, page, login_via_code):
        guild, wiki, url = _flag_a_delete_behind_a_bad_row(page, live_server, login_via_code)

        # The boosted link is held; with the delete still waiting the page asks, and Stay keeps it.
        page.get_by_role("link", name="Back to Guild Page").click()
        dialog = page.get_by_role("dialog").filter(has_text="A change did not save")
        expect(dialog).to_be_visible()
        dialog.get_by_role("button", name="Stay").click()
        expect(dialog).to_be_hidden()
        assert "/edit/" in page.url
        assert GuildLink.objects.filter(pk=wiki.pk).exists()

        # Fixing the URL lands the form and the delete with it.
        before = _saves(page)
        url.fill("https://example.com/docs")
        url.press("Tab")
        _wait_saved(page, before + 1)
        assert not GuildLink.objects.filter(pk=wiki.pk).exists()
        assert GuildLink.objects.filter(guild=guild, label="Docs").exists()

    def it_refuses_a_bad_link_inline_keeps_what_was_typed_and_deletes_a_saved_link(live_server, page, login_via_code):
        guild = _admin_guild()
        discord = GuildLinkFactory(guild=guild, label="Discord", url="https://discord.gg/ceramics")
        _sign_in_as_admin(login_via_code)
        _open(page, live_server, guild, "links")

        # A new row with a bad URL: the error shows under the field, the value stays, nothing saves.
        page.get_by_role("button", name="+ Add a link").click()
        page.locator('input[name="links-1-label"]').fill("Docs")
        url = page.locator('input[name="links-1-url"]')
        url.fill("not a url")
        url.press("Tab")
        page.locator("#link-rows .pl-field-error").wait_for()
        expect(url).to_have_value("not a url")
        assert GuildLink.objects.filter(guild=guild).count() == 1
        assert page.locator('input[name="links-1-id"]').input_value() == ""

        # Fixing it saves the row, stamps its id and clears the error.
        before = _saves(page)
        url.fill("https://example.com/docs")
        url.press("Tab")
        _wait_saved(page, before + 1)
        expect(page.locator('input[name="links-1-id"]')).not_to_have_value("")
        expect(page.locator("#link-rows .pl-field-error")).to_have_count(0)
        assert GuildLink.objects.filter(guild=guild, label="Docs").exists()

        # Delete the saved link: the row goes, and so does the database row.
        before = _saves(page)
        _link_row(page, discord).get_by_role("button", name="Delete this link").click()
        _wait_saved(page, before + 1)
        expect(page.locator("#link-rows [data-formset-row]")).to_have_count(1)
        assert not GuildLink.objects.filter(pk=discord.pk).exists()

        page.reload()
        page.wait_for_function(ALPINE_READY)
        expect(page.locator("#link-rows [data-formset-row]")).to_have_count(1)
        expect(page.locator('input[name="links-0-label"]')).to_have_value("Docs")

    def it_creates_a_faq_question_once_and_edits_it_in_place(live_server, page, login_via_code):
        guild = _admin_guild()
        _sign_in_as_admin(login_via_code)
        _open(page, live_server, guild, "content")

        # The question alone is a half typed row: the form posts it as rendered and skips it.
        page.get_by_role("button", name="+ Add a question").click()
        question = page.locator('input[name="faq-0-question"]')
        question.fill("What should I bring?")
        _wait_saved(page, 1)
        assert GuildFAQItem.objects.filter(guild=guild).count() == 0
        assert page.locator('input[name="faq-0-id"]').input_value() == ""

        # A document picked on the half typed row goes nowhere yet (as rendered, the row posts
        # exactly what it did a moment ago, so nothing is sent) and stays picked: only a file
        # that went out is cleared after a save.
        page.locator('input[name="faq-0-document"]').set_input_files(
            {"name": "packing-list.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 packing list"}
        )
        page.wait_for_function(QUEUE_IDLE)
        assert GuildFAQItem.objects.filter(guild=guild).count() == 0
        assert page.evaluate("() => document.querySelector('input[name=\"faq-0-document\"]').files.length") == 1

        # The answer completes it: one row, with its id on the page and the document on the item.
        before = _saves(page)
        answer = page.locator('textarea[name="faq-0-answer"]')
        answer.fill("Closed toe shoes and an apron.")
        answer.press("Tab")
        _wait_saved(page, before + 1)
        expect(page.locator('input[name="faq-0-id"]')).not_to_have_value("")
        item = GuildFAQItem.objects.get(guild=guild)
        assert item.document_display_name.endswith(".pdf")
        assert page.evaluate("() => document.querySelector('input[name=\"faq-0-document\"]').files.length") == 0

        # Editing it again updates that row rather than adding a second one.
        before = _saves(page)
        question.fill("What do I need to bring?")
        question.press("Tab")
        _wait_saved(page, before + 1)
        page.reload()
        page.wait_for_function(ALPINE_READY)
        expect(page.locator("#faq-rows [data-formset-row]")).to_have_count(1)
        expect(page.locator('input[name="faq-0-question"]')).to_have_value("What do I need to bring?")
        assert list(GuildFAQItem.objects.filter(guild=guild).values_list("question", flat=True)) == [
            "What do I need to bring?"
        ]

    def it_saves_the_member_suggestions_toggle_at_once(live_server, page, login_via_code):
        guild = _admin_guild()
        _sign_in_as_admin(login_via_code)
        _open(page, live_server, guild, "announcements")

        box = page.locator("#id_allow_member_announcement_suggestions")
        expect(box).not_to_be_checked()
        page.locator("label.pl-toggle", has=box).click()
        _wait_saved(page, 1)
        guild.refresh_from_db()
        assert guild.allow_member_announcement_suggestions is True

        page.reload()
        page.wait_for_function(ALPINE_READY)
        expect(page.locator("#id_allow_member_announcement_suggestions")).to_be_checked()

    def it_saves_the_reservations_toggle_and_the_guild_page_gains_the_tab(live_server, page, login_via_code):
        guild = _admin_guild()
        EquipmentFactory(name="Pottery Wheel Seven", guild=guild)
        _sign_in_as_admin(login_via_code)
        _open(page, live_server, guild, "reservations")

        box = page.locator("#id_show_reservations_tab")
        expect(box).not_to_be_checked()
        page.locator("label.pl-toggle", has=box).click()
        _wait_saved(page, 1)
        guild.refresh_from_db()
        assert guild.show_reservations_tab is True

        page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[guild.slug])}")
        page.get_by_role("button", name="Reservations", exact=True).click()
        card = page.locator("[data-guild-reservations] .pl-equip-card", has_text="Pottery Wheel Seven")
        expect(card).to_be_visible()

    def it_lands_the_save_when_the_member_types_and_leaves_at_once(live_server, page, login_via_code):
        guild = _admin_guild()
        _sign_in_as_admin(login_via_code)

        # A boosted in page link: the request is held while the flushed save runs, then resumes.
        _open(page, live_server, guild, "basic")
        page.locator("#id_about").fill("Left by the back link.")
        page.get_by_role("link", name="Back to Guild Page").click()
        page.wait_for_url(re.compile(re.escape(reverse("hub_guild_detail", args=[guild.slug]))))
        _wait_for_about(guild, "Left by the back link.")

        # A hard sidebar link: the save is flushed with keepalive, the browser asks, Leave goes.
        _open(page, live_server, guild, "basic")
        page.locator("#id_about").fill("Left by the sidebar.")
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator(".hub-sidebar__nav a").first.click()
        page.wait_for_url(lambda url: "/edit/" not in url)
        _wait_for_about(guild, "Left by the sidebar.")

    def it_lands_an_orientations_page_save_when_the_member_types_and_leaves_at_once(live_server, page, login_via_code):
        guild = _admin_guild()
        _sign_in_as_admin(login_via_code)

        # A boosted in page link: the request is held while the flushed save runs, then resumes.
        _open_orientations(page, live_server, guild)
        page.locator("#id_thankyou_email_subject").fill("Left by the back link")
        page.get_by_role("link", name="Back to Ceramics Guild Settings").click()
        page.wait_for_url(re.compile(re.escape(reverse("hub_guild_edit", args=[guild.pk])) + "$"))
        _wait_for_thankyou_subject(guild, "Left by the back link")

        # A hard sidebar link: the save is flushed with keepalive, the browser asks, Leave goes.
        _open_orientations(page, live_server, guild)
        page.locator("#id_thankyou_email_subject").fill("Left by the sidebar")
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator(".hub-sidebar__nav a").first.click()
        page.wait_for_url(lambda url: reverse("hub_guild_orientations", args=[guild.pk]) not in url)
        _wait_for_thankyou_subject(guild, "Left by the sidebar")

    def it_keeps_a_photo_dropped_while_the_rows_first_save_is_in_flight(live_server, page, login_via_code):
        guild = _admin_guild()
        # A hidden guild (#732): the Add an Orientation page does not offer it, so the card's add adds a row here.
        _hide(guild)
        _sign_in_as_admin(login_via_code)
        _open_orientations(page, live_server, guild)
        # The name's save is held open, so the photo lands while it is in flight: its answer must
        # not clear a file it never sent.
        page.evaluate(HOLD_SAVES)

        page.locator("#otypes-form [data-formset-add]").click()
        page.locator('input[name="otypes-0-name"]').fill("Wheel basics")
        page.locator('input[name="otypes-0-duration_minutes"]').fill("60")
        page.wait_for_function("() => window.plHeldSaves > 0")
        page.evaluate(DROP_PNG, "image-upload-zone-id_otypes-0-photo")
        page.evaluate("() => window.plReleaseSaves()")

        expect(page.locator("#image-preview-id_otypes-0-photo img")).to_have_attribute(
            "src", re.compile(r"^data:image/png")
        )
        expect(page.locator("#image-upload-zone-id_otypes-0-photo .cls-image-upload-label")).to_have_text(
            "Replace image"
        )
        _wait_for_photo(guild, "Wheel basics")

    def it_clears_a_sent_photo_even_when_the_save_renumbers_the_rows(live_server, page, login_via_code):
        # A blank new row ahead of a saved one swaps places when the answer renumbers them; the
        # sent photo must still be cleared, or the next edit uploads it again.
        guild = _admin_guild()
        # A hidden guild (#732): the Add an Orientation page does not offer it, so the card's add adds a row here.
        _hide(guild)
        _sign_in_as_admin(login_via_code)
        _open_orientations(page, live_server, guild)

        add = page.locator("#otypes-form").get_by_role("button", name="Add New Orientation +")
        add.click()
        add.click()
        # The photo waits on the half typed row; filling the name sends both in the save whose
        # answer renumbers the rows.
        photo = page.locator("#otypes-form [data-formset-row]").nth(1).locator('input[type="file"]')
        photo.set_input_files({"name": "glaze.png", "mimeType": "image/png", "buffer": _PNG})
        page.locator('input[name="otypes-1-name"]').fill("Glaze basics")

        _wait_for_photo(guild, "Glaze basics")
        page.wait_for_function(QUEUE_IDLE)
        expect(photo).to_have_attribute("name", "otypes-0-photo")
        assert photo.evaluate("(input) => input.files.length") == 0
