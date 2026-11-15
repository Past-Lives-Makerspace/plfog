"""The composer's audience gate explains itself and keeps the announcement (#415).

A member refused at the audience gate is told they cannot email that audience, which is a
different sentence from "Your account cannot use Email Members." The htmx callers keep their 403
and carry the reason as an error toast; someone who cannot compose at all gets the composer's own
refusal there instead. Send keeps its 403 and renders the composer again with the message but
never the refused audience: no roster, no mailing list, no recipient rows to carry into the next
Send. A guild or class is named only when the viewer could already see it. The browser half of
the Send path is ``tests/e2e/compose_audience_refusal_spec.py``.
"""

from __future__ import annotations

import io
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering
from hub.view_as import ROLE_MEMBER, SESSION_ROLE_KEY
from hub.views import AUDIENCE_REFUSAL, COMPOSE_REFUSAL, _audience_refusal_message, _compose_audience_refusal
from membership.models import AnnouncementDraft, GuildAnnouncement
from tests.membership.factories import (
    AnnouncementDraftFactory,
    GuildFactory,
    GuildMailingListEmailFactory,
    GuildMembershipFactory,
    MembershipPlanFactory,
)

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


def _login_plain(client: Client) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username="plain", email="plain@x.com", password="p")
    client.login(username="plain", password="p")
    return user


def _rostered(guild, email: str) -> User:
    """A signed in member of ``guild`` at ``email``, so the guild's roster has a row to leak."""
    user = User.objects.create_user(username=email, email=email, last_login=timezone.now())
    GuildMembershipFactory(guild=guild, member=user.member)
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


def _refusal(client: Client, raw: str) -> str | None:
    """The gate's answer for the signed in client, on a real request so ``view_as`` is set."""
    return _compose_audience_refusal(client.get(reverse("hub_home")).wsgi_request, raw)


def describe_the_audience_reason():
    def it_names_each_audience_and_never_says_you_cannot_compose(client: Client):
        own = GuildFactory(name="Own")
        other = GuildFactory(name="Brass")
        offering = ClassOfferingFactory(title="Lost Wax Basics", status=ClassOffering.Status.PUBLISHED)
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

    def it_never_names_a_hidden_guild_or_an_unlisted_class(client: Client):
        hidden = GuildFactory(name="Hidden Zeta Guild", is_active=False)
        draft_class = ClassOfferingFactory(title="Secret Draft Class", status=ClassOffering.Status.DRAFT)
        private = ClassOfferingFactory(
            title="Private Session Class", status=ClassOffering.Status.PUBLISHED, is_private=True
        )
        _login_lead(client, GuildFactory())
        assert _refusal(client, f"guild:{hidden.pk}") == "Your account cannot email that guild."
        assert _refusal(client, f"class:{draft_class.pk}") == "Your account cannot email that class."
        assert _refusal(client, f"class:{private.pk}") == "Your account cannot email that class."

    def it_points_a_previewing_admin_at_the_switcher(client: Client):
        _login_admin(client)
        _preview_as_member(client)
        message = _refusal(client, "site")
        assert message is not None
        assert message.startswith("Your Viewing as switcher is set to ")
        assert message.endswith("which cannot email everyone. Switch it back to Admin to send this.")

    def it_does_not_send_a_previewing_admin_to_the_switcher_for_past_students(client: Client):
        # Switching back would not help: whose students they are ignores Viewing as.
        _login_admin(client)
        _preview_as_member(client)
        assert _refusal(client, "students") == "Your account cannot email past students."

    def it_does_not_tell_an_admin_who_is_not_previewing_to_switch_back(client: Client):
        # An admin who teaches nothing is refused My Past Students as Admin (#1042).
        _login_admin(client)
        assert _refusal(client, "students") == "Your account cannot email past students."

    def it_refuses_nothing_an_admin_or_a_teacher_may_address(client: Client):
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

    def it_gives_a_non_composer_the_compose_refusal_and_no_names(client: Client):
        hidden = GuildFactory(name="Hidden Zeta Guild", is_active=False)
        draft_class = ClassOfferingFactory(title="Secret Draft Class", status=ClassOffering.Status.DRAFT)
        _login_plain(client)
        refused = [
            client.get(reverse("hub_compose_count"), {"audience": f"guild:{hidden.pk}"}),
            client.get(reverse("hub_compose_count"), {"audience": f"class:{draft_class.pk}"}),
            client.post(
                reverse("hub_compose_add_addresses"), {"audience": f"guild:{hidden.pk}", "add_addresses": "a@x.com"}
            ),
            client.post(reverse("hub_compose_site_add"), {"site_add": "a@x.com"}),
            client.post(reverse("hub_compose_save_draft"), _send_data(audience=f"guild:{hidden.pk}")),
        ]
        for response in refused:
            assert response.status_code == 403
            assert _toast(response) == {"message": COMPOSE_REFUSAL, "type": "error"}
            assert b"Hidden Zeta" not in response.content
            assert b"Secret Draft" not in response.content

    def it_lets_a_composer_through_to_an_audience_they_may_address(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        assert client.get(reverse("hub_compose_count"), {"audience": f"guild:{guild.pk}"}).status_code == 200
        added = client.post(
            reverse("hub_compose_add_addresses"), {"audience": f"guild:{guild.pk}", "add_addresses": "new@x.com"}
        )
        assert added.status_code == 200
        _login_admin(client)
        assert client.post(reverse("hub_compose_site_add"), {"site_add": "new@x.com"}).status_code == 200


def describe_a_refused_send():
    def it_sends_a_non_composer_home_with_the_compose_refusal(client: Client):
        hidden = GuildFactory(name="Hidden Zeta Guild", is_active=False)
        GuildMailingListEmailFactory(guild=hidden, email="zeta-list@example.com")
        _rostered(hidden, "zeta-member@example.com")
        _login_plain(client)
        response = client.post(reverse("hub_compose_send"), _send_data(audience=f"guild:{hidden.pk}"))
        assert response.status_code == 302
        assert response.url == reverse("hub_home")
        assert [str(m) for m in get_messages(response.wsgi_request)] == [COMPOSE_REFUSAL]
        assert not AnnouncementDraft.objects.exists()

    def it_stays_in_the_composer_with_the_reason_and_the_message(client: Client, mailoutbox):
        alpha, bravo = GuildFactory(name="Ceramics"), GuildFactory(name="Bravo")
        _login_lead(client, alpha, bravo)
        alpha.guild_lead = None
        alpha.save(update_fields=["guild_lead"])

        response = client.post(
            reverse("hub_compose_send"),
            _send_data(audience=f"guild:{alpha.pk}", subject="Kiln schedule zxqv", send_email=""),
        )

        assert response.status_code == 403
        html = response.content.decode()
        assert "Glaze firing moves to Thursday zxqv." in html
        assert 'value="Kiln schedule zxqv"' in html
        assert "Your account cannot email members of Ceramics." in html
        assert "Select a valid choice" not in html
        assert "This field is required" not in html
        assert [str(m) for m in get_messages(response.wsgi_request)] == [
            "Your account cannot email members of Ceramics. Nothing was sent, and your message is still here."
        ]
        assert not AnnouncementDraft.objects.exists()
        assert not GuildAnnouncement.objects.exists()
        assert mailoutbox == []

    def it_shows_none_of_the_refused_guilds_addresses_or_roster(client: Client):
        own, other = GuildFactory(name="Own"), GuildFactory(name="Brass")
        GuildMailingListEmailFactory(guild=other, email="brass-list@example.com")
        rostered = _rostered(other, "brass-member@example.com")
        _login_lead(client, own)
        response = client.post(
            reverse("hub_compose_send"),
            {
                **_send_data(audience=f"guild:{other.pk}", lock="1"),
                "recipients": [f"user:{rostered.pk}"],
                "added_recipients": ["brass-list@example.com"],
            },
        )
        assert response.status_code == 403
        html = response.content.decode()
        assert "brass-list@example.com" not in html
        assert "brass-member@example.com" not in html
        assert f"user:{rostered.pk}" not in html
        assert "Email the members of Brass" not in html

    def it_carries_none_of_the_refused_targets_recipients_into_the_next_send(client: Client):
        alpha, bravo = GuildFactory(name="Alpha"), GuildFactory(name="Bravo")
        alpha_member = _rostered(alpha, "alpha-member@example.com")
        _rostered(bravo, "bravo-member@example.com")
        _login_lead(client, alpha, bravo)
        alpha.guild_lead = None
        alpha.save(update_fields=["guild_lead"])
        response = client.post(
            reverse("hub_compose_send"),
            {**_send_data(audience=f"guild:{alpha.pk}"), "recipients": [f"user:{alpha_member.pk}"]},
        )
        assert response.status_code == 403
        html = response.content.decode()
        assert f"user:{alpha_member.pk}" not in html
        assert "alpha-member@example.com" not in html

    def it_keeps_a_draft_whose_stored_audience_is_still_theirs(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        draft = AnnouncementDraftFactory(audience=AnnouncementDraft.Audience.GUILD, guild=guild, body="<p>Old</p>")
        response = client.post(reverse("hub_compose_send"), _send_data(audience="site", draft_pk=str(draft.pk)))
        assert response.status_code == 403
        assert f'name="draft_pk" value="{draft.pk}"' in response.content.decode()
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
        html = response.content.decode()
        assert "Glaze firing moves to Thursday zxqv." in html
        assert f'name="draft_pk" value="{theirs.pk}"' not in html
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

    def it_still_asks_for_a_chosen_image_again(client: Client):
        buffer = io.BytesIO()
        Image.new("RGB", (40, 20), "teal").save(buffer, format="PNG")
        other = GuildFactory()
        _login_lead(client, GuildFactory())
        image = SimpleUploadedFile("kiln.png", buffer.getvalue(), content_type="image/png")
        response = client.post(
            reverse("hub_compose_send"), {**_send_data(audience=f"guild:{other.pk}"), "image": image}
        )
        assert response.status_code == 403
        assert "data-compose-image-lost>Choose your image again.</p>" in response.content.decode()
