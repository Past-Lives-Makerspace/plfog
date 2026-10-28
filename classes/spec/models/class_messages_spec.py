"""BDD specs for a class's message thread (#975, the shared thread, core.threads).

The thread is between the class's instructors (all equal, #686) and its reviewers: the guild's
lead and Class Reviewers on a guild-led class, else the CMS Administrators; admins write as
reviewers but are never added to who hears. Posting always tells the other side minus the
author, and only posting does: a decision's note and a change request are written into the
thread and ride on their own notice. The thread hangs off the class, so a resubmit keeps it.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from PIL import Image

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassApproval, ClassMessage, ClassOffering
from core.events.registry import CLASS_MESSAGE_TO_INSTRUCTORS, CLASS_MESSAGE_TO_REVIEWERS
from core.models import Notification
from membership.models import AdminCapability, GuildStaffMembership, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _outbox() -> None:
    mail.outbox = []


def _person(slug: str, name: str) -> Member:
    """A member with a login and an email, named ``name``."""
    user = UserFactory(username=f"{slug}@example.com", email=f"{slug}@example.com")
    member = Member.objects.get(user=user)
    member.full_legal_name = name
    member.save(update_fields=["full_legal_name"])
    return member


def _teacher(slug: str, name: str) -> Member:
    user = UserFactory(username=f"{slug}@example.com", email=f"{slug}@example.com")
    return InstructorFactory(user=user, full_legal_name=name, instructor_slug=slug)


def _cms_admin(slug: str, name: str) -> Member:
    member = _person(slug, name)
    member.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
    return member


def _fog_admin(slug: str, name: str) -> Member:
    member = _person(slug, name)
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    return member


def _photo() -> SimpleUploadedFile:
    buf = BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return SimpleUploadedFile("shot.png", buf.getvalue(), content_type="image/png")


def _bells(member: Member, trigger: str) -> list[Notification]:
    return list(Notification.objects.filter(user=member.user, trigger=trigger))


class _GuildClass:
    """A guild-led class: the lead, one Class Reviewer, one plain staffer, two instructors, a CMS Administrator."""

    def __init__(self, prefix: str, **offering: object) -> None:
        self.lead = _person(f"{prefix}-lead", "Jay Holtman")
        self.reviewer = _person(f"{prefix}-rev", "Amber Capwell")
        self.plain = _person(f"{prefix}-plain", "Nicole Javaly")
        self.guild = GuildFactory(name=f"Zz {prefix} Guild", guild_lead=self.lead)
        GuildStaffMembershipFactory(
            guild=self.guild, member=self.reviewer, role=GuildStaffMembership.Role.CO_LEAD, can_review_classes=True
        )
        GuildStaffMembershipFactory(guild=self.guild, member=self.plain, role=GuildStaffMembership.Role.SECRETARY)
        self.cms = _cms_admin(f"{prefix}-cms", "Casey Approver")
        self.erin = _teacher(f"{prefix}-erin", "Erin Horgan")
        self.sam = _teacher(f"{prefix}-sam", "Sam McKee")
        self.offering = ClassOfferingFactory(
            title=f"Zz {prefix} Mugs",
            category=CategoryFactory(guild=self.guild),
            instructors=[self.erin, self.sam],
            **offering,
        )


class _LeadlessClass:
    """A class with no guild lead behind it: the CMS Administrators review it."""

    def __init__(self, prefix: str, **offering: object) -> None:
        self.cms = _cms_admin(f"{prefix}-cms", "Casey Approver")
        self.erin = _teacher(f"{prefix}-erin", "Erin Horgan")
        self.offering = ClassOfferingFactory(title=f"Zz {prefix} Lamps", instructors=[self.erin], **offering)


def describe_sides():
    def it_reads_an_instructors_message_as_the_instructor_side_signed_with_their_name():
        world = _GuildClass("side-i")
        message = world.offering.write_message(author=world.sam.user, body="Zzhello")
        assert message is not None
        assert (message.is_from_staff, message.author_name, message.shows_staff_tag) == (False, "Sam McKee", False)
        assert message.member_user_ids == {world.erin.user.pk, world.sam.user.pk}

    def it_reads_a_reviewers_message_as_the_reviewer_side_tagged_reviewer():
        world = _GuildClass("side-r")
        message = world.offering.write_message(author=world.lead.user, body="Zzlooks good")
        assert message is not None
        assert (message.is_from_staff, message.author_name, message.shows_staff_tag) == (True, "Jay Holtman", True)
        assert message.staff_signature == "Reviewer"

    def it_signs_a_message_with_no_author_as_the_reviewer():
        world = _GuildClass("side-n")
        message = world.offering.write_message(author=None, body="Zzold note")
        assert message is not None
        assert (message.is_from_staff, message.author_name, message.shows_staff_tag) == (True, "Reviewer", False)
        assert message.member_side_name == ""

    def it_writes_nothing_for_blank_text():
        world = _GuildClass("side-b")
        assert world.offering.write_message(author=world.erin.user, body="  \n ") is None
        assert world.offering.messages.count() == 0

    def it_names_each_message_and_photo():
        world = _GuildClass("side-s")
        message = world.offering.post_message(author=world.erin.user, body="Zzpic", photos=[_photo()])
        photo = message.thread_photos[0]
        assert str(message) == f"Message {message.pk} on class {world.offering.pk}"
        assert str(photo) == f"Photo {photo.pk} on class message {message.pk}"
        assert photo.image.name.startswith("class-messages/")


def describe_message_side():
    def it_places_each_person_on_their_side_or_nowhere():
        world = _GuildClass("ms")
        offering = world.offering
        outsider = _person("ms-out", "Out Sider")
        superuser = User.objects.create_superuser("ms-root", "ms-root@example.com", "x")
        assert offering.message_side(world.erin.user) == ClassOffering.MESSAGE_SIDE_INSTRUCTOR
        assert offering.message_side(world.lead.user) == ClassOffering.MESSAGE_SIDE_REVIEWER
        assert offering.message_side(world.reviewer.user) == ClassOffering.MESSAGE_SIDE_REVIEWER
        assert offering.message_side(world.cms.user) == ClassOffering.MESSAGE_SIDE_REVIEWER
        assert offering.message_side(_fog_admin("ms-admin", "Ada Admin").user) == ClassOffering.MESSAGE_SIDE_REVIEWER
        assert offering.message_side(superuser) == ClassOffering.MESSAGE_SIDE_REVIEWER
        assert offering.message_side(world.plain.user) is None
        assert offering.message_side(outsider.user) is None
        assert offering.message_side(AnonymousUser()) is None
        assert offering.message_side(None) is None

    def it_refuses_an_account_with_no_member():
        world = _LeadlessClass("ms-nomember")
        user = UserFactory(username="ms-nm@example.com")
        Member.objects.filter(user=user).delete()
        assert world.offering.message_side(user) is None

    def it_puts_an_instructor_who_also_reviews_on_the_instructor_side():
        world = _GuildClass("ms-both")
        world.offering.set_instructors([world.erin, world.lead])
        offering = ClassOffering.objects.get(pk=world.offering.pk)
        assert offering.message_side(world.lead.user) == ClassOffering.MESSAGE_SIDE_INSTRUCTOR


def describe_posting():
    def it_tells_the_guilds_lead_and_class_reviewers_of_an_instructors_message():
        world = _GuildClass("post-i")
        message = world.offering.post_message(author=world.erin.user, body="Zzis the list long enough?")
        assert message.notified is True
        [bell] = _bells(world.lead, CLASS_MESSAGE_TO_REVIEWERS)
        assert bell.url == world.offering.messages_path
        assert len(_bells(world.reviewer, CLASS_MESSAGE_TO_REVIEWERS)) == 1
        # Never the CMS Administrators on a guild-led class, nor plain staff, nor the other instructor.
        for member in (world.cms, world.plain, world.sam, world.erin):
            assert _bells(member, CLASS_MESSAGE_TO_REVIEWERS) == []
        assert message.told_sentence() == f" The {world.guild.name} lead and Class Reviewers were notified."
        assert any("Zzis the list long enough?" in m.body for m in mail.outbox)

    def it_tells_the_cms_administrators_on_a_class_with_no_guild_lead():
        world = _LeadlessClass("post-cms")
        message = world.offering.post_message(author=world.erin.user, body="Zzquestion")
        assert len(_bells(world.cms, CLASS_MESSAGE_TO_REVIEWERS)) == 1
        assert message.told_sentence() == " The CMS Administrators were notified."
        assert world.offering.reviewers_label == "the CMS Administrators"

    def it_leaves_the_author_out_when_a_reviewer_teaches_too():
        world = _GuildClass("post-self")
        world.offering.set_instructors([world.erin, world.reviewer])
        offering = ClassOffering.objects.get(pk=world.offering.pk)
        offering.post_message(author=world.reviewer.user, body="Zzmy own class")
        assert _bells(world.reviewer, CLASS_MESSAGE_TO_REVIEWERS) == []
        assert len(_bells(world.lead, CLASS_MESSAGE_TO_REVIEWERS)) == 1

    def it_tells_nobody_when_the_author_is_the_only_reviewer():
        world = _LeadlessClass("post-alone")
        world.cms.admin_capabilities.all().delete()
        lone = _cms_admin("post-alone-only", "Only Reviewer")
        lone.instructor_slug = "only"
        lone.save(update_fields=["instructor_slug"])
        world.offering.set_instructors([world.erin, lone])
        offering = ClassOffering.objects.get(pk=world.offering.pk)
        message = offering.post_message(author=lone.user, body="Zzhello")
        assert message.notified is False
        assert message.told_sentence() == ""
        assert Notification.objects.filter(trigger=CLASS_MESSAGE_TO_REVIEWERS).count() == 0

    def it_tells_every_instructor_but_the_author_of_a_reviewers_message():
        world = _GuildClass("post-r")
        message = world.offering.post_message(author=world.lead.user, body="Zzadd a mug photo", photos=[_photo()])
        [bell] = _bells(world.erin, CLASS_MESSAGE_TO_INSTRUCTORS)
        assert bell.url == world.offering.messages_path
        assert len(_bells(world.sam, CLASS_MESSAGE_TO_INSTRUCTORS)) == 1
        assert _bells(world.lead, CLASS_MESSAGE_TO_INSTRUCTORS) == []
        assert message.told_sentence() == " Erin Horgan and Sam McKee were notified."
        assert len(message.thread_photos) == 1

    def it_names_one_instructor_in_the_singular_and_three_with_commas():
        one = _LeadlessClass("post-one")
        message = one.offering.post_message(author=one.cms.user, body="Zzhi")
        assert message.told_sentence() == " Erin Horgan was notified."
        many = _GuildClass("post-three")
        many.offering.set_instructors([many.erin, many.sam, _teacher("post-three-kai", "Kai Lee")])
        offering = ClassOffering.objects.get(pk=many.offering.pk)
        assert offering.post_message(author=many.lead.user, body="Zzhi").told_sentence() == (
            " Erin Horgan, Sam McKee and Kai Lee were notified."
        )

    def it_tells_the_other_instructors_when_an_instructor_signs_out_and_a_review_link_writes():
        world = _GuildClass("post-anon")
        message = world.offering.post_message(author=None, body="Zzfrom the link")
        assert message.is_from_staff is True
        assert len(_bells(world.erin, CLASS_MESSAGE_TO_INSTRUCTORS)) == 1
        assert len(_bells(world.sam, CLASS_MESSAGE_TO_INSTRUCTORS)) == 1

    def it_tells_nobody_when_no_instructor_has_an_account():
        offering = ClassOfferingFactory(title="Zz no login")
        admin = _fog_admin("post-nologin", "Ada Admin")
        message = offering.post_message(author=admin.user, body="Zzanyone?")
        assert message.notified is False
        assert Notification.objects.filter(trigger=CLASS_MESSAGE_TO_INSTRUCTORS).count() == 0

    def it_tells_a_reviewer_side_writer_the_answer_while_they_still_review():
        world = _GuildClass("post-admin")
        admin = _fog_admin("post-admin-ada", "Ada Admin")
        world.offering.post_message(author=admin.user, body="Zzadmin here")
        world.offering.post_message(author=world.erin.user, body="Zzthanks")
        assert len(_bells(admin, CLASS_MESSAGE_TO_REVIEWERS)) == 1
        # The CMS Administrators are not this guild-led class's audience until one writes.
        assert _bells(world.cms, CLASS_MESSAGE_TO_REVIEWERS) == []

    def it_stops_telling_a_writer_who_no_longer_reviews():
        world = _GuildClass("post-demoted")
        admin = _fog_admin("post-demoted-ada", "Ada Admin")
        world.offering.post_message(author=admin.user, body="Zzadmin here")
        admin.fog_role = Member.FogRole.MEMBER
        admin.save(update_fields=["fog_role"])
        world.offering.post_message(author=world.erin.user, body="Zzthanks")
        assert _bells(admin, CLASS_MESSAGE_TO_REVIEWERS) == []
        assert len(_bells(world.lead, CLASS_MESSAGE_TO_REVIEWERS)) == 1


def describe_decision_notes():
    def it_keeps_the_note_and_writes_it_into_the_thread_with_one_notice():
        world = _GuildClass("dec", ready=True)
        (row,) = world.offering.submit_for_review()
        row.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=world.lead.user, notes="Zzadd a mug photo")
        row.refresh_from_db()
        assert row.notes == "Zzadd a mug photo"
        [message] = world.offering.thread()
        assert (message.author_id, message.body, message.created_at) == (
            world.lead.user.pk,
            "Zzadd a mug photo",
            row.decided_at,
        )
        assert Notification.objects.filter(trigger=CLASS_MESSAGE_TO_INSTRUCTORS).count() == 0

    def it_writes_nothing_for_a_decision_with_no_note_or_the_admins_auto_close():
        world = _GuildClass("dec-none", ready=True)
        world.offering.submit_for_review()
        admin = _fog_admin("dec-none-ada", "Ada Admin")
        world.offering.approve(admin.user)
        assert world.offering.messages.count() == 0
        assert ClassApproval.objects.filter(
            class_offering=world.offering, notes="Approved automatically when an admin gave final approval."
        ).exists()

    def it_keeps_every_message_through_a_resubmit():
        world = _GuildClass("dec-re", ready=True)
        (row,) = world.offering.submit_for_review()
        row.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=world.lead.user, notes="Zzfirst round")
        world.offering.post_message(author=world.erin.user, body="Zzwhy?")
        world.offering.refresh_from_db()
        world.offering.submit_for_review()
        assert not ClassApproval.objects.filter(notes="Zzfirst round").exists()
        assert [m.body for m in world.offering.thread()] == ["Zzfirst round", "Zzwhy?"]


def describe_change_requests():
    def it_writes_the_request_as_the_instructors_message_with_only_the_change_notice():
        world = _LeadlessClass("chg", status=ClassOffering.Status.PUBLISHED)
        assert world.offering.request_change(world.erin, "Zzmove it\nto Friday") is True
        [message] = world.offering.thread()
        assert (message.author_id, message.body, message.is_from_staff) == (
            world.erin.user.pk,
            "Zzmove it\nto Friday",
            False,
        )
        assert Notification.objects.filter(trigger=CLASS_MESSAGE_TO_REVIEWERS).count() == 0
        assert Notification.objects.filter(trigger="class_change_requested").count() == 1

    def it_writes_nothing_for_a_repeat_the_admins_already_have():
        world = _LeadlessClass("chg-rep", status=ClassOffering.Status.PUBLISHED)
        world.offering.request_change(world.erin, "Zzmove it")
        assert world.offering.request_change(world.erin, "Zzmove it") is False
        assert world.offering.messages.count() == 1


def describe_reading():
    def it_reads_a_thread_of_any_length_in_a_fixed_number_of_queries():
        world = _GuildClass("read")
        world.offering.post_message(author=world.erin.user, body="Zzone", photos=[_photo()])
        offering = ClassOffering.objects.get(pk=world.offering.pk)
        with CaptureQueriesContext(connection) as few:
            [(m.is_from_staff, m.author_name, len(m.thread_photos)) for m in offering.thread()]
        for n in range(4):
            world.offering.post_message(author=world.lead.user, body=f"Zzmore {n}", photos=[_photo()])
        offering = ClassOffering.objects.get(pk=world.offering.pk)
        with CaptureQueriesContext(connection) as many:
            messages = offering.thread()
            [(m.is_from_staff, m.author_name, len(m.thread_photos)) for m in messages]
        assert len(many) == len(few)
        assert [m.body for m in messages][:2] == ["Zzone", "Zzmore 0"]

    def it_keys_and_links_the_thread():
        world = _LeadlessClass("keys")
        assert world.offering.message_thread_key == f"class-{world.offering.pk}"
        assert world.offering.messages_path == f"/classes/teach/classes/{world.offering.pk}/messages/"
        assert isinstance(world.offering.messages.model(), ClassMessage)
