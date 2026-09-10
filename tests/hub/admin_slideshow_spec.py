"""The Slideshow admin page at /manage/slideshow/ — Screens, Slides, and Automatic Slides.

It used to be a Site Settings tab; it is its own page (and its own Admin Tools tile) now,
because it is the only surface that managed its own models through two sibling forms.

The nested-form guard is still the load-bearing structural test: the Screens and Slides
editors are SIBLING <form>s, never nested (you can't nest forms; that would orphan their
Save buttons).
"""

from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration
from membership.models import SlideshowSlide, SlideshowZone
from tests.membership.factories import GuildAnnouncementFactory, SlideshowSlideFactory, SlideshowZoneFactory

pytestmark = pytest.mark.django_db

_PAGE = reverse("hub_admin_slideshow")


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


def describe_slideshow_page_gating():
    def it_redirects_anonymous_users(client):
        assert client.get(_PAGE).status_code == 302

    def it_forbids_non_admins(client):
        User.objects.create_user(username="plain", email="plain@x.com", password="p")
        client.login(username="plain", password="p")
        assert client.get(_PAGE).status_code == 403

    def it_gates_the_zone_save_view(client):
        User.objects.create_user(username="plain2", email="plain2@x.com", password="p")
        client.login(username="plain2", password="p")
        assert client.post(reverse("hub_admin_slideshow_zones_save"), _zone_mgmt(0, 0)).status_code == 403

    def it_lands_a_zone_save_back_on_the_slideshow_page(client):
        _superuser(client)
        resp = client.post(reverse("hub_admin_slideshow_zones_save"), _zone_mgmt(0, 0))
        assert resp.status_code == 302
        assert resp["Location"] == _PAGE

    def it_lands_a_slide_save_back_on_the_slideshow_page(client):
        _superuser(client)
        resp = client.post(reverse("hub_admin_slideshow_slides_save"), _slide_mgmt(0, 0))
        assert resp.status_code == 302
        assert resp["Location"] == _PAGE


def describe_the_old_site_settings_tab():
    def it_redirects_the_old_bookmark_to_the_new_page(client):
        _superuser(client)
        resp = client.get(reverse("hub_admin_site_settings") + "?tab=slideshow")
        assert resp.status_code == 302
        assert resp["Location"] == _PAGE

    def it_no_longer_renders_a_slideshow_tab_on_site_settings(client):
        _superuser(client)
        html = client.get(reverse("hub_admin_site_settings")).content.decode()
        assert "tab = 'slideshow'" not in html
        assert "tab === 'slideshow'" not in html
        assert "pl-slideshow-tab-a" not in html
        assert "pl-slideshow-tab-b" not in html
        assert "slideshow/zones/save" not in html
        assert "slideshow/slides/save" not in html

    def it_falls_back_to_general_for_an_unknown_tab(client):
        _superuser(client)
        resp = client.get(reverse("hub_admin_site_settings") + "?tab=nonsense")
        assert resp.status_code == 200
        assert resp.context["active_tab"] == "general"


def describe_slideshow_page_render():
    def it_wraps_the_page_in_an_alpine_root_so_copy_url_is_live(client):
        _superuser(client)
        SlideshowZoneFactory(slug="woodshop")
        html = client.get(_PAGE).content.decode()
        # Alpine only evaluates directives inside an x-data tree; the Copy URL button
        # carries no x-data of its own, so without this root it silently does nothing.
        assert 'class="pl-slideshow-page" x-data="{}"' in html
        assert "Copy URL" in html

    def it_keeps_the_two_editor_forms_as_siblings_never_nested(client):
        _superuser(client)
        html = client.get(_PAGE).content.decode()
        zones_at = html.index(reverse("hub_admin_slideshow_zones_save"))
        zones_end = html.index("</form>", zones_at)
        slides_at = html.index(reverse("hub_admin_slideshow_slides_save"))
        assert slides_at > zones_end  # the slides form starts AFTER the zones form closes

    def it_puts_the_signage_settings_on_the_settings_form_not_on_site_settings(client):
        _superuser(client)
        settings_html = client.get(reverse("hub_admin_site_settings")).content.decode()
        assert 'name="signage_default_slide_seconds"' not in settings_html

        page = client.get(_PAGE).content.decode()
        start = page.index(f'<form method="post" action="{_PAGE}"')
        settings_form = page[start : page.index("</form>", start)]
        assert 'name="signage_default_slide_seconds"' in settings_form
        assert 'name="signage_event_days_ahead"' in settings_form
        # The editors' save targets are NOT inside the settings form.
        assert "slideshow/zones/save" not in settings_form
        assert "slideshow/slides/save" not in settings_form

    def it_renders_every_automatic_slide_switch_as_a_toggle(client):
        _superuser(client)
        html = client.get(_PAGE).content.decode()
        for field in (
            "signage_show_events",
            "signage_show_classes",
            "signage_show_guilds",
            "signage_show_calendar",
            "signage_show_voting",
            "signage_show_directory",
            "signage_show_teach",
            "signage_show_tour",
        ):
            assert f'name="{field}"' in html
        # The tour link is the one block with a destination to type, so its field rides along.
        assert 'name="signage_tour_url"' in html
        # form_field.html renders booleans as pl-toggle switches, never a raw checkbox.
        assert "pl-toggle" in html

    def it_renders_the_editors_add_buttons_empty_templates_and_saves(client):
        _superuser(client)
        html = client.get(_PAGE).content.decode()
        assert 'id="zone-empty-template"' in html
        assert 'id="slide-empty-template"' in html
        assert "+ Add a screen" in html
        assert "+ Add a slide" in html
        # Rule 21: every Save reads just "Save", and there are three of them (one per form).
        assert html.count('<button type="submit" class="pl-btn pl-btn--primary">Save</button>') == 3

    def it_renders_both_empty_states(client):
        _superuser(client)
        html = client.get(_PAGE).content.decode()
        assert "No screens yet." in html
        assert "No slides yet." in html

    def it_confirms_deleting_a_screen_because_its_slides_cascade(client):
        _superuser(client)
        zone = SlideshowZoneFactory(name="Woodshop", slug="woodshop")
        html = client.get(_PAGE).content.decode()
        assert "Delete this screen" in html
        assert "pl-btn--danger" in html
        assert "margin-top:0.75rem" in html
        # The modal names the cascade, and it is a SIBLING of the zones form.
        assert f"$dispatch('open-confirm', 'delete-zone-{zone.pk}')" in html
        assert "Every slide pinned to this screen is deleted too." in html
        modal_at = html.index("Every slide pinned to this screen is deleted too.")
        assert modal_at > html.index("</form>", html.index(reverse("hub_admin_slideshow_zones_save")))

    def it_renders_the_zone_setup_url_and_qr_for_a_saved_zone(client):
        from django.conf import settings

        _superuser(client)
        SlideshowZoneFactory(slug="woodshop")
        html = client.get(_PAGE).content.decode()
        assert "pl-slideshow-zone__url" in html
        # SIGNAGE_BASE_URL rides the global `surface` context processor — the new view does
        # not pass it, so this is the assertion that catches it going missing.
        assert f"{settings.SIGNAGE_BASE_URL}/woodshop/" in html
        assert "<svg" in html  # the inline QR
        assert "Copy URL" in html

    def it_renders_both_kind_groups_with_layout_in_a_class(client):
        _superuser(client)
        html = client.get(_PAGE).content.decode()
        assert "x-show=\"kind === 'custom'\"" in html
        assert "x-show=\"kind === 'announcement'\"" in html
        assert "pl-slide-fields" in html  # layout lives in a class, not inline display

    def it_renders_slides_as_compact_summary_rows_with_a_draggable_image_zone(client):
        _superuser(client)
        SlideshowSlideFactory(kind="custom", title="Flyer night")
        html = client.get(_PAGE).content.decode()
        # Collapsed-by-default summary; only the explicit Edit button reveals the editor panel.
        assert "pl-slide-summary" in html
        assert 'x-show="expanded"' in html
        assert "pl-slide-editor" in html
        assert "expanded ? 'Done' : 'Edit'" in html  # the per-row Edit toggle (a button, never a submit)
        # Upgraded image input: the shared draggable drop-zone + a recommended-size tooltip.
        assert "cls-image-upload-zone" in html
        assert "1920×1080 (16:9)" in html
        # A single delegated script drives every zone (clone-safe for "+ Add a slide"),
        # and it travelled with the markup off Site Settings.
        assert "document.getElementById('slide-rows')" in html

    def it_renders_reorder_affordances_and_a_hidden_sort_order(client):
        _superuser(client)
        SlideshowSlideFactory(kind="custom", title="Flyer night")
        html = client.get(_PAGE).content.decode()
        # Drag grip (desktop) + up/down move buttons (touch fallback) on every row.
        assert "pl-slide-grip" in html
        assert 'draggable="true"' in html
        assert 'data-move="up"' in html
        assert 'data-move="down"' in html
        # sort_order is a hidden input now — the reorder JS rewrites its value; no visible number field.
        assert '<input type="hidden" name="slides-0-sort_order"' in html
        # The reorder is persisted purely by the slides Save — one delegated handler drives it.
        assert "slides:reindex" in html

    def it_offers_only_published_announcements_in_the_picker(client):
        _superuser(client)
        GuildAnnouncementFactory(title="Live post")
        GuildAnnouncementFactory(pending=True, title="Draft post")
        html = client.get(_PAGE).content.decode()
        assert "Live post" in html
        assert "Draft post" not in html


def describe_automatic_slides_settings_save():
    def it_persists_every_switch(client):
        _superuser(client)
        resp = client.post(
            _PAGE,
            {
                "signage_default_slide_seconds": "20",
                "signage_event_days_ahead": "14",
                # Unchecked booleans simply don't post; send only the ones staying on.
                "signage_show_classes": "on",
                "signage_show_guilds": "on",
                "signage_tour_url": "https://www.pastlives.space/tours",
            },
        )
        assert resp.status_code == 302
        assert resp["Location"] == _PAGE
        config = SiteConfiguration.load()
        assert config.signage_default_slide_seconds == 20
        assert config.signage_event_days_ahead == 14
        assert config.signage_show_classes is True
        assert config.signage_show_guilds is True
        assert config.signage_show_events is False
        assert config.signage_show_calendar is False
        assert config.signage_show_voting is False
        assert config.signage_show_directory is False
        assert config.signage_show_teach is False
        assert config.signage_show_tour is False
        assert config.signage_tour_url == "https://www.pastlives.space/tours"

    def it_re_renders_with_the_typed_value_on_an_invalid_save(client):
        _superuser(client)
        resp = client.post(_PAGE, {"signage_default_slide_seconds": "nope", "signage_event_days_ahead": "14"})
        assert resp.status_code == 200  # re-rendered, NOT redirected away
        html = resp.content.decode()
        assert 'value="nope"' in html
        assert SiteConfiguration.load().signage_default_slide_seconds != 0


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

    def it_re_renders_with_the_typed_rows_on_an_invalid_save(client):
        _superuser(client)
        existing = SlideshowZoneFactory(name="Woodshop", slug="woodshop")
        data = {
            **_zone_mgmt(1, 1),
            "zones-0-id": str(existing.pk),
            "zones-0-name": "Renamed Woodshop",
            "zones-0-slug": "not a valid slug",
            "zones-0-is_enabled": "on",
            "zones-0-sort_order": "0",
        }
        resp = client.post(reverse("hub_admin_slideshow_zones_save"), data)
        # The old behaviour redirected and threw the admin's edits away, pointing at
        # highlighted fields that were never rendered. It re-renders them now.
        assert resp.status_code == 200
        html = resp.content.decode()
        assert 'value="Renamed Woodshop"' in html
        assert "pl-field-error" in html
        existing.refresh_from_db()
        assert existing.name == "Woodshop"  # nothing persisted


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
        # Save POSTs it. Simulate the drag that lifts "Second" above "First".
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

    def it_skips_a_blank_add_row(client):
        _superuser(client)
        data = {
            **_slide_mgmt(1, 0),
            "slides-0-kind": "custom",
            "slides-0-title": "",
            "slides-0-body": "",
            "slides-0-link_url": "",
            # A changed sort_order makes the row "touched" without giving it any content,
            # which is exactly the +Add row an admin opens and abandons.
            "slides-0-sort_order": "3",
            "slides-0-is_enabled": "on",
        }
        resp = client.post(reverse("hub_admin_slideshow_slides_save"), data)
        assert resp.status_code == 302
        assert SlideshowSlide.objects.count() == 0

    def it_deletes_a_flagged_row_and_keeps_the_others(client):
        _superuser(client)
        keep = SlideshowSlideFactory(kind="custom", title="Keep", sort_order=0)
        drop = SlideshowSlideFactory(kind="custom", title="Drop", sort_order=1)
        data = {
            **_slide_mgmt(2, 2),
            "slides-0-id": str(keep.pk),
            "slides-0-kind": "custom",
            "slides-0-title": "Keep",
            "slides-0-body": "",
            "slides-0-link_url": "",
            "slides-0-sort_order": "0",
            "slides-0-is_enabled": "on",
            "slides-1-id": str(drop.pk),
            "slides-1-kind": "custom",
            "slides-1-title": "Drop",
            "slides-1-body": "",
            "slides-1-link_url": "",
            "slides-1-sort_order": "1",
            "slides-1-is_enabled": "on",
            "slides-1-DELETE": "on",
        }
        resp = client.post(reverse("hub_admin_slideshow_slides_save"), data)
        assert resp.status_code == 302
        assert SlideshowSlide.objects.filter(pk=keep.pk).exists()
        assert not SlideshowSlide.objects.filter(pk=drop.pk).exists()

    def it_re_renders_with_the_typed_row_when_a_custom_slide_has_no_title_or_image(client):
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
        # Re-rendered with the row intact rather than redirected: the invalid row is still
        # not persisted, but the admin's typing survives.
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "Just a body, no title or image" in html
        assert "Give the slide a title or an image." in html
        assert SlideshowSlide.objects.count() == 0
