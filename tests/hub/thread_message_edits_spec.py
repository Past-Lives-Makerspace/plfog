"""Edit and delete a posted message on every shared thread (#1140): who may, what changes, and that nobody hears.

One world per thread kind (feedback, class, orientation booking, equipment reservation), each with
the member side's author, someone on the other side, an outsider who cannot see the thread, and an
admin. Names and message text are factory strings no changelog could contain (STANDARDS.md section 8).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core import mail
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassMessage
from core.models import FeedbackComment, FeedbackRequest, Notification
from core.threads import ThreadMessage
from membership.models import EquipmentReservation, EquipmentReservationMessage, Member, OrientationBookingMessage
from tests.core.factories import FeedbackRequestFactory
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}


def _png(name: str = "shot.png") -> SimpleUploadedFile:
    buf = BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _user(username: str, name: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save(update_fields=["status", "full_legal_name"])
    return user


def _admin() -> User:
    return User.objects.create_superuser(username="zzthreadadmin", password="pass", email="zzthreadadmin@example.com")


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


@dataclass
class World:
    """One thread: the member side's author, the other side, an outsider and where the member reads it."""

    kind: str
    member: User
    staff: User
    outsider: User
    write: Callable[..., ThreadMessage]
    post: Callable[..., ThreadMessage]
    member_page: str

    def mine(self, body: str = "Zzmember words", photos: int = 0) -> ThreadMessage:
        return self.write(author=self.member, body=body, photos=[_png(f"m{i}.png") for i in range(photos)])

    def theirs(self, body: str = "Zzstaff words") -> ThreadMessage:
        return self.write(author=self.staff, body=body)


def _feedback_world() -> World:
    member = _user("zzfb_sender", "Fennel Quarrystone")
    request = FeedbackRequestFactory(user=member)
    staff = User.objects.create_superuser(username="zzfb_webmaster", password="pass", email="zzfbweb@example.com")
    return World(
        kind="feedback",
        member=member,
        staff=staff,
        outsider=_user("zzfb_other", "Odile Brackwater"),
        write=lambda **kw: FeedbackComment.write(request=request, **kw),
        post=lambda **kw: FeedbackComment.post(request=request, **kw),
        member_page=f"{reverse('hub_beta_feedback')}?sent={request.pk}",
    )


def _class_world() -> World:
    lead_user = UserFactory(username="zzcls_lead@example.com", email="zzcls_lead@example.com")
    lead = Member.objects.get(user=lead_user)
    guild = GuildFactory(name="Zzclay Hollow Guild", guild_lead=lead)
    teacher_user = UserFactory(username="zzcls_teach@example.com", email="zzcls_teach@example.com")
    teacher = InstructorFactory(user=teacher_user, full_legal_name="Tamsin Kilnwright", instructor_slug="zzcls-teach")
    offering = ClassOfferingFactory(
        title="Zzhollow Mugs", category=CategoryFactory(guild=guild), instructors=[teacher], ready=True
    )
    offering.submit_for_review()
    outsider = UserFactory(username="zzcls_out@example.com", email="zzcls_out@example.com")
    return World(
        kind="class",
        member=teacher_user,
        staff=lead_user,
        outsider=outsider,
        write=lambda **kw: ClassMessage.write(class_offering=offering, **kw),
        post=lambda **kw: ClassMessage.post(class_offering=offering, **kw),
        member_page=reverse("classes:teach_class_detail", kwargs={"pk": offering.pk}),
    )


def _booking_world() -> World:
    lead = _user("zzbk_lead", "Lark Mossbridge").member
    guild = GuildFactory(guild_lead=lead, name="Zzfernbank Guild")
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    orientation_type = OrientationTypeFactory(guild=guild, name="Zzplaner Basics")
    member = _user("zzbk_member", "Wren Ashcombe")
    booking = OrientationBookingFactory(
        slot=OrientationSlotFactory(guild=guild, orientation_type=orientation_type), member=member.member
    )
    return World(
        kind="booking",
        member=member,
        staff=lead.user,
        outsider=_user("zzbk_out", "Osric Pellwood"),
        write=lambda **kw: OrientationBookingMessage.write(booking=booking, **kw),
        post=lambda **kw: OrientationBookingMessage.post(booking=booking, **kw),
        member_page=reverse("hub_orientation_booking", args=[booking.pk]),
    )


def _reservation_world() -> World:
    equipment = EquipmentFactory(name="Zzdrum Sander", requires_approval=True)
    manager = _user("zzres_mgr", "Mabry Coldwell")
    EquipmentStaffMembershipFactory(equipment=equipment, member=manager.member)
    member = _user("zzres_member", "Rhosyn Tallowmere")
    starts = timezone.now() + timedelta(days=2)
    reservation = EquipmentReservationFactory(
        equipment=equipment,
        member=member.member,
        starts_at=starts,
        ends_at=starts + timedelta(hours=1),
        status=EquipmentReservation.Status.PENDING_APPROVAL,
    )
    return World(
        kind="reservation",
        member=member,
        staff=manager,
        outsider=_user("zzres_out", "Ottilie Grimsby"),
        write=lambda **kw: EquipmentReservationMessage.write(reservation=reservation, **kw),
        post=lambda **kw: EquipmentReservationMessage.post(reservation=reservation, **kw),
        member_page=reverse("hub_equipment_reservation_messages", args=[reservation.pk]),
    )


WORLDS = {
    "feedback": _feedback_world,
    "class": _class_world,
    "booking": _booking_world,
    "reservation": _reservation_world,
}


@pytest.fixture(params=list(WORLDS))
def world(request: pytest.FixtureRequest) -> World:
    return WORLDS[request.param]()


@pytest.fixture
def silent() -> Iterator[dict[str, MagicMock]]:
    """Every way a change could tell someone: the event emitter (bell, email, push, Discord) and web push."""
    with (
        patch("core.events.emit.emit") as emit,
        patch("core.events.channels.send_web_push") as push,
    ):
        yield {"emit": emit, "push": push}


def _edit_url(message: ThreadMessage) -> str:
    return reverse("hub_thread_message_edit", args=[message.THREAD_KIND, message.pk])


def _delete_url(message: ThreadMessage) -> str:
    return reverse("hub_thread_message_delete", args=[message.THREAD_KIND, message.pk])


def _edit(
    client: Client, message: ThreadMessage, body: str, *, remove: list[int] | None = None, hx: bool = True
) -> Any:
    data: dict[str, Any] = {"body": body, "remove_photos": remove or [], "next": "/zz-back/"}
    return client.post(_edit_url(message), data, **(HX if hx else {}))


def _reload(message: ThreadMessage) -> ThreadMessage | None:
    return type(message)._default_manager.filter(pk=message.pk).first()


def _toast(response: Any) -> str:
    return response["HX-Trigger"]


def _flash(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def describe_who_may_change_a_message():
    def it_lets_the_author_edit_and_delete_their_own(world: World, silent: dict[str, MagicMock]):
        message = world.mine()
        client = _client(world.member)

        edited = _edit(client, message, "Zzmember words, fixed")
        assert edited.status_code == 200
        reloaded = _reload(message)
        assert reloaded is not None and reloaded.body == "Zzmember words, fixed" and reloaded.edited_at is not None
        assert "Saved." in _toast(edited)

        deleted = client.post(_delete_url(message), **HX)
        assert deleted.status_code == 200 and deleted.content == b""
        assert "Deleted." in _toast(deleted)
        assert _reload(message) is None

    def it_lets_an_admin_edit_and_delete_anyone_elses(world: World, silent: dict[str, MagicMock]):
        message = world.mine()
        client = _client(_admin())

        assert _edit(client, message, "Zzadmin fixed it").status_code == 200
        reloaded = _reload(message)
        assert reloaded is not None and reloaded.body == "Zzadmin fixed it"
        assert client.post(_delete_url(message), **HX).status_code == 200
        assert _reload(message) is None

    def it_lets_an_admin_change_a_carried_over_note_with_no_author(world: World, silent: dict[str, MagicMock]):
        note = world.write(author=None, body="Zzold note")
        client = _client(_admin())

        assert _edit(client, note, "Zzold note, tidied").status_code == 200
        reloaded = _reload(note)
        assert reloaded is not None and reloaded.body == "Zzold note, tidied"

    # A feedback request's other side is the admins, who may change any message, so it has no such case.
    @pytest.mark.parametrize("kind", ["class", "booking", "reservation"])
    def it_refuses_the_other_side_on_a_thread_they_can_see_with_a_403(kind: str):
        world = WORLDS[kind]()
        message = world.mine()
        client = _client(world.staff)

        assert _edit(client, message, "Zzsneaky").status_code == 403
        assert client.post(_delete_url(message), **HX).status_code == 403
        reloaded = _reload(message)
        assert reloaded is not None and reloaded.body == "Zzmember words" and reloaded.edited_at is None

    def it_refuses_the_member_side_on_the_other_sides_message_with_a_403(world: World):
        message = world.theirs()
        client = _client(world.member)

        assert _edit(client, message, "Zzsneaky").status_code == 403
        assert client.post(_delete_url(message), **HX).status_code == 403
        assert _reload(message) is not None

    def it_answers_someone_who_cannot_see_the_thread_with_a_404(world: World):
        message = world.mine()
        client = _client(world.outsider)

        assert _edit(client, message, "Zzsneaky").status_code == 404
        assert client.post(_delete_url(message), **HX).status_code == 404
        reloaded = _reload(message)
        assert reloaded is not None and reloaded.body == "Zzmember words"

    def it_sends_a_signed_out_visitor_to_sign_in(world: World):
        message = world.mine()

        response = Client().post(_edit_url(message), {"body": "Zzanon"})

        assert response.status_code == 302 and "/accounts/login/" in response["Location"]
        assert Client().post(_delete_url(message)).status_code == 302
        assert _reload(message) is not None

    def it_takes_posts_only(world: World):
        message = world.mine()

        assert _client(world.member).get(_edit_url(message)).status_code == 405
        assert _client(world.member).get(_delete_url(message)).status_code == 405


def describe_an_admin_previewing_another_role():
    def it_keeps_an_admins_power_while_they_view_as_a_member():
        world = _booking_world()
        message = world.mine()
        admin = _user("zzpreview_admin", "Pim Ravensworth")
        admin.member.fog_role = Member.FogRole.ADMIN
        admin.member.save(update_fields=["fog_role"])
        client = _client(admin)
        session = client.session
        session["view_as_role"] = "member"
        session.save()

        assert _edit(client, message, "Zzpreview edit").status_code == 200

    def it_never_hands_the_power_to_a_member_who_picks_a_higher_role():
        world = _booking_world()
        message = world.theirs()
        client = _client(world.member)
        session = client.session
        session["view_as_role"] = "admin"
        session.save()

        assert _edit(client, message, "Zzpreview edit").status_code == 403


def describe_an_unknown_message():
    def it_is_a_404_for_an_unknown_thread_or_message():
        client = _client(_admin())

        assert client.post(reverse("hub_thread_message_edit", args=["zznothing", 1]), {"body": "x"}).status_code == 404
        assert client.post(reverse("hub_thread_message_delete", args=["feedback", 999999])).status_code == 404


def describe_editing():
    def it_redraws_the_message_with_the_edited_marker_for_everyone(world: World, silent: dict[str, MagicMock]):
        message = world.mine()

        response = _edit(_client(world.member), message, "Zznew wording")

        html = response.content.decode()
        assert f'data-thread-dom="{message.dom_id}"' in html and "Zznew wording" in html
        assert "data-thread-edited" in html
        page = (
            _client(world.member).get(world.member_page, **({} if world.kind != "reservation" else HX)).content.decode()
        )
        assert "Zznew wording" in page and "data-thread-edited" in page

    def it_shows_no_marker_on_a_message_never_edited(world: World):
        world.mine()

        page = (
            _client(world.member).get(world.member_page, **({} if world.kind != "reservation" else HX)).content.decode()
        )

        assert "Zzmember words" in page and "data-thread-edited" not in page

    def it_keeps_every_photo_it_was_not_asked_to_remove_and_removes_the_one_it_was(
        world: World, django_capture_on_commit_callbacks: Any
    ):
        message = world.mine(photos=2)
        first, second = list(getattr(message, "photos").all())
        gone_name, kept_name = first.image.name, second.image.name

        with django_capture_on_commit_callbacks(execute=True):
            _edit(_client(world.member), message, "Zzmember words", remove=[first.pk])

        assert list(getattr(message, "photos").values_list("pk", flat=True)) == [second.pk]
        assert not default_storage.exists(gone_name)
        assert default_storage.exists(kept_name)
        reloaded = _reload(message)
        assert reloaded is not None and reloaded.edited_at is not None

    def it_keeps_the_photos_when_only_the_text_changes(world: World):
        message = world.mine(photos=1)

        _edit(_client(world.member), message, "Zzonly the words")

        assert getattr(message, "photos").count() == 1

    def it_refuses_an_empty_message_with_no_photo_and_keeps_the_box_open(world: World):
        message = world.mine()

        response = _edit(_client(world.member), message, "   ")

        html = response.content.decode()
        assert response.status_code == 200
        assert "Write something, or keep a photo." in html
        assert "editing: true" in html
        reloaded = _reload(message)
        assert reloaded is not None and reloaded.body == "Zzmember words" and reloaded.edited_at is None

    def it_refuses_to_remove_the_last_photo_from_a_message_with_no_text(world: World):
        message = world.mine(photos=1)
        photo = getattr(message, "photos").get()

        response = _edit(_client(world.member), message, "", remove=[photo.pk])

        assert "Write something, or keep a photo." in response.content.decode()
        assert getattr(message, "photos").count() == 1

    def it_lets_the_text_go_while_a_photo_stays(world: World):
        message = world.mine(photos=1)

        _edit(_client(world.member), message, "")

        reloaded = _reload(message)
        assert reloaded is not None and reloaded.body == "" and getattr(reloaded, "photos").count() == 1

    def it_writes_nothing_when_nothing_changed(world: World):
        message = world.mine()

        _edit(_client(world.member), message, "  Zzmember words  ")

        reloaded = _reload(message)
        assert reloaded is not None and reloaded.edited_at is None

    def it_lands_a_plain_post_back_on_next_with_a_message(world: World):
        message = world.mine()

        saved = _edit(_client(world.member), message, "Zzplain save", hx=False)
        refused = _edit(_client(world.member), message, "", hx=False)

        assert (saved.status_code, saved["Location"]) == (302, "/zz-back/")
        assert "Saved." in _flash(saved)
        assert (refused.status_code, refused["Location"]) == (302, "/zz-back/")
        assert "Not saved. Write something, or keep a photo." in _flash(refused)

    def it_tells_nobody(world: World, silent: dict[str, MagicMock]):
        message = world.mine()
        bells = Notification.objects.count()
        mail.outbox.clear()

        _edit(_client(world.member), message, "Zzquiet fix")
        _edit(_client(_admin()), message, "Zzquiet admin fix")

        assert mail.outbox == []
        assert Notification.objects.count() == bells
        silent["emit"].assert_not_called()
        silent["push"].assert_not_called()


def describe_deleting():
    def it_removes_the_message_and_every_photo_file_once_it_commits(
        world: World, django_capture_on_commit_callbacks: Any
    ):
        message = world.mine(photos=2)
        names = [photo.image.name for photo in getattr(message, "photos").all()]
        photo_model = type(getattr(message, "photos").first())

        with django_capture_on_commit_callbacks(execute=True):
            _client(world.member).post(_delete_url(message), **HX)

        assert _reload(message) is None
        assert not photo_model.objects.filter(image__in=names).exists()
        assert not any(default_storage.exists(name) for name in names)

    def it_is_gone_from_the_page_for_both_sides(world: World):
        message = world.mine(body="Zzgoing away")

        _client(_admin()).post(_delete_url(message), **HX)

        page = (
            _client(world.member).get(world.member_page, **({} if world.kind != "reservation" else HX)).content.decode()
        )
        assert "Zzgoing away" not in page

    def it_lands_a_plain_post_back_on_next(world: World):
        message = world.mine()

        response = _client(world.member).post(_delete_url(message), {"next": "/zz-back/"})

        assert (response.status_code, response["Location"]) == (302, "/zz-back/")
        assert "Deleted." in _flash(response)

    def it_tells_nobody(world: World, silent: dict[str, MagicMock]):
        message = world.mine()
        bells = Notification.objects.count()
        mail.outbox.clear()

        _client(world.member).post(_delete_url(message), **HX)

        assert mail.outbox == []
        assert Notification.objects.count() == bells
        silent["emit"].assert_not_called()
        silent["push"].assert_not_called()


def describe_the_menu_on_the_page():
    def it_shows_the_member_a_menu_on_their_own_message_only(world: World):
        mine, theirs = world.mine(), world.theirs()

        page = (
            _client(world.member).get(world.member_page, **({} if world.kind != "reservation" else HX)).content.decode()
        )

        assert page.count("data-thread-edit-form") == 1
        assert f'hx-post="{mine.edit_url}"' in page
        assert theirs.edit_url not in page
        assert f"'{mine.dom_id}-delete'" in page and f"{theirs.dom_id}-delete" not in page

    def it_always_draws_the_empty_line_so_deleting_the_last_message_shows_it(world: World):
        world.mine()

        page = (
            _client(world.member).get(world.member_page, **({} if world.kind != "reservation" else HX)).content.decode()
        )

        # thread.css hides it while a message is listed; once the last one is swapped out it shows.
        assert "data-thread-empty" in page


def describe_the_guidance_target():
    def it_marks_one_element_on_the_feedback_page_whether_or_not_there_are_messages():
        sender = _user("zzguide_sender", "Garnet Lowe")
        client = _client(sender)

        empty = client.get(reverse("hub_beta_feedback")).content.decode()
        request = FeedbackRequestFactory(user=sender)
        FeedbackComment.write(request=request, author=sender, body="Zzmine")
        FeedbackComment.write(request=request, author=sender, body="Zzmine too")
        full = client.get(f"{reverse('hub_beta_feedback')}?sent={request.pk}").content.decode()

        for page in (empty, full):
            assert page.count('data-guidance-target="feedback.edit-messages"') == 1
            assert 'data-guidance-target="feedback.edit-messages">Your Requests</h2>' in page


def describe_the_feedback_inbox_pill():
    def it_clears_member_replied_when_the_unseen_reply_is_deleted():
        sender = _user("zzpill_sender", "Pella Wickham")
        request = FeedbackRequestFactory(user=sender)
        FeedbackRequest.objects.filter(pk=request.pk).update(staff_seen_at=timezone.now() - timedelta(hours=2))
        older = FeedbackComment.write(request=request, author=sender, body="Zzolder")
        FeedbackComment.objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(hours=3))
        latest = FeedbackComment.write(request=request, author=sender, body="Zzlatest")
        FeedbackRequest.objects.filter(pk=request.pk).update(member_replied_at=latest.created_at)
        assert FeedbackRequest.objects.member_replied().filter(pk=request.pk).exists()

        _client(sender).post(_delete_url(latest), **HX)

        request.refresh_from_db()
        assert not request.has_member_reply
        assert not FeedbackRequest.objects.member_replied().filter(pk=request.pk).exists()
        assert request.member_replied_at is not None and request.member_replied_at < request.staff_seen_at

    def it_clears_it_when_the_senders_only_reply_goes():
        sender = _user("zzpill_only", "Quill Ambersmith")
        request = FeedbackRequestFactory(user=sender)
        only = FeedbackComment.write(request=request, author=sender, body="Zzonly reply")
        FeedbackRequest.objects.filter(pk=request.pk).update(member_replied_at=only.created_at)

        _client(sender).post(_delete_url(only), **HX)

        request.refresh_from_db()
        assert request.member_replied_at is None and not request.has_member_reply

    def it_keeps_member_replied_while_another_unseen_reply_stays():
        sender = _user("zzpill_two", "Sorrel Dunmore")
        request = FeedbackRequestFactory(user=sender)
        first = FeedbackComment.write(request=request, author=sender, body="Zzfirst")
        second = FeedbackComment.write(request=request, author=sender, body="Zzsecond")
        stamp = second.created_at + timedelta(seconds=1)
        FeedbackRequest.objects.filter(pk=request.pk).update(member_replied_at=stamp)

        _client(sender).post(_delete_url(first), **HX)

        request.refresh_from_db()
        assert request.member_replied_at == stamp and request.has_member_reply

    def it_leaves_the_stamps_alone_for_a_seen_reply_or_a_staff_comment():
        sender = _user("zzpill_seen", "Tansy Corrow")
        request = FeedbackRequestFactory(user=sender)
        reply = FeedbackComment.write(request=request, author=sender, body="Zzseen reply")
        seen = timezone.now() + timedelta(minutes=1)
        FeedbackRequest.objects.filter(pk=request.pk).update(member_replied_at=reply.created_at, staff_seen_at=seen)
        admin = _admin()
        staff_note = FeedbackComment.write(request=request, author=admin, body="Zzstaff note")
        FeedbackRequest.objects.filter(pk=request.pk).update(staff_seen_at=seen)

        _client(sender).post(_delete_url(reply), **HX)
        _client(admin).post(_delete_url(staff_note), **HX)

        request.refresh_from_db()
        assert (request.member_replied_at, request.staff_seen_at) == (reply.created_at, seen)
        assert request.status == FeedbackRequest.Status.RECEIVED

    def it_changes_no_stamp_on_an_edit():
        sender = _user("zzpill_edit", "Umber Halloway")
        request = FeedbackRequestFactory(user=sender)
        reply = FeedbackComment.write(request=request, author=sender, body="Zzbefore")
        FeedbackRequest.objects.filter(pk=request.pk).update(member_replied_at=reply.created_at)
        request.refresh_from_db()
        before = (request.member_replied_at, request.staff_seen_at, request.status)

        _edit(_client(sender), reply, "Zzafter")

        request.refresh_from_db()
        assert (request.member_replied_at, request.staff_seen_at, request.status) == before


def describe_the_reservation_new_message_cue():
    def it_clears_when_the_unread_message_is_deleted():
        world = _reservation_world()
        message = world.mine(body="Zzunread")
        reservation = EquipmentReservation.objects.get(messages=message)

        def unread() -> bool:
            return (
                EquipmentReservation.objects.with_unread_messages_for(world.staff)
                .get(pk=reservation.pk)
                .has_unread_messages
            )

        assert unread()

        _client(world.member).post(_delete_url(message), **HX)

        assert not unread()


def describe_the_bell_rows_a_notice_wrote():
    """Rows are found by the notice's own period (the message's key), never by their text."""

    def _bells(world: World, message: ThreadMessage) -> list[Notification]:
        return list(Notification.objects.filter(period=message.notice_period))

    def it_puts_the_new_words_in_the_rows_on_an_edit_and_a_neutral_line_on_a_delete(
        world: World, django_capture_on_commit_callbacks: Any
    ):
        with django_capture_on_commit_callbacks(execute=True):  # a sender's feedback reply tells on commit
            # The other side's message: it always tells the member side, who gets a bell row.
            message = world.post(author=world.staff, body="Zzbell words")
        rows = _bells(world, message)
        assert rows and all(row.body == "Zzbell words" for row in rows)
        same_text_elsewhere = Notification.objects.create(
            user=world.member, trigger="zzother", title="Zzother", body="Zzbell words", period="zz-other-1"
        )
        count = Notification.objects.count()

        _edit(_client(world.staff), message, "Zzbell words, fixed")

        assert {row.body for row in _bells(world, message)} == {"Zzbell words, fixed"}
        period = message.notice_period
        _client(world.staff).post(_delete_url(message), **HX)

        assert {row.body for row in Notification.objects.filter(period=period)} == {"This message was deleted."}
        same_text_elsewhere.refresh_from_db()
        assert same_text_elsewhere.body == "Zzbell words"
        assert Notification.objects.count() == count

    def it_says_a_photo_was_sent_once_the_words_are_edited_away(world: World, django_capture_on_commit_callbacks: Any):
        with django_capture_on_commit_callbacks(execute=True):
            message = world.post(author=world.staff, body="Zzwords then none")
        photo_message = world.mine(photos=1)
        Notification.objects.create(
            user=world.staff, trigger="zz", title="Zz", body="Zzold", period=photo_message.notice_period
        )

        _edit(_client(world.member), photo_message, "")

        assert set(Notification.objects.filter(period=photo_message.notice_period).values_list("body", flat=True)) == {
            "Sent a photo."
        }
        assert _bells(world, message)[0].body == "Zzwords then none"


def describe_races_and_crafted_posts():
    def it_is_a_404_for_another_kinds_pk():
        world = _booking_world()
        message = world.mine()
        assert not FeedbackComment.objects.filter(pk=message.pk).exists()

        response = _client(_admin()).post(
            reverse("hub_thread_message_edit", args=["feedback", message.pk]), {"body": "x"}
        )

        assert response.status_code == 404

    def it_refuses_a_co_instructor_who_did_not_write_it():
        # Classes have several equal instructors (#686): both see the thread, only the author may change it.
        world = _class_world()
        message = world.mine()
        offering = ClassMessage.objects.get(pk=message.pk).class_offering
        co_user = UserFactory(username="zzcls_co@example.com", email="zzcls_co@example.com")
        co = InstructorFactory(user=co_user, full_legal_name="Corin Ashby", instructor_slug="zzcls-co")
        offering.instructors.add(co)

        assert _edit(_client(co_user), message, "Zzsneaky").status_code == 403
        assert _client(co_user).post(_delete_url(message), **HX).status_code == 403

    def it_refuses_an_orientor_who_is_not_the_lead_on_the_members_message_but_lets_them_change_their_own():
        world = _booking_world()
        booking = OrientationBookingMessage.objects.get(pk=world.mine().pk).booking
        orientor = _user("zzbk_orientor", "Ottoline Reed")
        GuildStaffMembershipFactory(guild=booking.guild, member=orientor.member)
        members = world.mine(body="Zzmember note")
        theirs = OrientationBookingMessage.write(booking=booking, author=orientor, body="Zzorientor note")
        client = _client(orientor)

        assert _edit(client, members, "Zzsneaky").status_code == 403
        assert _edit(client, theirs, "Zzorientor note, fixed").status_code == 200

    def it_refuses_a_photo_that_belongs_to_another_message(world: World):
        mine, other = world.mine(photos=1), world.mine(body="Zzother", photos=1)
        other_photo = getattr(other, "photos").get()

        response = _edit(_client(world.member), mine, "Zzmember words", remove=[other_photo.pk])

        assert response.status_code == 200 and "editing: true" in response.content.decode()
        assert getattr(other, "photos").filter(pk=other_photo.pk).exists()
        assert getattr(mine, "photos").count() == 1

    def it_is_a_404_when_the_message_goes_between_the_gate_and_the_save(world: World):
        message = world.mine()

        def gone(self: ThreadMessage, **_: Any) -> bool:
            type(self)._default_manager.filter(pk=self.pk).delete()
            return ThreadMessage.edit(self, body="Zzlate")

        with patch.object(type(message), "edit", gone):
            assert _edit(_client(world.member), message, "Zzlate").status_code == 404

    def it_is_a_404_when_the_message_goes_before_the_redraw(world: World):
        message = world.mine()

        def gone(self: ThreadMessage, **_: Any) -> bool:
            type(self)._default_manager.filter(pk=self.pk).delete()
            return True

        with patch.object(type(message), "edit", gone):
            assert _edit(_client(world.member), message, "Zzlate").status_code == 404

    def it_redraws_the_box_when_the_last_photo_went_in_another_tab(world: World):
        message = world.mine(photos=1)
        photo = getattr(message, "photos").get()

        def other_tab(self: ThreadMessage, **kwargs: Any) -> bool:
            type(photo)._default_manager.filter(pk=photo.pk).delete()
            return ThreadMessage.edit(self, **kwargs)

        second = world.mine(body="Zzsecond", photos=1)
        second_photo = getattr(second, "photos").get()

        def other_tab_plain(self: ThreadMessage, **kwargs: Any) -> bool:
            type(second_photo)._default_manager.filter(pk=second_photo.pk).delete()
            return ThreadMessage.edit(self, **kwargs)

        with patch.object(type(message), "edit", other_tab):
            htmx = _edit(_client(world.member), message, "")
        with patch.object(type(second), "edit", other_tab_plain):
            plain = _edit(_client(world.member), second, "", hx=False)

        assert htmx.status_code == 200 and "Write something, or keep a photo." in htmx.content.decode()
        assert plain.status_code == 302 and "Not saved. Write something, or keep a photo." in _flash(plain)
        reloaded = _reload(message)
        assert reloaded is not None and reloaded.body == "Zzmember words"
