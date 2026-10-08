"""Unloading a firing, the ready for pickup notices, and the Unload pages (#691 part 3)."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from core.events.registry import KILN_CREW_REPLIED, KILN_READY_FOR_PICKUP
from core.models import Notification, PushSubscription
from kiln.forms import ExceptionNote
from kiln.models import KilnFiring, KilnReply, KilnTicket
from kiln.services import FiringAlreadyUnloaded, PickupNotice, load_kiln, unload_kiln
from membership.models import Member
from tests.kiln.factories import GlazeOptionFactory, KilnTicketFactory, KilnTicketPhotoFactory

pytestmark = pytest.mark.django_db

Status = KilnTicket.Status
Outcome = KilnReply.Outcome
LIST = reverse("kiln:unload_list")


def _waiting(maker: Member, firing_type: str = "glaze") -> KilnTicket:
    ticket = KilnTicketFactory(
        maker=maker, status=Status.SUBMITTED, firing_type=firing_type, submitted_at=timezone.now()
    )
    KilnTicketPhotoFactory(ticket=ticket, is_cover=True)
    return ticket


def _loaded(by: Member, *tickets: KilnTicket) -> KilnFiring:
    result = load_kiln(firing_type=tickets[0].firing_type, ticket_pks=[t.pk for t in tickets], by=by)
    assert result.firing is not None
    return result.firing


def _note(
    ticket: KilnTicket, outcome: str = Outcome.FIRED, what: str = "cracked", note: str = "Crack near the base."
) -> ExceptionNote:
    return ExceptionNote(ticket_pk=ticket.pk, what_happened=what, note=note, outcome=outcome)


def _fresh(ticket: KilnTicket) -> KilnTicket:
    return KilnTicket.objects.get(pk=ticket.pk)


def _bells(member: Member, key: str = KILN_READY_FOR_PICKUP) -> list[Notification]:
    return list(Notification.objects.filter(user=member.user, trigger=key))


@pytest.fixture
def pushed() -> Iterator[MagicMock]:
    with patch("core.events.channels.send_web_push") as send_web_push:
        yield send_web_push


def describe_unload_kiln():
    def it_fires_every_ticket_and_records_who_unloaded_the_firing(crew, maker, make_member, kiln_guild):
        one, two = _waiting(maker), _waiting(make_member())
        firing = _loaded(crew, one, two)

        result = unload_kiln(firing_pk=firing.pk, exceptions={}, by=crew)

        firing.refresh_from_db()
        assert (firing.unloaded_by, firing.is_unloaded) == (crew, True)
        for ticket in (one, two):
            fresh = _fresh(ticket)
            assert (fresh.status, fresh.firing, fresh.fired_at) == (Status.FIRED, firing, firing.unloaded_at)
        assert (result.fired, result.returned, result.notified) == (2, 0, 2)
        assert result.message == f"{firing.name} is unloaded: 2 tickets ready for pickup. 2 makers notified."
        assert not KilnReply.objects.exists()

    def it_counts_only_makers_a_notice_can_reach(crew, maker, make_member, kiln_guild):
        userless = make_member()
        one, two = _waiting(maker), _waiting(userless)
        firing = _loaded(crew, one, two)
        type(userless).objects.filter(pk=userless.pk).update(user=None)

        result = unload_kiln(firing_pk=firing.pk, exceptions={}, by=crew)

        assert (result.fired, result.notified) == (2, 1)
        assert result.message.endswith("1 maker notified.")

    def it_fires_an_exception_with_its_note_on_the_thread(crew, maker, kiln_guild):
        ticket = _waiting(maker)
        firing = _loaded(crew, ticket)

        result = unload_kiln(firing_pk=firing.pk, exceptions={ticket.pk: _note(ticket, what="glaze_ran")}, by=crew)

        fresh = _fresh(ticket)
        assert (fresh.status, fresh.firing) == (Status.FIRED, firing)
        reply = fresh.replies.get()
        assert (reply.author, reply.body, reply.firing, reply.what_happened, reply.outcome) == (
            crew,
            "Crack near the base.",
            firing,
            "glaze_ran",
            Outcome.FIRED,
        )
        assert reply.from_crew and reply.is_unload_note
        assert result.message == f"{firing.name} is unloaded: 1 ticket ready for pickup. 1 maker notified."

    def it_sends_an_exception_back_to_the_queue_where_the_maker_can_edit_it(crew, maker, make_member, kiln_guild):
        back, out = _waiting(maker), _waiting(make_member())
        submitted_at = back.submitted_at
        firing = _loaded(crew, back, out)

        result = unload_kiln(firing_pk=firing.pk, exceptions={back.pk: _note(back, Outcome.BACK_TO_QUEUE)}, by=crew)

        fresh = _fresh(back)
        assert (fresh.status, fresh.firing, fresh.fired_at) == (Status.SUBMITTED, None, None)
        assert fresh.is_editable and fresh.submitted_at == submitted_at
        assert fresh.replies.get().outcome == Outcome.BACK_TO_QUEUE
        assert _fresh(out).status == Status.FIRED
        assert (result.fired, result.returned) == (1, 1)
        assert "1 ticket ready for pickup, 1 back in the queue" in result.message
        assert back in KilnTicket.objects.waiting()

    def it_refuses_a_second_unload_and_sends_nothing_twice(crew, maker, make_member, kiln_guild):
        other = make_member(preferred_name="Sam Whitlock")
        ticket = _waiting(maker)
        firing = _loaded(crew, ticket)
        unload_kiln(firing_pk=firing.pk, exceptions={}, by=other)

        with pytest.raises(FiringAlreadyUnloaded) as refused:
            unload_kiln(firing_pk=firing.pk, exceptions={ticket.pk: _note(ticket, Outcome.BACK_TO_QUEUE)}, by=crew)

        firing.refresh_from_db()
        assert firing.unloaded_by == other
        assert _fresh(ticket).status == Status.FIRED
        assert not KilnReply.objects.exists()
        assert len(_bells(maker)) == 1
        assert refused.value.message.startswith(f"{firing.name} was already unloaded by Sam Whitlock at ")
        assert refused.value.message.endswith("Nothing changed and no one was notified twice.")

    def it_ignores_an_exception_for_a_ticket_outside_the_firing(crew, maker, kiln_guild):
        inside, outside = _waiting(maker), _waiting(maker)
        firing = _loaded(crew, inside)

        unload_kiln(firing_pk=firing.pk, exceptions={outside.pk: _note(outside)}, by=crew)

        assert _fresh(outside).status == Status.SUBMITTED
        assert not KilnReply.objects.exists()


def describe_the_ready_for_pickup_notice():
    def it_sends_one_notice_per_maker_listing_every_piece(crew, maker, make_member, kiln_guild, pushed):
        other = make_member()
        mine = [_waiting(maker), _waiting(maker), _waiting(maker)]
        theirs = _waiting(other)
        firing = _loaded(crew, *mine, theirs)
        PushSubscription.objects.create(user=maker.user, endpoint="https://push/maker", p256dh="k", auth="a")

        unload_kiln(firing_pk=firing.pk, exceptions={}, by=crew)

        bells = _bells(maker)
        assert len(bells) == 1 and len(_bells(other)) == 1
        numbers = [str(t.pk) for t in mine]
        assert bells[0].title == "Your pieces are ready for pickup"
        assert bells[0].body == (
            f"Tickets {numbers[0]}, {numbers[1]} and {numbers[2]} came out of {firing.name}. They are on the pickup shelf."
        )
        assert bells[0].url.endswith(reverse("kiln:mine"))
        to_maker = [m for m in mail.outbox if m.to == [maker.user.email]]
        assert len(to_maker) == 1 and len(mail.outbox) == 2
        for ticket in mine:
            assert f"Ticket {ticket.pk} ({ticket.summary}): Ready for pickup" in to_maker[0].body
        assert f"by {crew.display_name}." in to_maker[0].body
        pushed.assert_called_once()

    def it_links_a_single_piece_to_its_ticket(crew, maker, kiln_guild):
        ticket = _waiting(maker)
        firing = _loaded(crew, ticket)

        unload_kiln(firing_pk=firing.pk, exceptions={}, by=crew)

        bell = _bells(maker)[0]
        assert bell.title == "Your piece is ready for pickup"
        assert bell.body == f"Ticket {ticket.pk} came out of {firing.name}. It is on the pickup shelf."
        assert bell.url.endswith(reverse("kiln:detail", args=[ticket.pk]))
        assert f"{settings.MEMBER_BASE_URL}{reverse('kiln:detail', args=[ticket.pk])}" in mail.outbox[0].body

    def it_tells_a_maker_whose_only_piece_went_back_with_the_note(crew, maker, kiln_guild):
        ticket = _waiting(maker)
        firing = _loaded(crew, ticket)
        note = _note(ticket, Outcome.BACK_TO_QUEUE, "stuck", "The shelf was full, so it goes in the next one.")

        unload_kiln(firing_pk=firing.pk, exceptions={ticket.pk: note}, by=crew)

        bell = _bells(maker)[0]
        assert bell.title == "Your piece is back in the queue"
        assert (
            bell.body
            == f"Ticket {ticket.pk} was not fired and was put back in the queue. The kiln crew left you a note."
        )
        assert mail.outbox[0].subject == "Your piece is back in the queue"
        assert (
            f"Stuck to the shelf. {crew.display_name}: The shelf was full, so it goes in the next one."
            in mail.outbox[0].body
        )
        html = mail.outbox[0].alternatives[0][0]
        assert "The shelf was full, so it goes in the next one." in html and "Back in the queue" in html

    def it_says_both_in_one_notice_when_some_pieces_went_back(crew, maker, kiln_guild):
        out, back = _waiting(maker), _waiting(maker)
        firing = _loaded(crew, out, back)

        unload_kiln(firing_pk=firing.pk, exceptions={back.pk: _note(back, Outcome.BACK_TO_QUEUE)}, by=crew)

        bells = _bells(maker)
        assert len(bells) == 1 and len(mail.outbox) == 1
        assert bells[0].title == "Some of your pieces are ready for pickup"
        assert f"Ticket {out.pk} came out of" in bells[0].body
        assert f"Ticket {back.pk} was not fired" in bells[0].body

    def it_never_also_sends_the_note_as_a_crew_message(crew, maker, kiln_guild):
        ticket = _waiting(maker)
        firing = _loaded(crew, ticket)

        unload_kiln(firing_pk=firing.pk, exceptions={ticket.pk: _note(ticket)}, by=crew)

        assert _bells(maker, KILN_CREW_REPLIED) == []
        assert len(_bells(maker)) == 1 and len(mail.outbox) == 1
        assert "The kiln crew left you a note." in _bells(maker)[0].body

    def it_reaches_a_class_guest(crew, make_member, kiln_guild):
        guest = make_member(status=Member.Status.GUEST)
        firing = _loaded(crew, _waiting(guest))

        unload_kiln(firing_pk=firing.pk, exceptions={}, by=crew)

        assert len(_bells(guest)) == 1
        assert [m.to for m in mail.outbox] == [[guest.user.email]]

    def it_does_not_tell_the_crew_member_about_their_own_pieces(crew, maker, kiln_guild):
        firing = _loaded(crew, _waiting(crew), _waiting(maker))

        result = unload_kiln(firing_pk=firing.pk, exceptions={}, by=crew)

        assert _bells(crew) == [] and len(_bells(maker)) == 1
        assert result.notified == 1

    def it_says_several_pieces_went_back_in_the_plural(crew, maker, kiln_guild):
        one, two = _waiting(maker), _waiting(maker)
        firing = _loaded(crew, one, two)

        unload_kiln(
            firing_pk=firing.pk,
            exceptions={t.pk: _note(t, Outcome.BACK_TO_QUEUE) for t in (one, two)},
            by=crew,
        )

        bell = _bells(maker)[0]
        assert bell.title == "Your pieces are back in the queue"
        assert f"Tickets {one.pk} and {two.pk} were not fired and were put back in the queue." in bell.body

    def it_escapes_the_crews_note_in_the_email(crew, maker, kiln_guild):
        ticket = _waiting(maker)
        firing = _loaded(crew, ticket)
        note = _note(ticket, note="<b>hot</b> & cracked")

        unload_kiln(firing_pk=firing.pk, exceptions={ticket.pk: note}, by=crew)

        notice = PickupNotice(
            firing=KilnFiring.objects.get(pk=firing.pk),
            unloaded_by=crew,
            fired=[_fresh(ticket)],
            returned=[],
            notes={ticket.pk: KilnReply.objects.get()},
        )
        assert "&lt;b&gt;hot&lt;/b&gt; &amp; cracked" in notice.pieces_block
        assert "<b>hot</b>" not in mail.outbox[0].alternatives[0][0]


def describe_the_unload_list():
    def it_lists_the_firings_in_the_kiln_with_the_count_on_the_tab(crew, crew_client, maker, kiln_guild):
        first = _loaded(crew, _waiting(maker), _waiting(maker))
        done = _loaded(crew, _waiting(maker))
        unload_kiln(firing_pk=done.pk, exceptions={}, by=crew)

        response = crew_client.get(LIST)

        assert [f.pk for f in response.context["firings"]] == [first.pk]
        body = response.content.decode()
        assert f'href="{reverse("kiln:unload", args=[first.pk])}"' in body
        assert "2 tickets" in body
        assert response.context["unload_count"] == 1
        assert 'data-kiln-tab="unload"' in body and 'data-kiln-tab="log"' in body

    def it_says_when_nothing_is_in_the_kiln(crew_client):
        assert b"data-kiln-empty" in crew_client.get(LIST).content

    def it_is_for_the_crew_only(maker_client):
        assert maker_client.get(LIST).status_code == 403


def describe_the_unload_page():
    def it_shows_every_ticket_ticked(crew, crew_client, maker, make_member, kiln_guild):
        glaze = GlazeOptionFactory(name="Spec Plum Wine")
        one = _waiting(maker)
        one.glaze_studio = True
        one.save(update_fields=["glaze_studio"])
        one.studio_glazes.add(glaze)
        student = make_member(status=Member.Status.GUEST)
        two = _waiting(student)
        KilnTicket.objects.filter(pk=two.pk).update(maker_type=KilnTicket.MakerType.STUDENT)
        firing = _loaded(crew, one, two)

        response = crew_client.get(reverse("kiln:unload", args=[firing.pk]))

        body = response.content.decode()
        assert [row.ticked for row in response.context["form"].rows] == [True, True]
        assert body.count('name="fired"') == 2 and body.count("checked") >= 2
        assert "Spec Plum Wine" in body and "Student or guest" in body
        assert f"Loaded {timezone.localtime(firing.loaded_at):%a, %b} " in body

    def it_marks_fired_and_notifies(crew, crew_client, maker, kiln_guild):
        ticket = _waiting(maker)
        firing = _loaded(crew, ticket)

        response = crew_client.post(reverse("kiln:unload", args=[firing.pk]), {"fired": [ticket.pk]}, follow=True)

        assert response.redirect_chain[-1][0] == reverse("kiln:log")
        assert f"{firing.name} is unloaded" in [str(m) for m in response.context["messages"]][0]
        assert _fresh(ticket).status == Status.FIRED

    def it_sends_an_unticked_ticket_back_with_its_note(crew, crew_client, maker, make_member, kiln_guild):
        out, back = _waiting(make_member()), _waiting(maker)
        firing = _loaded(crew, out, back)

        crew_client.post(
            reverse("kiln:unload", args=[firing.pk]),
            {
                "fired": [out.pk],
                f"what-{back.pk}": "cracked",
                f"note-{back.pk}": "  It cracked at the foot.  ",
                f"next-{back.pk}": "back_to_queue",
                f"what-{out.pk}": "stuck",  # a ticked ticket's exception fields are ignored
            },
        )

        assert _fresh(back).status == Status.SUBMITTED
        reply = KilnReply.objects.get()
        assert (reply.ticket_id, reply.body, reply.outcome) == (
            back.pk,
            "It cracked at the foot.",
            Outcome.BACK_TO_QUEUE,
        )

    def it_keeps_the_page_and_asks_for_what_an_exception_needs(crew, crew_client, maker, kiln_guild):
        ticket = _waiting(maker)
        firing = _loaded(crew, ticket)

        response = crew_client.post(reverse("kiln:unload", args=[firing.pk]), {f"note-{ticket.pk}": "   "})

        assert response.status_code == 200
        body = response.content.decode()
        assert f"Say what happened to ticket {ticket.pk}." in body
        assert f"Write {maker.display_name} a note about ticket {ticket.pk}." in body
        assert f"Choose fired or back to the queue for ticket {ticket.pk}." in body
        assert [row.ticked for row in response.context["form"].rows] == [False]
        assert _fresh(ticket).status == Status.LOADED

    def it_refuses_a_ticked_ticket_from_another_firing(crew, crew_client, maker, kiln_guild):
        ticket = _waiting(maker)
        firing = _loaded(crew, ticket)

        response = crew_client.post(reverse("kiln:unload", args=[firing.pk]), {"fired": [ticket.pk, 99999]})

        assert "A ticked ticket is not in this firing." in response.content.decode()
        assert not KilnFiring.objects.get(pk=firing.pk).is_unloaded

    def it_tells_the_second_crew_member_it_is_already_unloaded(crew, crew_client, maker, make_member, kiln_guild):
        ticket = _waiting(maker)
        firing = _loaded(crew, ticket)
        other = make_member()
        from kiln import services

        def the_other_crew_member_finishes_first(the_firing: KilnFiring) -> list[KilnTicket]:
            tickets = services.unload_sheet(the_firing)
            unload_kiln(firing_pk=the_firing.pk, exceptions={}, by=other)
            return tickets

        with patch("kiln.views.unload_sheet", side_effect=the_other_crew_member_finishes_first):
            response = crew_client.post(reverse("kiln:unload", args=[firing.pk]), {"fired": [ticket.pk]}, follow=True)

        assert response.redirect_chain[-1][0] == reverse("kiln:firing", args=[firing.pk])
        assert "was already unloaded by" in [str(m) for m in response.context["messages"]][0]
        assert len(_bells(maker)) == 1

    def it_sends_an_unloaded_firing_to_its_page(crew, crew_client, maker, kiln_guild):
        firing = _loaded(crew, _waiting(maker))
        unload_kiln(firing_pk=firing.pk, exceptions={}, by=crew)

        response = crew_client.get(reverse("kiln:unload", args=[firing.pk]), follow=True)

        assert response.redirect_chain[-1][0] == reverse("kiln:firing", args=[firing.pk])
        assert f"{firing.name} is already unloaded." in [str(m) for m in response.context["messages"]]

    def it_is_a_404_for_no_such_firing(crew_client):
        assert crew_client.get(reverse("kiln:unload", args=[99999])).status_code == 404

    def it_is_for_the_crew_only(crew, maker, maker_client, kiln_guild):
        ticket = _waiting(maker)
        firing = _loaded(crew, ticket)

        assert maker_client.get(reverse("kiln:unload", args=[firing.pk])).status_code == 403
        assert maker_client.post(reverse("kiln:unload", args=[firing.pk]), {"fired": [ticket.pk]}).status_code == 403
        assert _fresh(ticket).status == Status.LOADED


def describe_the_ticket_after_the_unload():
    def it_shows_who_unloaded_it_on_the_timeline(crew, maker, maker_client, kiln_guild):
        ticket = _waiting(maker)
        firing = _loaded(crew, ticket)
        unload_kiln(firing_pk=firing.pk, exceptions={ticket.pk: _note(ticket)}, by=crew)

        body = maker_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()

        fired = timezone.localtime(_fresh(ticket).fired_at)
        assert f"Fired {fired:%a}, {fired:%b} {fired.day}, unloaded by {crew.display_name}" in body
        assert "data-kiln-unload-note" in body and f"Cracked · {firing.name} · fired" in body

    def it_shows_a_trip_back_to_the_queue(crew, maker, maker_client, kiln_guild):
        ticket = _waiting(maker)
        firing = _loaded(crew, ticket)
        unload_kiln(firing_pk=firing.pk, exceptions={ticket.pk: _note(ticket, Outcome.BACK_TO_QUEUE)}, by=crew)

        body = maker_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()

        assert "data-kiln-returned" in body and f"not fired in {firing.name}: cracked" in body
        assert f"Cracked · {firing.name} · back to the queue" in body

    def it_orders_my_history_by_when_each_piece_came_out(crew, maker, maker_client, kiln_guild):
        early, late = _waiting(maker), _waiting(maker)
        first = _loaded(crew, late)
        unload_kiln(firing_pk=first.pk, exceptions={}, by=crew)
        second = _loaded(crew, early)
        unload_kiln(firing_pk=second.pk, exceptions={}, by=crew)

        history = maker_client.get(reverse("kiln:mine")).context["history"]

        assert history == [_fresh(early), _fresh(late)]
