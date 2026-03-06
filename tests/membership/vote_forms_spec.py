"""Tests for vote_forms module."""

from datetime import date, timedelta

from membership.vote_forms import CreateSessionForm, VoteForm


GUILD_CHOICES = [("Ceramics", "Ceramics"), ("Glass", "Glass"), ("Wood", "Wood"), ("Metal", "Metal")]


def describe_VoteForm():
    def it_accepts_valid_three_different_guilds():
        form = VoteForm(GUILD_CHOICES, data={
            "guild_1st": "Ceramics",
            "guild_2nd": "Glass",
            "guild_3rd": "Wood",
        })
        assert form.is_valid()

    def it_rejects_duplicate_guilds():
        form = VoteForm(GUILD_CHOICES, data={
            "guild_1st": "Ceramics",
            "guild_2nd": "Ceramics",
            "guild_3rd": "Glass",
        })
        assert not form.is_valid()
        assert "three different guilds" in str(form.errors)

    def it_rejects_missing_selection():
        form = VoteForm(GUILD_CHOICES, data={
            "guild_1st": "Ceramics",
            "guild_2nd": "",
            "guild_3rd": "Glass",
        })
        assert not form.is_valid()

    def it_rejects_invalid_guild_not_in_choices():
        form = VoteForm(GUILD_CHOICES, data={
            "guild_1st": "NotAGuild",
            "guild_2nd": "Glass",
            "guild_3rd": "Wood",
        })
        assert not form.is_valid()

    def it_populates_choices_with_blank_option():
        form = VoteForm(GUILD_CHOICES)
        choices = form.fields["guild_1st"].choices
        assert choices[0] == ("", "-- Select a guild --")
        assert len(choices) == len(GUILD_CHOICES) + 1


def describe_CreateSessionForm():
    def it_accepts_valid_data():
        form = CreateSessionForm(data={
            "name": "March 2026",
            "open_date": date.today().isoformat(),
            "close_date": (date.today() + timedelta(days=7)).isoformat(),
        })
        assert form.is_valid()

    def it_rejects_close_date_before_open_date():
        form = CreateSessionForm(data={
            "name": "Bad Session",
            "open_date": "2026-03-10",
            "close_date": "2026-03-05",
        })
        assert not form.is_valid()
        assert "Close date must be after open date" in str(form.errors)

    def it_rejects_close_date_equal_to_open_date():
        form = CreateSessionForm(data={
            "name": "Same Day",
            "open_date": "2026-03-10",
            "close_date": "2026-03-10",
        })
        assert not form.is_valid()

    def it_rejects_empty_name():
        form = CreateSessionForm(data={
            "name": "",
            "open_date": "2026-03-01",
            "close_date": "2026-03-08",
        })
        assert not form.is_valid()

    def it_skips_date_validation_when_dates_are_invalid():
        form = CreateSessionForm(data={
            "name": "Test",
            "open_date": "not-a-date",
            "close_date": "2026-03-08",
        })
        assert not form.is_valid()
