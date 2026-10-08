"""Kiln ticket screens: My Tickets, the ticket form, detail, and the crew's lists (#691)."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from kiln.access import kiln_home_url
from kiln.models import ClayOption, GlazeOption, KilnFlag, KilnTicket
from membership.models import Member
from tests.kiln.conftest import photo_upload, signed_in
from tests.kiln.factories import (
    ClayOptionFactory,
    GlazeOptionFactory,
    KilnFlagFactory,
    KilnTicketFactory,
    KilnTicketPhotoFactory,
)

pytestmark = pytest.mark.django_db

NEW = reverse("kiln:new")
MINE = reverse("kiln:mine")
HOME = kiln_home_url()  # where /kiln/ sends everyone but a guest: the Ceramics Guild's Kiln Tickets tab


@pytest.fixture(autouse=True)
def _ceramics_guild(kiln_guild):
    """The guild page that holds a member's kiln home."""


def _bisque(clay: ClayOption, **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"firing_type": "bisque", "clay_choice": str(clay.pk), "walls_under_inch": "yes"}
    data.update(extra)
    return data


def _glaze(clay: ClayOption, **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "firing_type": "glaze",
        "clay_choice": str(clay.pk),
        "bottom_free_of_glaze": "yes",
        "glaze_cone6": "on",
    }
    data.update(extra)
    return data


def _post(client: Client, url: str, data: dict[str, Any], action: str = "submit", photos: int = 1) -> Any:
    payload = dict(data, action=action)
    if photos:
        payload["photos"] = [photo_upload(f"pot{i}.jpg") for i in range(photos)]
    return client.post(url, payload)


def _errors(response: Any) -> dict[str, list[str]]:
    return {key: [str(e) for e in value] for key, value in response.context["form"].errors.items()}


def describe_access():
    def it_sends_an_anonymous_visitor_to_sign_in():
        response = Client().get(MINE)

        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_refuses_a_user_with_no_member_row(db):
        from django.contrib.auth.models import User

        user = User.objects.create_user(username="nomember", email="nomember@example.com")
        Member.objects.filter(user=user).delete()
        client = Client()
        client.force_login(user)

        assert client.get(MINE).status_code == 403

    def it_lets_any_active_member_in(maker_client):
        assert maker_client.get(MINE)["Location"] == HOME
        assert maker_client.get(HOME).status_code == 200
        assert maker_client.get(NEW).status_code == 200


def describe_my_tickets():
    def it_groups_tickets_by_status_with_history_below(maker, maker_client):
        draft = KilnTicketFactory(maker=maker)
        queued = KilnTicketFactory(maker=maker, status="submitted", firing_type="bisque")
        loaded = KilnTicketFactory(maker=maker, status="loaded")
        fired = KilnTicketFactory(maker=maker, status="fired")
        KilnTicketFactory()  # someone else's

        response = maker_client.get(HOME)

        assert response.context["kiln_home"].drafts == [draft]
        assert response.context["kiln_home"].queued == [queued]
        assert response.context["kiln_home"].loaded == [loaded]
        assert response.context["kiln_home"].history == [fired]
        assert not response.context["kiln_home"].has_older
        assert not response.context["kiln_home"].runs_kiln
        body = response.content.decode()
        assert 'data-group="submitted"' in body and "In The Queue" in body
        assert f"Ticket {queued.pk} · Bisque · 1 piece" in body
        assert f"{NEW}?from={queued.pk}" in body

    def it_shows_the_first_page_of_history_and_then_the_rest(maker, maker_client):
        for _ in range(11):
            KilnTicketFactory(maker=maker, status="fired")

        first = maker_client.get(HOME)
        everything = maker_client.get(kiln_home_url(older=True))

        assert len(first.context["kiln_home"].history) == 10 and first.context["kiln_home"].has_older
        assert len(everything.context["kiln_home"].history) == 11 and not everything.context["kiln_home"].has_older

    def it_says_what_a_draft_still_needs(maker, maker_client):
        KilnTicketFactory(maker=maker)
        KilnTicketFactory(maker=maker, ready=True)

        body = maker_client.get(HOME).content.decode()

        assert "Still needs: a photo, the firing, the clay" in body
        assert "Ready to submit" in body

    def it_shows_the_flags_as_a_kind_note_on_a_queued_ticket(maker, maker_client):
        ticket = KilnTicketFactory(maker=maker, status="submitted")
        KilnFlagFactory(ticket=ticket, kind=KilnFlag.Kind.THICK_WALLS)
        loud = KilnTicketFactory(maker=maker, status="submitted")
        KilnFlagFactory(ticket=loud, kind=KilnFlag.Kind.GLAZE_ON_BOTTOM_NO_STILTS)

        body = maker_client.get(HOME).content.decode()

        assert "The crew will take a look: thick walls" in body
        assert "pl-kiln-flag pl-kiln-flag--loud" in body

    def it_shows_a_cover_photo(maker, maker_client):
        ticket = KilnTicketFactory(maker=maker, status="submitted")
        cover = KilnTicketPhotoFactory(ticket=ticket, is_cover=True)

        assert cover.tile.url in maker_client.get(HOME).content.decode()

    def it_offers_a_first_ticket_when_there_are_none(maker_client):
        assert "No tickets yet." in maker_client.get(HOME).content.decode()

    def it_shows_the_crew_links_to_guild_staff(crew_client):
        response = crew_client.get(HOME)

        assert response.context["kiln_home"].runs_kiln
        assert reverse("kiln:lists") in response.content.decode()


def describe_new_ticket():
    def it_asks_every_question_with_the_seeded_lists(maker_client):
        body = maker_client.get(NEW).content.decode()

        assert 'capture="environment"' in body and 'accept="image/*"' in body
        assert "G-Mix 6" in body and "Other (specify)" in body
        assert "Ritual Clear" in body
        assert 'data-branch="bisque"' in body and 'data-branch="glaze"' in body
        assert 'data-follow="clay-other"' in body and 'data-follow="stilts"' in body
        assert "Are all walls on this piece less than 1 inch thick?" in body
        # Name, contact and dates are the system's, never asked.
        assert 'name="email"' not in body and 'name="phone"' not in body and 'name="maker' not in body

    def it_saves_a_draft_with_nothing_answered(maker, maker_client):
        response = _post(maker_client, NEW, {}, action="draft", photos=0)

        assert response.status_code == 302 and response["Location"] == HOME
        ticket = KilnTicket.objects.get(maker=maker)
        assert ticket.status == KilnTicket.Status.DRAFT
        assert ticket.submitted_at is None
        assert not ticket.flags.exists()

    def it_keeps_a_drafts_answers(maker, maker_client):
        clay = ClayOptionFactory()
        _post(maker_client, NEW, {"clay_choice": str(clay.pk), "height_in": "4", "quantity": "2"}, action="draft")

        ticket = KilnTicket.objects.get(maker=maker)
        assert ticket.clay == clay and ticket.quantity == 2
        assert ticket.photos.count() == 1
        edit = maker_client.get(reverse("kiln:edit", args=[ticket.pk])).content.decode()
        assert f'value="{clay.pk}" selected' in edit

    def it_files_a_bisque_ticket_into_the_queue(maker, maker_client):
        response = _post(maker_client, NEW, _bisque(ClayOptionFactory()))

        assert response["Location"] == HOME
        ticket = KilnTicket.objects.get(maker=maker)
        assert ticket.status == KilnTicket.Status.SUBMITTED
        assert ticket.submitted_at is not None
        assert ticket.maker_type == KilnTicket.MakerType.MEMBER
        assert ticket.photos.get().is_cover
        page = maker_client.get(HOME).content.decode()
        assert f"Ticket {ticket.pk} is in the queue." in page

    def it_files_a_glaze_ticket_with_every_glaze_kind(maker, maker_client):
        glazes = [GlazeOptionFactory(), GlazeOptionFactory()]
        data = _glaze(
            ClayOptionFactory(),
            glaze_studio="on",
            studio_glazes=[str(g.pk) for g in glazes],
            glaze_commercial="on",
            commercial_glaze_name="Amaco Blue Rutile",
            glaze_self_made="on",
            self_made_glaze_description="Ash",
            bottom_free_of_glaze="no",
            stilts_added="no",
        )

        _post(maker_client, NEW, data, photos=2)

        ticket = KilnTicket.objects.get(maker=maker)
        assert set(ticket.studio_glazes.all()) == set(glazes)
        assert ticket.photos.count() == 2 and ticket.photos.filter(is_cover=True).count() == 1
        assert set(ticket.flags.values_list("kind", flat=True)) == {
            KilnFlag.Kind.COMMERCIAL_GLAZE,
            KilnFlag.Kind.SELF_MADE_GLAZE,
            KilnFlag.Kind.GLAZE_ON_BOTTOM_NO_STILTS,
        }

    def it_files_an_other_clay_with_its_confirmation_and_flags_it(maker, maker_client):
        data = {
            "firing_type": "bisque",
            "clay_choice": "other",
            "clay_other_name": "Standard 266",
            "clay_other_cone6": "on",
            "walls_under_inch": "no",
        }

        _post(maker_client, NEW, data)

        ticket = KilnTicket.objects.get(maker=maker)
        assert ticket.clay is None and ticket.clay_other_name == "Standard 266"
        assert set(ticket.flags.values_list("kind", flat=True)) == {
            KilnFlag.Kind.OTHER_CLAY,
            KilnFlag.Kind.THICK_WALLS,
        }

    def it_refuses_submit_without_a_photo(maker, maker_client):
        response = _post(maker_client, NEW, _bisque(ClayOptionFactory()), photos=0)

        assert response.status_code == 200
        assert "Add at least one photo of the piece." in _errors(response)["__all__"]
        assert not KilnTicket.objects.filter(maker=maker).exists()

    def it_refuses_submit_without_a_clay_or_firing(maker_client):
        response = _post(maker_client, NEW, {})

        errors = _errors(response)
        assert errors["firing_type"] == ["Choose bisque or glaze."]
        assert errors["clay_choice"] == ["Choose the clay."]

    def it_refuses_an_other_clay_without_its_cone6_confirmation(maker_client):
        response = _post(maker_client, NEW, {"firing_type": "bisque", "clay_choice": "other", "clay_other_name": "X"})

        assert _errors(response)["clay_other_cone6"] == ["Confirm this clay can be safely fired to Cone 6."]

    def it_refuses_a_glaze_ticket_without_the_glaze_cone6_confirmation(maker_client):
        data = _glaze(ClayOptionFactory())
        del data["glaze_cone6"]

        response = _post(maker_client, NEW, data)

        assert "glaze_cone6" in _errors(response)

    def it_does_not_ask_bisque_for_the_glaze_confirmation(maker, maker_client):
        _post(maker_client, NEW, _bisque(ClayOptionFactory()))

        assert KilnTicket.objects.get(maker=maker).status == KilnTicket.Status.SUBMITTED

    def it_refuses_a_ticked_glaze_kind_left_blank(maker_client):
        data = _glaze(ClayOptionFactory(), glaze_studio="on", glaze_commercial="on", glaze_self_made="on")

        errors = _errors(_post(maker_client, NEW, data))

        assert {"studio_glazes", "commercial_glaze_name", "self_made_glaze_description"} <= set(errors)

    def it_refuses_a_file_that_is_not_a_photo(maker, maker_client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        bogus = SimpleUploadedFile("notes.jpg", b"not an image", content_type="image/jpeg")
        response = maker_client.post(NEW, {"action": "draft", "photos": [bogus]})

        assert response.status_code == 200
        assert any("not a photo" in e for e in _errors(response)["__all__"])
        assert not KilnTicket.objects.filter(maker=maker).exists()

    def it_refuses_a_bad_number_even_for_a_draft(maker_client):
        response = _post(maker_client, NEW, {"quantity": "0"}, action="draft", photos=0)

        assert "quantity" in _errors(response)

    def it_drops_answers_from_the_branch_the_maker_left(maker, maker_client):
        data = _bisque(
            ClayOptionFactory(),
            glaze_studio="on",
            studio_glazes=[str(GlazeOptionFactory().pk)],
            glaze_commercial="on",
            commercial_glaze_name="Leftover",
        )

        _post(maker_client, NEW, data)

        ticket = KilnTicket.objects.get(maker=maker)
        assert not ticket.glaze_commercial and ticket.commercial_glaze_name == ""
        assert not ticket.studio_glazes.exists()
        assert not ticket.flags.exists()

    def it_records_guild_staff_as_staff(crew, crew_client):
        _post(crew_client, NEW, _bisque(ClayOptionFactory()))

        assert KilnTicket.objects.get(maker=crew).maker_type == KilnTicket.MakerType.STAFF


def describe_make_another_like_this():
    def it_opens_a_new_draft_form_with_every_answer_but_photos_and_confirmations(maker, maker_client):
        source = KilnTicketFactory(
            maker=maker,
            status="submitted",
            firing_type="bisque",
            clay_other=True,
            clay_other_name="Standard 266",
            clay_other_cone6=True,
            walls_under_inch=False,
            quantity=4,
        )
        KilnTicketPhotoFactory(ticket=source, is_cover=True)

        response = maker_client.get(f"{NEW}?from={source.pk}")

        form = response.context["form"]
        assert response.context["copied_from"] == source
        assert response.context["photos"] == []
        assert form["clay_other_name"].value() == "Standard 266"
        assert form["quantity"].value() == 4
        assert form["walls_under_inch"].value() == "no"
        assert form["firing_type"].value() == "bisque"
        assert not form["clay_other_cone6"].value()
        assert f"Copied from ticket {source.pk}." in response.content.decode()
        assert KilnTicket.objects.filter(maker=maker).count() == 1

    def it_saves_the_copy_as_a_new_ticket(maker, maker_client):
        source = KilnTicketFactory(maker=maker, status="submitted", ready=True)

        _post(maker_client, f"{NEW}?from={source.pk}", _bisque(source.clay))

        assert KilnTicket.objects.filter(maker=maker).count() == 2

    def it_will_not_copy_someone_elses_ticket(maker_client):
        assert maker_client.get(f"{NEW}?from={KilnTicketFactory().pk}").status_code == 404

    def it_rejects_a_malformed_source(maker_client):
        assert maker_client.get(f"{NEW}?from=abc").status_code == 404


def describe_editing():
    def it_lets_the_maker_change_a_queued_ticket_and_reruns_the_flags(maker, maker_client):
        ticket = KilnTicketFactory(maker=maker, ready=True)
        ticket.submit()
        url = reverse("kiln:edit", args=[ticket.pk])

        response = _post(maker_client, url, _bisque(ticket.clay, walls_under_inch="no"), photos=0)

        assert response["Location"] == HOME
        ticket.refresh_from_db()
        assert ticket.status == KilnTicket.Status.SUBMITTED
        assert list(ticket.flags.values_list("kind", flat=True)) == [KilnFlag.Kind.THICK_WALLS]
        assert f"Ticket {ticket.pk} is updated." in maker_client.get(HOME).content.decode()

    def it_checks_a_queued_ticket_like_a_submit_even_from_the_draft_button(maker, maker_client):
        ticket = KilnTicketFactory(maker=maker, status="submitted", ready=True)

        response = _post(maker_client, reverse("kiln:edit", args=[ticket.pk]), {}, action="draft", photos=0)

        assert response.status_code == 200
        assert "firing_type" in _errors(response)

    def it_shows_the_queued_ticket_its_save_button(maker, maker_client):
        ticket = KilnTicketFactory(maker=maker, status="submitted", ready=True)

        body = maker_client.get(reverse("kiln:edit", args=[ticket.pk])).content.decode()

        assert "Save changes" in body and 'hub-btn--ghost">Save as draft' not in body

    @pytest.mark.parametrize("status", ["loaded", "fired"])
    def it_stops_edits_once_the_crew_loads_it(maker, maker_client, status):
        ticket = KilnTicketFactory(maker=maker, status=status)

        response = maker_client.get(reverse("kiln:edit", args=[ticket.pk]))

        assert response["Location"] == reverse("kiln:detail", args=[ticket.pk])

    def it_hides_other_makers_tickets(maker_client):
        other = KilnTicketFactory()

        assert maker_client.get(reverse("kiln:edit", args=[other.pk])).status_code == 404
        assert maker_client.get(reverse("kiln:detail", args=[other.pk])).status_code == 404

    def it_makes_another_photo_the_cover_without_losing_answers(maker, maker_client):
        ticket = KilnTicketFactory(maker=maker)
        KilnTicketPhotoFactory(ticket=ticket, is_cover=True)
        second = KilnTicketPhotoFactory(ticket=ticket, sort_order=1)
        url = reverse("kiln:edit", args=[ticket.pk])

        response = _post(maker_client, url, {"quantity": "5"}, action=f"cover:{second.pk}", photos=0)

        assert response["Location"] == f"{url}#kiln-photos"
        second.refresh_from_db()
        ticket.refresh_from_db()
        assert second.is_cover and ticket.quantity == 5
        assert ticket.status == KilnTicket.Status.DRAFT

    def it_removes_a_photo(maker, maker_client):
        ticket = KilnTicketFactory(maker=maker)
        photo = KilnTicketPhotoFactory(ticket=ticket, is_cover=True)

        _post(maker_client, reverse("kiln:edit", args=[ticket.pk]), {}, action=f"remove:{photo.pk}", photos=0)

        assert not ticket.photos.exists()

    def it_refuses_to_submit_once_the_last_photo_is_removed(maker, maker_client):
        ticket = KilnTicketFactory(maker=maker, status="submitted", ready=True)
        photo = ticket.photos.get()

        response = _post(
            maker_client,
            reverse("kiln:edit", args=[ticket.pk]),
            _bisque(ticket.clay),
            action=f"remove:{photo.pk}",
            photos=0,
        )

        assert response.status_code == 200
        assert ticket.photos.exists()

    def it_refuses_a_photo_button_for_a_photo_on_another_ticket(maker, maker_client):
        ticket = KilnTicketFactory(maker=maker)
        stranger = KilnTicketPhotoFactory()

        response = _post(
            maker_client, reverse("kiln:edit", args=[ticket.pk]), {}, action=f"remove:{stranger.pk}", photos=0
        )

        assert "That photo is not on this ticket." in _errors(response)["__all__"]

    def it_keeps_an_archived_clay_on_the_ticket_that_used_it(maker, maker_client, crew):
        clay = ClayOptionFactory(name="Old Clay")
        ticket = KilnTicketFactory(maker=maker, clay=clay)
        clay.archive(by=crew)

        edit = maker_client.get(reverse("kiln:edit", args=[ticket.pk])).content.decode()
        fresh = maker_client.get(NEW).content.decode()

        assert "Old Clay" in edit
        assert "Old Clay" not in fresh

    def it_keeps_an_archived_glaze_ticked_on_the_ticket_that_used_it(maker, maker_client, crew):
        glaze = GlazeOptionFactory(name="Old Glaze")
        ticket = KilnTicketFactory(maker=maker, firing_type="glaze", glaze_studio=True)
        ticket.studio_glazes.add(glaze)
        glaze.archive(by=crew)

        form = maker_client.get(reverse("kiln:edit", args=[ticket.pk])).context["form"]

        assert glaze in form.fields["studio_glazes"].queryset
        assert glaze not in maker_client.get(NEW).context["form"].fields["studio_glazes"].queryset


def describe_detail():
    def it_shows_photos_status_answers_and_the_system_fields(maker, maker_client):
        maker.phone = "503 555 0142"
        maker.save(update_fields=["phone"])
        glaze = GlazeOptionFactory(name="Plum Detail")
        ticket = KilnTicketFactory(
            maker=maker,
            firing_type="glaze",
            glaze_studio=True,
            bottom_free_of_glaze=False,
            stilts_added=True,
            glaze_cone6=True,
            clay=ClayOptionFactory(name="Detail Clay"),
        )
        ticket.studio_glazes.add(glaze)
        ticket.submit()
        photo = KilnTicketPhotoFactory(ticket=ticket, is_cover=True)
        KilnTicketPhotoFactory(ticket=ticket, sort_order=1)

        body = maker_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()

        assert photo.image.url in body
        assert "In the queue" in body
        assert maker.display_name in body and maker.primary_email in body and "503 555 0142" in body
        assert "Member" in body
        assert "Studio: Plum Detail" in body and "Detail Clay" in body
        assert "Stilts or cookies added" in body
        assert 'data-flag="glaze_on_bottom"' in body
        assert KilnFlag.MAKER_NOTES[KilnFlag.Kind.GLAZE_ON_BOTTOM] in body
        assert "Change answers" in body

    def it_shows_a_bisque_draft_and_its_unanswered_questions(maker, maker_client):
        ticket = KilnTicketFactory(maker=maker, firing_type="bisque")

        body = maker_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()

        assert "Draft Ticket" in body and "Finish and submit" in body
        assert "All walls under 1 inch" in body and "Not answered" in body
        assert "Not yet" in body

    def it_marks_a_loud_flag(maker, maker_client):
        ticket = KilnTicketFactory(maker=maker, status="submitted", firing_type="glaze", bottom_free_of_glaze=False)
        ticket.sync_automatic_flags()

        body = maker_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()

        assert "pl-kiln-flag--loud" in body

    @pytest.mark.parametrize("status", ["loaded", "fired"])
    def it_offers_no_edit_once_loaded(maker, maker_client, status):
        ticket = KilnTicketFactory(maker=maker, status=status, submitted_at="2026-10-01T10:00Z")

        body = maker_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()

        assert reverse("kiln:edit", args=[ticket.pk]) not in body
        assert "Make another like this" in body


def describe_lists():
    def it_is_for_the_crew_only(maker_client, crew_client, kiln_guild):
        assert maker_client.get(reverse("kiln:lists")).status_code == 403
        assert maker_client.post(reverse("kiln:list_add", args=["clay"]), {"name": "Sneaky"}).status_code == 403
        assert not ClayOption.objects.filter(name="Sneaky").exists()
        assert crew_client.get(reverse("kiln:lists")).status_code == 200

    def it_admits_the_guild_lead(make_member, kiln_guild):
        lead = make_member()
        kiln_guild.guild_lead = lead
        kiln_guild.save(update_fields=["guild_lead"])

        assert signed_in(lead).get(reverse("kiln:lists")).status_code == 200

    def it_lists_both_cards_with_counts(crew_client):
        clay = ClayOption.objects.get(name="G-Mix 6")
        KilnTicketFactory(clay=clay)

        response = crew_client.get(reverse("kiln:lists"))

        clay_card, glaze_card = response.context["cards"]
        assert clay_card["title"] == "Clay Bodies" and glaze_card["title"] == "Studio Glazes"
        assert next(o for o in clay_card["active"] if o.pk == clay.pk).ticket_count == 1
        assert "Other (specify)" in response.content.decode()

    def it_adds_an_option_to_the_end(crew_client):
        response = crew_client.post(reverse("kiln:list_add", args=["glaze"]), {"name": "  Shino  "})

        assert response["Location"] == reverse("kiln:lists")
        added = GlazeOption.objects.get(name="Shino")
        assert added.sort_order > GlazeOption.objects.get(name="RC-2 Oxidation Red").sort_order

    def it_adds_the_first_option_of_an_empty_list(crew_client):
        ClayOption.objects.all().delete()

        crew_client.post(reverse("kiln:list_add", args=["clay"]), {"name": "Fresh"})

        assert ClayOption.objects.get(name="Fresh").sort_order == 0

    def it_refuses_a_duplicate_or_blank_name(crew_client):
        dup = crew_client.post(reverse("kiln:list_add", args=["clay"]), {"name": "g-mix 6"}, HTTP_HX_REQUEST="true")
        blank = crew_client.post(reverse("kiln:list_add", args=["clay"]), {"name": "   "}, HTTP_HX_REQUEST="true")

        assert "g-mix 6 is already on the list." in dup.content.decode()
        assert blank.context["card"]["error"]
        assert ClayOption.objects.filter(name__iexact="g-mix 6").count() == 1

    def it_shows_a_refusal_on_the_page_without_htmx(crew_client):
        response = crew_client.post(reverse("kiln:list_add", args=["clay"]), {"name": "G-Mix 6"}, follow=True)

        assert "G-Mix 6 is already on the list." in response.content.decode()

    def it_renames_and_answers_htmx_with_the_card(crew_client):
        clay = ClayOption.objects.get(name="White Salmon")

        response = crew_client.post(
            reverse("kiln:list_rename", args=["clay", clay.pk]), {"name": "White Salmon 2"}, HTTP_HX_REQUEST="true"
        )

        clay.refresh_from_db()
        assert clay.name == "White Salmon 2"
        assert response.context["card"]["saved_pk"] == clay.pk
        assert 'id="kiln-list-clay"' in response.content.decode() and "Saved" in response.content.decode()

    def it_keeps_its_own_name_on_a_rename(crew_client):
        clay = ClayOption.objects.get(name="White Salmon")

        crew_client.post(reverse("kiln:list_rename", args=["clay", clay.pk]), {"name": "white salmon"})

        clay.refresh_from_db()
        assert clay.name == "white salmon"

    def it_refuses_a_rename_onto_another_option(crew_client):
        clay = ClayOption.objects.get(name="White Salmon")

        crew_client.post(reverse("kiln:list_rename", args=["clay", clay.pk]), {"name": "G-Mix 6"})

        clay.refresh_from_db()
        assert clay.name == "White Salmon"

    def it_archives_and_restores(crew, crew_client):
        glaze = GlazeOption.objects.get(name="Plum Wine")
        ticket = KilnTicketFactory(firing_type="glaze", glaze_studio=True)
        ticket.studio_glazes.add(glaze)

        crew_client.post(reverse("kiln:list_archive", args=["glaze", glaze.pk]))
        glaze.refresh_from_db()
        assert glaze.is_archived and glaze.archived_by == crew
        page = crew_client.get(reverse("kiln:lists")).content.decode()
        assert "Archived (1)" in page and f"by {crew.display_name}" in page
        assert "Studio: Plum Wine" in [line for line in ticket.glaze_lines()]

        crew_client.post(reverse("kiln:list_restore", args=["glaze", glaze.pk]))
        glaze.refresh_from_db()
        assert not glaze.is_archived

    def it_will_not_restore_onto_a_name_now_taken(crew, crew_client):
        old = ClayOptionFactory(name="Twin")
        old.archive(by=crew)
        ClayOptionFactory(name="Twin")

        response = crew_client.post(reverse("kiln:list_restore", args=["clay", old.pk]), HTTP_HX_REQUEST="true")

        old.refresh_from_db()
        assert old.is_archived
        assert "Twin is already on the list." in response.content.decode()

    def it_404s_an_unknown_list(crew_client):
        assert crew_client.post(reverse("kiln:list_add", args=["kilnwash"]), {"name": "X"}).status_code == 404

    def it_only_accepts_posts(crew_client):
        assert crew_client.get(reverse("kiln:list_add", args=["clay"])).status_code == 405


def describe_former_members():
    def it_turns_a_former_member_away_before_the_view(make_member):
        response = signed_in(make_member(status=Member.Status.FORMER)).get(MINE)

        assert response.status_code == 302
        assert response["Location"] == f"{reverse('account_locked')}?reason=former"
