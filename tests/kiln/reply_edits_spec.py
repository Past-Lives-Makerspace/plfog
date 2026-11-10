"""Edit and delete a message on a kiln ticket's thread (#1140): who may, its photo, unload notes, and silence."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.core import mail
from django.core.files.storage import default_storage
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import Notification
from kiln.forms import ExceptionNote
from kiln.models import KilnFiring, KilnReply, KilnTicket
from kiln.services import firing_sheet, kiln_log, load_kiln, post_reply, unload_kiln
from membership.models import Member
from tests.kiln.conftest import photo_upload, signed_in
from tests.kiln.factories import KilnTicketFactory, KilnTicketPhotoFactory

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}
Outcome = KilnReply.Outcome


@pytest.fixture
def silent() -> Iterator[dict[str, MagicMock]]:
    """Every way a change could tell someone: the event emitter (bell, email, push) and web push."""
    with patch("core.events.emit.emit") as emit, patch("core.events.channels.send_web_push") as push:
        yield {"emit": emit, "push": push}


@pytest.fixture
def admin(make_member) -> Member:
    """A site admin who is not on the kiln crew."""
    return make_member(fog_role=Member.FogRole.ADMIN)


def _ticket(maker: Member) -> KilnTicket:
    ticket = KilnTicketFactory(
        maker=maker, status=KilnTicket.Status.SUBMITTED, firing_type="glaze", submitted_at=timezone.now()
    )
    KilnTicketPhotoFactory(ticket=ticket, is_cover=True)
    return ticket


def _edit_url(reply: KilnReply) -> str:
    return reverse("kiln:reply_edit", args=[reply.ticket_id, reply.pk])


def _delete_url(reply: KilnReply) -> str:
    return reverse("kiln:reply_delete", args=[reply.ticket_id, reply.pk])


def _detail(ticket: KilnTicket) -> str:
    return reverse("kiln:detail", args=[ticket.pk])


def _edit(client: Client, reply: KilnReply, body: str, *, remove: bool = False, hx: bool = True) -> Any:
    data: dict[str, Any] = {"body": body, "remove_photos": [reply.pk] if remove else []}
    return client.post(_edit_url(reply), data, **(HX if hx else {}))


def _crew_photo_reply(crew: Member, ticket: KilnTicket, body: str = "Zzglaze is thin here") -> KilnReply:
    return post_reply(ticket, crew, body, photo=photo_upload())


def _unload_note(crew: Member, maker: Member, outcome: str = Outcome.BACK_TO_QUEUE) -> tuple[KilnFiring, KilnReply]:
    ticket = _ticket(maker)
    firing = load_kiln(firing_type="glaze", ticket_pks=[ticket.pk], by=crew).firing
    assert firing is not None
    note = ExceptionNote(
        ticket_pk=ticket.pk,
        what_happened="cracked",
        note="Zzcracked at the foot",
        outcome=outcome,
        photo=photo_upload(),
    )
    unload_kiln(firing_pk=firing.pk, exceptions={ticket.pk: note}, by=crew)
    return firing, KilnReply.objects.get(ticket=ticket, firing=firing)


def describe_who_may_change_a_kiln_message():
    def it_lets_the_maker_edit_and_delete_their_own(maker, maker_client, kiln_guild, silent):
        reply = post_reply(_ticket(maker), maker, "Zzis it cone 6")

        edited = _edit(maker_client, reply, "Zzis it cone 6?")
        reply.refresh_from_db()
        assert edited.status_code == 200 and reply.body == "Zzis it cone 6?" and reply.edited_at is not None
        assert "Saved." in edited["HX-Trigger"]

        deleted = maker_client.post(_delete_url(reply), **HX)
        assert deleted.status_code == 200 and deleted.content == b""
        assert not KilnReply.objects.filter(pk=reply.pk).exists()

    def it_lets_a_crew_member_edit_and_delete_their_own_message_and_photo(
        crew, crew_client, maker, kiln_guild, django_capture_on_commit_callbacks
    ):
        reply = _crew_photo_reply(crew, _ticket(maker))
        names = [reply.image.name, reply.tile.name]

        with django_capture_on_commit_callbacks(execute=True):
            _edit(crew_client, reply, "Zzglaze is thin at the rim", remove=True)

        reply.refresh_from_db()
        assert (reply.body, reply.has_photo, bool(reply.tile)) == ("Zzglaze is thin at the rim", False, False)
        assert not any(default_storage.exists(name) for name in names)

        second = _crew_photo_reply(crew, reply.ticket, "Zzsecond look")
        second_names = [second.image.name, second.tile.name]
        with django_capture_on_commit_callbacks(execute=True):
            crew_client.post(_delete_url(second), **HX)
        assert not KilnReply.objects.filter(pk=second.pk).exists()
        assert not any(default_storage.exists(name) for name in second_names)

    def it_lets_an_admin_edit_and_delete_any_message(admin, maker, kiln_guild):
        reply = post_reply(_ticket(maker), maker, "Zzmaker words")
        client = signed_in(admin)

        assert _edit(client, reply, "Zzadmin fixed").status_code == 200
        reply.refresh_from_db()
        assert reply.body == "Zzadmin fixed"
        assert client.post(_delete_url(reply), **HX).status_code == 200
        assert not KilnReply.objects.filter(pk=reply.pk).exists()

    def it_refuses_the_maker_on_a_crew_message_with_a_403(crew, maker, maker_client, kiln_guild):
        reply = post_reply(_ticket(maker), crew, "Zzcrew words")

        assert _edit(maker_client, reply, "Zzsneaky").status_code == 403
        assert maker_client.post(_delete_url(reply), **HX).status_code == 403
        reply.refresh_from_db()
        assert reply.body == "Zzcrew words" and reply.edited_at is None

    def it_refuses_the_crew_on_the_makers_message_with_a_403(crew_client, maker, kiln_guild):
        reply = post_reply(_ticket(maker), maker, "Zzmaker words")

        assert _edit(crew_client, reply, "Zzsneaky").status_code == 403
        assert crew_client.post(_delete_url(reply), **HX).status_code == 403

    def it_answers_another_maker_with_a_404(maker, make_member, kiln_guild):
        reply = post_reply(_ticket(maker), maker, "Zzmine")
        other = signed_in(make_member())

        assert _edit(other, reply, "Zzsneaky").status_code == 404
        assert other.post(_delete_url(reply), **HX).status_code == 404
        assert KilnReply.objects.filter(pk=reply.pk, body="Zzmine").exists()

    def it_is_a_404_for_a_message_on_another_ticket(maker, maker_client, kiln_guild):
        reply = post_reply(_ticket(maker), maker, "Zzmine")
        other_ticket = _ticket(maker)

        url = reverse("kiln:reply_edit", args=[other_ticket.pk, reply.pk])
        assert maker_client.post(url, {"body": "Zzmoved"}, **HX).status_code == 404

    def it_sends_a_signed_out_visitor_to_sign_in_and_takes_posts_only(maker, maker_client, kiln_guild):
        reply = post_reply(_ticket(maker), maker, "Zzmine")

        assert Client().post(_edit_url(reply), {"body": "Zzanon"}).status_code == 302
        assert maker_client.get(_edit_url(reply)).status_code == 405
        assert maker_client.get(_delete_url(reply)).status_code == 405


def describe_editing_a_kiln_message():
    def it_shows_edited_to_both_sides_and_not_on_an_untouched_message(
        crew, crew_client, maker, maker_client, kiln_guild
    ):
        ticket = _ticket(maker)
        reply = post_reply(ticket, maker, "Zzfirst words")
        post_reply(ticket, crew, "Zzcrew answer")

        response = _edit(maker_client, reply, "Zzfirst words, fixed")

        html = response.content.decode()
        assert f'data-thread-dom="{reply.dom_id}"' in html and "data-thread-edited" in html
        for client in (maker_client, crew_client):
            page = client.get(_detail(ticket)).content.decode()
            assert "Zzfirst words, fixed" in page and page.count("data-thread-edited") == 1

    def it_refuses_an_empty_message_with_no_photo(maker, maker_client, kiln_guild):
        reply = post_reply(_ticket(maker), maker, "Zzkeep me")

        response = _edit(maker_client, reply, " ")

        assert "Write something, or keep a photo." in response.content.decode()
        reply.refresh_from_db()
        assert reply.body == "Zzkeep me"

    def it_refuses_removing_the_photo_of_a_message_with_no_text(crew, crew_client, maker, kiln_guild):
        reply = _crew_photo_reply(crew, _ticket(maker))

        _edit(crew_client, reply, "", remove=True)

        reply.refresh_from_db()
        assert reply.has_photo

    def it_keeps_the_photo_when_the_text_goes(crew, crew_client, maker, kiln_guild):
        reply = _crew_photo_reply(crew, _ticket(maker))

        _edit(crew_client, reply, "")

        reply.refresh_from_db()
        assert reply.body == "" and reply.has_photo and reply.edited_at is not None

    def it_holds_the_posting_length_cap(maker, maker_client, kiln_guild):
        reply = post_reply(_ticket(maker), maker, "Zzshort")

        response = _edit(maker_client, reply, "x" * 2001, hx=False)

        assert response.status_code == 302 and response["Location"].endswith("#kiln-messages")
        reply.refresh_from_db()
        assert reply.body == "Zzshort"

    def it_writes_nothing_when_nothing_changed(maker, maker_client, kiln_guild):
        reply = post_reply(_ticket(maker), maker, "Zzsame")

        _edit(maker_client, reply, "Zzsame")

        reply.refresh_from_db()
        assert reply.edited_at is None

    def it_tells_nobody(crew, crew_client, maker, maker_client, kiln_guild, silent):
        ticket = _ticket(maker)
        mine, theirs = post_reply(ticket, maker, "Zzmaker"), post_reply(ticket, crew, "Zzcrew")
        bells = Notification.objects.count()
        mail.outbox.clear()
        silent["emit"].reset_mock()  # posting them told the other side; changing them must not

        _edit(maker_client, mine, "Zzmaker, fixed")
        _edit(crew_client, theirs, "Zzcrew, fixed")
        maker_client.post(_delete_url(mine), **HX)

        assert mail.outbox == []
        assert Notification.objects.count() == bells
        silent["emit"].assert_not_called()
        silent["push"].assert_not_called()


def describe_the_menu_on_a_ticket():
    def it_shows_the_maker_a_menu_on_their_own_message_only(crew, maker, maker_client, kiln_guild):
        ticket = _ticket(maker)
        mine, theirs = post_reply(ticket, maker, "Zzmine"), post_reply(ticket, crew, "Zztheirs")

        page = maker_client.get(_detail(ticket)).content.decode()

        assert page.count("data-thread-edit-form") == 1
        assert mine.edit_url in page and theirs.edit_url not in page
        assert f"'{mine.dom_id}-delete'" in page and f"{theirs.dom_id}-delete" not in page


def describe_an_unload_note():
    def it_can_be_edited_and_the_kiln_log_reads_the_same(crew, crew_client, maker, kiln_guild):
        firing, note = _unload_note(crew, maker)
        [before] = kiln_log()

        _edit(crew_client, note, "Zzcracked right at the foot ring")

        note.refresh_from_db()
        assert note.body == "Zzcracked right at the foot ring" and note.outcome == Outcome.BACK_TO_QUEUE
        [after] = kiln_log()
        assert (after.ticket_count, after.exceptions_label) == (before.ticket_count, before.exceptions_label)

    def it_leaves_the_thread_when_deleted_but_the_kiln_log_still_counts_the_piece(
        crew, crew_client, maker, maker_client, kiln_guild, django_capture_on_commit_callbacks
    ):
        firing, note = _unload_note(crew, maker)
        names = [note.image.name, note.tile.name]
        [before] = kiln_log()
        assert before.ticket_count == 1 and before.exceptions_label == "1 exception: cracked"

        with django_capture_on_commit_callbacks(execute=True):
            response = crew_client.post(_delete_url(note), **HX)

        assert response.status_code == 200 and response.content == b""
        note.refresh_from_db()
        assert note.removed_at is not None and (note.body, note.has_photo) == ("", False)
        assert note.outcome == Outcome.BACK_TO_QUEUE and note.firing_id == firing.pk
        assert not any(default_storage.exists(name) for name in names)
        [after] = kiln_log()
        assert (after.ticket_count, after.exceptions_label) == (1, "1 exception: cracked")
        _sheet, rows = firing_sheet(firing.pk)
        assert [row.returned for row in rows] == [True]
        for client in (maker_client, crew_client):
            page = client.get(_detail(note.ticket)).content.decode()
            assert "Zzcracked at the foot" not in page and "data-kiln-unload-note" not in page
            assert "data-kiln-returned" in page

    def it_is_a_404_once_deleted(crew, crew_client, maker, kiln_guild):
        _firing, note = _unload_note(crew, maker, outcome=Outcome.FIRED)
        crew_client.post(_delete_url(note), **HX)

        assert crew_client.post(_delete_url(note), **HX).status_code == 404
        assert _edit(crew_client, note, "Zzback again").status_code == 404

    def it_shows_no_empty_note_on_the_firing_page_once_deleted(crew, crew_client, maker, kiln_guild):
        firing, note = _unload_note(crew, maker, outcome=Outcome.FIRED)

        crew_client.post(_delete_url(note), **HX)

        page = crew_client.get(reverse("kiln:firing", args=[firing.pk])).content.decode()
        assert "pl-kiln-unload__note" not in page and "Fired, with a note: cracked" in page


def describe_the_bell_rows_and_the_races():
    def it_rewrites_the_makers_bell_row_by_its_period_and_nothing_else(crew, crew_client, maker, kiln_guild):
        reply = post_reply(_ticket(maker), crew, "Zzwipe the foot")
        row = Notification.objects.get(period=reply.notice_period)
        # The kiln bell row names the author before the words; only the words change.
        assert row.user == maker.user and row.body == f"{reply.author_name}: Zzwipe the foot"
        same_text = Notification.objects.create(
            user=maker.user, trigger="zz", title="Zz", body="Zzwipe the foot", period="zz-other-1"
        )

        _edit(crew_client, reply, "Zzwipe the foot clean")
        row.refresh_from_db()
        assert row.body == f"{reply.author_name}: Zzwipe the foot clean"

        crew_client.post(_delete_url(reply), **HX)
        row.refresh_from_db()
        same_text.refresh_from_db()
        assert row.body == f"{reply.author_name}: This message was deleted."
        assert same_text.body == "Zzwipe the foot"

    def it_is_a_404_when_the_message_goes_before_the_redraw(maker, maker_client, kiln_guild):
        reply = post_reply(_ticket(maker), maker, "Zzmine")

        def gone(self: KilnReply, **_: Any) -> bool:
            KilnReply.objects.filter(pk=self.pk).delete()
            return True

        with patch.object(KilnReply, "edit", gone):
            assert _edit(maker_client, reply, "Zzlate").status_code == 404

    def it_is_a_404_when_the_message_goes_between_the_gate_and_the_save(maker, maker_client, kiln_guild):
        reply = post_reply(_ticket(maker), maker, "Zzmine")
        real_edit = KilnReply.edit

        def gone(self: KilnReply, **kwargs: Any) -> bool:
            KilnReply.objects.filter(pk=self.pk).delete()
            return real_edit(self, **kwargs)

        with patch.object(KilnReply, "edit", gone):
            assert _edit(maker_client, reply, "Zzlate").status_code == 404

    def it_redraws_the_box_when_the_photo_went_in_another_tab(crew, crew_client, maker, kiln_guild):
        reply = _crew_photo_reply(crew, _ticket(maker))
        real_edit = KilnReply.edit

        def other_tab(self: KilnReply, **kwargs: Any) -> bool:
            KilnReply.objects.filter(pk=self.pk).update(image="", tile="")
            return real_edit(self, **kwargs)

        with patch.object(KilnReply, "edit", other_tab):
            response = _edit(crew_client, reply, "")

        assert response.status_code == 200 and "Write something, or keep a photo." in response.content.decode()

    def it_always_draws_the_empty_line_so_deleting_the_last_message_shows_it(maker, maker_client, kiln_guild):
        ticket = _ticket(maker)
        post_reply(ticket, maker, "Zzonly one")

        page = maker_client.get(_detail(ticket)).content.decode()

        assert "data-kiln-thread-empty" in page and "Zzonly one" in page

    def it_refuses_in_the_model_to_leave_no_text_and_no_photo(maker, kiln_guild):
        from core.threads import EmptyMessageError

        reply = post_reply(_ticket(maker), maker, "Zzwords")

        with pytest.raises(EmptyMessageError):
            reply.edit(body="")

    def it_keeps_the_rest_of_a_bell_row_whose_words_were_clipped(crew, crew_client, maker, kiln_guild):
        reply = post_reply(_ticket(maker), crew, "Zzshort")
        Notification.objects.filter(period=reply.notice_period).update(body="Zzclipped…")

        _edit(crew_client, reply, "Zzshort, longer")

        assert Notification.objects.get(period=reply.notice_period).body == "Zzshort, longer"
