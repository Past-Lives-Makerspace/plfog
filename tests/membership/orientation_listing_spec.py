"""BDD specs for the Orientations page's domain rules (#502 part 2), on the models.

Which types the page lists and which a member's own booking keeps
(``OrientationType.objects.listed_for``), why a listed type is paused
(``OrientationType.paused_message``), where Book the orientation goes
(``OrientationType.booking_link``, ``Equipment.unlocking_orientation_links``), and the
many guilds orienter labels (``Guild.orienter_name_labels_for``).
"""

from __future__ import annotations

import pytest

from membership.models import Equipment, Guild, OrientationBooking, OrientationType
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _enabled(name: str, **settings_kwargs: object) -> Guild:
    guild = GuildFactory(name=name)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True, **settings_kwargs)
    return guild


def describe_listed_for():
    def it_lists_active_types_of_enabled_visible_guilds_and_active_equipment():
        shown = OrientationTypeFactory(guild=_enabled("Listed Guild"), name="Shown")
        item = OrientationTypeFactory(equipment_owned=True, name="Item")
        OrientationTypeFactory(guild=GuildFactory(name="Unenabled Guild"), name="Off")
        OrientationTypeFactory(guild=_enabled("Retired Guild"), name="Retired", is_active=False)
        listed = list(OrientationType.objects.listed_for(None))
        assert {t.pk for t in listed} == {shown.pk, item.pk}
        assert all(t.is_listed for t in listed)

    def it_keeps_a_type_the_member_holds_a_live_booking_on_marked_unlisted():
        member = MemberFactory()
        guild = _enabled("Held Guild")
        retired = OrientationTypeFactory(guild=guild, name="Held")
        OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild, orientation_type=retired), member=member)
        retired.is_active = False
        retired.save()
        assert list(OrientationType.objects.listed_for(None)) == []
        held = list(OrientationType.objects.listed_for(member))
        assert held == [retired]
        assert held[0].is_listed is False

    def it_lets_a_cancelled_booking_go():
        member = MemberFactory()
        guild = _enabled("Cancelled Guild")
        retired = OrientationTypeFactory(guild=guild, name="Gone", is_active=False)
        OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=guild, orientation_type=retired),
            member=member,
            status=OrientationBooking.Status.CANCELLED,
        )
        assert list(OrientationType.objects.listed_for(member)) == []


def describe_active_or_held_by():
    def it_is_active_types_plus_the_retired_ones_the_member_holds():
        member = MemberFactory()
        equipment = EquipmentFactory()
        active = OrientationTypeFactory(guild=None, equipment=equipment, name="Active")
        held = OrientationTypeFactory(guild=None, equipment=equipment, name="Held")
        OrientationTypeFactory(guild=None, equipment=equipment, name="Dropped", is_active=False)
        OrientationBookingFactory(slot=OrientationSlotFactory(guild=None, orientation_type=held), member=member)
        held.is_active = False
        held.save()
        assert set(equipment.owned_orientation_types.active_or_held_by(member)) == {active, held}
        assert set(equipment.owned_orientation_types.active_or_held_by(None)) == {active}


def describe_active_or_ahead_for():
    """The guild page's narrower pin (#502 part 4): a retired type stays only while something is ahead."""

    def it_keeps_a_retired_type_only_for_a_booking_or_hold_still_ahead():
        from datetime import timedelta

        from django.utils import timezone

        member = MemberFactory()
        guild = _enabled("Ahead Guild")
        now = timezone.now()

        def retired_with(name: str, *, hours: int, **booking_fields: object) -> OrientationType:
            orientation_type = OrientationTypeFactory(guild=guild, name=name)
            starts = now + timedelta(hours=hours)
            OrientationBookingFactory(
                slot=OrientationSlotFactory(
                    guild=guild,
                    orientation_type=orientation_type,
                    starts_at=starts,
                    ends_at=starts + timedelta(hours=1),
                ),
                member=member,
                **booking_fields,
            )
            orientation_type.is_active = False
            orientation_type.save(update_fields=["is_active"])
            return orientation_type

        active = OrientationTypeFactory(guild=guild, name="Active")
        requested = retired_with("Requested ahead", hours=48)
        confirmed = retired_with("Confirmed ahead", hours=48, status=OrientationBooking.Status.CONFIRMED)
        running = retired_with("Running now", hours=0, status=OrientationBooking.Status.CONFIRMED)
        hold = retired_with("Checkout hold", hours=-48, status=OrientationBooking.Status.PENDING_PAYMENT)
        past = retired_with("Past unmarked", hours=-48, status=OrientationBooking.Status.CONFIRMED)
        done = retired_with("Completed", hours=48, status=OrientationBooking.Status.CONFIRMED)
        done.bookings.get().mark_completed()
        cancelled = retired_with("Cancelled", hours=48, status=OrientationBooking.Status.CANCELLED)

        types = OrientationType.objects.filter(guild=guild)
        assert set(types.active_or_ahead_for(member)) == {active, requested, confirmed, running, hold}
        assert set(types.active_or_ahead_for(None)) == {active}
        # The wider rule the equipment and Orientations pages share is unchanged.
        assert {past, done} <= set(types.active_or_held_by(member))
        assert cancelled not in set(types.active_or_held_by(member))


def describe_paused_message():
    def _listed(orientation_type: OrientationType) -> OrientationType:
        return OrientationType.objects.listed_for(None).get(pk=orientation_type.pk)

    def it_is_empty_for_an_open_type():
        assert _listed(OrientationTypeFactory(guild=_enabled("Open Guild"), name="Open")).paused_message == ""
        assert _listed(OrientationTypeFactory(equipment_owned=True, name="Open Item")).paused_message == ""

    def it_says_a_closed_guilds_message_or_the_standard_line():
        said = OrientationTypeFactory(guild=_enabled("Said Guild", is_closed=True, closed_message="Back in May."))
        assert _listed(said).paused_message == "Back in May."
        quiet = OrientationTypeFactory(guild=_enabled("Quiet Guild", is_closed=True))
        assert _listed(quiet).paused_message.startswith("This guild isn't taking orientation bookings")

    def it_says_closed_equipments_message_or_the_plain_line():
        said = OrientationTypeFactory(
            guild=None, equipment=EquipmentFactory(is_closed=True, closed_message="New blade."), name="Blade"
        )
        assert _listed(said).paused_message == "New blade."
        quiet = OrientationTypeFactory(guild=None, equipment=EquipmentFactory(is_closed=True), name="Quiet")
        assert _listed(quiet).paused_message == "This orientation is paused."

    def it_reads_plainly_paused_for_a_type_kept_only_by_a_booking():
        member = MemberFactory()
        guild = _enabled("Kept Guild")
        kept = OrientationTypeFactory(guild=guild, name="Kept")
        OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild, orientation_type=kept), member=member)
        kept.is_active = False
        kept.save()
        row = OrientationType.objects.listed_for(member).get(pk=kept.pk)
        assert row.paused_message == "This orientation is paused."


def describe_booking_link():
    def it_sends_a_listed_type_to_its_card_and_an_unlisted_one_to_its_owner_page():
        orientation_type = OrientationTypeFactory(name="Linked")
        assert orientation_type.booking_link(listed=True) == orientation_type.orientations_page_path()
        assert orientation_type.booking_link(listed=False) == orientation_type.orientation_anchor_path()

    def it_is_decided_for_a_whole_grid_in_its_own_read(django_assert_num_queries):
        listed = OrientationTypeFactory(guild=_enabled("Gate Guild"), name="Listed Gate")
        hidden = OrientationTypeFactory(guild=GuildFactory(name="Hidden Gate Guild"), name="Hidden Gate")
        EquipmentFactory(name="Listed Tool", unlocking_orientations=[listed])
        EquipmentFactory(name="Hidden Tool", unlocking_orientations=[hidden])
        EquipmentFactory(name="Either Tool", unlocking_orientations=[listed, hidden])
        # The site configuration (demo guild switch), the grid, then one prefetch for every orientation.
        with django_assert_num_queries(3):
            links = {
                equipment.name: [link for _orientation, link in equipment.unlocking_orientation_links]
                for equipment in Equipment.objects.filter(name__endswith=" Tool").with_unlocking_orientations_listed()
            }
        assert links == {
            "Listed Tool": [listed.orientations_page_path()],
            "Hidden Tool": [hidden.orientation_anchor_path()],
            "Either Tool": [hidden.orientation_anchor_path(), listed.orientations_page_path()],
        }


def describe_orienter_name_labels_for():
    def it_matches_each_guilds_own_labels_in_two_queries(django_assert_num_queries):
        first = GuildFactory(name="Label Guild One", guild_lead=MemberFactory(preferred_name="Bob Placeholder"))
        GuildStaffMembershipFactory(guild=first, member=MemberFactory(preferred_name="Bob Quill"))
        second = GuildFactory(name="Label Guild Two")
        GuildStaffMembershipFactory(guild=second, member=MemberFactory(preferred_name="Amber Lane"))
        GuildStaffMembershipFactory(guild=second, member=MemberFactory(preferred_name="  "))
        lonely = GuildFactory(name="Label Guild Three")
        guilds = [first, second, lonely]
        with django_assert_num_queries(2):
            labels = Guild.orienter_name_labels_for(guilds)
        assert labels == {guild.pk: guild.orienter_name_labels() for guild in guilds}
        assert sorted(labels[first.pk].values()) == ["Bob P.", "Bob Q."]
        assert labels[lonely.pk] == {}

    def it_answers_nothing_for_no_guilds(django_assert_num_queries):
        with django_assert_num_queries(0):
            assert Guild.orienter_name_labels_for([]) == {}
