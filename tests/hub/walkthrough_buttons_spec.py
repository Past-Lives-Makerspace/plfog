"""BDD specs for the guild staff walkthrough buttons (#1076, part 3).

One component, ``{% walkthrough_button %}``, on five surfaces (Guild Settings, Cubbies, Email
Members, Orientations, Reservations) and the guild page. Only someone who may edit that guild sees
it; Take starts the walkthrough on the page it was pressed on and is logged, and leaving a staff
walkthrough partway is logged as an exit.

Names are factory strings no changelog could contain (STANDARDS.md section 8).
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Guild, GuildStaffOnboardingEvent, Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

Action = GuildStaffOnboardingEvent.Action


def _member(username: str, **fields: object) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = f"{username.title()} Fernhollow"
    member.welcome_dismissed_at = timezone.now()
    for name, value in fields.items():
        setattr(member, name, value)
    member.save()
    return member


@pytest.fixture
def guild() -> Guild:
    return GuildFactory(name="Brambleworth Guild", is_active=True)


@pytest.fixture
def lead(guild: Guild) -> Member:
    member = _member("osric")
    guild.guild_lead = member
    guild.save(update_fields=["guild_lead"])
    return member


def _get(member: Member, url: str) -> str:
    client = Client()
    client.force_login(member.user)
    return client.get(url).content.decode()


SURFACES = (
    ("hub_guild_edit", "guild-lead"),
    ("hub_guild_edit", "guild-cubbies"),
    ("hub_guild_edit", "guild-reservations"),
    ("hub_guild_edit", "guild-email-members"),
    ("hub_guild_orientations", "guild-orientations"),
)


def describe_the_walkthrough_button():
    @pytest.mark.parametrize(("url_name", "tour_key"), SURFACES)
    def it_shows_each_tools_walkthrough_to_the_guilds_lead(lead: Member, guild: Guild, url_name: str, tour_key: str):
        page = _get(lead, reverse(url_name, args=[guild.pk]))
        assert f'data-walkthrough="{tour_key}"' in page

    def it_shows_on_the_guild_page_to_its_lead_and_an_admin(lead: Member, guild: Guild):
        url = reverse("hub_guild_detail", args=[guild.slug])
        assert 'data-walkthrough="guild-lead"' in _get(lead, url)
        admin = _member("hester", fog_role=Member.FogRole.ADMIN)
        assert 'data-walkthrough="guild-lead"' in _get(admin, url)

    def it_never_shows_to_an_ordinary_member_or_another_guilds_lead(guild: Guild):
        url = reverse("hub_guild_detail", args=[guild.slug])
        assert "data-walkthrough" not in _get(_member("pell"), url)
        other_lead = _member("ivo")
        GuildFactory(name="Mossgate Guild", guild_lead=other_lead)
        assert "data-walkthrough" not in _get(other_lead, url)

    def it_starts_on_the_page_it_sits_on(lead: Member, guild: Guild):
        page = _get(lead, f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=cubbies")
        form = page[page.index('data-walkthrough="guild-cubbies"') :]
        form = form[: form.index("</form>")]
        assert f'name="start" value="{reverse("hub_guild_edit", args=[guild.pk])}?tab=cubbies"' in form
        assert 'value="exit"' not in form

    @pytest.mark.parametrize(("url_name", "tour_key"), SURFACES)
    def it_starts_in_place_on_each_tools_own_page(lead: Member, guild: Guild, url_name: str, tour_key: str):
        url = reverse(url_name, args=[guild.pk])
        assert f'name="start" value="{url}"' in _start_form(_get(lead, url), tour_key)

    def it_starts_the_guild_page_walkthrough_on_that_guilds_settings(lead: Member, guild: Guild):
        page = _get(lead, reverse("hub_guild_detail", args=[guild.slug]))
        form = _start_form(page, "guild-lead")
        assert f'name="start" value="{reverse("hub_guild_edit", args=[guild.pk])}"' in form

    def it_starts_an_admin_on_the_guild_they_pressed_it_on(guild: Guild):
        earlier = GuildFactory(name="Aldergrove Guild", is_active=True)
        admin = _member("hester", fog_role=Member.FogRole.ADMIN)
        form = _start_form(_get(admin, reverse("hub_guild_detail", args=[guild.slug])), "guild-lead")
        assert f'name="start" value="{reverse("hub_guild_edit", args=[guild.pk])}"' in form
        assert reverse("hub_guild_edit", args=[earlier.pk]) not in form

    def it_runs_an_admins_tour_on_the_guild_whose_settings_are_open(guild: Guild):
        GuildFactory(name="Aldergrove Guild", is_active=True)
        admin = _member("hester", fog_role=Member.FogRole.ADMIN)
        url = reverse("hub_guild_edit", args=[guild.pk])
        page = _get(admin, f"{url}?tour=guild-lead")
        payload = page[page.index('id="pl-tour-data"') :]
        payload = json.loads(payload[payload.index(">") + 1 : payload.index("</script>")])
        assert {step["page_path"] for step in payload["steps"]} == {url}


def _start_form(page: str, tour_key: str) -> str:
    form = page[page.index(f'data-walkthrough="{tour_key}"') :]
    return form[: form.index("</form>")]


def describe_taking_a_walkthrough():
    def it_logs_the_take_and_runs_the_tour_where_it_was_pressed(lead: Member, guild: Guild):
        client = Client()
        client.force_login(lead.user)
        start = f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=cubbies"
        response = client.post(
            reverse("hub_walkthrough_choice"), {"tour_key": "guild-cubbies", "choice": "take", "start": start}
        )
        assert response["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=cubbies&tour=guild-cubbies"
        assert lead.staff_onboarding_events.filter(action=Action.TOUR_TAKEN, tour_key="guild-cubbies").count() == 1

    def it_falls_back_to_the_entry_page_for_a_start_on_another_host(lead: Member, guild: Guild):
        client = Client()
        client.force_login(lead.user)
        response = client.post(
            reverse("hub_walkthrough_choice"),
            {"tour_key": "guild-orientations", "choice": "take", "start": "https://elsewhere.example/x"},
        )
        assert response["Location"] == f"{reverse('hub_guild_orientations', args=[guild.pk])}?tour=guild-orientations"

    @pytest.mark.parametrize("start", ["http://testserver//elsewhere.example/x", "/\\elsewhere.example/x"])
    def it_never_starts_on_another_site(lead: Member, guild: Guild, start: str):
        client = Client()
        client.force_login(lead.user)
        response = client.post(
            reverse("hub_walkthrough_choice"), {"tour_key": "guild-cubbies", "choice": "take", "start": start}
        )
        assert response["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tour=guild-cubbies"


def describe_leaving_a_walkthrough_partway():
    def it_logs_an_exit_for_a_staff_walkthrough(lead: Member):
        client = Client()
        client.force_login(lead.user)
        response = client.post(reverse("hub_tour_state", args=["guild-reservations"]), {"status": "dismissed"})
        assert response.status_code == 204
        assert lead.staff_onboarding_events.filter(action=Action.TOUR_EXITED, tour_key="guild-reservations").exists()

    def it_logs_nothing_for_the_member_tour_or_a_finished_walkthrough(lead: Member):
        client = Client()
        client.force_login(lead.user)
        client.post(reverse("hub_tour_state", args=["member-welcome"]), {"status": "dismissed"})
        client.post(reverse("hub_tour_state", args=["guild-reservations"]), {"status": "completed"})
        assert not lead.staff_onboarding_events.exists()


def describe_no_auto_offer():
    def it_never_offers_a_tools_walkthrough_on_its_own_page(lead: Member, guild: Guild):
        from core.models import TourState

        page = _get(lead, reverse("hub_guild_orientations", args=[guild.pk]))
        assert "data-tour-accept" not in page
        assert TourState.objects.status_for(lead.user, "guild-orientations") is None
