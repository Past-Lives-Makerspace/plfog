"""The crew's view of any ticket, and the flags they add and clear (#691 part 2)."""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from kiln.models import KilnFlag, KilnTicket
from tests.kiln.conftest import signed_in
from tests.kiln.factories import ClayOptionFactory, KilnFlagFactory, KilnTicketFactory, KilnTicketPhotoFactory

pytestmark = pytest.mark.django_db

Kind = KilnFlag.Kind


def _detail(ticket: KilnTicket) -> str:
    return reverse("kiln:detail", args=[ticket.pk])


def _queued(maker: object, **kwargs: object) -> KilnTicket:
    ticket = KilnTicketFactory(maker=maker, status="submitted", submitted_at=timezone.now(), **kwargs)
    KilnTicketPhotoFactory(ticket=ticket, is_cover=True)
    return ticket


def describe_crew_ticket_view():
    def it_opens_any_makers_ticket_with_contact_answers_and_history(crew_client, maker):
        maker.phone = "503 555 0142"
        maker.save(update_fields=["phone"])
        ticket = _queued(maker, firing_type="bisque", clay=ClayOptionFactory(name="Crew View Clay"))

        response = crew_client.get(_detail(ticket))
        body = response.content.decode()

        assert response.templates[0].name == "kiln/ticket_detail_crew.html"
        assert f"Ticket {ticket.pk} · {maker.display_name}" in body
        assert maker.primary_email in body and "503 555 0142" in body and "· Member" in body
        assert "Crew View Clay" in body and "data-kiln-timeline" in body
        assert "No flags." in body
        assert f"Message {maker.display_name}" in body
        # Someone else's ticket: no edit or copy for the crew member.
        assert reverse("kiln:edit", args=[ticket.pk]) not in body

    def it_still_hides_another_makers_ticket_from_a_maker(maker_client, make_member):
        ticket = _queued(make_member())

        assert maker_client.get(_detail(ticket)).status_code == 404

    def it_gives_a_crew_member_their_own_ticket_with_its_actions(crew, crew_client):
        ticket = _queued(crew, firing_type="bisque")

        body = crew_client.get(_detail(ticket)).content.decode()

        assert reverse("kiln:edit", args=[ticket.pk]) in body
        assert "Make another like this" in body
        assert "Reply to the crew" in body

    def it_offers_finish_on_a_crew_members_own_draft_and_no_edit_once_loaded(crew, crew_client):
        draft = KilnTicketFactory(maker=crew)
        loaded = KilnTicketFactory(maker=crew, status="loaded", submitted_at=timezone.now())

        assert "Finish and submit" in crew_client.get(_detail(draft)).content.decode()
        assert reverse("kiln:edit", args=[loaded.pk]) not in crew_client.get(_detail(loaded)).content.decode()

    def it_lists_open_flags_loudest_first_then_cleared_ones_with_who(crew, crew_client, maker):
        ticket = _queued(maker, firing_type="glaze")
        quiet = KilnFlagFactory(ticket=ticket, kind=Kind.OTHER_CLAY, answer="Standard 266")
        loud = KilnFlagFactory(ticket=ticket, kind=Kind.GLAZE_ON_BOTTOM_NO_STILTS)
        cleared = KilnFlagFactory(ticket=ticket, kind=Kind.COMMERCIAL_GLAZE)
        cleared.clear(by=crew)
        manual = ticket.add_flag("Lid glazed shut?", by=crew)

        body = crew_client.get(_detail(ticket)).content.decode()

        order = [body.index(f'data-flag="{f.pk}"') for f in (loud, quiet, manual, cleared)]
        assert order == sorted(order)
        assert '"Standard 266".' in body
        assert f"Lid glazed shut? Added by {crew.display_name}" in body
        assert f"Cleared by {crew.display_name}" in body
        assert reverse("kiln:flag_clear", args=[ticket.pk, cleared.pk]) not in body
        assert reverse("kiln:flag_clear", args=[ticket.pk, quiet.pk]) in body
        assert 'pl-kiln-count">3<' in body


def describe_adding_a_flag():
    def it_adds_a_crew_flag_with_who_added_it(crew, crew_client, maker):
        ticket = _queued(maker)

        response = crew_client.post(reverse("kiln:flag_add", args=[ticket.pk]), {"note": "  Thin foot  "}, follow=True)

        flag = ticket.flags.get()
        assert (flag.kind, flag.note, flag.added_by, flag.cleared_at) == (Kind.MANUAL, "Thin foot", crew, None)
        assert "Flag added. Only the crew can see it." in [str(m) for m in response.context["messages"]]

    def it_refuses_a_flag_with_no_note(crew_client, maker):
        ticket = _queued(maker)

        response = crew_client.post(reverse("kiln:flag_add", args=[ticket.pk]), {"note": " "}, follow=True)

        assert not ticket.flags.exists()
        assert "Say what the crew should check." in [str(m) for m in response.context["messages"]]

    def it_is_for_the_crew_only(maker, maker_client, kiln_guild):
        ticket = _queued(maker)

        response = maker_client.post(reverse("kiln:flag_add", args=[ticket.pk]), {"note": "Mine"})

        assert response.status_code == 403
        assert not ticket.flags.exists()

    def it_404s_an_unknown_ticket(crew_client):
        assert crew_client.post(reverse("kiln:flag_add", args=[999999]), {"note": "x"}).status_code == 404


def describe_clearing_a_flag():
    @pytest.mark.parametrize("kind", [Kind.OTHER_CLAY, Kind.MANUAL])
    def it_clears_any_flag_and_keeps_who_did_it(crew, crew_client, maker, kind):
        ticket = _queued(maker)
        flag = KilnFlagFactory(ticket=ticket, kind=kind)

        response = crew_client.post(reverse("kiln:flag_clear", args=[ticket.pk, flag.pk]), follow=True)

        flag.refresh_from_db()
        assert flag.cleared_by == crew and flag.cleared_at is not None
        assert "Flag cleared." in [str(m) for m in response.context["messages"]]

    def it_keeps_the_first_clear(crew, make_member, maker):
        flag = KilnFlagFactory(ticket=_queued(maker))
        flag.clear(by=crew)
        first = flag.cleared_at

        flag.clear(by=make_member())

        flag.refresh_from_db()
        assert (flag.cleared_by, flag.cleared_at) == (crew, first)

    def it_is_for_the_crew_only(maker, maker_client, kiln_guild):
        ticket = _queued(maker)
        flag = KilnFlagFactory(ticket=ticket)

        assert maker_client.post(reverse("kiln:flag_clear", args=[ticket.pk, flag.pk])).status_code == 403
        flag.refresh_from_db()
        assert flag.cleared_at is None

    def it_404s_a_flag_on_another_ticket(crew_client, maker):
        flag = KilnFlagFactory(ticket=_queued(maker))
        other = _queued(maker)

        assert crew_client.post(reverse("kiln:flag_clear", args=[other.pk, flag.pk])).status_code == 404

    def it_reopens_a_cleared_automatic_flag_only_when_its_answer_changes(crew, maker):
        ticket = _queued(maker, clay_other=True, clay_other_name="Standard 266")
        ticket.sync_automatic_flags()
        flag = ticket.flags.get(kind=Kind.OTHER_CLAY)
        flag.clear(by=crew)

        ticket.sync_automatic_flags()
        flag.refresh_from_db()
        assert flag.cleared_by == crew

        ticket.clay_other_name = "Standard 112"
        ticket.save()
        ticket.sync_automatic_flags()
        flag.refresh_from_db()
        assert flag.cleared_at is None and flag.cleared_by is None

    def it_never_touches_a_crew_flag_when_the_answers_change(crew, maker):
        ticket = _queued(maker)
        manual = ticket.add_flag("Check the foot", by=crew)

        ticket.sync_automatic_flags()

        assert ticket.flags.get() == manual


def describe_what_the_maker_sees():
    def it_hides_crew_flags_and_their_notes_from_the_maker(crew, maker, maker_client):
        ticket = _queued(maker, clay_other=True, clay_other_name="Mystery")
        ticket.sync_automatic_flags()
        ticket.add_flag("Secret crew note", by=crew)

        detail = maker_client.get(_detail(ticket)).content.decode()
        mine = maker_client.get(reverse("kiln:mine")).content.decode()

        assert "Secret crew note" not in detail and "Secret crew note" not in mine
        assert 'data-flag="manual"' not in detail
        assert KilnFlag.MAKER_NOTES[Kind.OTHER_CLAY] in detail
        assert "The crew will take a look: other clay" in mine
        assert "added by the crew" not in mine

    def it_shows_no_flag_notice_for_a_crew_flag_alone(crew, maker, maker_client):
        ticket = _queued(maker)
        ticket.add_flag("Only crew", by=crew)

        detail = maker_client.get(_detail(ticket)).content.decode()

        assert "data-kiln-flags" not in detail
        assert ticket.maker_flags() == []
        assert len(ticket.open_flags()) == 1


def describe_crew_label():
    def it_names_the_kind_or_quotes_a_crew_note():
        assert KilnFlagFactory(kind=Kind.OTHER_CLAY).crew_label == "Other clay"
        assert KilnFlagFactory(kind=Kind.MANUAL, note="Lid shut?").crew_label == "Crew note: Lid shut?"


def describe_lead_as_crew():
    def it_opens_any_ticket_for_the_guild_lead(make_member, kiln_guild, maker):
        lead = make_member()
        kiln_guild.guild_lead = lead
        kiln_guild.save(update_fields=["guild_lead"])
        ticket = _queued(maker)

        assert signed_in(lead).get(_detail(ticket)).status_code == 200
