"""BDD specs for the rich text size limit on the forms and autosaves that save what members type.

One limit, ``core.html_sanitize.RICH_TEXT_MAX_CHARS``, applied before anything is sanitized:
a form shows the copy under the field, an autosave answers 422 with it as the error toast.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.html_sanitize import RICH_TEXT_MAX_CHARS, RICH_TEXT_TOO_LONG
from hub.forms import GuildEditForm, GuildMeetingNoteForm
from membership.models import Member, WikiDraft
from tests.features import turn_on
from tests.membership.factories import GuildFactory, MeetingFactory, MembershipPlanFactory, WikiPageFactory

pytestmark = pytest.mark.django_db

TOO_LONG = "<p>" + "x" * RICH_TEXT_MAX_CHARS + "</p>"
COPY = "This is too long to save. Shorten it to under 100,000 characters."


def _login(client: Client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = Member.FogRole.MEMBER
    member.status = Member.Status.ACTIVE
    member.full_legal_name = username.title()
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def describe_the_copy():
    def it_is_plain_and_names_the_limit():
        assert RICH_TEXT_TOO_LONG == COPY


def describe_guild_edit_form():
    def it_refuses_a_wishlist_over_the_limit():
        guild = GuildFactory()
        form = GuildEditForm({"name": guild.name, "wishlist": TOO_LONG}, instance=guild)
        assert not form.is_valid()
        assert form.errors["wishlist"] == [COPY]

    def it_saves_a_wishlist_under_the_limit():
        guild = GuildFactory()
        form = GuildEditForm({"name": guild.name, "wishlist": "- **Clamps**"}, instance=guild)
        assert form.is_valid(), form.errors
        assert form.save().wishlist == "- **Clamps**"

    def it_saves_an_empty_wishlist():
        guild = GuildFactory()
        form = GuildEditForm({"name": guild.name}, instance=guild)
        assert form.is_valid(), form.errors
        assert form.save().wishlist == ""


def describe_guild_meeting_note_form():
    def it_refuses_a_body_over_the_limit():
        form = GuildMeetingNoteForm({"meeting_date": "2026-10-01", "title": "October", "body": TOO_LONG})
        assert not form.is_valid()
        assert form.errors["body"] == [COPY]

    def it_accepts_a_body_under_the_limit():
        form = GuildMeetingNoteForm({"meeting_date": "2026-10-01", "title": "October", "body": "We met."})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["body"] == "We met."


def describe_meeting_notes_autosave():
    def it_answers_422_with_the_copy_and_keeps_the_saved_notes(client: Client):
        guild = GuildFactory()
        user = _login(client, "notes_lead")
        guild.guild_lead = user.member
        guild.save(update_fields=["guild_lead"])
        meeting = MeetingFactory(guild=guild, other_notes="<p>kept</p>")
        response = client.post(
            reverse("hub_meeting_save", args=[meeting.pk]), {"field": "other_notes", "value": TOO_LONG}
        )
        assert response.status_code == 422
        assert response.content.decode() == COPY
        assert "showToast" in response["HX-Trigger"]
        meeting.refresh_from_db()
        assert meeting.other_notes == "<p>kept</p>"


def describe_wiki_body_autosave():
    def it_answers_422_with_the_copy_and_writes_no_draft(client: Client):
        turn_on("wiki")
        user = _login(client, "wiki_long")
        page = WikiPageFactory()
        response = client.post(reverse("hub_wiki_autosave", args=[page.slug]), {"field": "body", "value": TOO_LONG})
        assert response.status_code == 422
        assert response.content.decode() == COPY
        assert not WikiDraft.objects.filter(page=page, author=user.member).exists()
