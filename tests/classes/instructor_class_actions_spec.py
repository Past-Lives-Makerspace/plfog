"""BDD specs for the class's own instructor on their class (#816): Remind lead, Duplicate, and Delete in the composer.

The instructor gets Remind lead while their class waits on a guild lead gate, Duplicate at
every status (admins too, archived included), and Delete in the composer on their own
draft (the class page's ``_may_delete`` rule). Guild staff who do not teach the class and
are not admins get neither the buttons nor the endpoints.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassApproval, ClassOffering
from membership.models import GuildStaffMembership, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory

pytestmark = pytest.mark.django_db

Status = ClassOffering.Status


@pytest.fixture(autouse=True)
def _outbox() -> None:
    mail.outbox = []


def _teacher(slug: str, name: str) -> Member:
    user = UserFactory(username=f"{slug}@example.com", email=f"{slug}@example.com")
    return InstructorFactory(user=user, full_legal_name=name, instructor_slug=slug)


def _person(slug: str, name: str) -> Member:
    user = UserFactory(username=f"{slug}@example.com", email=f"{slug}@example.com")
    member = Member.objects.get(user=user)
    member.full_legal_name = name
    member.save(update_fields=["full_legal_name"])
    return member


class _Woodshop:
    """A guild with a lead and a plain staffer, and an instructor teaching a class in it."""

    def __init__(self) -> None:
        self.lead = _person("wlead", "Wanda Lead")
        self.staff = _person("wstaff", "Walt Staff")
        self.teacher = _teacher("wteach", "Tess Teacher")
        self.guild = GuildFactory(name="Woodshop Guild", guild_lead=self.lead)
        GuildStaffMembershipFactory(guild=self.guild, member=self.staff, role=GuildStaffMembership.Role.SECRETARY)
        self.category = CategoryFactory(guild=self.guild)

    def class_at(self, status: str, **kwargs: object) -> ClassOffering:
        return ClassOfferingFactory(instructor=self.teacher, category=self.category, status=status, **kwargs)

    def pending_on_lead(self) -> ClassOffering:
        offering = self.class_at(Status.PENDING)
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        return offering


@pytest.fixture
def shop() -> _Woodshop:
    return _Woodshop()


def _page(client: Client, member: Member, offering: ClassOffering) -> str:
    client.force_login(member.user)
    response = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _page_or_refusal(client: Client, member: Member, offering: ClassOffering) -> str:
    """The class page as ``member`` sees it; empty when the page itself refuses them (a 404)."""
    client.force_login(member.user)
    response = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk}))
    assert response.status_code in {200, 404}
    return response.content.decode() if response.status_code == 200 else ""


def _admin(slug: str = "wadmin") -> Member:
    member = _person(slug, "Ada Admin")
    Member.objects.filter(pk=member.pk).update(fog_role=Member.FogRole.ADMIN)
    member.refresh_from_db()
    return member


def describe_remind_lead_for_the_instructor():
    def _url(offering: ClassOffering) -> str:
        return reverse("classes:admin_class_remind_lead", kwargs={"pk": offering.pk})

    def it_shows_the_button_while_the_class_waits_on_a_guild_lead(client: Client, shop: _Woodshop):
        offering = shop.pending_on_lead()
        assert _url(offering) in _page(client, shop.teacher, offering)

    def it_hides_the_button_once_the_class_waits_on_an_admin_instead(client: Client, shop: _Woodshop):
        offering = shop.class_at(Status.PENDING)
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.APPROVED,
            decided_at=timezone.now(),
        )
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        assert _url(offering) not in _page(client, shop.teacher, offering)

    def it_hides_the_button_on_a_draft(client: Client, shop: _Woodshop):
        offering = shop.class_at(Status.DRAFT)
        assert _url(offering) not in _page(client, shop.teacher, offering)

    def it_reminds_the_same_people_an_admin_click_does_once_a_day(client: Client, shop: _Woodshop):
        offering = shop.pending_on_lead()
        client.force_login(shop.teacher.user)
        first = client.post(_url(offering))
        assert first.status_code == 204
        assert "Reminder sent to Wanda Lead." in first["HX-Trigger"]
        assert [m.to for m in mail.outbox] == [["wlead@example.com"]]
        second = client.post(_url(offering))
        assert "Already reminded today." in second["HX-Trigger"]
        assert len(mail.outbox) == 1

    def it_never_reminds_plain_staff(client: Client, shop: _Woodshop):
        offering = shop.pending_on_lead()
        client.force_login(shop.teacher.user)
        client.post(_url(offering))
        assert not [m for m in mail.outbox if m.to == ["wstaff@example.com"]]

    def describe_guild_staff_who_do_not_teach_the_class():
        def it_shows_them_no_button(client: Client, shop: _Woodshop):
            offering = shop.pending_on_lead()
            assert _url(offering) not in _page_or_refusal(client, shop.staff, offering)
            assert _url(offering) not in _page_or_refusal(client, shop.lead, offering)

        def it_refuses_their_post(client: Client, shop: _Woodshop):
            offering = shop.pending_on_lead()
            client.force_login(shop.staff.user)
            assert client.post(_url(offering)).status_code == 404
            client.force_login(shop.lead.user)
            assert client.post(_url(offering)).status_code == 404
            assert mail.outbox == []


def describe_duplicate():
    def _url(offering: ClassOffering) -> str:
        return reverse("classes:admin_class_duplicate", kwargs={"pk": offering.pk})

    @pytest.mark.parametrize(
        "status", [Status.DRAFT, Status.PENDING, Status.PUBLISHED, Status.CANCELLED, Status.ARCHIVED]
    )
    def it_shows_the_instructor_duplicate_at_every_status(client: Client, shop: _Woodshop, status: str):
        offering = shop.class_at(status)
        assert _url(offering) in _page(client, shop.teacher, offering)

    def it_shows_an_admin_duplicate_on_an_archived_class(client: Client, shop: _Woodshop):
        offering = shop.class_at(Status.ARCHIVED)
        assert _url(offering) in _page(client, _admin(), offering)

    def it_makes_the_instructor_a_draft_copy_they_own(client: Client, shop: _Woodshop):
        offering = shop.class_at(Status.PUBLISHED, published_at=timezone.now())
        client.force_login(shop.teacher.user)
        response = client.post(_url(offering))
        copy = ClassOffering.objects.exclude(pk=offering.pk).get(title=f"{offering.title} (copy)")
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:teach_class_edit", kwargs={"pk": copy.pk})
        assert copy.status == Status.DRAFT
        assert copy.instructor_list == [shop.teacher]
        assert client.get(response["Location"]).status_code == 200

    def it_keeps_a_co_instructor_on_the_copy_they_make(client: Client, shop: _Woodshop):
        partner = _teacher("wpartner", "Pat Partner")
        offering = shop.class_at(Status.PUBLISHED, published_at=timezone.now())
        offering.set_instructors([shop.teacher, partner])
        client.force_login(partner.user)
        client.post(_url(offering))
        copy = ClassOffering.objects.exclude(pk=offering.pk).get(title=f"{offering.title} (copy)")
        assert copy.instructor_list == [shop.teacher, partner]

    def it_keeps_every_instructor_on_an_admin_copy_too(client: Client, shop: _Woodshop):
        partner = _teacher("wpartner2", "Pat Partner")
        offering = shop.class_at(Status.DRAFT)
        offering.set_instructors([shop.teacher, partner])
        client.force_login(_admin().user)
        client.post(_url(offering))
        copy = ClassOffering.objects.exclude(pk=offering.pk).get(title=f"{offering.title} (copy)")
        assert copy.instructor_list == [shop.teacher, partner]

    def it_copies_an_archived_imported_class_into_a_fresh_draft(client: Client, shop: _Woodshop):
        offering = shop.class_at(
            Status.ARCHIVED,
            legacy_cms_id="node-816",
            channel_announced_at=timezone.now(),
            published_at=timezone.now(),
        )
        client.force_login(shop.teacher.user)
        assert client.post(_url(offering)).status_code == 302
        copy = ClassOffering.objects.exclude(pk=offering.pk).get(title=f"{offering.title} (copy)")
        assert copy.status == Status.DRAFT
        assert copy.legacy_cms_id == ""
        assert copy.channel_announced_at is None
        assert copy.published_at is None
        offering.refresh_from_db()
        assert offering.legacy_cms_id == "node-816"

    def describe_guild_staff_who_do_not_teach_the_class():
        def it_shows_them_no_button(client: Client, shop: _Woodshop):
            offering = shop.class_at(Status.DRAFT)
            assert _url(offering) not in _page_or_refusal(client, shop.staff, offering)

        def it_refuses_their_post(client: Client, shop: _Woodshop):
            offering = shop.class_at(Status.DRAFT)
            client.force_login(shop.staff.user)
            assert client.post(_url(offering)).status_code == 404
            assert ClassOffering.objects.count() == 1


def describe_delete_in_the_composer():
    def _composer(client: Client, member: Member, offering: ClassOffering) -> str:
        client.force_login(member.user)
        response = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        assert response.status_code == 200
        return response.content.decode()

    def _delete_url(offering: ClassOffering) -> str:
        return reverse("classes:admin_class_delete", kwargs={"pk": offering.pk})

    def it_shows_the_instructor_delete_on_their_draft(client: Client, shop: _Woodshop):
        offering = shop.class_at(Status.DRAFT)
        html = _composer(client, shop.teacher, offering)
        assert "data-delete-class" in html
        assert _delete_url(offering) in html
        assert "This permanently removes your draft and its dates." in html

    def it_deletes_the_draft_and_lands_on_the_teaching_dashboard(client: Client, shop: _Woodshop):
        offering = shop.class_at(Status.DRAFT)
        client.force_login(shop.teacher.user)
        response = client.post(_delete_url(offering))
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:teach_dashboard")
        assert not ClassOffering.objects.filter(pk=offering.pk).exists()

    def it_hides_delete_once_the_class_is_submitted(client: Client, shop: _Woodshop):
        offering = shop.pending_on_lead()
        assert "data-delete-class" not in _composer(client, shop.teacher, offering)

    def it_says_delete_is_unavailable_when_the_draft_has_registrations(client: Client, shop: _Woodshop):
        from classes.factories import RegistrationFactory

        offering = shop.class_at(Status.DRAFT)
        RegistrationFactory(class_offering=offering)
        html = _composer(client, shop.teacher, offering)
        assert "Delete unavailable (has registrations)" in html
        assert _delete_url(offering) not in html

    def it_shows_an_admin_delete_on_a_published_class(client: Client, shop: _Woodshop):
        offering = shop.class_at(Status.PUBLISHED, published_at=timezone.now())
        html = _composer(client, _admin(), offering)
        assert "data-delete-class" in html
        assert "This permanently removes the class and its sessions." in html

    def describe_guild_staff_who_do_not_teach_the_class():
        def it_shows_them_no_delete(client: Client, shop: _Woodshop):
            offering = shop.class_at(Status.DRAFT)
            html = _composer(client, shop.staff, offering)
            assert "data-delete-class" not in html
            assert _delete_url(offering) not in html

        def it_refuses_their_post(client: Client, shop: _Woodshop):
            offering = shop.class_at(Status.DRAFT)
            client.force_login(shop.staff.user)
            assert client.post(_delete_url(offering)).status_code == 404
            assert ClassOffering.objects.filter(pk=offering.pk).exists()
