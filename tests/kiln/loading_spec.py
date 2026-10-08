"""Loading the kiln: the waiting queue, Confirm loaded, and the firing it makes (#691 part 2)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone

from kiln.models import KilnFiring, KilnTicket
from kiln.services import LoadResult, load_kiln, load_queue
from membership.models import Member
from tests.kiln.conftest import signed_in
from tests.kiln.factories import (
    ClayOptionFactory,
    GlazeOptionFactory,
    KilnFlagFactory,
    KilnTicketFactory,
    KilnTicketPhotoFactory,
)

pytestmark = pytest.mark.django_db

LOAD = reverse("kiln:load")
Status = KilnTicket.Status


def _waiting(firing_type: str = "bisque", *, days: int = 0, **kwargs: object) -> KilnTicket:
    """A ticket in the queue that has waited ``days`` days, with a cover photo."""
    ticket = KilnTicketFactory(
        status=Status.SUBMITTED,
        firing_type=firing_type,
        submitted_at=timezone.now() - timedelta(days=days),
        **kwargs,
    )
    KilnTicketPhotoFactory(ticket=ticket, is_cover=True)
    return ticket


def _statuses(*tickets: KilnTicket) -> list[str]:
    return [KilnTicket.objects.get(pk=t.pk).status for t in tickets]


def describe_load_kiln():
    def it_moves_exactly_the_ticked_waiting_tickets_of_the_firings_type(crew):
        one, two, untouched = _waiting("glaze"), _waiting("glaze"), _waiting("glaze")

        result = load_kiln(firing_type="glaze", ticket_pks=[one.pk, two.pk], by=crew)

        assert result.loaded == 2 and result.skipped == 0
        firing = result.firing
        assert firing is not None
        assert (firing.firing_type, firing.loaded_by) == ("glaze", crew)
        assert _statuses(one, two, untouched) == [Status.LOADED, Status.LOADED, Status.SUBMITTED]
        assert set(firing.tickets.all()) == {one, two}

    def it_skips_the_other_type_drafts_and_tickets_already_loaded(crew):
        glaze = _waiting("glaze")
        bisque = _waiting("bisque")
        draft = KilnTicketFactory(firing_type="glaze")
        earlier = load_kiln(firing_type="glaze", ticket_pks=[glaze.pk], by=crew).firing

        # A second crew member ticked the same piece a moment ago, plus the others.
        result = load_kiln(firing_type="glaze", ticket_pks=[glaze.pk, bisque.pk, draft.pk], by=crew)

        assert result.firing is None and result.loaded == 0 and result.skipped == 3
        assert KilnTicket.objects.get(pk=glaze.pk).firing == earlier
        assert _statuses(bisque, draft) == [Status.SUBMITTED, Status.DRAFT]
        assert KilnFiring.objects.count() == 1

    def it_loads_what_it_can_and_counts_the_rest(crew):
        glaze, bisque = _waiting("glaze"), _waiting("bisque")

        result = load_kiln(firing_type="bisque", ticket_pks=[glaze.pk, bisque.pk], by=crew)

        assert (result.loaded, result.skipped) == (1, 1)
        assert _statuses(glaze, bisque) == [Status.SUBMITTED, Status.LOADED]

    def it_locks_the_ticked_rows_before_reading_them(crew):
        ticket = _waiting()

        with patch("kiln.models.KilnTicketQuerySet.select_for_update", autospec=True) as lock:
            lock.side_effect = lambda qs, *a, **k: qs
            load_kiln(firing_type="bisque", ticket_pks=[ticket.pk], by=crew)

        lock.assert_called_once()

    def it_numbers_firings_in_one_sequence_across_types(crew):
        first = load_kiln(firing_type="bisque", ticket_pks=[_waiting("bisque").pk], by=crew).firing
        second = load_kiln(firing_type="glaze", ticket_pks=[_waiting("glaze").pk], by=crew).firing

        assert first is not None and second is not None
        assert (first.number, second.number) == (1, 2)
        assert second.name == str(second) == "Glaze firing 2"
        assert KilnFiring.next_number() == 3


def describe_numbering_a_firing():
    def it_takes_the_next_number_when_another_crew_took_this_one(crew):
        KilnFiring.objects.create(firing_type="bisque", number=1, loaded_by=crew)

        with patch.object(KilnFiring, "next_number", side_effect=[1, 2]):
            firing = KilnFiring.start("glaze", crew)

        assert firing.number == 2

    def it_gives_up_loudly_after_repeated_clashes(crew):
        KilnFiring.objects.create(firing_type="bisque", number=1, loaded_by=crew)

        with patch.object(KilnFiring, "next_number", return_value=1), pytest.raises(IntegrityError):
            KilnFiring.start("glaze", crew)


def describe_load_result_message():
    def it_names_the_firing_and_the_count(crew):
        firing = KilnFiring(firing_type="glaze", number=88, loaded_by=crew)

        assert LoadResult(firing, 1, 0).message == "Glaze firing 88 is loaded with 1 ticket."
        assert LoadResult(firing, 5, 0).message == "Glaze firing 88 is loaded with 5 tickets."

    def it_says_what_was_left_out_and_why(crew):
        firing = KilnFiring(firing_type="bisque", number=3, loaded_by=crew)

        assert LoadResult(firing, 2, 1).message.endswith(
            " 1 ticket was left out: already loaded by someone else, changed by the maker, or not this firing's type."
        )
        assert "2 tickets were left out" in LoadResult(None, 0, 2).message
        assert LoadResult(None, 0, 2).message.startswith("Nothing was loaded.")


def describe_load_queue():
    def it_lists_waiting_tickets_longest_wait_first_with_counts():
        newer = _waiting("glaze", days=1)
        older = _waiting("bisque", days=6)
        flagged = _waiting("glaze", days=3)
        KilnFlagFactory(ticket=flagged)
        KilnTicketFactory(firing_type="glaze")  # a draft is not waiting
        KilnTicketFactory(status=Status.LOADED, firing_type="glaze", submitted_at=timezone.now())

        queue = load_queue()

        assert queue.tickets == [older, flagged, newer]
        assert (queue.bisque_count, queue.glaze_count, queue.flagged_count) == (1, 2, 1)
        assert queue.longest_waiting_type == "bisque"

    def it_starts_on_bisque_when_nothing_waits():
        assert load_queue().longest_waiting_type == "bisque"


def describe_load_page():
    def it_is_for_the_crew_only(maker_client, kiln_guild):
        ticket = _waiting()

        assert maker_client.get(LOAD).status_code == 403
        assert maker_client.post(LOAD, {"firing_type": "bisque", "tickets": [ticket.pk]}).status_code == 403
        assert KilnTicket.objects.get(pk=ticket.pk).status == Status.SUBMITTED

    def it_lets_the_lead_in(make_member, kiln_guild):
        lead = make_member()
        kiln_guild.guild_lead = lead
        kiln_guild.save(update_fields=["guild_lead"])

        assert signed_in(lead).get(LOAD).status_code == 200

    def it_shows_every_waiting_ticket_as_a_tile(crew_client, make_member):
        student = make_member(status=Member.Status.GUEST)
        glaze = _waiting("glaze", days=6, maker=student, maker_type="student", glaze_studio=True)
        glaze.studio_glazes.add(GlazeOptionFactory(name="Tile Plum"))
        KilnFlagFactory(ticket=glaze, kind="glaze_no_stilts")
        bisque = _waiting("bisque", days=1, height_in=4, width_in=6, length_in=6, quantity=3)
        KilnFlagFactory(ticket=bisque, kind="manual", note="Lid glazed shut?")

        response = crew_client.get(LOAD)
        body = response.content.decode()

        assert response.context["firing_type"] == "glaze"  # the longest wait decides
        assert body.index(f'data-ticket="{glaze.pk}"') < body.index(f'data-ticket="{bisque.pk}"')
        assert f"{student.display_name} · Student or guest" in body
        assert "Tile Plum" in body and "6 days" in body
        assert "pl-kiln-tile--loud" in body and "Glaze on the bottom, no stilts" in body
        assert "Crew note: Lid glazed shut?" in body
        assert "4 x 6 x 6 in each" in body and "3 pieces" in body
        assert glaze.cover_photo().tile.url in body
        assert "Glaze firing 1" in body
        assert 'aria-label="2 waiting"' in body
        assert reverse("kiln:detail", args=[glaze.pk]) in body

    def it_opens_on_the_type_asked_for(crew_client):
        _waiting("glaze")

        assert crew_client.get(f"{LOAD}?type=bisque").context["firing_type"] == "bisque"
        assert crew_client.get(f"{LOAD}?type=raku").context["firing_type"] == "glaze"

    def it_says_when_nothing_is_waiting(crew_client):
        assert "data-kiln-empty" in crew_client.get(LOAD).content.decode()

    def it_loads_the_ticked_tiles_and_says_so(crew, crew_client):
        one, two = _waiting("glaze"), _waiting("glaze")

        response = crew_client.post(LOAD, {"firing_type": "glaze", "tickets": [one.pk]}, follow=True)

        assert response.redirect_chain[-1][0] == f"{LOAD}?type=glaze"
        assert "Glaze firing 1 is loaded with 1 ticket." in [str(m) for m in response.context["messages"]]
        assert _statuses(one, two) == [Status.LOADED, Status.SUBMITTED]

    def it_reports_a_load_that_put_nothing_in(crew_client):
        ticket = _waiting("bisque")

        response = crew_client.post(LOAD, {"firing_type": "glaze", "tickets": [ticket.pk]}, follow=True)

        messages = [(m.level_tag, str(m)) for m in response.context["messages"]]
        assert messages[0][0] == "error" and messages[0][1].startswith("Nothing was loaded.")

    @pytest.mark.parametrize(
        ("data", "error"),
        [
            ({"firing_type": "glaze"}, "Tick at least one ticket to load."),
            ({"firing_type": "raku", "tickets": ["1"]}, "Choose bisque or glaze."),
            ({"firing_type": "glaze", "tickets": ["999999"]}, "One of the ticked tickets no longer exists."),
            ({"firing_type": "glaze", "tickets": ["x"]}, "One of the ticked tickets no longer exists."),
        ],
    )
    def it_refuses_a_load_it_cannot_read(crew_client, data, error):
        _waiting("glaze")

        response = crew_client.post(LOAD, data, follow=True)

        assert error in [str(m) for m in response.context["messages"]]
        assert KilnFiring.objects.count() == 0

    def it_shows_the_maker_in_the_kiln_and_the_firing(crew, maker, maker_client):
        ticket = _waiting("glaze", maker=maker)
        firing = load_kiln(firing_type="glaze", ticket_pks=[ticket.pk], by=crew).firing
        assert firing is not None

        mine = maker_client.get(reverse("kiln:mine")).content.decode()
        detail = maker_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()

        assert 'data-group="loaded"' in mine and "In the kiln" in mine
        assert f"Loaded {timezone.localdate():%a, %b} " in detail
        assert f"by {crew.display_name} in Glaze firing 1" in detail
        assert reverse("kiln:edit", args=[ticket.pk]) not in detail


def describe_an_edit_racing_a_load():
    def it_refuses_a_save_that_arrives_after_the_load(crew, maker, maker_client):
        ticket = _waiting("bisque", maker=maker)
        clay = ClayOptionFactory()
        stale = KilnTicket.objects.get(pk=ticket.pk)
        load_kiln(firing_type="bisque", ticket_pks=[ticket.pk], by=crew)

        # The edit page passed its "still editable" check before the load committed.
        with patch("kiln.views.get_object_or_404", return_value=stale):
            response = maker_client.post(
                reverse("kiln:edit", args=[ticket.pk]),
                {"firing_type": "bisque", "clay_choice": str(clay.pk), "walls_under_inch": "yes", "action": "submit"},
                follow=True,
            )

        assert KilnTicket.objects.get(pk=ticket.pk).status == Status.LOADED
        assert "This piece is already in the kiln" in " ".join(str(m) for m in response.context["messages"])
