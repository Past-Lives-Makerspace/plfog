"""BDD specs for the Help page hub views, nav slots (Help + external Wiki), and footer links."""

from __future__ import annotations

import io
import json

import pytest
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from core.models import SiteConfiguration
from hub.forms import WikiArticleForm
from membership.models import HelpCategory, Member, OrgFAQItem, OrgInfoPage, OrgLink, WikiArticle
from tests.membership.factories import (
    HelpCategoryFactory,
    MembershipPlanFactory,
    OrgFAQItemFactory,
    OrgLinkFactory,
    WikiArticleFactory,
)

pytestmark = pytest.mark.django_db

_MEMBER_GUIDE_URL = "https://docs.google.com/document/d/1snMD2H2APfNR3MdwSmEuxTLIiTODHpefjJsLfb29HjQ/edit"
_CODE_OF_CONDUCT_URL = "https://docs.google.com/document/d/1avWCAnbwDbO79k-n-_QpUc0P2Dz-s6f4/edit"


def _image_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 120, 120)).save(buf, format="PNG")
    return buf.getvalue()


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member  # auto-linked via signal
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _faq_payload(question: str, answer: str, *, video_url: str = "") -> dict:
    return {
        "faq-TOTAL_FORMS": "1",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
        "faq-0-question": question,
        "faq-0-answer": answer,
        "faq-0-video_url": video_url,
        "faq-0-document_url": "",
        "faq-0-sort_order": "0",
    }


def _link_payload(label: str, url: str) -> dict:
    return {
        "links-TOTAL_FORMS": "1",
        "links-INITIAL_FORMS": "0",
        "links-MIN_NUM_FORMS": "0",
        "links-MAX_NUM_FORMS": "1000",
        "links-0-label": label,
        "links-0-url": url,
        "links-0-sort_order": "0",
    }


def _category_payload(name: str, *, slug: str = "", audience: str = "member", description: str = "") -> dict:
    return {
        "categories-TOTAL_FORMS": "1",
        "categories-INITIAL_FORMS": "0",
        "categories-MIN_NUM_FORMS": "0",
        "categories-MAX_NUM_FORMS": "1000",
        "categories-0-id": "",
        "categories-0-name": name,
        "categories-0-slug": slug,
        "categories-0-audience": audience,
        "categories-0-description": description,
        "categories-0-sort_order": "0",
    }


def _article_payload(title: str, body: str, *, slug: str = "", is_published: str = "on") -> dict:
    payload = {
        "articles-TOTAL_FORMS": "1",
        "articles-INITIAL_FORMS": "0",
        "articles-MIN_NUM_FORMS": "0",
        "articles-MAX_NUM_FORMS": "1000",
        "articles-0-title": title,
        "articles-0-slug": slug,
        "articles-0-body": body,
        "articles-0-sort_order": "0",
    }
    if is_published:
        payload["articles-0-is_published"] = is_published
    return payload


def describe_org_info_read_page():
    def it_is_accessible_to_anonymous_guests(client: Client):
        assert client.get(reverse("hub_help")).status_code == 200

    def it_is_accessible_to_a_member(client: Client):
        _user_with_role("m_read")
        client.login(username="m_read", password="pass")
        assert client.get(reverse("hub_help")).status_code == 200

    def it_redirects_to_home_when_the_help_page_is_disabled(client: Client):
        config = SiteConfiguration.load()
        config.help_page_enabled = False
        config.save()
        resp = client.get(reverse("hub_help"))
        assert resp.status_code == 302
        assert resp.url == reverse("hub_home")

    def it_does_not_carry_the_floor_plan_any_more(client: Client):
        """The map moved to Spaces — the Wiki must not quietly keep rendering it."""
        page = OrgInfoPage.load()
        page.floorplan_image = SimpleUploadedFile("m.png", _image_bytes(400, 200), content_type="image/png")
        page.save()
        resp = client.get(reverse("hub_help"))
        assert b"pl-org-map" not in resp.content
        assert b"facility map is coming soon" not in resp.content

    def it_hides_parking_when_blank(client: Client):
        assert b"Parking &amp; Arrival" not in client.get(reverse("hub_help")).content

    def it_shows_parking_when_set(client: Client):
        page = OrgInfoPage.load()
        page.parking = "Free street parking after 5pm."
        page.save()
        resp = client.get(reverse("hub_help"))
        assert b"Parking &amp; Arrival" in resp.content
        assert b"Free street parking after 5pm." in resp.content

    def it_hides_who_to_contact_when_blank(client: Client):
        assert b"Who to Contact" not in client.get(reverse("hub_help")).content

    def it_shows_who_to_contact_when_set(client: Client):
        page = OrgInfoPage.load()
        page.who_to_contact = "Billing: ask Sam."
        page.save()
        assert b"Billing: ask Sam." in client.get(reverse("hub_help")).content

    def it_shows_the_intro_when_set(client: Client):
        page = OrgInfoPage.load()
        page.intro = "Here is how our space works."
        page.save()
        assert b"Here is how our space works." in client.get(reverse("hub_help")).content

    def describe_code_of_conduct():
        def it_shows_a_resources_link_when_a_url_is_set(client: Client):
            page = OrgInfoPage.load()
            page.code_of_conduct_url = "https://example.com/coc"
            page.save()
            resp = client.get(reverse("hub_help"))
            # It's a resource link in the sidebar now, not its own body section.
            assert b'href="https://example.com/coc"' in resp.content
            assert b">Code of Conduct</a>" in resp.content
            assert b"Code of Conduct</h2>" not in resp.content

        def it_does_not_render_the_body_as_a_section(client: Client):
            page = OrgInfoPage.load()
            page.code_of_conduct = "Be excellent to each other."
            page.code_of_conduct_url = ""
            page.save()
            resp = client.get(reverse("hub_help"))
            assert b"Be excellent to each other." not in resp.content
            assert b"Code of Conduct</h2>" not in resp.content

        def it_hides_the_link_when_no_url_is_set(client: Client):
            page = OrgInfoPage.load()  # the migration seeds a CoC url — clear it for this case
            page.code_of_conduct = ""
            page.code_of_conduct_url = ""
            page.save()
            content = client.get(reverse("hub_help")).content
            assert b">Code of Conduct</a>" not in content
            assert b"Code of Conduct</h2>" not in content

    def it_shows_faq_items(client: Client):
        OrgFAQItemFactory(question="Who runs billing?")
        assert b"Who runs billing?" in client.get(reverse("hub_help")).content

    def it_hides_the_faq_section_when_empty(client: Client):
        assert b"pl-guild-faq__q" not in client.get(reverse("hub_help")).content

    def it_shows_resource_links(client: Client):
        OrgLinkFactory(label="Handbook", url="https://example.com/h")
        assert b"Handbook" in client.get(reverse("hub_help")).content

    def describe_the_all_guides_fallback_list():
        def it_lists_an_uncategorized_published_guide_with_its_canonical_link(client: Client):
            article = WikiArticleFactory(title="Guild voting", body="Rank three guilds.", is_published=True)
            resp = client.get(reverse("hub_help"))
            assert b"All guides" in resp.content
            assert f'href="{article.get_absolute_url()}"'.encode() in resp.content
            assert b"Rank three guilds." in resp.content

        def it_hides_a_draft_article_from_a_guest(client: Client):
            WikiArticleFactory(title="Secret draft", body="Not ready yet.", is_published=False)
            resp = client.get(reverse("hub_help"))
            assert b"Secret draft" not in resp.content
            assert b"Not ready yet." not in resp.content

        def it_hides_unlisted_articles(client: Client):
            WikiArticleFactory(title="Instructor orientation", slug="instructor-orientation", is_published=True)
            assert b"Instructor orientation" not in client.get(reverse("hub_help")).content

        def it_keeps_categorized_guides_off_the_fallback_list(client: Client):
            category = HelpCategoryFactory(name="Guilds")
            WikiArticleFactory(title="Guild voting", category=category, is_published=True)
            resp = client.get(reverse("hub_help"))
            assert b"All guides" not in resp.content

        def it_exposes_only_published_uncategorized_articles_in_the_context(client: Client):
            WikiArticleFactory(title="Live guide", is_published=True)
            WikiArticleFactory(title="Hidden guide", is_published=False)
            WikiArticleFactory(title="Categorized guide", category=HelpCategoryFactory(), is_published=True)
            resp = client.get(reverse("hub_help"))
            assert [a.title for a in resp.context["uncategorized"]] == ["Live guide"]

    def describe_the_category_grid():
        def it_groups_categories_under_audience_headings_in_rank_order(client: Client):
            for audience, name, sort in [
                (HelpCategory.Audience.ADMIN, "Admin tools", 10),
                (HelpCategory.Audience.GUILD_LEAD, "Running a guild", 20),
                (HelpCategory.Audience.INSTRUCTOR, "Teaching things", 30),
                (HelpCategory.Audience.MEMBER, "Getting started", 40),
            ]:
                WikiArticleFactory(category=HelpCategoryFactory(name=name, audience=audience, sort_order=sort))
            content = client.get(reverse("hub_help")).content
            positions = [
                content.index(b"For every member"),
                content.index(b"Teaching"),
                content.index(b"Running a guild"),
                content.index(b"Admin"),
            ]
            assert positions == sorted(positions)

        def it_renders_the_card_with_count_badge_and_category_link(client: Client):
            category = HelpCategoryFactory(name="Guilds", description="Voting and pages.")
            WikiArticleFactory(category=category)
            WikiArticleFactory(category=category)
            resp = client.get(reverse("hub_help"))
            assert f'href="{reverse("hub_help_category", args=[category.slug])}"'.encode() in resp.content
            assert b"2 guides" in resp.content
            assert b"Voting and pages." in resp.content
            assert b"pl-help-badge pl-help-badge--member" in resp.content

        def it_hides_a_category_with_no_published_guides(client: Client):
            HelpCategoryFactory(name="Empty category")
            draft_cat = HelpCategoryFactory(name="Drafts only")
            WikiArticleFactory(category=draft_cat, is_published=False)
            content = client.get(reverse("hub_help")).content
            assert b"Empty category" not in content
            assert b"Drafts only" not in content

        def it_lists_every_published_guide_as_a_link_inside_its_category_card(client: Client):
            category = HelpCategoryFactory(name="Guilds")
            first = WikiArticleFactory(title="Alpha guide", category=category, sort_order=10)
            second = WikiArticleFactory(title="Zed guide", category=category, sort_order=20)
            content = client.get(reverse("hub_help")).content
            assert f'href="{first.get_absolute_url()}"'.encode() in content
            assert f'href="{second.get_absolute_url()}"'.encode() in content
            assert content.index(b"Alpha guide") < content.index(b"Zed guide")

        def it_keeps_drafts_and_unlisted_guides_out_of_the_card_lists(client: Client):
            category = HelpCategoryFactory(name="Guilds")
            WikiArticleFactory(title="Live guide", category=category)
            WikiArticleFactory(title="Hidden draft", category=category, is_published=False)
            WikiArticleFactory(title="Instructor orientation", slug="instructor-orientation", category=category)
            content = client.get(reverse("hub_help")).content
            assert b"Live guide" in content
            assert b"Hidden draft" not in content
            assert b"Instructor orientation" not in content

        def it_does_not_grow_queries_with_more_categories_and_guides(client: Client):
            from django.db import connection
            from django.test.utils import CaptureQueriesContext

            WikiArticleFactory(category=HelpCategoryFactory(name="Cat A"))
            client.get(reverse("hub_help"))  # warm per-process caches (content types, site config)
            with CaptureQueriesContext(connection) as baseline:
                client.get(reverse("hub_help"))
            for name in ("Cat B", "Cat C", "Cat D"):
                category = HelpCategoryFactory(name=name)
                WikiArticleFactory(category=category)
                WikiArticleFactory(category=category)
            with CaptureQueriesContext(connection) as grown:
                client.get(reverse("hub_help"))
            assert len(grown) == len(baseline)

    def describe_the_hero():
        def it_renders_the_search_form_inside_the_hero_pointed_at_the_search_view(client: Client):
            content = client.get(reverse("hub_help")).content
            assert b"pl-help-hero" in content
            assert f'action="{reverse("hub_help_search")}"'.encode() in content
            assert content.index(b"pl-help-hero") < content.index(b"pl-help-hero__input")

        def it_uses_the_gradient_backdrop_when_no_banner_is_set(client: Client):
            content = client.get(reverse("hub_help")).content
            assert b"pl-guild-hero--noimg" in content

        def it_drops_the_intro_admonition_below_the_hero_keeping_the_lead_inside(client: Client):
            page = OrgInfoPage.load()
            page.intro = "Welcome to the hub.\n\n!!! tip\n    Ask around."
            page.save()
            content = client.get(reverse("hub_help")).content
            assert b"Welcome to the hub." in content
            assert b"admonition tip" in content
            # Lead renders inside the hero (before the search input); the tip after it.
            assert content.index(b"Welcome to the hub.") < content.index(b"pl-help-hero__input")
            assert content.index(b"admonition tip") > content.index(b"pl-help-hero__input")

    def describe_the_tours_card():
        def it_explains_what_a_tour_is_with_an_explainer_line_and_tooltip(client: Client):
            _user_with_role("tour_expl")
            client.login(username="tour_expl", password="pass")
            content = client.get(reverse("hub_help")).content.decode()
            assert "Interactive walkthroughs that highlight parts of the app, right on the page." in content
            assert "pl-help__bubble" in content
            assert "one step at a time" in content

        def it_marks_an_untaken_tour_with_an_empty_status_circle(client: Client):
            _user_with_role("tour_circle")
            client.login(username="tour_circle", password="pass")
            content = client.get(reverse("hub_help")).content.decode()
            assert "pl-tour-row__status" in content
            assert "pl-tour-row__status--done" not in content

    def describe_legacy_anchors():
        def it_maps_old_slugs_to_live_article_urls_and_drops_dead_targets(client: Client, monkeypatch):
            article = WikiArticleFactory(title="Getting oriented", slug="getting-oriented", is_published=True)
            monkeypatch.setattr(
                "membership.help_content.LEGACY_SLUG_MAP",
                {"orientations": "getting-oriented", "connecting-discord": "notifications"},
            )
            resp = client.get(reverse("hub_help"))
            assert resp.context["legacy_anchor_map"] == {"orientations": article.get_absolute_url()}
            assert b'id="help-legacy-anchors"' in resp.content

        def it_drops_targets_that_exist_but_are_unpublished(client: Client, monkeypatch):
            WikiArticleFactory(slug="getting-oriented", is_published=False)
            monkeypatch.setattr("membership.help_content.LEGACY_SLUG_MAP", {"orientations": "getting-oriented"})
            resp = client.get(reverse("hub_help"))
            assert resp.context["legacy_anchor_map"] == {}

        def it_emits_an_empty_map_by_default(client: Client):
            resp = client.get(reverse("hub_help"))
            assert resp.context["legacy_anchor_map"] == {}
            assert b'id="help-legacy-anchors"' in resp.content

    def it_shows_an_edit_button_for_an_admin(client: Client):
        _user_with_role("adm_edit_btn", fog_role=Member.FogRole.ADMIN)
        client.login(username="adm_edit_btn", password="pass")
        assert b"Edit this page" in client.get(reverse("hub_help")).content

    def it_hides_the_edit_button_from_a_member(client: Client):
        _user_with_role("m_no_edit_btn")
        client.login(username="m_no_edit_btn", password="pass")
        assert b"Edit this page" not in client.get(reverse("hub_help")).content


def describe_org_info_nav_and_folded_footer_links():
    def it_links_the_sidebar_to_both_the_spaces_and_help_pages(client: Client):
        _user_with_role("m_nav")
        client.login(username="m_nav", password="pass")
        resp = client.get(reverse("hub_home"))
        assert reverse("hub_spaces").encode() in resp.content
        assert reverse("hub_help").encode() in resp.content
        assert b"Spaces" in resp.content
        assert b"Help" in resp.content
        # The combined page is gone, so nothing should still navigate to it. (Its old
        # *label* still appears in the changelog modal, which is release history and
        # deliberately never rewritten — so assert on the link, not the words.)
        assert b'href="/info/"' not in resp.content

    def it_renders_an_external_wiki_link_in_the_member_nav(client: Client, settings):
        """The Wiki nav link points at the external MediaWiki and opens in a new tab."""
        settings.MAKERSPACE_WIKI_URL = "https://wiki.example.test"
        _user_with_role("m_wiki_nav")
        client.login(username="m_wiki_nav", password="pass")
        resp = client.get(reverse("hub_home"))
        assert (
            b'<a href="https://wiki.example.test" class="hub-sidebar__link" '
            b'target="_blank" rel="noopener noreferrer">' in resp.content
        )

    def it_hides_the_wiki_link_when_no_url_is_configured(client: Client, settings):
        settings.MAKERSPACE_WIKI_URL = ""
        _user_with_role("m_no_wiki")
        client.login(username="m_no_wiki", password="pass")
        resp = client.get(reverse("hub_home"))
        assert b"hub-sidebar__link" in resp.content  # the rest of the nav still renders
        # The external Wiki link is the only nav link that opens in a new tab.
        assert b'class="hub-sidebar__link" target="_blank"' not in resp.content

    def it_no_longer_shows_the_two_google_doc_footer_links(client: Client):
        _user_with_role("m_footer")
        client.login(username="m_footer", password="pass")
        resp = client.get(reverse("hub_home"))
        assert _MEMBER_GUIDE_URL.encode() not in resp.content
        assert _CODE_OF_CONDUCT_URL.encode() not in resp.content


def describe_org_info_editor_permissions():
    @pytest.fixture
    def member_client(client: Client) -> Client:
        _user_with_role("plain_member")
        client.login(username="plain_member", password="pass")
        return client

    def it_forbids_a_member_from_the_editor(member_client: Client):
        assert member_client.get(reverse("hub_help_edit")).status_code == 403

    def it_forbids_a_member_from_saving_faq(member_client: Client):
        assert member_client.post(reverse("hub_org_info_faq_save")).status_code == 403

    def it_forbids_a_member_from_saving_links(member_client: Client):
        assert member_client.post(reverse("hub_org_info_links_save")).status_code == 403

    def it_forbids_a_member_from_saving_articles(member_client: Client):
        assert member_client.post(reverse("hub_help_articles_save")).status_code == 403

    def it_forbids_a_member_from_saving_categories(member_client: Client):
        assert member_client.post(reverse("hub_help_categories_save")).status_code == 403

    def it_forbids_a_member_from_deleting_the_floorplan(member_client: Client):
        assert member_client.post(reverse("hub_org_info_floorplan_delete")).status_code == 403


def describe_org_info_editor():
    @pytest.fixture
    def admin_client(client: Client) -> Client:
        _user_with_role("big_admin", fog_role=Member.FogRole.ADMIN)
        client.login(username="big_admin", password="pass")
        return client

    def it_renders_the_editor_for_an_admin(admin_client: Client):
        assert admin_client.get(reverse("hub_help_edit")).status_code == 200

    def it_saves_the_main_content_form(admin_client: Client):
        resp = admin_client.post(
            reverse("hub_help_edit"),
            {
                "intro": "Hi team",
                "parking": "",
                "who_to_contact": "",
                "code_of_conduct": "",
                "code_of_conduct_url": "",
                "floorplan_caption": "",
            },
        )
        assert resp.status_code == 302
        assert OrgInfoPage.load().intro == "Hi team"

    def it_re_renders_on_an_invalid_main_form(admin_client: Client):
        resp = admin_client.post(
            reverse("hub_help_edit"),
            {
                "intro": "",
                "parking": "",
                "who_to_contact": "",
                "code_of_conduct": "",
                "code_of_conduct_url": "not a url",
                "floorplan_caption": "",
            },
        )
        assert resp.status_code == 200

    def it_saves_a_new_faq_row(admin_client: Client):
        resp = admin_client.post(
            reverse("hub_org_info_faq_save"), _faq_payload("Where are the restrooms?", "Down the hall.")
        )
        assert resp.status_code == 302
        assert OrgFAQItem.objects.filter(question="Where are the restrooms?").exists()

    def it_re_renders_a_faq_row_with_a_non_youtube_video(admin_client: Client):
        # A valid URL that isn't YouTube — passes URLField, then fails clean_video_url.
        resp = admin_client.post(
            reverse("hub_org_info_faq_save"), _faq_payload("Q?", "A", video_url="https://vimeo.com/12345")
        )
        assert resp.status_code == 200
        assert not OrgFAQItem.objects.filter(question="Q?").exists()
        # The bound formset re-renders on the right tab with the admin's edits intact.
        assert b"section: 'faq' ||" in resp.content
        assert b'value="Q?"' in resp.content

    def it_re_renders_a_faq_row_with_both_a_document_and_a_link(admin_client: Client):
        payload = _faq_payload("Q?", "A")
        payload["faq-0-document_url"] = "https://docs.example/x"
        payload["faq-0-document"] = SimpleUploadedFile("a.pdf", b"%PDF-1.4")
        resp = admin_client.post(reverse("hub_org_info_faq_save"), payload)
        assert resp.status_code == 200
        assert not OrgFAQItem.objects.filter(question="Q?").exists()
        assert b"Add a document OR a link for this answer, not both." in resp.content

    def it_deletes_a_saved_faq_row_flagged_for_deletion(admin_client: Client):
        faq = OrgFAQItemFactory(question="Old question?")
        payload = {
            "faq-TOTAL_FORMS": "1",
            "faq-INITIAL_FORMS": "1",
            "faq-MIN_NUM_FORMS": "0",
            "faq-MAX_NUM_FORMS": "1000",
            "faq-0-id": str(faq.pk),
            "faq-0-question": faq.question,
            "faq-0-answer": faq.answer,
            "faq-0-video_url": "",
            "faq-0-document_url": "",
            "faq-0-sort_order": "0",
            "faq-0-DELETE": "on",
        }
        resp = admin_client.post(reverse("hub_org_info_faq_save"), payload)
        assert resp.status_code == 302
        assert not OrgFAQItem.objects.filter(pk=faq.pk).exists()

    def it_saves_a_new_link_row(admin_client: Client):
        resp = admin_client.post(reverse("hub_org_info_links_save"), _link_payload("Handbook", "https://example.com/h"))
        assert resp.status_code == 302
        assert OrgLink.objects.filter(label="Handbook").exists()

    def it_re_renders_invalid_links_with_the_bound_formset(admin_client: Client):
        resp = admin_client.post(reverse("hub_org_info_links_save"), _link_payload("Bad", "not a url"))
        assert resp.status_code == 200
        assert not OrgLink.objects.filter(label="Bad").exists()
        # The FAQ & Links tab stays active and the admin's edits survive.
        assert b"section: 'faq' ||" in resp.content
        assert b'value="Bad"' in resp.content

    def it_deletes_the_floorplan(admin_client: Client):
        page = OrgInfoPage.load()
        page.floorplan_image = SimpleUploadedFile("m.png", _image_bytes(300, 150), content_type="image/png")
        page.save()
        resp = admin_client.post(reverse("hub_org_info_floorplan_delete"))
        assert resp.status_code == 302
        page.refresh_from_db()
        assert not page.floorplan_image

    def it_is_a_no_op_when_deleting_an_absent_floorplan(admin_client: Client):
        resp = admin_client.post(reverse("hub_org_info_floorplan_delete"))
        assert resp.status_code == 302

    def it_renders_the_articles_tab_for_an_admin(admin_client: Client):
        resp = admin_client.get(reverse("hub_help_edit"))
        assert resp.status_code == 200
        assert b"Save Articles" in resp.content

    def it_saves_a_new_article_row(admin_client: Client):
        resp = admin_client.post(
            reverse("hub_help_articles_save"), _article_payload("Guild voting", "Rank three guilds.")
        )
        assert resp.status_code == 302
        article = WikiArticle.objects.get(title="Guild voting")
        assert article.slug == "guild-voting"

    def it_re_renders_an_article_row_missing_its_body(admin_client: Client):
        resp = admin_client.post(reverse("hub_help_articles_save"), _article_payload("No body", ""))
        assert resp.status_code == 200
        assert not WikiArticle.objects.filter(title="No body").exists()
        # Bound re-render: the Articles tab stays active, the field error shows, edits survive.
        assert b"section: 'articles' ||" in resp.content
        assert b"This field is required." in resp.content
        assert b'value="No body"' in resp.content

    def it_deletes_a_saved_article_row_flagged_for_deletion(admin_client: Client):
        article = WikiArticleFactory(title="Old guide")
        payload = {
            "articles-TOTAL_FORMS": "1",
            "articles-INITIAL_FORMS": "1",
            "articles-MIN_NUM_FORMS": "0",
            "articles-MAX_NUM_FORMS": "1000",
            "articles-0-id": str(article.pk),
            "articles-0-title": article.title,
            "articles-0-slug": article.slug,
            "articles-0-body": article.body,
            "articles-0-sort_order": "0",
            "articles-0-is_published": "on",
            "articles-0-DELETE": "on",
        }
        resp = admin_client.post(reverse("hub_help_articles_save"), payload)
        assert resp.status_code == 302
        assert not WikiArticle.objects.filter(pk=article.pk).exists()


def describe_help_categories_editor():
    @pytest.fixture
    def admin_client(client: Client) -> Client:
        _user_with_role("cat_admin", fog_role=Member.FogRole.ADMIN)
        client.login(username="cat_admin", password="pass")
        return client

    def it_renders_the_categories_tab_for_an_admin(admin_client: Client):
        resp = admin_client.get(reverse("hub_help_edit"))
        assert resp.status_code == 200
        assert b"Save Categories" in resp.content
        assert b"+ Add a category" in resp.content

    def it_saves_a_new_category_and_redirects_back_to_the_tab(admin_client: Client):
        resp = admin_client.post(reverse("hub_help_categories_save"), _category_payload("Getting started"))
        assert resp.status_code == 302
        assert resp.url == f"{reverse('hub_help_edit')}?tab=categories"
        category = HelpCategory.objects.get(name="Getting started")
        assert category.slug == "getting-started"

    def it_deletes_a_saved_category_row_flagged_for_deletion(admin_client: Client):
        category = HelpCategoryFactory(name="Old category")
        payload = _category_payload(category.name, slug=category.slug)
        payload["categories-INITIAL_FORMS"] = "1"
        payload["categories-0-id"] = str(category.pk)
        payload["categories-0-DELETE"] = "on"
        resp = admin_client.post(reverse("hub_help_categories_save"), payload)
        assert resp.status_code == 302
        assert not HelpCategory.objects.filter(pk=category.pk).exists()

    def describe_with_a_reserved_slug():
        def it_re_renders_the_bound_formset_with_the_field_error(admin_client: Client):
            resp = admin_client.post(reverse("hub_help_categories_save"), _category_payload("Weird name", slug="edit"))
            assert resp.status_code == 200
            assert "That name is reserved — pick another.".encode() in resp.content
            assert not HelpCategory.objects.filter(name="Weird name").exists()

        def it_keeps_the_categories_tab_active(admin_client: Client):
            resp = admin_client.post(
                reverse("hub_help_categories_save"), _category_payload("Weird name", slug="search")
            )
            assert b"section: 'categories' ||" in resp.content

        def it_preserves_the_submitted_values(admin_client: Client):
            resp = admin_client.post(
                reverse("hub_help_categories_save"),
                _category_payload("Weird name", slug="more", description="A description to keep."),
            )
            assert b'value="Weird name"' in resp.content
            assert b'value="A description to keep."' in resp.content


def describe_wiki_article_form_category_and_related():
    @pytest.fixture
    def admin_client(client: Client) -> Client:
        _user_with_role("rel_admin", fog_role=Member.FogRole.ADMIN)
        client.login(username="rel_admin", password="pass")
        return client

    def it_round_trips_category_and_related_articles_through_the_editor(admin_client: Client):
        category = HelpCategoryFactory(name="Guilds")
        other = WikiArticleFactory(title="Other guide")
        payload = _article_payload("Guild voting", "Rank three guilds.")
        payload["articles-0-category"] = str(category.pk)
        payload["articles-0-related_articles"] = [str(other.pk)]
        resp = admin_client.post(reverse("hub_help_articles_save"), payload)
        assert resp.status_code == 302
        article = WikiArticle.objects.get(title="Guild voting")
        assert article.category == category
        assert list(article.related_articles.all()) == [other]

    def it_excludes_the_article_itself_from_the_related_picker_when_editing(db):
        article = WikiArticleFactory(title="Self guide")
        other = WikiArticleFactory(title="Other guide")
        queryset = WikiArticleForm(instance=article).fields["related_articles"].queryset
        assert other in queryset
        assert article not in queryset

    def it_offers_every_article_on_an_unsaved_row(db):
        existing = WikiArticleFactory(title="Existing guide")
        assert existing in WikiArticleForm().fields["related_articles"].queryset

    def it_labels_the_empty_category_choice_as_hidden_from_the_landing_grid(db):
        assert WikiArticleForm().fields["category"].empty_label == "— No category (hidden from the landing grid) —"


def describe_help_center_feature_flag():
    @pytest.fixture
    def flag_off(db) -> None:
        config = SiteConfiguration.load()
        config.help_page_enabled = False
        config.save()

    def it_redirects_every_public_help_get_to_home_when_disabled(client: Client, flag_off):
        urls = [
            reverse("hub_help"),
            reverse("hub_help_search"),
            reverse("hub_help_category", args=["guilds"]),
            reverse("hub_help_article", args=["guilds", "guild-voting"]),
        ]
        for url in urls:
            resp = client.get(url)
            assert resp.status_code == 302, url
            assert resp.url == reverse("hub_home"), url


def describe_help_category_page():
    def it_lists_published_guides_in_order_with_lead_text(client: Client):
        category = HelpCategoryFactory(name="Guilds", description="Everything guilds.")
        second = WikiArticleFactory(title="Zed guide", category=category, sort_order=20, body="Zed lead here.")
        first = WikiArticleFactory(title="Alpha guide", category=category, sort_order=10)
        resp = client.get(reverse("hub_help_category", args=[category.slug]))
        assert resp.status_code == 200
        assert list(resp.context["articles"]) == [first, second]
        assert b"Zed lead here." in resp.content
        assert f'href="{first.get_absolute_url()}"'.encode() in resp.content
        assert b"Everything guilds." in resp.content

    def it_shows_the_color_coded_audience_chip(client: Client):
        category = HelpCategoryFactory(name="Teaching", audience=HelpCategory.Audience.INSTRUCTOR)
        WikiArticleFactory(category=category)
        resp = client.get(reverse("hub_help_category", args=[category.slug]))
        assert b"pl-help-badge pl-help-badge--instructor" in resp.content
        assert b"Instructors" in resp.content

    def it_renders_the_breadcrumb_back_to_help(client: Client):
        category = HelpCategoryFactory()
        WikiArticleFactory(category=category)
        resp = client.get(reverse("hub_help_category", args=[category.slug]))
        assert b"pl-help-breadcrumbs" in resp.content
        assert f'href="{reverse("hub_help")}"'.encode() in resp.content

    def it_excludes_unlisted_articles(client: Client):
        category = HelpCategoryFactory()
        WikiArticleFactory(title="Instructor orientation", slug="instructor-orientation", category=category)
        resp = client.get(reverse("hub_help_category", args=[category.slug]))
        assert b"Instructor orientation" not in resp.content

    def it_hides_drafts_from_a_member(client: Client):
        category = HelpCategoryFactory()
        WikiArticleFactory(title="Live guide", category=category)
        WikiArticleFactory(title="Hidden draft", category=category, is_published=False)
        resp = client.get(reverse("hub_help_category", args=[category.slug]))
        assert b"Live guide" in resp.content
        assert b"Hidden draft" not in resp.content

    def it_flags_drafts_for_an_admin(client: Client):
        _user_with_role("cat_draft_admin", fog_role=Member.FogRole.ADMIN)
        client.login(username="cat_draft_admin", password="pass")
        category = HelpCategoryFactory()
        WikiArticleFactory(title="Hidden draft", category=category, is_published=False)
        resp = client.get(reverse("hub_help_category", args=[category.slug]))
        assert b"Hidden draft" in resp.content
        assert b"Draft" in resp.content
        assert b"Edit guides" in resp.content

    def it_renders_the_empty_state_with_a_way_back(client: Client):
        category = HelpCategoryFactory(name="Nothing here")
        resp = client.get(reverse("hub_help_category", args=[category.slug]))
        assert b"No guides here yet." in resp.content
        assert b"Back to Help" in resp.content

    def it_404s_an_unknown_slug(client: Client):
        assert client.get(reverse("hub_help_category", args=["nope"])).status_code == 404


def describe_help_article_page():
    def it_renders_the_body_through_the_help_profile_with_toc_and_aside(client: Client):
        category = HelpCategoryFactory(name="Guilds")
        article = WikiArticleFactory(
            title="Guild voting",
            category=category,
            body="## Rank your top 3 {#rank-top-3}\n\nPick three guilds.",
        )
        sibling = WikiArticleFactory(title="Other guide", category=category)
        resp = client.get(article.get_absolute_url())
        assert resp.status_code == 200
        assert b"pl-md--help" in resp.content
        assert b'href="#rank-top-3"' in resp.content
        assert b"On this page" in resp.content
        assert b"In this category" in resp.content
        assert f'href="{sibling.get_absolute_url()}"'.encode() in resp.content
        assert f'href="{reverse("hub_help_category", args=[category.slug])}"'.encode() in resp.content

    def it_hides_the_toc_when_the_body_has_no_anchored_headings(client: Client):
        article = WikiArticleFactory(body="Just a paragraph.")
        resp = client.get(article.get_absolute_url())
        assert b"On this page" not in resp.content

    def it_renders_related_guides_and_prev_next(client: Client):
        category = HelpCategoryFactory()
        previous = WikiArticleFactory(title="Previous guide", category=category, sort_order=10)
        article = WikiArticleFactory(title="Middle guide", category=category, sort_order=20)
        upcoming = WikiArticleFactory(title="Upcoming guide", category=category, sort_order=30)
        resp = client.get(article.get_absolute_url())
        assert b"Related guides" in resp.content
        assert b"pl-help-badge pl-help-badge--member" in resp.content
        assert b"pl-help-prevnext" in resp.content
        assert f'href="{previous.get_absolute_url()}"'.encode() in resp.content
        assert f'href="{upcoming.get_absolute_url()}"'.encode() in resp.content
        # Footer nav cells carry the small direction label with the guide title under it.
        content = resp.content.decode()
        assert "&larr; Previous" in content
        assert "Next &rarr;" in content
        assert '<span class="pl-help-prevnext__title">Previous guide</span>' in content
        assert '<span class="pl-help-prevnext__title">Upcoming guide</span>' in content

    def it_hides_the_related_footer_and_prevnext_when_alone(client: Client):
        article = WikiArticleFactory(title="Loner guide")
        resp = client.get(article.get_absolute_url())
        assert b"Related guides" not in resp.content
        assert b"pl-help-prevnext" not in resp.content

    def describe_when_uncategorized():
        def it_renders_at_the_more_segment_with_help_only_breadcrumbs(client: Client):
            article = WikiArticleFactory(title="Floating guide")
            resp = client.get(article.get_absolute_url())
            assert resp.status_code == 200
            assert resp.request["PATH_INFO"].startswith("/help/more/")
            assert b"In this category" not in resp.content
            assert "Can't find it?".encode() in resp.content

        def it_never_links_the_more_listing_itself(client: Client):
            article = WikiArticleFactory(title="Floating guide")
            resp = client.get(article.get_absolute_url())
            assert b'href="/help/more/"' not in resp.content

    def it_301s_a_stale_category_segment_to_the_canonical_url(client: Client):
        category = HelpCategoryFactory(name="Guilds")
        article = WikiArticleFactory(title="Guild voting", category=category)
        resp = client.get(reverse("hub_help_article", args=["more", article.slug]))
        assert resp.status_code == 301
        assert resp.url == article.get_absolute_url()

    def it_404s_an_unknown_article(client: Client):
        HelpCategoryFactory(name="Guilds")
        assert client.get(reverse("hub_help_article", args=["guilds", "nope"])).status_code == 404

    def it_404s_the_more_pseudo_category_listing(client: Client):
        assert client.get("/help/more/").status_code == 404

    def it_resolves_an_unlisted_article_by_direct_url(client: Client):
        article = WikiArticleFactory(title="Instructor orientation", slug="instructor-orientation")
        assert client.get(article.get_absolute_url()).status_code == 200

    def describe_when_the_article_is_a_draft():
        def it_404s_for_a_member(client: Client):
            article = WikiArticleFactory(title="Draft guide", is_published=False)
            assert client.get(article.get_absolute_url()).status_code == 404

        def it_shows_the_draft_banner_to_an_admin(client: Client):
            _user_with_role("art_draft_admin", fog_role=Member.FogRole.ADMIN)
            client.login(username="art_draft_admin", password="pass")
            article = WikiArticleFactory(title="Draft guide", is_published=False)
            resp = client.get(article.get_absolute_url())
            assert resp.status_code == 200
            assert "Draft — members can't see this yet".encode() in resp.content


def describe_help_search_page():
    def it_finds_articles_by_per_term_and_matching(client: Client):
        match = WikiArticleFactory(title="Book a slot", body="How an orientation works.")
        WikiArticleFactory(title="Book a slot", body="Nothing else.")
        resp = client.get(reverse("hub_help_search"), {"q": "book orientation"})
        assert resp.status_code == 200
        assert [a for a, _ in resp.context["results"]] == [match]
        assert b"1 guide matches" in resp.content

    def it_marks_the_hit_in_the_snippet_and_shows_the_badges(client: Client):
        category = HelpCategoryFactory(name="Guilds", audience=HelpCategory.Audience.GUILD_LEAD)
        WikiArticleFactory(title="Guild voting", body="Rank the kiln guild.", category=category)
        resp = client.get(reverse("hub_help_search"), {"q": "kiln"})
        assert b"<mark>kiln</mark>" in resp.content
        assert b"Guilds" in resp.content
        assert b"Guild leads &amp; staff" in resp.content
        assert b"pl-help-badge pl-help-badge--guild_lead" in resp.content

    def it_renders_the_prompt_state_with_category_suggestions_for_an_empty_q(client: Client):
        category = HelpCategoryFactory(name="Getting started")
        WikiArticleFactory(category=category)
        resp = client.get(reverse("hub_help_search"))
        assert b"Type something to search the guides." in resp.content
        assert f'href="{reverse("hub_help_category", args=[category.slug])}"'.encode() in resp.content

    def it_renders_the_no_results_state_with_a_browse_all_button(client: Client):
        WikiArticleFactory(title="Guild voting")
        resp = client.get(reverse("hub_help_search"), {"q": "zzzunfindable"})
        assert b"Nothing matched &ldquo;zzzunfindable&rdquo;." in resp.content
        assert b"Try fewer or different words." in resp.content
        assert b"Browse all guides" in resp.content
        assert f'href="{reverse("hub_help")}"'.encode() in resp.content


def describe_hero_adjust_for_the_org_banner():
    def _adjust(client: Client) -> Client:
        page = OrgInfoPage.load()
        ct = ContentType.objects.get_for_model(OrgInfoPage)
        return client.post(
            reverse("hub_hero_adjust"),
            data=json.dumps(
                {"content_type_id": ct.pk, "object_id": page.pk, "crop": {"x": 10, "y": 20, "w": 100, "h": 50}}
            ),
            content_type="application/json",
        )

    def it_lets_an_admin_adjust_the_org_banner(client: Client):
        _user_with_role("hero_admin", fog_role=Member.FogRole.ADMIN)
        client.login(username="hero_admin", password="pass")
        assert _adjust(client).status_code == 200

    def it_forbids_a_member_from_adjusting_the_org_banner(client: Client):
        _user_with_role("hero_member")
        client.login(username="hero_member", password="pass")
        assert _adjust(client).status_code == 403


def describe_help_editor_rich_text():
    """The /help/edit/ text areas are dual-mode Quill editors (PageContentEditorWidget)."""

    @pytest.fixture
    def admin_client(client: Client) -> Client:
        _user_with_role("rte_admin", fog_role=Member.FogRole.ADMIN)
        client.login(username="rte_admin", password="pass")
        return client

    def describe_the_editor_page_markup():
        def it_renders_a_server_seeded_page_toolbar_mount_for_each_org_block(admin_client: Client):
            html = admin_client.get(reverse("hub_help_edit")).content.decode()
            for field in ("intro", "parking", "who_to_contact", "code_of_conduct"):
                assert f'data-rte-for="id_{field}"' in html
            assert 'data-rte-seed="server"' in html
            assert 'data-rte-toolbar="page"' in html

        def it_seeds_the_mount_with_the_rendered_markdown_not_the_raw_source(admin_client: Client):
            page = OrgInfoPage.load()
            page.parking = "**Free** after 5pm."
            page.save()
            html = admin_client.get(reverse("hub_help_edit")).content.decode()
            assert "<strong>Free</strong> after 5pm." in html

        def it_loads_the_quill_assets_and_the_shared_initializer(admin_client: Client):
            html = admin_client.get(reverse("hub_help_edit")).content.decode()
            assert "js/quill.min.js" in html
            assert "js/rich-editor-init.js" in html

        def it_renders_rich_editors_for_article_bodies_and_faq_answers_including_the_clone_templates(
            admin_client: Client,
        ):
            WikiArticleFactory(title="Existing guide", slug="existing-guide")
            OrgFAQItemFactory(question="Existing?")
            html = admin_client.get(reverse("hub_help_edit")).content.decode()
            assert 'data-rte-for="id_articles-0-body"' in html
            assert 'data-rte-for="id_articles-__prefix__-body"' in html
            assert 'data-rte-for="id_faq-0-answer"' in html
            assert 'data-rte-for="id_faq-__prefix__-answer"' in html

        def it_reinitializes_editors_after_a_row_clone(admin_client: Client):
            # Cloned template rows never execute widget scripts (FRONTEND.md rule 16) —
            # both "+ Add" handlers with rich fields must call the shared initializer.
            html = admin_client.get(reverse("hub_help_edit")).content.decode()
            assert html.count("window.plRteInitAll();") >= 2

        def describe_the_seed_owned_hint():
            def it_warns_on_a_seed_owned_article_row(admin_client: Client):
                WikiArticleFactory(title="Welcome to FOG", slug="welcome-to-fog")
                html = admin_client.get(reverse("hub_help_edit")).content.decode()
                assert "will overwrite edits made here" in html

            def it_stays_quiet_for_an_admin_authored_article(admin_client: Client):
                WikiArticleFactory(title="House rules", slug="house-rules-custom")
                html = admin_client.get(reverse("hub_help_edit")).content.decode()
                assert "will overwrite edits made here" not in html

    def describe_saving_rich_html():
        def it_sanitizes_and_saves_an_html_org_block(admin_client: Client):
            resp = admin_client.post(
                reverse("hub_help_edit"),
                {
                    "intro": "",
                    "parking": '<p class="ql-align-center"><strong>Free</strong> after 5pm.<script>evil()</script></p>',
                    "who_to_contact": "",
                    "code_of_conduct": "",
                    "code_of_conduct_url": "",
                    "floorplan_caption": "",
                },
            )
            assert resp.status_code == 302
            parking = OrgInfoPage.load().parking
            assert "<strong>Free</strong>" in parking
            assert "<script" not in parking
            assert "class=" not in parking

        def it_passes_a_markdown_org_block_through_unchanged(admin_client: Client):
            resp = admin_client.post(
                reverse("hub_help_edit"),
                {
                    "intro": "",
                    "parking": "**Free** after 5pm.",
                    "who_to_contact": "",
                    "code_of_conduct": "",
                    "code_of_conduct_url": "",
                    "floorplan_caption": "",
                },
            )
            assert resp.status_code == 302
            assert OrgInfoPage.load().parking == "**Free** after 5pm."

        def it_stores_an_emptied_editor_as_blank(admin_client: Client):
            page = OrgInfoPage.load()
            page.parking = "old text"
            page.save()
            resp = admin_client.post(
                reverse("hub_help_edit"),
                {
                    "intro": "",
                    "parking": "<p><br></p>",
                    "who_to_contact": "",
                    "code_of_conduct": "",
                    "code_of_conduct_url": "",
                    "floorplan_caption": "",
                },
            )
            assert resp.status_code == 302
            assert OrgInfoPage.load().parking == ""

        def it_round_trips_a_rich_article_body_to_the_public_page(admin_client: Client):
            resp = admin_client.post(
                reverse("hub_help_articles_save"),
                _article_payload("Guild voting", '<p><u>Rank</u> three guilds.<img src="https://evil.example/x"></p>'),
            )
            assert resp.status_code == 302
            article = WikiArticle.objects.get(title="Guild voting")
            assert article.body == "<p><u>Rank</u> three guilds.</p>"
            page = admin_client.get(article.get_absolute_url()).content.decode()
            assert "<u>Rank</u> three guilds." in page

        def it_rejects_an_article_body_that_sanitizes_to_nothing(admin_client: Client):
            resp = admin_client.post(reverse("hub_help_articles_save"), _article_payload("Empty", "<p><br></p>"))
            assert resp.status_code == 200
            assert not WikiArticle.objects.filter(title="Empty").exists()
            assert b"The guide needs a body." in resp.content

        def it_round_trips_a_rich_faq_answer_to_the_help_page(admin_client: Client):
            resp = admin_client.post(
                reverse("hub_org_info_faq_save"),
                _faq_payload("Restrooms?", "<p>Down the hall, past the <em>kiln</em>.<script>x()</script></p>"),
            )
            assert resp.status_code == 302
            faq = OrgFAQItem.objects.get(question="Restrooms?")
            assert "<em>kiln</em>" in faq.answer
            assert "<script" not in faq.answer
            page = admin_client.get(reverse("hub_help")).content.decode()
            assert "<em>kiln</em>" in page

        def it_rejects_a_faq_answer_that_sanitizes_to_nothing(admin_client: Client):
            resp = admin_client.post(reverse("hub_org_info_faq_save"), _faq_payload("Q?", "<p><br></p>"))
            assert resp.status_code == 200
            assert not OrgFAQItem.objects.filter(question="Q?").exists()
            assert b"Add an answer." in resp.content

    def describe_rendering_rich_html_on_the_public_pages():
        def it_renders_a_stored_html_org_block_sanitized_on_help(client: Client):
            page = OrgInfoPage.load()
            page.parking = "<p><strong>Free</strong> after 5pm.<script>evil()</script></p>"
            page.save()
            html = client.get(reverse("hub_help")).content.decode()
            assert "<strong>Free</strong> after 5pm." in html
            assert "<script>evil()" not in html
