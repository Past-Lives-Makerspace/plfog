"""BDD specs for EventImage, the gallery row on a Calendar event (#831, part 3)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from PIL import Image

from membership.models import EventImage
from tests.membership.factories import CommunityEventFactory, EventImageFactory

pytestmark = pytest.mark.django_db


def describe_EventImage():
    def it_names_its_event():
        image = EventImageFactory(event=CommunityEventFactory(title="Zine Night"))
        assert str(image) == f"Image #{image.pk} for Zine Night"

    def it_orders_by_sort_order_then_created_at():
        event = CommunityEventFactory()
        later = EventImageFactory(event=event, sort_order=1)
        first = EventImageFactory(event=event, sort_order=0)
        assert list(event.gallery_images.all()) == [first, later]

    def it_normalizes_an_upload_to_the_gallery_cap(settings):
        settings.IMAGE_MAX_LONG_EDGE_GALLERY = 2
        image = EventImageFactory()
        with image.image.open("rb") as handle:
            assert max(Image.open(handle).size) == 2


def describe_object_position():
    def it_is_the_centre_without_a_focal_point():
        assert EventImage().object_position == "50% 50%"
        assert EventImage(focus_x=10).object_position == "50% 50%"

    def it_is_the_focal_point_as_percentages():
        assert EventImage(focus_x=20, focus_y=80).object_position == "20% 80%"


def describe_set_focus():
    def it_stores_a_point():
        image = EventImageFactory()
        image.set_focus((20, 80))
        image.refresh_from_db()
        assert (image.focus_x, image.focus_y) == (20, 80)

    def it_clears_back_to_the_centre():
        image = EventImageFactory(focus_x=20, focus_y=80)
        image.set_focus(None)
        image.refresh_from_db()
        assert (image.focus_x, image.focus_y) == (None, None)


def describe_set_alt_text():
    def it_stores_the_text_cut_to_the_column():
        image = EventImageFactory()
        image.set_alt_text("y" * 300)
        image.refresh_from_db()
        assert image.alt_text == "y" * 255


def describe_remember_eventbrite_image():
    def it_writes_the_column_without_a_save():
        image = EventImageFactory()
        with patch.object(EventImage, "save") as save:
            image.remember_eventbrite_image("media-1")
        save.assert_not_called()
        image.refresh_from_db()
        assert image.eventbrite_image_id == "media-1"


def describe_clean():
    def it_refuses_an_eleventh_photo():
        event = CommunityEventFactory()
        for order in range(10):
            EventImageFactory(event=event, sort_order=order)
        with pytest.raises(ValidationError, match="at most 10 photos"):
            EventImage(event=event, image="events/images/x.jpg").clean()

    def it_leaves_an_existing_row_out_of_its_own_count():
        event = CommunityEventFactory()
        rows = [EventImageFactory(event=event, sort_order=order) for order in range(10)]
        rows[-1].clean()  # the tenth row editing itself is not an eleventh


def describe_remove():
    def it_drops_the_row_and_the_file_nobody_else_shows():
        image = EventImageFactory()
        name = image.image.name
        image.remove()
        assert not EventImage.objects.filter(pk=image.pk).exists()
        assert not default_storage.exists(name)

    def it_keeps_a_file_another_row_still_shows():
        image = EventImageFactory()
        name = image.image.name
        EventImage.objects.create(event=CommunityEventFactory(), image=name)
        image.remove()
        assert default_storage.exists(name)


def describe_gallery_display_images():
    def it_lists_the_rows_as_the_gallery_component_expects_them():
        event = CommunityEventFactory(title="Zine Night")
        named = EventImageFactory(event=event, sort_order=0, alt_text="Loom", focus_x=20, focus_y=80)
        unnamed = EventImageFactory(event=event, sort_order=1)
        assert event.gallery_display_images == [
            {"url": named.image.url, "alt": "Loom", "position": "20% 80%"},
            {"url": unnamed.image.url, "alt": "Zine Night", "position": "50% 50%"},
        ]

    def it_is_empty_without_a_gallery():
        assert CommunityEventFactory().gallery_display_images == []
