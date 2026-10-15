"""BDD specs for Class Reviewers (#816): who a guild's class review reaches, and who may act on it.

A guild lead marks some staff as Class Reviewer. The review request, Remind lead, the two
review queues, the tokenized review page and the notification settings row all follow one
audience: the guild lead plus the staff marked Class Reviewer, leaving out the class's own
instructors unless one of them is the lead. Plain staff keep every other lead power.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.emails import class_has_reachable_reviewer, reminded_names, send_guild_lead_review_request
from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassApproval, ClassOffering
from core.events import resolvers, settings_matrix
from core.events.channels import Channel
from core.events.emit import EmitResult
from core.models import Notification
from membership.models import AdminCapability, Guild, GuildStaffMembership, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db

Role = GuildStaffMembership.Role


@pytest.fixture(autouse=True)
def _outbox() -> None:
    mail.outbox = []


def _person(slug: str, first: str, last: str) -> Member:
    """A member with a login and an email, named ``first last``."""
    user = UserFactory(username=f"{slug}@example.com", email=f"{slug}@example.com", first_name=first, last_name=last)
    member = Member.objects.get(user=user)
    member.full_legal_name = f"{first} {last}"
    member.save(update_fields=["full_legal_name"])
    return member


def _teacher(slug: str, first: str, last: str) -> Member:
    """A member with a login who can teach (teaching portal open, public instructor page)."""
    user = UserFactory(username=f"{slug}@example.com", email=f"{slug}@example.com", first_name=first, last_name=last)
    return InstructorFactory(user=user, full_legal_name=f"{first} {last}", instructor_slug=slug)


class _Ceramics:
    """The incident's shape: a lead, one Class Reviewer, one plain staff member, and a class."""

    def __init__(self) -> None:
        self.lead = _person("jay", "Jay", "Holtman")
        self.reviewer = _person("amber", "Amber", "Capwell")
        self.plain = _person("nicole", "Nicole", "Javaly")
        self.guild = GuildFactory(name="Ceramics Guild", guild_lead=self.lead)
        GuildStaffMembershipFactory(
            guild=self.guild, member=self.reviewer, role=Role.SECRETARY, can_review_classes=True
        )
        GuildStaffMembershipFactory(guild=self.guild, member=self.plain, role=Role.TREASURER)
        self.offering = ClassOfferingFactory(
            title="Make Two Mugs", status=ClassOffering.Status.PENDING, category=CategoryFactory(guild=self.guild)
        )
        self.gate = ClassApproval.objects.create(class_offering=self.offering, role=ClassApproval.Role.GUILD_LEAD)

    def taught_by(self, *teachers: Member) -> ClassOffering:
        self.offering.set_instructors(list(teachers))
        return self.offering


@pytest.fixture
def ceramics() -> _Ceramics:
    return _Ceramics()


def _review_emails(member: Member) -> list:
    return [m for m in mail.outbox if m.to == [member.user.email] and m.subject.startswith("Review request:")]


def _bells(member: Member) -> int:
    return Notification.objects.filter(trigger="class_review_requested", user=member.user).count()


def _reached(member: Member) -> bool:
    return bool(_review_emails(member)) or _bells(member) > 0


def describe_the_class_reviewer_flag():
    def it_starts_off_on_a_new_staff_row():
        row = GuildStaffMembershipFactory(role=Role.ORIENTER)
        row.refresh_from_db()
        assert row.can_review_classes is False

    def it_is_off_by_default_in_the_schema_so_the_migration_leaves_every_existing_row_off():
        field = GuildStaffMembership._meta.get_field("can_review_classes")
        assert field.default is False
        assert field.verbose_name == "Class Reviewer"

    def it_sets_every_row_a_member_holds_on_the_guild_at_once(ceramics: _Ceramics):
        GuildStaffMembershipFactory(guild=ceramics.guild, member=ceramics.plain, role=Role.ORIENTER)
        assert ceramics.guild.set_class_reviewer(ceramics.plain, True) == 2
        assert set(ceramics.plain.guild_staff_roles.values_list("can_review_classes", flat=True)) == {True}
        assert ceramics.guild.set_class_reviewer(ceramics.plain, False) == 2
        assert set(ceramics.plain.guild_staff_roles.values_list("can_review_classes", flat=True)) == {False}

    def it_touches_nothing_for_a_member_not_on_the_staff(ceramics: _Ceramics):
        assert ceramics.guild.set_class_reviewer(MemberFactory(), True) == 0

    def it_never_touches_the_same_member_on_another_guild(ceramics: _Ceramics):
        other = GuildFactory(name="Other Guild")
        GuildStaffMembershipFactory(guild=other, member=ceramics.plain, role=Role.SECRETARY)
        ceramics.guild.set_class_reviewer(ceramics.plain, True)
        assert GuildStaffMembership.objects.get(guild=other, member=ceramics.plain).can_review_classes is False


def describe_Guild_class_reviewers():
    def it_is_the_lead_then_the_class_reviewers_and_never_plain_staff(ceramics: _Ceramics):
        assert ceramics.guild.class_reviewers() == [ceramics.lead, ceramics.reviewer]

    def it_lists_a_reviewer_once_however_many_rows_they_hold(ceramics: _Ceramics):
        GuildStaffMembershipFactory(
            guild=ceramics.guild, member=ceramics.reviewer, role=Role.ORIENTER, can_review_classes=True
        )
        assert ceramics.guild.class_reviewers() == [ceramics.lead, ceramics.reviewer]

    def it_lists_a_lead_who_is_also_a_class_reviewer_once(ceramics: _Ceramics):
        GuildStaffMembershipFactory(
            guild=ceramics.guild, member=ceramics.lead, role=Role.CO_LEAD, can_review_classes=True
        )
        assert ceramics.guild.class_reviewers() == [ceramics.lead, ceramics.reviewer]

    def it_orders_class_reviewers_by_name_after_the_lead(ceramics: _Ceramics):
        aaron = _person("aaron", "Aaron", "Abbot")
        GuildStaffMembershipFactory(guild=ceramics.guild, member=aaron, role=Role.ORIENTER, can_review_classes=True)
        assert ceramics.guild.class_reviewers() == [ceramics.lead, aaron, ceramics.reviewer]

    def it_is_the_class_reviewers_alone_for_a_guild_with_no_lead():
        reviewer = _person("solo", "Solo", "Reviewer")
        guild = GuildFactory(name="Leaderless", guild_lead=None)
        GuildStaffMembershipFactory(guild=guild, member=reviewer, role=Role.SECRETARY, can_review_classes=True)
        assert guild.class_reviewers() == [reviewer]

    def describe_with_a_class():
        def it_leaves_out_a_class_reviewer_who_teaches_it(ceramics: _Ceramics):
            offering = ceramics.taught_by(ceramics.reviewer)
            assert ceramics.guild.class_reviewers(offering) == [ceramics.lead]

        def it_keeps_a_lead_who_teaches_it(ceramics: _Ceramics):
            offering = ceramics.taught_by(ceramics.lead)
            assert ceramics.guild.class_reviewers(offering) == [ceramics.lead, ceramics.reviewer]

    def describe_reviews_classes():
        def it_answers_yes_for_the_lead_and_a_class_reviewer(ceramics: _Ceramics):
            assert ceramics.guild.reviews_classes(ceramics.lead) is True
            assert ceramics.guild.reviews_classes(ceramics.reviewer) is True

        def it_answers_no_for_plain_staff_and_strangers(ceramics: _Ceramics):
            assert ceramics.guild.reviews_classes(ceramics.plain) is False
            assert ceramics.guild.reviews_classes(MemberFactory()) is False

        def it_answers_no_for_a_class_reviewer_who_teaches_the_class(ceramics: _Ceramics):
            offering = ceramics.taught_by(ceramics.reviewer)
            assert ceramics.guild.reviews_classes(ceramics.reviewer, offering) is False

        def it_answers_yes_for_a_lead_who_teaches_the_class(ceramics: _Ceramics):
            offering = ceramics.taught_by(ceramics.lead)
            assert ceramics.guild.reviews_classes(ceramics.lead, offering) is True

    def describe_Member_class_reviewed_guilds():
        def it_holds_led_guilds_and_guilds_where_the_member_reviews(ceramics: _Ceramics):
            assert list(ceramics.lead.class_reviewed_guilds) == [ceramics.guild]
            assert list(ceramics.reviewer.class_reviewed_guilds) == [ceramics.guild]

        def it_leaves_out_guilds_where_the_member_is_plain_staff(ceramics: _Ceramics):
            assert list(ceramics.plain.class_reviewed_guilds) == []


def describe_the_review_request_audience():
    def describe_the_resolver():
        def it_reaches_the_lead_and_the_class_reviewer(ceramics: _Ceramics):
            found = resolvers.guild_leadership_or_class_approvers(
                {"guild": ceramics.guild, "offering": ceramics.offering}
            )
            assert [(user.pk, reason) for user, reason in found] == [
                (ceramics.lead.user_id, "guild_class_reviewer"),
                (ceramics.reviewer.user_id, "guild_class_reviewer"),
            ]

        def it_leaves_out_a_class_reviewer_who_teaches_the_class(ceramics: _Ceramics):
            offering = ceramics.taught_by(ceramics.reviewer)
            found = resolvers.guild_class_reviewers({"guild": ceramics.guild, "offering": offering})
            assert [user.pk for user, _ in found] == [ceramics.lead.user_id]

        def it_resolves_nobody_for_a_null_guild():
            assert resolvers.guild_class_reviewers({"guild": None}) == []

        def it_fails_loudly_without_the_offering(ceramics: _Ceramics):
            with pytest.raises(KeyError):
                resolvers.guild_leadership_or_class_approvers({"guild": ceramics.guild})

    def describe_on_submission():
        def it_sends_the_request_to_the_lead_and_the_class_reviewer_only(ceramics: _Ceramics):
            send_guild_lead_review_request(ceramics.offering, ceramics.gate)
            assert len(_review_emails(ceramics.lead)) == 1
            assert len(_review_emails(ceramics.reviewer)) == 1
            assert _bells(ceramics.lead) == 1
            assert _bells(ceramics.reviewer) == 1
            assert _reached(ceramics.plain) is False

        def it_sends_nothing_to_a_class_reviewer_who_teaches_the_class(ceramics: _Ceramics):
            teacher = _teacher("erin", "Erin", "Horgan")
            GuildStaffMembershipFactory(
                guild=ceramics.guild, member=teacher, role=Role.SECRETARY, can_review_classes=True
            )
            offering = ceramics.taught_by(teacher)
            send_guild_lead_review_request(offering, ceramics.gate)
            assert _reached(teacher) is False
            assert _reached(ceramics.lead) is True
            assert _reached(ceramics.reviewer) is True

        def it_still_sends_to_a_lead_who_teaches_the_class(ceramics: _Ceramics):
            offering = ceramics.taught_by(ceramics.lead)
            send_guild_lead_review_request(offering, ceramics.gate)
            assert len(_review_emails(ceramics.lead)) == 1
            assert _bells(ceramics.lead) == 1


def describe_remind_lead():
    @pytest.fixture
    def admin_client(client: Client) -> Client:
        admin = UserFactory(username="admin816@example.com", is_staff=True, is_superuser=True)
        Member.objects.filter(user=admin).update(fog_role=Member.FogRole.ADMIN)
        client.force_login(admin)
        return client

    def _remind(client: Client, offering: ClassOffering) -> str:
        response = client.post(reverse("classes:admin_class_remind_lead", kwargs={"pk": offering.pk}))
        assert response.status_code == 204
        return response["HX-Trigger"]

    def it_reminds_the_lead_and_the_class_reviewer_and_names_both(admin_client: Client, ceramics: _Ceramics):
        toast = _remind(admin_client, ceramics.offering)
        assert "Reminder sent to Jay Holtman and Amber Capwell." in toast
        assert len(_review_emails(ceramics.lead)) == 1
        assert len(_review_emails(ceramics.reviewer)) == 1
        assert _reached(ceramics.plain) is False

    def it_names_every_class_reviewer_reminded(admin_client: Client, ceramics: _Ceramics):
        GuildStaffMembershipFactory(guild=ceramics.guild, member=ceramics.plain, role=Role.ORIENTER)
        ceramics.guild.set_class_reviewer(ceramics.plain, True)
        toast = _remind(admin_client, ceramics.offering)
        assert "Reminder sent to Jay Holtman, Amber Capwell and Nicole Javaly." in toast

    def it_leaves_out_a_class_reviewer_who_teaches_the_class(admin_client: Client, ceramics: _Ceramics):
        ceramics.taught_by(ceramics.reviewer)
        toast = _remind(admin_client, ceramics.offering)
        assert "Reminder sent to Jay Holtman." in toast
        assert _reached(ceramics.reviewer) is False

    def it_refuses_when_the_lead_cannot_be_reached_and_the_only_class_reviewer_teaches_it(admin_client: Client):
        ghost_lead = MemberFactory(_pre_signup_email="")
        teacher = _teacher("only", "Only", "Reviewer")
        guild = GuildFactory(name="Unreachable", guild_lead=ghost_lead)
        GuildStaffMembershipFactory(guild=guild, member=teacher, role=Role.SECRETARY, can_review_classes=True)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING, category=CategoryFactory(guild=guild))
        offering.set_instructors([teacher])
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        assert class_has_reachable_reviewer(offering) is False
        assert "Review it yourself." in _remind(admin_client, offering)
        assert mail.outbox == []

    def it_refuses_when_the_lead_cannot_be_reached_and_only_plain_staff_remain(admin_client: Client):
        guild = GuildFactory(name="Plain Staff Only", guild_lead=MemberFactory(_pre_signup_email=""))
        GuildStaffMembershipFactory(guild=guild, member=_person("plainonly", "Plain", "Only"), role=Role.TREASURER)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING, category=CategoryFactory(guild=guild))
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        assert "Review it yourself." in _remind(admin_client, offering)
        assert mail.outbox == []

    def describe_the_admin_queue():
        def it_offers_review_it_yourself_when_nobody_can_be_reminded(admin_client: Client):
            from classes.views import _with_guild_leads_queue

            guild = GuildFactory(name="Queue Unreachable", guild_lead=MemberFactory(_pre_signup_email=""))
            GuildStaffMembershipFactory(guild=guild, member=_person("plainq", "Plain", "Queue"), role=Role.TREASURER)
            offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING, category=CategoryFactory(guild=guild))
            ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
            rows = {row["offering"].pk: row for row in _with_guild_leads_queue(timezone.now())}
            assert rows[offering.pk]["leadless"] is True

        def it_offers_remind_lead_when_a_class_reviewer_can_be_reached(admin_client: Client, ceramics: _Ceramics):
            from classes.views import _with_guild_leads_queue

            rows = {row["offering"].pk: row for row in _with_guild_leads_queue(timezone.now())}
            assert rows[ceramics.offering.pk]["leadless"] is False

    def describe_reminded_names():
        def it_names_each_person_once_in_delivery_order(ceramics: _Ceramics):
            result = EmitResult(
                event_key="class_review_requested",
                activity=None,
                recipient_count=2,
                delivered=[
                    (ceramics.reviewer.user_id, Channel.EMAIL),
                    (ceramics.lead.user_id, Channel.EMAIL),
                    (ceramics.reviewer.user_id, Channel.IN_APP),
                ],
                skipped_duplicates=[(ceramics.plain.user_id, Channel.EMAIL)],
                released=[],
            )
            assert reminded_names(result) == ["Amber Capwell", "Jay Holtman"]


def describe_review_queues():
    def describe_awaiting_guild_lead():
        def it_lists_the_class_for_the_lead_and_the_class_reviewer(ceramics: _Ceramics):
            assert list(ClassOffering.objects.awaiting_guild_lead(ceramics.lead)) == [ceramics.offering]
            assert list(ClassOffering.objects.awaiting_guild_lead(ceramics.reviewer)) == [ceramics.offering]

        def it_lists_nothing_for_plain_staff(ceramics: _Ceramics):
            assert list(ClassOffering.objects.awaiting_guild_lead(ceramics.plain)) == []

        def it_lists_nothing_for_a_class_reviewer_who_teaches_the_class(ceramics: _Ceramics):
            ceramics.taught_by(ceramics.reviewer)
            assert list(ClassOffering.objects.awaiting_guild_lead(ceramics.reviewer)) == []

        def it_still_lists_the_class_for_a_lead_who_teaches_it(ceramics: _Ceramics):
            ceramics.taught_by(ceramics.lead)
            assert list(ClassOffering.objects.awaiting_guild_lead(ceramics.lead)) == [ceramics.offering]

        def it_keeps_a_reviewer_who_teaches_in_another_guild(ceramics: _Ceramics):
            elsewhere = ClassOfferingFactory(category=CategoryFactory(guild=GuildFactory(name="Elsewhere")))
            elsewhere.set_instructors([ceramics.reviewer])
            assert list(ClassOffering.objects.awaiting_guild_lead(ceramics.reviewer)) == [ceramics.offering]

    def describe_the_teaching_dashboard():
        def _overview(client: Client, member: Member) -> str:
            client.force_login(member.user)
            response = client.get(reverse("classes:teach_overview"))
            assert response.status_code == 200
            return response.content.decode()

        def it_shows_the_class_to_a_class_reviewer_who_leads_no_guild(client: Client, ceramics: _Ceramics):
            reviewer = _teacher("teachrev", "Teach", "Reviewer")
            GuildStaffMembershipFactory(
                guild=ceramics.guild, member=reviewer, role=Role.ORIENTER, can_review_classes=True
            )
            html = _overview(client, reviewer)
            assert "Waiting on Your Review" in html
            assert reverse("classes:class_review", kwargs={"token": ceramics.gate.token}) in html

        def it_hides_the_class_from_plain_staff(client: Client, ceramics: _Ceramics):
            staff = _teacher("teachplain", "Teach", "Plain")
            GuildStaffMembershipFactory(guild=ceramics.guild, member=staff, role=Role.ORIENTER)
            html = _overview(client, staff)
            assert "Waiting on Your Review" not in html
            assert reverse("classes:class_review", kwargs={"token": ceramics.gate.token}) not in html

    def describe_guild_settings_needs_attention():
        def _settings_page(client: Client, member: Member, guild: Guild) -> str:
            client.force_login(member.user)
            response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
            assert response.status_code == 200
            return response.content.decode()

        def it_lists_the_class_for_the_lead_and_the_class_reviewer(client: Client, ceramics: _Ceramics):
            review_url = reverse("classes:class_review", kwargs={"token": ceramics.gate.token})
            assert review_url in _settings_page(client, ceramics.lead, ceramics.guild)
            assert review_url in _settings_page(client, ceramics.reviewer, ceramics.guild)

        def it_does_not_list_the_class_for_plain_staff(client: Client, ceramics: _Ceramics):
            html = _settings_page(client, ceramics.plain, ceramics.guild)
            assert reverse("classes:class_review", kwargs={"token": ceramics.gate.token}) not in html
            assert "Waiting on Your Review" not in html


def describe_the_tokenized_review_page():
    def _url(gate: ClassApproval) -> str:
        return reverse("classes:class_review", kwargs={"token": gate.token})

    def _decide(client: Client, gate: ClassApproval) -> None:
        client.post(_url(gate), {"decision": ClassApproval.Decision.CHANGES_REQUESTED, "notes": "Tighten the title."})
        gate.refresh_from_db()

    def describe_a_signed_in_plain_staff_member():
        def it_shows_the_class_with_a_notice_instead_of_the_form(client: Client, ceramics: _Ceramics):
            client.force_login(ceramics.plain.user)
            html = client.get(_url(ceramics.gate)).content.decode()
            assert "data-review-not-yours" in html
            assert 'name="decision"' not in html

        @pytest.mark.parametrize(
            "decision",
            [
                ClassApproval.Decision.APPROVED,
                ClassApproval.Decision.CHANGES_REQUESTED,
                ClassApproval.Decision.DENIED,
            ],
        )
        def it_records_no_decision(client: Client, ceramics: _Ceramics, decision: str):
            client.force_login(ceramics.plain.user)
            response = client.post(_url(ceramics.gate), {"decision": decision, "notes": "Looks fine."})
            assert response.status_code == 200
            ceramics.gate.refresh_from_db()
            assert ceramics.gate.decision == ""
            ceramics.offering.refresh_from_db()
            assert ceramics.offering.status == ClassOffering.Status.PENDING

    def it_refuses_a_class_reviewer_who_teaches_the_class(client: Client, ceramics: _Ceramics):
        ceramics.taught_by(ceramics.reviewer)
        client.force_login(ceramics.reviewer.user)
        _decide(client, ceramics.gate)
        assert ceramics.gate.decision == ""

    def it_refuses_a_signed_in_user_with_no_member(client: Client, ceramics: _Ceramics):
        user = User.objects.create_user(username="nomember816", email="nomember816@example.com")
        Member.objects.filter(user=user).delete()
        client.force_login(user)
        _decide(client, ceramics.gate)
        assert ceramics.gate.decision == ""

    def it_lets_an_anonymous_visitor_with_the_token_decide(client: Client, ceramics: _Ceramics):
        _decide(client, ceramics.gate)
        assert ceramics.gate.decision == ClassApproval.Decision.CHANGES_REQUESTED
        assert ceramics.gate.decided_by is None

    def it_lets_the_lead_decide_signed_in(client: Client, ceramics: _Ceramics):
        client.force_login(ceramics.lead.user)
        _decide(client, ceramics.gate)
        assert ceramics.gate.decision == ClassApproval.Decision.CHANGES_REQUESTED

    def it_lets_a_class_reviewer_decide_signed_in(client: Client, ceramics: _Ceramics):
        client.force_login(ceramics.reviewer.user)
        _decide(client, ceramics.gate)
        assert ceramics.gate.decided_by == ceramics.reviewer.user

    def it_lets_an_admin_decide(client: Client, ceramics: _Ceramics):
        admin = _person("fogadmin", "Fog", "Admin")
        Member.objects.filter(pk=admin.pk).update(fog_role=Member.FogRole.ADMIN)
        client.force_login(admin.user)
        _decide(client, ceramics.gate)
        assert ceramics.gate.decision == ClassApproval.Decision.CHANGES_REQUESTED

    def it_lets_a_cms_administrator_decide(client: Client, ceramics: _Ceramics):
        approver = _person("cmsadmin", "Cms", "Admin")
        approver.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        client.force_login(approver.user)
        _decide(client, ceramics.gate)
        assert ceramics.gate.decision == ClassApproval.Decision.CHANGES_REQUESTED

    def it_lets_a_superuser_decide(client: Client, ceramics: _Ceramics):
        root = UserFactory(username="root816@example.com", is_superuser=True, is_staff=True)
        client.force_login(root)
        _decide(client, ceramics.gate)
        assert ceramics.gate.decision == ClassApproval.Decision.CHANGES_REQUESTED

    def describe_ClassApproval_may_be_decided_by():
        def it_reads_the_guild_the_gate_was_opened_for(ceramics: _Ceramics):
            other_lead = _person("otherlead", "Other", "Lead")
            other = GuildFactory(name="Asked Guild", guild_lead=other_lead)
            ClassApproval.objects.filter(pk=ceramics.gate.pk).update(opened_for_guild=other)
            ceramics.gate.refresh_from_db()
            assert ceramics.gate.may_be_decided_by(other_lead.user) is True
            assert ceramics.gate.may_be_decided_by(ceramics.lead.user) is False

        def it_lets_the_lead_decide_a_gate_opened_for_their_guild(ceramics: _Ceramics):
            other_lead = _person("otherlead2", "Other", "Lead")
            GuildFactory(name="Unasked Guild", guild_lead=other_lead)
            assert ceramics.gate.may_be_decided_by(ceramics.lead.user) is True
            assert ceramics.gate.may_be_decided_by(other_lead.user) is False

        def it_falls_back_to_the_class_guild_for_a_row_with_no_stamped_guild(ceramics: _Ceramics):
            ClassApproval.objects.filter(pk=ceramics.gate.pk).update(opened_for_guild=None)
            ceramics.gate.refresh_from_db()
            assert ceramics.gate.may_be_decided_by(ceramics.reviewer.user) is True
            assert ceramics.gate.may_be_decided_by(ceramics.plain.user) is False

        def it_lets_the_token_decide_when_the_gate_has_no_guild_at_all():
            offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING, category=CategoryFactory(guild=None))
            gate = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
            assert gate.may_be_decided_by(_person("anyone", "Any", "One").user) is True

        def it_leaves_the_admin_gate_unchanged(ceramics: _Ceramics):
            admin_gate = ClassApproval.objects.create(class_offering=ceramics.offering, role=ClassApproval.Role.ADMIN)
            assert admin_gate.may_be_decided_by(ceramics.plain.user) is True

        def it_allows_nobody_signed_in(ceramics: _Ceramics):
            assert ceramics.gate.may_be_decided_by(None) is True


def describe_notification_settings():
    def _shows_class_review(member: Member) -> bool:
        rows = [
            row
            for section in settings_matrix.build_matrix(member.user)
            for block in section.blocks
            for row in block.rows
        ]
        return any(row.event_key == "class_review_requested" for row in rows)

    def it_shows_the_class_review_row_to_the_lead(ceramics: _Ceramics):
        assert _shows_class_review(ceramics.lead) is True

    def it_shows_the_class_review_row_to_a_class_reviewer(ceramics: _Ceramics):
        assert _shows_class_review(ceramics.reviewer) is True

    def it_hides_the_class_review_row_from_plain_staff(ceramics: _Ceramics):
        assert _shows_class_review(ceramics.plain) is False

    def it_files_a_class_reviewer_row_under_guild_leadership(ceramics: _Ceramics):
        headings = [
            block.heading
            for section in settings_matrix.build_matrix(ceramics.reviewer.user)
            for block in section.blocks
            if any(row.event_key == "class_review_requested" for row in block.rows)
        ]
        assert headings == ["Guild leadership"]
