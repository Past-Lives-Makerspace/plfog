"""Forms for guild voting."""

from __future__ import annotations

from datetime import date, timedelta

from django import forms


class VoteForm(forms.Form):
    """Guild voting form with 3 ranked choices."""

    guild_1st = forms.ChoiceField(label="1st Choice")
    guild_2nd = forms.ChoiceField(label="2nd Choice")
    guild_3rd = forms.ChoiceField(label="3rd Choice")

    def __init__(self, guild_choices: list[tuple[str, str]], *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("", "-- Select a guild --")] + guild_choices
        self.fields["guild_1st"].choices = choices
        self.fields["guild_2nd"].choices = choices
        self.fields["guild_3rd"].choices = choices

    def clean(self):
        cleaned = super().clean()
        picks = [cleaned.get("guild_1st"), cleaned.get("guild_2nd"), cleaned.get("guild_3rd")]
        picks = [p for p in picks if p]
        if len(picks) != 3:
            raise forms.ValidationError("Please select a guild for all three choices.")
        if len(set(picks)) != 3:
            raise forms.ValidationError("You must pick three different guilds.")
        return cleaned


class CreateSessionForm(forms.Form):
    name = forms.CharField(max_length=100, help_text='e.g. "March 2026"')
    open_date = forms.DateField(
        initial=date.today,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    close_date = forms.DateField(
        initial=lambda: date.today() + timedelta(days=7),
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("open_date") and cleaned.get("close_date"):
            if cleaned["close_date"] <= cleaned["open_date"]:
                raise forms.ValidationError("Close date must be after open date.")
        return cleaned
