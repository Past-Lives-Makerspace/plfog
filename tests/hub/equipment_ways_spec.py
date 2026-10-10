"""Ways to Qualify (#747): the editor on Manage > Details and Add Equipment, the member's banner and the cards.

The CNC shape runs through it: way 1 is the one 6 hour orientation, way 2 is both 3 hour
sessions. Model rules (the predicate, the sentence, the progress line) are specced in
``tests/membership/equipment_spec.py``; the browser side of the editor in
``tests/e2e/equipment_ways_editor_spec.py``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from hub.forms import EquipmentForm, EquipmentWayForm
from membership.equipment import reserve
from membership.models import Equipment, EquipmentError, Member, OrientationBooking, OrientationType
from tests.hub.equipment_ways import ways_data
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationRecordFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

SENTENCE = "Complete the CNC Machine Orientation, or both Session 1 of 2 and Session 2 of 2, before you reserve the CNC Machine."


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = Member.Status.ACTIVE
    member.full_legal_name = username.title()
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def _cnc(*, ways: bool = True) -> tuple[Equipment, OrientationType, OrientationType, OrientationType]:
    guild = GuildFactory(name="Ways Woodshop")
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    full = OrientationTypeFactory(guild=guild, name="CNC Machine Orientation", duration_minutes=360)
    first = OrientationTypeFactory(guild=guild, name="Session 1 of 2", duration_minutes=180)
    second = OrientationTypeFactory(guild=guild, name="Session 2 of 2", duration_minutes=180)
    cnc = EquipmentFactory(name="CNC Machine", guild=guild)
    if ways:
        cnc.set_unlocking_ways([[full], [first, second]])
    return cnc, full, first, second


def _manager(client: Client, username: str, equipment: Equipment) -> User:
    user = _login(client, username)
    EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
    return user


def _details(equipment: Equipment, **extra: Any) -> dict[str, Any]:
    return {"name": equipment.name, "kind": "tool", "guild": str(equipment.guild_id), "is_active": "on", **extra}


def _save(client: Client, equipment: Equipment, data: dict[str, Any]) -> Any:
    return client.post(reverse("hub_equipment_details_save", args=[equipment.slug]), data)


def _manage(client: Client, equipment: Equipment) -> Any:
    return client.get(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=details")


def describe_the_editor():
    def it_saves_a_pair_and_a_single_and_reloads_showing_both(client: Client):
        cnc, full, first, second = _cnc(ways=False)
        _manager(client, "ways_save", cnc)
        response = _save(client, cnc, _details(cnc, **ways_data([first, second], [full])))
        assert response.status_code == 302
        assert response["Location"].endswith("?tab=details")
        assert cnc.unlocking_ways() == [[first, second], [full]]
        response = _manage(client, cnc)
        form = response.context["form"]
        assert [(way.way_number, way.is_saved, way.summary()) for way in form.ways_formset] == [
            (1, True, "Both Session 1 of 2 and Session 2 of 2"),
            (2, True, "CNC Machine Orientation"),
        ]
        content = response.content.decode()
        assert '<span class="pl-equip-way__summary" data-way-summary>Both Session 1 of 2 and Session 2 of 2</span>' in (
            content
        )
        assert content.count("data-way-delete") == 2
        assert "A member can reserve the CNC Machine once they finish every orientation in any one way." in content
        assert "+ Add Another Way" in content

    def it_deletes_a_saved_way_and_keeps_every_other_edit(client: Client):
        cnc, full, first, second = _cnc()
        _manager(client, "ways_delete", cnc)
        data = _details(cnc, **ways_data([full], [first, second], saved=2, delete={0}))
        data["name"] = "CNC Router"
        assert _save(client, cnc, data).status_code == 302
        cnc.refresh_from_db()
        assert cnc.name == "CNC Router"
        assert cnc.unlocking_ways() == [[first, second]]

    def it_skips_a_way_with_nothing_ticked(client: Client):
        cnc, full, _first, _second = _cnc(ways=False)
        _manager(client, "ways_empty", cnc)
        assert _save(client, cnc, _details(cnc, **ways_data([], [full], []))).status_code == 302
        assert cnc.unlocking_ways() == [[full]]

    def it_refuses_two_identical_ways_and_saves_nothing(client: Client):
        cnc, full, first, second = _cnc()
        _manager(client, "ways_same", cnc)
        data = _details(cnc, **ways_data([full], [first, second], [second, first], saved=2))
        data["name"] = "Renamed"
        response = _save(client, cnc, data)
        assert response.status_code == 200
        assert response.context["form"].ways_formset.non_form_errors() == [
            "Way 3 is the same as Way 2. Delete one of them."
        ]
        assert "Way 3 is the same as Way 2. Delete one of them." in response.content.decode()
        cnc.refresh_from_db()
        assert cnc.name == "CNC Machine"
        assert cnc.unlocking_ways() == [[full], [first, second]]

    def it_numbers_the_ways_as_shown_when_one_is_being_deleted(client: Client):
        cnc, full, first, second = _cnc()
        _manager(client, "ways_numbered", cnc)
        response = _save(
            client, cnc, _details(cnc, **ways_data([full], [first, second], [first, second], saved=2, delete={0}))
        )
        assert response.status_code == 200
        form = response.context["form"]
        assert form.ways_formset.non_form_errors() == ["Way 2 is the same as Way 1. Delete one of them."]
        assert [way.way_number for way in form.ways_formset] == [0, 1, 2]
        assert form.ways_count == 2
        # The way being deleted stays in the form, hidden, so the next Save still deletes it.
        assert 'data-way-index="0" hidden' in response.content.decode()
        assert cnc.unlocking_ways() == [[full], [first, second]]

    def it_holds_an_orientation_to_one_way(client: Client):
        cnc, full, first, _second = _cnc(ways=False)
        _manager(client, "ways_once", cnc)
        response = _save(client, cnc, _details(cnc, **ways_data([full], [full, first])))
        assert response.status_code == 200
        assert response.context["form"].ways_formset.non_form_errors() == [
            "CNC Machine Orientation is in Way 1 and Way 2. An orientation can be in one way only."
        ]
        assert cnc.unlocking_ways() == []

    def it_makes_a_new_orientation_its_own_way_in_one_save(client: Client):
        cnc, full, first, second = _cnc()
        _manager(client, "ways_new", cnc)
        data = _details(
            cnc,
            **ways_data([full], [first, second], saved=2),
            **{
                EquipmentForm.NEW_TYPE_FIELD: "1",
                "new_type-name": "CNC Team Orientation",
                "new_type-duration_minutes": "120",
                "new_type-default_seats": "2",
                "new_type-price": "",
                "new_type-default_location": "",
            },
        )
        assert _save(client, cnc, data).status_code == 302
        own = cnc.owned_orientation_types.get()
        assert (own.name, own.guild, own.is_active) == ("CNC Team Orientation", None, True)
        assert cnc.unlocking_ways() == [[full], [first, second], [own]]

    def it_adds_the_new_orientation_after_the_saved_ways_when_the_post_has_no_ways(client: Client):
        cnc, full, first, second = _cnc()
        form = EquipmentForm(
            _details(
                cnc,
                **{
                    EquipmentForm.NEW_TYPE_FIELD: "1",
                    "new_type-name": "CNC Team Orientation",
                    "new_type-duration_minutes": "120",
                    "new_type-default_seats": "2",
                    "new_type-price": "",
                    "new_type-default_location": "",
                },
            ),
            instance=cnc,
        )
        assert form.is_valid() is True
        form.save()
        assert cnc.unlocking_ways() == [[full], [first, second], [cnc.owned_orientation_types.get()]]

    def it_keeps_a_way_whose_ticks_match_a_saved_way_at_the_same_index(client: Client):
        # INITIAL_FORMS lower than the saved count: each posted way equals the saved way's initial
        # at its index, which Django's unchanged extra form shortcut would read as empty.
        cnc, full, first, second = _cnc()
        _manager(client, "ways_initial_low", cnc)
        assert _save(client, cnc, _details(cnc, **ways_data([full], [first, second], saved=0))).status_code == 302
        assert cnc.unlocking_ways() == [[full], [first, second]]

    def it_shows_only_the_posted_guilds_types_when_a_save_comes_back(client: Client):
        cnc, full, first, _second = _cnc()
        elsewhere = OrientationTypeFactory(guild=GuildFactory(name="Ceramics"), name="Wheel")
        unpicked = OrientationTypeFactory(guild=GuildFactory(name="Metals"), name="Forge")
        _manager(client, "ways_bound_narrow", cnc)
        data = _details(cnc, **ways_data([full], [first, elsewhere], saved=2))
        response = _save(client, cnc, data)
        assert response.status_code == 200
        form = response.context["form"]
        assert form.ways_formset.non_form_errors() == [
            "Pick orientations offered by the chosen guild, or this equipment's own orientations."
        ]
        empty_names = [pill["name"] for pill in form.ways_formset.empty_form.pills()]
        assert empty_names == ["CNC Machine Orientation", "Session 1 of 2", "Session 2 of 2"]
        # The refused pick stays in view, ticked, beside its error; an unticked foreign type does not show.
        second_way = [pill["name"] for pill in form.ways_formset.forms[1].pills()]
        assert "Wheel" in second_way
        assert unpicked.name not in second_way
        assert unpicked.name not in response.content.decode()

    def it_validates_against_every_guilds_types_when_the_guild_changes(client: Client):
        cnc, *_types = _cnc()
        ceramics = GuildFactory(name="Ceramics")
        wheel = OrientationTypeFactory(guild=ceramics, name="Wheel")
        _manager(client, "ways_bound_rehome", cnc)
        data = _details(cnc, **ways_data([wheel]))
        data["guild"] = str(ceramics.pk)
        assert _save(client, cnc, data).status_code == 302
        assert cnc.unlocking_ways() == [[wheel]]

    def it_leaves_the_ways_alone_when_the_post_carries_none(client: Client):
        cnc, full, first, second = _cnc()
        _manager(client, "ways_absent", cnc)
        data = _details(cnc)
        data["name"] = "CNC Router"
        assert _save(client, cnc, data).status_code == 302
        assert cnc.unlocking_ways() == [[full], [first, second]]

    def describe_the_empty_state():
        def it_invites_a_first_way_on_add(client: Client):
            _login(client, "ways_add_empty", fog_role=Member.FogRole.ADMIN)
            OrientationTypeFactory(name="Lathe Basics")
            content = client.get(reverse("hub_equipment_add")).content.decode()
            assert (
                "No orientation is needed. Any active member can reserve this equipment. Add a way to require one."
            ) in content
            assert "+ Add a Way to Qualify" in content
            assert "data-ways-add" in content
            assert "css/equipment-manage" in content

        def it_points_at_new_orientation_when_there_is_nothing_to_pick(client: Client):
            _login(client, "ways_add_nothing", fog_role=Member.FogRole.ADMIN)
            content = client.get(reverse("hub_equipment_add")).content.decode()
            assert "Any active member can reserve this equipment. Create its orientation with + New Orientation." in (
                content
            )
            assert "data-ways-add" not in content

        def it_names_the_item_on_its_details_tab(client: Client):
            cnc, *_types = _cnc(ways=False)
            _manager(client, "ways_none", cnc)
            content = _manage(client, cnc).content.decode()
            assert "Any active member can reserve the CNC Machine. Add a way to require one." in content

    def describe_a_way_card():
        def _empty_way(equipment: Equipment | None = None) -> EquipmentWayForm:
            form = EquipmentForm(instance=equipment) if equipment is not None else EquipmentForm()
            return form.ways_formset.empty_form

        def it_summarizes_nothing_one_two_and_three():
            cnc, full, first, second = _cnc()
            team = OrientationTypeFactory(guild=cnc.guild, name="Team")
            assert _empty_way(cnc).summary() == EquipmentWayForm.NOTHING_TICKED
            form = EquipmentForm(instance=cnc)
            assert [way.summary() for way in form.ways_formset] == [
                "CNC Machine Orientation",
                "Both Session 1 of 2 and Session 2 of 2",
            ]
            cnc.set_unlocking_ways([[first, second, team]])
            assert EquipmentForm(instance=cnc).ways_formset.forms[0].summary() == (
                "All of Session 1 of 2, Session 2 of 2 and Team"
            )
            assert full.name not in EquipmentForm(instance=cnc).ways_formset.forms[0].summary()

        def it_names_the_owner_only_for_another_guilds_type():
            cnc, *_types = _cnc()
            own = OrientationTypeFactory(equipment_owned=True, equipment=cnc, name="Own Basics", duration_minutes=45)
            pills = {pill["name"]: pill["meta"] for pill in _empty_way(cnc).pills()}
            assert pills["Own Basics"] == "45 minutes"
            assert pills["CNC Machine Orientation"] == "6 hours"
            assert own.name in pills
            other = OrientationTypeFactory(guild=GuildFactory(name="Ceramics"), name="Wheel", duration_minutes=60)
            standalone = EquipmentFactory(name="House Kiln")
            pills = {pill["name"]: pill["meta"] for pill in _empty_way(standalone).pills()}
            assert pills[other.name] == "1 hour · Ceramics"

        def it_draws_the_cloned_way_with_remove_and_saved_ways_with_delete(client: Client):
            cnc, *_types = _cnc()
            _manager(client, "ways_buttons", cnc)
            content = _manage(client, cnc).content.decode()
            template = content[content.index('<template x-ref="template">') : content.index("</template>")]
            assert 'name="ways-__prefix__-orientations"' in template
            assert "data-way-remove" in template
            assert "data-way-delete" not in template
            assert '<input type="hidden" name="ways-TOTAL_FORMS" value="2" id="id_ways-TOTAL_FORMS">' in content
            assert '<input type="hidden" name="ways-INITIAL_FORMS" value="2" id="id_ways-INITIAL_FORMS">' in content

    def it_refuses_a_deferred_save():
        cnc, full, _first, _second = _cnc(ways=False)
        form = EquipmentForm(_details(cnc, **ways_data([full])), instance=cnc)
        assert form.is_valid() is True
        with pytest.raises(ValueError, match="commit=False"):
            form.save(commit=False)
        assert cnc.unlocking_ways() == []


def describe_the_member_banner():
    def _page(client: Client, equipment: Equipment) -> Any:
        return client.get(reverse("hub_equipment_detail", args=[equipment.slug]))

    def it_lets_a_member_with_either_way_reserve(client: Client):
        cnc, full, first, second = _cnc()
        user = _login(client, "ways_member_full")
        OrientationRecordFactory(member=user.member, orientation_type=full)
        assert _page(client, cnc).context["access_state"] == Equipment.AccessState.OK
        sessions = _login(Client(), "ways_member_sessions").member
        OrientationRecordFactory(member=sessions, orientation_type=first)
        OrientationRecordFactory(member=sessions, orientation_type=second)
        assert cnc.booking_blockers(sessions) == []

    def it_shows_the_sentence_and_each_way_to_a_member_halfway_there(client: Client):
        cnc, full, first, second = _cnc()
        user = _login(client, "ways_member_half")
        OrientationRecordFactory(member=user.member, orientation_type=first)
        response = _page(client, cnc)
        assert response.context["orientation_requirement_sentence"] == SENTENCE
        content = response.content.decode()
        assert (
            f'<p class="pl-equip-unlock-lead">{SENTENCE} Trained on this before the Member Portal went live?' in content
        )
        assert "Any one of these orientations unlocks this." not in content
        assert content.count('class="pl-equip-unlock-way"') == 2
        assert content.count('<p class="pl-equip-unlock-or" aria-hidden="true">or</p>') == 1
        done = content[content.index(f'data-unlock-type="{first.pk}"') :]
        assert done.index("data-unlock-done") < done.index("</div>")
        for orientation_type in (full, second):
            row = content[content.index(f'data-unlock-type="{orientation_type.pk}"') :].split("</div>", 1)[0]
            assert (
                f'href="{escape(orientation_type.orientation_anchor_path())}" class="pl-btn pl-btn--primary pl-btn--sm">Book'
                in row
            )
        assert "· 6 hours" in content
        assert "css/equipment-detail" in content

    def it_refuses_the_reservation_with_the_sentence(client: Client):
        cnc, _full, first, _second = _cnc()
        member = _login(client, "ways_member_reserve").member
        OrientationRecordFactory(member=member, orientation_type=first)
        with pytest.raises(EquipmentError, match="Complete the CNC Machine Orientation, or both Session 1 of 2"):
            reserve(cnc, member, timezone.now() + timedelta(days=1), 60)
        assert not cnc.reservations.exists()

    def it_keeps_each_orientations_booking_states(client: Client):
        cnc, full, first, second = _cnc()
        user = _login(client, "ways_member_states")
        member = user.member
        OrientationRecordFactory(member=member, orientation_type=first)
        slot = OrientationSlotFactory(
            guild=second.guild, orientation_type=second, starts_at=timezone.now() + timedelta(days=3)
        )
        OrientationBookingFactory(member=member, slot=slot, status=OrientationBooking.Status.CONFIRMED)
        full.is_active = False
        full.save()
        content = _page(client, cnc).content.decode()
        assert "Bookings are paused. Check back soon." in content
        assert f'<a href="{escape(second.orientation_anchor_path())}">Booked for' in content
        OrientationBooking.objects.update(status=OrientationBooking.Status.REQUESTED)
        content = _page(client, cnc).content.decode()
        assert "Your request is in. The guild will confirm a time." in content

    def it_keeps_todays_copy_when_every_way_is_one_orientation(client: Client):
        cnc, full, first, _second = _cnc(ways=False)
        cnc.set_unlocking_ways([[full], [first]])
        _login(client, "ways_member_singles")
        response = _page(client, cnc)
        assert response.context["orientation_requirement_sentence"] == ""
        assert "Any one of these orientations unlocks this." in response.content.decode()


def describe_the_cards():
    def it_reads_each_cards_ways_from_the_bulk_set(client: Client):
        cnc, _full, first, second = _cnc()
        user = _login(client, "ways_cards")
        OrientationRecordFactory(member=user.member, orientation_type=first)
        cards = client.get(reverse("hub_equipment_index")).context["cards"]
        assert [card["access_state"] for card in cards] == [Equipment.AccessState.NEEDS_ORIENTATION]
        OrientationRecordFactory(member=user.member, orientation_type=second)
        cards = client.get(reverse("hub_equipment_index")).context["cards"]
        assert [card["access_state"] for card in cards] == [Equipment.AccessState.OK]

    def it_groups_a_locked_cards_book_links_by_way(client: Client):
        cnc, full, first, second = _cnc()
        _login(client, "ways_cards_grouped")
        content = client.get(reverse("hub_equipment_index")).content.decode()
        assert content.count("data-unlock-way") == 2
        assert content.count('<span class="pl-equip-card__or" aria-hidden="true">or</span>') == 1
        assert '<span class="pl-equip-card__way-lead">Both</span>' in content
        for orientation_type in (full, first, second):
            assert f'data-unlock-type="{orientation_type.pk}">Book {orientation_type.name}</a>' in content
        cards = client.get(reverse("hub_equipment_index")).context["cards"]
        assert [[o for o, _link in way] for way in cards[0]["equipment"].unlocking_way_links] == [
            [full],
            [first, second],
        ]

    def it_keeps_the_flat_links_when_every_way_is_one_orientation(client: Client):
        cnc, full, first, _second = _cnc(ways=False)
        cnc.set_unlocking_ways([[full], [first]])
        _login(client, "ways_cards_flat")
        content = client.get(reverse("hub_equipment_index")).content.decode()
        assert "data-unlock-way" not in content
        assert f'data-unlock-type="{full.pk}">Book CNC Machine Orientation</a>' in content

    def it_renders_the_grid_in_a_fixed_number_of_queries_however_many_ways(client: Client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        _login(client, "ways_cards_queries")
        url = reverse("hub_equipment_index")

        def count_queries() -> int:
            client.get(url)  # warm the session and per-request caches
            with CaptureQueriesContext(connection) as ctx:
                assert client.get(url).status_code == 200
            return len(ctx.captured_queries)

        _cnc()
        with_one = count_queries()
        for index in range(4):
            guild = GuildFactory(name=f"Ways Guild {index}")
            pair = [OrientationTypeFactory(guild=guild, name=f"Pair {index} {n}") for n in (1, 2)]
            single = OrientationTypeFactory(guild=guild, name=f"Single {index}")
            EquipmentFactory(name=f"Ways Item {index}", guild=guild).set_unlocking_ways([pair, [single]])
        assert Equipment.objects.count() == 5
        assert count_queries() == with_one
