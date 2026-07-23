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
from membership.models import Member, OrgFAQItem, OrgInfoPage, OrgLink, WikiArticle
from tests.membership.factories import (
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

    def describe_wiki_articles():
        def it_renders_a_published_article_with_its_anchor_and_toc_link(client: Client):
            WikiArticleFactory(title="Guild voting", body="Rank three guilds.", is_published=True)
            resp = client.get(reverse("hub_help"))
            assert b'id="guild-voting"' in resp.content
            assert b'href="#guild-voting"' in resp.content
            assert b"Rank three guilds." in resp.content

        def it_hides_a_draft_article_from_a_guest(client: Client):
            WikiArticleFactory(title="Secret draft", body="Not ready yet.", is_published=False)
            resp = client.get(reverse("hub_help"))
            assert b"Secret draft" not in resp.content
            assert b"Not ready yet." not in resp.content

        def it_hides_the_table_of_contents_when_no_articles_are_published(client: Client):
            WikiArticleFactory(title="Draft only", is_published=False)
            assert b"pl-wiki-toc" not in client.get(reverse("hub_help")).content

        def it_exposes_only_published_articles_in_the_context(client: Client):
            WikiArticleFactory(title="Live guide", is_published=True)
            WikiArticleFactory(title="Hidden guide", is_published=False)
            resp = client.get(reverse("hub_help"))
            assert [a.title for a in resp.context["articles"]] == ["Live guide"]

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

    def it_reports_a_faq_row_with_a_non_youtube_video(admin_client: Client):
        # A valid URL that isn't YouTube — passes URLField, then fails clean_video_url.
        resp = admin_client.post(
            reverse("hub_org_info_faq_save"), _faq_payload("Q?", "A", video_url="https://vimeo.com/12345")
        )
        assert resp.status_code == 302
        assert not OrgFAQItem.objects.filter(question="Q?").exists()

    def it_rejects_a_faq_row_with_both_a_document_and_a_link(admin_client: Client):
        payload = _faq_payload("Q?", "A")
        payload["faq-0-document_url"] = "https://docs.example/x"
        payload["faq-0-document"] = SimpleUploadedFile("a.pdf", b"%PDF-1.4")
        resp = admin_client.post(reverse("hub_org_info_faq_save"), payload)
        assert resp.status_code == 302
        assert not OrgFAQItem.objects.filter(question="Q?").exists()

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

    def it_reports_invalid_links(admin_client: Client):
        resp = admin_client.post(reverse("hub_org_info_links_save"), _link_payload("Bad", "not a url"))
        assert resp.status_code == 302
        assert not OrgLink.objects.filter(label="Bad").exists()

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

    def it_reports_an_article_row_missing_its_body(admin_client: Client):
        resp = admin_client.post(reverse("hub_help_articles_save"), _article_payload("No body", ""))
        assert resp.status_code == 302
        assert not WikiArticle.objects.filter(title="No body").exists()

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
