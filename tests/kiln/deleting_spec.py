"""Deleting a kiln ticket: the maker their own, the crew and site admins any, with a note to the maker (#1025)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.core import mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.events.registry import KILN_CREW_REPLIED, KILN_TICKET_DELETED, Channel, Recipients, get_event
from core.events.settings_matrix import build_matrix
from core.models import Notification, NotificationPreference, PushSubscription
from kiln.access import kiln_home_url
from kiln.forms import ExceptionNote
from kiln.models import ClayOption, KilnFiring, KilnFlag, KilnReply, KilnTicket, KilnTicketPhoto
from kiln.services import (
    DeletedTicket,
    LoadResult,
    TicketDeleted,
    delete_ticket,
    kiln_log,
    load_kiln,
    unload_kiln,
)
from membership.models import Member
from tests.kiln.conftest import photo_upload, signed_in
from tests.kiln.factories import ClayOptionFactory, KilnTicketFactory

pytestmark = pytest.mark.django_db

Status = KilnTicket.Status


@pytest.fixture
def pushed() -> Iterator[MagicMock]:
    with patch("core.events.channels.send_web_push") as send_web_push:
        yield send_web_push


def _waiting(maker: Member, firing_type: str = "glaze") -> KilnTicket:
    """A ticket in the queue with a real photo (files on storage), a flag and a message."""
    clay = ClayOption.objects.filter(name="Standard 266").first() or ClayOptionFactory(name="Standard 266")
    ticket = KilnTicketFactory(
        maker=maker,
        status=Status.SUBMITTED,
        firing_type=firing_type,
        clay=clay,
        quantity=3,
        submitted_at=timezone.now(),
    )
    ticket.add_photo(photo_upload())
    KilnFlag.objects.create(ticket=ticket, kind=KilnFlag.Kind.OTHER_CLAY, answer="Standard 266")
    KilnReply.objects.create(ticket=ticket, author=maker, body="Is it on the cart?")
    return ticket


def _at(status: str, maker: Member, crew: Member) -> KilnTicket:
    """A ticket of ``maker``'s at ``status``, reached the way the crew would reach it."""
    ticket = _waiting(maker)
    if status == Status.DRAFT:
        KilnTicket.objects.filter(pk=ticket.pk).update(status=Status.DRAFT, submitted_at=None)
    if status in (Status.LOADED, Status.FIRED):
        firing = load_kiln(firing_type="glaze", ticket_pks=[ticket.pk], by=crew).firing
        assert firing is not None
        if status == Status.FIRED:
            unload_kiln(firing_pk=firing.pk, exceptions={}, by=crew)
    ticket.refresh_from_db()
    assert ticket.status == status
    return ticket


def _delete_url(ticket: KilnTicket) -> str:
    return reverse("kiln:delete", args=[ticket.pk])


def _gone(ticket: KilnTicket) -> bool:
    return not KilnTicket.objects.filter(pk=ticket.pk).exists()


def _bells(member: Member) -> list[Notification]:
    return list(Notification.objects.filter(user=member.user, trigger=KILN_TICKET_DELETED))


def _toasts(response: Any) -> list[str]:
    return [str(m) for m in response.context["messages"]]


def describe_a_maker_deleting_their_own_ticket():
    @pytest.mark.parametrize("status", [Status.DRAFT, Status.SUBMITTED, Status.LOADED, Status.FIRED])
    def it_deletes_it_at_any_status_with_its_photos_flags_and_replies(
        status, maker, maker_client, crew, django_capture_on_commit_callbacks
    ):
        ticket = _at(status, maker, crew)
        photo = ticket.photos.get()
        storage, names = photo.image.storage, [photo.image.name, photo.tile.name]
        firing_pk = ticket.firing_id

        with django_capture_on_commit_callbacks(execute=True):
            response = maker_client.post(_delete_url(ticket), follow=True)

        assert _gone(ticket)
        assert not KilnTicketPhoto.objects.filter(ticket_id=ticket.pk).exists()
        assert not KilnFlag.objects.filter(ticket_id=ticket.pk).exists()
        assert not KilnReply.objects.filter(ticket_id=ticket.pk).exists()
        assert not any(storage.exists(name) for name in names)
        assert f"Ticket {ticket.pk} is deleted." in _toasts(response)
        # The maker lands on My Tickets: the Ceramics Guild's Kiln Tickets tab.
        assert response.redirect_chain[0][0] == kiln_home_url()
        if firing_pk is not None:
            assert KilnFiring.objects.filter(pk=firing_pk).exists()

    def it_tells_nobody(maker, maker_client, crew, kiln_guild):
        ticket = _waiting(maker)

        maker_client.post(_delete_url(ticket))

        assert not Notification.objects.exists()
        assert mail.outbox == []

    def it_cannot_delete_another_makers_ticket(maker_client, make_member):
        ticket = _waiting(make_member())

        response = maker_client.post(_delete_url(ticket), {"note": "mine now"})

        assert response.status_code == 404
        assert not _gone(ticket)

    def it_deletes_nothing_on_a_get(maker, maker_client):
        ticket = _waiting(maker)

        assert maker_client.get(_delete_url(ticket)).status_code == 405
        assert not _gone(ticket)

    def it_says_so_when_the_ticket_was_already_deleted(maker, maker_client):
        ticket = _waiting(maker)

        with patch("kiln.views.delete_ticket", side_effect=TicketDeleted(ticket.pk)):
            response = maker_client.post(_delete_url(ticket), follow=True)

        assert f"Ticket {ticket.pk} was already deleted." in _toasts(response)

    def it_shows_the_button_and_the_confirm_on_their_page(maker, maker_client):
        ticket = _waiting(maker)

        body = maker_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()

        assert "data-kiln-delete" in body
        assert f'action="{_delete_url(ticket)}"' in body
        assert "Its photos and messages go too. This cannot be undone." in body
        assert "Why is it being deleted?" not in body


def describe_the_crew_deleting_a_ticket():
    def it_deletes_any_ticket_with_a_note_and_goes_back_to_the_queue(crew_client, maker):
        ticket = _waiting(maker)

        response = crew_client.post(_delete_url(ticket), {"note": "It cracked on the cart."}, follow=True)

        assert _gone(ticket)
        assert response.redirect_chain[0][0] == reverse("kiln:load")
        assert f"Ticket {ticket.pk} is deleted. {maker.display_name} got your note." in _toasts(response)

    def it_refuses_without_a_note(crew_client, maker):
        ticket = _waiting(maker)

        response = crew_client.post(_delete_url(ticket), {"note": "   "}, follow=True)

        assert not _gone(ticket)
        assert "Say why the ticket is being deleted." in _toasts(response)
        assert response.redirect_chain[0][0] == reverse("kiln:detail", args=[ticket.pk])

    def it_refuses_a_note_over_the_limit(crew_client, maker):
        ticket = _waiting(maker)

        response = crew_client.post(_delete_url(ticket), {"note": "x" * 501}, follow=True)

        assert not _gone(ticket)
        assert "Keep the note under 500 characters." in _toasts(response)

    def it_needs_no_note_on_their_own_ticket_and_tells_nobody(crew, crew_client):
        ticket = _waiting(crew)

        response = crew_client.post(_delete_url(ticket), follow=True)

        assert _gone(ticket)
        assert f"Ticket {ticket.pk} is deleted." in _toasts(response)
        assert not Notification.objects.exists() and mail.outbox == []

    def it_lets_a_site_admin_delete_any_ticket_with_a_note(make_member, maker):
        admin = make_member(fog_role=Member.FogRole.ADMIN)
        ticket = _waiting(maker)

        signed_in(admin).post(_delete_url(ticket), {"note": "Duplicate of another ticket."})

        assert _gone(ticket)
        assert len(_bells(maker)) == 1

    def it_shows_the_note_field_in_the_confirm(crew_client, maker):
        ticket = _waiting(maker)

        body = crew_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()

        assert "data-kiln-delete" in body
        assert "Why is it being deleted?" in body
        assert f"{maker.display_name} gets this note with a description of the piece." in body
        assert 'name="note"' in body

    def it_shows_no_note_field_on_their_own_ticket(crew, crew_client):
        ticket = _waiting(crew)

        body = crew_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()

        assert "data-kiln-delete" in body and "Why is it being deleted?" not in body


def describe_the_makers_notice():
    def it_rings_and_emails_once_with_the_note_and_the_piece(crew, maker, kiln_guild):
        ticket = _waiting(maker)

        result = delete_ticket(ticket_pk=ticket.pk, by=crew, note="One bowl cracked on the cart.")

        assert result == DeletedTicket(pk=ticket.pk, maker_name=maker.display_name, notified=True)
        [bell] = _bells(maker)
        assert bell.title == f"The kiln crew deleted Ticket {ticket.pk}"
        assert bell.body == f"Glaze · 3 pieces · Standard 266. {crew.display_name}: One bowl cracked on the cart."
        assert bell.url == reverse("kiln:mine")
        [email] = mail.outbox
        assert email.to == [maker.user.email]
        assert email.subject == f"The kiln crew deleted your Ticket {ticket.pk}"
        assert "Glaze · 3 pieces · Standard 266" in email.body
        assert "One bowl cracked on the cart." in email.body
        assert f"{settings.MEMBER_BASE_URL}{reverse('kiln:mine')}" in email.body

    def it_reaches_a_class_guest(crew, make_member, kiln_guild):
        guest = make_member(status=Member.Status.GUEST)
        ticket = _waiting(guest)

        delete_ticket(ticket_pk=ticket.pk, by=crew, note="It broke.")

        assert len(_bells(guest)) == 1
        assert [m.to for m in mail.outbox] == [[guest.user.email]]

    def it_follows_the_makers_email_switch_and_still_rings_the_bell(crew, maker, kiln_guild):
        NotificationPreference.objects.create(
            user=maker.user, event_key=KILN_TICKET_DELETED, channel=Channel.EMAIL.value, enabled=False
        )

        delete_ticket(ticket_pk=_waiting(maker).pk, by=crew, note="It broke.")

        assert len(_bells(maker)) == 1
        assert mail.outbox == []

    def it_pushes_by_default(crew, maker, kiln_guild, pushed):
        PushSubscription.objects.create(user=maker.user, endpoint="https://push/maker", p256dh="k", auth="a")

        delete_ticket(ticket_pk=_waiting(maker).pk, by=crew, note="It broke.")

        pushed.assert_called_once()

    def it_describes_a_draft_with_no_answers_by_its_count(crew, maker):
        ticket = KilnTicketFactory(maker=maker)

        delete_ticket(ticket_pk=ticket.pk, by=crew, note="Empty draft.")

        assert _bells(maker)[0].body == f"1 piece. {crew.display_name}: Empty draft."

    def it_reaches_nobody_for_a_maker_with_no_login(crew):
        from tests.membership.factories import MemberFactory

        ticket = KilnTicketFactory(maker=MemberFactory(user=None))

        result = delete_ticket(ticket_pk=ticket.pk, by=crew, note="Gone.")

        assert result.notified is False and result.message == f"Ticket {ticket.pk} is deleted."
        assert not Notification.objects.exists()

    def it_is_a_single_user_kiln_event_with_push_on():
        event = get_event(KILN_TICKET_DELETED)

        assert (event.recipient, event.category) == (Recipients.SINGLE_USER, "Kiln tickets")
        assert {spec.channel for spec in event.channels} == {Channel.IN_APP, Channel.EMAIL, Channel.PUSH}
        assert all(spec.default.value == "on" for spec in event.channels)


def describe_the_settings_row():
    def _rows(member: Member) -> set[tuple[str, str]]:
        return {
            (section.title, row.label)
            for section in build_matrix(member.user)
            for block in section.blocks
            for row in block.rows
        }

    @pytest.mark.parametrize("who", ["member", "guest", "crew", "admin"])
    def it_shows_exactly_where_the_crew_message_row_shows(who, make_member, kiln_guild, crew):
        member = {
            "member": lambda: make_member(),
            "guest": lambda: make_member(status=Member.Status.GUEST),
            "crew": lambda: crew,
            "admin": lambda: make_member(fog_role=Member.FogRole.ADMIN),
        }[who]()
        rows = _rows(member)
        crew_row = get_event(KILN_CREW_REPLIED).label
        deleted_row = get_event(KILN_TICKET_DELETED).label

        sections = {section for section, label in rows if label == crew_row}
        assert sections == {section for section, label in rows if label == deleted_row}
        if who in ("member", "guest"):
            assert sections, "a maker sees the kiln maker rows"


def describe_files_and_the_firing():
    def it_deletes_the_files_only_once_the_delete_commits(maker, django_capture_on_commit_callbacks):
        ticket = _waiting(maker)
        photo = ticket.photos.get()
        storage, names = photo.image.storage, [photo.image.name, photo.tile.name]

        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            ticket.delete_with_photos()
        assert all(storage.exists(name) for name in names)
        assert len(callbacks) == 1

        callbacks[0]()
        assert not any(storage.exists(name) for name in names)

    def it_keeps_the_files_and_the_rows_when_the_delete_rolls_back(maker, django_capture_on_commit_callbacks):
        ticket = _waiting(maker)
        pk = ticket.pk
        photo = ticket.photos.get()
        storage, name = photo.image.storage, photo.image.name

        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    ticket.delete_with_photos()
                    raise RuntimeError("the delete failed")
            except RuntimeError:
                pass

        assert callbacks == []
        assert storage.exists(name)
        assert KilnTicket.objects.filter(pk=pk).exists()
        assert KilnTicketPhoto.objects.filter(pk=photo.pk).exists()

    def it_schedules_nothing_for_a_ticket_with_no_photos(maker, django_capture_on_commit_callbacks):
        ticket = KilnTicketFactory(maker=maker)

        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            ticket.delete_with_photos()

        assert callbacks == [] and _gone(ticket)

    def it_locks_the_ticket_row_before_deleting(maker):
        ticket = _waiting(maker)

        with patch("kiln.models.KilnTicketQuerySet.select_for_update", autospec=True) as lock:
            lock.side_effect = lambda qs, *a, **k: qs
            delete_ticket(ticket_pk=ticket.pk, by=maker)

        lock.assert_called_once()

    def it_refuses_a_ticket_someone_already_deleted(maker):
        ticket = _waiting(maker)
        delete_ticket(ticket_pk=ticket.pk, by=maker)

        with pytest.raises(TicketDeleted):
            delete_ticket(ticket_pk=ticket.pk, by=maker)

    def it_keeps_a_firing_whose_back_to_queue_note_went_with_the_ticket(crew, maker):
        ticket = _waiting(maker)
        firing = load_kiln(firing_type="glaze", ticket_pks=[ticket.pk], by=crew).firing
        assert firing is not None
        note = ExceptionNote(ticket_pk=ticket.pk, what_happened="cracked", note="Cracked.", outcome="back_to_queue")
        unload_kiln(firing_pk=firing.pk, exceptions={ticket.pk: note}, by=crew)

        delete_ticket(ticket_pk=ticket.pk, by=maker)

        assert KilnFiring.objects.filter(pk=firing.pk).exists()
        assert not KilnReply.objects.filter(firing=firing).exists()


def describe_a_firing_whose_tickets_were_all_deleted():
    @pytest.fixture
    def emptied(crew, maker) -> KilnFiring:
        tickets = [_waiting(maker), _waiting(maker)]
        firing = load_kiln(firing_type="glaze", ticket_pks=[t.pk for t in tickets], by=crew).firing
        assert firing is not None
        for ticket in tickets:
            delete_ticket(ticket_pk=ticket.pk, by=maker)
        return firing

    def it_still_unloads_and_marks_it_unloaded(emptied, crew_client):
        page = crew_client.get(reverse("kiln:unload", args=[emptied.pk])).content.decode()
        assert "Every ticket in this firing was deleted." in page

        response = crew_client.post(reverse("kiln:unload", args=[emptied.pk]), follow=True)

        assert response.redirect_chain[-1][0] == reverse("kiln:log")
        assert f"{emptied.name} is unloaded: 0 tickets ready for pickup. 0 makers notified." in _toasts(response)
        assert KilnFiring.objects.get(pk=emptied.pk).is_unloaded
        assert not Notification.objects.exists()

    def it_shows_0_pieces_on_the_kiln_log_and_its_page(emptied, crew_client):
        [row] = kiln_log()
        assert (row.pk, row.ticket_count) == (emptied.pk, 0)

        log = crew_client.get(reverse("kiln:log"))
        firing_page = crew_client.get(reverse("kiln:firing", args=[emptied.pk]))
        unload_list = crew_client.get(reverse("kiln:unload_list"))

        assert log.status_code == firing_page.status_code == unload_list.status_code == 200
        assert '<td class="pl-kiln-num">0</td>' in log.content.decode()
        assert "· 0 tickets" in firing_page.content.decode()
        assert "0 tickets" in unload_list.content.decode()


def describe_a_ticket_deleted_while_the_crew_had_a_page_open():
    def it_is_skipped_on_load_the_kiln_and_counted_in_the_toast(crew_client, maker):
        staying, going = _waiting(maker), _waiting(maker)
        delete_ticket(ticket_pk=going.pk, by=maker)

        response = crew_client.post(
            reverse("kiln:load"), {"firing_type": "glaze", "tickets": [staying.pk, going.pk]}, follow=True
        )

        assert KilnTicket.objects.get(pk=staying.pk).status == Status.LOADED
        assert (
            "Glaze firing 1 is loaded with 1 ticket. 1 ticket was deleted while this page was open, "
            "so it was left out." in _toasts(response)
        )

    def it_loads_nothing_when_every_ticked_ticket_was_deleted(crew_client, maker):
        going = _waiting(maker)
        delete_ticket(ticket_pk=going.pk, by=maker)

        response = crew_client.post(reverse("kiln:load"), {"firing_type": "glaze", "tickets": [going.pk]}, follow=True)

        assert response.status_code == 200
        assert "Nothing was loaded. 1 ticket was deleted while this page was open, so it was left out." in _toasts(
            response
        )
        assert not KilnFiring.objects.exists()

    def it_counts_deleted_and_left_out_apart(crew, maker):
        bisque, going = _waiting(maker, "bisque"), _waiting(maker)
        delete_ticket(ticket_pk=going.pk, by=maker)

        result = load_kiln(firing_type="glaze", ticket_pks=[bisque.pk, going.pk], by=crew)

        assert (result.loaded, result.skipped, result.deleted) == (0, 1, 1)

    def it_words_several_deleted_tickets(crew):
        assert LoadResult(None, 0, 0, 2).message == (
            "Nothing was loaded. 2 tickets were deleted while this page was open, so they were left out."
        )

    @pytest.mark.parametrize("ticked", [True, False])
    def it_is_skipped_on_unload_and_counted_in_the_toast(ticked, crew, crew_client, maker, kiln_guild):
        staying, going = _waiting(maker), _waiting(maker)
        firing = load_kiln(firing_type="glaze", ticket_pks=[staying.pk, going.pk], by=crew).firing
        assert firing is not None
        delete_ticket(ticket_pk=going.pk, by=maker)
        # The page was opened before the delete, so it posts both rows; the deleted one ticked or
        # unticked with its exception answers.
        data: dict[str, object] = {"shown": [staying.pk, going.pk], "fired": [staying.pk]}
        if ticked:
            data["fired"] = [staying.pk, going.pk]
        else:
            data.update({f"what-{going.pk}": "cracked", f"note-{going.pk}": "Cracked.", f"next-{going.pk}": "fired"})

        response = crew_client.post(reverse("kiln:unload", args=[firing.pk]), data, follow=True)

        assert response.redirect_chain[-1][0] == reverse("kiln:log")
        assert KilnTicket.objects.get(pk=staying.pk).status == Status.FIRED
        assert (
            f"{firing.name} is unloaded: 1 ticket ready for pickup. 1 maker notified. "
            "1 ticket was deleted while this page was open, so it was left out." in _toasts(response)
        )
        assert not KilnReply.objects.filter(firing=firing).exists()

    def it_skips_a_ticket_deleted_after_the_form_was_checked(crew, maker):
        staying, going = _waiting(maker), _waiting(maker)
        firing = load_kiln(firing_type="glaze", ticket_pks=[staying.pk, going.pk], by=crew).firing
        assert firing is not None
        note = ExceptionNote(ticket_pk=going.pk, what_happened="cracked", note="Cracked.", outcome="back_to_queue")
        delete_ticket(ticket_pk=going.pk, by=maker)

        result = unload_kiln(firing_pk=firing.pk, exceptions={going.pk: note}, by=crew, shown=[staying.pk, going.pk])

        assert (result.fired, result.returned, result.deleted) == (1, 0, 1)
        assert not KilnReply.objects.filter(firing=firing).exists()


def describe_a_maker_saving_a_ticket_deleted_meanwhile():
    def it_saves_nothing_and_says_it_was_deleted(maker, maker_client, crew):
        ticket = KilnTicketFactory(maker=maker)
        edit = reverse("kiln:edit", args=[ticket.pk])
        data = {"action": "draft", "firing_type": "glaze", "quantity": "2"}

        with patch("kiln.views.save_ticket", side_effect=TicketDeleted(ticket.pk)):
            response = maker_client.post(edit, data, follow=True)

        assert f"Ticket {ticket.pk} was deleted, so nothing was saved." in _toasts(response)

    def it_never_writes_a_deleted_ticket_back(maker, crew):
        from kiln.forms import KilnTicketForm
        from kiln.services import save_ticket

        ticket = KilnTicketFactory(maker=maker)
        form = KilnTicketForm(
            {"firing_type": "glaze", "quantity": "2"},
            instance=ticket,
            action="draft",
            maker_photo_pks=[],
        )
        assert form.is_valid(), form.errors
        delete_ticket(ticket_pk=ticket.pk, by=crew, note="It broke.")

        with pytest.raises(TicketDeleted):
            save_ticket(form, maker)

        assert _gone(ticket)
