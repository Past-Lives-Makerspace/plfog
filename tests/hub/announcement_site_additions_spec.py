"""People added to a site announcement on top of everyone: the vetting, the add endpoint, the form.

Covers ``hub.forms.classify_site_additions`` (who may be added and why anyone is refused),
``split_site_additions``, the "Add a member" choices, ``hub_compose_site_add`` (rows, toasts,
admins only), the form's stored shape, and the composer's count and resumed rows.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hub.forms import (
    AnnouncementComposeForm,
    classify_site_additions,
    site_addable_member_choices,
    split_site_additions,
)
from hub.views import _compose_count_for
from membership.models import AnnouncementDraft, Member
from tests.membership.factories import AnnouncementDraftFactory, GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _account(username: str, *, status: str = Member.Status.ACTIVE, logged_in: bool = True) -> User:
    """A member account (the signal provisions the member), with its status and login set."""
    MembershipPlanFactory()
    user = User.objects.create_user(
        username=username,
        email=f"{username}@x.com",
        password="p",
        first_name=username.title(),
        last_login=timezone.now() if logged_in else None,
    )
    Member.objects.filter(user=user).update(status=status)
    return User.objects.get(pk=user.pk)


def _login_admin(client: Client) -> User:
    user = User.objects.create_superuser(username="admin", email="admin@x.com", password="p")
    client.login(username="admin", password="p")
    return user


def _post_add(client: Client, **data):
    return client.post(reverse("hub_compose_site_add"), data=data)


def describe_split_site_additions():
    def it_splits_on_commas_semicolons_and_whitespace():
        assert split_site_additions(" a@x.com, b@x.com;c@x.com\nd@x.com ") == [
            "a@x.com",
            "b@x.com",
            "c@x.com",
            "d@x.com",
        ]

    def it_returns_nothing_for_blank_text():
        assert split_site_additions("  ") == []


def describe_site_addable_member_choices():
    def it_lists_members_everyone_does_not_reach_with_their_status():
        former = _account("former", status=Member.Status.FORMER)
        _account("active")
        blank = _account("blank", status=Member.Status.FORMER)
        User.objects.filter(pk=blank.pk).update(email="")
        choices = dict(site_addable_member_choices())
        assert choices == {f"user:{former.pk}": "Former · former@x.com (Former)"}


def describe_classify_site_additions():
    def _classify(tokens, include_never_logged_in=False):
        return classify_site_additions(tokens, include_never_logged_in=include_never_logged_in)

    def it_adds_a_former_member_picked_from_the_list():
        former = _account("former", status=Member.Status.FORMER)
        assert _classify([f"user:{former.pk}"]) == ([(f"user:{former.pk}", "Former · former@x.com", False)], [])

    def it_adds_a_typed_address_as_its_member_when_the_account_is_not_active():
        former = _account("former", status=Member.Status.FORMER)
        rows, problems = _classify(["FORMER@x.com"])
        assert rows == [(f"user:{former.pk}", "Former · former@x.com", False)]
        assert problems == []

    def it_adds_a_typed_address_with_no_account_as_email_only():
        assert _classify(["Guest@Example.com"]) == ([("custom:guest@example.com", "guest@example.com", True)], [])

    def it_re_vets_a_row_already_added():
        assert _classify(["custom:guest@example.com"]) == (
            [("custom:guest@example.com", "guest@example.com", True)],
            [],
        )

    def it_refuses_an_active_member_who_already_gets_it():
        active = _account("active")
        assert _classify([f"user:{active.pk}", "active@x.com"]) == ([], ["active@x.com already gets it."] * 2)

    def it_points_at_the_toggle_for_an_active_member_who_never_logged_in():
        _account("never", logged_in=False)
        rows, problems = _classify(["never@x.com"])
        assert rows == []
        assert problems == [
            "never@x.com hasn't logged in yet. Turn on \"Also include members who haven't logged in yet\" to reach them."
        ]

    def it_says_a_never_logged_in_member_already_gets_it_once_the_toggle_is_on():
        _account("never", logged_in=False)
        assert _classify(["never@x.com"], include_never_logged_in=True) == ([], ["never@x.com already gets it."])

    def it_refuses_text_that_is_not_an_email_address():
        assert _classify(["not-an-address"]) == ([], ["not-an-address isn't an email address."])

    def it_refuses_a_picked_member_who_is_gone():
        assert _classify(["user:99999"]) == ([], ["A member you picked can no longer be added."])

    def it_refuses_a_picked_member_with_no_email():
        former = _account("former", status=Member.Status.FORMER)
        User.objects.filter(pk=former.pk).update(email="")
        assert _classify([f"user:{former.pk}"]) == ([], ["Former has no email address."])

    def it_returns_each_person_once():
        former = _account("former", status=Member.Status.FORMER)
        rows, _problems = _classify([f"user:{former.pk}", "former@x.com", "guest@example.com", "GUEST@example.com"])
        assert [value for value, _label, _email_only in rows] == [f"user:{former.pk}", "custom:guest@example.com"]

    def it_takes_four_queries_however_many_tokens(django_assert_num_queries):
        former = _account("former", status=Member.Status.FORMER)
        tokens = [f"user:{former.pk}"] + [f"guest{n}@example.com" for n in range(10)]
        with django_assert_num_queries(4):
            classify_site_additions(tokens, include_never_logged_in=False)

    def it_knows_an_active_member_by_an_alias_address():
        from allauth.account.models import EmailAddress

        active = _account("active")
        EmailAddress.objects.create(user=active, email="active.work@example.com", verified=True)
        assert _classify(["Active.Work@example.com"]) == ([], ["active@x.com already gets it."])

    def it_knows_an_active_member_by_their_notification_email():
        active = _account("active")
        Member.objects.filter(user=active).update(notification_email="active.notes@example.com")
        assert _classify(["active.notes@example.com"]) == ([], ["active@x.com already gets it."])

    def it_refuses_a_former_member_whose_account_is_turned_off():
        former = _account("former", status=Member.Status.FORMER)
        User.objects.filter(pk=former.pk).update(is_active=False)
        assert _classify(["former@x.com"]) == ([], ["former@x.com's account is turned off."])

    def it_reads_a_member_pick_with_non_ascii_digits_as_text():
        assert _classify(["user:\u00b2"]) == ([], ["user:\u00b2 isn't an email address."])


def describe_hub_compose_site_add():
    def it_returns_a_row_for_each_new_person(client: Client):
        _login_admin(client)
        former = _account("former", status=Member.Status.FORMER)
        response = _post_add(client, site_add="guest@example.com, former@x.com")
        assert response.status_code == 200
        body = response.content.decode()
        assert 'value="custom:guest@example.com" checked data-email-only' in body
        assert "guest@example.com (email only)" in body
        assert f'value="user:{former.pk}" checked>' in body
        assert "HX-Trigger" not in response

    def it_adds_a_member_picked_from_the_list(client: Client):
        _login_admin(client)
        former = _account("former", status=Member.Status.FORMER)
        body = _post_add(client, site_add_member=f"user:{former.pk}").content.decode()
        assert f'data-compose-site-added-row="user:{former.pk}"' in body

    def it_leaves_out_rows_already_added(client: Client):
        _login_admin(client)
        response = _post_add(client, site_add="guest@example.com", added_recipients="custom:guest@example.com")
        assert "data-compose-site-added-row" not in response.content.decode()

    def it_toasts_why_anyone_was_refused(client: Client):
        _login_admin(client)
        _account("active")
        response = _post_add(client, site_add="active@x.com nope")
        toast = json.loads(response["HX-Trigger"])["showToast"]
        assert toast == {"message": "active@x.com already gets it. nope isn't an email address.", "type": "error"}

    def it_reads_the_toggle_to_word_a_refusal(client: Client):
        _login_admin(client)
        _account("never", logged_in=False)
        response = _post_add(client, site_add="never@x.com", include_never_logged_in="on")
        assert json.loads(response["HX-Trigger"])["showToast"]["message"] == "never@x.com already gets it."

    def it_refuses_a_guild_lead(client: Client):
        guild = GuildFactory()
        lead = _account("lead")
        guild.guild_lead = lead.member
        guild.save(update_fields=["guild_lead"])
        client.login(username="lead", password="p")
        assert _post_add(client, site_add="guest@example.com").status_code == 403

    def it_takes_only_posts(client: Client):
        _login_admin(client)
        assert client.get(reverse("hub_compose_site_add")).status_code == 405


def describe_the_compose_form():
    def _form(data):
        return AnnouncementComposeForm(data=data, is_admin=True, editable_guilds=[GuildFactory()])

    def it_stores_the_vetted_people_for_a_site_audience():
        former = _account("former", status=Member.Status.FORMER)
        active = _account("active")
        form = _form(
            {
                "audience": "site",
                "body": "<p>x</p>",
                "added_recipients": [f"user:{former.pk}", f"user:{active.pk}", "custom:guest@example.com"],
            }
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["added_recipients"] == {"users": [former.pk], "custom": ["guest@example.com"]}

    def it_stores_nothing_for_a_guild_audience():
        guild = GuildFactory()
        form = AnnouncementComposeForm(
            data={
                "audience": f"guild:{guild.pk}",
                "body": "<p>x</p>",
                "added_recipients": ["custom:guest@example.com"],
            },
            is_admin=True,
            editable_guilds=[guild],
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["added_recipients"] == {}

    def it_offers_nothing_to_add_to_a_sender_who_is_not_an_admin():
        guild = GuildFactory()
        _account("former", status=Member.Status.FORMER)
        form = AnnouncementComposeForm(
            initial={"added_recipients": ["custom:guest@example.com"]}, editable_guilds=[guild]
        )
        assert form.site_add_member_choices == []
        assert form.site_added_rows == []


def describe_the_composer():
    def it_resumes_a_draft_with_its_added_people_checked_and_counted(client: Client):
        _login_admin(client)
        former = _account("former", status=Member.Status.FORMER)
        draft = AnnouncementDraftFactory(
            audience="site", added_recipients={"users": [former.pk], "custom": ["guest@example.com"]}
        )
        response = client.get(reverse("hub_compose_resume", args=[draft.pk]))
        body = response.content.decode()
        assert f'data-compose-site-added-row="user:{former.pk}"' in body
        assert 'data-compose-site-added-row="custom:guest@example.com"' in body
        assert response.context["initial_recipient_count"] == _compose_count_for("site", None) + 2

    def it_leaves_typed_addresses_out_of_the_count_while_email_is_off(client: Client):
        _login_admin(client)
        draft = AnnouncementDraftFactory(
            audience="site", send_email=False, added_recipients={"users": [], "custom": ["guest@example.com"]}
        )
        response = client.get(reverse("hub_compose_resume", args=[draft.pk]))
        assert response.context["initial_recipient_count"] == _compose_count_for("site", None)

    def it_offers_the_members_everyone_does_not_reach(client: Client):
        _login_admin(client)
        former = _account("former", status=Member.Status.FORMER)
        body = client.get(reverse("hub_compose")).content.decode()
        assert f'<option value="user:{former.pk}">Former · former@x.com (Former)</option>' in body
        assert 'name="site_add"' in body

    def it_saves_a_site_draft_with_its_added_people(client: Client):
        _login_admin(client)
        client.post(
            reverse("hub_compose_save_draft"),
            data={
                "audience": "site",
                "body": "<p>x</p>",
                "discord_channel": "none",
                "mention": "none",
                "draft_pk": "",
                "added_recipients": ["custom:guest@example.com"],
            },
        )
        assert AnnouncementDraft.objects.get().added_recipients == {"users": [], "custom": ["guest@example.com"]}
