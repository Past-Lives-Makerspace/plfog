"""BDD specs for the equipment usage agreement on the model (#734).

The version fingerprint, the acceptance record, the gate's place after the orientation
in ``booking_blockers`` and ``access_state``, and the reserve engine refusing a member
who has not agreed to the current version.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from django.utils import timezone

from membership import equipment as equipment_service
from membership.models import (
    Equipment,
    EquipmentAgreementAcceptance,
    EquipmentError,
    EquipmentReservation,
    Member,
    OrientationType,
    WikiPage,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    MemberFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
    WikiPageFactory,
)

pytestmark = pytest.mark.django_db

LINK = "https://docs.google.com/document/d/cnc-rules/edit"
TEXT = "Wear eye protection.\nNever leave the CNC running alone."


def _day():
    return timezone.localdate() + timedelta(days=2)


def _at(day, hour: int):
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


def _active_member() -> Member:
    return MemberFactory(status=Member.Status.ACTIVE)


def _complete(member: Member, orientation_type: OrientationType) -> None:
    slot = OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type)
    OrientationBookingFactory(member=member, slot=slot, is_completed=True)


def _agree(equipment: Equipment, member: Member) -> EquipmentAgreementAcceptance:
    acceptance, _created = equipment.record_agreement(
        member, fingerprint=equipment.usage_agreement_fingerprint, ip_address="203.0.113.9", user_agent="Firefox"
    )
    return acceptance


def describe_has_usage_agreement():
    def it_is_off_with_neither_link_nor_text():
        assert not EquipmentFactory().has_usage_agreement

    def it_is_off_for_whitespace_only():
        assert not EquipmentFactory(usage_agreement_url=" ", usage_agreement_text="  \r\n ").has_usage_agreement

    def it_is_on_with_only_a_link():
        assert EquipmentFactory(usage_agreement_url=LINK).has_usage_agreement

    def it_is_on_with_only_text():
        assert EquipmentFactory(usage_agreement_text=TEXT).has_usage_agreement


def describe_usage_agreement_fingerprint():
    def it_is_blank_without_an_agreement():
        assert EquipmentFactory().usage_agreement_fingerprint == ""

    def it_is_a_sha256_of_the_link_and_text():
        fingerprint = EquipmentFactory(usage_agreement_url=LINK, usage_agreement_text=TEXT).usage_agreement_fingerprint
        assert len(fingerprint) == 64
        assert int(fingerprint, 16) >= 0

    def it_ignores_line_ending_style_and_outer_whitespace():
        plain = EquipmentFactory(usage_agreement_url=LINK, usage_agreement_text=TEXT)
        from_a_browser = EquipmentFactory(
            usage_agreement_url=f"  {LINK} ", usage_agreement_text="\r\n" + TEXT.replace("\n", "\r\n") + "\r\n"
        )
        old_mac = EquipmentFactory(usage_agreement_url=LINK, usage_agreement_text=TEXT.replace("\n", "\r"))
        assert plain.usage_agreement_fingerprint == from_a_browser.usage_agreement_fingerprint
        assert plain.usage_agreement_fingerprint == old_mac.usage_agreement_fingerprint

    def it_changes_when_the_link_changes():
        equipment = EquipmentFactory(usage_agreement_url=LINK, usage_agreement_text=TEXT)
        before = equipment.usage_agreement_fingerprint
        equipment.usage_agreement_url = LINK + "?v=2"
        assert equipment.usage_agreement_fingerprint != before

    def it_changes_when_the_text_changes_even_by_one_character():
        equipment = EquipmentFactory(usage_agreement_url=LINK, usage_agreement_text=TEXT)
        before = equipment.usage_agreement_fingerprint
        equipment.usage_agreement_text = TEXT + "!"
        assert equipment.usage_agreement_fingerprint != before

    def it_tells_a_link_apart_from_the_same_words_as_text():
        assert (
            EquipmentFactory(usage_agreement_url=LINK).usage_agreement_fingerprint
            != EquipmentFactory(usage_agreement_text=LINK).usage_agreement_fingerprint
        )


def describe_record_agreement():
    def it_records_the_version_the_link_the_text_the_ip_and_the_browser():
        equipment = EquipmentFactory(usage_agreement_url=f" {LINK} ", usage_agreement_text=TEXT + "\r\n")
        member = _active_member()
        acceptance, created = equipment.record_agreement(
            member, fingerprint=equipment.usage_agreement_fingerprint, ip_address="203.0.113.9", user_agent="Firefox"
        )
        assert created
        acceptance.refresh_from_db()
        assert acceptance.member == member
        assert acceptance.equipment == equipment
        assert acceptance.fingerprint == equipment.usage_agreement_fingerprint
        assert acceptance.agreement_url == LINK
        assert acceptance.agreement_text == TEXT
        assert acceptance.ip_address == "203.0.113.9"
        assert acceptance.user_agent == "Firefox"
        assert acceptance.accepted_at is not None

    def it_records_once_on_a_double_submit():
        equipment = EquipmentFactory(usage_agreement_text=TEXT)
        member = _active_member()
        first = _agree(equipment, member)
        again, created = equipment.record_agreement(
            member, fingerprint=equipment.usage_agreement_fingerprint, ip_address="198.51.100.1", user_agent="Other"
        )
        assert not created
        assert again.pk == first.pk
        assert again.ip_address == "203.0.113.9"
        assert EquipmentAgreementAcceptance.objects.count() == 1

    def it_refuses_a_stale_version_and_records_nothing():
        equipment = EquipmentFactory(usage_agreement_text=TEXT)
        member = _active_member()
        with pytest.raises(EquipmentError, match="changed while you were reading it"):
            equipment.record_agreement(member, fingerprint="0" * 64, ip_address=None, user_agent="")
        assert not EquipmentAgreementAcceptance.objects.exists()

    def it_refuses_equipment_with_no_agreement():
        equipment = EquipmentFactory()
        with pytest.raises(EquipmentError, match="no usage agreement"):
            equipment.record_agreement(_active_member(), fingerprint="", ip_address=None, user_agent="")

    def it_stores_a_blank_ip_as_none_and_truncates_a_long_user_agent():
        equipment = EquipmentFactory(usage_agreement_text=TEXT)
        acceptance, _created = equipment.record_agreement(
            _active_member(), fingerprint=equipment.usage_agreement_fingerprint, ip_address="", user_agent="x" * 1500
        )
        acceptance.refresh_from_db()
        assert acceptance.ip_address is None
        assert len(acceptance.user_agent) == 1000

    def it_names_the_member_and_equipment():
        equipment = EquipmentFactory(name="CNC Router", usage_agreement_text=TEXT)
        member = MemberFactory(status=Member.Status.ACTIVE, preferred_name="Sam", full_legal_name="Sam Rivera")
        acceptance = _agree(equipment, member)
        assert str(acceptance).startswith(f"{member.display_name} agreed to the CNC Router usage agreement on ")


def describe_has_current_agreement_from():
    def it_holds_with_no_agreement_without_a_query(django_assert_num_queries):
        equipment = EquipmentFactory()
        member = _active_member()
        with django_assert_num_queries(0):
            assert equipment.has_current_agreement_from(member)

    def it_holds_only_for_the_current_version():
        equipment = EquipmentFactory(usage_agreement_text=TEXT)
        member = _active_member()
        assert not equipment.has_current_agreement_from(member)
        _agree(equipment, member)
        assert equipment.has_current_agreement_from(member)
        equipment.usage_agreement_text = TEXT + " Updated."
        equipment.save()
        assert not equipment.has_current_agreement_from(member)

    def it_reads_the_bulk_set_without_a_query(django_assert_num_queries):
        equipment = EquipmentFactory(usage_agreement_text=TEXT)
        member = _active_member()
        _agree(equipment, member)
        accepted = EquipmentAgreementAcceptance.objects.pairs_for(member)
        assert accepted == {(equipment.pk, equipment.usage_agreement_fingerprint)}
        with django_assert_num_queries(0):
            assert equipment.has_current_agreement_from(member, accepted=accepted)
            assert not equipment.has_current_agreement_from(member, accepted=set())

    def it_does_not_count_another_members_acceptance():
        equipment = EquipmentFactory(usage_agreement_text=TEXT)
        _agree(equipment, _active_member())
        assert not equipment.has_current_agreement_from(_active_member())


def describe_awaits_agreement_from():
    def it_is_false_with_no_agreement():
        assert not EquipmentFactory().awaits_agreement_from(_active_member())

    def it_is_false_for_no_member_or_an_inactive_one():
        equipment = EquipmentFactory(usage_agreement_text=TEXT)
        assert not equipment.awaits_agreement_from(None)
        assert not equipment.awaits_agreement_from(MemberFactory(status=Member.Status.FORMER))

    def it_is_false_before_the_orientation():
        equipment = EquipmentFactory(usage_agreement_text=TEXT, unlocking_orientations=[OrientationTypeFactory()])
        assert not equipment.awaits_agreement_from(_active_member())

    def it_is_true_once_oriented_until_they_agree():
        lathe = OrientationTypeFactory(name="Lathe")
        equipment = EquipmentFactory(usage_agreement_text=TEXT, unlocking_orientations=[lathe])
        member = _active_member()
        _complete(member, lathe)
        assert equipment.awaits_agreement_from(member)
        _agree(equipment, member)
        assert not equipment.awaits_agreement_from(member)


def describe_current_agreement_member_count():
    def it_is_zero_with_no_agreement():
        assert EquipmentFactory().current_agreement_member_count() == 0

    def it_counts_only_acceptances_of_the_current_version():
        equipment = EquipmentFactory(usage_agreement_text=TEXT)
        _agree(equipment, _active_member())
        equipment.usage_agreement_text = "Version two."
        equipment.save()
        _agree(equipment, _active_member())
        _agree(equipment, _active_member())
        assert equipment.current_agreement_member_count() == 2


def describe_the_gate():
    def describe_with_no_agreement():
        def it_leaves_blockers_and_access_state_as_they_were():
            equipment = EquipmentFactory()
            member = _active_member()
            assert equipment.booking_blockers(member) == []
            assert equipment.access_state(member, has_unpaid_fee=False) == Equipment.AccessState.OK

    def describe_with_an_agreement():
        def it_blocks_an_oriented_member_who_has_not_agreed():
            lathe = OrientationTypeFactory(name="Lathe")
            equipment = EquipmentFactory(usage_agreement_url=LINK, unlocking_orientations=[lathe])
            member = _active_member()
            _complete(member, lathe)
            assert equipment.booking_blockers(member) == [
                "Agree to the usage agreement before you reserve this equipment."
            ]
            assert equipment.access_state(member, has_unpaid_fee=False) == Equipment.AccessState.NEEDS_AGREEMENT

        def it_asks_for_the_orientation_first():
            lathe = OrientationTypeFactory(name="Lathe")
            equipment = EquipmentFactory(usage_agreement_url=LINK, unlocking_orientations=[lathe])
            member = _active_member()
            assert equipment.booking_blockers(member) == [
                "You need the Lathe orientation before you can reserve this equipment."
            ]
            assert equipment.access_state(member, has_unpaid_fee=False) == Equipment.AccessState.NEEDS_ORIENTATION

        def it_keeps_retired_and_inactive_ahead_of_it():
            equipment = EquipmentFactory(usage_agreement_url=LINK, is_active=False)
            assert equipment.booking_blockers(_active_member()) == [
                "This equipment is retired and not taking reservations."
            ]
            open_equipment = EquipmentFactory(usage_agreement_url=LINK)
            former = MemberFactory(status=Member.Status.FORMER)
            assert open_equipment.booking_blockers(former) == [
                "Your membership needs to be active to reserve equipment."
            ]
            assert open_equipment.access_state(former) == Equipment.AccessState.INACTIVE_MEMBER

        def it_lists_closure_after_it():
            equipment = EquipmentFactory(usage_agreement_url=LINK, is_closed=True, closed_message="Down for repairs.")
            assert equipment.booking_blockers(_active_member()) == [
                "Agree to the usage agreement before you reserve this equipment.",
                "Down for repairs.",
            ]

        def it_clears_once_they_agree():
            equipment = EquipmentFactory(usage_agreement_text=TEXT)
            member = _active_member()
            _agree(equipment, member)
            assert equipment.booking_blockers(member) == []
            assert equipment.access_state(member, has_unpaid_fee=False) == Equipment.AccessState.OK

        def it_reads_the_bulk_set_on_the_access_state():
            equipment = EquipmentFactory(usage_agreement_text=TEXT)
            member = _active_member()
            assert (
                equipment.access_state(member, has_unpaid_fee=False, accepted_agreements=set())
                == Equipment.AccessState.NEEDS_AGREEMENT
            )
            accepted = {(equipment.pk, equipment.usage_agreement_fingerprint)}
            assert (
                equipment.access_state(member, has_unpaid_fee=False, accepted_agreements=accepted)
                == Equipment.AccessState.OK
            )


def describe_reserve_engine():
    def _open(**kwargs) -> Equipment:
        equipment = EquipmentFactory(**kwargs)
        EquipmentHoursFactory(
            equipment=equipment, weekday=_day().weekday(), start_time=time(9, 0), end_time=time(17, 0)
        )
        return equipment

    def it_refuses_a_member_who_has_not_agreed():
        equipment = _open(usage_agreement_text=TEXT)
        member = _active_member()
        with pytest.raises(EquipmentError, match="Agree to the usage agreement"):
            equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        assert not EquipmentReservation.objects.exists()

    def it_reserves_after_they_agree_and_does_not_ask_again():
        equipment = _open(usage_agreement_text=TEXT)
        member = _active_member()
        _agree(equipment, member)
        equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        equipment_service.reserve(equipment, member, _at(_day(), 12), 60)
        assert EquipmentReservation.objects.filter(member=member).count() == 2
        assert EquipmentAgreementAcceptance.objects.filter(member=member).count() == 1

    def it_refuses_again_after_the_agreement_changes():
        equipment = _open(usage_agreement_url=LINK)
        member = _active_member()
        _agree(equipment, member)
        equipment.usage_agreement_url = LINK + "?rev=2"
        equipment.save()
        with pytest.raises(EquipmentError, match="Agree to the usage agreement"):
            equipment_service.reserve(equipment, member, _at(_day(), 10), 60)


def describe_wiki_official_block():
    def it_tells_an_oriented_member_to_agree():
        equipment = EquipmentFactory(usage_agreement_text=TEXT)
        page = WikiPageFactory(kind=WikiPage.Kind.MACHINE, equipment=equipment)
        block = page.official_block_context(_active_member())
        assert block is not None
        assert block["access_state"] == Equipment.AccessState.NEEDS_AGREEMENT
        assert block["access_line"] == "Agree to the usage agreement on its equipment page before you reserve it."
