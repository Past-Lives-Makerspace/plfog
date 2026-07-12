"""Site Settings → Slideshow tab — global/alert fields (Block A, in the shared form),
and the Zones + Slides editors (Block B, their own forms outside it).

The nested-form guard is the load-bearing structural test: Block A's inputs live INSIDE
#site-settings-form; Block B's editor <form>s come AFTER its </form> (you can't nest forms).
"""

from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from membership.models import SlideshowSlide, SlideshowZone
from tests.membership.factories import GuildAnnouncementFactory, SlideshowSlideFactory, SlideshowZoneFactory

pytestmark = pytest.mark.django_db


def _superuser(client: Client) -> None:
    User.objects.create_superuser(username="ssadmin", email="ssadmin@x.com", password="p")
    client.login(username="ssadmin", password="p")


def _png_upload(name: str = "slide.png") -> SimpleUploadedFile:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (12, 12), "red").save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _zone_mgmt(total: int, initial: int) -> dict[str, str]:
    return {
        "zones-TOTAL_FORMS": str(total),
        "zones-INITIAL_FORMS": str(initial),
        "zones-MIN_NUM_FORMS": "0",
        "zones-MAX_NUM_FORMS": "1000",
    }


def _slide_mgmt(total: int, initial: int) -> dict[str, str]:
    return {
        "slides-TOTAL_FORMS": str(total),
        "slides-INITIAL_FORMS": str(initial),
        "slides-MIN_NUM_FORMS": "0",
        "slides-MAX_NUM_FORMS": "1000",
    }


def describe_slideshow_tab_gating():
    def it_redirects_anonymous_users(client):
        assert client.get(reverse("hub_admin_site_settings") + "?tab=slideshow").status_code == 302

    def it_forbids_non_admins(client):
        User.objects.create_user(username="plain", email="plain@x.com", password="p")
        client.login(username="plain", password="p")
        assert client.get(reverse("hub_admin_site_settings") + "?tab=slideshow").status_code == 403

    def it_gates_the_zone_save_view(client):
        User.objects.create_user(username="plain2", email="plain2@x.com", password="p")
        client.login(username="plain2", password="p")
        assert client.post(reverse("hub_admin_slideshow_zones_save"), _zone_mgmt(0, 0)).status_code == 403

    def it_keeps_the_slideshow_tab_active_after_a_zone_save(client):
        _superuser(client)
        resp = client.post(reverse("hub_admin_slideshow_zones_save"), _zone_mgmt(0, 0))
        assert resp.status_code == 302
        assert "tab=slideshow" in resp["Location"]


def describe_slideshow_tab_render():
    def it_shows_the_tab_button_and_both_blocks(client):
        _superuser(client)
        html = client.get(reverse("hub_admin_site_settings")).content.decode()
        assert "tab = 'slideshow'" in html  # tab button @click
        assert html.count("tab === 'slideshow'") >= 2  # TWO x-show blocks (A + B)
        assert "pl-slideshow-tab-a" in html
        assert "pl-slideshow-tab-b" in html

    def it_puts_the_global_signage_fields_inside_the_settings_form_but_not_the_editor_forms(client):
        _superuser(client)
        html = client.get(reverse("hub_admin_site_settings")).content.decode()
        start = html.index('<form method="post" id="site-settings-form"')
        main_form = html[start : html.index("</form>", start)]
        # Block A: the global signage fields ride the shared form.
        assert 'name="signage_default_slide_seconds"' in main_form
        # Block B: the editor forms are NOT nested inside the settings form.
        assert "slideshow/zones/save" not in main_form
        assert "slideshow/slides/save" not in main_form

    def it_renders_the_editors_add_button_and_empty_templates(client):
        _superuser(client)
        html = client.get(reverse("hub_admin_site_settings")).content.decode()
        assert 'id="zone-empty-template"' in html
        assert 'id="slide-empty-template"' in html
        assert "+ Add a screen" in html
        assert "+ Add a slide" in html

    def it_renders_a_delete_button_not_a_toggle_for_a_saved_zone(client):
        _superuser(client)
        SlideshowZoneFactory(name="Woodshop", slug="woodshop")
        html = client.get(reverse("hub_admin_site_settings")).content.decode()
        # The Delete control is a real danger button, margin-spaced — never a toggle switch.
        assert "Delete this screen" in html
        assert "pl-btn--danger" in html
        assert "margin-top:0.75rem" in html

    def it_renders_the_zone_setup_url_and_qr_for_a_saved_zone(client):
        _superuser(client)
        SlideshowZoneFactory(slug="woodshop")
        html = client.get(reverse("hub_admin_site_settings")).content.decode()
        assert "pl-slideshow-zone__url" in html
        assert "<svg" in html  # the inline QR
        assert "Copy URL" in html

    def it_renders_both_kind_groups_with_layout_in_a_class(client):
        _superuser(client)
        html = client.get(reverse("hub_admin_site_settings")).content.decode()
        assert "x-show=\"kind === 'custom'\"" in html
        assert "x-show=\"kind === 'announcement'\"" in html
        assert "pl-slide-fields" in html  # layout lives in a class, not inline display

    def it_renders_slides_as_compact_summary_rows_with_a_draggable_image_zone(client):
        _superuser(client)
        SlideshowSlideFactory(kind="custom", title="Flyer night")
        html = client.get(reverse("hub_admin_site_settings")).content.decode()
        # Collapsed-by-default summary; only the explicit Edit button reveals the editor panel.
        assert "pl-slide-summary" in html
        assert 'x-show="expanded"' in html
        assert "pl-slide-editor" in html
        assert "expanded ? 'Done' : 'Edit'" in html  # the per-row Edit toggle (a button, never a submit)
        # Upgraded image input: the shared draggable drop-zone + a recommended-size tooltip.
        assert "cls-image-upload-zone" in html
        assert "1920×1080 (16:9)" in html
        # A single delegated script drives every zone (clone-safe for "+ Add a slide").
        assert "document.getElementById('slide-rows')" in html

    def it_renders_reorder_affordances_and_a_hidden_sort_order(client):
        _superuser(client)
        SlideshowSlideFactory(kind="custom", title="Flyer night")
        html = client.get(reverse("hub_admin_site_settings")).content.decode()
        # Drag grip (desktop) + up/down move buttons (touch fallback) on every row.
        assert "pl-slide-grip" in html
        assert 'draggable="true"' in html
        assert 'data-move="up"' in html
        assert 'data-move="down"' in html
        # sort_order is a hidden input now — the reorder JS rewrites its value; no visible number field.
        assert '<input type="hidden" name="slides-0-sort_order"' in html
        # The reorder is persisted purely by the existing "Save slides" — one delegated handler drives it.
        assert "slides:reindex" in html

    def it_offers_only_published_announcements_in_the_picker(client):
        _superuser(client)
        GuildAnnouncementFactory(title="Live post")
        GuildAnnouncementFactory(pending=True, title="Draft post")
        html = client.get(reverse("hub_admin_site_settings")).content.decode()
        # Assert on the announcement labels (robust against unrelated value="N" ids
        # elsewhere on the page, e.g. guild-pk option values in the Discord tab editors).
        assert "Live post" in html
        assert "Draft post" not in html


def describe_zone_editor_save():
    def it_creates_a_zone_and_auto_fills_the_slug_from_the_name(client):
        _superuser(client)
        data = {
            **_zone_mgmt(1, 0),
            "zones-0-name": "Woodshop",
            "zones-0-slug": "",
            "zones-0-is_enabled": "on",
            "zones-0-sort_order": "0",
        }
        resp = client.post(reverse("hub_admin_slideshow_zones_save"), data)
        assert resp.status_code == 302
        assert SlideshowZone.objects.filter(slug="woodshop").exists()

    def it_skips_a_blank_add_row(client):
        _superuser(client)
        data = {
            **_zone_mgmt(1, 0),
            "zones-0-name": "",
            "zones-0-slug": "",
            "zones-0-is_enabled": "on",  # matches the rendered default so the row is unchanged
            "zones-0-sort_order": "0",
        }
        resp = client.post(reverse("hub_admin_slideshow_zones_save"), data)
        assert resp.status_code == 302
        assert SlideshowZone.objects.count() == 0

    def it_deletes_a_flagged_row_and_keeps_the_others(client):
        _superuser(client)
        keep = SlideshowZoneFactory(name="Keep", slug="keep", sort_order=0)
        drop = SlideshowZoneFactory(name="Drop", slug="drop", sort_order=1)
        data = {
            **_zone_mgmt(2, 2),
            "zones-0-id": str(keep.pk),
            "zones-0-name": keep.name,
            "zones-0-slug": keep.slug,
            "zones-0-is_enabled": "on",
            "zones-0-sort_order": "0",
            "zones-1-id": str(drop.pk),
            "zones-1-name": drop.name,
            "zones-1-slug": drop.slug,
            "zones-1-is_enabled": "on",
            "zones-1-sort_order": "1",
            "zones-1-DELETE": "on",
        }
        resp = client.post(reverse("hub_admin_slideshow_zones_save"), data)
        assert resp.status_code == 302
        assert SlideshowZone.objects.filter(pk=keep.pk).exists()
        assert not SlideshowZone.objects.filter(pk=drop.pk).exists()


def describe_slide_editor_save():
    def it_creates_a_custom_slide_with_an_uploaded_image(client):
        _superuser(client)
        data = {
            **_slide_mgmt(1, 0),
            "slides-0-kind": "custom",
            "slides-0-title": "Flyer",
            "slides-0-body": "",
            "slides-0-link_url": "",
            "slides-0-sort_order": "0",
            "slides-0-is_enabled": "on",
            "slides-0-image": _png_upload(),
        }
        resp = client.post(reverse("hub_admin_slideshow_slides_save"), data)
        assert resp.status_code == 302
        slide = SlideshowSlide.objects.get(title="Flyer")
        assert bool(slide.image)  # the normalize/orphan save() stack ran and the file persisted

    def it_removes_an_image_without_deleting_the_slide(client):
        _superuser(client)
        slide = SlideshowSlideFactory(title="Has image", image=_png_upload())
        assert bool(slide.image)
        data = {
            **_slide_mgmt(1, 1),
            "slides-0-id": str(slide.pk),
            "slides-0-kind": "custom",
            "slides-0-title": "Has image",
            "slides-0-body": "",
            "slides-0-link_url": "",
            "slides-0-sort_order": "0",
            "slides-0-is_enabled": "on",
            "slides-0-remove_image": "1",
        }
        resp = client.post(reverse("hub_admin_slideshow_slides_save"), data)
        assert resp.status_code == 302
        slide.refresh_from_db()
        assert SlideshowSlide.objects.filter(pk=slide.pk).exists()  # still there
        assert not bool(slide.image)  # image cleared

    def it_persists_a_reorder_from_the_hidden_sort_order_inputs(client):
        _superuser(client)
        first = SlideshowSlideFactory(kind="custom", title="First", sort_order=0)
        second = SlideshowSlideFactory(kind="custom", title="Second", sort_order=1)
        # The reorder JS rewrites each row's hidden sort_order to its new visual index, then
        # "Save slides" POSTs it. Simulate the drag that lifts "Second" above "First".
        data = {
            **_slide_mgmt(2, 2),
            "slides-0-id": str(second.pk),
            "slides-0-kind": "custom",
            "slides-0-title": "Second",
            "slides-0-body": "",
            "slides-0-link_url": "",
            "slides-0-sort_order": "0",
            "slides-0-is_enabled": "on",
            "slides-1-id": str(first.pk),
            "slides-1-kind": "custom",
            "slides-1-title": "First",
            "slides-1-body": "",
            "slides-1-link_url": "",
            "slides-1-sort_order": "1",
            "slides-1-is_enabled": "on",
        }
        resp = client.post(reverse("hub_admin_slideshow_slides_save"), data)
        assert resp.status_code == 302
        first.refresh_from_db()
        second.refresh_from_db()
        assert second.sort_order == 0
        assert first.sort_order == 1
        # Meta.ordering = ["sort_order", "id"] renders the saved order on reload.
        assert list(SlideshowSlide.objects.values_list("pk", flat=True)) == [second.pk, first.pk]

    def it_requires_a_title_or_image_for_a_custom_slide(client):
        _superuser(client)
        data = {
            **_slide_mgmt(1, 0),
            "slides-0-kind": "custom",
            "slides-0-title": "",
            "slides-0-body": "Just a body, no title or image",
            "slides-0-link_url": "",
            "slides-0-sort_order": "0",
            "slides-0-is_enabled": "on",
        }
        resp = client.post(reverse("hub_admin_slideshow_slides_save"), data)
        # Like the FAQ/Links editors, the save view redirects with an error message on an
        # invalid formset (rather than re-rendering) — the invalid row is not persisted.
        assert resp.status_code == 302
        assert SlideshowSlide.objects.count() == 0
