"""Kiln ticket answers, flags, photos and the archived lists (#691)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import IntegrityError

from kiln.models import ClayOption, GlazeOption, KilnFlag, KilnTicket, KilnTicketPhoto
from tests.kiln.conftest import photo_upload
from tests.kiln.factories import (
    ClayOptionFactory,
    GlazeOptionFactory,
    KilnFlagFactory,
    KilnTicketFactory,
    KilnTicketPhotoFactory,
)
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

Kind = KilnFlag.Kind


def _glaze(**answers: object) -> KilnTicket:
    defaults: dict[str, object] = {"firing_type": KilnTicket.FiringType.GLAZE, "bottom_free_of_glaze": True}
    defaults.update(answers)
    return KilnTicketFactory(**defaults)


def _fields(ticket: KilnTicket, photos: int = 1, glazes: int = 0) -> list[str]:
    return [m.field for m in ticket.missing_for_submit(photo_count=photos, studio_glaze_count=glazes)]


def describe_seeded_lists():
    def it_offers_the_guild_clays_in_slip_order():
        names = list(ClayOption.objects.active().values_list("name", flat=True))

        assert names[:5] == ["G-Mix 6", "Trail Mix Dark Chocolate", "Kristy Lombard", "Trail Mix Toast", "White Salmon"]

    def it_offers_the_studio_glazes():
        names = set(GlazeOption.objects.active().values_list("name", flat=True))

        assert {"Perfect White", "Ritual Clear", "Plum Wine", "RC-2 Oxidation Red"} <= names
        assert len(names) >= 10


def describe_list_options():
    def it_archives_and_restores_without_deleting(make_member):
        crew = make_member()
        clay = ClayOptionFactory(name="B-Mix 5")
        ticket = KilnTicketFactory(clay=clay)

        clay.archive(by=crew)

        assert clay.is_archived
        assert clay.archived_by == crew
        assert clay not in ClayOption.objects.active()
        assert clay in ClayOption.objects.archived()
        ticket.refresh_from_db()
        assert ticket.clay_label == "B-Mix 5"

        clay.restore()

        assert not clay.is_archived
        assert clay.archived_by is None
        assert clay in ClayOption.objects.active()

    def it_refuses_two_active_options_with_one_name():
        GlazeOptionFactory(name="Shino")

        with pytest.raises(IntegrityError):
            GlazeOptionFactory(name="Shino")

    def it_names_itself():
        assert str(ClayOptionFactory(name="Porcelain")) == "Porcelain"


def describe_missing_for_submit():
    def it_needs_a_photo_a_firing_and_a_clay_on_a_blank_ticket():
        assert _fields(KilnTicketFactory(), photos=0) == ["photos", "firing_type", "clay_choice"]

    def it_accepts_a_bisque_ticket_with_a_photo_and_a_studio_clay():
        ticket = KilnTicketFactory(firing_type=KilnTicket.FiringType.BISQUE, clay=ClayOptionFactory())

        assert _fields(ticket) == []

    def it_does_not_require_the_bisque_walls_answer():
        ticket = KilnTicketFactory(firing_type=KilnTicket.FiringType.BISQUE, clay=ClayOptionFactory())

        assert ticket.walls_under_inch is None
        assert _fields(ticket) == []

    def it_needs_the_name_and_cone6_confirmation_for_an_other_clay():
        ticket = KilnTicketFactory(firing_type=KilnTicket.FiringType.BISQUE, clay_other=True)

        assert _fields(ticket) == ["clay_other_name", "clay_other_cone6"]

    def it_accepts_a_named_and_confirmed_other_clay():
        ticket = KilnTicketFactory(
            firing_type=KilnTicket.FiringType.BISQUE,
            clay_other=True,
            clay_other_name="Standard 266",
            clay_other_cone6=True,
        )

        assert _fields(ticket) == []

    def it_needs_the_glaze_cone6_confirmation_on_a_glaze_ticket():
        ticket = _glaze(clay=ClayOptionFactory())

        assert _fields(ticket) == ["glaze_cone6"]

    def it_needs_each_ticked_glaze_kind_answered():
        ticket = _glaze(
            clay=ClayOptionFactory(), glaze_studio=True, glaze_commercial=True, glaze_self_made=True, glaze_cone6=True
        )

        assert _fields(ticket) == ["studio_glazes", "commercial_glaze_name", "self_made_glaze_description"]

    def it_accepts_a_fully_answered_glaze_ticket():
        ticket = _glaze(
            clay=ClayOptionFactory(),
            glaze_studio=True,
            glaze_commercial=True,
            commercial_glaze_name="Amaco Blue Rutile",
            glaze_self_made=True,
            self_made_glaze_description="Ash glaze",
            glaze_cone6=True,
        )

        assert _fields(ticket, glazes=2) == []

    def it_lists_what_a_draft_still_needs_in_short_words():
        ticket = KilnTicketFactory()

        assert ticket.still_needs() == ["a photo", "the firing", "the clay"]

    def it_counts_saved_photos_and_glazes_for_the_draft_row():
        ticket = _glaze(clay=ClayOptionFactory(), glaze_studio=True, glaze_cone6=True)
        KilnTicketPhotoFactory(ticket=ticket, is_cover=True)
        ticket.studio_glazes.add(GlazeOptionFactory())

        assert ticket.still_needs() == []


def describe_automatic_flags():
    def it_raises_nothing_for_studio_answers():
        ticket = _glaze(clay=ClayOptionFactory(), glaze_studio=True)

        assert ticket.automatic_flag_kinds() == []

    def it_flags_an_other_clay():
        assert KilnTicketFactory(clay_other=True).automatic_flag_kinds() == [Kind.OTHER_CLAY]

    def it_flags_thick_bisque_walls():
        ticket = KilnTicketFactory(firing_type=KilnTicket.FiringType.BISQUE, walls_under_inch=False)

        assert ticket.automatic_flag_kinds() == [Kind.THICK_WALLS]

    def it_does_not_flag_thin_or_unanswered_walls():
        assert KilnTicketFactory(firing_type="bisque", walls_under_inch=True).automatic_flag_kinds() == []
        assert KilnTicketFactory(firing_type="bisque").automatic_flag_kinds() == []

    def it_flags_commercial_and_self_made_glazes():
        ticket = _glaze(glaze_commercial=True, glaze_self_made=True)

        assert ticket.automatic_flag_kinds() == [Kind.COMMERCIAL_GLAZE, Kind.SELF_MADE_GLAZE]

    def it_flags_glaze_on_the_bottom_quietly_when_stilts_are_on():
        ticket = _glaze(bottom_free_of_glaze=False, stilts_added=True)

        assert ticket.automatic_flag_kinds() == [Kind.GLAZE_ON_BOTTOM]

    @pytest.mark.parametrize("stilts", [False, None])
    def it_flags_glaze_on_the_bottom_loudly_without_stilts(stilts):
        ticket = _glaze(bottom_free_of_glaze=False, stilts_added=stilts)

        assert ticket.automatic_flag_kinds() == [Kind.GLAZE_ON_BOTTOM_NO_STILTS]

    def it_ignores_glaze_answers_on_a_bisque_ticket():
        ticket = KilnTicketFactory(firing_type="bisque", glaze_commercial=True, bottom_free_of_glaze=False)

        assert ticket.automatic_flag_kinds() == []

    def it_ignores_wall_answers_on_a_glaze_ticket():
        assert _glaze(walls_under_inch=False).automatic_flag_kinds() == []


def describe_sync_automatic_flags():
    def it_adds_the_flags_the_answers_raise():
        ticket = KilnTicketFactory(clay_other=True)

        ticket.sync_automatic_flags()

        assert list(ticket.flags.values_list("kind", flat=True)) == [Kind.OTHER_CLAY]
        assert ticket.flags.get().added_by is None

    def it_drops_stale_automatic_flags_and_keeps_a_cleared_one_that_still_applies(make_member):
        crew = make_member()
        ticket = _glaze(clay_other=True, glaze_commercial=False)
        stale = KilnFlagFactory(ticket=ticket, kind=Kind.COMMERCIAL_GLAZE)
        from django.utils import timezone

        kept = KilnFlagFactory(ticket=ticket, kind=Kind.OTHER_CLAY, cleared_at=timezone.now(), cleared_by=crew)

        ticket.sync_automatic_flags()

        assert not KilnFlag.objects.filter(pk=stale.pk).exists()
        kept.refresh_from_db()
        assert kept.cleared_by == crew
        assert ticket.flags.count() == 1

    def it_never_touches_a_flag_the_crew_added(make_member):
        ticket = KilnTicketFactory()
        manual = KilnFlagFactory(ticket=ticket, kind=Kind.MANUAL, note="Check the foot", added_by=make_member())

        ticket.sync_automatic_flags()

        assert KilnFlag.objects.filter(pk=manual.pk).exists()


def describe_submit():
    def it_puts_a_draft_in_the_queue_with_its_flags():
        ticket = KilnTicketFactory(clay_other=True)

        ticket.submit()

        ticket.refresh_from_db()
        assert ticket.status == KilnTicket.Status.SUBMITTED
        assert ticket.get_status_display() == "In the queue"
        assert ticket.submitted_at is not None
        assert ticket.flags.filter(kind=Kind.OTHER_CLAY).exists()

    def it_keeps_the_first_submitted_time_on_an_edit():
        ticket = KilnTicketFactory(clay_other=True)
        ticket.submit()
        first = ticket.submitted_at
        ticket.clay_other = False
        ticket.save()

        ticket.submit()

        ticket.refresh_from_db()
        assert ticket.submitted_at == first
        assert not ticket.flags.exists()


def describe_drop_inapplicable_answers():
    def it_clears_glaze_answers_when_the_ticket_is_bisque():
        ticket = KilnTicketFactory(
            firing_type="bisque",
            glaze_studio=True,
            glaze_commercial=True,
            commercial_glaze_name="X",
            glaze_self_made=True,
            self_made_glaze_description="Y",
            bottom_free_of_glaze=False,
            stilts_added=True,
            glaze_cone6=True,
            walls_under_inch=True,
        )

        ticket.drop_inapplicable_answers()

        assert (ticket.glaze_studio, ticket.glaze_commercial, ticket.glaze_self_made) == (False, False, False)
        assert ticket.commercial_glaze_name == ticket.self_made_glaze_description == ""
        assert ticket.bottom_free_of_glaze is None and ticket.stilts_added is None
        assert ticket.glaze_cone6 is False
        assert ticket.walls_under_inch is True
        assert not ticket.keeps_studio_glazes

    def it_clears_the_walls_answer_and_stilts_on_a_glaze_ticket_with_a_clean_bottom():
        ticket = _glaze(walls_under_inch=False, bottom_free_of_glaze=True, stilts_added=False, glaze_studio=True)

        ticket.drop_inapplicable_answers()

        assert ticket.walls_under_inch is None
        assert ticket.stilts_added is None
        assert ticket.keeps_studio_glazes

    def it_keeps_only_one_clay_answer():
        clay = ClayOptionFactory()
        listed = KilnTicketFactory(clay=clay, clay_other_name="leftover", clay_other_cone6=True)
        other = KilnTicketFactory(clay=clay, clay_other=True, clay_other_name="Mine")

        listed.drop_inapplicable_answers()
        other.drop_inapplicable_answers()

        assert listed.clay == clay and listed.clay_other_name == "" and listed.clay_other_cone6 is False
        assert other.clay is None and other.clay_other_name == "Mine"


def describe_copy_initial():
    def it_copies_every_answer_but_the_cone6_confirmations():
        glaze = GlazeOptionFactory()
        ticket = _glaze(
            clay_other=True,
            clay_other_name="Standard 266",
            clay_other_cone6=True,
            glaze_studio=True,
            glaze_commercial=True,
            commercial_glaze_name="Amaco",
            bottom_free_of_glaze=False,
            stilts_added=True,
            glaze_cone6=True,
            height_in=Decimal("4"),
            quantity=3,
        )
        ticket.studio_glazes.add(glaze)

        initial = ticket.copy_initial()

        assert initial["firing_type"] == "glaze"
        assert initial["clay_choice"] == "other"
        assert initial["clay_other_name"] == "Standard 266"
        assert initial["studio_glazes"] == [glaze.pk]
        assert initial["bottom_free_of_glaze"] == "no"
        assert initial["stilts_added"] == "yes"
        assert initial["walls_under_inch"] == ""
        assert initial["quantity"] == 3
        assert "clay_other_cone6" not in initial and "glaze_cone6" not in initial

    def it_names_a_listed_clay_and_drops_archived_glazes(make_member):
        clay = ClayOptionFactory()
        archived = GlazeOptionFactory()
        ticket = _glaze(clay=clay, glaze_studio=True)
        ticket.studio_glazes.add(archived)
        archived.archive(by=make_member())

        initial = ticket.copy_initial()

        assert initial["clay_choice"] == str(clay.pk)
        assert initial["studio_glazes"] == []

    def it_leaves_a_blank_clay_blank():
        assert KilnTicketFactory().copy_initial()["clay_choice"] == ""


def describe_labels():
    def it_counts_pieces():
        assert KilnTicketFactory().pieces_label == "1 piece"
        assert KilnTicketFactory(quantity=4).pieces_label == "4 pieces"

    def it_summarises_the_firing():
        assert KilnTicketFactory(firing_type="glaze", quantity=3).summary == "Glaze · 3 pieces"
        assert KilnTicketFactory().summary == "1 piece"

    def it_shows_a_size_only_when_complete():
        ticket = KilnTicketFactory(height_in=Decimal("4.50"), width_in=Decimal("6"), length_in=Decimal("6.00"))

        assert ticket.size_label == "4.5 x 6 x 6 in"
        assert KilnTicketFactory(height_in=Decimal("4")).size_label == ""

    def it_says_each_for_a_set():
        ticket = KilnTicketFactory(height_in=Decimal("1"), width_in=Decimal("10"), length_in=Decimal("10"), quantity=2)

        assert ticket.size_label == "1 x 10 x 10 in each"

    def it_labels_the_clay():
        assert KilnTicketFactory(clay_other=True).clay_label == "Other"
        assert KilnTicketFactory(clay_other=True, clay_other_name="Red").clay_label == "Other: Red"
        assert KilnTicketFactory().clay_label == ""

    def it_lists_glaze_lines_on_a_glaze_ticket_only():
        glaze = GlazeOptionFactory(name="Plum Spec")
        ticket = _glaze(
            glaze_studio=True,
            glaze_commercial=True,
            commercial_glaze_name="Amaco",
            glaze_self_made=True,
            self_made_glaze_description="Ash",
        )
        assert ticket.glaze_lines()[0] == "Studio"
        ticket.studio_glazes.add(glaze)

        assert ticket.glaze_lines() == ["Studio: Plum Spec", "Commercial: Amaco", "Self made or other: Ash"]
        assert KilnTicketFactory(firing_type="bisque").glaze_lines() == []

    def it_is_editable_until_loaded():
        assert KilnTicketFactory(status="draft").is_editable
        assert KilnTicketFactory(status="submitted").is_editable
        assert not KilnTicketFactory(status="loaded").is_editable
        assert not KilnTicketFactory(status="fired").is_editable

    def it_names_itself():
        ticket = KilnTicketFactory()
        photo = KilnTicketPhotoFactory(ticket=ticket)
        flag = KilnFlagFactory(ticket=ticket)

        assert str(ticket) == f"Ticket {ticket.pk}"
        assert str(photo) == f"Photo {photo.pk} of ticket {ticket.pk}"
        assert str(flag) == f"Other clay on ticket {ticket.pk}"


def describe_flag_display():
    def it_is_loud_only_without_stilts():
        assert KilnFlagFactory(kind=Kind.GLAZE_ON_BOTTOM_NO_STILTS).is_loud
        assert not KilnFlagFactory(kind=Kind.GLAZE_ON_BOTTOM).is_loud

    def it_has_a_kind_note_for_every_kind():
        for kind in Kind:
            assert KilnFlag.MAKER_NOTES[kind]
        assert KilnFlagFactory(kind=Kind.THICK_WALLS).maker_note.startswith("Some walls are 1 inch thick")
        assert KilnFlagFactory(kind=Kind.THICK_WALLS).short_label == "thick walls"

    def it_lists_open_flags_loudest_first(make_member):
        from django.utils import timezone

        ticket = KilnTicketFactory()
        quiet = KilnFlagFactory(ticket=ticket, kind=Kind.OTHER_CLAY)
        loud = KilnFlagFactory(ticket=ticket, kind=Kind.GLAZE_ON_BOTTOM_NO_STILTS)
        KilnFlagFactory(ticket=ticket, kind=Kind.THICK_WALLS, cleared_at=timezone.now(), cleared_by=make_member())

        assert ticket.open_flags() == [loud, quiet]


def describe_photos():
    def it_makes_the_first_photo_the_cover_and_resizes_a_tile():
        ticket = KilnTicketFactory()

        first = ticket.add_photo(photo_upload(size=(2400, 1800)))
        second = ticket.add_photo(photo_upload())

        assert first.is_cover and not second.is_cover
        assert first.image.width == 1600
        assert max(first.tile.width, first.tile.height) == 480
        assert ticket.cover_photo() == first

    def it_has_no_cover_without_photos():
        assert KilnTicketFactory().cover_photo() is None

    def it_has_no_cover_when_no_photo_is_marked():
        ticket = KilnTicketFactory()
        KilnTicketPhotoFactory(ticket=ticket)

        assert ticket.cover_photo() is None

    def it_moves_the_cover_and_keeps_exactly_one():
        ticket = KilnTicketFactory()
        first = KilnTicketPhotoFactory(ticket=ticket, is_cover=True)
        second = KilnTicketPhotoFactory(ticket=ticket, sort_order=1)

        ticket.set_cover(second.pk)
        ticket.set_cover(second.pk)

        assert list(ticket.photos.filter(is_cover=True)) == [second]
        first.refresh_from_db()
        assert not first.is_cover

    def it_refuses_two_covers():
        ticket = KilnTicketFactory()
        KilnTicketPhotoFactory(ticket=ticket, is_cover=True)

        with pytest.raises(IntegrityError):
            KilnTicketPhotoFactory(ticket=ticket, is_cover=True)

    def it_hands_the_cover_on_when_the_cover_is_removed():
        ticket = KilnTicketFactory()
        cover = KilnTicketPhotoFactory(ticket=ticket, is_cover=True)
        other = KilnTicketPhotoFactory(ticket=ticket, sort_order=1)

        ticket.remove_photo(cover.pk)

        other.refresh_from_db()
        assert other.is_cover
        assert not KilnTicketPhoto.objects.filter(pk=cover.pk).exists()

    def it_removes_a_plain_photo_and_the_last_photo():
        ticket = KilnTicketFactory()
        cover = KilnTicketPhotoFactory(ticket=ticket, is_cover=True)
        other = KilnTicketPhotoFactory(ticket=ticket, sort_order=1)

        ticket.remove_photo(other.pk)
        assert list(ticket.photos.all()) == [cover]
        ticket.remove_photo(cover.pk)
        assert not ticket.photos.exists()

    def it_refuses_a_photo_from_another_ticket():
        stranger = KilnTicketPhotoFactory()

        with pytest.raises(KilnTicketPhoto.DoesNotExist):
            KilnTicketFactory().set_cover(stranger.pk)


def describe_maker():
    def it_keeps_the_maker_as_a_member():
        member = MemberFactory()

        assert KilnTicketFactory(maker=member).maker == member
