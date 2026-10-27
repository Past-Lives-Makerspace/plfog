"""BDD specs for a class's Messages on its pages (#975): who reads and writes, and where a send lands.

Every instructor of the class sees Messages on the composer (under Where Your Class Is) and on
the Overview, in every state, and can write without resubmitting. The review page carries the
same thread in place of the old Review History list, for the class's reviewers and any admin, and
on a review link for whoever may decide that gate. Anyone else gets a 404. Viewing sends nothing.
"""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassApproval, ClassOffering
from core.events.registry import CLASS_MESSAGE_TO_INSTRUCTORS, CLASS_MESSAGE_TO_REVIEWERS
from core.models import Notification
from membership.models import AdminCapability, GuildStaffMembership, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory

pytestmark = pytest.mark.django_db


def _person(slug: str, name: str) -> Member:
    user = UserFactory(username=f"{slug}@example.com", email=f"{slug}@example.com")
    member = Member.objects.get(user=user)
    member.full_legal_name = name
    member.save(update_fields=["full_legal_name"])
    return member


def _teacher(slug: str, name: str) -> Member:
    user = UserFactory(username=f"{slug}@example.com", email=f"{slug}@example.com")
    return InstructorFactory(user=user, full_legal_name=name, instructor_slug=slug)


def _client(member: Member | User) -> Client:
    client = Client()
    client.force_login(member.user if isinstance(member, Member) else member)
    return client


def _png() -> SimpleUploadedFile:
    buf = BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return SimpleUploadedFile("shot.png", buf.getvalue(), content_type="image/png")


class _World:
    """A guild-led class in review: its lead, a Class Reviewer, a plain staffer, two instructors."""

    def __init__(self, prefix: str, *, status: str = ClassOffering.Status.DRAFT, submit: bool = True) -> None:
        self.lead = _person(f"{prefix}-lead", "Jay Holtman")
        self.reviewer = _person(f"{prefix}-rev", "Amber Capwell")
        self.plain = _person(f"{prefix}-plain", "Nicole Javaly")
        self.guild = GuildFactory(name=f"Zz {prefix} Guild", guild_lead=self.lead)
        GuildStaffMembershipFactory(
            guild=self.guild, member=self.reviewer, role=GuildStaffMembership.Role.CO_LEAD, can_review_classes=True
        )
        GuildStaffMembershipFactory(guild=self.guild, member=self.plain, role=GuildStaffMembership.Role.SECRETARY)
        self.erin = _teacher(f"{prefix}-erin", "Erin Horgan")
        self.sam = _teacher(f"{prefix}-sam", "Sam McKee")
        self.offering = ClassOfferingFactory(
            title=f"Zz {prefix} Mugs",
            category=CategoryFactory(guild=self.guild),
            instructors=[self.erin, self.sam],
            ready=True,
            status=status,
        )
        self.gate = self.offering.submit_for_review()[0] if submit else None

    @property
    def messages_url(self) -> str:
        return reverse("classes:class_messages", kwargs={"pk": self.offering.pk})


def describe_the_messages_link():
    def it_lands_an_instructor_on_the_overview_with_the_thread_open():
        world = _World("land-i")
        response = _client(world.erin).get(world.messages_url)
        overview = reverse("classes:teach_class_detail", kwargs={"pk": world.offering.pk})
        assert response.status_code == 302
        assert response["Location"] == f"{overview}?messages=class-{world.offering.pk}"

    def it_lands_a_cms_administrator_on_the_overview(admin_user):
        world = _World("land-cms")
        cms = _person("land-cms-casey", "Casey Approver")
        cms.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        overview = reverse("classes:teach_class_detail", kwargs={"pk": world.offering.pk})
        assert _client(cms).get(world.messages_url)["Location"].startswith(overview)
        assert _client(admin_user).get(world.messages_url)["Location"].startswith(overview)

    def it_lands_a_guild_reviewer_on_the_classes_review_link():
        world = _World("land-r")
        assert world.gate is not None
        response = _client(world.reviewer).get(world.messages_url)
        assert response["Location"] == f"{world.gate.review_path}?messages=class-{world.offering.pk}"

    def it_refuses_a_guild_reviewer_when_the_class_has_no_review_link():
        world = _World("land-none", submit=False)
        assert _client(world.lead).get(world.messages_url).status_code == 404

    def it_refuses_anyone_with_no_side_on_the_thread():
        world = _World("land-out")
        outsider = _person("land-out-x", "Out Sider")
        assert _client(world.plain).get(world.messages_url).status_code == 404
        assert _client(outsider).get(world.messages_url).status_code == 404
        assert _client(outsider).post(world.messages_url, {"body": "Zzhi"}).status_code == 404
        assert world.offering.messages.count() == 0

    def it_sends_a_signed_out_visitor_to_log_in():
        world = _World("land-anon")
        response = Client().get(world.messages_url)
        assert response.status_code == 302
        assert "login" in response["Location"]


def describe_posting_from_a_class_page():
    def it_posts_an_instructors_message_with_a_photo_while_changes_are_requested():
        world = _World("post-i")
        assert world.gate is not None
        world.gate.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=world.lead.user, notes="Zzadd photos")
        overview = reverse("classes:teach_class_detail", kwargs={"pk": world.offering.pk})
        response = _client(world.erin).post(
            world.messages_url, {"body": "Zzwhich photos?", "photos": [_png()], "next": overview}, follow=True
        )
        assert response.redirect_chain[-1][0] == f"{overview}?messages=class-{world.offering.pk}"
        assert f"Sent. The {world.guild.name} lead and Class Reviewers were notified." in response.content.decode()
        world.offering.refresh_from_db()
        assert world.offering.status == ClassOffering.Status.DRAFT
        assert [m.body for m in world.offering.thread()] == ["Zzadd photos", "Zzwhich photos?"]
        assert Notification.objects.filter(user=world.lead.user, trigger=CLASS_MESSAGE_TO_REVIEWERS).count() == 1

    def it_ignores_a_next_that_leaves_the_site():
        world = _World("post-next")
        response = _client(world.erin).post(world.messages_url, {"body": "Zzhi", "next": "https://evil.example/"})
        assert response["Location"].startswith(reverse("classes:teach_class_detail", kwargs={"pk": world.offering.pk}))

    def it_refuses_an_empty_message_and_sends_nothing():
        world = _World("post-empty")
        response = _client(world.erin).post(world.messages_url, {"body": ""}, follow=True)
        assert "Not sent." in response.content.decode()
        assert world.offering.messages.count() == 0

    def it_posts_a_guild_reviewers_message_back_to_the_review_link():
        world = _World("post-r")
        assert world.gate is not None
        response = _client(world.reviewer).post(world.messages_url, {"body": "Zzlooks close"})
        assert response["Location"] == f"{world.gate.review_path}?messages=class-{world.offering.pk}"
        assert Notification.objects.filter(trigger=CLASS_MESSAGE_TO_INSTRUCTORS).count() == 2

    def it_refuses_a_get_on_the_review_link_post_address():
        world = _World("post-get")
        assert world.gate is not None
        url = reverse("classes:class_review_message", kwargs={"token": world.gate.token})
        assert _client(world.lead).get(url).status_code == 405


def describe_posting_from_a_review_link():
    def it_takes_a_signed_out_message_as_the_reviewer():
        world = _World("tok-anon")
        assert world.gate is not None
        url = reverse("classes:class_review_message", kwargs={"token": world.gate.token})
        response = Client().post(url, {"body": "Zzfrom my phone"})
        assert response["Location"] == f"{world.gate.review_path}?messages=class-{world.offering.pk}"
        [message] = world.offering.thread()
        assert (message.author_id, message.is_from_staff) == (None, True)
        assert Notification.objects.filter(trigger=CLASS_MESSAGE_TO_INSTRUCTORS).count() == 2

    def it_refuses_a_signed_in_member_who_may_not_decide_the_guild_gate():
        world = _World("tok-plain")
        assert world.gate is not None
        url = reverse("classes:class_review_message", kwargs={"token": world.gate.token})
        assert _client(world.plain).post(url, {"body": "Zzhi"}).status_code == 404
        assert Client().post(reverse("classes:class_review_message", kwargs={"token": "nope"}), {}).status_code == 404

    def it_takes_a_signed_in_instructors_message_as_the_instructor():
        world = _World("tok-i")
        assert world.gate is not None
        url = reverse("classes:class_review_message", kwargs={"token": world.gate.token})
        _client(world.sam).post(url, {"body": "Zzme"})
        assert world.offering.thread()[0].is_from_staff is False


def describe_the_review_page():
    def it_shows_every_rounds_notes_in_messages_and_no_review_history(admin_user):
        world = _World("rev")
        assert world.gate is not None
        world.gate.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=world.lead.user, notes="Zzround one")
        world.offering.refresh_from_db()
        (gate,) = world.offering.submit_for_review()
        html = _client(world.lead).get(gate.review_path).content.decode()
        assert 'data-thread-open="class-' in html
        assert "Zzround one" in html
        assert "Review History" not in html
        assert reverse("classes:class_review_message", kwargs={"token": gate.token}) in html
        admin_html = (
            _client(admin_user)
            .get(reverse("classes:admin_class_review", kwargs={"pk": world.offering.pk}))
            .content.decode()
        )
        assert "Zzround one" in admin_html
        assert world.messages_url in admin_html
        assert Notification.objects.filter(trigger__startswith="class.message").count() == 0

    def it_shows_a_signed_out_review_link_the_thread():
        world = _World("rev-anon")
        assert world.gate is not None
        html = Client().get(world.gate.review_path).content.decode()
        assert "Write to the instructors" in html
        assert "Erin Horgan and Sam McKee get it in the app and by email" in html

    def it_hides_the_thread_from_a_signed_in_member_who_may_not_decide():
        world = _World("rev-plain")
        assert world.gate is not None
        html = _client(world.plain).get(world.gate.review_path).content.decode()
        assert "This review belongs to the guild lead." in html
        assert 'data-thread-open="class-' not in html

    def it_shows_the_admin_page_thread_only_to_someone_with_a_side():
        offering = ClassOfferingFactory(title="Zz bare", status=ClassOffering.Status.PUBLISHED)
        reviewer = _person("rev-bare", "Casey Approver")
        reviewer.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        html = _client(reviewer).get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk})).content
        assert b'data-thread-open="class-' in html
        assert b"Write to the instructor" in html


def describe_the_instructors_pages():
    def it_shows_messages_on_the_overview_to_every_instructor():
        world = _World("ovw")
        url = reverse("classes:teach_class_detail", kwargs={"pk": world.offering.pk})
        html = _client(world.sam).get(url).content.decode()
        assert 'id="messages"' in html
        assert "Write to the reviewers" in html
        assert f"Goes to the {world.guild.name} lead and Class Reviewers. You do not need to resubmit." in html
        assert "No messages yet. Ask the reviewers anything about this class, any time." in html
        assert f'name="next" value="{url}"' in html

    def it_marks_only_the_viewers_own_messages_as_you():
        world = _World("ovw-you")
        world.offering.post_message(author=world.erin.user, body="Zzerin wrote")
        html = _client(world.sam).get(reverse("classes:teach_class_detail", kwargs={"pk": world.offering.pk}))
        content = html.content.decode()
        assert "<strong>Erin Horgan</strong>" in content
        assert "<strong>You</strong>" not in content
        mine = _client(world.erin).get(reverse("classes:teach_class_detail", kwargs={"pk": world.offering.pk}))
        assert "<strong>You</strong>" in mine.content.decode()

    def it_shows_messages_under_where_your_class_is_on_the_composer():
        world = _World("cmp", submit=False)
        url = reverse("classes:teach_class_edit", kwargs={"pk": world.offering.pk})
        html = _client(world.erin).get(f"{url}?step=2").content.decode()
        assert html.index("Where Your Class Is") < html.index('id="messages"')
        assert 'data-thread-open="class-' in html
        assert f'name="next" value="{url}?step=2"' in html

    def it_lands_a_send_from_the_overview_after_a_refused_cancel_on_the_overview():
        world = _World("ovw-post", status=ClassOffering.Status.DRAFT)
        world.offering.status = ClassOffering.Status.PUBLISHED
        world.offering.save(update_fields=["status"])
        response = _client(world.erin).post(
            reverse("classes:teach_class_cancel", kwargs={"pk": world.offering.pk}), {"reason": ""}
        )
        url = reverse("classes:teach_class_detail", kwargs={"pk": world.offering.pk})
        assert f'name="next" value="{url}"' in response.content.decode()

    def it_shows_no_messages_on_a_new_class():
        teacher = _teacher("new-cls", "Erin Horgan")
        html = _client(teacher).get(reverse("classes:teach_class_create")).content.decode()
        assert 'data-thread-open="class-' not in html


def describe_class_lists():
    """No list of classes reads a thread: no per-class query for messages, audiences or writers."""

    def _queries(client: Client, url: str) -> int:
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        client.get(url)  # warm caches (site settings, sessions) so only the list varies
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(url).status_code == 200
        return len(ctx)

    def _classes_with_messages(world: _World, count: int) -> None:
        for n in range(count):
            offering = ClassOfferingFactory(
                title=f"Zz list {world.guild.pk} {n}",
                category=CategoryFactory(guild=world.guild),
                instructors=[world.erin],
            )
            offering.write_message(author=world.lead.user, body="Zzreviewer")
            offering.write_message(author=world.erin.user, body="Zzinstructor")

    def it_keeps_the_teach_dashboard_constant_from_one_class_to_six():
        world = _World("list-teach", submit=False)
        client = _client(world.erin)
        url = reverse("classes:teach_dashboard")
        world.offering.write_message(author=world.lead.user, body="Zzhi")
        one = _queries(client, url)
        _classes_with_messages(world, 5)
        assert _queries(client, url) == one

    def it_keeps_the_admin_class_list_constant_from_one_class_to_six(admin_user):
        world = _World("list-admin", submit=False)
        client = _client(admin_user)
        url = reverse("classes:admin_classes")
        world.offering.write_message(author=world.lead.user, body="Zzhi")
        one = _queries(client, url)
        _classes_with_messages(world, 5)
        assert _queries(client, url) == one


def describe_a_guild_that_does_not_review_the_class():
    """A guild with no lead does not review its classes, so its Class Reviewers get no side and no admin gate."""

    def _leadless(prefix: str) -> tuple[ClassOffering, Member, Member, Member]:
        reviewer = _person(f"{prefix}-rev", "Amber Capwell")
        guild = GuildFactory(name=f"Zz {prefix} Guild", guild_lead=None)
        GuildStaffMembershipFactory(
            guild=guild, member=reviewer, role=GuildStaffMembership.Role.CO_LEAD, can_review_classes=True
        )
        cms = _person(f"{prefix}-cms", "Casey Approver")
        cms.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        erin = _teacher(f"{prefix}-erin", "Erin Horgan")
        offering = ClassOfferingFactory(
            title=f"Zz {prefix} Mugs", category=CategoryFactory(guild=guild), instructors=[erin], ready=True
        )
        (gate,) = offering.submit_for_review()
        assert gate.role == ClassApproval.Role.ADMIN
        return offering, reviewer, cms, erin

    def it_gives_a_leadless_guilds_class_reviewer_no_side_no_link_and_no_messages():
        offering, reviewer, cms, erin = _leadless("leadless")
        url = reverse("classes:class_messages", kwargs={"pk": offering.pk})
        assert offering.message_side(reviewer.user) is None
        assert _client(reviewer).get(url).status_code == 404
        assert _client(reviewer).post(url, {"body": "Zzhi"}).status_code == 404
        offering.post_message(author=erin.user, body="Zzquestion")
        assert Notification.objects.filter(user=reviewer.user, trigger=CLASS_MESSAGE_TO_REVIEWERS).count() == 0
        assert Notification.objects.filter(user=cms.user, trigger=CLASS_MESSAGE_TO_REVIEWERS).count() == 1
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PENDING

    def it_never_hands_a_lead_who_arrived_after_submission_the_admin_gate():
        offering, _reviewer, _cms, erin = _leadless("late-lead")
        lead = _person("late-lead-jay", "Jay Holtman")
        guild = offering.category.guild
        guild.guild_lead = lead
        guild.save(update_fields=["guild_lead"])
        offering = ClassOffering.objects.get(pk=offering.pk)
        # The guild reviews the class now: the lead writes and hears as a reviewer...
        assert offering.message_side(lead.user) == ClassOffering.MESSAGE_SIDE_REVIEWER
        offering.post_message(author=erin.user, body="Zzquestion")
        assert Notification.objects.filter(user=lead.user, trigger=CLASS_MESSAGE_TO_REVIEWERS).count() == 1
        # ...but the only gate is the admin's, whose link decides for anyone: no landing on it.
        response = _client(lead).get(reverse("classes:class_messages", kwargs={"pk": offering.pk}))
        assert response.status_code == 404
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PENDING


def describe_a_spent_review_link_signed_out():
    def it_shows_and_takes_nothing_once_the_class_is_published(admin_user):
        world = _World("spent-pub")
        assert world.gate is not None
        world.gate.decide(ClassApproval.Decision.APPROVED, user=world.lead.user)
        world.offering.refresh_from_db()
        world.offering.approve(admin_user)
        world.offering.refresh_from_db()
        assert world.offering.status == ClassOffering.Status.PUBLISHED
        html = Client().get(world.gate.review_path).content.decode()
        assert 'data-thread-open="class-' not in html
        assert "class_review_message" not in html and "/messages/" not in html
        url = reverse("classes:class_review_message", kwargs={"token": world.gate.token})
        assert Client().post(url, {"body": "Zzlate"}).status_code == 404
        assert world.offering.messages.count() == 0

    def it_shows_and_takes_nothing_from_a_gate_decided_in_an_earlier_round(admin_user):
        world = _World("spent-round")
        assert world.gate is not None
        world.gate.decide(ClassApproval.Decision.APPROVED, user=world.lead.user)
        world.offering.refresh_from_db()
        admin_gate = world.offering.approvals.get(role=ClassApproval.Role.ADMIN)
        admin_gate.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=admin_user, notes="Zzfix it")
        world.offering.refresh_from_db()
        world.offering.submit_for_review()
        world.gate.refresh_from_db()
        assert world.gate.decision == ClassApproval.Decision.APPROVED
        assert world.gate.review_cycle_is_live is False
        assert 'data-thread-open="class-' not in Client().get(world.gate.review_path).content.decode()
        url = reverse("classes:class_review_message", kwargs={"token": world.gate.token})
        assert Client().post(url, {"body": "Zzlate"}).status_code == 404

    def it_keeps_a_bounced_drafts_link_live_for_its_own_bounce_only():
        world = _World("live-bounce")
        assert world.gate is not None
        world.gate.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=world.lead.user, notes="Zzmore photos")
        world.gate.refresh_from_db()
        assert world.gate.review_cycle_is_live is True
        assert "Zzmore photos" in Client().get(world.gate.review_path).content.decode()
        url = reverse("classes:class_review_message", kwargs={"token": world.gate.token})
        assert Client().post(url, {"body": "Zzsigned out"}).status_code == 302
        older = ClassApproval.objects.create(
            class_offering=world.offering,
            role=ClassApproval.Role.ADMIN,
            decision=ClassApproval.Decision.DENIED,
            decided_at=world.gate.decided_at - timedelta(days=1),
        )
        assert older.review_cycle_is_live is False

    def it_refuses_a_signed_in_member_with_no_side_even_on_an_admin_gate():
        offering = ClassOfferingFactory(title="Zz admin gate", ready=True)
        (gate,) = offering.submit_for_review()
        assert gate.role == ClassApproval.Role.ADMIN
        stranger = _person("admin-gate-x", "Out Sider")
        assert 'data-thread-open="class-' not in _client(stranger).get(gate.review_path).content.decode()
        url = reverse("classes:class_review_message", kwargs={"token": gate.token})
        assert _client(stranger).post(url, {"body": "Zzhi"}).status_code == 404
