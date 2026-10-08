"""The reply thread on a kiln ticket and the notification each side gets (#691 part 2)."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from core.events.registry import KILN_CREW_REPLIED, KILN_MAKER_REPLIED, Channel
from core.models import Notification, NotificationPreference, PushSubscription
from kiln.models import KilnReply, KilnTicket
from kiln.services import post_reply
from membership.models import Member
from tests.kiln.conftest import signed_in
from tests.kiln.factories import KilnTicketFactory
from tests.membership.factories import GuildStaffMembershipFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def pushed() -> Iterator[MagicMock]:
    with patch("core.events.channels.send_web_push") as send_web_push:
        yield send_web_push


def _ticket(maker: Member) -> KilnTicket:
    return KilnTicketFactory(maker=maker, status="submitted", firing_type="glaze", submitted_at=timezone.now())


def _bells(member: Member, key: str) -> list[Notification]:
    return list(Notification.objects.filter(user=member.user, trigger=key))


def _reply_url(ticket: KilnTicket) -> str:
    return reverse("kiln:reply", args=[ticket.pk])


def describe_posting():
    def it_lets_the_maker_post_on_their_own_ticket(maker, maker_client, kiln_guild):
        ticket = _ticket(maker)

        response = maker_client.post(_reply_url(ticket), {"body": "Is it cone 6?"}, follow=True)

        reply = ticket.replies.get()
        assert (reply.author, reply.body, reply.from_crew) == (maker, "Is it cone 6?", False)
        assert response.redirect_chain[-1][0] == f"{reverse('kiln:detail', args=[ticket.pk])}#kiln-messages"
        assert "Message sent." in [str(m) for m in response.context["messages"]]

    def it_lets_any_crew_member_post_on_any_ticket(crew, crew_client, maker):
        ticket = _ticket(maker)

        crew_client.post(_reply_url(ticket), {"body": "Quick check"})

        reply = ticket.replies.get()
        assert reply.author == crew and reply.from_crew

    def it_refuses_another_makers_ticket(maker_client, make_member, kiln_guild):
        ticket = _ticket(make_member())

        assert maker_client.post(_reply_url(ticket), {"body": "Hi"}).status_code == 404
        assert not KilnReply.objects.exists()

    def it_refuses_an_empty_or_long_message(maker, maker_client, kiln_guild):
        ticket = _ticket(maker)

        blank = maker_client.post(_reply_url(ticket), {"body": "   "}, follow=True)
        long = maker_client.post(_reply_url(ticket), {"body": "x" * 2001}, follow=True)

        assert "Write a message first." in [str(m) for m in blank.context["messages"]]
        assert "Keep it under 2000 characters." in [str(m) for m in long.context["messages"]]
        assert not KilnReply.objects.exists()

    def it_only_accepts_posts(maker, maker_client):
        assert maker_client.get(_reply_url(_ticket(maker))).status_code == 405


def describe_the_thread():
    def it_shows_the_maker_every_message_with_the_crew_tagged(crew, maker, maker_client, kiln_guild):
        ticket = _ticket(maker)
        KilnReply.objects.create(ticket=ticket, author=crew, body="Is the 266 cone 6?\nThe label was gone.")
        KilnReply.objects.create(ticket=ticket, author=maker, body="Yes it is.")

        body = maker_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()

        assert body.index("Is the 266 cone 6?<br>The label was gone.") < body.index("Yes it is.")
        assert crew.display_name in body and "Kiln crew" in body
        assert "pl-kiln-msg pl-kiln-msg--mine" in body and "<strong>You</strong>" in body
        assert "Reply to the crew" in body and "The kiln crew gets a notification." in body

    def it_invites_the_first_message(maker, maker_client, crew_client):
        ticket = _ticket(maker)

        assert (
            "No messages yet. Ask the crew"
            in maker_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()
        )
        crew_body = crew_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()
        assert f"No messages yet. Ask {maker.display_name}" in crew_body

    def it_reads_the_thread_without_a_query_per_message(crew, maker, maker_client, django_assert_max_num_queries):
        ticket = _ticket(maker)
        for i in range(6):
            KilnReply.objects.create(ticket=ticket, author=crew if i % 2 else maker, body=f"message {i}")
        url = reverse("kiln:detail", args=[ticket.pk])
        maker_client.get(url)

        with django_assert_max_num_queries(40) as few:
            maker_client.get(url)
        KilnReply.objects.create(ticket=ticket, author=crew, body="one more")
        with django_assert_max_num_queries(len(few.captured_queries)):
            maker_client.get(url)


def describe_a_crew_reply():
    def it_rings_and_emails_the_maker_linking_the_ticket(crew, maker, kiln_guild):
        ticket = _ticket(maker)

        post_reply(ticket, crew, "Is the Standard 266 the cone 6 one?")

        bells = _bells(maker, KILN_CREW_REPLIED)
        assert len(bells) == 1
        assert bells[0].title == f"The kiln crew wrote on Ticket {ticket.pk}"
        path = f"{reverse('kiln:detail', args=[ticket.pk])}#kiln-messages"
        assert bells[0].url == path
        assert [m.to for m in mail.outbox] == [[maker.user.email]]
        assert mail.outbox[0].subject == f"The kiln crew wrote on your Ticket {ticket.pk}"
        assert "Is the Standard 266 the cone 6 one?" in mail.outbox[0].body
        assert f"{settings.MEMBER_BASE_URL}{path}" in mail.outbox[0].body
        assert not _bells(crew, KILN_MAKER_REPLIED)

    def it_reaches_a_class_guest(crew, make_member, kiln_guild):
        guest = make_member(status=Member.Status.GUEST)
        ticket = _ticket(guest)

        post_reply(ticket, crew, "Your mug is ready to go in.")

        assert len(_bells(guest, KILN_CREW_REPLIED)) == 1
        assert [m.to for m in mail.outbox] == [[guest.user.email]]

    def it_notifies_once_for_every_message(crew, maker, kiln_guild):
        ticket = _ticket(maker)

        post_reply(ticket, crew, "First")
        post_reply(ticket, crew, "Second")

        assert len(_bells(maker, KILN_CREW_REPLIED)) == 2

    def it_follows_the_makers_email_switch_and_still_rings_the_bell(crew, maker, kiln_guild):
        NotificationPreference.objects.create(
            user=maker.user, event_key=KILN_CREW_REPLIED, channel=Channel.EMAIL.value, enabled=False
        )

        post_reply(_ticket(maker), crew, "Quick check")

        assert len(_bells(maker, KILN_CREW_REPLIED)) == 1
        assert mail.outbox == []

    def it_pushes_by_default(crew, maker, kiln_guild, pushed):
        PushSubscription.objects.create(user=maker.user, endpoint="https://push/maker", p256dh="k", auth="a")

        post_reply(_ticket(maker), crew, "Quick check")

        pushed.assert_called_once()


def describe_a_maker_reply():
    def it_tells_the_lead_and_staff_but_never_the_maker(maker, crew, make_member, kiln_guild, pushed):
        lead = make_member()
        kiln_guild.guild_lead = lead
        kiln_guild.save(update_fields=["guild_lead"])
        ticket = _ticket(maker)

        post_reply(ticket, maker, "Yes, cone 6.")

        assert len(_bells(lead, KILN_MAKER_REPLIED)) == 1
        assert len(_bells(crew, KILN_MAKER_REPLIED)) == 1
        assert _bells(maker, KILN_MAKER_REPLIED) == [] and _bells(maker, KILN_CREW_REPLIED) == []
        assert sorted(m.to[0] for m in mail.outbox) == sorted([lead.user.email, crew.user.email])
        assert mail.outbox[0].subject == f"{maker.display_name} wrote on kiln Ticket {ticket.pk}"
        assert _bells(crew, KILN_MAKER_REPLIED)[0].body == "Yes, cone 6."
        assert not pushed.called  # push is opt in for the crew

    def it_leaves_out_a_crew_member_writing_on_their_own_ticket(crew, make_member, kiln_guild):
        other = make_member()
        GuildStaffMembershipFactory(guild=kiln_guild, member=other)
        ticket = _ticket(crew)

        reply = post_reply(ticket, crew, "Mine, on the top shelf.")

        assert not reply.from_crew
        assert _bells(crew, KILN_MAKER_REPLIED) == []
        assert len(_bells(other, KILN_MAKER_REPLIED)) == 1

    def it_reaches_nobody_when_there_is_no_kiln_guild(maker):
        reply = post_reply(_ticket(maker), maker, "Anyone there?")

        assert reply.pk is not None
        assert not Notification.objects.exists()

    def it_skips_a_staff_row_with_no_login(maker, kiln_guild):
        from tests.membership.factories import MemberFactory

        GuildStaffMembershipFactory(guild=kiln_guild, member=MemberFactory(user=None))

        post_reply(_ticket(maker), maker, "Hello?")

        assert not Notification.objects.exists()


def describe_crew_reply_through_the_page():
    def it_notifies_the_maker_when_the_crew_sends_from_the_page(crew_client, maker, crew):
        ticket = _ticket(maker)
        signed_in(maker)

        crew_client.post(_reply_url(ticket), {"body": "Loaded on the middle shelf."})

        assert len(_bells(maker, KILN_CREW_REPLIED)) == 1
