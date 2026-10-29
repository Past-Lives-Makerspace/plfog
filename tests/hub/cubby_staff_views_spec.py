"""BDD specs for the staff Cubbies manager's Rentals tab (#979): hub/cubby_staff_views.py and its templates.

The tab (tiles, chips, the sortable table and its phone cards, End Rental's confirm with a required
reason, Assign a Cubby), Export CSV, Assign (Comped or Paid Another Way, refusals reopening the
modal), End Rental, a member's Cancel Rental on an assigned rental, and the Pricing tab's split bar.
Every URL is a 404 to anyone who does not run the guild, a crafted POST naming another guild's cubby
or rental included; admins reach every guild, a Guild Officer only one they lead or staff (#1015).
Assertions anchor on markup and factory strings.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
import stripe
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hub.cubby_forms import CubbyAssignForm
from membership.models import CubbyRental, Guild, Member
from tests.membership.factories import (
    CubbyFactory,
    CubbyRentalFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
    ShelfUnitFactory,
)

pytestmark = pytest.mark.django_db

Status = CubbyRental.Status
Source = CubbyRental.Source


def _account(username: str, name: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    member = user.member
    member.fog_role = fog_role
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save(update_fields=["fog_role", "status", "full_legal_name"])
    member.sync_user_permissions()
    return member


def _staff(client: Client) -> tuple[Member, Guild]:
    lead = _account("vellum_lead", "Vellum Harrowgate")
    client.login(username="vellum_lead", password="pass")
    guild = GuildFactory(name="Kettlewick Pottery Guild", guild_lead=lead, show_cubbies_tab=True)
    return lead, guild


def _cubby(guild: Guild, label: str = "A3", **kwargs: object) -> object:
    unit = ShelfUnitFactory(guild=guild, name="Glaze Wall")
    return CubbyFactory(unit=unit, label=label, **kwargs)


def _rentals_url(guild: Guild, **query: str) -> str:
    url = reverse("hub_guild_cubbies_rentals", args=[guild.pk])
    if query:
        url += "?" + "&".join(f"{key}={value}" for key, value in query.items())
    return url


def describe_the_rentals_tab():
    def it_draws_tiles_chips_the_table_the_cards_and_an_end_confirm(client: Client):
        _lead, guild = _staff(client)
        renter = _account("odessa_r", "Odessa Rainwhistle")
        rental = CubbyRentalFactory(cubby=_cubby(guild), member=renter, amount_cents=2200)
        CubbyRentalFactory(cubby=_cubby(guild, "B1"), status=Status.ENDED, end_date=timezone.localdate())
        content = client.get(_rentals_url(guild)).content.decode()
        assert 'aria-current="page" data-cubby-rentals-tab>Rentals<span class="pl-cubby-tabcount">1</span>' in content
        assert 'data-cubby-tile="revenue"' in content and "$22" in content
        assert 'data-rental-filter="current"' in content and "Current (1)" in content and "Ended (1)" in content
        assert f'data-rental-row="{rental.pk}"' in content and "odessa_r@example.com" in content
        assert f'data-rental-card="{rental.pk}"' in content
        assert 'data-rental-chip="active"' in content
        assert "End Odessa Rainwhistle&#x27;s Rental?" in content or "End Odessa Rainwhistle's Rental?" in content
        assert 'name="reason"' in content
        assert 'href="?sort=member__full_legal_name&amp;dir=asc"' in content
        csv_link = content.split("data-cubby-csv", 1)[0].rsplit("<a ", 1)[1]
        assert 'hx-boost="false"' in csv_link and "data-pl-download" in csv_link
        assert 'id="cubby-assign-title"' in content and "data-member-picker" in content

    def it_filters_and_sorts(client: Client):
        _lead, guild = _staff(client)
        CubbyRentalFactory(cubby=_cubby(guild, "Z1"), amount_cents=3000)
        CubbyRentalFactory(cubby=_cubby(guild, "M1"), amount_cents=1000)
        CubbyRentalFactory(cubby=_cubby(guild, "P1"), status=Status.PAST_DUE)
        content = client.get(_rentals_url(guild, show="past_due")).content.decode()
        assert content.count("data-rental-row=") == 1 and "P1<small>" in content
        content = client.get(_rentals_url(guild, sort="amount_cents", dir="desc")).content.decode()
        assert content.index("Z1<small>") < content.index("M1<small>")
        content = client.get(_rentals_url(guild, sort="cubby__unit__guild__name")).content.decode()
        assert content.index("M1<small>") < content.index("Z1<small>")

    def it_shows_the_empty_states(client: Client):
        _lead, guild = _staff(client)
        content = client.get(_rentals_url(guild)).content.decode()
        assert "No rentals yet. Members rent from the Cubbies tab, or assign one here." in content
        assert "data-cubby-assign-none" in content
        CubbyRentalFactory(cubby=_cubby(guild), status=Status.ENDED)
        content = client.get(_rentals_url(guild, show="past_due")).content.decode()
        assert "No past due rentals." in content

    def it_is_a_404_to_another_guilds_staff_and_open_to_admins(client: Client):
        _lead, guild = _staff(client)
        other = GuildFactory(name="Saltmarsh Glass Guild")
        assert client.get(_rentals_url(other)).status_code == 404
        assert client.get(reverse("hub_guild_cubbies_rentals_csv", args=[other.pk])).status_code == 404
        _account("root_admin", "Root Adminton", fog_role=Member.FogRole.ADMIN)
        client.login(username="root_admin", password="pass")
        assert client.get(_rentals_url(other)).status_code == 200

    def it_is_a_404_to_a_guild_officer_until_they_lead_or_staff_the_guild(client: Client):
        # #1015: the Guild Officer role alone opens no guild's Rentals, CSV, Assign or End Rental; a staff row there does.
        officer = _account("roving_officer", "Roving Officerly", fog_role=Member.FogRole.GUILD_OFFICER)
        client.login(username="roving_officer", password="pass")
        guild = GuildFactory(name="Kettlewick Pottery Guild", show_cubbies_tab=True)
        rental = CubbyRentalFactory(
            cubby=_cubby(guild), source=Source.COMPED, amount_cents=0, stripe_subscription_id=None
        )
        csv_url = reverse("hub_guild_cubbies_rentals_csv", args=[guild.pk])
        assign = reverse("hub_guild_cubby_assign", args=[guild.pk])
        end = reverse("hub_cubby_rental_end", args=[rental.pk])
        assert client.get(_rentals_url(guild)).status_code == 404
        assert client.get(csv_url).status_code == 404
        assert client.post(assign, {"source": "comped"}).status_code == 404
        assert client.post(end, {"reason": "Not yours."}).status_code == 404
        rental.refresh_from_db()
        assert rental.status == Status.ACTIVE
        GuildStaffMembershipFactory(guild=guild, member=officer)
        assert client.get(_rentals_url(guild)).status_code == 200
        assert client.get(csv_url).status_code == 200
        assert client.post(assign, {"source": "comped"}).status_code == 200  # the modal reopens with what is missing
        assert client.post(end, {"reason": "Moving you to D4."}).status_code == 302
        rental.refresh_from_db()
        assert rental.status == Status.ENDED

    def it_is_a_404_to_an_admin_previewing_as_a_guild_officer(client: Client):
        # #1015: the preview is refused like the role it shows, on Rentals, CSV, Assign and End Rental.
        _account("preview_admin", "Preview Admin", fog_role=Member.FogRole.ADMIN)
        client.login(username="preview_admin", password="pass")
        session = client.session
        session["view_as_role"] = "guild_officer"
        session.save()
        guild = GuildFactory(name="Kettlewick Pottery Guild", show_cubbies_tab=True)
        rental = CubbyRentalFactory(
            cubby=_cubby(guild), source=Source.COMPED, amount_cents=0, stripe_subscription_id=None
        )
        assert client.get(_rentals_url(guild)).status_code == 404
        assert client.get(reverse("hub_guild_cubbies_rentals_csv", args=[guild.pk])).status_code == 404
        assert client.post(reverse("hub_guild_cubby_assign", args=[guild.pk]), {"source": "comped"}).status_code == 404
        assert (
            client.post(reverse("hub_cubby_rental_end", args=[rental.pk]), {"reason": "Not yours."}).status_code == 404
        )
        rental.refresh_from_db()
        assert rental.status == Status.ACTIVE


def describe_export_csv():
    def it_streams_one_row_per_rental(client: Client):
        _lead, guild = _staff(client)
        renter = _account("odessa_r", "=Odessa Rainwhistle")
        CubbyRentalFactory(cubby=_cubby(guild), member=renter, amount_cents=2200)
        response = client.get(reverse("hub_guild_cubbies_rentals_csv", args=[guild.pk]))
        assert response["Content-Disposition"].startswith(f'attachment; filename="{guild.slug}-cubby-rentals-')
        body = b"".join(response.streaming_content).decode().splitlines()
        assert body[0] == "Member,Cubby,Unit,Amount,Start,End,Status,Source"
        assert body[1].startswith("'=Odessa Rainwhistle,A3,Glaze Wall,22.00,")
        assert body[1].endswith(",,Active,Stripe")


def describe_assign():
    def _post(client: Client, guild: Guild, **data: str) -> object:
        return client.post(reverse("hub_guild_cubby_assign", args=[guild.pk]), data)

    def it_assigns_a_comped_cubby_and_lands_on_the_tab_with_a_toast(client: Client):
        _lead, guild = _staff(client)
        cubby = _cubby(guild)
        _account("odessa_r", "Odessa Rainwhistle")
        response = _post(client, guild, member="odessa rainwhistle ", cubby=str(cubby.pk), source="comped")
        assert response.status_code == 302 and response.url == _rentals_url(guild)
        rental = CubbyRental.objects.get()
        assert (rental.source, rental.amount_cents, rental.status) == (Source.COMPED, 0, Status.ACTIVE)
        content = client.get(response.url).content.decode()
        assert "A3 assigned to Odessa Rainwhistle." in content

    def it_assigns_paid_another_way_with_its_amount(client: Client):
        _lead, guild = _staff(client)
        cubby = _cubby(guild)
        _account("odessa_r", "Odessa Rainwhistle")
        _post(client, guild, member="Odessa Rainwhistle", cubby=str(cubby.pk), source="outside", amount="17.50")
        rental = CubbyRental.objects.get()
        assert (rental.source, rental.amount_cents) == (Source.OUTSIDE, 1750)

    def it_reopens_the_modal_with_what_is_missing(client: Client):
        _lead, guild = _staff(client)
        cubby = _cubby(guild)
        response = _post(client, guild, member="Nobody Byname", cubby=str(cubby.pk), source="outside")
        content = response.content.decode()
        assert response.status_code == 200
        assert "Pick a member from the list." in content and "Enter what they pay each month." in content
        assert "x-init=\"$nextTick(() => $dispatch('open-modal', 'cubby-assign'))\"" in content
        assert not CubbyRental.objects.exists()

    def it_refuses_a_cubby_someone_took_meanwhile(client: Client):
        _lead, guild = _staff(client)
        cubby = _cubby(guild)
        _account("odessa_r", "Odessa Rainwhistle")
        with patch("membership.cubby_rentals.assign_problem", return_value="A3 is out of service right now."):
            response = _post(client, guild, member="Odessa Rainwhistle", cubby=str(cubby.pk), source="comped")
        assert "A3 is out of service right now." in response.content.decode()
        assert not CubbyRental.objects.exists()

    def it_refuses_a_taken_cubby_as_no_longer_open(client: Client):
        _lead, guild = _staff(client)
        cubby = _cubby(guild)
        CubbyRentalFactory(cubby=cubby)
        _account("odessa_r", "Odessa Rainwhistle")
        response = _post(client, guild, member="Odessa Rainwhistle", cubby=str(cubby.pk), source="comped")
        assert "That cubby isn&#x27;t open any more. Pick another." in response.content.decode()

    def it_is_a_404_for_another_guilds_cubby_or_guild(client: Client):
        _lead, guild = _staff(client)
        other = GuildFactory(name="Saltmarsh Glass Guild")
        theirs = _cubby(other, "X1")
        _account("odessa_r", "Odessa Rainwhistle")
        assert (
            _post(client, guild, member="Odessa Rainwhistle", cubby=str(theirs.pk), source="comped").status_code == 404
        )
        assert (
            _post(client, other, member="Odessa Rainwhistle", cubby=str(theirs.pk), source="comped").status_code == 404
        )
        assert not CubbyRental.objects.exists()

    def it_refuses_a_cubby_that_does_not_exist_and_a_bad_amount(client: Client):
        _lead, guild = _staff(client)
        _cubby(guild)
        _account("odessa_r", "Odessa Rainwhistle")
        content = _post(
            client, guild, member="Odessa Rainwhistle", cubby="987654", source="outside", amount="lots"
        ).content.decode()
        assert "That cubby isn&#x27;t open any more. Pick another." in content
        assert "Enter dollars, like 15 or 15.50." in content
        assert "Enter what they pay each month." not in content
        content = _post(client, guild, member="Odessa Rainwhistle", cubby="", source="comped").content.decode()
        assert "Pick an open cubby." in content

    def it_asks_how_they_pay():
        guild = GuildFactory(name="Kettlewick Pottery Guild")
        cubby = _cubby(guild)
        _account("odessa_r", "Odessa Rainwhistle")
        form = CubbyAssignForm({"member": "Odessa Rainwhistle", "cubby": str(cubby.pk), "source": "card"}, guild=guild)
        assert not form.is_valid() and "source" in form.errors
        assert "amount_cents" not in form.cleaned_data

    def it_lists_members_with_what_they_rent_here():
        guild = GuildFactory(name="Kettlewick Pottery Guild")
        renter = _account("odessa_r", "Odessa Rainwhistle")
        CubbyRentalFactory(cubby=_cubby(guild, "D2"), member=renter)
        _cubby(guild, "D3")
        form = CubbyAssignForm(guild=guild)
        notes = dict(form.member_options)
        assert notes["Odessa Rainwhistle"] == "Rents D2"
        assert [group for group, _options in form.fields["cubby"].choices[1:]] == ["Glaze Wall"]


def describe_end_rental():
    def _end(client: Client, rental: CubbyRental, reason: str = "Moving you to D4.") -> object:
        return client.post(reverse("hub_cubby_rental_end", args=[rental.pk]), {"reason": reason})

    @patch("billing.stripe_utils.cancel_subscription")
    def it_ends_a_rental_with_the_reason(mock_cancel, client: Client):
        _lead, guild = _staff(client)
        renter = _account("odessa_r", "Odessa Rainwhistle")
        rental = CubbyRentalFactory(cubby=_cubby(guild), member=renter)
        response = _end(client, rental)
        assert response.status_code == 302 and response.url == _rentals_url(guild)
        rental.refresh_from_db()
        assert (rental.status, rental.end_reason) == (Status.ENDED, "Moving you to D4.")
        mock_cancel.assert_called_once()
        assert "A3 is open again. We told Odessa Rainwhistle." in client.get(response.url).content.decode()

    def it_needs_a_reason(client: Client):
        _lead, guild = _staff(client)
        rental = CubbyRentalFactory(cubby=_cubby(guild))
        response = _end(client, rental, reason="   ")
        rental.refresh_from_db()
        assert rental.status == Status.ACTIVE
        assert "Say why you&#x27;re ending it." in client.get(response.url).content.decode()

    def it_says_so_when_the_rental_already_ended(client: Client):
        _lead, guild = _staff(client)
        rental = CubbyRentalFactory(cubby=_cubby(guild), status=Status.ENDED)
        response = _end(client, rental)
        assert "This rental isn&#x27;t active any more." in client.get(response.url).content.decode()

    @patch("billing.stripe_utils.cancel_subscription", side_effect=stripe.APIConnectionError("timed out"))
    def it_says_to_check_again_when_stripe_gives_no_answer(_mock_cancel, client: Client):
        _lead, guild = _staff(client)
        rental = CubbyRentalFactory(cubby=_cubby(guild))
        response = _end(client, rental)
        rental.refresh_from_db()
        assert rental.status == Status.ACTIVE
        content = client.get(response.url).content.decode()
        assert "Stripe didn&#x27;t answer. Check again in a minute." in content
        assert "nothing changed" not in content

    @patch("billing.stripe_utils.cancel_subscription", side_effect=stripe.PermissionError("no access"))
    def it_says_nothing_changed_when_stripe_refuses(_mock_cancel, client: Client):
        _lead, guild = _staff(client)
        rental = CubbyRentalFactory(cubby=_cubby(guild))
        response = _end(client, rental)
        rental.refresh_from_db()
        assert rental.status == Status.ACTIVE
        assert "Stripe refused, so nothing changed. Try again in a minute." in client.get(response.url).content.decode()

    def it_anchors_each_rental_for_the_staff_notices(client: Client):
        _lead, guild = _staff(client)
        rental = CubbyRentalFactory(cubby=_cubby(guild))
        content = client.get(_rentals_url(guild)).content.decode()
        assert f'id="rental-{rental.pk}"' in content and f'id="rental-{rental.pk}-card"' in content

    def it_is_a_404_for_another_guilds_rental(client: Client):
        _lead, _guild = _staff(client)
        other = GuildFactory(name="Saltmarsh Glass Guild")
        rental = CubbyRentalFactory(cubby=_cubby(other, "X1"))
        assert _end(client, rental).status_code == 404
        rental.refresh_from_db()
        assert rental.status == Status.ACTIVE

    def it_lets_guild_staff_who_are_not_the_lead_end_one(client: Client):
        guild = GuildFactory(name="Kettlewick Pottery Guild")
        staff = _account("tobin_staff", "Tobin Merriweather")
        GuildStaffMembershipFactory(guild=guild, member=staff)
        client.login(username="tobin_staff", password="pass")
        rental = CubbyRentalFactory(
            cubby=_cubby(guild), source=Source.COMPED, amount_cents=0, stripe_subscription_id=None
        )
        _end(client, rental)
        rental.refresh_from_db()
        assert rental.status == Status.ENDED


def describe_the_members_assigned_cubby():
    def it_says_assigned_by_guild_staff_and_hides_the_payment_buttons(client: Client):
        guild = GuildFactory(name="Kettlewick Pottery Guild", show_cubbies_tab=True)
        renter = _account("odessa_r", "Odessa Rainwhistle")
        client.login(username="odessa_r", password="pass")
        rental = CubbyRentalFactory(
            cubby=_cubby(guild), member=renter, source=Source.COMPED, amount_cents=0, stripe_subscription_id=None
        )
        content = client.get(reverse("hub_guild_detail", args=[guild.slug]) + "?tab=cubbies").content.decode()
        card = content.split('data-cubby-mine="A3"', 1)[1].split("</article>", 1)[0]
        assert "data-cubby-assigned" in card and "Assigned by guild staff" in card
        assert "data-cubby-change" not in card and "data-cubby-manage-payment" not in card
        assert "data-cubby-cancel" in card
        assert rental.change_modal_id not in content

    def it_ends_it_today_on_cancel(client: Client):
        guild = GuildFactory(name="Kettlewick Pottery Guild", show_cubbies_tab=True)
        renter = _account("odessa_r", "Odessa Rainwhistle")
        client.login(username="odessa_r", password="pass")
        rental = CubbyRentalFactory(
            cubby=_cubby(guild), member=renter, source=Source.OUTSIDE, amount_cents=1500, stripe_subscription_id=None
        )
        response = client.post(reverse("hub_cubby_rental_cancel", args=[rental.pk]))
        rental.refresh_from_db()
        assert rental.status == Status.ENDED
        assert "A3 is no longer yours." in client.get(response.url).content.decode()


def describe_the_pricing_split_bar():
    def it_draws_the_admins_pair_and_the_payees_name(client: Client):
        lead, guild = _staff(client)
        Guild.objects.filter(pk=guild.pk).update(cubby_payee=lead, cubby_suggested_cents=2000)
        content = client.get(reverse("hub_guild_cubbies_pricing", args=[guild.pk])).content.decode()
        assert 'aria-label="Guild 85 percent, Past Lives 15 percent"' in content
        assert 'width="85.00"' in content and 'x="85.00"' in content
        assert 'id="cubby-payee-names"' in content and "Vellum Harrowgate" in content
        assert "data-cubby-split-text" in content and "data-cubby-split-none" in content
        assert "guildPct: 85.00" in content

    def it_counts_past_due_rentals_in_the_tab_label_until_they_end(client: Client):
        _lead, guild = _staff(client)
        CubbyRentalFactory(cubby=_cubby(guild, "A1"), status=Status.PAST_DUE)
        CubbyRentalFactory(
            cubby=_cubby(guild, "A2"), status=Status.ENDED, end_date=timezone.localdate() - timedelta(days=1)
        )
        content = client.get(reverse("hub_guild_cubbies_pricing", args=[guild.pk])).content.decode()
        assert 'data-cubby-rentals-tab>Rentals<span class="pl-cubby-tabcount">1</span>' in content
