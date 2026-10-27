"""BDD specs for an equipment reservation's message thread (the shared thread, core.threads, #974).

Posting always tells the other side: the member's message reaches the ``equipment_managers``
audience minus the author (admins are not added); a manager or an admin writing tells the member.
A decline's or a manager cancel's reason is written into the thread and rides on that action's one
notice. A per person read stamp drives the lists' unread cue.
"""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from core.events.copy import default_copy_for, placeholders_for
from core.events.registry import (
    EQUIPMENT_RESERVATION_MESSAGE_TO_MANAGERS,
    EQUIPMENT_RESERVATION_MESSAGE_TO_MEMBER,
    Channel,
    Recipients,
    get_event,
)
from core.models import Notification
from membership.models import (
    AdminCapability,
    Equipment,
    EquipmentError,
    EquipmentReservation,
    EquipmentReservationMessage,
    EquipmentReservationMessagePhoto,
    EquipmentReservationMessageRead,
    Member,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

TO_MEMBER = EQUIPMENT_RESERVATION_MESSAGE_TO_MEMBER
TO_MANAGERS = EQUIPMENT_RESERVATION_MESSAGE_TO_MANAGERS


def _member(username: str, name: str = "") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name or f"{username.title()} Quillfeather"
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _admin(username: str) -> Member:
    admin = _member(username)
    admin.fog_role = Member.FogRole.ADMIN
    admin.save()
    return admin


def _photo() -> SimpleUploadedFile:
    buf = BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return SimpleUploadedFile("shot.png", buf.getvalue(), content_type="image/png")


def _tool(prefix: str) -> tuple[Equipment, Member, Member]:
    """A tool needing approval with two managers (staff rows), and the second one."""
    equipment = EquipmentFactory(name=f"Zzlathe {prefix}", requires_approval=True)
    first = _member(f"{prefix}_mgr1", "Sami Ironwood")
    second = _member(f"{prefix}_mgr2", "Tova Ironwood")
    EquipmentStaffMembershipFactory(equipment=equipment, member=first)
    EquipmentStaffMembershipFactory(equipment=equipment, member=second)
    return equipment, first, second


def _reservation(equipment: Equipment, member: Member, **kwargs) -> EquipmentReservation:
    starts = timezone.now() + timedelta(days=2)
    return EquipmentReservationFactory(
        equipment=equipment,
        member=member,
        starts_at=starts,
        ends_at=starts + timedelta(hours=1),
        status=kwargs.pop("status", EquipmentReservation.Status.PENDING_APPROVAL),
        **kwargs,
    )


def _bell_users(trigger: str) -> set[int]:
    return set(Notification.objects.filter(trigger=trigger).values_list("user_id", flat=True))


def describe_sides():
    def it_reads_the_bookers_message_as_the_member_side():
        equipment, _a, _b = _tool("side_m")
        reservation = _reservation(equipment, _member("side_m_bk", "Robin Vale"))
        message = reservation.write_message(author=reservation.member.user, body="Zzhello")
        assert message is not None
        assert (message.is_from_staff, message.author_name, message.shows_staff_tag) == (False, "Robin Vale", False)

    def it_tags_a_managers_message_with_the_equipment_name():
        equipment, manager, _b = _tool("side_s")
        reservation = _reservation(equipment, _member("side_s_bk"))
        message = reservation.write_message(author=manager.user, body="Zzhi")
        assert message is not None
        assert message.is_from_staff is True
        assert message.author_name == "Sami Ironwood"
        assert (message.shows_staff_tag, message.staff_signature) == (True, "Zzlathe side_s")

    def it_names_its_rows():
        equipment, manager, _b = _tool("side_str")
        reservation = _reservation(equipment, _member("side_str_bk"))
        message = reservation.post_message(author=manager.user, body="Zzlook", photos=[_photo()])
        photo = EquipmentReservationMessagePhoto.objects.get(message=message)
        read = EquipmentReservationMessageRead.objects.get(reservation=reservation, user=manager.user)
        assert str(message) == f"Message {message.pk} on reservation {reservation.pk}"
        assert str(photo) == f"Photo {photo.pk} on reservation message {message.pk}"
        assert str(read).startswith(f"User {manager.user.pk} read reservation {reservation.pk} at ")
        assert reservation.thread_key == f"res-{reservation.pk}"


def describe_posting_as_the_member():
    def it_tells_every_manager_but_not_an_admin_who_does_not_manage_it():
        equipment, first, second = _tool("pm")
        _admin("pm_admin")
        reservation = _reservation(equipment, _member("pm_bk", "Robin Vale"))
        mail.outbox.clear()
        message = reservation.post_message(author=reservation.member.user, body="Zzwhich bit?", photos=[_photo()])
        assert message.notified is True
        assert _bell_users(TO_MANAGERS) == {first.user.pk, second.user.pk}
        assert _bell_users(TO_MEMBER) == set()
        assert message.told_sentence() == " The managers of the Zzlathe pm were notified."
        assert message.photos.count() == 1
        assert {m.to[0] for m in mail.outbox} == {"pm_mgr1@example.com", "pm_mgr2@example.com"}
        bell = Notification.objects.filter(trigger=TO_MANAGERS).first()
        assert bell is not None
        assert bell.url == reservation.staff_messages_url

    def it_leaves_out_a_manager_who_booked_it_themselves():
        equipment, first, second = _tool("pm_self")
        reservation = _reservation(equipment, first)
        reservation.post_message(author=first.user, body="Zznote to my co manager")
        assert _bell_users(TO_MANAGERS) == {second.user.pk}

    def it_tells_nobody_when_the_only_manager_is_the_author():
        equipment = EquipmentFactory()
        only = _member("pm_only")
        EquipmentStaffMembershipFactory(equipment=equipment, member=only)
        reservation = _reservation(equipment, only)
        message = reservation.post_message(author=only.user, body="Zzjust me")
        assert message.notified is False
        assert message.told_sentence() == ""
        assert not Notification.objects.filter(trigger=TO_MANAGERS).exists()

    def it_reaches_the_guilds_leadership_on_a_guild_tool():
        lead = _member("pm_lead")
        guild = GuildFactory(name="Zzwood pm Guild", guild_lead=lead)
        equipment = EquipmentFactory(guild=guild)
        reservation = _reservation(equipment, _member("pm_g_bk"))
        reservation.post_message(author=reservation.member.user, body="Zzhi guild")
        assert _bell_users(TO_MANAGERS) == {lead.user.pk}

    def it_falls_back_to_the_equipment_administrators_with_no_managers():
        equipment = EquipmentFactory()
        holder = _member("pm_cap")
        holder.sync_admin_capabilities([AdminCapability.Capability.EQUIPMENT], granted_by=None)
        reservation = _reservation(equipment, _member("pm_cap_bk"))
        reservation.post_message(author=reservation.member.user, body="Zzanyone?")
        assert _bell_users(TO_MANAGERS) == {holder.user.pk}


def describe_staff_side_writers():
    def it_tells_an_admin_who_asked_when_the_member_answers():
        equipment, first, second = _tool("wr_admin")
        admin = _admin("wr_admin_a")
        reservation = _reservation(equipment, _member("wr_admin_bk"))
        reservation.post_message(author=admin.user, body="Zzwhat are you cutting?")
        Notification.objects.all().delete()
        reservation.post_message(author=reservation.member.user, body="Zzwalnut")
        assert _bell_users(TO_MANAGERS) == {first.user.pk, second.user.pk, admin.user.pk}

    def it_never_tells_a_writer_who_no_longer_manages_it():
        equipment, first, second = _tool("wr_gone")
        reservation = _reservation(equipment, _member("wr_gone_bk"))
        reservation.post_message(author=second.user, body="Zzwhich blade?")
        equipment.staff_memberships.filter(member=second).delete()
        Notification.objects.all().delete()
        reservation.post_message(author=reservation.member.user, body="Zzthe fine one")
        assert _bell_users(TO_MANAGERS) == {first.user.pk}

    def it_tells_a_superuser_who_wrote():
        equipment, first, _second = _tool("wr_su")
        root = _member("wr_su_root")
        assert root.user is not None
        root.user.is_superuser = True
        root.user.save(update_fields=["is_superuser"])
        reservation = _reservation(equipment, _member("wr_su_bk"))
        reservation.write_message(author=root.user, body="Zzchecking in")
        reservation.post_message(author=reservation.member.user, body="Zzall good")
        assert root.user.pk in _bell_users(TO_MANAGERS)

    def it_does_not_add_the_members_own_earlier_messages():
        equipment, first, second = _tool("wr_self")
        reservation = _reservation(equipment, _member("wr_self_bk"))
        reservation.post_message(author=reservation.member.user, body="Zzone")
        Notification.objects.all().delete()
        reservation.post_message(author=reservation.member.user, body="Zztwo")
        assert _bell_users(TO_MANAGERS) == {first.user.pk, second.user.pk}


def describe_posting_as_staff():
    def it_tells_the_member_only():
        equipment, first, second = _tool("ps")
        reservation = _reservation(equipment, _member("ps_bk", "Robin Vale"))
        mail.outbox.clear()
        message = reservation.post_message(author=first.user, body="Zzbring your own bit")
        assert message.notified is True
        assert _bell_users(TO_MEMBER) == {reservation.member.user.pk}
        assert _bell_users(TO_MANAGERS) == set()
        assert message.told_sentence() == " Robin Vale was notified."
        assert [m.to[0] for m in mail.outbox] == ["ps_bk@example.com"]
        assert "Zzbring your own bit" in mail.outbox[0].body
        bell = Notification.objects.get(trigger=TO_MEMBER)
        assert bell.url == reservation.member_messages_url

    def it_lets_an_admin_write_as_staff_and_tells_the_member():
        equipment, _a, _b = _tool("ps_admin")
        admin = _admin("ps_admin_a")
        reservation = _reservation(equipment, _member("ps_admin_bk"))
        message = reservation.post_message(author=admin.user, body="Zzadmin here")
        assert message.is_from_staff is True
        assert _bell_users(TO_MEMBER) == {reservation.member.user.pk}

    def it_tells_nobody_when_the_member_has_no_account():
        equipment, first, _b = _tool("ps_noacct")
        reservation = _reservation(equipment, MemberFactory(user=None))
        message = reservation.post_message(author=first.user, body="Zzhello?")
        assert message.notified is False
        assert message.told_sentence() == ""
        assert not Notification.objects.filter(trigger=TO_MEMBER).exists()


def describe_blocks():
    def it_refuses_a_message_on_a_block():
        equipment, first, _b = _tool("blk")
        block = _reservation(equipment, first, kind=EquipmentReservation.Kind.BLOCK, status="confirmed")
        with pytest.raises(EquipmentError, match="A block has no messages."):
            block.post_message(author=first.user, body="Zzno")
        assert not EquipmentReservationMessage.objects.exists()


def describe_write_message():
    def it_writes_nothing_for_blank_text():
        equipment, first, _b = _tool("wm")
        reservation = _reservation(equipment, _member("wm_bk"))
        assert reservation.write_message(author=first.user, body="   ") is None
        assert not reservation.messages.exists()

    def it_tells_nobody():
        equipment, first, _b = _tool("wm2")
        reservation = _reservation(equipment, _member("wm2_bk"))
        reservation.write_message(author=first.user, body="Zzquiet")
        assert not Notification.objects.filter(trigger__in=[TO_MEMBER, TO_MANAGERS]).exists()


def describe_reasons_in_the_thread():
    def it_posts_a_decline_reason_as_the_managers_message_with_one_notice():
        equipment, first, _b = _tool("rd")
        reservation = _reservation(equipment, _member("rd_bk"))
        reservation.decline(first, reason="  Zzspindle is out for repair  ")
        [message] = EquipmentReservation.objects.with_thread().get(pk=reservation.pk).thread()
        assert (message.author_id, message.body, message.is_from_staff) == (
            first.user.pk,
            "Zzspindle is out for repair",
            True,
        )
        bells = Notification.objects.filter(user=reservation.member.user)
        assert list(bells.values_list("trigger", flat=True)) == ["equipment.reservation_declined"]

    def it_posts_a_manager_cancel_reason_with_one_notice():
        equipment, first, _b = _tool("rc")
        reservation = _reservation(equipment, _member("rc_bk"), status="confirmed")
        reservation.cancel(first, reason="Zzshop closed for a leak", as_manager=True)
        assert list(reservation.messages.values_list("body", flat=True)) == ["Zzshop closed for a leak"]
        bells = Notification.objects.filter(user=reservation.member.user)
        assert list(bells.values_list("trigger", flat=True)) == ["equipment.reservation_cancelled_by_manager"]

    def it_writes_nothing_when_a_manager_cancels_their_own_row():
        equipment, first, _b = _tool("rown")
        reservation = _reservation(equipment, first, status="confirmed")
        reservation.cancel(first, reason="Zzchanged my plans", as_manager=True)
        assert not reservation.messages.exists()

    def it_writes_nothing_on_a_members_own_cancel():
        equipment, _a, _b = _tool("rself")
        member = _member("rself_bk")
        reservation = _reservation(equipment, member, status="confirmed")
        reservation.cancel(member)
        assert not reservation.messages.exists()


def describe_unread():
    def it_marks_a_thread_unread_until_the_viewer_opens_it():
        equipment, first, second = _tool("ur")
        reservation = _reservation(equipment, _member("ur_bk"))
        reservation.post_message(author=reservation.member.user, body="Zzhi")

        def unread(user: User | None) -> bool:
            row = EquipmentReservation.objects.with_unread_messages_for(user).get(pk=reservation.pk)
            return row.has_unread_messages  # type: ignore[attr-defined]

        assert unread(first.user) is True
        assert unread(reservation.member.user) is False  # their own message
        reservation.mark_messages_read(first.user)
        assert unread(first.user) is False
        assert unread(second.user) is True
        assert unread(None) is False

    def it_is_unread_again_after_a_newer_message():
        equipment, first, _b = _tool("ur2")
        reservation = _reservation(equipment, _member("ur2_bk"))
        reservation.mark_messages_read(first.user, at=timezone.now() - timedelta(hours=1))
        reservation.post_message(author=reservation.member.user, body="Zzlater")
        row = EquipmentReservation.objects.with_unread_messages_for(first.user).get(pk=reservation.pk)
        assert row.has_unread_messages is True  # type: ignore[attr-defined]

    def it_counts_a_message_with_no_author_as_unread_for_the_member():
        equipment, _a, _b = _tool("ur3")
        reservation = _reservation(equipment, _member("ur3_bk"))
        reservation.write_message(author=None, body="Zzold note")
        row = EquipmentReservation.objects.with_unread_messages_for(reservation.member.user).get(pk=reservation.pk)
        assert row.has_unread_messages is True  # type: ignore[attr-defined]

    def it_never_moves_a_read_stamp_backwards():
        equipment, first, _b = _tool("ur4")
        reservation = _reservation(equipment, _member("ur4_bk"))
        now = timezone.now()
        reservation.mark_messages_read(first.user, at=now)
        reservation.mark_messages_read(first.user, at=now - timedelta(hours=2))
        assert EquipmentReservationMessageRead.objects.get(user=first.user).read_at == now
        later = now + timedelta(minutes=5)
        reservation.mark_messages_read(first.user, at=later)
        assert EquipmentReservationMessageRead.objects.get(user=first.user).read_at == later

    def it_ignores_a_reader_with_no_account():
        equipment, _a, _b = _tool("ur5")
        reservation = _reservation(equipment, _member("ur5_bk"))
        reservation.mark_messages_read(None)
        assert not EquipmentReservationMessageRead.objects.exists()


def describe_events():
    @pytest.mark.parametrize(
        ("key", "recipient"),
        [(TO_MEMBER, Recipients.SINGLE_USER), (TO_MANAGERS, Recipients.EQUIPMENT_MANAGERS)],
    )
    def it_registers_each_with_its_audience_and_copy(key: str, recipient: Recipients):
        event = get_event(key)
        assert event.recipient == recipient
        assert event.category == "Spaces & Equipment"
        assert "{{ message_body }}" in default_copy_for(key, Channel.EMAIL).body_text
        assert "reservation_url" in placeholders_for(key)

    def it_links_the_member_and_managers_to_their_pages_with_the_thread_open():
        equipment, _a, _b = _tool("ev_urls")
        reservation = _reservation(equipment, _member("ev_urls_bk"))
        assert reservation.member_messages_url == f"/equipment/?view=bookings&messages=res-{reservation.pk}"
        assert reservation.staff_messages_url == (
            f"/equipment/{equipment.slug}/manage/?tab=reservations&messages=res-{reservation.pk}"
        )
