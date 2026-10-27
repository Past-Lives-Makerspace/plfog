"""BDD specs for an orientation booking's message thread (the shared thread, core.threads).

Posting always tells the other side: a member's message reaches the request's audience (and any
staff side writer on the thread) minus the author; anyone else writing is the guild and tells the
member. A message written with another action's notice (``write_message``) tells nobody.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from PIL import Image

from core.events.registry import ORIENTATION_MESSAGE_TO_MEMBER, ORIENTATION_MESSAGE_TO_ORIENTORS
from core.models import Notification
from membership.models import Member, OrientationBooking, OrientationBookingMessage, OrientationBookingMessagePhoto
from tests.membership.factories import (
    EquipmentStaffMembershipFactory,
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db


def _member(username: str, name: str = "") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com")
    member = user.member
    if name:
        member.preferred_name = name
        member.save(update_fields=["preferred_name"])
    return member


def _photo() -> SimpleUploadedFile:
    buf = BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return SimpleUploadedFile("shot.png", buf.getvalue(), content_type="image/png")


def _guild_booking(prefix: str) -> tuple[OrientationBooking, Member]:
    """A booking on a guild's shared slot (the whole leadership hears) and the guild's lead."""
    lead = _member(f"{prefix}_lead", "Lead Person")
    guild = GuildFactory(name=f"Zzthread {prefix} Guild", guild_lead=lead)
    booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=_member(f"{prefix}_m", "Maya"))
    return booking, lead


def _bells(user: User | None, trigger: str) -> list[Notification]:
    return list(Notification.objects.filter(user=user, trigger=trigger))


def describe_sides():
    def it_reads_the_members_message_as_the_member_side():
        booking, _lead = _guild_booking("side_m")
        message = booking.write_message(author=booking.member.user, body="Zzhello")
        assert message is not None
        assert (message.is_from_staff, message.author_name, message.shows_staff_tag) == (False, "Maya", False)

    def it_signs_a_message_with_no_author_as_the_owner():
        booking, _lead = _guild_booking("side_none")
        message = booking.write_message(author=None, body="Zzold lead note")
        assert message is not None
        assert message.is_from_staff is True
        assert message.author_name == booking.orientation_type.owner_name
        assert message.shows_staff_tag is False

    def it_names_a_staff_author_and_tags_them_with_the_owner():
        booking, lead = _guild_booking("side_lead")
        message = booking.write_message(author=lead.user, body="Zzsee you then")
        assert message is not None
        assert (message.is_from_staff, message.author_name, message.shows_staff_tag) == (True, "Lead Person", True)
        assert message.staff_signature == booking.orientation_type.owner_name

    def it_writes_nothing_for_blank_text():
        booking, _lead = _guild_booking("side_blank")
        assert booking.write_message(author=None, body="   ") is None
        assert booking.thread() == []

    def it_finds_the_newest_message_from_each_side():
        booking, lead = _guild_booking("side_latest")
        booking.write_message(author=booking.member.user, body="Zzfirst")
        booking.write_message(author=lead.user, body="Zzanswer")
        booking.write_message(author=booking.member.user, body="Zzsecond")
        assert booking.latest_message(from_staff=False).body == "Zzsecond"  # type: ignore[union-attr]
        assert booking.latest_message(from_staff=True).body == "Zzanswer"  # type: ignore[union-attr]

    def it_has_no_newest_message_on_an_empty_side():
        booking, _lead = _guild_booking("side_empty")
        assert booking.latest_message(from_staff=True) is None

    def it_names_itself():
        booking, _lead = _guild_booking("side_str")
        message = booking.write_message(author=None, body="Zzx")
        photo = OrientationBookingMessagePhoto.objects.create(message=message, image=_photo())
        assert str(message) == f"Message {message.pk} on orientation booking {booking.pk}"  # type: ignore[union-attr]
        assert str(photo) == f"Photo {photo.pk} on orientation message {message.pk}"  # type: ignore[union-attr]


def describe_post_message():
    def describe_from_the_member():
        def it_tells_the_guild_leadership_with_a_bell_and_an_email():
            booking, lead = _guild_booking("post_m")
            message = booking.post_message(author=booking.member.user, body="Zzcan we do 6?", photos=[_photo()])

            assert message.notified is True
            [bell] = _bells(lead.user, ORIENTATION_MESSAGE_TO_ORIENTORS)
            assert bell.body == "Zzcan we do 6?"
            assert bell.url == f"/orientation/bookings/{booking.pk}/respond/#messages"
            assert _bells(booking.member.user, ORIENTATION_MESSAGE_TO_ORIENTORS) == []
            [email] = mail.outbox
            assert email.to == [lead.user.email]
            assert "Zzcan we do 6?" in email.body
            assert message.thread_photos[0].image.name.startswith("orientation-messages/")
            assert message.told_sentence() == f" The Orientors for {booking.orientation_type.owner_name} were notified."

        def it_also_tells_a_staff_writer_outside_the_audience_but_never_the_author():
            booking, lead = _guild_booking("post_writer")
            admin = User.objects.create_superuser(username="post_writer_admin", password="x", email="pwa@example.com")
            booking.write_message(author=admin, body="Zzan admin asked")

            message = booking.post_message(author=booking.member.user, body="Zzanswering")

            assert message.orientor_user_ids() == {lead.user_id, admin.pk}
            assert len(_bells(admin, ORIENTATION_MESSAGE_TO_ORIENTORS)) == 1

        def it_leaves_out_a_lead_who_writes_on_their_own_booking():
            lead = _member("post_self_lead")
            guild = GuildFactory(name="Zzthread self Guild", guild_lead=lead)
            booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=lead)

            message = booking.post_message(author=lead.user, body="Zzmy own")

            assert message.is_from_staff is False
            assert message.notified is False
            assert message.told_sentence() == ""
            assert _bells(lead.user, ORIENTATION_MESSAGE_TO_ORIENTORS) == []

        def it_tells_the_managers_of_equipment():
            booking = OrientationBookingFactory(equipment_owned=True, member=_member("post_eq_m"))
            manager = _member("post_eq_mgr")
            EquipmentStaffMembershipFactory(equipment=booking.orientation_type.equipment, member=manager)

            booking.post_message(author=booking.member.user, body="Zzlaser question")

            assert len(_bells(manager.user, ORIENTATION_MESSAGE_TO_ORIENTORS)) == 1

    def describe_from_the_staff_side():
        def it_tells_the_member_with_a_bell_and_an_email_linking_their_booking_page():
            booking, lead = _guild_booking("post_s")
            message = booking.post_message(author=lead.user, body="Zzbring shoes")

            assert message.notified is True
            [bell] = _bells(booking.member.user, ORIENTATION_MESSAGE_TO_MEMBER)
            assert bell.title == "Lead Person wrote about your orientation"
            assert bell.url == f"/orientation/bookings/{booking.pk}/"
            [email] = mail.outbox
            assert email.to == [booking.member.user.email]
            assert "Zzbring shoes" in email.body
            assert f"/orientation/bookings/{booking.pk}/" in email.body
            assert message.told_sentence() == " Maya was notified."

        def it_tells_nobody_when_the_member_has_no_account():
            lead = _member("post_noacct_lead")
            guild = GuildFactory(name="Zzthread noacct Guild", guild_lead=lead)
            booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=MemberFactory())

            message = booking.post_message(author=lead.user, body="Zzhello?")

            assert message.notified is False
            assert mail.outbox == []


def describe_write_message():
    def it_tells_nobody():
        booking, lead = _guild_booking("write_quiet")
        booking.write_message(author=lead.user, body="Zzquiet")
        assert Notification.objects.count() == 0
        assert mail.outbox == []


def describe_with_thread():
    def it_loads_any_number_of_threads_in_a_fixed_number_of_queries():
        def _count(bookings: int) -> int:
            OrientationBookingMessage.objects.all().delete()
            for booking in OrientationBooking.objects.all()[:bookings]:
                message = booking.write_message(author=booking.member.user, body="Zzm")
                OrientationBookingMessagePhoto.objects.create(message=message, image=_photo())
                booking.write_message(author=None, body="Zzs")
            with CaptureQueriesContext(connection) as queries:
                for booking in OrientationBooking.objects.with_thread():
                    for message in booking.thread():
                        _ = (message.author_name, message.is_from_staff, message.thread_photos)
            return len(queries.captured_queries)

        for index in range(3):
            _guild_booking(f"wt{index}")
        assert _count(1) == _count(3)
