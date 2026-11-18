"""Kiln ticket photos upload one by one as they are picked (#1246): ``kiln:photo_new`` and ``kiln:photo_add``."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from kiln.models import KilnTicket
from kiln.services import TicketDeleted, add_ticket_photo
from tests.kiln.conftest import photo_upload, set_kiln_open
from tests.kiln.factories import KilnTicketFactory, KilnTicketPhotoFactory

pytestmark = pytest.mark.django_db

NEW = reverse("kiln:photo_new")


@pytest.fixture(autouse=True)
def _ceramics_guild(kiln_guild):
    """The guild that makes the crew (``maker_type_for`` reads it)."""


def _add(ticket: KilnTicket) -> str:
    return reverse("kiln:photo_add", args=[ticket.pk])


def _not_a_photo() -> SimpleUploadedFile:
    return SimpleUploadedFile("notes.jpg", b"not an image at all", content_type="image/jpeg")


def describe_the_first_photo_on_a_new_ticket():
    def it_starts_a_draft_for_the_maker_with_the_photo_as_its_cover(maker_client, maker):
        response = maker_client.post(NEW, {"photo": photo_upload()})

        assert response.status_code == 200
        ticket = KilnTicket.objects.get(maker=maker)
        assert ticket.status == KilnTicket.Status.DRAFT
        assert ticket.maker_type == KilnTicket.MakerType.MEMBER
        photo = ticket.photos.get()
        assert photo.is_cover
        body = response.json()
        assert body["edit_url"] == reverse("kiln:edit", args=[ticket.pk])
        assert body["upload_url"] == reverse("kiln:photo_add", args=[ticket.pk])
        assert f'data-kiln-photo="{photo.pk}"' in body["tile"]
        assert 'alt="Photo 1, the cover"' in body["tile"]

    def it_starts_no_draft_when_the_photo_is_refused(maker_client, maker):
        response = maker_client.post(NEW, {"photo": _not_a_photo()})

        assert response.status_code == 400
        assert response.json()["error"]
        assert not KilnTicket.objects.filter(maker=maker).exists()

    def it_asks_for_a_photo_when_none_is_posted(maker_client, maker):
        response = maker_client.post(NEW, {})

        assert response.status_code == 400
        assert response.json() == {"error": "Take or choose a photo."}
        assert not KilnTicket.objects.filter(maker=maker).exists()

    def it_answers_only_a_post(maker_client):
        assert maker_client.get(NEW).status_code == 405

    def it_is_closed_with_the_launch_switch(maker_client, maker):
        set_kiln_open(False)

        assert maker_client.post(NEW, {"photo": photo_upload()}).status_code == 404
        assert not KilnTicket.objects.filter(maker=maker).exists()


def describe_a_new_ticket_forms_token():
    def it_adds_a_second_upload_with_the_same_token_to_the_draft_the_first_started(maker_client, maker):
        first = maker_client.post(NEW, {"photo": photo_upload(), "new_token": "form-1"})
        # The reply to the first was lost, so the page sends again to the new ticket address.
        again = maker_client.post(NEW, {"photo": photo_upload(), "new_token": "form-1"})

        assert first.status_code == again.status_code == 200
        ticket = KilnTicket.objects.get(maker=maker)
        assert ticket.photos.count() == 2
        assert again.json()["edit_url"] == reverse("kiln:edit", args=[ticket.pk])

    def it_starts_a_separate_draft_for_another_token(maker_client, maker):
        maker_client.post(NEW, {"photo": photo_upload(), "new_token": "form-1"})
        maker_client.post(NEW, {"photo": photo_upload(), "new_token": "form-2"})

        assert KilnTicket.objects.filter(maker=maker).count() == 2

    def it_ignores_a_token_that_points_at_another_makers_ticket(maker_client, maker, make_member):
        theirs = KilnTicketFactory(maker=make_member())
        session = maker_client.session
        session["kiln-new-form-1"] = theirs.pk
        session.save()

        assert maker_client.post(NEW, {"photo": photo_upload(), "new_token": "form-1"}).status_code == 200

        assert not theirs.photos.exists()
        mine = KilnTicket.objects.get(maker=maker)
        assert mine.photos.count() == 1
        assert maker_client.session["kiln-new-form-1"] == mine.pk

    def it_starts_fresh_when_the_tokens_draft_was_loaded(maker_client, maker):
        maker_client.post(NEW, {"photo": photo_upload(), "new_token": "form-1"})
        KilnTicket.objects.filter(maker=maker).update(status=KilnTicket.Status.LOADED)

        maker_client.post(NEW, {"photo": photo_upload(), "new_token": "form-1"})

        assert KilnTicket.objects.filter(maker=maker).count() == 2


def describe_a_photo_on_the_makers_own_ticket():
    def it_adds_the_photo_after_the_cover(maker_client, maker):
        ticket = KilnTicketFactory(maker=maker)
        KilnTicketPhotoFactory(ticket=ticket, is_cover=True)

        response = maker_client.post(_add(ticket), {"photo": photo_upload()})

        assert response.status_code == 200
        added = ticket.photos.order_by("pk").last()
        assert added is not None and not added.is_cover
        tile = response.json()["tile"]
        assert f'value="cover:{added.pk}"' in tile
        assert 'aria-label="Remove photo 2"' in tile
        assert response.json()["edit_url"] == reverse("kiln:edit", args=[ticket.pk])

    def it_adds_to_a_ticket_already_in_the_queue(maker_client, maker):
        ticket = KilnTicketFactory(maker=maker, ready=True)
        ticket.submit()

        assert maker_client.post(_add(ticket), {"photo": photo_upload()}).status_code == 200
        assert ticket.photos.count() == 2

    def it_keeps_the_photos_already_stored_when_a_later_one_fails(maker_client, maker):
        ticket = KilnTicketFactory(maker=maker)
        maker_client.post(_add(ticket), {"photo": photo_upload()})

        response = maker_client.post(_add(ticket), {"photo": _not_a_photo()})

        assert response.status_code == 400
        assert ticket.photos.count() == 1

    def it_keeps_the_stored_photos_when_a_later_save_is_refused(maker_client, maker):
        ticket = KilnTicketFactory(maker=maker)
        maker_client.post(_add(ticket), {"photo": photo_upload()})

        # Submit with no answers is refused; the photo uploaded before it stays on the ticket.
        response = maker_client.post(reverse("kiln:edit", args=[ticket.pk]), {"action": "submit"})

        assert response.status_code == 200 and response.context["form"].errors
        assert ticket.photos.count() == 1

    def it_refuses_a_ticket_the_crew_has_loaded(maker_client, maker):
        ticket = KilnTicketFactory(maker=maker, status=KilnTicket.Status.LOADED)

        response = maker_client.post(_add(ticket), {"photo": photo_upload()})

        assert response.status_code == 409
        assert response.json()["error"] == "This ticket can no longer change, so the photo was not kept."
        assert not ticket.photos.exists()

    def it_answers_404_on_another_makers_ticket(maker_client, make_member):
        ticket = KilnTicketFactory(maker=make_member())

        assert maker_client.post(_add(ticket), {"photo": photo_upload()}).status_code == 404
        assert not ticket.photos.exists()


def describe_add_ticket_photo():
    def it_refuses_a_ticket_deleted_since_the_form_opened(maker):
        ticket = KilnTicketFactory(maker=maker)
        stale: Any = KilnTicket.objects.get(pk=ticket.pk)
        ticket.delete()

        with pytest.raises(TicketDeleted):
            add_ticket_photo(maker, stale, photo_upload())

    def it_records_a_crew_makers_type_on_the_draft(crew):
        photo = add_ticket_photo(crew, None, photo_upload())

        assert photo.ticket.maker == crew
        assert photo.ticket.maker_type == KilnTicket.MakerType.STAFF
