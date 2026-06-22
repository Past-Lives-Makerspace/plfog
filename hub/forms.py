"""Forms for the member hub."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django import forms
from django.conf import settings
from django.core.mail import send_mail

if TYPE_CHECKING:
    from django.contrib.auth.models import User

from core.models import CalendarFeed, SiteConfiguration
from membership.models import Guild, GuildAnnouncement, GuildFAQItem, GuildLink, Member


class GuildEditForm(forms.ModelForm):
    """Edit form for a guild's public-facing fields, including calendar integration."""

    _WEEKDAY_CHOICES = [("", "—")] + [
        (str(i), name)
        for i, name in enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
    ]
    _WEEK_CHOICES = [("", "—"), ("1", "1st"), ("2", "2nd"), ("3", "3rd"), ("4", "4th"), ("5", "Last")]
    meeting_cadence = forms.ChoiceField(choices=Guild.MeetingCadence.choices, required=False, label="Meeting cadence")
    meeting_weekday = forms.TypedChoiceField(
        choices=_WEEKDAY_CHOICES, coerce=int, required=False, empty_value=None, label="Meeting day"
    )
    meeting_week_of_month = forms.TypedChoiceField(
        choices=_WEEK_CHOICES, coerce=int, required=False, empty_value=None, label="Week of month (for monthly)"
    )

    class Meta:
        model = Guild
        fields = [
            "name",
            "about",
            "banner_image",
            "calendar_url",
            "calendar_color",
            "youtube_url",
            "meeting_cadence",
            "meeting_weekday",
            "meeting_week_of_month",
            "meeting_time",
            "meeting_location",
            "meeting_next_override",
            "meeting_is_tba",
            "meeting_schedule",
            "contact_email",
            "show_members",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Guild name"}),
            "about": forms.Textarea(
                attrs={"rows": 5, "placeholder": "Tell members what this guild is about..."},
            ),
            "calendar_url": forms.URLInput(attrs={"placeholder": "https://calendar.google.com/calendar/ical/..."}),
            "calendar_color": forms.TextInput(
                attrs={"type": "color", "class": "pl-color-input"},
            ),
            "youtube_url": forms.URLInput(attrs={"placeholder": "https://youtube.com/watch?v=..."}),
            "meeting_time": forms.TimeInput(attrs={"type": "time"}),
            "meeting_location": forms.TextInput(attrs={"placeholder": "Studio B"}),
            "meeting_next_override": forms.DateInput(attrs={"type": "date"}),
            "meeting_schedule": forms.Textarea(
                attrs={"rows": 2, "placeholder": "Extra notes, e.g. 'bring your current project'"}
            ),
            "contact_email": forms.EmailInput(attrs={"placeholder": "guild@example.com"}),
        }
        labels = {
            "about": "About",
            "banner_image": "Banner image",
            "calendar_url": "Google Calendar iCal URL",
            "calendar_color": "Calendar Color",
            "youtube_url": "YouTube video",
            "meeting_cadence": "Meeting cadence",
            "meeting_time": "Meeting time",
            "meeting_location": "Meeting location",
            "meeting_next_override": "Override next date (optional)",
            "meeting_is_tba": "No meeting scheduled yet (show TBA)",
            "meeting_schedule": "Meeting notes",
            "contact_email": "Contact email",
            "show_members": "Show members roster",
        }
        help_texts = {
            "banner_image": "Shown at the top of the guild page. Max 5 MB.",
            "calendar_url": (
                "In Google Calendar → Settings → your calendar → 'Secret address in iCal format'. "
                "Leave blank if you don't use Google Calendar."
            ),
            "calendar_color": "Color used for your guild's events on the Community Calendar.",
        }

    def clean_meeting_cadence(self) -> str:
        # An omitted/blank cadence means "no regular meeting", not an empty string.
        return self.cleaned_data.get("meeting_cadence") or Guild.MeetingCadence.NONE


class ProfileSettingsForm(forms.ModelForm):
    """Form for editing member profile fields plus per-field directory visibility."""

    VISIBILITY_PREFIX = "show_"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Admins, Guild Officers, Guild Leads, and Instructors are always listed —
        # the field gets force-true on save and is shown disabled with a note.
        if self.instance and self.instance.pk and self.instance.must_be_listed_in_directory:
            field = self.fields["show_in_directory"]
            field.disabled = True
            field.initial = True
            field.help_text = "Your role (admin, officer, guild lead, or instructor) requires a public profile."

        current = self.instance.directory_visibility if self.instance and self.instance.pk else {}
        for field_name in Member.DIRECTORY_TOGGLEABLE_FIELDS:
            self.fields[f"{self.VISIBILITY_PREFIX}{field_name}"] = forms.BooleanField(
                required=False,
                initial=bool(current.get(field_name, True)),
                label=f"Show {field_name.replace('_', ' ')} on my directory card",
            )

    def save(self, commit: bool = True) -> Member:
        member = super().save(commit=False)
        if member.must_be_listed_in_directory:
            member.show_in_directory = True
        member.directory_visibility = {
            field_name: bool(self.cleaned_data.get(f"{self.VISIBILITY_PREFIX}{field_name}", True))
            for field_name in Member.DIRECTORY_TOGGLEABLE_FIELDS
        }
        if commit:
            member.save()
        return member

    class Meta:
        model = Member
        fields = [
            "preferred_name",
            "pronouns",
            "phone",
            "discord_handle",
            "other_contact_info",
            "about_me",
            "profile_photo",
            "show_in_directory",
            "instructor_website",
            "instructor_social_handle",
        ]
        widgets = {
            "preferred_name": forms.TextInput(attrs={"placeholder": "How should we call you?"}),
            "phone": forms.TextInput(attrs={"placeholder": "(optional)"}),
            "discord_handle": forms.TextInput(attrs={"placeholder": "@username"}),
            "other_contact_info": forms.TextInput(attrs={"placeholder": "Instagram, Signal, etc."}),
            "about_me": forms.Textarea(attrs={"rows": 3, "placeholder": "Tell other members a bit about yourself..."}),
            "instructor_social_handle": forms.TextInput(attrs={"placeholder": "@handle"}),
        }
        labels = {
            "show_in_directory": "Show me in the member directory",
            "discord_handle": "Discord",
            "other_contact_info": "Other contact info",
            "about_me": "About me",
            "profile_photo": "Profile photo",
            "instructor_website": "Website",
            "instructor_social_handle": "Social handle",
        }
        help_texts = {
            "profile_photo": "Optional. Shown next to your name in the member directory. Max 5 MB.",
            "instructor_website": "Shown on your public instructor profile.",
            "instructor_social_handle": "Shown on your public instructor profile.",
        }


class EmailPreferencesForm(forms.Form):
    """Form for email notification preferences."""

    voting_results = forms.BooleanField(required=False, label="Voting Result Emails")


class BetaFeedbackForm(forms.Form):
    """Form for submitting beta feedback (bug reports, feature requests, general feedback)."""

    CATEGORY_CHOICES = [
        ("bug", "Bug Report"),
        ("feature", "Feature Request"),
        ("feedback", "General Feedback"),
    ]

    category = forms.ChoiceField(choices=CATEGORY_CHOICES, label="Category")
    subject = forms.CharField(max_length=200, label="Subject")
    message = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "Describe your issue or idea..."}), label="Message"
    )

    def send(self, *, user: User) -> None:
        """Send the feedback email to the configured recipients."""
        category_label = dict(self.CATEGORY_CHOICES)[self.cleaned_data["category"]]
        subject = f"[Beta {category_label}] {self.cleaned_data['subject']}"
        body = (
            f"From: {user.get_full_name() or user.email} ({user.email})\n"
            f"Category: {category_label}\n\n"
            f"{self.cleaned_data['message']}"
        )
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=settings.BETA_FEEDBACK_EMAILS,
        )


class MemberAdminEditForm(forms.ModelForm):
    """Admin-side Member edit form with a unified role dropdown.

    The `role` token doesn't map 1:1 to a model field — Member.apply_admin_role
    handles the fog_role/status/Instructor dispatch. This form only validates
    inputs; the view calls `member.apply_admin_role(cleaned_data["role"])`.
    """

    ROLE_CHOICES: list[tuple[str, str]] = [
        (Member.FogRole.ADMIN, "Admin"),
        (Member.FogRole.GUILD_OFFICER, "Guild Officer"),
        (Member.FogRole.MEMBER, "Member"),
        (Member.ADMIN_ROLE_INSTRUCTOR, "Instructor"),
        (Member.ADMIN_ROLE_GUEST, "Guest"),
    ]

    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        label="Role",
        help_text=(
            "Admin / Guild Officer / Member set the hierarchy role. "
            "Instructor also grants teaching access. "
            "Guest deactivates the member (no hub access)."
        ),
    )

    class Meta:
        model = Member
        fields = [
            "full_legal_name",
            "preferred_name",
            "pronouns",
            "discord_handle",
            "about_me",
            "status",
            "member_type",
            "show_in_directory",
        ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["role"].initial = self._derive_initial_role(self.instance)

    @staticmethod
    def _derive_initial_role(member: Member) -> str:
        if member.status != Member.Status.ACTIVE:
            return Member.ADMIN_ROLE_GUEST
        if member.is_instructor and member.fog_role == Member.FogRole.MEMBER:
            return Member.ADMIN_ROLE_INSTRUCTOR
        return member.fog_role


class SiteSettingsForm(forms.ModelForm):
    """Admin form for the SiteConfiguration singleton.

    Calendar feed rows live on the separate ``CalendarFeedFormSet`` below — keeping
    them off this form lets the Calendar tab manage an arbitrary number of feeds
    via a Django inline-style formset.
    """

    class Meta:
        model = SiteConfiguration
        fields = [
            "registration_mode",
            "sync_classes_enabled",
            "classes_calendar_color",
            "legacy_cms_sync_enabled",
            "mailchimp_api_key",
            "mailchimp_list_id",
            "google_analytics_measurement_id",
        ]
        widgets = {
            "classes_calendar_color": forms.TextInput(attrs={"type": "color"}),
        }


class CalendarFeedForm(forms.ModelForm):
    """One row in the Calendar tab's feeds list."""

    class Meta:
        model = CalendarFeed
        fields = ["name", "ical_url", "color"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Workshops"}),
            "ical_url": forms.URLInput(attrs={"placeholder": "https://calendar.google.com/calendar/ical/..."}),
            "color": forms.TextInput(attrs={"type": "color"}),
        }


CalendarFeedFormSet = forms.modelformset_factory(
    CalendarFeed,
    form=CalendarFeedForm,
    extra=0,
    can_delete=True,
)


class VotePreferenceForm(forms.Form):
    """Form for submitting or updating a member's persistent guild vote preferences."""

    guild_1st = forms.ModelChoiceField(
        queryset=Guild.objects.filter(is_active=True),
        label="1st Choice (5 pts)",
        empty_label="-- Select a guild --",
    )
    guild_2nd = forms.ModelChoiceField(
        queryset=Guild.objects.filter(is_active=True),
        label="2nd Choice (3 pts)",
        empty_label="-- Select a guild --",
    )
    guild_3rd = forms.ModelChoiceField(
        queryset=Guild.objects.filter(is_active=True),
        label="3rd Choice (2 pts)",
        empty_label="-- Select a guild --",
    )

    def clean(self) -> dict:
        """Validate that all three guild choices are distinct."""
        cleaned: dict = super().clean() or {}
        g1 = cleaned.get("guild_1st")
        g2 = cleaned.get("guild_2nd")
        g3 = cleaned.get("guild_3rd")

        if g1 and g2 and g3:
            choices = [g1, g2, g3]
            if len(set(g.pk for g in choices)) != 3:
                raise forms.ValidationError("Please select three different guilds.")

        return cleaned


class GuildFAQItemForm(forms.ModelForm):
    """A single FAQ question/answer row on the guild edit page."""

    class Meta:
        model = GuildFAQItem
        fields = ["question", "answer", "sort_order"]
        widgets = {
            "answer": forms.Textarea(attrs={"rows": 3}),
            "sort_order": forms.HiddenInput(),
        }


GuildFAQItemFormSet = forms.inlineformset_factory(Guild, GuildFAQItem, form=GuildFAQItemForm, extra=1, can_delete=True)


class GuildLinkForm(forms.ModelForm):
    """A single external-link row on the guild edit page."""

    class Meta:
        model = GuildLink
        fields = ["label", "url", "sort_order"]
        widgets = {"sort_order": forms.HiddenInput()}


GuildLinkFormSet = forms.inlineformset_factory(Guild, GuildLink, form=GuildLinkForm, extra=1, can_delete=True)


class GuildAnnouncementForm(forms.ModelForm):
    """Post a news announcement on a guild page."""

    class Meta:
        model = GuildAnnouncement
        fields = ["title", "body", "expires_at"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 4}),
            "expires_at": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {"expires_at": "Hide after (optional)"}
        help_texts = {"expires_at": "Leave blank to keep it up indefinitely."}


class SiteAnnouncementForm(forms.Form):
    """Admin form to broadcast a site-wide announcement to all members."""

    title = forms.CharField(max_length=300, label="Title")
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), label="Message")
