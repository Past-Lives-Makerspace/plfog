"""End-to-end: a manager builds the CNC Machine's ways to qualify on Manage > Details (#747).

The editor's browser half: "+ Add a Way to Qualify" clones an empty way, ticking pills rewrites
its summary line, Remove on an unsaved way drops only that card and renumbers the rest (and the
posted forms stay contiguous, so the Save still lands), Delete on a saved way saves the page
without it, and "+ New Orientation" makes the new type a way of its own in the same Save. Run
with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import EquipmentFactory, GuildFactory, MembershipPlanFactory, OrientationTypeFactory

MANAGER_EMAIL = "equipment-ways-editor@example.com"


def describe_the_ways_to_qualify_editor():
    def it_adds_removes_saves_and_deletes_ways(live_server, page, login_via_code):
        MembershipPlanFactory()  # so the user signal provisions the member
        user = User.objects.create_user(username=MANAGER_EMAIL, email=MANAGER_EMAIL)
        member = user.member
        member.status = Member.Status.ACTIVE
        member.fog_role = Member.FogRole.ADMIN
        member.save(update_fields=["status", "fog_role"])
        member.sync_user_permissions()
        guild = GuildFactory(name="Woodshop")
        full = OrientationTypeFactory(guild=guild, name="CNC Machine Orientation", duration_minutes=360)
        first = OrientationTypeFactory(guild=guild, name="Session 1 of 2", duration_minutes=180)
        second = OrientationTypeFactory(guild=guild, name="Session 2 of 2", duration_minutes=180)
        cnc = EquipmentFactory(name="CNC Machine", guild=guild)

        login_via_code(MANAGER_EMAIL)
        page.set_viewport_size({"width": 1100, "height": 900})
        page.goto(f"{live_server.url}{reverse('hub_equipment_manage', args=[cnc.slug])}?tab=details")
        editor = page.locator("[data-ways-editor]")
        editor.get_by_text("Any active member can reserve the CNC Machine. Add a way to require one.").wait_for()

        editor.get_by_role("button", name="+ Add a Way to Qualify").click()
        ways = editor.locator("[data-way-row]")
        ways.nth(0).get_by_text("Way 1").wait_for()
        ways.nth(0).get_by_label("Session 1 of 2").check()
        ways.nth(0).get_by_label("Session 2 of 2").check()
        ways.nth(0).locator("[data-way-summary]").get_by_text("Both Session 1 of 2 and Session 2 of 2").wait_for()

        add_another = editor.get_by_role("button", name="+ Add Another Way")
        add_another.click()
        add_another.click()
        ways.nth(2).get_by_text("Way 3").wait_for()
        ways.nth(1).get_by_role("button", name="Remove").click()
        assert ways.count() == 2
        ways.nth(1).get_by_text("Way 2").wait_for()
        ways.nth(1).get_by_label("CNC Machine Orientation").check()
        ways.nth(1).locator("[data-way-summary]").get_by_text("CNC Machine Orientation").wait_for()

        page.get_by_role("button", name="Save", exact=True).click()
        # The saved page shows Delete on both ways: the observable that the Save landed.
        editor.locator("[data-way-delete]").nth(1).wait_for()
        assert cnc.unlocking_ways() == [[first, second], [full]]

        ways.nth(0).get_by_role("button", name="Delete").click()
        editor.locator("[data-way-delete]").nth(1).wait_for(state="detached")
        assert cnc.unlocking_ways() == [[full]]

        # "+ New Orientation" reveals the new type's fields and posts its flag; Save makes it a way of its own.
        editor.get_by_role("button", name="+ New Orientation").click()
        editor.get_by_label("Orientation name").fill("CNC Team Orientation")
        page.get_by_role("button", name="Save", exact=True).click()
        editor.locator("[data-way-delete]").nth(1).wait_for()
        own = cnc.owned_orientation_types.get()
        assert own.name == "CNC Team Orientation"
        assert cnc.unlocking_ways() == [[full], [own]]
