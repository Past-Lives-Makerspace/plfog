"""End-to-end (#620): a member added from "Add a member" survives reopening the draft.

The added row is built in the browser when picked; on resume the server renders it back as a
checked row, so saving again keeps it and unchecking it drops it. Only a browser proves the
round trip, because the save posts whatever checkboxes are in the form. Run with
``pytest -m e2e``.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import AnnouncementDraft, Member
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MembershipPlanFactory

LEAD_EMAIL = "resume-lead@example.com"
EDITOR = '.pl-rte[data-rte-for="id_body"] .ql-editor'


def _member(email: str, name: str) -> Member:
    """A member whose login account has no name of its own (the name is on the Member), as in prod."""
    user = get_user_model().objects.create_user(username=email, email=email)
    member = Member.objects.get(user=user)
    member.full_legal_name = name
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["full_legal_name", "status"])
    return member


def _saved_users(pk: int) -> list[int]:
    return list((AnnouncementDraft.objects.get(pk=pk).recipient_selection or {}).get("users") or [])


def _save(page) -> None:
    with page.expect_response(lambda r: r.url.endswith(reverse("hub_compose_save_draft")) and r.status == 200):
        page.locator("[data-compose-save-draft]:visible").click()


def describe_resuming_a_draft_with_added_members():
    def it_keeps_an_added_member_through_add_save_reopen_save(live_server, page, login_via_code):
        MembershipPlanFactory()
        guild = GuildFactory(name="Resume Guild")
        roster = _member("rory@example.com", "Rory Roster")
        GuildMembershipFactory(guild=guild, member=roster)
        added = _member("ada@example.com", "Ada Added")

        login_via_code(LEAD_EMAIL)
        guild.guild_lead = Member.objects.get(user__username=LEAD_EMAIL)
        guild.save(update_fields=["guild_lead"])

        # Add: compose for the guild and pick Ada, who is not on its roster.
        page.goto(f"{live_server.url}{reverse('hub_compose')}?audience=guild:{guild.pk}")
        expect(page.locator(f'#compose-recipients input[value="user:{roster.user_id}"]')).to_be_attached()
        page.locator(EDITOR).fill("Open shop night moves to Thursday.")
        page.locator("#compose-add-member").select_option(f"user:{added.user_id}")
        added_row = page.locator(f'#compose-added-recipients input[value="user:{added.user_id}"]')
        expect(added_row).to_be_checked()

        # Save: the draft stores Ada next to the roster.
        _save(page)
        expect(page.locator("#compose-draft-pk")).not_to_have_value("")
        pk = int(page.locator("#compose-draft-pk").input_value())
        assert added.user_id in _saved_users(pk)

        # Reopen: Ada comes back as a checked, named row in the added area.
        page.goto(f"{live_server.url}{reverse('hub_compose_resume', args=[pk])}")
        expect(added_row).to_be_checked()
        expect(page.locator("#compose-added-recipients")).to_contain_text("Ada Added · ada@example.com")

        # Save again without changes: Ada is still on the draft.
        _save(page)
        assert added.user_id in _saved_users(pk)

        # Uncheck Ada and save: she is gone and nothing else changed.
        added_row.uncheck()
        _save(page)
        assert added.user_id not in _saved_users(pk)
