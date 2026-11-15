"""The composer's audience gate explains itself and keeps the announcement (#415).

A member refused at the audience gate is told they cannot email that audience, which is a
different sentence from "Your account cannot use Email Members." The htmx callers keep their 403
and carry the reason as an error toast; Send keeps its 403 but renders the composer again with
everything that was posted, so the announcement is not lost. The browser half of the Send path
is ``tests/e2e/compose_audience_refusal_spec.py``.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client, RequestFactory
from django.urls import reverse

from classes.factories import ClassOfferingFactory
from hub.view_as import ROLE_MEMBER, SESSION_ROLE_KEY
from hub.views import AUDIENCE_REFUSAL, COMPOSE_REFUSAL, _audience_refusal_message
from membership.models import AnnouncementDraft, GuildAnnouncement
from tests.membership.factories import AnnouncementDraftFactory, GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _login_admin(client: Client, username: str = "admin") -> User:
    user = User.objects.create_superuser(username=username, email=f"{username}@x.com", password="p")
    client.login(username=username, password="p")
    return user


def _login_lead(client: Client, *guilds, username: str = "lead") -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password="p")
    for guild in guilds:
        guild.guild_lead = user.member
        guild.save(update_fields=["guild_lead"])
    client.login(username=username, password="p")
    return user


def _preview_as_member(client: Client) -> None:
    session = client.session
    session[SESSION_ROLE_KEY] = ROLE_MEMBER
    session.save()


def _send_data(**overrides: str) -> dict[str, str]:
    data = {
        "audience": "site",
        "subject": "Kiln schedule",
        "body": "<p>Glaze firing moves to Thursday zxqv.</p>",
        "discord_channel": "none",
        "mention": "none",
        "draft_pk": "",
    }
    data.update(overrides)
    return data


def _toast(response) -> dict[str, str]:
    return json.loads(response["HX-Trigger"])["showToast"]


def _refusal(request_client: Client, raw: str) -> str | None:
    """The gate's answer for the logged in client, through a real request so ``view_as`` is set."""
    response = request_client.get(reverse("hub_compose_count"), {"audience": raw})
    if response.status_code == 200:
        return None
    return _toast(response)["message"]


def describe_the_audience_reason():
    def it_names_each_audience_and_never_says_you_cannot_compose(client: Client):
        own = GuildFactory(name="Own")
        other = GuildFactory(name="Brass")
        offering = ClassOfferingFactory(title="Lost Wax Basics")
        _login_lead(client, own)
        assert _refusal(client, f"guild:{own.pk}") is None
        assert _refusal(client, "site") == "Your account cannot email everyone."
        assert _refusal(client, "leads") == "Your account cannot email the guild leads."
        assert _refusal(client, "instructor") == "Your account cannot email all instructors."
        assert _refusal(client, "students") == "Your account cannot email past students."
        assert _refusal(client, f"guild:{other.pk}") == "Your account cannot email members of Brass."
        assert _refusal(client, f"class:{offering.pk}") == "Your account cannot email the class Lost Wax Basics."
        assert _refusal(client, "guild:999999") == "Your account cannot email that audience."
        assert _refusal(client, "nonsense") == "Your account cannot email that audience."
        assert COMPOSE_REFUSAL not in AUDIENCE_REFUSAL

    def it_points_a_previewing_admin_at_the_switcher(client: Client):
        _login_admin(client)
        _preview_as_member(client)
        message = _refusal(client, "site")
        assert message is not None
        assert message.startswith("Your Viewing as switcher is set to ")
        assert message.endswith("which cannot email everyone. Switch it back to Admin to send this.")

    def it_does_not_tell_an_admin_who_is_not_previewing_to_switch_back(client: Client):
        # An admin who teaches nothing is refused My Past Students as Admin (#1042).
        _login_admin(client)
        assert _refusal(client, "students") == "Your account cannot email past students."

    def it_refuses_nothing_an_admin_or_a_teacher_may_address(client: Client):
        from django.utils import timezone

        MembershipPlanFactory()
        user = User.objects.create_user(username="teach", email="teach@x.com", password="p")
        user.member.instructor_oriented_at = timezone.now()
        user.member.save(update_fields=["instructor_oriented_at"])
        offering = ClassOfferingFactory(instructor=user.member)
        client.login(username="teach", password="p")
        assert _refusal(client, f"class:{offering.pk}") is None
        assert _refusal(client, "students") is None
        _login_admin(client)
        assert _refusal(client, "site") is None

    def it_reads_a_request_without_view_as_as_not_previewing():
        request = RequestFactory().get("/")
        assert _audience_refusal_message(request, "everyone") == "Your account cannot email everyone."


def describe_the_htmx_callers():
    def it_keeps_the_count_refusal_a_403_with_an_error_toast(client: Client):
        _login_lead(client, GuildFactory())
        response = client.get(reverse("hub_compose_count"), {"audience": "site"})
        assert response.status_code == 403
        assert _toast(response) == {"message": "Your account cannot email everyone.", "type": "error"}

    def it_keeps_the_save_draft_refusal_a_403_with_an_error_toast(client: Client):
        other = GuildFactory(name="Brass")
        _login_lead(client, GuildFactory())
        response = client.post(reverse("hub_compose_save_draft"), _send_data(audience=f"guild:{other.pk}"))
        assert response.status_code == 403
        assert _toast(response) == {"message": "Your account cannot email members of Brass.", "type": "error"}
        assert not AnnouncementDraft.objects.exists()


def describe_a_refused_send():
    def it_stays_in_the_composer_with_the_reason_and_the_message(client: Client, mailoutbox):
        guild = GuildFactory(name="Ceramics")
        _login_lead(client, guild)
        guild.guild_lead = None
        guild.save(update_fields=["guild_lead"])

        response = client.post(
            reverse("hub_compose_send"),
            _send_data(audience=f"guild:{guild.pk}", subject="Kiln schedule zxqv"),
        )

        assert response.status_code == 403
        html = response.content.decode()
        assert "Glaze firing moves to Thursday zxqv." in html
        assert 'value="Kiln schedule zxqv"' in html
        assert "Your account cannot email members of Ceramics." in html
        assert "Select a valid choice" not in html
        assert [str(m) for m in get_messages(response.wsgi_request)] == [
            "Your account cannot email members of Ceramics. Nothing was sent, and your message is still here."
        ]
        assert not AnnouncementDraft.objects.exists()
        assert not GuildAnnouncement.objects.exists()
        assert mailoutbox == []

    def it_keeps_a_draft_whose_stored_audience_is_still_theirs(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        draft = AnnouncementDraftFactory(audience=AnnouncementDraft.Audience.GUILD, guild=guild, body="<p>Old</p>")
        response = client.post(reverse("hub_compose_send"), _send_data(audience="site", draft_pk=str(draft.pk)))
        assert response.status_code == 403
        assert f'value="{draft.pk}"' in response.content.decode()
        draft.refresh_from_db()
        assert (draft.audience, draft.body, draft.sent_at) == ("guild", "<p>Old</p>", None)

    def it_drops_a_draft_pk_that_is_no_longer_theirs(client: Client):
        other = GuildFactory()
        _login_lead(client, GuildFactory())
        theirs = AnnouncementDraftFactory(audience=AnnouncementDraft.Audience.GUILD, guild=other)
        response = client.post(
            reverse("hub_compose_send"), _send_data(audience=f"guild:{other.pk}", draft_pk=str(theirs.pk))
        )
        assert response.status_code == 403
        assert "Glaze firing moves to Thursday zxqv." in response.content.decode()
        theirs.refresh_from_db()
        assert theirs.sent_at is None

    def it_keeps_a_refused_poll(client: Client):
        guild = GuildFactory(name="Ceramics")
        _login_lead(client, GuildFactory())
        data = _send_data(audience=f"guild:{guild.pk}", body="")
        data.update(
            {
                "kind": "poll",
                "poll-title": "Which glaze night zxqv",
                "question-TOTAL_FORMS": "0",
                "question-INITIAL_FORMS": "0",
                "question-MIN_NUM_FORMS": "0",
                "question-MAX_NUM_FORMS": "10",
            }
        )
        response = client.post(reverse("hub_compose_send"), data)
        assert response.status_code == 403
        assert "Which glaze night zxqv" in response.content.decode()
