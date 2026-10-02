"""The registrations lists link each class name to that class's own screen.

An admin asked "which class was this payment for" answers from the list: the title in the
admin Registrations tab and in the instructor's cross class Registrations page is a link to
``classes:teach_class_detail``, the same target the registration detail page already uses.
Assertions anchor on the URL, never on copy (the changelog renders on every page).
"""

from __future__ import annotations

from django.urls import reverse

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)


def _class_link(offering) -> str:
    return f'href="{reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})}"'


def describe_admin_registrations_list():
    def it_links_the_class_name_to_the_class_screen(admin_user, client, db):
        offering = ClassOfferingFactory(title="Blade Smithing Links")
        RegistrationFactory(class_offering=offering)
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_registrations")).content.decode()
        assert f"<td><a {_class_link(offering)}>Blade Smithing Links</a></td>" in html

    def it_links_only_the_classes_a_guild_lead_can_open(client, db):
        """A lead's list also holds the guild's other classes, whose screen 404s for them."""
        from tests.membership.factories import GuildFactory

        user = UserFactory(username="links-lead")
        lead = InstructorFactory(user=user, instructor_slug="links-lead")
        category = CategoryFactory(guild=GuildFactory(guild_lead=lead))
        others = ClassOfferingFactory(category=category, title="Someone Elses Links")
        own = ClassOfferingFactory(instructor=lead, title="The Leads Own Links")
        RegistrationFactory(class_offering=others)
        RegistrationFactory(class_offering=own)
        client.force_login(user)
        html = client.get(reverse("classes:admin_registrations")).content.decode()
        assert f"<td><a {_class_link(own)}>The Leads Own Links</a></td>" in html
        assert "<td>Someone Elses Links</td>" in html
        assert _class_link(others) not in html

    def it_does_not_link_for_an_instructor_without_the_teaching_grant(client, db):
        """Named as instructor but never granted teaching: the class screen refuses them."""
        user = UserFactory(username="links-ungranted")
        named = InstructorFactory(user=user, instructor_slug="links-ungranted", instructor_oriented_at=None)
        offering = ClassOfferingFactory(instructor=named, title="Ungranted Links")
        RegistrationFactory(class_offering=offering)
        client.force_login(user)
        html = client.get(reverse("classes:admin_registrations")).content.decode()
        assert "<td>Ungranted Links</td>" in html
        assert _class_link(offering) not in html


def describe_teach_registrations_page():
    def it_links_each_group_header_to_the_class_screen(client, db):
        user = UserFactory(username="links-teacher")
        member = InstructorFactory(user=user, instructor_slug="links-teacher")
        offering = ClassOfferingFactory(instructor=member, title="Forge Welding Links")
        RegistrationFactory(class_offering=offering)
        client.force_login(user)
        html = client.get(reverse("classes:teach_registrations")).content.decode()
        # @click.stop keeps the click on the name from toggling the collapsible group bar.
        assert f"<strong><a {_class_link(offering)} @click.stop>Forge Welding Links</a></strong>" in html
