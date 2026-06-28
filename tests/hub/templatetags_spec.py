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
