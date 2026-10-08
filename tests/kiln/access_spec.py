"""Who may use kiln tickets: the crew, makers, and the guest gate (#691)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse

from core.models import SiteConfiguration
from kiln import access
from kiln.access import (
    KilnNav,
    can_file_tickets,
    crew_required,
    is_crew,
    maker_required,
    maker_type_for,
    shows_kiln_nav,
)
from kiln.models import KilnTicket
from membership.models import Member
from tests.kiln.conftest import signed_in
from tests.membership.factories import GuildFactory, GuildMembershipFactory, GuildStaffMembershipFactory

pytestmark = pytest.mark.django_db

MINE = reverse("kiln:mine")
GUEST = Member.Status.GUEST
FORMER = Member.Status.FORMER
SUSPENDED = Member.Status.SUSPENDED


@pytest.fixture
def suspensions_lock_out(db):
    config = SiteConfiguration.load()
    config.suspended_members_locked_out = True
    config.save()


def describe_kiln_guild():
    def it_finds_the_guild_by_its_slug(kiln_guild):
        assert access.kiln_guild() == kiln_guild

    def it_is_none_without_that_guild(db):
        assert access.kiln_guild() is None

    def it_follows_the_setting(settings, db):
        other = GuildFactory(name="Glass Guild", slug="glass-guild")
        settings.KILN_GUILD_SLUG = "glass-guild"

        assert access.kiln_guild() == other


def describe_is_crew():
    def it_admits_the_lead_and_staff_rows(kiln_guild, make_member):
        lead, staff, member = make_member(), make_member(), make_member()
        kiln_guild.guild_lead = lead
        kiln_guild.save(update_fields=["guild_lead"])
        GuildStaffMembershipFactory(guild=kiln_guild, member=staff)
        GuildMembershipFactory(guild=kiln_guild, member=member)

        assert is_crew(lead) and is_crew(staff, kiln_guild)
        assert not is_crew(member)

    def it_ignores_staff_of_another_guild(kiln_guild, make_member):
        other_staff = make_member()
        GuildStaffMembershipFactory(guild=GuildFactory(), member=other_staff)

        assert not is_crew(other_staff)

    def it_has_no_crew_without_the_guild_or_a_member(make_member):
        assert not is_crew(make_member())
        assert not is_crew(None)

    def it_gives_admins_no_override(kiln_guild, make_member):
        assert not is_crew(make_member(fog_role=Member.FogRole.ADMIN))


def describe_who_files_tickets():
    @pytest.mark.parametrize("status", [Member.Status.ACTIVE, Member.Status.INVITED, SUSPENDED, GUEST])
    def it_admits_members_the_portal_admits_and_guests(make_member, status):
        assert can_file_tickets(make_member(status=status))

    def it_refuses_a_former_member_and_nobody(make_member):
        assert not can_file_tickets(make_member(status=FORMER))
        assert not can_file_tickets(None)


def describe_maker_type():
    def it_records_staff_guests_and_members(kiln_guild, make_member):
        staff, guest, member = make_member(), make_member(status=GUEST), make_member()
        GuildStaffMembershipFactory(guild=kiln_guild, member=staff)

        assert maker_type_for(staff) == KilnTicket.MakerType.STAFF
        assert maker_type_for(guest) == KilnTicket.MakerType.STUDENT
        assert maker_type_for(member) == KilnTicket.MakerType.MEMBER
        assert KilnTicket.MakerType.STUDENT.label == "Student or guest"


def describe_sidebar_entry():
    def it_shows_for_guild_members_staff_and_lead(kiln_guild, make_member):
        joined, staff, lead = make_member(), make_member(), make_member()
        GuildMembershipFactory(guild=kiln_guild, member=joined)
        GuildStaffMembershipFactory(guild=kiln_guild, member=staff)
        kiln_guild.guild_lead = lead
        kiln_guild.save(update_fields=["guild_lead"])

        assert all(shows_kiln_nav(m) for m in (joined, staff, lead))

    def it_hides_for_everyone_else(kiln_guild, make_member):
        outsider = make_member()
        GuildMembershipFactory(guild=GuildFactory(), member=outsider)

        assert not shows_kiln_nav(outsider)

    def it_computes_once_and_only_when_read(make_member):
        nav = KilnNav(make_member())

        with patch("kiln.access.shows_kiln_nav", return_value=True) as check:
            assert nav.show and nav.show
        assert check.call_count == 1

    def it_is_off_for_nobody_and_marks_guests_guest_only(make_member, settings):
        settings.KILN_GUILD_SLUG = "ceramics-guild"
        nobody = KilnNav(None)

        assert not nobody.show and not nobody.guest_only
        assert nobody.guild_slug == "ceramics-guild"
        assert KilnNav(make_member(status=GUEST)).guest_only
        assert not KilnNav(make_member()).guest_only


def describe_decorators():
    def _request(member: Member | None):
        request = RequestFactory().get("/kiln/")
        request.user = member.user if member is not None else AnonymousUser()
        return request

    def _ok(request):
        return HttpResponse("ok")

    def it_lets_a_maker_through_and_stops_anyone_else(make_member):
        assert maker_required(_ok)(_request(make_member())).content == b"ok"
        assert maker_required(_ok)(_request(make_member(status=GUEST))).content == b"ok"
        with pytest.raises(PermissionDenied):
            maker_required(_ok)(_request(make_member(status=FORMER)))
        with pytest.raises(PermissionDenied):
            maker_required(_ok)(_request(None))

    def it_lets_the_crew_through_and_stops_a_member(crew, make_member):
        assert crew_required(_ok)(_request(crew)).content == b"ok"
        with pytest.raises(PermissionDenied):
            crew_required(_ok)(_request(make_member()))


def describe_the_guest_gate():
    """The lockout's one exception, both ways (core.middleware.MemberLockoutMiddleware)."""

    def it_lets_a_guest_reach_the_kiln_pages_and_file_a_draft(make_member):
        guest = make_member(status=GUEST)
        client = signed_in(guest)

        assert client.get(MINE).status_code == 200
        assert client.get(reverse("kiln:new")).status_code == 200
        response = client.post(reverse("kiln:new"), {"action": "draft", "quantity": "2"})
        assert response["Location"] == MINE
        ticket = KilnTicket.objects.get(maker=guest)
        assert ticket.maker_type == KilnTicket.MakerType.STUDENT
        assert client.get(reverse("kiln:detail", args=[ticket.pk])).status_code == 200

    def it_sends_a_guest_anywhere_else_back_to_their_tickets(make_member):
        client = signed_in(make_member(status=GUEST))

        for path in (
            reverse("hub_home"),
            reverse("hub_member_directory"),
            reverse("hub_user_settings"),
            reverse("hub_community_calendar"),
        ):
            response = client.get(path)
            assert response.status_code == 302 and response["Location"] == MINE, path

    def it_answers_a_guests_htmx_request_elsewhere_with_an_htmx_redirect(make_member):
        response = signed_in(make_member(status=GUEST)).get(reverse("hub_home"), HTTP_HX_REQUEST="true")

        assert response["HX-Redirect"] == MINE

    def it_keeps_the_crew_lists_from_a_guest(make_member):
        assert signed_in(make_member(status=GUEST)).get(reverse("kiln:lists")).status_code == 403

    def it_keeps_a_former_member_locked_out_of_the_kiln(make_member):
        response = signed_in(make_member(status=FORMER)).get(MINE)

        assert response.status_code == 302
        assert response["Location"] == f"{reverse('account_locked')}?reason=former"

    def it_keeps_a_suspended_member_locked_out_of_the_kiln_while_suspensions_lock(make_member, suspensions_lock_out):
        response = signed_in(make_member(status=SUSPENDED)).get(MINE)

        assert response["Location"] == f"{reverse('account_locked')}?reason=suspended"

    def it_does_not_open_the_kiln_on_the_book_surface(make_member, settings):
        settings.ALLOWED_HOSTS = ["testserver", "book.pastlives.space"]
        settings.PUBLIC_HOSTS = ["book.pastlives.space"]

        response = signed_in(make_member(status=GUEST)).get(MINE, HTTP_HOST="book.pastlives.space")

        assert response.status_code == 404

    def it_shows_a_guest_only_kiln_tickets_and_sign_out(make_member):
        body = signed_in(make_member(status=GUEST)).get(MINE).content.decode()

        nav = body.split('aria-label="Hub navigation"')[1].split("</nav>")[0]
        assert nav.count("hub-sidebar__link") == 2
        assert "Kiln Tickets" in nav and reverse("account_logout") in nav
        assert reverse("hub_member_directory") not in nav
        assert f'href="{reverse("hub_beta_feedback")}"' not in body
        assert f'href="{reverse("hub_user_settings")}"' not in body
        assert f'href="{reverse("hub_tab_detail")}"' not in body
        assert f'href="{reverse("hub_home")}"' not in body
        assert f'href="{MINE}" class="pl-brand"' in body

    def it_keeps_the_tab_and_home_links_for_a_member(make_member):
        body = signed_in(make_member()).get(MINE).content.decode()

        assert f'href="{reverse("hub_home")}" class="pl-brand"' in body


def describe_signing_in():
    def _sign_in(client: Client, email: str):
        cache.clear()
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code", return_value="ABCDEF"):
            client.post(reverse("account_request_login_code"), {"email": email})
        return client.post(reverse("account_confirm_login_code"), {"code": "ABCDEF"})

    def it_signs_a_guest_in_and_lands_them_on_their_tickets(make_member):
        guest = make_member(status=GUEST)
        client = Client()

        response = _sign_in(client, guest.user.email)

        assert response.status_code == 302
        assert client.session.get("_auth_user_id") == str(guest.user.pk)
        landing = client.get(response["Location"], follow=True)
        assert landing.redirect_chain[-1][0] == MINE

    def it_still_turns_away_a_former_member(make_member):
        former = make_member(status=FORMER)
        client = Client()

        response = _sign_in(client, former.user.email)

        assert response["Location"] == f"{reverse('account_locked')}?reason=former"
        assert client.session.get("_auth_user_id") is None

    def it_still_turns_away_a_suspended_member_while_suspensions_lock(make_member, suspensions_lock_out):
        suspended = make_member(status=SUSPENDED)
        client = Client()

        response = _sign_in(client, suspended.user.email)

        assert response["Location"] == f"{reverse('account_locked')}?reason=suspended"
        assert client.session.get("_auth_user_id") is None


def describe_entry_points():
    def it_lists_kiln_tickets_in_a_guild_members_sidebar(kiln_guild, make_member):
        member = make_member()
        GuildMembershipFactory(guild=kiln_guild, member=member)

        body = signed_in(member).get(reverse("hub_community_calendar")).content.decode()

        assert 'data-nav="kiln"' in body

    def it_leaves_it_out_for_other_members(kiln_guild, make_member):
        body = signed_in(make_member()).get(reverse("hub_community_calendar")).content.decode()

        assert 'data-nav="kiln"' not in body

    def it_lists_it_for_an_admin_in_the_guild(kiln_guild, make_member):
        admin = make_member(fog_role=Member.FogRole.ADMIN)
        admin.sync_user_permissions()
        GuildMembershipFactory(guild=kiln_guild, member=admin)

        body = signed_in(admin).get(reverse("hub_community_calendar")).content.decode()

        assert 'data-nav="kiln"' in body

    def it_links_kiln_tickets_from_the_ceramics_guild_page_for_everyone(kiln_guild, make_member):
        page = signed_in(make_member()).get(reverse("hub_guild_detail", args=[kiln_guild.slug])).content.decode()
        other = GuildFactory(name="Print Guild", slug="print-guild")
        other_page = signed_in(make_member()).get(reverse("hub_guild_detail", args=[other.slug])).content.decode()

        assert 'data-nav="kiln-guild-link"' in page
        assert 'data-nav="kiln-guild-link"' not in other_page
