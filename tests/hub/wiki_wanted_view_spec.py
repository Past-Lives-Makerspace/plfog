"""Specs for /wiki/wanted/: the list, the editor, and the three row controls.

The editor is a list editor, so it is checked against the FRONTEND.md rules that keep
burning us: ``extra=0`` and no blank row, a real danger-button Delete rather than a toggle,
a "+ Add" that clones a template, and Save last saying just "Save".
"""

from __future__ import annotations

import json
import re

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from datetime import timedelta

from django.utils import timezone

from core.models import SiteConfiguration
from membership.models import Member, WikiWantedPage
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    WikiPageFactory,
    WikiWantedPageFactory,
)

pytestmark = pytest.mark.django_db

_HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture(autouse=True)
def _wiki_on(db):
    config = SiteConfiguration.load()
    config.wiki_enabled = True
    config.save()
    return config


def _member_user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.full_legal_name = username.title()
    member.save()
    member.sync_user_permissions()
    return user


def _login(client: Client, username: str, **kwargs: str) -> User:
    user = _member_user(username, **kwargs)
    client.login(username=username, password="pass")
    return user


def _wanted_url(guild=None) -> str:
    base = reverse("hub_wiki_wanted")
    return f"{base}?guild={guild.slug}" if guild is not None else base


def _toast(response) -> dict:
    return json.loads(response["HX-Trigger"])["showToast"]


def describe_the_list_page():
    def it_renders_for_a_plain_member_with_claim_and_no_editor(db, client):
        _login(client, "wanted_plain")
        guild = GuildFactory()
        WikiWantedPageFactory(guild=guild, title="Sharpening jigs")
        html = client.get(_wanted_url(guild)).content.decode()
        assert "Sharpening jigs" in html
        assert ">Claim</button>" in html
        assert "Edit The List" not in html

    def it_404s_on_an_unknown_guild_slug(db, client):
        _login(client, "wanted_badslug")
        assert client.get(f"{reverse('hub_wiki_wanted')}?guild=nope").status_code == 404

    def it_shows_the_already_written_section_once_a_row_is_closed(db, client):
        """As previously specced this section could never fill: fulfil() had no caller."""
        _login(client, "wanted_done_section")
        guild = GuildFactory()
        row = WikiWantedPageFactory(guild=guild, title="Bandsaw setup")
        row.fulfil(WikiPageFactory(guild=guild, title="Bandsaw Setup Guide"))
        html = client.get(_wanted_url(guild)).content.decode()
        assert "Already Written (1)" in html
        assert "Bandsaw Setup Guide" in html

    def it_offers_a_real_go_button_beside_the_guild_switcher(db, client):
        """A bare navigate-on-change select does nothing at all with JavaScript off."""
        user = _login(client, "wanted_switcher")
        GuildFactory(guild_lead=user.member)
        html = client.get(reverse("hub_wiki_wanted")).content.decode()
        switcher = html.split('class="pl-wp-tab__switcher"', 1)[1].split("</form>", 1)[0]
        assert 'type="submit"' in switcher


def describe_the_editor():
    def it_renders_no_blank_row(db, client):
        """extra=1 would render a perpetual blank row whose required title blocks Save."""
        user = _login(client, "wanted_extra_zero")
        guild = GuildFactory(guild_lead=user.member)
        html = client.get(_wanted_url(guild)).content.decode()
        assert 'name="wanted-TOTAL_FORMS" value="0"' in html
        assert "+ Add A Wanted Page" in html
        assert 'id="wanted-empty-template"' in html

    def it_renders_a_real_delete_button_and_a_hidden_delete_field(db, client):
        user = _login(client, "wanted_delete_button")
        guild = GuildFactory(guild_lead=user.member)
        WikiWantedPageFactory(guild=guild, title="Sharpening jigs")
        html = client.get(_wanted_url(guild)).content.decode()
        row = html.split("Edit The List", 1)[1]
        assert "pl-btn--danger" in row
        assert "margin-top:0.75rem;" in row
        assert 'style="display:none;">' in row
        # Never a toggle: the DELETE field must not go through form_field.html.
        assert "pl-toggle" not in row.split("Delete this request", 1)[0][-2000:]

    def it_ends_with_a_save_button_that_says_save(db, client):
        user = _login(client, "wanted_save_last")
        guild = GuildFactory(guild_lead=user.member)
        html = client.get(_wanted_url(guild)).content.decode()
        form = html.split("Edit The List", 1)[1].split("</form>", 1)[0]
        assert form.rstrip().endswith("</div>")
        assert '<button type="submit" class="pl-btn pl-btn--primary">Save</button>' in form

    def it_saves_a_new_row_and_redirects_with_a_message(db, client):
        user = _login(client, "wanted_save")
        guild = GuildFactory(guild_lead=user.member)
        response = client.post(
            _wanted_url(guild),
            {
                "wanted-TOTAL_FORMS": "1",
                "wanted-INITIAL_FORMS": "0",
                "wanted-MIN_NUM_FORMS": "0",
                "wanted-MAX_NUM_FORMS": "1000",
                "wanted-0-id": "",
                "wanted-0-title": "Sharpening jigs",
                "wanted-0-note": "Cover the honing guide.",
            },
        )
        assert response.status_code == 302
        row = WikiWantedPage.objects.get()
        assert (row.title, row.guild_id, row.created_by_id) == ("Sharpening jigs", guild.pk, user.member.pk)

    def it_ignores_a_row_nobody_typed_in(db, client):
        """An abandoned "+ Add" clone must never block Save."""
        user = _login(client, "wanted_blank_row")
        guild = GuildFactory(guild_lead=user.member)
        response = client.post(
            _wanted_url(guild),
            {
                "wanted-TOTAL_FORMS": "2",
                "wanted-INITIAL_FORMS": "0",
                "wanted-MIN_NUM_FORMS": "0",
                "wanted-MAX_NUM_FORMS": "1000",
                "wanted-0-id": "",
                "wanted-0-title": "Sharpening jigs",
                "wanted-0-note": "",
                "wanted-1-id": "",
                "wanted-1-title": "",
                "wanted-1-note": "",
            },
        )
        assert response.status_code == 302
        assert WikiWantedPage.objects.count() == 1

    def it_re_renders_bound_with_the_typed_text_on_an_error(db, client):
        user = _login(client, "wanted_invalid")
        guild = GuildFactory(guild_lead=user.member)
        response = client.post(
            _wanted_url(guild),
            {
                "wanted-TOTAL_FORMS": "1",
                "wanted-INITIAL_FORMS": "0",
                "wanted-MIN_NUM_FORMS": "0",
                "wanted-MAX_NUM_FORMS": "1000",
                "wanted-0-id": "",
                "wanted-0-title": "ab",
                "wanted-0-note": "Do not lose this sentence.",
            },
        )
        assert response.status_code == 200
        html = response.content.decode()
        assert "Do not lose this sentence." in html
        assert "at least three characters" in html
        assert WikiWantedPage.objects.count() == 0

    def it_refuses_a_duplicate_open_title_in_the_same_scope(db, client):
        user = _login(client, "wanted_dupe")
        guild = GuildFactory(guild_lead=user.member)
        WikiWantedPageFactory(guild=guild, title="Sharpening jigs")
        response = client.post(
            _wanted_url(guild),
            {
                "wanted-TOTAL_FORMS": "1",
                "wanted-INITIAL_FORMS": "1",
                "wanted-MIN_NUM_FORMS": "0",
                "wanted-MAX_NUM_FORMS": "1000",
                "wanted-0-id": str(WikiWantedPage.objects.get().pk),
                "wanted-0-title": "Sharpening jigs",
                "wanted-0-note": "",
                "wanted-TOTAL_FORMS_extra": "",
            },
        )
        # The row's own pk is excluded, so editing a row without renaming it still saves.
        assert response.status_code == 302

    def it_catches_two_identical_rows_in_one_submission(db, client):
        """The DB constraint would raise IntegrityError, which reaches a member as a 500."""
        user = _login(client, "wanted_twins")
        guild = GuildFactory(guild_lead=user.member)
        response = client.post(
            _wanted_url(guild),
            {
                "wanted-TOTAL_FORMS": "2",
                "wanted-INITIAL_FORMS": "0",
                "wanted-MIN_NUM_FORMS": "0",
                "wanted-MAX_NUM_FORMS": "1000",
                "wanted-0-id": "",
                "wanted-0-title": "Sharpening jigs",
                "wanted-0-note": "",
                "wanted-1-id": "",
                "wanted-1-title": "sharpening JIGS",
                "wanted-1-note": "",
            },
        )
        assert response.status_code == 200
        assert "twice" in response.content.decode()
        assert WikiWantedPage.objects.count() == 0

    def it_deletes_a_row_the_delete_button_marked(db, client):
        user = _login(client, "wanted_delete_save")
        guild = GuildFactory(guild_lead=user.member)
        row = WikiWantedPageFactory(guild=guild, title="Sharpening jigs")
        response = client.post(
            _wanted_url(guild),
            {
                "wanted-TOTAL_FORMS": "1",
                "wanted-INITIAL_FORMS": "1",
                "wanted-MIN_NUM_FORMS": "0",
                "wanted-MAX_NUM_FORMS": "1000",
                "wanted-0-id": str(row.pk),
                "wanted-0-title": "Sharpening jigs",
                "wanted-0-note": "",
                "wanted-0-DELETE": "on",
            },
        )
        assert response.status_code == 302
        assert WikiWantedPage.objects.count() == 0

    def it_keeps_the_original_asker_on_a_row_a_lead_edits(db, client):
        """A lead tidying the wording must not become the person who asked."""
        user = _login(client, "wanted_keep_asker")
        guild = GuildFactory(guild_lead=user.member)
        asker = MemberFactory()
        row = WikiWantedPageFactory(guild=guild, title="Sharpening jigs", created_by=asker)
        response = client.post(
            _wanted_url(guild),
            {
                "wanted-TOTAL_FORMS": "1",
                "wanted-INITIAL_FORMS": "1",
                "wanted-MIN_NUM_FORMS": "0",
                "wanted-MAX_NUM_FORMS": "1000",
                "wanted-0-id": str(row.pk),
                "wanted-0-title": "Sharpening jigs and honing guides",
                "wanted-0-note": "",
            },
        )
        assert response.status_code == 302
        row.refresh_from_db()
        assert row.created_by_id == asker.pk
        assert row.title == "Sharpening jigs and honing guides"

    def it_refuses_a_new_row_duplicating_an_open_one(db, client):
        user = _login(client, "wanted_dupe_new")
        guild = GuildFactory(guild_lead=user.member)
        WikiWantedPageFactory(guild=guild, title="Sharpening jigs")
        response = client.post(
            _wanted_url(guild),
            {
                "wanted-TOTAL_FORMS": "1",
                "wanted-INITIAL_FORMS": "0",
                "wanted-MIN_NUM_FORMS": "0",
                "wanted-MAX_NUM_FORMS": "1000",
                "wanted-0-id": "",
                "wanted-0-title": "sharpening JIGS",
                "wanted-0-note": "",
            },
        )
        assert response.status_code == 200
        assert "You already have a request called" in response.content.decode()
        assert WikiWantedPage.objects.count() == 1

    def it_refuses_a_post_from_a_lead_of_another_guild(db, client):
        user = _login(client, "wanted_wrong_lead")
        GuildFactory(guild_lead=user.member)
        other = GuildFactory()
        response = client.post(
            _wanted_url(other),
            {"wanted-TOTAL_FORMS": "0", "wanted-INITIAL_FORMS": "0"},
        )
        assert response.status_code == 403

    def describe_the_space_wide_scope():
        def it_hides_the_editor_from_a_guild_lead(db, client):
            user = _login(client, "wanted_sw_lead")
            GuildFactory(guild_lead=user.member)
            assert "Edit The List" not in client.get(reverse("hub_wiki_wanted")).content.decode()

        def it_shows_the_editor_to_an_admin(db, client):
            _login(client, "wanted_sw_admin", fog_role=Member.FogRole.ADMIN)
            assert "Edit The List" in client.get(reverse("hub_wiki_wanted")).content.decode()


def describe_claim_and_release():
    def it_claims_and_swaps_the_row_back(db, client):
        user = _login(client, "wanted_claim")
        row = WikiWantedPageFactory(guild=GuildFactory())
        response = client.post(reverse("hub_wiki_wanted_claim", args=[row.pk]), **_HTMX)
        assert response.status_code == 200
        assert f'id="wiki-wanted-row-{row.pk}"' in response.content.decode()
        assert _toast(response)["message"] == "Claimed. It's yours."
        row.refresh_from_db()
        assert row.claimed_by_id == user.member.pk

    def it_lets_the_claimer_release_without_ceremony(db, client):
        user = _login(client, "wanted_release_self")
        row = WikiWantedPageFactory(guild=GuildFactory(), claimed_by=user.member, claimed_at=timezone.now())
        response = client.post(reverse("hub_wiki_wanted_claim", args=[row.pk]), {"release": "1"}, **_HTMX)
        assert response.status_code == 200
        row.refresh_from_db()
        assert row.claimed_by_id is None

    def it_lets_a_lead_release_somebody_elses_stale_claim(db, client):
        """release() always said "the claimer (or a lead)"; the control was lead-invisible,
        so is_claim_stale was a label describing a problem nobody could fix."""
        user = _login(client, "wanted_release_lead")
        guild = GuildFactory(guild_lead=user.member)
        row = WikiWantedPageFactory(guild=guild, stale_claim=True)
        response = client.post(reverse("hub_wiki_wanted_claim", args=[row.pk]), {"release": "1"}, **_HTMX)
        assert response.status_code == 200
        row.refresh_from_db()
        assert row.claimed_by_id is None

    def it_refuses_a_bystander_releasing_someone_elses_claim(db, client):
        _login(client, "wanted_release_bystander")
        row = WikiWantedPageFactory(guild=GuildFactory(), claimed_by=MemberFactory(), claimed_at=timezone.now())
        response = client.post(reverse("hub_wiki_wanted_claim", args=[row.pk]), {"release": "1"}, **_HTMX)
        assert response.status_code == 403

    def it_answers_400_on_a_row_already_written(db, client):
        _login(client, "wanted_claim_closed")
        guild = GuildFactory()
        row = WikiWantedPageFactory(guild=guild)
        assert row.fulfil(WikiPageFactory(guild=guild)) is True
        response = client.post(reverse("hub_wiki_wanted_claim", args=[row.pk]), **_HTMX)
        assert response.status_code == 400
        assert _toast(response)["type"] == "error"

    def it_404s_on_an_unknown_row(db, client):
        _login(client, "wanted_claim_404")
        assert client.post(reverse("hub_wiki_wanted_claim", args=[9999]), **_HTMX).status_code == 404

    def it_refuses_a_member_whose_membership_lapsed(db, client):
        user = _login(client, "wanted_claim_lapsed")
        row = WikiWantedPageFactory(guild=GuildFactory())
        user.member.status = Member.Status.FORMER
        user.member.save(update_fields=["status"])
        assert client.post(reverse("hub_wiki_wanted_claim", args=[row.pk]), **_HTMX).status_code == 403


def describe_the_release_confirm_modal():
    def it_renders_for_a_lead_on_somebody_elses_claim(db, client):
        user = _login(client, "wanted_release_modal")
        guild = GuildFactory(guild_lead=user.member)
        row = WikiWantedPageFactory(guild=guild, stale_claim=True)
        html = client.get(_wanted_url(guild)).content.decode()
        assert f"wiki-wanted-release-{row.pk}" in html
        assert "Nothing is deleted." in html

    def it_names_the_staleness_beside_the_lever(db, client):
        user = _login(client, "wanted_release_hint")
        guild = GuildFactory(guild_lead=user.member)
        WikiWantedPageFactory(guild=guild, stale_claim=True)
        html = client.get(_wanted_url(guild)).content.decode()
        assert "still open to anyone" in html

    def it_does_not_render_for_the_claimer(db, client):
        user = _login(client, "wanted_release_own")
        guild = GuildFactory(guild_lead=user.member)
        row = WikiWantedPageFactory(guild=guild, claimed_by=user.member, claimed_at=timezone.now())
        html = client.get(_wanted_url(guild)).content.decode()
        assert f"wiki-wanted-release-{row.pk}" not in html
        assert ">Release</button>" in html


def describe_mark_as_written():
    def it_renders_for_a_lead_and_not_for_a_plain_member(db, client):
        user = _login(client, "wanted_fulfil_lead")
        guild = GuildFactory(guild_lead=user.member)
        WikiWantedPageFactory(guild=guild)
        WikiPageFactory(guild=guild)
        assert "Mark As Written" in client.get(_wanted_url(guild)).content.decode()
        client.logout()
        _login(client, "wanted_fulfil_member")
        assert "Mark As Written" not in client.get(_wanted_url(guild)).content.decode()

    def it_closes_the_row_and_swaps_it_into_already_written(db, client):
        user = _login(client, "wanted_fulfil_post")
        guild = GuildFactory(guild_lead=user.member)
        row = WikiWantedPageFactory(guild=guild)
        page = WikiPageFactory(guild=guild, title="Bandsaw Setup Guide")
        response = client.post(
            reverse("hub_wiki_wanted_fulfil", args=[row.pk]),
            {f"fulfil{row.pk}-page": page.slug},
            **_HTMX,
        )
        assert response.status_code == 200
        assert _toast(response)["message"] == "Marked as written. Nice."
        assert "Bandsaw Setup Guide" in response.content.decode()
        row.refresh_from_db()
        assert row.fulfilled_page_id == page.pk
        assert "Already Written (1)" in client.get(_wanted_url(guild)).content.decode()

    def it_closes_the_modal_on_success(db, client):
        user = _login(client, "wanted_fulfil_close")
        guild = GuildFactory(guild_lead=user.member)
        row = WikiWantedPageFactory(guild=guild)
        page = WikiPageFactory(guild=guild)
        response = client.post(
            reverse("hub_wiki_wanted_fulfil", args=[row.pk]),
            {f"fulfil{row.pk}-page": page.slug},
            **_HTMX,
        )
        triggers = json.loads(response["HX-Trigger"])
        assert triggers["close-modal"] == f"wiki-wanted-fulfil-{row.pk}"
        assert "showToast" in triggers  # the toast is set FIRST, so it survives the merge

    def it_answers_400_and_not_500_on_a_row_already_closed(db, client):
        user = _login(client, "wanted_fulfil_twice")
        guild = GuildFactory(guild_lead=user.member)
        row = WikiWantedPageFactory(guild=guild)
        first = WikiPageFactory(guild=guild)
        row.fulfil(first)
        second = WikiPageFactory(guild=guild)
        response = client.post(
            reverse("hub_wiki_wanted_fulfil", args=[row.pk]),
            {f"fulfil{row.pk}-page": second.slug},
            **_HTMX,
        )
        assert response.status_code == 400
        assert _toast(response)["message"] == "That request was already closed."

    def it_refuses_a_page_from_another_guild(db, client):
        user = _login(client, "wanted_fulfil_scope")
        guild = GuildFactory(guild_lead=user.member)
        row = WikiWantedPageFactory(guild=guild)
        elsewhere = WikiPageFactory(guild=GuildFactory())
        response = client.post(
            reverse("hub_wiki_wanted_fulfil", args=[row.pk]),
            {f"fulfil{row.pk}-page": elsewhere.slug},
            **_HTMX,
        )
        assert response.status_code == 400
        row.refresh_from_db()
        assert row.fulfilled_page_id is None

    def it_refuses_a_lapsed_member(db, client):
        user = _login(client, "wanted_fulfil_lapsed", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        row = WikiWantedPageFactory(guild=guild)
        page = WikiPageFactory(guild=guild)
        user.member.status = Member.Status.FORMER
        user.member.save(update_fields=["status"])
        response = client.post(
            reverse("hub_wiki_wanted_fulfil", args=[row.pk]),
            {f"fulfil{row.pk}-page": page.slug},
            **_HTMX,
        )
        assert response.status_code == 403

    def it_404s_on_an_unknown_row(db, client):
        _login(client, "wanted_fulfil_404")
        assert client.post(reverse("hub_wiki_wanted_fulfil", args=[9999]), **_HTMX).status_code == 404

    def it_names_the_scope_when_a_slug_input_gets_the_wrong_page(db, client, monkeypatch):
        from hub.forms import WikiWantedFulfilForm

        monkeypatch.setattr(WikiWantedFulfilForm, "CHOICE_LIMIT", 0)
        user = _login(client, "wanted_fulfil_slug_wrong")
        guild = GuildFactory(guild_lead=user.member)
        row = WikiWantedPageFactory(guild=guild)
        WikiPageFactory(guild=guild)  # one candidate, so CHOICE_LIMIT=0 takes the slug branch
        elsewhere = WikiPageFactory(guild=GuildFactory())
        response = client.post(
            reverse("hub_wiki_wanted_fulfil", args=[row.pk]),
            {f"fulfil{row.pk}-page": elsewhere.slug},
            **_HTMX,
        )
        assert response.status_code == 400
        assert _toast(response)["message"] == "That page is not in this guild."

    def it_refuses_a_plain_member(db, client):
        _login(client, "wanted_fulfil_plain")
        guild = GuildFactory()
        row = WikiWantedPageFactory(guild=guild)
        page = WikiPageFactory(guild=guild)
        response = client.post(
            reverse("hub_wiki_wanted_fulfil", args=[row.pk]),
            {f"fulfil{row.pk}-page": page.slug},
            **_HTMX,
        )
        assert response.status_code == 403

    def it_degrades_to_a_text_input_past_the_choice_limit(db, client, monkeypatch):
        from hub.forms import WikiWantedFulfilForm

        monkeypatch.setattr(WikiWantedFulfilForm, "CHOICE_LIMIT", 1)
        user = _login(client, "wanted_fulfil_many")
        guild = GuildFactory(guild_lead=user.member)
        WikiWantedPageFactory(guild=guild)
        WikiPageFactory(guild=guild)
        WikiPageFactory(guild=guild)
        html = client.get(_wanted_url(guild)).content.decode()
        modal = html.split("Mark As Written", 1)[1]
        assert re.search(r'<input[^>]*name="fulfil\d+-page"', modal)


def describe_putting_a_row_back_on_the_list():
    def it_reopens_for_a_lead_and_keeps_the_ask(db, client):
        """Before this, a wrongly closed row could only be removed by Delete, which throws
        away the title, the note and the count of how many people asked."""
        user = _login(client, "wanted_reopen_lead")
        guild = GuildFactory(guild_lead=user.member)
        row = WikiWantedPageFactory(guild=guild, title="Sharpening jigs")
        row.fulfil(WikiPageFactory(guild=guild))
        assert "Put Back On The List" in client.get(_wanted_url(guild)).content.decode()
        response = client.post(reverse("hub_wiki_wanted_fulfil", args=[row.pk]), {"reopen": "1"}, **_HTMX)
        assert response.status_code == 200
        assert _toast(response)["message"] == "Back on the list."
        row.refresh_from_db()
        assert row.state == "open"
        assert row.title == "Sharpening jigs"

    def it_answers_400_when_the_same_ask_was_filed_again(db, client):
        """Reopening moves the row back INTO the partial unique index, so a duplicate
        opened while it sat closed would otherwise be an uncaught IntegrityError."""
        user = _login(client, "wanted_reopen_clash")
        guild = GuildFactory(guild_lead=user.member)
        row = WikiWantedPageFactory(guild=guild, title="Sharpening jigs")
        row.fulfil(WikiPageFactory(guild=guild))
        WikiWantedPageFactory(guild=guild, title="sharpening JIGS")
        response = client.post(reverse("hub_wiki_wanted_fulfil", args=[row.pk]), {"reopen": "1"}, **_HTMX)
        assert response.status_code == 400
        assert "already asked for" in _toast(response)["message"]
        row.refresh_from_db()
        assert row.state == "done"

    def it_answers_400_on_a_row_that_is_already_open(db, client):
        """Otherwise it silently clears a live claim, walking past the Release confirm."""
        user = _login(client, "wanted_reopen_open")
        guild = GuildFactory(guild_lead=user.member)
        holder = MemberFactory()
        row = WikiWantedPageFactory(guild=guild, claimed_by=holder, claimed_at=timezone.now())
        response = client.post(reverse("hub_wiki_wanted_fulfil", args=[row.pk]), {"reopen": "1"}, **_HTMX)
        assert response.status_code == 400
        row.refresh_from_db()
        assert row.claimed_by_id == holder.pk

    def it_is_not_offered_to_a_plain_member(db, client):
        guild = GuildFactory()
        row = WikiWantedPageFactory(guild=guild)
        row.fulfil(WikiPageFactory(guild=guild))
        _login(client, "wanted_reopen_member")
        assert "Put Back On The List" not in client.get(_wanted_url(guild)).content.decode()
        response = client.post(reverse("hub_wiki_wanted_fulfil", args=[row.pk]), {"reopen": "1"}, **_HTMX)
        assert response.status_code == 403


def describe_the_release_copy():
    def it_names_who_claimed_it_and_when(db, client):
        """The modal teleports to <body> and covers the row, so the "Claimed by Sam" line
        the lead would be reading is underneath the dialog."""
        user = _login(client, "wanted_release_copy")
        guild = GuildFactory(guild_lead=user.member)
        holder = MemberFactory(full_legal_name="Sam Holder")
        WikiWantedPageFactory(guild=guild, claimed_by=holder, claimed_at=timezone.now() - timedelta(days=35))
        html = client.get(_wanted_url(guild)).content.decode()
        assert "Sam Holder claimed this" in html
        assert "Nothing is deleted." in html


def describe_start_this_page():
    def it_carries_the_guild_the_title_and_the_row(db, client):
        _login(client, "wanted_start")
        guild = GuildFactory()
        row = WikiWantedPageFactory(guild=guild, title="Sharpening jigs")
        html = client.get(_wanted_url(guild)).content.decode()
        assert f"guild={guild.slug}&amp;title=Sharpening%20jigs&amp;wanted={row.pk}" in html
