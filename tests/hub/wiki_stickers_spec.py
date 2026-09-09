"""BDD specs for the wiki's sticker surfaces (spec A, PR A4).

The ``/m/<code>/`` scan route and its dead end, the per-page QR download, the "Share This
Page" card on the reading page, and the printable sticker sheet.

The signed-out redirect is asserted on its exact query string on purpose: whether a scan
carries the page through login is the single detail that decides whether a sticker on a
machine works at all.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration
from membership.models import Member, WikiPage
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    MembershipPlanFactory,
    WikiPageFactFactory,
    WikiPageFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _wiki_on(db):
    """Every spec here runs with the wiki turned on; the flag-off case is explicit."""
    config = SiteConfiguration.load()
    config.wiki_enabled = True
    config.save()
    return config


def _member_user(username: str, *, fog_role: str = Member.FogRole.MEMBER, status: str = Member.Status.ACTIVE) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = status
    member.full_legal_name = username.title()
    member.save()
    member.sync_user_permissions()
    return user


def _login(client: Client, username: str, **kwargs: str) -> User:
    user = _member_user(username, **kwargs)
    client.login(username=username, password="pass")
    return user


def describe_the_sticker_scan_route():
    def describe_a_signed_in_member():
        def it_lands_on_the_page(client, db):
            _login(client, "scanner@example.com")
            page = WikiPageFactory(title="Table Saw")
            response = client.get(reverse("hub_wiki_qr", args=[page.qr_code]))
            assert response.status_code == 302
            assert response["Location"] == page.get_absolute_url()

        def it_resolves_a_lower_cased_code(client, db):
            # A phone camera that lower-cases the path must still reach the page.
            _login(client, "scanner@example.com")
            page = WikiPageFactory(title="Table Saw")
            response = client.get(reverse("hub_wiki_qr", args=[page.qr_code.lower()]))
            assert response["Location"] == page.get_absolute_url()

        def it_still_resolves_for_an_archived_page(client, db):
            # The URL keeps working; the page explains itself to its author.
            _login(client, "scanner@example.com")
            page = WikiPageFactory(title="Table Saw", archived=True)
            response = client.get(reverse("hub_wiki_qr", args=[page.qr_code]))
            assert response["Location"] == page.get_absolute_url()

    def describe_a_signed_out_scan():
        def it_carries_the_page_through_the_login(client, db):
            page = WikiPageFactory(title="Table Saw")
            response = client.get(reverse("hub_wiki_qr", args=[page.qr_code]))
            assert response.status_code == 302
            assert response["Location"] == f"{reverse('account_login')}?next={page.get_absolute_url()}"

        def it_sends_an_unknown_code_through_login_too(client, db):
            # One hop later they are signed in and get the written explanation, instead of
            # the member shell being rendered to the street.
            WikiPageFactory(title="Table Saw")
            response = client.get(reverse("hub_wiki_qr", args=["ZZZZZZ"]))
            assert response.status_code == 302
            assert response["Location"] == f"{reverse('account_login')}?next=/m/ZZZZZZ/"

    def describe_an_unknown_code():
        def it_answers_404(client, db):
            _login(client, "scanner@example.com")
            assert client.get(reverse("hub_wiki_qr", args=["ZZZZZZ"])).status_code == 404

        def it_renders_the_wikis_own_dead_end_and_not_the_site_wide_404(client, db):
            _login(client, "scanner@example.com")
            response = client.get(reverse("hub_wiki_qr", args=["ZZZZZZ"]))
            assert b"No Page For That Sticker" in response.content
            assert b"That sticker points at a page that has moved" in response.content
            # The site-wide 404's copy, which get_object_or_404 would have rendered.
            assert b"Browse Past Lives classes" not in response.content

        def it_offers_a_search_box(client, db):
            _login(client, "scanner@example.com")
            response = client.get(reverse("hub_wiki_qr", args=["ZZZZZZ"]))
            assert reverse("hub_wiki_search").encode() in response.content

    def describe_the_feature_flag():
        def it_404s_a_known_code_while_the_wiki_is_off(client, db, _wiki_on):
            _login(client, "scanner@example.com")
            page = WikiPageFactory(title="Table Saw")
            _wiki_on.wiki_enabled = False
            _wiki_on.save()
            assert client.get(reverse("hub_wiki_qr", args=[page.qr_code])).status_code == 404

        def it_404s_for_a_signed_out_scan_too(client, db, _wiki_on):
            page = WikiPageFactory(title="Table Saw")
            _wiki_on.wiki_enabled = False
            _wiki_on.save()
            assert client.get(reverse("hub_wiki_qr", args=[page.qr_code])).status_code == 404

    def describe_the_public_book_surface():
        def it_does_not_resolve_there(client, db, settings):
            # /m/ joins MEMBER_ONLY_PATH_PREFIXES, so the sticker link is reachable
            # pre-login on the members host but does not exist on the book surface.
            page = WikiPageFactory(title="Table Saw")
            settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "book.testserver"]
            settings.PUBLIC_HOSTS = ["book.testserver"]
            response = client.get(reverse("hub_wiki_qr", args=[page.qr_code]), HTTP_HOST="book.testserver")
            assert response.status_code == 404


def describe_the_page_qr_download():
    @pytest.fixture
    def page(db):
        return WikiPageFactory(title="Table Saw")

    def it_returns_svg_by_default(client, db, page):
        _login(client, "editor@example.com")
        response = client.get(reverse("hub_wiki_qr_download", args=[page.slug]))
        assert response["Content-Type"] == "image/svg+xml"
        assert response["Content-Disposition"] == f'attachment; filename="{page.slug}-qr.svg"'

    def it_returns_png_when_asked(client, db, page):
        _login(client, "editor@example.com")
        response = client.get(reverse("hub_wiki_qr_download", args=[page.slug]) + "?fmt=png")
        assert response["Content-Type"] == "image/png"
        assert response.content[:4] == b"\x89PNG"

    def it_encodes_the_sticker_link(client, db, page):
        _login(client, "editor@example.com")
        response = client.get(reverse("hub_wiki_qr_download", args=[page.slug]))
        # The QR is generated from sticker_url, so the two can never disagree; assert the
        # SVG is the same bytes the model would produce for that link.
        assert response.content.decode() == page.qr_svg()

    def it_404s_an_unknown_format(client, db, page):
        _login(client, "editor@example.com")
        assert client.get(reverse("hub_wiki_qr_download", args=[page.slug]) + "?fmt=pdf").status_code == 404

    def it_404s_an_unknown_slug(client, db, page):
        _login(client, "editor@example.com")
        assert client.get(reverse("hub_wiki_qr_download", args=["no-such-page"])).status_code == 404

    def it_403s_a_member_on_an_official_page(client, db):
        official = WikiPageFactory(title="Shop Rules", official=True)
        _login(client, "member@example.com")
        assert client.get(reverse("hub_wiki_qr_download", args=[official.slug])).status_code == 403

    def it_403s_a_former_member(client, db, page):
        _login(client, "lapsed@example.com", status=Member.Status.FORMER)
        assert client.get(reverse("hub_wiki_qr_download", args=[page.slug])).status_code == 403

    def it_404s_while_the_wiki_is_off(client, db, page, _wiki_on):
        _login(client, "editor@example.com")
        _wiki_on.wiki_enabled = False
        _wiki_on.save()
        assert client.get(reverse("hub_wiki_qr_download", args=[page.slug])).status_code == 404


def describe_the_share_this_page_card():
    def it_renders_for_a_member_who_may_edit(client, db):
        page = WikiPageFactory(title="Table Saw")
        _login(client, "editor@example.com")
        response = client.get(page.get_absolute_url())
        assert b"Share This Page" in response.content

    def it_shows_the_sticker_link(client, db):
        page = WikiPageFactory(title="Table Saw")
        _login(client, "editor@example.com")
        response = client.get(page.get_absolute_url())
        assert page.sticker_url.encode() in response.content

    def it_links_both_download_formats(client, db):
        page = WikiPageFactory(title="Table Saw")
        _login(client, "editor@example.com")
        response = client.get(page.get_absolute_url())
        download = reverse("hub_wiki_qr_download", args=[page.slug])
        assert f'href="{download}?fmt=svg"'.encode() in response.content
        assert f'href="{download}?fmt=png"'.encode() in response.content

    def it_gives_the_two_copies_distinct_field_ids(client, db):
        # It renders twice under the width swap (wide, and inside the phone Details
        # disclosure). Two inputs sharing an id would break the <label for> association.
        page = WikiPageFactory(title="Table Saw")
        _login(client, "editor@example.com")
        body = client.get(page.get_absolute_url()).content
        assert body.count(b'id="qr-share-url"') == 1
        assert body.count(b'id="qr-share-url-phone"') == 1

    def it_is_absent_on_an_official_page_for_a_member(client, db):
        # An Official page gives a member no edit affordance at all, and the QR download
        # behind this card is gated the same way.
        official = WikiPageFactory(title="Shop Rules", official=True)
        _login(client, "member@example.com")
        response = client.get(official.get_absolute_url())
        assert b"Share This Page" not in response.content

    def it_is_absent_on_an_archived_page_for_a_member(client, db):
        archived = WikiPageFactory(title="Old Saw", archived=True)
        _login(client, "member@example.com")
        response = client.get(archived.get_absolute_url())
        assert b"Share This Page" not in response.content

    def it_is_absent_on_an_archived_page_for_a_moderator(client, db):
        # The one who actually has can_edit on an archived page. Every other affordance is
        # gone there, and inviting somebody to print a sticker for it is no different.
        archived = WikiPageFactory(title="Old Saw", archived=True)
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(archived.get_absolute_url())
        assert b"Share This Page" not in response.content

    def it_is_present_for_that_moderator_once_the_page_is_live(client, db):
        live = WikiPageFactory(title="Table Saw")
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(live.get_absolute_url())
        assert b"Share This Page" in response.content


def describe_the_sticker_sheet():
    @pytest.fixture
    def machine_page(db):
        tool = EquipmentFactory(name="Table Saw", guild=GuildFactory(name="Woodworking"))
        return WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.MACHINE, equipment=tool, guild=tool.guild)

    def it_renders_one_cell_per_machine_page(client, db, machine_page):
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_stickers"))
        assert response.status_code == 200
        assert response.content.count(b'class="pl-wp-sticker"') == 1

    def it_prints_the_machine_name(client, db, machine_page):
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_stickers"))
        assert b"Table Saw" in response.content

    def it_prints_the_typed_short_link(client, db, machine_page):
        # A member with a dead phone battery has to be able to read it and type it.
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_stickers"))
        assert machine_page.sticker_url_typed.encode() in response.content

    def it_carries_its_own_print_stylesheet_and_no_member_chrome(client, db, machine_page):
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_stickers"))
        assert b"wiki-stickers.css" in response.content
        assert b"hub-sidebar" not in response.content

    def it_offers_a_real_print_button(client, db, machine_page):
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_stickers"))
        assert b"Print / Save as PDF" in response.content

    def it_shows_only_machine_pages_by_default(client, db, machine_page):
        WikiPageFactory(title="Finishing Walnut", kind=WikiPage.Kind.HOWTO)
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_stickers"))
        assert b"Finishing Walnut" not in response.content

    def it_narrows_to_one_guild(client, db, machine_page):
        other_tool = EquipmentFactory(name="Kiln", guild=GuildFactory(name="Ceramics"))
        WikiPageFactory(title="Kiln", kind=WikiPage.Kind.MACHINE, equipment=other_tool, guild=other_tool.guild)
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_stickers") + "?guild=woodworking")
        assert b"Table Saw" in response.content
        assert b"Kiln" not in response.content

    def it_can_be_asked_for_another_kind(client, db, machine_page):
        WikiPageFactory(title="Finishing Walnut", kind=WikiPage.Kind.HOWTO)
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_stickers") + "?kind=howto")
        assert b"Finishing Walnut" in response.content
        assert b"Table Saw" not in response.content

    def it_falls_back_to_machines_on_an_unknown_kind(client, db, machine_page):
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_stickers") + "?kind=nonsense")
        assert b"Table Saw" in response.content

    def it_leaves_archived_pages_off_the_sheet(client, db):
        tool = EquipmentFactory(name="Old Lathe")
        WikiPageFactory(title="Old Lathe", kind=WikiPage.Kind.MACHINE, equipment=tool, archived=True)
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_stickers"))
        assert b"Old Lathe" not in response.content

    def describe_when_nothing_matches():
        def it_says_to_run_the_seed_first(client, db):
            _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
            response = client.get(reverse("hub_wiki_stickers"))
            assert b"Ask an admin to run the equipment seed" in response.content

        def it_says_the_same_for_an_unknown_guild(client, db, machine_page):
            _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
            response = client.get(reverse("hub_wiki_stickers") + "?guild=no-such-guild")
            assert response.status_code == 200
            assert b"Ask an admin to run the equipment seed" in response.content

    def describe_the_gate():
        def it_403s_a_plain_member(client, db, machine_page):
            _login(client, "member@example.com")
            assert client.get(reverse("hub_wiki_stickers")).status_code == 403

        def it_403s_a_guild_lead(client, db, machine_page):
            # The sheet is staff-wide and has no page in scope, so it takes
            # is_effective_staff rather than a per-page moderation check.
            user = _login(client, "lead@example.com")
            guild = GuildFactory(name="Metal")
            guild.guild_lead = user.member
            guild.save()
            assert client.get(reverse("hub_wiki_stickers")).status_code == 403

        def it_lets_an_officer_in(client, db, machine_page):
            _login(client, "officer@example.com", fog_role=Member.FogRole.GUILD_OFFICER)
            assert client.get(reverse("hub_wiki_stickers")).status_code == 200

        def it_404s_while_the_wiki_is_off(client, db, machine_page, _wiki_on):
            _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
            _wiki_on.wiki_enabled = False
            _wiki_on.save()
            assert client.get(reverse("hub_wiki_stickers")).status_code == 404


def describe_the_wiki_homes_link_to_the_sheet():
    @pytest.fixture
    def machine_page(db):
        tool = EquipmentFactory(name="Table Saw")
        return WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.MACHINE, equipment=tool)

    def it_shows_staff_the_way_to_print(client, db, machine_page):
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_home"))
        assert b"Print stickers" in response.content

    def it_is_invisible_to_a_plain_member(client, db, machine_page):
        _login(client, "member@example.com")
        response = client.get(reverse("hub_wiki_home"))
        assert b"Print stickers" not in response.content


def describe_the_sticker_link_on_the_model():
    def it_points_at_the_member_host(db, settings):
        settings.MEMBER_BASE_URL = "https://members.example.test"
        page = WikiPageFactory(title="Table Saw")
        assert page.sticker_url == f"https://members.example.test/m/{page.qr_code}/"

    def it_drops_the_scheme_for_the_typed_form(db, settings):
        settings.MEMBER_BASE_URL = "https://members.example.test"
        page = WikiPageFactory(title="Table Saw")
        assert page.sticker_url_typed == f"members.example.test/m/{page.qr_code}/"

    def it_renders_a_scalable_svg(db):
        page = WikiPageFactory(title="Table Saw")
        svg = page.qr_svg()
        assert svg.startswith("<svg")
        assert "viewBox=" in svg

    def it_renders_png_bytes(db):
        assert WikiPageFactory(title="Table Saw").qr_png_bytes()[:4] == b"\x89PNG"


def describe_a_seeded_stub_on_the_reading_page():
    def it_says_a_prompt_has_no_answer_yet(client, db):
        # The seeder writes the starter prompts with no answer; an empty cell reads as
        # broken, so the page says so instead.
        page = WikiPageFactory(title="Table Saw")
        WikiPageFactFactory(page=page, label="Blade or bit", value="")
        _login(client, "reader@example.com")
        response = client.get(page.get_absolute_url())
        assert b"Not filled in yet" in response.content

    def it_shows_a_real_answer_plainly(client, db):
        page = WikiPageFactory(title="Table Saw")
        WikiPageFactFactory(page=page, label="Blade or bit", value="40 tooth")
        _login(client, "reader@example.com")
        response = client.get(page.get_absolute_url())
        assert b"40 tooth" in response.content
        assert b"Not filled in yet" not in response.content


def describe_a_seeded_stub_body():
    """The seeder fills every stub with the starter's headings, so `page.body` alone is
    truthy on a page nobody has written a word of. Without the body-clock gate the reading
    page showed four empty headings and a TOC pointing at nothing — on every machine page
    reached from a sticker, on launch day."""

    def it_shows_the_invitation_and_not_the_empty_headings(client, db):
        stub = WikiPageFactory(title="Table Saw", body="<h2>What It Does</h2><p></p>", seeded=True)
        _login(client, "reader@example.com")
        response = client.get(stub.get_absolute_url())
        assert b"Nobody has written this one yet" in response.content
        assert b"What It Does" not in response.content

    def it_hides_the_table_of_contents(client, db):
        stub = WikiPageFactory(
            title="Table Saw",
            body="<h2>What It Does</h2><p></p><h2>How To Use It</h2><p></p>",
            seeded=True,
        )
        _login(client, "reader@example.com")
        response = client.get(stub.get_absolute_url())
        # The scrollspy script names the active-chip class either way, so assert on the nav.
        assert b'<nav class="pl-wp-toc"' not in response.content

    def it_offers_an_editor_the_way_in(client, db):
        stub = WikiPageFactory(title="Table Saw", body="<h2>What It Does</h2><p></p>", seeded=True)
        _login(client, "editor@example.com")
        response = client.get(stub.get_absolute_url())
        assert b"+ Add What You Know" in response.content

    def it_shows_the_prose_once_a_person_has_written(client, db):
        written = WikiPageFactory(title="Table Saw", body="<h2>What It Does</h2><p>It rips boards.</p>")
        _login(client, "reader@example.com")
        response = client.get(written.get_absolute_url())
        assert b"It rips boards." in response.content
        assert b"Nobody has written this one yet" not in response.content

    def it_returns_the_prose_after_the_first_tip(client, db):
        # add_tip goes through apply_edit, which stamps the body clock.
        stub = WikiPageFactory(title="Table Saw", body="<h2>What It Does</h2><p></p>", seeded=True)
        member = _login(client, "editor@example.com").member
        stub.add_tip(member=member, editor_may_verify=False, tip_html="<p>Mind the riving knife.</p>")
        response = client.get(stub.get_absolute_url())
        assert b"Mind the riving knife." in response.content
        assert b"Nobody has written this one yet" not in response.content


def describe_the_quick_answers_on_a_seeded_stub():
    def it_nudges_someone_to_answer_the_prompts(client, db):
        page = WikiPageFactory(title="Table Saw", seeded=True)
        WikiPageFactFactory(page=page, label="Blade or bit", value="")
        WikiPageFactFactory(page=page, label="Max size", value="")
        _login(client, "editor@example.com")
        response = client.get(page.get_absolute_url())
        assert b"Nobody has answered these yet" in response.content
        assert b"Fill them in" in response.content

    def it_drops_the_nudge_once_one_is_answered(client, db):
        page = WikiPageFactory(title="Table Saw", seeded=True)
        WikiPageFactFactory(page=page, label="Blade or bit", value="40 tooth")
        WikiPageFactFactory(page=page, label="Max size", value="")
        _login(client, "editor@example.com")
        response = client.get(page.get_absolute_url())
        assert b"Nobody has answered these yet" not in response.content

    def it_offers_a_member_who_cannot_edit_no_link(client, db):
        official = WikiPageFactory(title="Shop Rules", official=True, seeded=True)
        WikiPageFactFactory(page=official, label="Who to ask", value="")
        _login(client, "member@example.com")
        response = client.get(official.get_absolute_url())
        assert b"Nobody has answered these yet" in response.content
        assert b"Fill them in" not in response.content


def describe_the_sticker_sheet_and_held_back_pages():
    def it_keeps_a_page_the_safety_gate_is_holding_off_the_sheet(client, db):
        # visible_wiki_pages hands effective staff everything, so .published() is the only
        # thing stopping an unreviewed page being printed and taped to a wall.
        tool = EquipmentFactory(name="Table Saw")
        WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.MACHINE, equipment=tool, is_published=False)
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_stickers"))
        assert b"Table Saw" not in response.content
        assert b"Ask an admin to run the equipment seed" in response.content

    def it_narrows_to_space_wide_pages(client, db):
        # The wiki home speaks ?guild=space-wide; the sheet reads the same parameter.
        guild_tool = EquipmentFactory(name="Table Saw", guild=GuildFactory(name="Woodworking"))
        WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.MACHINE, equipment=guild_tool, guild=guild_tool.guild)
        WikiPageFactory(title="Air Compressor", kind=WikiPage.Kind.MACHINE, guild=None)
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_stickers") + "?guild=space-wide")
        assert b"Air Compressor" in response.content
        assert b"Table Saw" not in response.content

    def it_does_not_speak_the_enum_label_in_the_empty_state(client, db):
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_stickers") + "?kind=howto")
        assert b"No pages of that kind yet" in response.content
        # Only the machine sheet can honestly point at the equipment seeder.
        assert b"run the equipment seed" not in response.content

    def it_names_the_page_for_a_screen_reader_without_printing_it(client, db):
        tool = EquipmentFactory(name="Table Saw")
        WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.MACHINE, equipment=tool)
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_stickers"))
        assert b'<h1 class="pl-stickers-title">Wiki Stickers' in response.content
        # The heading sits inside the no-print toolbar, so it costs no printed sheet space.
        head, _, _rest = response.content.partition(b"pl-stickers-sheet")
        assert b"no-print" in head


def describe_the_print_stickers_link():
    def it_carries_the_current_guild_filter(client, db):
        guild = GuildFactory(name="Woodworking")
        tool = EquipmentFactory(name="Table Saw", guild=guild)
        WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.MACHINE, equipment=tool, guild=guild)
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_home") + "?guild=woodworking")
        assert f"{reverse('hub_wiki_stickers')}?guild=woodworking".encode() in response.content

    def it_carries_a_space_wide_filter(client, db):
        # Both surfaces speak this parameter; the link used to drop it, which left the
        # sheet's space-wide branch unreachable from anywhere in the app.
        tool = EquipmentFactory(name="Table Saw")
        WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.MACHINE, equipment=tool, guild=None)
        _login(client, "officer@example.com", fog_role=Member.FogRole.ADMIN)
        response = client.get(reverse("hub_wiki_home") + "?guild=space-wide")
        assert f"{reverse('hub_wiki_stickers')}?guild=space-wide".encode() in response.content
