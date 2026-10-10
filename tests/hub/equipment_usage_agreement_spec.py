"""BDD specs for the equipment usage agreement on the hub pages (#734).

The member's agree prompt and modal on the equipment page, the agree endpoint, the
crafted reserve POST, the index badge and its fixed query count, and the manager's
fields on the Details tab with the "asked again" message.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hub.equipment_views import _equipment_queryset, reservation_cards
from membership.models import (
    Equipment,
    EquipmentAgreementAcceptance,
    EquipmentReservation,
    Member,
    OrientationType,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentStaffMembershipFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

LINK = "https://docs.google.com/document/d/cnc-rules/edit"
TEXT = "Wear eye protection.\nNever leave the <b>CNC</b> running alone."


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = Member.Status.ACTIVE
    member.full_legal_name = member.full_legal_name or username.title()
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def _day():
    return timezone.localdate() + timedelta(days=2)


def _at(day, hour: int):
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


def _open_tool(**kwargs) -> Equipment:
    equipment = EquipmentFactory(**kwargs)
    EquipmentHoursFactory(equipment=equipment, weekday=_day().weekday(), start_time=time(9, 0), end_time=time(17, 0))
    return equipment


def _complete(member: Member, orientation_type: OrientationType) -> None:
    slot = OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type)
    OrientationBookingFactory(member=member, slot=slot, is_completed=True)


def _detail(client: Client, equipment: Equipment) -> str:
    return client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()


def _agree(client: Client, equipment: Equipment, **extra: str):
    data = {"agree": "on", "fingerprint": equipment.usage_agreement_fingerprint, **extra}
    return client.post(
        reverse("hub_equipment_agree", args=[equipment.slug]),
        data,
        HTTP_USER_AGENT="Mozilla/5.0 Spec",
        REMOTE_ADDR="198.51.100.7",
    )


def _reserve(client: Client, equipment: Equipment, hour: int = 10):
    return client.post(
        reverse("hub_equipment_reserve", args=[equipment.slug]),
        {"starts_at": _at(_day(), hour).isoformat(), "duration_minutes": 60, "day": _day().isoformat()},
    )


def _messages(response) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def describe_equipment_page():
    def describe_with_no_agreement():
        def it_shows_book_a_time_and_no_prompt(client: Client):
            _login(client, "ua_none")
            content = _detail(client, _open_tool())
            assert 'id="equip-reserve-form"' in content
            assert "data-equip-agree-prompt" not in content
            assert 'modal_id="equip-agreement"' not in content
            assert "equip-agreement-title" not in content

    def describe_an_oriented_member_who_has_not_agreed():
        def _setup(client: Client) -> Equipment:
            user = _login(client, "ua_prompt")
            lathe = OrientationTypeFactory(name="Lathe")
            equipment = _open_tool(usage_agreement_url=LINK, usage_agreement_text=TEXT, unlocking_orientations=[lathe])
            _complete(user.member, lathe)
            return equipment

        def it_shows_the_prompt_where_the_reserve_form_would_be(client: Client):
            content = _detail(client, _setup(client))
            assert "data-equip-agree-prompt" in content
            assert ">Agree to the usage agreement</button>" in content
            assert 'id="equip-reserve-form"' not in content
            assert "data-equip-agree-banner" in content

        def it_opens_a_modal_with_the_text_escaped_and_the_link_in_a_new_tab(client: Client):
            equipment = _setup(client)
            content = _detail(client, equipment)
            assert 'id="equip-agreement-title"' in content
            box = content[content.index("data-equip-agreement-text") :].split("</div>", 1)[0]
            assert "Wear eye protection.<br>Never leave the &lt;b&gt;CNC&lt;/b&gt; running alone." in box
            link = content[content.index("data-equip-agreement-link") - 200 :].split("</a>", 1)[0]
            assert f'href="{LINK}"' in link
            assert 'target="_blank"' in link
            assert f'value="{equipment.usage_agreement_fingerprint}"' in content
            assert reverse("hub_equipment_agree", args=[equipment.slug]) in content

        def it_shows_only_the_link_when_there_is_no_text(client: Client):
            _login(client, "ua_link_only")
            content = _detail(client, _open_tool(usage_agreement_url=LINK))
            assert "data-equip-agreement-link" in content
            assert "data-equip-agreement-text" not in content
            assert "Return to this tab to agree." in content

        def it_shows_only_the_text_when_there_is_no_link(client: Client):
            _login(client, "ua_text_only")
            content = _detail(client, _open_tool(usage_agreement_text=TEXT))
            assert "data-equip-agreement-text" in content
            assert "data-equip-agreement-link" not in content

    def describe_a_member_without_the_orientation():
        def it_shows_the_orientation_gate_and_no_prompt(client: Client):
            _login(client, "ua_unoriented")
            equipment = _open_tool(
                usage_agreement_url=LINK, unlocking_orientations=[OrientationTypeFactory(name="Lathe")]
            )
            content = _detail(client, equipment)
            assert "You need the Lathe orientation" in content
            assert "data-equip-agree-prompt" not in content
            assert "data-equip-agree-banner" not in content


def describe_agree_endpoint():
    def it_records_the_version_ip_and_browser_and_returns_to_the_schedule(client: Client):
        user = _login(client, "ua_agree")
        equipment = _open_tool(usage_agreement_text=TEXT)
        response = _agree(client, equipment)
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_equipment_detail", args=[equipment.slug]) + "#equipment-schedule"
        assert _messages(response) == ["Thanks for agreeing. You can reserve a time now."]
        acceptance = EquipmentAgreementAcceptance.objects.get(member=user.member, equipment=equipment)
        assert acceptance.fingerprint == equipment.usage_agreement_fingerprint
        assert acceptance.ip_address == "198.51.100.7"
        assert acceptance.user_agent == "Mozilla/5.0 Spec"

    def it_reads_the_first_forwarded_ip(client: Client):
        user = _login(client, "ua_forwarded")
        equipment = _open_tool(usage_agreement_text=TEXT)
        client.post(
            reverse("hub_equipment_agree", args=[equipment.slug]),
            {"agree": "on", "fingerprint": equipment.usage_agreement_fingerprint},
            HTTP_X_FORWARDED_FOR="203.0.113.5, 10.0.0.1",
        )
        assert EquipmentAgreementAcceptance.objects.get(member=user.member).ip_address == "203.0.113.5"

    def it_records_once_on_a_double_submit(client: Client):
        user = _login(client, "ua_double")
        equipment = _open_tool(usage_agreement_text=TEXT)
        _agree(client, equipment)
        second = _agree(client, equipment)
        assert second.status_code == 302
        assert EquipmentAgreementAcceptance.objects.filter(member=user.member).count() == 1

    def it_refuses_a_stale_version_with_a_message(client: Client):
        user = _login(client, "ua_stale")
        equipment = _open_tool(usage_agreement_text=TEXT)
        read_version = equipment.usage_agreement_fingerprint
        equipment.usage_agreement_text = TEXT + " Also sweep up."
        equipment.save()
        response = client.post(
            reverse("hub_equipment_agree", args=[equipment.slug]), {"agree": "on", "fingerprint": read_version}
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_equipment_detail", args=[equipment.slug])
        assert _messages(response) == ["The usage agreement changed while you were reading it. Please read it again."]
        assert not EquipmentAgreementAcceptance.objects.filter(member=user.member).exists()

    def it_refuses_without_the_checkbox(client: Client):
        user = _login(client, "ua_unchecked")
        equipment = _open_tool(usage_agreement_text=TEXT)
        response = client.post(
            reverse("hub_equipment_agree", args=[equipment.slug]),
            {"fingerprint": equipment.usage_agreement_fingerprint},
        )
        assert _messages(response) == ["Check the box to agree to the usage agreement."]
        assert not EquipmentAgreementAcceptance.objects.filter(member=user.member).exists()

    def it_refuses_a_viewer_with_no_member_profile(client: Client):
        user = _login(client, "ua_no_member")
        user.member.delete()
        equipment = _open_tool(usage_agreement_text=TEXT)
        response = _agree(client, equipment)
        assert _messages(response) == ["You need a member profile to agree to the usage agreement."]
        assert not EquipmentAgreementAcceptance.objects.exists()

    def it_404s_retired_equipment_for_a_member(client: Client):
        _login(client, "ua_retired")
        equipment = _open_tool(usage_agreement_text=TEXT, is_active=False)
        assert _agree(client, equipment).status_code == 404

    def it_is_post_only_and_needs_a_login(client: Client):
        equipment = _open_tool(usage_agreement_text=TEXT)
        url = reverse("hub_equipment_agree", args=[equipment.slug])
        assert client.post(url).status_code == 302
        _login(client, "ua_get")
        assert client.get(url).status_code == 405


def describe_after_agreeing():
    def it_shows_the_reserve_form_and_reserves_without_asking_again(client: Client):
        user = _login(client, "ua_then_reserve")
        equipment = _open_tool(usage_agreement_text=TEXT)
        _agree(client, equipment)
        content = _detail(client, equipment)
        assert 'id="equip-reserve-form"' in content
        assert "data-equip-agree-prompt" not in content
        assert _reserve(client, equipment, 10).status_code == 200
        assert _reserve(client, equipment, 12).status_code == 200
        assert EquipmentReservation.objects.filter(member=user.member).count() == 2
        assert "data-equip-agree-prompt" not in _detail(client, equipment)

    @pytest.mark.parametrize(
        ("field", "new_value"),
        [("usage_agreement_url", LINK + "?rev=2"), ("usage_agreement_text", TEXT + "\nWear ear protection too.")],
    )
    def it_asks_again_after_the_link_or_text_changes(client: Client, field: str, new_value: str):
        _login(client, f"ua_again_{field}")
        equipment = _open_tool(usage_agreement_url=LINK, usage_agreement_text=TEXT)
        _agree(client, equipment)
        setattr(equipment, field, new_value)
        equipment.save()
        content = _detail(client, equipment)
        assert "data-equip-agree-prompt" in content
        assert 'id="equip-reserve-form"' not in content


def describe_crafted_reserve_post():
    def it_is_refused_without_a_current_acceptance(client: Client):
        user = _login(client, "ua_crafted")
        equipment = _open_tool(usage_agreement_url=LINK)
        response = _reserve(client, equipment)
        assert json.loads(response["HX-Trigger"])["showToast"]["message"] == (
            "Agree to the usage agreement before you reserve this equipment."
        )
        assert not EquipmentReservation.objects.filter(member=user.member).exists()


def describe_index_cards():
    def it_badges_a_card_that_needs_the_agreement(client: Client):
        _login(client, "ua_card")
        EquipmentFactory(name="CNC Router", usage_agreement_text=TEXT)
        response = client.get(reverse("hub_equipment_index"))
        assert [card["access_state"] for card in response.context["cards"]] == [Equipment.AccessState.NEEDS_AGREEMENT]
        assert "data-equip-card-agreement" in response.content.decode()

    def it_reads_ok_once_agreed(client: Client):
        _login(client, "ua_card_ok")
        equipment = EquipmentFactory(name="CNC Router", usage_agreement_text=TEXT)
        _agree(client, equipment)
        cards = client.get(reverse("hub_equipment_index")).context["cards"]
        assert [card["access_state"] for card in cards] == [Equipment.AccessState.OK]

    def it_reads_the_acceptances_once_for_the_whole_grid(django_assert_num_queries):
        from tests.membership.factories import MemberFactory

        member = MemberFactory(status=Member.Status.ACTIVE)
        EquipmentFactory(name="Agreement A", usage_agreement_text=TEXT)
        queryset = _equipment_queryset().active()
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as one:
            reservation_cards(member, queryset)
        EquipmentFactory(name="Agreement B", usage_agreement_text=TEXT)
        EquipmentFactory(name="Agreement C", usage_agreement_url=LINK)
        EquipmentFactory(name="No agreement")
        with django_assert_num_queries(len(one.captured_queries)):
            cards = reservation_cards(member, _equipment_queryset().active())
        assert len(cards) == 4

    def it_skips_the_acceptance_read_when_no_card_has_an_agreement(django_assert_num_queries):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from tests.membership.factories import MemberFactory

        member = MemberFactory(status=Member.Status.ACTIVE)
        EquipmentFactory(name="Plain")
        with CaptureQueriesContext(connection) as plain:
            reservation_cards(member, _equipment_queryset().active())
        assert not any("equipmentagreementacceptance" in query["sql"] for query in plain.captured_queries)


def describe_details_tab():
    def _manager(client: Client, username: str, equipment: Equipment) -> None:
        user = _login(client, username)
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)

    def _save(client: Client, equipment: Equipment, **fields: str):
        data = {"name": equipment.name, "kind": "tool", "is_active": "on", **fields}
        return client.post(reverse("hub_equipment_details_save", args=[equipment.slug]), data)

    def _agreed_by(equipment: Equipment, count: int) -> None:
        from tests.membership.factories import MemberFactory

        for _ in range(count):
            equipment.record_agreement(
                MemberFactory(status=Member.Status.ACTIVE),
                fingerprint=equipment.usage_agreement_fingerprint,
                ip_address=None,
                user_agent="",
            )

    def it_offers_the_link_and_text_fields(client: Client):
        equipment = EquipmentFactory()
        _manager(client, "ua_fields", equipment)
        content = client.get(reverse("hub_equipment_manage", args=[equipment.slug])).content.decode()
        assert 'name="usage_agreement_url"' in content
        assert 'name="usage_agreement_text"' in content
        assert "Usage agreement link" in content
        assert "Any change to the link or text asks everyone to agree again." in content
        assert "data-agreement-member-count" not in content

    def it_saves_both_fields(client: Client):
        equipment = EquipmentFactory()
        _manager(client, "ua_save", equipment)
        response = _save(client, equipment, usage_agreement_url=LINK, usage_agreement_text=TEXT)
        assert response.status_code == 302
        assert _messages(response) == [
            "Saved. Members will agree to the usage agreement before their next reservation."
        ]
        equipment.refresh_from_db()
        assert equipment.usage_agreement_url == LINK
        assert equipment.usage_agreement_text == TEXT

    def it_warns_how_many_members_agreed_before_a_change(client: Client):
        equipment = EquipmentFactory(usage_agreement_text=TEXT)
        _agreed_by(equipment, 3)
        _manager(client, "ua_note", equipment)
        content = client.get(reverse("hub_equipment_manage", args=[equipment.slug])).content.decode()
        note = content[content.index("data-agreement-member-count") :].split("</p>", 1)[0]
        assert "3 members agreed to the current version." in note
        assert "asks all of them to agree again" in note

    def it_says_them_for_one_member(client: Client):
        equipment = EquipmentFactory(usage_agreement_text=TEXT)
        _agreed_by(equipment, 1)
        _manager(client, "ua_note_one", equipment)
        content = client.get(reverse("hub_equipment_manage", args=[equipment.slug])).content.decode()
        assert "1 member agreed to the current version. Changing the link or text asks them to agree" in content

    @pytest.mark.parametrize(("agreed", "people"), [(1, "1 member"), (2, "2 members")])
    def it_says_how_many_will_be_asked_again_after_a_change(client: Client, agreed: int, people: str):
        equipment = EquipmentFactory(usage_agreement_text=TEXT)
        _agreed_by(equipment, agreed)
        _manager(client, f"ua_changed_{agreed}", equipment)
        response = _save(client, equipment, usage_agreement_text=TEXT + " Updated.")
        assert _messages(response) == [
            f"Saved. The usage agreement changed, so {people} who agreed will be asked to agree again."
        ]

    def it_says_plain_saved_when_the_agreement_is_unchanged(client: Client):
        equipment = EquipmentFactory(usage_agreement_url=LINK, usage_agreement_text=TEXT)
        _agreed_by(equipment, 2)
        _manager(client, "ua_unchanged", equipment)
        response = _save(client, equipment, usage_agreement_url=LINK, usage_agreement_text=TEXT.replace("\n", "\r\n"))
        assert _messages(response) == ["Saved."]
        assert equipment.current_agreement_member_count() == 2

    def it_says_plain_saved_when_the_agreement_is_removed(client: Client):
        equipment = EquipmentFactory(usage_agreement_text=TEXT)
        _agreed_by(equipment, 2)
        _manager(client, "ua_removed", equipment)
        response = _save(client, equipment, usage_agreement_url="", usage_agreement_text="")
        assert _messages(response) == ["Saved."]
        equipment.refresh_from_db()
        assert not equipment.has_usage_agreement

    def it_keeps_the_saved_count_on_a_failed_save(client: Client):
        equipment = EquipmentFactory(usage_agreement_text=TEXT)
        _agreed_by(equipment, 2)
        _manager(client, "ua_failed", equipment)
        response = _save(client, equipment, usage_agreement_url="not a link", usage_agreement_text="Changed.")
        assert response.status_code == 200
        assert response.context["agreement_member_count"] == 2
        assert "2 members agreed to the current version." in response.content.decode()
