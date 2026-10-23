"""Volunteer shifts (Track C, part 2): the Your Info step, signing up and removing, the band,
the shifts by phase, the organisers' roster and CSV, the shifts editor on the card's edit
page, and the volunteers card now linking to its page."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from hq import services
from hq.models import HubCard, HubGoal, ShiftSignup, VolunteerShift
from hq.services import VolunteerBand, VolunteerInfo, goal_rows, roster_rows
from hub.view_as import ROLE_GUEST
from tests.hq.conftest import signed_in
from tests.hq.factories import HubCardFactory, HubGoalFactory, ShiftSignupFactory, VolunteerShiftFactory

pytestmark = pytest.mark.django_db

PUBLIC = HubCard.Audience.PUBLIC
HTMX = {"HTTP_HX_REQUEST": "true"}
GUEST = {"name": "Pat Guest", "email": "pat@example.com", "phone": "503 555 0100", "note": "Evenings only"}


def _page(hub: Any) -> str:
    return reverse("hq:volunteers", args=[hub.slug])


def _info_url(hub: Any) -> str:
    return reverse("hq:volunteer_info", args=[hub.slug])


def _signup_url(shift: Any) -> str:
    return reverse("hq:shift_signup", args=[shift.hub.slug, shift.pk])


def _remove_url(shift: Any) -> str:
    return reverse("hq:shift_remove", args=[shift.hub.slug, shift.pk])


def _roster_url(hub: Any) -> str:
    return reverse("hq:volunteer_roster", args=[hub.slug])


def _csv_url(hub: Any) -> str:
    return reverse("hq:volunteer_roster_csv", args=[hub.slug])


def _toast(response: Any) -> dict[str, str]:
    toast: dict[str, str] = json.loads(response["HX-Trigger"])["showToast"]
    return toast


def _guest_with_info(hub: Any, **over: str) -> Client:
    """A signed out browser that has saved the Your Info step."""
    client = Client()
    response = client.post(_info_url(hub), {**GUEST, **over})
    assert response.status_code == 302
    return client


@pytest.fixture
def card(open_hub):
    return HubCardFactory(hub=open_hub, kind=HubCard.Kind.VOLUNTEERS, audience=PUBLIC, title="Volunteer Sign-up")


@pytest.fixture
def shift(card):
    return VolunteerShiftFactory(hub=card.hub, phase=VolunteerShift.Phase.BEFORE, role="Flyer runner", need=2)


def describe_your_info():
    def it_saves_a_guests_info_in_the_session_and_lands_back_on_the_page(card, shift):
        client = Client()

        response = client.post(_info_url(card.hub), GUEST)

        assert response.status_code == 302
        assert response["Location"] == _page(card.hub)
        assert client.session[services.SESSION_KEY] == {**GUEST, "has_olcc": False}
        body = client.get(_page(card.hub)).content.decode()
        assert "data-hq-vol-saved" in body
        assert 'value="Pat Guest"' in body
        assert f'action="{_signup_url(shift)}"' in body

    def it_shows_the_empty_state_and_disabled_buttons_to_a_guest_who_saved_nothing(card, shift):
        body = Client().get(_page(card.hub)).content.decode()

        assert "data-hq-vol-empty" in body
        assert "data-hq-vol-need-info" in body
        assert "disabled data-hq-shift-signup" in body
        assert "data-hq-shift-hint" in body
        assert _signup_url(shift) not in body

    def it_prefills_a_members_name_email_and_phone(card, shift, make_member):
        member = make_member(preferred_name="Lee", phone="503 555 0199")

        body = signed_in(member).get(_page(card.hub)).content.decode()

        assert "data-hq-vol-prefilled" in body
        assert 'value="Lee"' in body
        assert f'value="{member.primary_email}"' in body
        assert 'value="503 555 0199"' in body
        assert f'action="{_signup_url(shift)}"' in body
        assert 'name="website"' not in body

    def it_prefers_what_a_member_saved_over_their_record(card, make_member):
        member = make_member(preferred_name="Lee")
        client = signed_in(member)
        client.post(_info_url(card.hub), {**GUEST, "name": "Lee on the Night", "has_olcc": "on"})

        body = client.get(_page(card.hub)).content.decode()

        assert 'value="Lee on the Night"' in body
        assert "data-hq-vol-saved" in body
        assert services.person_for(client.get(_page(card.hub)).wsgi_request, member) == VolunteerInfo(
            name="Lee on the Night", email="pat@example.com", phone="503 555 0100", has_olcc=True, note="Evenings only"
        )

    def it_refuses_a_missing_email_and_renders_the_page_with_the_error(card):
        client = Client()

        response = client.post(_info_url(card.hub), {**GUEST, "email": ""})

        assert response.status_code == 200
        body = response.content.decode()
        assert "data-hq-volunteers" in body
        assert "pl-field-error" in body
        assert services.SESSION_KEY not in client.session

    def it_lowercases_and_trims_the_email_and_the_words(card):
        client = _guest_with_info(card.hub, email="  Pat@Example.COM ", name=" Pat ")

        assert client.session[services.SESSION_KEY]["email"] == "pat@example.com"
        assert client.session[services.SESSION_KEY]["name"] == "Pat"

    def it_writes_nothing_when_the_honeypot_is_filled(card):
        client = Client()

        response = client.post(_info_url(card.hub), {**GUEST, "website": "https://spam.example"})

        assert response.status_code == 302
        assert response["Location"] == _page(card.hub)
        assert services.SESSION_KEY not in client.session

    def it_caps_the_note_at_500_characters(card):
        client = Client()

        refused = client.post(_info_url(card.hub), {**GUEST, "note": "x" * 501})
        accepted = client.post(_info_url(card.hub), {**GUEST, "note": "x" * 500})

        assert refused.status_code == 200
        assert "pl-field-error" in refused.content.decode()
        assert accepted.status_code == 302
        assert len(client.session[services.SESSION_KEY]["note"]) == 500
        assert ShiftSignup._meta.get_field("note").max_length == ShiftSignup.MAX_NOTE_LENGTH == 500

    def it_renders_the_honeypot_for_guests_only(card, member_client):
        assert 'name="website"' in Client().get(_page(card.hub)).content.decode()
        assert 'name="website"' not in member_client.get(_page(card.hub)).content.decode()

    def it_is_nobody_for_an_admin_previewing_as_guest(card, admin_client):
        session = admin_client.session
        session["view_as_role"] = ROLE_GUEST
        session.save()

        body = admin_client.get(_page(card.hub)).content.decode()

        assert "data-hq-vol-empty" in body


def describe_signing_up():
    def it_puts_a_guest_on_a_shift_and_shows_remove_me(card, shift):
        client = _guest_with_info(card.hub)

        response = client.post(_signup_url(shift))

        assert response.status_code == 302
        assert response["Location"] == _page(card.hub)
        signup = ShiftSignup.objects.get()
        assert signup.shift == shift
        assert signup.member is None
        assert signup.name == "Pat Guest"
        assert signup.email == "pat@example.com"
        assert signup.phone == "503 555 0100"
        assert signup.note == "Evenings only"
        body = client.get(_page(card.hub)).content.decode()
        assert f'action="{_remove_url(shift)}"' in body
        assert "data-hq-shift-remove" in body
        assert "data-hq-band-filled>1 / 2<" in body
        assert "pl-hq-vol__shift--mine" in body

    def it_records_the_member_on_a_members_sign_up(card, shift, member):
        response = signed_in(member).post(_signup_url(shift))

        assert response.status_code == 302
        signup = ShiftSignup.objects.get()
        assert signup.member == member
        assert signup.name == member.display_name
        assert signup.email == member.primary_email

    def it_refuses_a_duplicate_email_on_one_shift(card, shift):
        client = _guest_with_info(card.hub)
        client.post(_signup_url(shift))

        response = client.post(_signup_url(shift), **HTMX)

        assert response.status_code == 200
        assert _toast(response)["type"] == "info"
        assert ShiftSignup.objects.count() == 1
        with pytest.raises(services.AlreadySignedUp):
            services.sign_up(shift, VolunteerInfo(name="Pat", email="PAT@example.com"), member=None)

    def it_refuses_a_full_shift(card):
        full = VolunteerShiftFactory(hub=card.hub, need=1)
        ShiftSignupFactory(shift=full)
        client = _guest_with_info(card.hub)

        response = client.post(_signup_url(full), **HTMX)

        assert response.status_code == 200
        assert _toast(response)["type"] == "error"
        assert full.signups.count() == 1
        with pytest.raises(services.ShiftFull):
            services.sign_up(full, VolunteerInfo(name="Pat", email="pat@example.com"), member=None)
        body = client.get(_page(card.hub)).content.decode()
        assert "data-hq-shift-full" in body
        assert _signup_url(full) not in body

    def it_keeps_remove_me_for_a_person_on_a_full_shift(card):
        full = VolunteerShiftFactory(hub=card.hub, need=1)
        ShiftSignupFactory(shift=full, email="pat@example.com")
        client = _guest_with_info(card.hub)

        body = client.get(_page(card.hub)).content.decode()

        assert "data-hq-shift-full" not in body
        assert f'action="{_remove_url(full)}"' in body

    def it_answers_an_htmx_post_with_the_row_the_band_and_a_toast(card, shift):
        client = _guest_with_info(card.hub)

        response = client.post(_signup_url(shift), **HTMX)

        assert response.status_code == 200
        body = response.content.decode()
        assert body.count(f'data-hq-shift="{shift.pk}"') == 1
        assert "data-hq-shift-remove" in body
        assert 'id="hq-volunteer-band" data-hq-band hx-swap-oob="true"' in body
        assert "data-hq-band-filled>1 / 2<" in body
        assert "data-hq-band-people>1<" in body
        assert _toast(response) == {"message": "You are on Flyer runner. Thank you!", "type": "success"}

    def it_tells_a_guest_with_no_info_to_save_it_first(card, shift):
        plain = Client().post(_signup_url(shift))
        htmx = Client().post(_signup_url(shift), **HTMX)

        assert plain.status_code == 302
        assert _toast(htmx)["type"] == "error"
        assert not ShiftSignup.objects.exists()

    def it_counts_guest_sign_ups_against_the_address_and_refuses_past_the_cap(card, settings):
        cache.clear()
        shifts = [VolunteerShiftFactory(hub=card.hub) for _ in range(3)]
        client = _guest_with_info(card.hub)

        with patch("hq.services.RATE_HOURLY_LIMIT", 2):
            first = client.post(_signup_url(shifts[0]), **HTMX)
            second = client.post(_signup_url(shifts[1]), **HTMX)
            third = client.post(_signup_url(shifts[2]), **HTMX)

        assert _toast(first)["type"] == "success"
        assert _toast(second)["type"] == "success"
        assert _toast(third)["type"] == "error"
        assert ShiftSignup.objects.count() == 2
        assert not shifts[2].signups.exists()

    def it_never_counts_a_member(card, shift, member):
        cache.clear()

        with patch("hq.services.RATE_HOURLY_LIMIT", 0):
            response = signed_in(member).post(_signup_url(shift))

        assert response.status_code == 302
        assert ShiftSignup.objects.count() == 1

    def it_is_a_post_on_a_shift_of_this_hub(card, shift):
        other = VolunteerShiftFactory()
        client = _guest_with_info(card.hub)

        assert client.get(_signup_url(shift)).status_code == 405
        assert client.post(reverse("hq:shift_signup", args=[card.hub.slug, other.pk])).status_code == 404

    def it_acts_on_whatever_email_was_saved_even_someone_elses(card, shift):
        """Identity on the sheet is the email in Your Info: that address's sign-ups are this visitor's."""
        ShiftSignupFactory(shift=shift, name="Someone Else", email="pat@example.com")
        client = _guest_with_info(card.hub)

        body = client.get(_page(card.hub)).content.decode()
        response = client.post(_remove_url(shift))

        assert f'action="{_remove_url(shift)}"' in body
        assert response.status_code == 302
        assert not shift.signups.exists()

    def it_matches_a_member_by_the_email_they_typed_not_their_record(card, shift, member):
        client = signed_in(member)
        client.post(_signup_url(shift))
        client.post(_info_url(card.hub), {**GUEST, "email": "other@example.com"})

        body = client.get(_page(card.hub)).content.decode()
        response = client.post(_signup_url(shift))

        assert f'action="{_signup_url(shift)}"' in body
        assert response.status_code == 302
        assert sorted(shift.signups.values_list("email", flat=True)) == [member.primary_email, "other@example.com"]
        assert {signup.member for signup in shift.signups.all()} == {member}

    def it_lowercases_the_email_on_the_row(shift):
        signup = ShiftSignup.objects.create(shift=shift, name="Pat", email="  PAT@Example.com ")

        assert signup.email == "pat@example.com"
        assert str(signup) == "Pat on Flyer runner"


def describe_rate_key():
    def it_prefers_cloudflares_address_then_the_rightmost_forwarded_entry_then_remote_addr(rf):
        connecting = rf.post("/", HTTP_CF_CONNECTING_IP=" 203.0.113.9 ", HTTP_X_FORWARDED_FOR="1.1.1.1, 2.2.2.2")
        forwarded = rf.post("/", HTTP_X_FORWARDED_FOR="1.1.1.1, 2.2.2.2")
        direct = rf.post("/")

        assert services.rate_key(connecting) == "203.0.113.9"
        assert services.rate_key(forwarded) == "2.2.2.2"
        assert services.rate_key(direct) == "127.0.0.1"


def describe_removing():
    def it_takes_the_person_off_the_shift(card, shift):
        ShiftSignupFactory(shift=shift, email="pat@example.com")
        client = _guest_with_info(card.hub)

        response = client.post(_remove_url(shift))

        assert response.status_code == 302
        assert not shift.signups.exists()

    def it_tells_a_person_who_was_not_on_it(card, shift):
        client = _guest_with_info(card.hub)

        response = client.post(_remove_url(shift), **HTMX)

        assert _toast(response) == {"message": "You were not on Flyer runner.", "type": "info"}
        with pytest.raises(services.NotSignedUp):
            services.remove(shift, "pat@example.com")

    def it_answers_htmx_with_sign_me_up_back_on_the_row(card, shift):
        ShiftSignupFactory(shift=shift, email="pat@example.com")
        client = _guest_with_info(card.hub)

        response = client.post(_remove_url(shift), **HTMX)

        body = response.content.decode()
        assert _toast(response)["type"] == "success"
        assert "data-hq-shift-signup" in body
        assert "data-hq-band-filled>0 / 2<" in body

    def it_needs_the_info_step(card, shift):
        ShiftSignupFactory(shift=shift, email="pat@example.com")

        response = Client().post(_remove_url(shift), **HTMX)

        assert _toast(response)["type"] == "error"
        assert shift.signups.count() == 1


def describe_the_band():
    @pytest.fixture
    def sheet(card):
        """Three shifts: 2 of 3, 0 of 2 and 1 of 1, with one person on two of them."""
        a = VolunteerShiftFactory(hub=card.hub, role="A", need=3, sort_order=0)
        b = VolunteerShiftFactory(hub=card.hub, role="B", need=2, sort_order=1)
        c = VolunteerShiftFactory(hub=card.hub, role="C", need=1, sort_order=2)
        ShiftSignupFactory(shift=a, email="one@example.com")
        ShiftSignupFactory(shift=a, email="two@example.com")
        ShiftSignupFactory(shift=c, email="one@example.com")
        return a, b, c

    def it_counts_people_once_and_spots_against_the_total(card, sheet):
        band = services.volunteer_band(card.hub)

        assert (band.people, band.filled, band.total) == (2, 3, 6)
        assert band.most_needed == sheet[1]
        assert band.most_needed.spots_open == 2

    def it_picks_the_first_of_tied_gaps_in_page_order(card):
        first = VolunteerShiftFactory(hub=card.hub, need=2, sort_order=0)
        VolunteerShiftFactory(hub=card.hub, need=2, sort_order=1)

        assert services.volunteer_band(card.hub).most_needed == first

    def it_has_nothing_most_needed_when_every_shift_is_full_or_there_are_none(card):
        assert services.volunteer_band(card.hub) == VolunteerBand(people=0, filled=0, total=0, most_needed=None)
        full = VolunteerShiftFactory(hub=card.hub, need=1)
        ShiftSignupFactory(shift=full)

        assert services.volunteer_band(card.hub).most_needed is None

    def it_feeds_the_volunteer_spots_goal(card, sheet):
        HubGoalFactory(hub=card.hub, kind=HubGoal.Kind.VOLUNTEER_SPOTS, target=82)

        (row,) = goal_rows(card.hub)

        assert row.current == 3
        assert row.target == 82

    def it_renders_on_the_page(card, sheet):
        body = Client().get(_page(card.hub)).content.decode()

        assert "data-hq-band-people>2<" in body
        assert "data-hq-band-filled>3 / 6<" in body
        assert "Most needed: B (2 open)" in body
        assert 'max="6" value="3"' in body

    def it_says_every_shift_is_covered_with_no_gap(card):
        body = Client().get(_page(card.hub)).content.decode()

        assert "data-hq-band-most-needed>Every shift is covered" in body
        assert 'max="1" value="0"' in body


def describe_the_shifts():
    def it_groups_by_phase_in_order_and_skips_an_empty_phase(card):
        VolunteerShiftFactory(hub=card.hub, phase=VolunteerShift.Phase.AFTER, role="Teardown")
        VolunteerShiftFactory(hub=card.hub, phase=VolunteerShift.Phase.BEFORE, role="Flyers")
        VolunteerShiftFactory(hub=card.hub, phase=VolunteerShift.Phase.DURING, role="Door", requires_olcc=True)

        body = Client().get(_page(card.hub)).content.decode()

        assert (
            body.index('data-hq-phase="before"')
            < body.index('data-hq-phase="during"')
            < body.index('data-hq-phase="after"')
        )
        assert 'data-hq-phase="setup"' not in body
        assert body.count("data-hq-shift-olcc") == 1
        assert "data-hq-vol-no-shifts" not in body

    def it_orders_by_phase_then_sort_order(card):
        late = VolunteerShiftFactory(hub=card.hub, phase=VolunteerShift.Phase.DURING, sort_order=1)
        early = VolunteerShiftFactory(hub=card.hub, phase=VolunteerShift.Phase.BEFORE, sort_order=9)
        first = VolunteerShiftFactory(hub=card.hub, phase=VolunteerShift.Phase.DURING, sort_order=0)
        setup = VolunteerShiftFactory(hub=card.hub, phase=VolunteerShift.Phase.SETUP, sort_order=0)

        assert list(card.hub.shifts.all()) == [early, setup, first, late]
        assert [group.key for group in services.phase_groups(list(card.hub.shifts.all()))] == [
            "before",
            "setup",
            "during",
        ]

    def it_shows_the_empty_state_with_no_shifts(card):
        assert "data-hq-vol-no-shifts" in Client().get(_page(card.hub)).content.decode()

    def it_counts_through_the_annotation_or_a_query(shift):
        ShiftSignupFactory(shift=shift)
        ShiftSignupFactory(shift=shift)

        annotated = VolunteerShift.objects.with_counts().get(pk=shift.pk)
        plain = VolunteerShift.objects.get(pk=shift.pk)

        assert annotated.filled == 2
        assert annotated.filled_count == 2
        assert annotated.is_full is True
        with CaptureQueriesContext(connection) as queries:
            assert plain.filled_count == 2
        assert len(queries) == 1
        assert str(shift) == "Flyer runner"

    def it_never_reads_open_spots_below_zero(card):
        one = VolunteerShiftFactory(hub=card.hub, need=1)
        ShiftSignupFactory(shift=one)
        ShiftSignupFactory(shift=one)

        assert one.spots_open == 0
        assert one.is_full is True


def describe_the_wine_wall_link():
    def it_shows_when_the_wine_wall_card_is_open_to_the_viewer(card):
        HubCardFactory(hub=card.hub, kind=HubCard.Kind.WINE_WALL, audience=PUBLIC)

        body = Client().get(_page(card.hub)).content.decode()

        assert f'href="{reverse("hq:wine", args=[card.hub.slug])}"' in body
        assert "data-hq-vol-wine" in body

    def it_is_absent_for_a_closed_or_hidden_card(card, member_client, admin_client):
        wine = HubCardFactory(hub=card.hub, kind=HubCard.Kind.WINE_WALL, audience=HubCard.Audience.ADMINS)

        assert "data-hq-vol-wine" not in member_client.get(_page(card.hub)).content.decode()
        assert "data-hq-vol-wine" in admin_client.get(_page(card.hub)).content.decode()
        wine.audience = PUBLIC
        wine.is_hidden = True
        wine.save()
        assert "data-hq-vol-wine" not in member_client.get(_page(card.hub)).content.decode()
        assert "data-hq-vol-wine" in admin_client.get(_page(card.hub)).content.decode()

    def it_is_absent_without_a_wine_wall_card(card):
        assert "data-hq-vol-wine" not in Client().get(_page(card.hub)).content.decode()


def describe_the_roster():
    @pytest.fixture
    def people(card):
        a = VolunteerShiftFactory(hub=card.hub, role="A", sort_order=0, phase=VolunteerShift.Phase.BEFORE, when_text="")
        b = VolunteerShiftFactory(hub=card.hub, role="B", sort_order=1, when_text="Late")
        ShiftSignupFactory(shift=b, name="Zed Last", email="zed@example.com", note="Old note")
        ShiftSignupFactory(shift=a, name="Zed Last", email="zed@example.com", phone="555", has_olcc=True, note="New")
        ShiftSignupFactory(shift=a, name="Amy First", email="amy@example.com")
        return a, b

    def it_groups_a_person_across_shifts_with_their_newest_details(card, people):
        amy, zed = roster_rows(card.hub)

        assert (amy.name, amy.shifts) == ("Amy First", ["A"])
        assert (zed.name, zed.email, zed.phone, zed.has_olcc, zed.note) == (
            "Zed Last",
            "zed@example.com",
            "555",
            True,
            "New",
        )
        assert zed.shifts == ["B", "A"]
        assert services.roster_emails([amy, zed]) == "amy@example.com, zed@example.com"

    def it_is_on_the_page_for_admins_and_the_team_only(card, people, admin_client, member_client, make_member):
        named = make_member()
        card.team.add(named)

        admin_body = admin_client.get(_page(card.hub)).content.decode()
        named_body = signed_in(named).get(_page(card.hub)).content.decode()
        member_body = member_client.get(_page(card.hub)).content.decode()

        for body in (admin_body, named_body):
            assert "data-hq-vol-organizers" in body
            assert body.count("data-hq-roster-row") == 2
            assert "amy@example.com, zed@example.com" in body
            assert 'hx-boost="false" data-pl-download data-hq-roster-csv' in body
        assert "data-hq-vol-organizers" not in member_body
        assert "data-hq-roster-csv" not in member_body

    def it_hides_the_on_page_roster_while_an_admin_previews_but_keeps_the_roster_page(card, people, admin_client):
        """Rendering reads the effective role; the roster page and the CSV read the actual one."""
        session = admin_client.session
        session["view_as_role"] = ROLE_GUEST
        session.save()

        assert "data-hq-vol-organizers" not in admin_client.get(_page(card.hub)).content.decode()
        assert admin_client.get(_roster_url(card.hub)).status_code == 200
        assert admin_client.get(_csv_url(card.hub)).status_code == 200

    def it_has_its_own_page_for_organisers_and_refuses_everyone_else(
        card, people, admin_client, member_client, make_member
    ):
        named = make_member()
        card.team.add(named)

        assert admin_client.get(_roster_url(card.hub)).status_code == 200
        named_body = signed_in(named).get(_roster_url(card.hub)).content.decode()
        assert "data-hq-roster-page" in named_body
        assert named_body.count("data-hq-roster-row") == 2
        assert member_client.get(_roster_url(card.hub)).status_code == 403
        assert Client().get(_roster_url(card.hub)).status_code == 403

    def it_streams_the_csv_for_organisers_only(card, people, admin_client, member_client):
        response = admin_client.get(_csv_url(card.hub))

        assert response.status_code == 200
        assert response["Content-Disposition"].startswith(f'attachment; filename="{card.hub.slug}-volunteers-')
        rows = b"".join(response.streaming_content).decode().splitlines()
        assert rows[0] == "Name,Email,Phone,OLCC permit,Shift,Phase,When,Note,Signed up,Hours logged"
        assert rows[1].startswith("Zed Last,zed@example.com,555,yes,A,Before the event,,New,")
        assert rows[2].startswith("Amy First,amy@example.com,,no,A,Before the event,,,")
        assert rows[3].startswith("Zed Last,zed@example.com,,no,B,During the event,Late,Old note,")
        assert {row.rsplit(",", 1)[1] for row in rows[1:]} == {"0"}
        assert member_client.get(_csv_url(card.hub)).status_code == 403
        assert Client().get(_csv_url(card.hub)).status_code == 403

    def it_neutralises_a_formula_in_what_someone_typed(card, shift, admin_client):
        ShiftSignupFactory(shift=shift, name="=HYPERLINK(1)", note="+1")

        rows = b"".join(admin_client.get(_csv_url(card.hub)).streaming_content).decode().splitlines()

        assert rows[1].startswith("'=HYPERLINK(1),")
        assert ",'+1," in rows[1]

    def it_shows_the_empty_roster_with_copy_disabled(card, admin_client):
        body = admin_client.get(_roster_url(card.hub)).content.decode()

        assert "data-hq-roster-empty" in body
        assert "data-hq-copy-emails disabled" in body


def describe_the_shifts_editor():
    def _edit(card: Any) -> str:
        return reverse("hq:card_edit", args=[card.hub.slug, card.pk])

    def _post(shifts: list[dict[str, Any]], initial: int = 0, **fields: Any) -> dict[str, Any]:
        data: dict[str, Any] = {
            "title": "Volunteer Sign-up",
            "who_for": "Volunteers",
            "bullets": "One",
            "button_text": "Sign up",
            "audience": PUBLIC,
            "shifts-TOTAL_FORMS": str(len(shifts)),
            "shifts-INITIAL_FORMS": str(initial),
            "shifts-MIN_NUM_FORMS": "0",
            "shifts-MAX_NUM_FORMS": "1000",
        }
        for index, shift in enumerate(shifts):
            row = {
                "phase": VolunteerShift.Phase.DURING,
                "role": "Role",
                "when_text": "",
                "need": "1",
                "sort_order": "0",
            }
            row.update(shift)
            for key, value in row.items():
                data[f"shifts-{index}-{key}"] = value
        data.update(fields)
        return data

    def it_is_on_the_volunteers_cards_page_only(card, open_hub, admin_client):
        placeholder = HubCardFactory(hub=open_hub)

        assert "data-hq-shifts-editor" in admin_client.get(_edit(card)).content.decode()
        assert "data-hq-shifts-editor" not in admin_client.get(_edit(placeholder)).content.decode()

    def it_starts_empty_with_the_add_button_and_the_template(card, admin_client):
        body = admin_client.get(_edit(card)).content.decode()

        assert "data-hq-shifts-empty" in body
        assert "data-hq-add-shift" in body
        assert 'id="hq-shift-empty-template"' in body
        assert 'name="shifts-TOTAL_FORMS" value="0"' in body

    def it_adds_a_shift(card, admin_client):
        rows = [
            {
                "phase": VolunteerShift.Phase.BEFORE,
                "role": "Flyer runner",
                "when_text": "Nov 1 to Dec 1",
                "need": "6",
                "description": "Drop flyers around town.",
                "requires_olcc": "",
                "sort_order": "2",
            }
        ]

        response = admin_client.post(_edit(card), _post(rows))

        assert response.status_code == 302
        shift = card.hub.shifts.get()
        assert (shift.phase, shift.role, shift.when_text, shift.need) == ("before", "Flyer runner", "Nov 1 to Dec 1", 6)
        assert shift.description == "Drop flyers around town."
        assert shift.requires_olcc is False
        assert shift.sort_order == 2

    def it_edits_a_shift(card, shift, admin_client):
        rows = [{"id": str(shift.pk), "role": "Bartender", "need": "3", "requires_olcc": "on"}]

        response = admin_client.post(_edit(card), _post(rows, initial=1))

        assert response.status_code == 302
        shift.refresh_from_db()
        assert (shift.role, shift.need, shift.requires_olcc) == ("Bartender", 3, True)

    def it_deletes_a_shift_nobody_is_on_and_offers_delete_for_it(card, shift, admin_client):
        body = admin_client.get(_edit(card)).content.decode()
        assert "data-hq-shift-delete" in body
        assert "data-hq-shift-signed" not in body

        response = admin_client.post(_edit(card), _post([{"id": str(shift.pk), "DELETE": "on"}], initial=1))

        assert response.status_code == 302
        assert not card.hub.shifts.exists()

    def it_refuses_to_delete_a_shift_with_sign_ups_and_names_the_count(card, shift, admin_client):
        ShiftSignupFactory(shift=shift)
        ShiftSignupFactory(shift=shift)
        body = admin_client.get(_edit(card)).content.decode()
        assert "data-hq-shift-delete" not in body
        assert "data-hq-shift-signed>2 signed up." in body

        response = admin_client.post(_edit(card), _post([{"id": str(shift.pk), "DELETE": "on"}], initial=1))

        assert response.status_code == 200
        refused = response.content.decode()
        assert "data-hq-shift-error" in refused
        assert "A shift with sign-ups cannot be deleted: Flyer runner has 2 people signed up." in refused
        assert card.hub.shifts.filter(pk=shift.pk).exists()

    def it_names_one_person_in_the_singular(card, shift, admin_client):
        ShiftSignupFactory(shift=shift)

        response = admin_client.post(_edit(card), _post([{"id": str(shift.pk), "DELETE": "on"}], initial=1))

        assert "Flyer runner has 1 person signed up." in response.content.decode()

    def it_drops_a_delete_for_a_shift_already_gone_and_saves_the_rest(card, shift, admin_client):
        """A stale page: the row another admin removed is skipped, never a 500, and the Save lands."""
        gone = shift.pk
        shift.delete()
        other = VolunteerShiftFactory(hub=card.hub)

        response = admin_client.post(_edit(card), _post([{"id": str(gone), "DELETE": "on"}], initial=1))

        assert response.status_code == 302
        assert list(card.hub.shifts.all()) == [other]

    def it_ignores_a_delete_for_another_hubs_shift(card, admin_client):
        foreign = VolunteerShiftFactory(role="Not ours")
        ShiftSignupFactory(shift=foreign)

        response = admin_client.post(_edit(card), _post([{"id": str(foreign.pk), "DELETE": "on"}], initial=1))

        assert response.status_code == 302
        assert VolunteerShift.objects.filter(pk=foreign.pk).exists()
        assert foreign.signups.count() == 1
        assert not card.hub.shifts.exists()

    def it_skips_a_row_added_and_left_as_it_rendered(card, admin_client):
        rows = [{"role": "", "phase": VolunteerShift.Phase.DURING, "need": "1", "sort_order": "0"}]

        response = admin_client.post(_edit(card), _post(rows))

        assert response.status_code == 302
        assert not card.hub.shifts.exists()

    def it_saves_the_card_and_the_shifts_together(card, shift, admin_client):
        rows = [{"id": str(shift.pk), "role": "Renamed"}]

        admin_client.post(_edit(card), _post(rows, initial=1, title="Help Us Out"))

        card.refresh_from_db()
        shift.refresh_from_db()
        assert card.title == "Help Us Out"
        assert shift.role == "Renamed"


def describe_the_card_url():
    def it_links_the_volunteers_card_to_its_page(card):
        assert card.url == _page(card.hub)
        body = Client().get(reverse("hq:page", args=[card.hub.slug])).content.decode()
        assert f'<a href="{_page(card.hub)}" class="pl-hq-card" data-hq-card-link>' in body


def describe_access():
    def it_follows_the_cards_audience(open_hub, member_client):
        members_only = HubCardFactory(hub=open_hub, kind=HubCard.Kind.VOLUNTEERS, audience=HubCard.Audience.MEMBERS)

        anonymous = Client().get(_page(open_hub))
        assert anonymous.status_code == 302
        assert anonymous["Location"] == f"/accounts/login/?next={_page(open_hub)}"
        assert member_client.get(_page(open_hub)).status_code == 200
        members_only.audience = HubCard.Audience.ADMINS
        members_only.save()
        assert member_client.get(_page(open_hub)).status_code == 404
        assert member_client.post(_info_url(open_hub), GUEST).status_code == 404

    def it_404s_without_a_volunteers_card(open_hub, admin_client):
        assert admin_client.get(_page(open_hub)).status_code == 404


def describe_queries():
    def it_keeps_the_page_flat_however_many_shifts_and_sign_ups(card, admin_client):
        def render() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert admin_client.get(_page(card.hub)).status_code == 200
            return len(queries)

        ShiftSignupFactory(shift=VolunteerShiftFactory(hub=card.hub))
        render()  # the first visit pays for session and sign in bookkeeping; measure the second
        few = render()
        for _ in range(4):
            busy = VolunteerShiftFactory(hub=card.hub)
            ShiftSignupFactory(shift=busy)
            ShiftSignupFactory(shift=busy)

        assert render() == few

    def it_keeps_a_guests_page_flat_too(card):
        client = _guest_with_info(card.hub)

        def render() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert client.get(_page(card.hub)).status_code == 200
            return len(queries)

        ShiftSignupFactory(shift=VolunteerShiftFactory(hub=card.hub), email="pat@example.com")
        few = render()
        for _ in range(3):
            ShiftSignupFactory(shift=VolunteerShiftFactory(hub=card.hub), email="pat@example.com")

        assert render() == few
