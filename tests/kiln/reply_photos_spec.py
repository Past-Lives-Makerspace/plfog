"""A photo on a crew message or an unload note (#1026).

The crew may attach one optional photo to a message on another maker's ticket and to the
note on a piece they untick at an unload. It is checked and resized like a ticket photo,
shows under its message for the maker and the crew, and the emails say it is there and link
to the ticket. The maker's own message box takes no photo, whatever is posted.
"""

from __future__ import annotations

from unittest.mock import patch

from pathlib import Path

import pytest
from django.conf import settings
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from kiln.forms import ExceptionNote
from kiln.models import TILE_LONG_EDGE, KilnReply, KilnTicket
from kiln.services import TicketDeleted, delete_ticket, load_kiln, post_reply, unload_kiln
from membership.models import Member
from tests.kiln.conftest import photo_upload, signed_in
from tests.kiln.factories import KilnTicketFactory, KilnTicketPhotoFactory

pytestmark = pytest.mark.django_db

Status = KilnTicket.Status
Outcome = KilnReply.Outcome


def _ticket(maker: Member) -> KilnTicket:
    ticket = KilnTicketFactory(maker=maker, status=Status.SUBMITTED, firing_type="glaze", submitted_at=timezone.now())
    KilnTicketPhotoFactory(ticket=ticket, is_cover=True)
    return ticket


def _reply_url(ticket: KilnTicket) -> str:
    return reverse("kiln:reply", args=[ticket.pk])


def _detail(ticket: KilnTicket) -> str:
    return reverse("kiln:detail", args=[ticket.pk])


def _reply_files() -> set[Path]:
    """Every reply photo file on storage, to show a refused or ignored post wrote none."""
    root = Path(settings.MEDIA_ROOT) / "kiln" / "replies"
    return {path for path in root.rglob("*") if path.is_file()} if root.exists() else set()


def _toasts(response) -> list[str]:
    return [str(m) for m in response.context["messages"]]


def _exists(reply: KilnReply) -> bool:
    return all(file.storage.exists(file.name) for file in reply.photo_files)


def _not_a_photo() -> SimpleUploadedFile:
    return SimpleUploadedFile("notes.jpg", b"not an image", content_type="image/jpeg")


def describe_a_crew_message_with_a_photo():
    def it_saves_the_message_and_the_photo_at_both_sizes(crew_client, maker):
        ticket = _ticket(maker)

        response = crew_client.post(
            _reply_url(ticket),
            {"body": "Is this a crack?", "photo": photo_upload(size=(3000, 2000))},
            follow=True,
        )

        reply = ticket.replies.get()
        assert reply.body == "Is this a crack?" and reply.has_photo and _exists(reply)
        assert reply.image.name.startswith("kiln/replies/photos/")
        assert reply.tile.name.startswith("kiln/replies/tiles/")
        with Image.open(reply.tile.path) as tile, Image.open(reply.image.path) as image:
            assert max(tile.size) == TILE_LONG_EDGE
            assert max(image.size) <= settings.IMAGE_MAX_LONG_EDGE_GALLERY and max(image.size) > TILE_LONG_EDGE
        assert "Message sent." in _toasts(response)

    def it_shows_the_photo_under_its_message_to_the_maker_and_the_crew(crew, crew_client, maker, maker_client):
        ticket = _ticket(maker)
        crew_client.post(_reply_url(ticket), {"body": "See the foot.", "photo": photo_upload()})
        reply = ticket.replies.get()

        for client in (maker_client, crew_client):
            body = client.get(_detail(ticket)).content.decode()
            message = body[body.index(f'data-reply="{reply.pk}"') :]
            message = message[: message.index("</li>")]
            assert f'href="{reply.image.url}"' in message and f'src="{reply.tile.url}"' in message
            assert f'alt="Photo from {crew.display_name}"' in message
            # Tapping opens the full size photo itself, never a boosted swap of the file into the page.
            assert 'target="_blank"' in message and 'hx-boost="false"' in message
            assert message.index("See the foot.") < message.index("data-kiln-reply-photo")

    def it_works_exactly_as_before_without_a_photo(crew_client, maker, maker_client):
        ticket = _ticket(maker)

        crew_client.post(_reply_url(ticket), {"body": "Loaded on the middle shelf."})

        reply = ticket.replies.get()
        assert reply.body == "Loaded on the middle shelf." and not reply.has_photo
        assert not reply.image and not reply.tile
        assert "data-kiln-reply-photo" not in maker_client.get(_detail(ticket)).content.decode()

    def it_refuses_a_file_that_is_not_a_photo_and_saves_nothing(crew_client, maker):
        ticket = _ticket(maker)
        before = _reply_files()

        response = crew_client.post(_reply_url(ticket), {"body": "Look", "photo": _not_a_photo()}, follow=True)

        assert _toasts(response) == ["That file is not a photo we can open. Choose a JPG, PNG, WEBP or HEIC image."]
        assert not KilnReply.objects.exists() and _reply_files() == before

    def it_refuses_a_photo_over_the_size_cap_like_a_ticket_photo(crew_client, maker):
        ticket = _ticket(maker)

        with override_settings(MAX_UPLOAD_IMAGE_BYTES=100):
            response = crew_client.post(_reply_url(ticket), {"body": "Look", "photo": photo_upload()}, follow=True)

        assert _toasts(response)[0].startswith("Image must be 0.0 MB or smaller")
        assert not KilnReply.objects.exists()

    def it_still_asks_for_the_message_when_only_a_photo_is_sent(crew_client, maker):
        ticket = _ticket(maker)
        before = _reply_files()

        response = crew_client.post(_reply_url(ticket), {"body": " ", "photo": photo_upload()}, follow=True)

        assert _toasts(response) == ["Write a message first."]
        assert not KilnReply.objects.exists() and _reply_files() == before


def describe_a_makers_post_carrying_a_photo():
    def it_stores_the_message_and_no_photo(maker, maker_client, kiln_guild):
        ticket = _ticket(maker)
        before = _reply_files()

        # Crafted: the maker's box has no photo input, so only a hand made POST carries one.
        response = maker_client.post(_reply_url(ticket), {"body": "Here is mine", "photo": photo_upload()}, follow=True)

        reply = ticket.replies.get()
        assert reply.body == "Here is mine" and not reply.has_photo
        assert _reply_files() == before
        assert "Message sent." in _toasts(response)

    def it_ignores_even_a_file_that_is_not_a_photo(maker, maker_client, kiln_guild):
        ticket = _ticket(maker)

        maker_client.post(_reply_url(ticket), {"body": "Hi", "photo": _not_a_photo()})

        assert ticket.replies.get().body == "Hi"

    def it_stores_no_photo_for_a_crew_member_writing_on_their_own_ticket(crew, crew_client, kiln_guild):
        ticket = _ticket(crew)
        before = _reply_files()

        crew_client.post(_reply_url(ticket), {"body": "Mine", "photo": photo_upload()})

        assert not ticket.replies.get().has_photo and _reply_files() == before

    def it_is_refused_by_the_service_too(maker):
        ticket = _ticket(maker)
        before = _reply_files()

        reply = post_reply(ticket, maker, "From the maker", photo=photo_upload())

        assert not reply.has_photo and _reply_files() == before


def describe_the_message_boxes():
    def it_gives_the_crew_a_take_or_choose_photo_control_on_a_multipart_unboosted_form(crew_client, maker):
        body = crew_client.get(_detail(_ticket(maker))).content.decode()

        form = body[body.index('class="pl-kiln-reply" enctype="multipart/form-data" hx-boost="false"') :]
        form = form[: form.index("</form>")]
        assert form.count('name="photo"') == 2
        assert 'name="photo" accept="image/*" capture="environment"' in form
        assert "Take a photo" in form and "Choose a photo" in form
        assert "js/kiln_photo_pick" in body

    def it_leaves_the_makers_box_unchanged(maker, maker_client, kiln_guild):
        body = maker_client.get(_detail(_ticket(maker))).content.decode()

        form = body[body.index(f'action="{reverse("kiln:reply", args=[KilnTicket.objects.get().pk])}"') :]
        form = form[: form.index("</form>")]
        assert 'type="file"' not in form and "multipart" not in body[body.index("data-kiln-thread") :]
        assert "Reply to the crew" in form and "js/kiln_photo_pick" not in body


def describe_a_message_deleted_meanwhile():
    def it_removes_the_photo_files_it_wrote(crew, maker):
        ticket = _ticket(maker)
        before = _reply_files()
        KilnTicket.objects.filter(pk=ticket.pk).delete()

        with pytest.raises(TicketDeleted):
            post_reply(ticket, crew, "Too late", photo=photo_upload())

        assert _reply_files() == before and not KilnReply.objects.exists()


def describe_the_crew_message_email():
    def it_says_a_photo_is_attached_and_links_the_ticket(crew, maker, kiln_guild):
        ticket = _ticket(maker)

        post_reply(ticket, crew, "Is this an air bubble?", photo=photo_upload())

        url = f"{settings.MEMBER_BASE_URL}{_detail(ticket)}#kiln-messages"
        email = mail.outbox[0]
        assert f"A photo is attached. Open the ticket to see it: {url}" in email.body
        html = email.alternatives[0][0]
        assert f'href="{url}">A photo is attached. Open the ticket to see it</a>' in html
        # A link to the ticket, never the image itself.
        assert "<img" not in html.split("Is this an air bubble?")[1].split("Read And Reply")[0]
        assert "kiln/replies" not in email.body and "kiln/replies" not in html

    def it_says_nothing_about_a_photo_when_there_is_none(crew, maker, kiln_guild):
        post_reply(_ticket(maker), crew, "Quick check")

        email = mail.outbox[0]
        assert "photo" not in email.body and "[missing" not in email.body
        assert "photo" not in email.alternatives[0][0] and "[missing" not in email.alternatives[0][0]


def _loaded(by: Member, *tickets: KilnTicket):
    result = load_kiln(firing_type="glaze", ticket_pks=[t.pk for t in tickets], by=by)
    assert result.firing is not None
    return result.firing


def describe_an_unload_note_with_a_photo():
    def it_saves_an_unticked_pieces_photo_on_that_notes_reply(crew, crew_client, maker, make_member, kiln_guild):
        out, back = _ticket(make_member()), _ticket(maker)
        firing = _loaded(crew, out, back)
        before = _reply_files()

        crew_client.post(
            reverse("kiln:unload", args=[firing.pk]),
            {
                "fired": [out.pk],
                f"what-{back.pk}": "cracked",
                f"note-{back.pk}": "It cracked at the foot.",
                f"next-{back.pk}": "back_to_queue",
                f"photo-{back.pk}": photo_upload(),
                # A ticked (fired) piece's photo is ignored, even one that would not pass.
                f"photo-{out.pk}": _not_a_photo(),
            },
        )

        note = KilnReply.objects.get()
        assert (note.ticket_id, note.outcome) == (back.pk, Outcome.BACK_TO_QUEUE)
        assert note.has_photo and _exists(note)
        with Image.open(note.tile.path) as tile:
            assert max(tile.size) == TILE_LONG_EDGE
        assert len(_reply_files() - before) == 2  # the note's photo and tile, nothing for the fired piece
        assert KilnTicket.objects.get(pk=out.pk).status == Status.FIRED

    def it_ignores_a_ticked_pieces_good_photo_too(crew, crew_client, maker, kiln_guild):
        ticket = _ticket(maker)
        firing = _loaded(crew, ticket)
        before = _reply_files()

        crew_client.post(
            reverse("kiln:unload", args=[firing.pk]), {"fired": [ticket.pk], f"photo-{ticket.pk}": photo_upload()}
        )

        assert KilnTicket.objects.get(pk=ticket.pk).status == Status.FIRED
        assert not KilnReply.objects.exists() and _reply_files() == before

    def it_works_exactly_as_before_without_a_photo(crew, crew_client, maker, kiln_guild):
        ticket = _ticket(maker)
        firing = _loaded(crew, ticket)

        crew_client.post(
            reverse("kiln:unload", args=[firing.pk]),
            {f"what-{ticket.pk}": "stuck", f"note-{ticket.pk}": "Stuck.", f"next-{ticket.pk}": "fired"},
        )

        note = KilnReply.objects.get()
        assert note.body == "Stuck." and not note.has_photo

    def it_keeps_the_page_and_says_why_when_the_photo_is_not_a_photo(crew, crew_client, maker, kiln_guild):
        ticket = _ticket(maker)
        firing = _loaded(crew, ticket)

        response = crew_client.post(
            reverse("kiln:unload", args=[firing.pk]),
            {
                f"what-{ticket.pk}": "stuck",
                f"note-{ticket.pk}": "Stuck.",
                f"next-{ticket.pk}": "fired",
                f"photo-{ticket.pk}": _not_a_photo(),
            },
        )

        assert response.status_code == 200
        assert "That file is not a photo we can open." in response.content.decode()
        assert KilnTicket.objects.get(pk=ticket.pk).status == Status.LOADED and not KilnReply.objects.exists()

    def it_gives_each_unticked_row_the_photo_control_on_a_multipart_unboosted_form(crew, crew_client, maker):
        ticket = _ticket(maker)
        firing = _loaded(crew, ticket)

        body = crew_client.get(reverse("kiln:unload", args=[firing.pk])).content.decode()

        assert 'enctype="multipart/form-data" hx-boost="false" data-kiln-unload' in body
        assert body.count(f'name="photo-{ticket.pk}"') == 2
        assert f'name="photo-{ticket.pk}" accept="image/*" capture="environment"' in body


def describe_the_ready_for_pickup_email():
    def it_says_a_photo_is_attached_and_links_the_ticket(crew, maker, kiln_guild):
        ticket = _ticket(maker)
        firing = _loaded(crew, ticket)
        note = ExceptionNote(
            ticket_pk=ticket.pk,
            what_happened="glaze_ran",
            note="Ran onto the shelf.",
            outcome="fired",
            photo=photo_upload(),
        )

        unload_kiln(firing_pk=firing.pk, exceptions={ticket.pk: note}, by=crew)

        url = f"{settings.MEMBER_BASE_URL}{_detail(ticket)}"
        email = mail.outbox[0]
        assert f"  A photo is attached. Open the ticket to see it: {url}" in email.body
        html = email.alternatives[0][0]
        assert f'<br><a href="{url}" style="color:#092E4C;">A photo is attached. Open the ticket to see it</a>.' in html
        assert "kiln/replies" not in email.body and "kiln/replies" not in html
        from core.events.registry import KILN_READY_FOR_PICKUP
        from core.models import Notification

        bell = Notification.objects.get(user=maker.user, trigger=KILN_READY_FOR_PICKUP)
        assert bell.body.endswith("The kiln crew left you a note with a photo.")

    def it_says_nothing_about_a_photo_for_a_note_without_one(crew, maker, kiln_guild):
        ticket = _ticket(maker)
        firing = _loaded(crew, ticket)
        note = ExceptionNote(ticket_pk=ticket.pk, what_happened="cracked", note="Cracked.", outcome="fired")

        unload_kiln(firing_pk=firing.pk, exceptions={ticket.pk: note}, by=crew)

        assert "photo" not in mail.outbox[0].body and "photo" not in mail.outbox[0].alternatives[0][0]


def describe_deleting_the_ticket():
    def it_deletes_the_reply_photo_files_only_once_the_delete_commits(
        crew, maker, kiln_guild, django_capture_on_commit_callbacks
    ):
        ticket = _ticket(maker)
        reply = post_reply(ticket, crew, "See this", photo=photo_upload())
        assert _exists(reply)

        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            delete_ticket(ticket_pk=ticket.pk, by=maker)
        assert _exists(reply)  # still there until the delete commits
        assert not KilnReply.objects.filter(pk=reply.pk).exists()

        for callback in callbacks:
            callback()
        assert not any(file.storage.exists(file.name) for file in reply.photo_files)

    def it_keeps_the_reply_photo_files_when_the_delete_rolls_back(
        crew, maker, kiln_guild, django_capture_on_commit_callbacks
    ):
        from django.db import transaction

        ticket = _ticket(maker)
        reply = post_reply(ticket, crew, "See this", photo=photo_upload())

        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    ticket.delete_with_photos()
                    raise RuntimeError("the delete failed")
            except RuntimeError:
                pass

        assert callbacks == [] and _exists(reply)
        assert KilnReply.objects.filter(pk=reply.pk).exists()

    def it_deletes_an_unload_notes_photo_with_the_ticket(crew, maker, kiln_guild, django_capture_on_commit_callbacks):
        ticket = _ticket(maker)
        firing = _loaded(crew, ticket)
        note = ExceptionNote(
            ticket_pk=ticket.pk, what_happened="cracked", note="Cracked.", outcome="fired", photo=photo_upload()
        )
        unload_kiln(firing_pk=firing.pk, exceptions={ticket.pk: note}, by=crew)
        reply = KilnReply.objects.get()

        with django_capture_on_commit_callbacks(execute=True):
            signed_in(crew).post(reverse("kiln:delete", args=[ticket.pk]), {"note": "Broke on the cart."})

        assert not KilnTicket.objects.filter(pk=ticket.pk).exists()
        assert not any(file.storage.exists(file.name) for file in reply.photo_files)


def _too_many_pixels() -> SimpleUploadedFile:
    """A small file that declares a huge bitmap: 8000 by 7000 pixels, over the 50 million cap."""
    import io

    buffer = io.BytesIO()
    Image.new("1", (8000, 7000)).save(buffer, format="PNG")
    assert len(buffer.getvalue()) < 1_000_000
    return SimpleUploadedFile("huge.png", buffer.getvalue(), content_type="image/png")


def describe_an_image_with_too_many_pixels():
    def it_is_refused_on_a_crew_message_before_it_is_resized(crew_client, maker, monkeypatch):
        ticket = _ticket(maker)
        before = _reply_files()
        resized: list[object] = []
        monkeypatch.setattr("kiln.models.normalize_image", lambda *a, **k: resized.append(a))

        response = crew_client.post(_reply_url(ticket), {"body": "Look", "photo": _too_many_pixels()}, follow=True)

        assert _toasts(response)[0].startswith("That image is 8,000 by 7,000 pixels, too large to open.")
        assert resized == [] and not KilnReply.objects.exists() and _reply_files() == before

    def it_is_refused_as_a_ticket_photo_too(maker, maker_client, monkeypatch):
        resized: list[object] = []
        monkeypatch.setattr("kiln.models.normalize_image", lambda *a, **k: resized.append(a))

        response = maker_client.post(reverse("kiln:new"), {"action": "draft", "photos": [_too_many_pixels()]})

        assert response.status_code == 200
        errors = [str(e) for e in response.context["form"].errors["__all__"]]
        assert any(e.startswith("That image is 8,000 by 7,000 pixels") for e in errors)
        assert resized == [] and not KilnTicket.objects.filter(maker=maker).exists()

    def it_is_refused_on_an_unload_note(crew, crew_client, maker, kiln_guild):
        ticket = _ticket(maker)
        firing = _loaded(crew, ticket)

        response = crew_client.post(
            reverse("kiln:unload", args=[firing.pk]),
            {
                f"what-{ticket.pk}": "cracked",
                f"note-{ticket.pk}": "Cracked.",
                f"next-{ticket.pk}": "fired",
                f"photo-{ticket.pk}": _too_many_pixels(),
            },
        )

        assert response.status_code == 200 and "too large to open" in response.content.decode()
        assert not KilnReply.objects.exists()


def describe_a_refused_photo_on_the_message_box():
    def it_keeps_the_typed_message_in_the_box(crew_client, maker):
        ticket = _ticket(maker)

        response = crew_client.post(
            _reply_url(ticket), {"body": "The foot looks <thick> here.", "photo": _not_a_photo()}, follow=True
        )

        assert response.redirect_chain[-1][0] == f"{_detail(ticket)}#kiln-messages"
        assert "That file is not a photo we can open." in _toasts(response)[0]
        assert response.context["reply_form"]["body"].value() == "The foot looks <thick> here."
        assert "The foot looks &lt;thick&gt; here.</textarea>" in response.content.decode()
        # Put back once: opening the ticket again starts with an empty box.
        assert crew_client.get(_detail(ticket)).context["reply_form"]["body"].value() == ""

    def it_starts_empty_after_a_message_is_sent(crew_client, maker):
        ticket = _ticket(maker)

        response = crew_client.post(_reply_url(ticket), {"body": "Sent fine."}, follow=True)

        assert response.context["reply_form"]["body"].value() == ""

    def it_gives_the_photo_control_the_size_cap_for_the_check_on_pick(crew, crew_client, maker):
        ticket = _ticket(maker)
        firing = _loaded(crew, ticket)
        cap = f'data-max-bytes="{settings.MAX_UPLOAD_IMAGE_BYTES}"'

        assert cap in crew_client.get(_detail(ticket)).content.decode()
        assert cap in crew_client.get(reverse("kiln:unload", args=[firing.pk])).content.decode()


def describe_unload_photos_and_the_locks():
    def it_resizes_every_photo_before_locking_the_firing(crew, maker, kiln_guild, monkeypatch):
        import kiln.models
        from kiln.models import KilnFiring

        ticket = _ticket(maker)
        firing = _loaded(crew, ticket)
        order: list[str] = []
        store, lock = kiln.models.store_photo, KilnFiring.objects.select_for_update

        def storing(*args, **kwargs):
            order.append("resize")
            return store(*args, **kwargs)

        def locking(*args, **kwargs):
            order.append("lock")
            return lock(*args, **kwargs)

        monkeypatch.setattr(kiln.models, "store_photo", storing)
        monkeypatch.setattr(KilnFiring.objects, "select_for_update", locking)
        note = ExceptionNote(
            ticket_pk=ticket.pk, what_happened="cracked", note="Cracked.", outcome="fired", photo=photo_upload()
        )

        unload_kiln(firing_pk=firing.pk, exceptions={ticket.pk: note}, by=crew)

        assert order == ["resize", "lock"]
        reply = KilnReply.objects.get()
        assert reply.has_photo and _exists(reply) and reply.firing_id == firing.pk

    def it_removes_the_photos_when_the_firing_was_already_unloaded(crew, maker, kiln_guild):
        from kiln.services import FiringAlreadyUnloaded

        ticket = _ticket(maker)
        firing = _loaded(crew, ticket)
        unload_kiln(firing_pk=firing.pk, exceptions={}, by=crew)
        before = _reply_files()
        note = ExceptionNote(
            ticket_pk=ticket.pk, what_happened="cracked", note="Cracked.", outcome="fired", photo=photo_upload()
        )

        with pytest.raises(FiringAlreadyUnloaded):
            unload_kiln(firing_pk=firing.pk, exceptions={ticket.pk: note}, by=crew)

        assert _reply_files() == before and not KilnReply.objects.exists()

    def it_removes_a_photo_whose_ticket_left_the_firing(crew, maker, kiln_guild, django_capture_on_commit_callbacks):
        inside, gone = _ticket(maker), _ticket(maker)
        firing = _loaded(crew, inside, gone)
        KilnTicket.objects.filter(pk=gone.pk).delete()
        before = _reply_files()
        note = ExceptionNote(
            ticket_pk=gone.pk, what_happened="cracked", note="Cracked.", outcome="fired", photo=photo_upload()
        )

        with django_capture_on_commit_callbacks(execute=True):
            unload_kiln(firing_pk=firing.pk, exceptions={gone.pk: note}, by=crew)

        assert KilnTicket.objects.get(pk=inside.pk).status == Status.FIRED
        assert not KilnReply.objects.exists() and _reply_files() == before

    def it_still_tells_the_makers_when_removing_a_leftover_photo_fails(
        crew, maker, kiln_guild, django_capture_on_commit_callbacks
    ):
        inside, gone = _ticket(maker), _ticket(maker)
        firing = _loaded(crew, inside, gone)
        KilnTicket.objects.filter(pk=gone.pk).delete()
        note = ExceptionNote(
            ticket_pk=gone.pk, what_happened="cracked", note="Cracked.", outcome="fired", photo=photo_upload()
        )

        with (
            patch(
                "django.core.files.storage.FileSystemStorage.delete", side_effect=OSError("storage down")
            ) as deleting,
            django_capture_on_commit_callbacks(execute=True),
        ):
            result = unload_kiln(firing_pk=firing.pk, exceptions={gone.pk: note}, by=crew)

        assert deleting.called
        assert result.fired == 1 and result.notified == 1
        assert KilnTicket.objects.get(pk=inside.pk).status == Status.FIRED


def describe_an_unload_page_sent_back_with_errors():
    def it_tells_each_row_whose_photo_was_not_kept(crew, crew_client, maker, make_member, kiln_guild):
        with_photo, without, missing_note = _ticket(maker), _ticket(make_member()), _ticket(make_member())
        firing = _loaded(crew, with_photo, without, missing_note)

        response = crew_client.post(
            reverse("kiln:unload", args=[firing.pk]),
            {
                f"what-{with_photo.pk}": "cracked",
                f"note-{with_photo.pk}": "Cracked.",
                f"next-{with_photo.pk}": "fired",
                f"photo-{with_photo.pk}": photo_upload(),
                f"what-{without.pk}": "stuck",
                f"note-{without.pk}": "Stuck.",
                f"next-{without.pk}": "fired",
                # The error is on another row: no note for this one.
                f"what-{missing_note.pk}": "stuck",
                f"next-{missing_note.pk}": "fired",
            },
        )

        assert response.status_code == 200
        rows = {row.ticket.pk: row for row in response.context["form"].rows}
        assert [rows[t.pk].photo_dropped for t in (with_photo, without, missing_note)] == [True, False, False]
        body = response.content.decode()
        assert body.count("data-kiln-photo-dropped") == 1
        row = body[body.index(f'data-ticket="{with_photo.pk}"') :]
        assert "Pick the photo again, it was not kept." in row[: row.index("</li>")]
        assert not KilnReply.objects.exists()

    def it_says_nothing_on_a_fresh_page(crew, crew_client, maker, kiln_guild):
        firing = _loaded(crew, _ticket(maker))

        body = crew_client.get(reverse("kiln:unload", args=[firing.pk])).content.decode()

        assert "data-kiln-photo-dropped" not in body
