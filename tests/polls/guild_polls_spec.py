"""Guild polls (#925 part 2, AC 6 to 9): the Polls tab, who posts and closes, one open per guild, voting."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from membership.models import Guild, Member
from polls.models import GuildPolls, Poll, PollAlreadyOpenError, PollCard, PollVote
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)
from tests.polls.factories import PollVoteFactory, poll_with

pytestmark = pytest.mark.django_db

PORTLAND = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 8, 9, 0, tzinfo=PORTLAND)
GUILDS_HOST = "guilds.pastlives.space"


def _user() -> User:
    user, _created = User.objects.get_or_create(username="guildpolladmin")
    return user


def _post(guild: Guild | None, now: datetime = NOW, question: str = "Zorblax guild?") -> Poll:
    return Poll.post(question=question, choices=["Laser", "Lathe"], days=7, by=_user(), now=now, guild=guild)


def _closed(question: str, guild: Guild | None = None) -> Poll:
    opens = timezone.now() - timedelta(days=10)
    return poll_with(
        "Laser",
        "Lathe",
        votes=(1, 0),
        question=question,
        guild=guild,
        opens_at=opens,
        closes_at=opens + timedelta(days=7),
    )


def _login(client: Client, username: str, *, role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    if member.fog_role != role:
        member.fog_role = role
        member.save(update_fields=["fog_role"])
        member.sync_user_permissions()
    client.force_login(user)
    return member


def _page(client: Client, guild: Guild, **extra: str) -> str:
    return client.get(reverse("hub_guild_detail", args=[guild.slug]), **extra).content.decode()


def _form() -> dict:
    return {"question": "Zorblax which wood?", "choice": ["Maple", "Walnut"], "days": "7"}


def describe_one_open_poll_per_scope():
    def it_refuses_a_second_open_poll_in_the_same_guild():
        guild = GuildFactory()
        first = _post(guild)

        with pytest.raises(PollAlreadyOpenError, match="Zorblax guild"):
            _post(guild, now=NOW + timedelta(hours=1), question="Zorblax again?")

        assert list(Poll.objects.for_guild(guild)) == [first]

    def it_lets_each_guild_and_the_site_have_their_own_open_poll():
        wood, metal = GuildFactory(name="Zorblax Wood"), GuildFactory(name="Zorblax Metal")

        site = _post(None, question="Zorblax site?")
        in_wood = _post(wood)
        in_metal = _post(metal)

        assert {poll.pk for poll in Poll.objects.open_at(NOW)} == {site.pk, in_wood.pk, in_metal.pk}
        assert in_wood.guild == wood
        assert site.guild is None

    def it_never_counts_a_guild_poll_against_the_site_wide_limit():
        _post(GuildFactory())

        assert _post(None, question="Zorblax site?").is_open(NOW)

    def it_keeps_a_guild_poll_off_home_and_the_admin_page():
        _post(GuildFactory())

        assert Poll.objects.site_wide().open_poll(NOW) is None
        assert PollCard.open_for(MemberFactory(), NOW) is None

    def it_goes_with_its_guild():
        guild = GuildFactory()
        _post(guild)

        guild.delete()

        assert not Poll.objects.exists()


def describe_GuildPolls():
    def it_puts_the_open_poll_on_top_and_the_closed_ones_below_newest_first():
        guild = GuildFactory()
        older = poll_with("A", "B", guild=guild, opens_at=NOW - timedelta(days=40), closes_at=NOW - timedelta(days=33))
        newer = poll_with("A", "B", guild=guild, opens_at=NOW - timedelta(days=20), closes_at=NOW - timedelta(days=13))
        current = poll_with("A", "B", guild=guild, opens_at=NOW - timedelta(hours=1), closes_at=NOW + timedelta(days=6))
        poll_with("A", "B", guild=GuildFactory(), opens_at=NOW - timedelta(hours=1), closes_at=NOW + timedelta(days=6))

        tab = GuildPolls.load(guild, MemberFactory(), NOW)

        assert tab.open is not None
        assert tab.open.poll == current
        assert [card.poll for card in tab.past] == [newer, older]
        assert bool(tab) is True

    def it_is_empty_for_a_guild_with_no_polls_in_one_query():
        guild = GuildFactory()
        member = MemberFactory()

        with CaptureQueriesContext(connection) as queries:
            tab = GuildPolls.load(guild, member, NOW)

        assert bool(tab) is False
        assert tab.open is None
        assert tab.past == []
        assert len(queries) == 1

    def it_reads_a_guild_with_polls_in_three_queries():
        guild = GuildFactory()
        for days in (10, 20, 30):
            poll_with(
                "A",
                "B",
                votes=(1, 1),
                guild=guild,
                opens_at=NOW - timedelta(days=days),
                closes_at=NOW - timedelta(days=days - 7),
            )
        member = MemberFactory()

        with CaptureQueriesContext(connection) as queries:
            GuildPolls.load(guild, member, NOW)

        assert len(queries) == 3


def describe_the_polls_tab():
    def it_shows_a_lead_the_tab_the_form_and_the_empty_state(client: Client):
        lead = _login(client, "zorblaxlead")
        guild = GuildFactory(guild_lead=lead)

        html = _page(client, guild)

        assert 'data-guild-tab="polls"' in html
        assert "data-guild-poll-post" in html
        assert f'action="{reverse("polls:guild_new", args=[guild.slug])}"' in html
        assert "data-guild-polls-empty" in html
        assert "if (t === 'polls') section = 'polls';" in html

    def it_leaves_the_tab_off_for_a_member_when_the_guild_has_no_poll(client: Client):
        _login(client, "zorblaxplain")
        guild = GuildFactory()

        html = _page(client, guild)

        assert 'data-guild-tab="polls"' not in html
        assert "data-guild-polls-tab" not in html

    def it_shows_a_member_the_open_poll_to_vote_in_and_no_form(client: Client):
        _login(client, "zorblaxvoter")
        guild = GuildFactory()
        poll = poll_with("Laser", "Lathe", question="Zorblax guild open?", guild=guild)
        _closed("Zorblax guild past?", guild=guild)

        html = _page(client, guild)
        tab = html.split("data-guild-polls-tab", 1)[1]

        assert 'data-guild-tab="polls"' in html
        assert "Zorblax guild open?" in tab.split("data-guild-past-polls", 1)[0]
        assert "Zorblax guild past?" in tab.split("data-guild-past-polls", 1)[1]
        assert f'hx-post="{reverse("polls:vote", args=[poll.pk])}"' in tab
        assert '<input type="hidden" name="variant" value="guild">' in tab
        assert "data-guild-poll-post" not in tab
        assert "data-close-poll" not in tab
        assert "data-poll-guild" not in tab
        assert f'href="{reverse("polls:index")}"' in tab

    def it_shows_the_tab_when_the_guild_has_only_past_polls(client: Client):
        _login(client, "zorblaxpast")
        guild = GuildFactory()
        _closed("Zorblax only past?", guild=guild)

        html = _page(client, guild)

        assert 'data-guild-tab="polls"' in html
        assert "data-guild-open-poll" not in html

    def it_gives_the_lead_close_now_on_the_open_poll(client: Client):
        lead = _login(client, "zorblaxcloser")
        guild = GuildFactory(guild_lead=lead)
        poll = poll_with("Laser", "Lathe", guild=guild)

        html = _page(client, guild)

        assert "data-close-poll" in html
        assert reverse("polls:guild_close", args=[guild.slug, poll.pk]) in html
        assert "data-new-poll-blocked" in html

    def it_never_shows_another_guilds_poll(client: Client):
        _login(client, "zorblaxother")
        guild = GuildFactory(name="Zorblax Here")
        poll_with("Laser", "Lathe", question="Zorblax elsewhere?", guild=GuildFactory(name="Zorblax There"))

        assert "Zorblax elsewhere?" not in _page(client, guild)

    @override_settings(
        ALLOWED_HOSTS=["guilds.pastlives.space", "testserver"],
        GUILDS_HOSTS=["guilds.pastlives.space"],
        GUILDS_BASE_URL="https://guilds.pastlives.space",
    )
    def it_hides_the_tab_on_the_guilds_surface(client: Client):
        lead = _login(client, "zorblaxsurface")
        guild = GuildFactory(guild_lead=lead)
        poll_with("Laser", "Lathe", question="Zorblax surface?", guild=guild)

        response = client.get(f"/guilds/{guild.slug}/", HTTP_HOST=GUILDS_HOST)

        assert response.status_code == 200
        assert 'data-guild-tab="polls"' not in response.content.decode()
        assert "Zorblax surface?" not in response.content.decode()


def describe_who_posts():
    def it_lets_the_lead_post_and_opens_the_tab_again(client: Client):
        lead = _login(client, "zorblaxposter")
        guild = GuildFactory(guild_lead=lead)

        response = client.post(reverse("polls:guild_new", args=[guild.slug]), _form())

        assert response.status_code == 302
        assert response.url == f"{reverse('hub_guild_detail', args=[guild.slug])}?tab=polls"
        poll = Poll.objects.get()
        assert poll.guild == guild
        assert [choice.text for choice in poll.choices.all()] == ["Maple", "Walnut"]

    def it_lets_the_guilds_staff_post(client: Client):
        staff = _login(client, "zorblaxstaff")
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=staff)

        client.post(reverse("polls:guild_new", args=[guild.slug]), _form())

        assert Poll.objects.for_guild(guild).count() == 1

    @pytest.mark.parametrize("role", [Member.FogRole.ADMIN, Member.FogRole.GUILD_OFFICER])
    def it_lets_an_admin_or_officer_post_in_any_guild(client: Client, role: str):
        _login(client, f"zorblax{role}", role=role)
        guild = GuildFactory()

        client.post(reverse("polls:guild_new", args=[guild.slug]), _form())

        assert Poll.objects.for_guild(guild).count() == 1

    def it_refuses_a_plain_member(client: Client):
        _login(client, "zorblaxnobody")
        guild = GuildFactory()

        response = client.post(reverse("polls:guild_new", args=[guild.slug]), _form())

        assert response.status_code == 403
        assert not Poll.objects.exists()

    def it_refuses_another_guilds_lead(client: Client):
        lead = _login(client, "zorblaxotherlead")
        GuildFactory(name="Zorblax Mine", guild_lead=lead)
        guild = GuildFactory(name="Zorblax Theirs")

        assert client.post(reverse("polls:guild_new", args=[guild.slug]), _form()).status_code == 403

    def it_sends_a_signed_out_visitor_to_sign_in(client: Client):
        response = client.post(reverse("polls:guild_new", args=[GuildFactory().slug]), _form())

        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_refuses_while_the_guild_has_a_poll_open_with_a_message(client: Client):
        lead = _login(client, "zorblaxtwice")
        guild = GuildFactory(guild_lead=lead)
        current = poll_with("Laser", "Lathe", guild=guild)

        response = client.post(reverse("polls:guild_new", args=[guild.slug]), _form(), follow=True)

        assert "This guild already has a poll open. Close it first, then post this one." in response.content.decode()
        assert list(Poll.objects.for_guild(guild)) == [current]

    def it_refuses_a_bad_form_with_its_reason(client: Client):
        lead = _login(client, "zorblaxbadform")
        guild = GuildFactory(guild_lead=lead)

        response = client.post(
            reverse("polls:guild_new", args=[guild.slug]), {**_form(), "choice": ["Maple"]}, follow=True
        )

        assert "A poll needs at least 2 answers." in response.content.decode()
        assert not Poll.objects.exists()

    def it_answers_404_for_an_unknown_guild(client: Client):
        _login(client, "zorblax404", role=Member.FogRole.ADMIN)

        assert client.post(reverse("polls:guild_new", args=["no-such-guild"]), _form()).status_code == 404


def describe_who_closes():
    def it_lets_the_lead_close_now(client: Client):
        lead = _login(client, "zorblaxshut")
        guild = GuildFactory(guild_lead=lead)
        poll = poll_with("Laser", "Lathe", guild=guild)

        response = client.post(reverse("polls:guild_close", args=[guild.slug, poll.pk]))

        assert response.status_code == 302
        poll.refresh_from_db()
        assert not poll.is_open(timezone.now())

    def it_refuses_a_plain_member(client: Client):
        _login(client, "zorblaxnoshut")
        guild = GuildFactory()
        poll = poll_with("Laser", "Lathe", guild=guild)

        assert client.post(reverse("polls:guild_close", args=[guild.slug, poll.pk])).status_code == 403
        poll.refresh_from_db()
        assert poll.is_open(timezone.now())

    def it_will_not_close_another_guilds_poll_through_this_one(client: Client):
        lead = _login(client, "zorblaxcross")
        guild = GuildFactory(name="Zorblax Ours", guild_lead=lead)
        theirs = poll_with("Laser", "Lathe", guild=GuildFactory(name="Zorblax Others"))

        assert client.post(reverse("polls:guild_close", args=[guild.slug, theirs.pk])).status_code == 404
        theirs.refresh_from_db()
        assert theirs.is_open(timezone.now())

    def it_is_post_only(client: Client):
        lead = _login(client, "zorblaxgetshut")
        guild = GuildFactory(guild_lead=lead)
        poll = poll_with("Laser", "Lathe", guild=guild)

        assert client.get(reverse("polls:guild_close", args=[guild.slug, poll.pk])).status_code == 405


def describe_voting_in_a_guild_poll():
    def it_lets_any_member_vote_once_and_swaps_in_the_counts(client: Client):
        member = _login(client, "zorblaxguildvote")
        guild = GuildFactory()
        poll = poll_with("Laser", "Lathe", guild=guild)
        lathe = poll.choices.get(text="Lathe")
        url = reverse("polls:vote", args=[poll.pk])

        response = client.post(url, {"choice": lathe.pk, "variant": "guild"}, HTTP_HX_REQUEST="true")
        again = client.post(url, {"choice": lathe.pk, "variant": "guild"}, HTTP_HX_REQUEST="true")

        html = response.content.decode()
        assert html.lstrip().startswith('<article class="pl-poll-card pl-poll-card--open pl-poll-card--guild"')
        assert "Your vote" in html
        assert "data-poll-guild" not in html
        assert "You already voted in this poll." in again["HX-Trigger"]
        assert list(PollVote.objects.values_list("member", flat=True)) == [member.pk]


def describe_past_polls_page():
    def it_labels_each_guild_poll_with_its_guild(client: Client):
        member = _login(client, "zorblaxlabels")
        guild = GuildFactory(name="Zorblax Ceramics")
        guild_poll = _closed("Zorblax guild question?", guild=guild)
        _closed("Zorblax site question?")
        PollVoteFactory(choice=guild_poll.choices.get(text="Lathe"), member=member)

        html = client.get(reverse("polls:index")).content.decode()

        guild_card = html.split(f'data-poll-card="{guild_poll.pk}"', 1)[1].split("</article>", 1)[0]
        site_card = html.split("Zorblax site question?", 1)[1].split("</article>", 1)[0]
        assert '<span class="pl-poll-card__guild" data-poll-guild>Zorblax Ceramics</span>' in guild_card
        assert "Your vote" in guild_card
        assert "data-poll-guild" not in site_card

    def it_reads_the_guild_names_in_the_same_query(client: Client):
        _login(client, "zorblaxnplus")
        for n in range(4):
            _closed(f"Zorblax {n}?", guild=GuildFactory(name=f"Zorblax Guild {n}"))
        client.get(reverse("polls:index"))  # warm the per-request caches

        with CaptureQueriesContext(connection) as few:
            client.get(reverse("polls:index"))
        _closed("Zorblax 4?", guild=GuildFactory(name="Zorblax Guild 4"))
        with CaptureQueriesContext(connection) as more:
            client.get(reverse("polls:index"))

        assert len(more) == len(few)


def describe_home_and_admin_stay_site_wide():
    def it_leaves_a_guild_poll_off_home(client: Client):
        _login(client, "zorblaxhomeguild")
        poll_with("Laser", "Lathe", question="Zorblax guild only?", guild=GuildFactory())

        html = client.get(reverse("hub_home")).content.decode()

        assert 'data-block="poll"' not in html
        assert "Zorblax guild only?" not in html

    def it_leaves_guild_polls_off_admin_tools_polls(client: Client):
        _login(client, "zorblaxadminguild", role=Member.FogRole.ADMIN)
        poll_with("Laser", "Lathe", question="Zorblax open guild?", guild=GuildFactory())
        _closed("Zorblax past guild?", guild=GuildFactory())

        html = client.get(reverse("polls:admin")).content.decode()

        assert "data-open-poll-empty" in html
        assert "Zorblax open guild?" not in html
        assert "Zorblax past guild?" not in html
