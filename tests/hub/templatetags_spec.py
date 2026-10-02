"""BDD specs for hub template tags."""

from __future__ import annotations

import pytest
from django.template import Context, Template
from django.test import RequestFactory


@pytest.mark.django_db
def describe_active_nav():
    def it_returns_active_when_path_matches(rf: RequestFactory):
        request = rf.get("/guilds/voting/")
        template = Template("{% load hub_tags %}{% active_nav 'hub_guild_voting' %}")
        context = Context({"request": request})

        result = template.render(context)

        assert result == "active"

    def it_returns_empty_when_path_does_not_match(rf: RequestFactory):
        request = rf.get("/settings/profile/")
        template = Template("{% load hub_tags %}{% active_nav 'hub_guild_voting' %}")
        context = Context({"request": request})

        result = template.render(context)

        assert result == ""

    def it_handles_url_with_pk_argument(rf: RequestFactory):
        from tests.membership.factories import GuildFactory

        guild = GuildFactory()
        request = rf.get(f"/guilds/{guild.pk}/")
        template = Template("{%% load hub_tags %%}{%% active_nav 'hub_guild_detail' %d %%}" % guild.pk)
        context = Context({"request": request})

        result = template.render(context)

        assert result == "active"

    def it_handles_url_with_slug_argument(rf: RequestFactory):
        # The real sidebar usage: a string slug must be treated as a reverse arg, not a url name.
        from tests.membership.factories import GuildFactory

        guild = GuildFactory()
        request = rf.get(f"/guilds/{guild.slug}/")
        template = Template("{% load hub_tags %}{% active_nav 'hub_guild_detail' guild.slug %}")
        context = Context({"request": request, "guild": guild})

        result = template.render(context)

        assert result == "active"

    def it_returns_empty_for_pk_url_when_path_does_not_match(rf: RequestFactory):
        from tests.membership.factories import GuildFactory

        guild = GuildFactory()
        request = rf.get("/settings/profile/")
        template = Template("{%% load hub_tags %%}{%% active_nav 'hub_guild_detail' %d %%}" % guild.pk)
        context = Context({"request": request})

        result = template.render(context)

        assert result == ""

    def it_returns_empty_when_no_request_in_context():
        template = Template("{% load hub_tags %}{% active_nav 'hub_guild_voting' %}")
        context = Context({})

        result = template.render(context)

        assert result == ""


@pytest.mark.django_db
def describe_has_active_guild():
    def it_returns_true_when_on_guild_detail_page(rf: RequestFactory):
        from membership.models import Guild

        from tests.membership.factories import GuildFactory

        guild = GuildFactory()
        request = rf.get(f"/guilds/{guild.slug}/")
        template = Template("{% load hub_tags %}{% has_active_guild guilds as result %}{{ result }}")
        context = Context({"request": request, "guilds": Guild.objects.all()})

        result = template.render(context)

        assert result == "True"

    def it_returns_false_when_not_on_guild_detail_page(rf: RequestFactory):
        from membership.models import Guild

        from tests.membership.factories import GuildFactory

        GuildFactory()
        request = rf.get("/guilds/voting/")
        template = Template("{% load hub_tags %}{% has_active_guild guilds as result %}{{ result }}")
        context = Context({"request": request, "guilds": Guild.objects.all()})

        result = template.render(context)

        assert result == "False"

    def it_returns_false_when_no_request_in_context():
        from membership.models import Guild

        template = Template("{% load hub_tags %}{% has_active_guild guilds as result %}{{ result }}")
        context = Context({"guilds": Guild.objects.none()})

        result = template.render(context)

        assert result == "False"


def describe_by_kind():
    def it_returns_only_contacts_of_the_given_kind():
        from hub.templatetags.hub_tags import by_kind
        from membership.models import MemberContact

        website = MemberContact(label="Site", value="https://s.example", kind=MemberContact.Kind.WEBSITE)
        social = MemberContact(label="Instagram", value="@s", kind=MemberContact.Kind.SOCIAL)

        assert by_kind([website, social], "website") == [website]

    def it_returns_an_empty_list_for_empty_or_missing_contacts():
        from hub.templatetags.hub_tags import by_kind

        assert by_kind([], "website") == []
        assert by_kind(None, "social") == []


def describe_guild_logo_prefix():
    def it_maps_a_guild_name_to_its_logo_file_prefix():
        from hub.templatetags.hub_tags import guild_logo_prefix

        assert guild_logo_prefix("Printmaking Guild") == "printmaking"

    def it_returns_none_for_a_guild_without_a_logo():
        from hub.templatetags.hub_tags import guild_logo_prefix

        assert guild_logo_prefix("Quantum Computing") is None


def describe_tel_href():
    """The number part of a tel: link from whatever a member typed."""

    @pytest.mark.parametrize(
        ("typed", "href"),
        [
            ("(503) 555 0199", "5035550199"),
            ("+1 503.555.0199", "+15035550199"),
            ("503 555 0199 x12", "5035550199;ext=12"),
            ("503-555-0199 ext. 12", "5035550199;ext=12"),
            ("503-555-0199 extension 12", "5035550199;ext=12"),
            ("(503) 555 0199, ext 12", "5035550199;ext=12"),
            ("5035550199x12", "5035550199;ext=12"),
            ("503-555-0199 #12", "5035550199;ext=12"),
            ("503 555 0199 / 503 555 0200", "5035550199"),
            ("503 555 0199, 503 555 0200", "5035550199"),
            ("ext. 12 503-555-0199", ""),
            ("ask at the desk", ""),
            ("+", ""),
            ("\u00b2\u00b3", ""),
        ],
    )
    def it_keeps_the_digits_an_extension_and_a_leading_plus(typed: str, href: str):
        from hub.templatetags.hub_tags import tel_href

        assert tel_href(typed) == href
