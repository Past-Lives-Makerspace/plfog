"""The registrations lists link each class name to that class's own screen.

An admin asked "which class was this payment for" answers from the list: the title in the
admin Registrations tab and in the instructor's cross class Registrations page is a link to
``classes:teach_class_detail``, the same target the registration detail page already uses.
Assertions anchor on the URL, never on copy (the changelog renders on every page).
"""

from __future__ import annotations

from django.urls import reverse

from classes.factories import ClassOfferingFactory, InstructorFactory, RegistrationFactory, UserFactory


def _class_link(offering) -> str:
    return f'href="{reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})}"'


def describe_admin_registrations_list():
    def it_links_the_class_name_to_the_class_screen(admin_user, client, db):
        offering = ClassOfferingFactory(title="Blade Smithing Links")
        RegistrationFactory(class_offering=offering)
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_registrations")).content.decode()
        assert f"<td><a {_class_link(offering)}>Blade Smithing Links</a></td>" in html


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
